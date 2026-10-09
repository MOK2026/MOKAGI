# -*- coding: utf-8 -*-
"""
mok_web 保丁（補丁）載入器 v2.0
================================
位置: .mok/frontends/mok_web/保丁.py
作用: mok_web.py 啟動時自動掃描並載入本目錄下所有補丁，實現「不改核心、無限擴充」。
      核心檔 mok_web.py 只加一行引用。

【目錄規則】
    .mok/frontends/mok_web/
    ├── 保丁.py                          ← 本載入器（勿刪勿改）
    ├── 載入順序.txt                      ← 載入順序清單（一行一個補丁目錄名；可選）
    └── <補丁名>_<YYYYMMDDHHMM>/
        ├── 保丁.py                       ← 補丁程式碼（必需，檔名固定）
        └── README.md                     ← 補丁說明（可選）

【補丁寫法】
    補丁是獨立 Python 模組，透過 sys.modules['__main__'] 取得核心 mok_web，
    重新定義函數後覆蓋回去（monkey patch）：
        import sys
        main = sys.modules['__main__']
        def get_file_tree(path, depth=0): ...
        main.get_file_tree = get_file_tree
    （補丁可用 __file__ 相對定位自己的目錄／資料檔。）

【載入順序】★ v2.0 變更：改讀「載入順序.txt」，不再靠目錄名排序
    1) 有 載入順序.txt → 清單內者依清單由上到下載入（後載入者優先）；
                        清單外的新補丁自動接在最後（依目錄名排序）。
    2) 無 載入順序.txt → 退回舊行為：全部依目錄名排序載入。
【安全】單一補丁失敗只印錯誤，不影響其他補丁與核心啟動。
【停用】把補丁目錄改名（前面加 _）即可停用，無需改核心、也無需改清單。
"""
import os, sys, glob, importlib.util, traceback

# ★ 定位保丁目錄：不依賴自身 __file__（被 exec 執行時 __file__ 是 mok_web.py 的路徑）。
#   一律從核心模組 __main__ 推斷：mok_web.py 所在目錄 + /mok_web
_main_mod = sys.modules.get('__main__')
_web_dir = os.path.dirname(os.path.abspath(getattr(_main_mod, '__file__', os.getcwd())))
PATCH_DIR = os.path.join(_web_dir, 'mok_web')
_PATCH_FILENAME = '保丁.py'          # 每個補丁目錄內的補丁檔名（固定）
_ORDER_FILENAME = '載入順序.txt'      # 載入順序清單（可選）


def _read_order():
    """讀取 載入順序.txt；回傳清單（可能為空 list）或 None（無檔／讀取失敗）。"""
    path = os.path.join(PATCH_DIR, _ORDER_FILENAME)
    if not os.path.exists(path):
        return None
    order = []
    try:
        with open(path, encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                order.append(line)
    except Exception as e:
        print(f"[保丁] ⚠️ 讀取 {_ORDER_FILENAME} 失敗，改用目錄名排序：{e}")
        return None
    return order


def _resolve_order():
    """回傳 (有序目錄路徑 list, 來源說明或 None)。"""
    dirs = {}
    for d in glob.glob(os.path.join(PATCH_DIR, '*/')):
        if os.path.isdir(d):
            dirs[os.path.basename(d.rstrip(os.sep))] = d
    order = _read_order()
    if order is None:
        return [dirs[k] for k in sorted(dirs)], None
    listed, seen = [], set()
    for k in order:
        if k in dirs:
            if k not in seen:
                listed.append(k)
                seen.add(k)
        else:
            print(f"[保丁] ⚠️ {_ORDER_FILENAME} 列出的「{k}」不存在，略過")
    rest = sorted(k for k in dirs if k not in seen)
    if rest:
        print(f"[保丁] ℹ️  {_ORDER_FILENAME} 未列出的補丁 {len(rest)} 個，接在清單後依序載入")
    return [dirs[k] for k in listed] + [dirs[k] for k in rest], _ORDER_FILENAME


def _load_one(patch_file, label):
    """用 importlib 載入單一補丁模組"""
    name = 'mokweb_patch_' + label
    spec = importlib.util.spec_from_file_location(name, patch_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print(f"[保丁] ✅ {label} 已載入 ← {os.path.relpath(patch_file, PATCH_DIR)}")


def load_patches():
    """依 載入順序.txt（或退回目錄名排序）掃描並載入所有補丁"""
    ordered, src = _resolve_order()
    total, ok = len(ordered), 0
    if src:
        print(f"[保丁] 🧭 依 {src} 載入（共 {total} 個目錄）")
    for d in ordered:
        label = os.path.basename(d.rstrip(os.sep))
        if label.startswith('_'):
            print(f"[保丁] ⏸️  {label} 已停用（目錄前綴 _），跳過")
            continue
        pf = os.path.join(d, _PATCH_FILENAME)
        if not os.path.exists(pf):
            print(f"[保丁] ⚠️  {label} 缺少 {_PATCH_FILENAME}，跳過")
            continue
        try:
            _load_one(pf, label)
            ok += 1
        except Exception as e:
            print(f"[保丁] ❌ {label} 載入失敗: {e}")
            traceback.print_exc()
    print(f"[保丁] 掃描完畢：{ok}/{total} 個補丁成功載入")
    return ok


# 被 mok_web.py 以 exec 方式引用時自動載入。
# ★ 注意：exec 環境的 __name__ 是 '__main__'，不能用 if __name__ 判斷，直接執行。
_PATCHES_LOADED = globals().get('_PATCHES_LOADED', False)
if not _PATCHES_LOADED:
    globals()['_PATCHES_LOADED'] = True
    load_patches()
