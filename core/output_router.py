# -*- coding: utf-8 -*-
"""
產物三層落點權威來源 (output_router)  v1.0  2026-09-28
=====================================================
問題：
    任何 agent 產出檔案時，落點靠「慣例」而非系統保證——
      第 1 層 未登入訪客產物混進侍女房間（沒人告訴 agent 要寫 anon）
      第 2 層 已登入會員沒有依身分導向
      第 3 層 owner 的 jobs/ 全憑自律，零強制

本模組是唯一的權威來源：依「身分」決定輸出目錄（不再散落各處）。

    第 1 層  未登入 / 訪客  ->  ~/.mok/_tmp/anon/<sid>/
    第 2 層  已登入會員      ->  ~/.mok/user/<username>/
    第 3 層  owner(admin)   ->  ~/.mok/agent/<agent_name>/jobs/<job|日期>/

用法：
    from output_router import output_dir_for_request, ensure_dir
    path, role = output_dir_for_request(user_id, agent_name, sid=anon_sid, job=job_name)
    ensure_dir(path)
"""
import os
import re
import time
import sqlite3
from db_conn import connect

# ----------------------------------------------------------------------
def _home():
    """回傳 ~/.mok 絕對路徑（本檔位於 <home>/core/output_router.py）。"""
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _safe(name, default='unknown'):
    """把任意字串淨化成安全的單層目錄名（防路徑穿越）。"""
    s = str(name or '').strip()
    s = s.replace('\\', '_').replace('/', '_')
    s = s.replace('..', '_')
    s = re.sub(r'[\x00-\x1f<>:"|?*]', '_', s)
    s = s.strip('. ')
    return s or default


# ----------------------------------------------------------------------
# 會員名冊（member.db）：用來判斷 admin / member
# ----------------------------------------------------------------------
_MEMBER_DB_CACHE = {'path': None, 'mtime': 0}


def _member_db_path():
    env = os.environ.get('MOK_MEMBER_DB')
    if env and os.path.exists(env):
        return env
    import glob
    cands = glob.glob(os.path.join(_home(), 'frontends', 'mok_web', '*', 'member.db'))
    if not cands:
        return None
    # 取最新修改的那個
    cands.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return cands[0]


def _member_plan(username):
    """查 member.db，回傳該使用者 plan（admin/vip/free…）；查不到回 None。"""
    if not username:
        return None
    try:
        db = _member_db_path()
        if not db or not os.path.exists(db):
            return None
        con = connect(db, readonly=True, timeout=10.0)
        try:
            row = con.execute('SELECT plan FROM users WHERE username = ?', (str(username),)).fetchone()
        finally:
            con.close()
        return row[0] if row else None
    except Exception:
        return None


# ----------------------------------------------------------------------
# 身分分類（唯一來源）
# ----------------------------------------------------------------------
def classify_role(user_id):
    """把 user_id 分成 'anon' / 'member' / 'admin'。

    規則（與 resolve_tenant 一致）：
      - 空 / guest:* / web_guest_*  -> anon
      - member.db 內 plan=admin 或 root -> admin
      - 其它已登入帳號 -> member
    """
    uid = str(user_id or '').strip()
    if not uid:
        return 'anon'
    if uid.startswith('guest:') or uid.startswith('web_guest_') or uid == 'guest':
        return 'anon'
    if uid in ('admin', 'root'):
        return 'admin'
    plan = _member_plan(uid)
    if plan in ('admin', 'root'):
        return 'admin'
    # 查得到會員 -> member；查不到（非會員歷史帳號等）也歸 member（落點較安全）
    return 'member'


def anon_key(user_id, sid=None):
    """anon 落點的目錄名：優先用簽章 cookie 的 sid，否則退回 guest uuid。"""
    if sid:
        return _safe(sid, 'anon')
    uid = str(user_id or '')
    if uid.startswith('guest:'):
        return _safe(uid.split(':', 1)[1], 'anon')
    if uid.startswith('web_guest_'):
        return _safe(uid[len('web_guest_'):], 'anon')
    return _safe(uid, 'anon')


# ----------------------------------------------------------------------
# 落點解析
# ----------------------------------------------------------------------
def resolve_output_dir(role, agent_name, user_id=None, sid=None, job=None):
    """依角色回傳輸出目錄（不建立）。"""
    home = _home()
    if role == 'anon':
        return os.path.join(home, '_tmp', 'anon', anon_key(user_id, sid))
    if role == 'member':
        return os.path.join(home, 'user', _safe(user_id, 'member'))
    # admin / owner
    day = time.strftime('%Y-%m-%d')
    return os.path.join(home, 'agent', _safe(agent_name, 'agent'), 'jobs', _safe(job, day))


def output_dir_for_request(user_id, agent_name, sid=None, job=None):
    """前端呼叫入口：回傳 (輸出目錄, 角色)。"""
    role = classify_role(user_id)
    return resolve_output_dir(role, agent_name, user_id=user_id, sid=sid, job=job), role


def ensure_dir(path):
    """建立目錄（含上層），失敗不拋例外。"""
    try:
        if path:
            os.makedirs(path, exist_ok=True)
        return path
    except Exception:
        return path


def human_rule(role):
    """給系統提示用的一句話規則說明。"""
    return {
        'anon':   '未登入訪客 -> ~/.mok/_tmp/anon/<sid>/（匿名沙盒，登入後整包 migrate 進會員房間）',
        'member': '已登入會員 -> ~/.mok/user/<會員帳號>/',
        'admin':  'owner(admin) -> ~/.mok/agent/<agent自己房間>/jobs/<jobs名>/',
    }.get(role, '')


# 房間根目錄：侍女 -> ~/.mok/agent/<名字>/；會員 -> ~/.mok/user/<會員帳號>/
AGENT_ROOT_NAME = 'agent'
MEMBER_ROOT_NAME = 'user'


def agent_root():
    return os.path.join(_home(), AGENT_ROOT_NAME)


def member_root():
    return os.path.join(_home(), MEMBER_ROOT_NAME)


def is_member(username):
    # 是否為真人會員（member.db 內查得到 plan）
    return _member_plan(username) is not None


def room_root_for(name):
    # 依名字回傳房間根目錄：會員 -> ~/.mok/user；其餘 -> ~/.mok/agent
    return member_root() if is_member(name) else agent_root()


def room_dir_for(name):
    # 依名字回傳房間目錄（單層，未建立）
    return os.path.join(room_root_for(name), _safe(name, 'unknown'))
