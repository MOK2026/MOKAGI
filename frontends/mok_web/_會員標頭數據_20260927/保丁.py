# -*- coding: utf-8 -*-
"""
會員標頭數據補丁 v1.0  (2026-09-27, by indexPage)
==================================================
目的（單一後端數據源，供首頁 header 顯示）：
  1) #headerMemberName：已登入 -> 會員帳號；未登入 -> 伺服器頒發的臨時 ID（guest:<uuid>）。
     未登入者絕不回空字串，也不使用寫死的稱呼字樣（一律由後端給值）。
  2) #headerTokenBalance：一律顯示「每月 Token 額度 - 已用 = 剩餘」。
     - 額度 = plans.monthly_tokens（依該用戶的 plan；admin/無限方案 -> unlimited，前端顯示 無限）
     - 已用 = users.monthly_used（跨月自動歸零，僅呈現）
     - 剩餘 = users.balance_tokens
  3) 所有會員層級（admin / vip / pro / free / 未登入）共用同一支 API，
     資料以「登入 session 的會員帳號」分開，前端不再自行拼接或寫死層級文字。
"""

import os, sys, glob, json, sqlite3, time, uuid

main = sys.modules.get('__main__')
app = getattr(main, 'app', None)

_HERE = os.path.dirname(os.path.abspath(__file__))
_MW_DIR = os.path.dirname(_HERE)


def _find_member_db():
    """沿用「會員系統」補丁的 member.db（唯讀）。"""
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
    'admin': '系統管理（admin）',
    'vip': '尊貴版（vip）',
    'pro': '專業版（pro）',
    'free': '免費版（free）',
    'guest': '未登入（臨時 ID）',
}


def _month_key():
    return time.strftime('%Y-%m')


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


def _temp_id():
    """未登入者的臨時 ID：優先取核心頒發的 tenant（guest:<uuid>，簽章 cookie/session）。"""
    for fn in ('resolve_tenant_arg', 'resolve_tenant'):
        f = getattr(main, fn, None)
        if callable(f):
            try:
                v = f()
                if v:
                    return str(v)
            except Exception:
                pass
    try:
        from flask import session as _s
        g = _s.get('mok_guest_id')
        if not g:
            g = 'guest:' + uuid.uuid4().hex[:16]
            _s['mok_guest_id'] = g
        return str(g)
    except Exception:
        return 'guest:' + uuid.uuid4().hex[:16]


def _read_user(username):
    if not MEMBER_DB or not os.path.exists(MEMBER_DB) or not username:
        return None, None
    conn = None
    try:
        conn = sqlite3.connect('file:%s?mode=ro' % MEMBER_DB, uri=True, timeout=10)
        conn.row_factory = sqlite3.Row
        u = conn.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        p = None
        if u is not None:
            try:
                p = conn.execute('SELECT * FROM plans WHERE plan=?', (u['plan'],)).fetchone()
            except Exception:
                p = None
        return (dict(u) if u is not None else None), (dict(p) if p is not None else None)
    except Exception:
        return None, None
    finally:
        if conn is not None:
            try:
                conn.close()
            except Exception:
                pass


def _int(v):
    try:
        return int(v or 0)
    except Exception:
        return 0


if app is not None:
    @app.route('/api/member/header', methods=['GET'])
    def api_member_header():
        from flask import jsonify
        username = _session_user()
        tk = _month_key()
        user = plan = None

        if username:
            user, plan = _read_user(username)
            if user is None:
                username = None

        if username and user is not None:
            used = _int(user.get('monthly_used'))
            u_month = str(user.get('month_key') or '')
            if u_month and u_month != tk:
                used = 0
            quota = _int((plan or {}).get('monthly_tokens'))
            remaining = _int(user.get('balance_tokens'))
            tier = str(user.get('plan') or 'free')
            unlimited = (tier == 'admin') or quota <= 0
            try:
                agents = json.loads((plan or {}).get('agents') or '[]')
            except Exception:
                agents = []
            return jsonify({
                'success': True, 'logged_in': True,
                'username': username, 'display_name': username, 'temp_id': username,
                'tier': tier, 'plan_label': _PLAN_LABEL.get(tier, tier),
                'quota': quota, 'used': used, 'remaining': remaining,
                'unlimited': bool(unlimited), 'month_key': tk,
                'agents': agents, 'guest': False,
            })

        tid = _temp_id()
        return jsonify({
            'success': True, 'logged_in': False,
            'username': None, 'display_name': tid, 'temp_id': tid,
            'tier': 'guest', 'plan_label': _PLAN_LABEL['guest'],
            'quota': 0, 'used': 0, 'remaining': 0,
            'unlimited': False, 'month_key': tk, 'agents': [], 'guest': True,
        })

    print('[會員標頭數據] OK 補丁已載入 | DB=%s | 路由: /api/member/header' % (MEMBER_DB,))
else:
    print('[會員標頭數據] 未取得 app，略過路由註冊')
