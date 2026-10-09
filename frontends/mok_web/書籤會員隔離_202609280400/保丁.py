# -*- coding: utf-8 -*-
"""
書籤會員隔離 補丁 v1.0  (2026-09-28, author: indexPage)
=====================================================
目標：/api/bookmark/* 依「會員 / 訪客」各自隔離；訪客套匿名沙盒機制（限時刪、限時可拉回會員房）。
對應 1-8 點：uuid id + user_id / add 401 / list 只回自己 / delete,rename 用 id 驗 owner /
舊資料歸 admin / 重啟生效 / 訪客各自隔離(guest:<sid>) 限時刪 + 登入拉回會員房。
不改核心 mok_web.py（monkey patch app.view_functions）。停用：目錄改名加 _ 前綴再重啟。
"""
import os
import sys
import json
import time
import uuid
import threading
import traceback

main = sys.modules["__main__"]

_HOME = os.path.expanduser("~/.%s" % getattr(main, "MOKAGI_home", "mok"))
BOOKMARK_FILE = os.path.join(_HOME, "html", "webTools", "書籤", "書籤.json")

LEGACY_OWNER = "admin"
JANITOR_EVERY = 60 * 10
FALLBACK_TTL_IDLE = 3600 * 6   # 2026-09-30 統一 TTL＝6 小時
FALLBACK_TTL_MAX = 3600 * 6
FALLBACK_COOKIE = "mok_anon"


def _LOG(m):
    print("[bookmark-iso] %s" % m)


def _cfg(key, default):
    try:
        v = (getattr(main, "mok_anon", None) or {}).get(key)
        return v if v else default
    except Exception:
        return default


def _ttl_idle():
    return int(_cfg("ttl_idle", FALLBACK_TTL_IDLE))


def _ttl_max():
    return int(_cfg("ttl_max", FALLBACK_TTL_MAX))


def _cookie_name():
    return _cfg("cookie_name", FALLBACK_COOKIE)


def _is_guest(v):
    v = str(v or "")
    return v.startswith("web_guest_") or v.startswith("guest:")


def _load():
    if not os.path.exists(BOOKMARK_FILE):
        return []
    try:
        with open(BOOKMARK_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, list) else []
    except Exception as e:
        _LOG("load fail: %s" % e)
        return []


def _save(bms):
    try:
        os.makedirs(os.path.dirname(BOOKMARK_FILE), exist_ok=True)
        with open(BOOKMARK_FILE, "w", encoding="utf-8") as f:
            json.dump(bms, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        _LOG("save fail: %s" % e)
        return False


# ---------------- 身分解析（與匿名沙盒同一把 cookie） ----------------
def _session_user():
    try:
        return main._session_member_user()
    except Exception:
        return None


def _cookie_sid():
    try:
        from flask import request as _rq
        raw = (_rq.cookies.get(_cookie_name()) or "").strip()
    except Exception:
        return None
    if not raw:
        return None
    return (raw.split("|", 1)[0].strip()) or None


def bookmark_tenant(data=None):
    """登入 -> session 會員帳號；訪客 -> guest:<mok_anon sid>。取不到回 None。"""
    u = _session_user()
    if u:
        return u
    if isinstance(data, dict):
        c = str(data.get("user_id") or "").strip()
        if _is_guest(c):
            return c
    sid = _cookie_sid()
    if sid:
        return "guest:" + sid
    return None


def _can_read_all(t):
    try:
        return bool(main._can_read_all(t))
    except Exception:
        return str(t) in ("admin", "root")


# ---------------- 路由（覆蓋核心 view） ----------------
def _deny(msg="unauthorized: 請先登入會員，或重新整理頁面後再試"):
    return {"success": False, "error": msg, "code": "UNAUTHORIZED"}, 401


def bookmark_add():
    data = main.request.get_json(silent=True) or {}
    tenant = bookmark_tenant(data)
    if not tenant:
        return _deny()
    conv_id = str(data.get("conv_id", "")).strip()
    if not conv_id or conv_id == "?":
        return {"success": False, "error": "缺少 conv_id"}, 400
    bms = _load()
    for bm in bms:
        if str(bm.get("conv_id", "")) == conv_id and str(bm.get("user_id", "")) == tenant:
            return {"success": True, "message": "已存在"}
    bms.append({
        "id": uuid.uuid4().hex,
        "user_id": tenant,
        "conv_id": conv_id,
        "snippet": str(data.get("snippet", ""))[:200],
        "agent": data.get("agent", ""),
        "role": data.get("role", ""),
        "title": str(data.get("title") or "")[:60],
        "timestamp": data.get("timestamp", time.time()),
    })
    if not _save(bms):
        return {"success": False, "error": "書籤儲存失敗（請檢查檔案權限或磁碟空間）"}, 500
    return {"success": True, "message": "已加入書籤"}


def bookmark_list():
    tenant = bookmark_tenant()
    if not tenant:
        return {"success": True, "bookmarks": []}
    bms = _load()
    if not _can_read_all(tenant):
        bms = [b for b in bms if str(b.get("user_id", "")) == tenant]
    bms.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    return {"success": True, "bookmarks": bms}


def bookmark_delete():
    data = main.request.get_json(silent=True) or {}
    tenant = bookmark_tenant(data)
    if not tenant:
        return _deny()
    bid = str(data.get("id") or "").strip()
    if not bid:
        return {"success": False, "error": "缺少 id（已廢除 index 定址，請更新前端）"}, 400
    bms = _load()
    read_all = _can_read_all(tenant)
    for i, bm in enumerate(bms):
        if str(bm.get("id", "")) == bid:
            if not read_all and str(bm.get("user_id", "")) != tenant:
                return {"success": False, "error": "無權限刪除此書籤"}, 403
            bms.pop(i)
            _save(bms)
            return {"success": True, "message": "已刪除書籤"}
    return {"success": False, "error": "找不到該書籤"}, 404


def bookmark_rename():
    data = main.request.get_json(silent=True) or {}
    tenant = bookmark_tenant(data)
    if not tenant:
        return _deny()
    bid = str(data.get("id") or "").strip()
    if not bid:
        return {"success": False, "error": "缺少 id（已廢除 conv_id 定址，請更新前端）"}, 400
    bms = _load()
    read_all = _can_read_all(tenant)
    for bm in bms:
        if str(bm.get("id", "")) == bid:
            if not read_all and str(bm.get("user_id", "")) != tenant:
                return {"success": False, "error": "無權限修改此書籤"}, 403
            bm["title"] = str(data.get("title") or "").strip()[:60]
            _save(bms)
            return {"success": True, "message": "已更新書籤標題"}
    return {"success": False, "error": "找不到該書籤"}, 404


def _install_view(name, fn):
    try:
        if name in main.app.view_functions:
            main.app.view_functions[name] = fn
            return True
    except Exception as e:
        _LOG("install %s fail: %s" % (name, e))
    return False


# ---------------- 6. 舊資料一次性歸 admin ----------------
def migrate_legacy():
    bms = _load()
    if not bms:
        return 0
    n = 0
    for bm in bms:
        changed = False
        if not bm.get("id"):
            bm["id"] = uuid.uuid4().hex
            changed = True
        if not bm.get("user_id"):
            bm["user_id"] = LEGACY_OWNER
            changed = True
        if changed:
            n += 1
    if n:
        _save(bms)
        _LOG("legacy migrated %d bookmark(s) -> %s" % (n, LEGACY_OWNER))
    return n


# ---------------- 8. 限時可拉回會員房 ----------------
def migrate_guest_to_member(username=None, sid=None):
    username = username or _session_user()
    sid = sid or _cookie_sid()
    if not username or not sid:
        return 0
    keys = {"guest:" + sid, "web_guest_" + sid}
    bms = _load()
    n = 0
    for bm in bms:
        if str(bm.get("user_id", "")) in keys:
            bm["user_id"] = username
            n += 1
    if n:
        _save(bms)
        _LOG("guest bookmarks pulled back: %d -> %s" % (n, username))
    return n


def _bm_before_request():
    try:
        u = _session_user()
        if u:
            migrate_guest_to_member(u)
    except Exception:
        pass
    return None


# ---------------- 8. 限時刪（janitor） ----------------
def janitor_once():
    bms = _load()
    if not bms:
        return 0
    now = time.time()
    groups = {}
    for bm in bms:
        t = str(bm.get("user_id", ""))
        if not _is_guest(t):
            continue
        ts = float(bm.get("timestamp") or 0)
        g = groups.get(t)
        if g is None:
            groups[t] = [ts, ts]
        else:
            if ts < g[0]:
                g[0] = ts
            if ts > g[1]:
                g[1] = ts
    idle = _ttl_idle()
    mx = _ttl_max()
    dead = set()
    for t, pair in groups.items():
        if (now - pair[1]) > idle or (now - pair[0]) > mx:
            dead.add(t)
    if not dead:
        return 0
    kept = [b for b in bms if str(b.get("user_id", "")) not in dead]
    removed = len(bms) - len(kept)
    _save(kept)
    _LOG("janitor swept %d guest bookmark(s) across %d tenant(s)" % (removed, len(dead)))
    return removed


def _janitor_loop():
    while True:
        try:
            janitor_once()
        except Exception:
            _LOG("janitor error: %s" % traceback.format_exc().splitlines()[-1])
        time.sleep(JANITOR_EVERY)


_started = False


def start_janitor():
    global _started
    if _started:
        return
    _started = True
    try:
        threading.Thread(target=_janitor_loop, name="bookmark-janitor", daemon=True).start()
        _LOG("janitor started (every %ds, idle=%dm max=%dh)"
             % (JANITOR_EVERY, _ttl_idle() // 60, _ttl_max() // 3600))
    except Exception as e:
        _LOG("janitor start fail: %s" % e)


# ---------------- 掛載 ----------------
migrate_legacy()
start_janitor()

try:
    main.app.before_request(_bm_before_request)
except Exception as e:
    _LOG("before_request hook fail: %s" % e)

_ok = []
for _name, _fn in (("bookmark_add", bookmark_add), ("bookmark_list", bookmark_list),
                   ("bookmark_delete", bookmark_delete), ("bookmark_rename", bookmark_rename)):
    if _install_view(_name, _fn):
        _ok.append(_name)

main.bookmark_iso = {
    "file": BOOKMARK_FILE,
    "tenant": bookmark_tenant,
    "migrate_legacy": migrate_legacy,
    "migrate_guest_to_member": migrate_guest_to_member,
    "janitor_once": janitor_once,
}

_LOG("loaded: views=%s ttl idle=%dm max=%dh legacy=%s"
     % (",".join(_ok) or "NONE", _ttl_idle() // 60, _ttl_max() // 3600, LEGACY_OWNER))
