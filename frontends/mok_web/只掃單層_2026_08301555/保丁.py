# -*- coding: utf-8 -*-
"""
補丁：檔案樹只掃單層（不遞迴）
================================
目的: 解決 /api/tree 遞迴掃描整個 home 造成超時（524）與回應過大的問題。
      改為只顯示根目錄的一層，之後所有子目錄 children 皆為空，
      檔案樹秒開、JSON 極小。

【調整】想改成兩層/三層，改下方 MAX_TREE_DEPTH 即可，不用動核心。
        MAX_TREE_DEPTH = 1  → 只顯示一層（本次需求）
        MAX_TREE_DEPTH = 2  → 顯示兩層
        MAX_TREE_DEPTH = 0  → 完全恢復原版無限遞迴

【覆蓋目標】main.get_file_tree（核心函數）
"""
import sys, time

main = sys.modules['__main__']            # 核心 mok_web 模組

# 從核心模組借用符號（補丁是獨立模組，沒有核心的全局變數）
os = main.os
SKIP_DIRS = main.SKIP_DIRS
MAX_TREE_ITEMS = main.MAX_TREE_ITEMS
WATCH_PATH = main.WATCH_PATH

MAX_TREE_DEPTH = 1                         # 掃描最大深度（1 = 只掃單層）


def get_file_tree(path, depth=0):
    """只掃單層的檔案樹（不遞迴）"""
    try:
        items = os.listdir(path)
    except OSError:
        return []

    # 排序: 目錄在前, 檔案在後; 同類按名稱排序
    def sort_key(item):
        full_path = os.path.join(path, item)
        is_dir = os.path.isdir(full_path)
        return (0 if is_dir else 1, item.lower())
    items.sort(key=sort_key)

    tree = []
    filtered = []
    for item in items:
        if 'web_viewer' in item:
            continue
        full_path = os.path.join(path, item)
        is_dir = os.path.isdir(full_path)
        # 跳過運行時/無用大目錄
        if is_dir and item in SKIP_DIRS:
            continue
        filtered.append(item)
        # 限制每層項目數
        if len(filtered) >= MAX_TREE_ITEMS:
            break

    for item in filtered:
        full_path = os.path.join(path, item)
        is_dir = os.path.isdir(full_path)
        node = {'name': item, 'path': os.path.relpath(full_path, WATCH_PATH), 'is_dir': is_dir}
        # ★ 只掃單層：不再遞迴展開 children
        node['children'] = []
        tree.append(node)
    return tree


# ================= 覆蓋核心函數（monkey patch） =================
main.get_file_tree = get_file_tree
print("[保丁][只掃單層] 已覆蓋 get_file_tree（MAX_TREE_DEPTH =", MAX_TREE_DEPTH, "）")

# ================= 惰性載入（看才掃描） =================
# /api/tree            → 只回傳根目錄第一層（秒開、JSON 極小）
# /api/tree?path=xxx   → 回傳指定目錄第一層（用戶展開資料夾時才掃描）
# 每層結果快取 TREE_CACHE_TTL 秒；檔案變動（file_change）自動失效
TREE_CACHE_TTL = getattr(main, "TREE_CACHE_TTL", 15)

# 每目錄惰性快取: {rel_path: {"ts": float, "data": [...]}}
_dir_cache = {}


def _list_children(full_path):
    """列出一層目錄內容（子目錄 children 為空，展開時再掃）"""
    try:
        items = os.listdir(full_path)
    except OSError:
        return []

    def sort_key(item):
        full = os.path.join(full_path, item)
        is_dir = os.path.isdir(full)
        mtime = os.path.getmtime(full) if os.path.exists(full) else 0
        return (0 if is_dir else 1, item.lower())
    items.sort(key=sort_key)

    tree = []
    filtered = []
    for item in items:
        if "web_viewer" in item:
            continue
        full = os.path.join(full_path, item)
        is_dir = os.path.isdir(full)
        if is_dir and item in SKIP_DIRS:
            continue
        filtered.append(item)
        if len(filtered) >= MAX_TREE_ITEMS:
            break

    for item in filtered:
        full = os.path.join(full_path, item)
        is_dir = os.path.isdir(full)
        node = {"name": item, "path": os.path.relpath(full, WATCH_PATH), "is_dir": is_dir}
        node["children"] = []          # 惰性：展開時再掃
        tree.append(node)
    return tree


def get_dir_children(rel_path):
    """回傳指定目錄（相對 WATCH_PATH）的第一層內容，帶快取"""
    rel_path = (rel_path or "").strip().strip("/")
    if not rel_path:
        return main.get_file_tree_cached()   # 根目錄走原快取

    # 安全防護：不允許跳出 WATCH_PATH
    base = os.path.normpath(WATCH_PATH)
    full = os.path.normpath(os.path.join(base, rel_path))
    if not (full == base or full.startswith(base + os.sep)) or not os.path.isdir(full):
        return []

    now = time.time()
    hit = _dir_cache.get(rel_path)
    if hit and (now - hit["ts"]) < TREE_CACHE_TTL:
        return hit["data"]

    data = _list_children(full)
    _dir_cache[rel_path] = {"ts": now, "data": data}
    return data


# ---- 覆蓋 /api/tree 路由：支援 ?path= 惰性掃描 ----
_orig_api_tree = main.app.view_functions.get("api_tree")

def api_tree_lazy():
    rel = main.request.args.get("path", "").strip()
    if rel:
        return {"tree": get_dir_children(rel)}
    return _orig_api_tree()

main.app.view_functions["api_tree"] = api_tree_lazy
print("[保丁][只掃單層] 已覆蓋 /api/tree（支援 ?path= 惰性載入）")


# ---- 檔案變動 → 惰性快取失效 ----
_orig_on_any_event = main.FileChangeHandler.on_any_event

def _on_any_event_patched(self, event):
    _dir_cache.clear()                     # 任何變動都清掉惰性快取（便宜、保證新鮮）
    return _orig_on_any_event(self, event)

main.FileChangeHandler.on_any_event = _on_any_event_patched
print("[保丁][只掃單層] 已覆蓋 FileChangeHandler.on_any_event（惰性快取失效）")
