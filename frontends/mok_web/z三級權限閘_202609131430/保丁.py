# -*- coding: utf-8 -*-
"""
三級權限閘補丁 v1.0  (2026-09-13)
==================================
由 mok_web/保丁.py 載入器自動掃描載入（目錄名 z 開頭，排序在 ASCII 命名的補丁之後）。

【目的】落地「三類身份 × 可見頁面 / 可用 agent」：
    身份      可見頁面                                  可用 agent
    admin     全部                                      全部
    member    依方案 pages 白名單（Plans 補丁處理）        依方案 agents 白名單
    未登入    僅 /login /register /plans /logout      僅 free 方案 agents

【作法】不改核心，只做兩件事：
    1. wrap main.get_env_files → /api/env_files 只吐該身份可用的 agent（列表層）
    2. app.before_request      → 未登入者瀏覽受限 HTML 頁面時導向 /login

【與其他補丁分工】
    - 會員系統_202608311340：/login /register /member + 聊天攔截（agent 權限 / 扣款）
    - Plans後台_202609051030：/admin/plans /plans + 已登入會員的 pages 白名單檢查
    - 本補丁：未登入訪客的頁面閘門 + agent 列表過濾（前面兩者未涵蓋之處）

【停用】目錄改名加底線開頭，重啟即停用。
"""
import os, sys, json, sqlite3, threading

main = sys.modules.get('__main__')
app = getattr(main, 'app', None)

_PATCH_DIR = os.path.dirname(os.path.abspath(__file__))
_MOK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_PATCH_DIR)))
MEMBER_DB = os.path.join(_MOK_DIR, 'frontends', 'mok_web', '會員系統_202608311340', 'member.db')

# 未登入（guest）仍可瀏覽的路徑（前綴比對）
GUEST_PAGES = ['/login', '/register', '/plans', '/logout']
# 完全放行的前綴（API / 靜態資源 / 各後台自行判斷）
ALWAYS_OPEN = ['/api', '/static', '/assets', '/img', '/js', '/css',
               '/socket.io', '/admin', '/_test', '/favicon', '/project']

ADMIN_USERS = set(x.strip() for x in (os.environ.get('ADMIN_USERNAMES') or 'admin').split(',') if x.strip())

_db_lock = threading.Lock()


def _conn():
    c = sqlite3.connect(MEMBER_DB, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def _session_user():
    try:
        from flask import session as _S
        return _S.get('member_user') or None
    except Exception:
        return None


def _plan_agents(plan):
    try:
        with _db_lock, _conn() as c:
            row = c.execute('SELECT agents FROM plans WHERE plan=?', (plan,)).fetchone()
        if row:
            v = json.loads(row['agents'] or '[]')
            return v if isinstance(v, list) else ['*']
    except Exception:
        pass
    return ['*']


def _user_plan(username):
    try:
        with _db_lock, _conn() as c:
            row = c.execute('SELECT plan FROM users WHERE username=?', (username,)).fetchone()
        return row['plan'] if row else 'free'
    except Exception:
        return 'free'


# ---------- 1) agent 列表過濾 ----------
_orig_get_env_files = getattr(main, 'get_env_files', None)


def get_env_files():
    files = _orig_get_env_files() if _orig_get_env_files else []
    try:
        who = _session_user()
        if who and who in ADMIN_USERS:
            return files
        plan = _user_plan(who) if who else 'free'
        allow = _plan_agents(plan)
        if '*' in allow or not allow:
            return files
        allow = set(allow)
        return [f for f in files if f.lstrip('.') in allow]
    except Exception:
        return files


if _orig_get_env_files is not None:
    main.get_env_files = get_env_files


# ---------- 2) 未登入頁面閘門 ----------
def _open(path):
    for p in ALWAYS_OPEN:
        if path == p or path.startswith(p + '/'):
            return True
    seg = path.rsplit('/', 1)[-1]
    if '.' in seg:          # 帶副檔名 → 靜態資源
        return True
    return False


def _guest_ok(path):
    return any(path == p or path.startswith(p + '/') for p in GUEST_PAGES)


def _page_guard():
    if app is None:
        return None
    try:
        from flask import request as _R, redirect as _Rd
        if _R.method not in ('GET', 'HEAD'):
            return None
        path = _R.path or '/'
        if _open(path) or _guest_ok(path):
            return None
        if _session_user():
            return None                     # 已登入 → 交給 Plans 補丁的 pages 白名單處理
        from urllib.parse import quote
        return _Rd('/login?next=' + quote(path))
    except Exception:
        return None


if app is not None:
    try:
        app.before_request(_page_guard)
        print('[三級權限閘] 已掛載：未登入導向 /login、agent 列表依方案過濾')
    except Exception as e:
        print('[三級權限閘] 掛載失敗:', e)
else:
    print('[三級權限閘] 找不到 app，未掛載')
