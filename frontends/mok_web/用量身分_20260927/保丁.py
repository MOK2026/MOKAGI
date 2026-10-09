# -*- coding: utf-8 -*-
"""用量與身分補丁 v1.0 (2026-09-27 by 凜)."""
import os, sys, glob, sqlite3, time
main = sys.modules.get("__main__")
app = getattr(main, "app", None)

_HERE = os.path.dirname(os.path.abspath(__file__))
_MW_DIR = os.path.dirname(_HERE)
TOKEN_DB = os.path.expanduser('~/.mok/.memory/chat_history.db')


def _find_member_db():
    cands = []
    for p in glob.glob(os.path.join(_MW_DIR, '*', 'member.db')):
        if '會員系統' in p:
            cands.insert(0, p)
        else:
            cands.append(p)
    for p in cands:
        if os.path.exists(p):
            return p
    return None


MEMBER_DB = _find_member_db()

_PLAN_LABEL = {
    'admin': '系統管理（admin）', 'root': '系統管理（admin）',
    'vip': '尊貴版（vip）', 'pro': '專業版（pro）', 'free': '免費版（free）',
}
_UNLIMITED = ('admin', 'root')


def _month():
    return time.strftime('%Y-%m')


def _conn():
    c = sqlite3.connect('file:%s?mode=ro' % MEMBER_DB, uri=True, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _session_user():
    f = getattr(main, '_session_member_user', None)
    if callable(f):
        try:
            u = f()
            if u:
                return str(u)
        except Exception:
            pass
    return None


def _anon_identity():
    try:
        from flask import g
        sid = getattr(g, '_anon_sid', None) or getattr(g, '_anon_new_sid', None)
        if sid:
            return 'guest:' + str(sid)
    except Exception:
        pass
    return None


def _user_row(username):
    try:
        with _conn() as c:
            u = c.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            if u is None:
                return None, None
            p = c.execute('SELECT * FROM plans WHERE plan=?', (u['plan'],)).fetchone()
            return dict(u), (dict(p) if p is not None else None)
    except Exception:
        return None, None


def _free_plan():
    try:
        with _conn() as c:
            r = c.execute("SELECT * FROM plans WHERE plan='free'").fetchone()
            return dict(r) if r else None
    except Exception:
        return None


def _month_tokens(user_id):
    try:
        with sqlite3.connect('file:%s?mode=ro' % TOKEN_DB, uri=True, timeout=10) as c:
            r = c.execute(
                "SELECT COALESCE(SUM(total_tokens),0) FROM token_usage "
                "WHERE user_id=? AND strftime('%Y-%m', datetime(timestamp,'unixepoch','localtime'))=?",
                (user_id, _month())).fetchone()
            return int(r[0] or 0)
    except Exception:
        return 0


def _sync_plan_read(username):
    """★ 2026-10-07 凜：讀取兜底 —— 讓 /api/member/header 的 plan/餘額永遠與旗標一致。
    實作在會員系統補丁；找不到就靜默略過（不影響顯示）。"""
    if not username:
        return
    try:
        import sys as _sys
        for _n, _m in list(_sys.modules.items()):
            if _n.startswith('mokweb_patch_') and hasattr(_m, 'sync_plan'):
                _m.sync_plan(username)
                return
    except Exception as _e:
        print('[anon-usage] sync_plan 失敗: %s' % _e)


def _payload():
    user = _session_user()
    month = _month()
    if user:
        _sync_plan_read(user)
        u, p = _user_row(user)
        plan = (u or {}).get('plan') or 'free'
        quota = int((p or {}).get('monthly_tokens') or 0)
        used = int((u or {}).get('monthly_used') or 0)
        remaining = int((u or {}).get('balance_tokens') or 0)
        unlimited = (str(plan).lower() in _UNLIMITED) or quota <= 0
        return {
            'success': True, 'logged_in': True,
            'identity': user, 'username': user, 'temp_id': '',
            'display_name': (u or {}).get('display_name') or user,
            'plan': plan, 'plan_label': _PLAN_LABEL.get(str(plan).lower(), str(plan)),
            'month': month, 'monthly_quota': (None if unlimited else quota),
            'used': used, 'remaining': (None if unlimited else remaining),
            'unit': 'tokens', 'unlimited': bool(unlimited),
        }
    identity = _anon_identity()
    fpl = _free_plan() or {}
    try:
        quota = int(fpl.get('monthly_tokens') or 0)
    except Exception:
        quota = 0
    _rl = fpl.get('requires_login')
    public = int(_rl if _rl is not None else 1) == 0
    if not public:
        quota = 0
    used = _month_tokens(identity) if identity else 0
    unlimited = quota <= 0
    return {
        'success': True, 'logged_in': False,
        'identity': identity or '', 'username': '', 'temp_id': identity or '',
        'display_name': identity or '訪客',
        'plan': 'free' if public else 'guest',
        'plan_label': '免費版（free）' if public else '未登入（臨時 ID）',
        'month': month, 'monthly_quota': (None if unlimited else quota),
        'used': used, 'remaining': (None if unlimited else max(0, quota - used)),
        'unit': 'tokens', 'unlimited': bool(unlimited),
    }


if app is not None:
    @app.route('/api/me/usage')
    def api_me_usage():
        from flask import jsonify
        return jsonify(_payload())

    @app.route('/api/member/header')
    def api_member_header():
        from flask import jsonify
        return jsonify(_payload())

    print('[anon-usage] OK loaded | DB=%s | routes: /api/me/usage, /api/member/header' % (MEMBER_DB,))
