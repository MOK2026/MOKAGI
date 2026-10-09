# -*- coding: utf-8 -*-
"""
Admin 會員管理後台 v1.0
======================
掛載：由 mok_web/保丁.py 載入器自動掃描載入（目錄名排序於「會員系統」之後，後載入優先）

功能（路徑）：
  • 會員列表：/admin/member
  • 新增會員：/admin/member/add
  • 改方案：/admin/member/<u>/plan
  • 加減餘額：/admin/member/<u>/balance
  • 改密碼：/admin/member/<u>/password
  • 停用/啟用：/admin/member/<u>/toggle
  • 刪除會員：/admin/member/<u>/delete
  • JSON API：/api/admin/members

安全設計：
  - 僅 ADMIN_USERNAMES（預設 admin）可進入 /admin/*
  - 停用會員：無法登入、無法使用任何 agent
  - admin 帳號不可被停用 / 刪除；不可刪除自己
  - 管理 POST 皆需 CSRF token
  - 所有管理操作寫入 admin_log 稽核紀錄
"""

import sys, os, sqlite3, time, json, hashlib, secrets, threading, asyncio, hmac
from urllib.parse import quote as urllib_quote
main = sys.modules.get('__main__')
import mokagi as _mokagi
from flask import request, session, redirect, jsonify, render_template_string, abort
app = getattr(main, 'app', None)
_PATCH_DIR = os.path.dirname(os.path.abspath(__file__))

_member_mod = None
for _n, _m in list(sys.modules.items()):
    if _n.startswith('mokweb_patch_') and '會員' in _n:
        _member_mod = _m
        break

if _member_mod is not None:
    MEMBER_DB = getattr(_member_mod, 'MEMBER_DB', None) or os.path.join(_PATCH_DIR, 'member.db')
else:
    MEMBER_DB = os.path.join(_PATCH_DIR, 'member.db')
    _parent = os.path.dirname(_PATCH_DIR)
    for _d in sorted(os.listdir(_parent)):
        _p = os.path.join(_parent, _d, 'member.db')
        if os.path.exists(_p):
            MEMBER_DB = _p
            break

_db_lock = threading.Lock()

def _connect():
    conn = sqlite3.connect(MEMBER_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def _ensure_disabled_column():
    with _db_lock, _connect() as conn:
        cols = [r[1] for r in conn.execute('PRAGMA table_info(users)').fetchall()]
        if 'disabled' not in cols:
            conn.execute('ALTER TABLE users ADD COLUMN disabled INTEGER DEFAULT 0')
            conn.commit()
        conn.execute('''CREATE TABLE IF NOT EXISTS admin_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            admin TEXT NOT NULL,
            action TEXT NOT NULL,
            target TEXT,
            detail TEXT,
            ts REAL NOT NULL
        )''')
        conn.commit()

_ensure_disabled_column()

def _auth2():
    """優先取得 P0（會員認證）的 bcrypt 實作；沒有則回 None。"""
    a2 = getattr(main, 'mok_auth2', None)
    if a2 and callable(a2.get('hash_pw')):
        return a2
    return None

def _hash_pw(pw):
    """統一雜湊：優先 bcrypt（與 P0 一致），bcrypt 不可用才退回舊式。
    ★ 2026-10-01 凜：原本取「最先載入的會員系統._hash_pw」＝舊式，
      導致後台每次新增/重設密碼都寫回舊格式，是登入不一致的根因。"""
    a2 = _auth2()
    if a2 is not None:
        try:
            return a2['hash_pw'](pw)
        except Exception:
            pass
    try:
        import bcrypt as _b
        return _b.hashpw(pw.encode('utf-8'), _b.gensalt(rounds=12)).decode()
    except Exception:
        return hashlib.sha256(('mok_member_v1' + pw).encode()).hexdigest()

def _verify_pw(pw, stored):
    """驗證密碼（bcrypt 或舊式皆可），回傳 bool。"""
    a2 = _auth2()
    if a2 is not None and callable(a2.get('verify_pw')):
        try:
            ok, _up = a2['verify_pw'](pw, stored)
            return bool(ok)
        except Exception:
            pass
    if not stored:
        return False
    if stored.startswith('$2'):
        try:
            import bcrypt as _b
            return _b.checkpw(pw.encode('utf-8'), stored.encode())
        except Exception:
            return False
    return hmac.compare_digest(stored, hashlib.sha256(('mok_member_v1' + pw).encode()).hexdigest())

def _get_user(username):
    if _member_mod is not None and hasattr(_member_mod, '_get_user'):
        u = _member_mod._get_user(username)
        if u is not None and 'disabled' not in u:
            u['disabled'] = 0
        return u
    with _connect() as conn:
        row = conn.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        return dict(row) if row else None

def _list_users(q=''):
    with _connect() as conn:
        if q:
            rows = conn.execute('SELECT * FROM users WHERE username LIKE ? ORDER BY id DESC', (f'%{q}%',)).fetchall()
        else:
            rows = conn.execute('SELECT * FROM users ORDER BY id DESC').fetchall()
        return [dict(r) for r in rows]

def _list_plans():
    with _connect() as conn:
        rows = conn.execute('SELECT * FROM plans ORDER BY plan').fetchall()
        return [dict(r) for r in rows]

def _plan_monthly_tokens(plan):
    """★ indexPage 2026-09-30：取方案月額度（tokens）；查無回 0。"""
    for _p in _list_plans():
        if _p.get('plan') == plan:
            try:
                return int(_p.get('monthly_tokens') or 0)
            except Exception:
                return 0
    return 0

def _month_key():
    return time.strftime('%Y-%m')

def _log_action(admin, action, target, detail=''):
    try:
        with _db_lock, _connect() as conn:
            conn.execute('INSERT INTO admin_log (admin, action, target, detail, ts) VALUES (?,?,?,?,?)',
                         (admin, action, target, detail, time.time()))
            conn.commit()
    except Exception as e:
        print(f'[管理後台] 稽核寫入失敗: {e}')

def _recent_logs(n=12):
    with _connect() as conn:
        rows = conn.execute('SELECT * FROM admin_log ORDER BY id DESC LIMIT ?', (n,)).fetchall()
        return [dict(r) for r in rows]

def _esc(s):
    return str(s).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')

def _fmt_ts(ts):
    try:
        return time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))
    except Exception:
        return '-'

def _fmt_tokens(n):
    try:
        n = int(n)
        if abs(n) >= 100000000:
            return f'{n/100000000:.1f}億'
        if abs(n) >= 10000:
            return f'{n/10000:.1f}萬'
        return f'{n:,}'
    except Exception:
        return str(n)

ADMIN_USERS = set(x.strip() for x in os.environ.get('ADMIN_USERNAMES', 'admin').split(',') if x.strip())

def _is_admin():
    return session.get('member_user') in ADMIN_USERS

def _require_admin():
    if not session.get('member_user'):
        return redirect('/login')
    if not _is_admin():
        return _page('無權限', '<p style="color:#ffb3c1;font-size:15px">⛔ 您不是管理員，無法存取後台。</p><p><a href="/member">← 返回會員中心</a></p>')
    return None

def _csrf_token():
    t = session.get('admin_csrf')
    if not t:
        t = secrets.token_hex(16)
        session['admin_csrf'] = t
    return t

def _check_csrf():
    t = session.get('admin_csrf')
    f = request.form.get('csrf') or request.headers.get('X-CSRF-Token')
    return bool(t and f and t == f)

_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,'PingFang TC','Microsoft JhengHei',system-ui,sans-serif;background:radial-gradient(1200px 600px at 15% -10%,#1b2338,#0b0f1a 60%);color:#e8eefc;min-height:100vh;padding:24px}
.wrap{max-width:1320px;margin:0 auto}
h1{font-size:24px;font-weight:800;letter-spacing:.5px;margin-bottom:4px;background:linear-gradient(90deg,#7aa2ff,#c084fc);-webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.sub{color:#8ea0c0;font-size:13px}
.nav{display:flex;gap:6px;font-size:13px;margin-bottom:22px;align-items:center;flex-wrap:wrap;background:#141a29;border:1px solid #27324a;border-radius:14px;padding:10px 14px}
.nav a{color:#a9bcff;text-decoration:none;padding:4px 10px;border-radius:8px;transition:.15s}
.nav a:hover{background:#1b2438;color:#fff}
.nav .sp{flex:1}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin-bottom:22px}
.card{position:relative;overflow:hidden;background:linear-gradient(160deg,#1b2438,#141a29);border:1px solid #27324a;border-radius:16px;padding:16px 18px}
.card::before{content:'';position:absolute;left:0;top:0;bottom:0;width:3px;background:linear-gradient(180deg,#7c5cff,#ff5ca8);opacity:.85}
.card .v{font-size:24px;font-weight:800;color:#9db4ff;letter-spacing:.3px}
.card .l{font-size:12px;color:#8b93b5;margin-top:4px}
.card.green .v{color:#2fa36b}
.card.red .v{color:#ff8fa3}
.card.purple .v{color:#c9a4ff}
.panel{background:#141a29;border:1px solid #27324a;border-radius:16px;padding:18px;margin-bottom:20px;box-shadow:0 10px 30px rgba(0,0,0,.28)}
.panel h2{font-size:15px;margin-bottom:14px;color:#c9d1f0;display:flex;align-items:center;gap:8px}
table{width:100%;border-collapse:separate;border-spacing:0;font-size:13px;min-width:1040px}
.tablewrap{overflow-x:auto;padding-bottom:4px}
thead th{position:sticky;top:0;z-index:2;background:#141a29;color:#8ea0c0;text-align:left;padding:11px 12px;border-bottom:1px solid #27324a;font-weight:700;font-size:12px;letter-spacing:.4px;white-space:nowrap}
tbody td{padding:11px 12px;border-bottom:1px solid #1e2440;vertical-align:middle;white-space:nowrap}
tbody tr{transition:background .12s}
tbody tr:hover td{background:#19203a}
tbody tr:last-child td{border-bottom:none}
.badge{display:inline-block;padding:2px 9px;border-radius:20px;font-size:11px;font-weight:700}
.badge.free{background:#23304f;color:#9db4e8}
.badge.pro{background:#233a52;color:#6fc2ff}
.badge.vip{background:#3a2b52;color:#d0a4ff}
.badge.on{background:#15352a;color:#a7f3d0}
.badge.off{background:#3a1d2e;color:#ffb3c1}
.badge.admin{background:#4a2e1d;color:#ffd29e}
.ops{display:flex;flex-wrap:wrap;gap:6px;align-items:center;max-width:470px}
.ops form{display:flex;gap:5px;align-items:center}
select,input[type=text],input[type=password],input[type=number]{background:#12162a;border:1px solid #2c3560;color:#e8e8f0;border-radius:8px;padding:6px 8px;font-size:12px;outline:none}
select:focus,input:focus{border-color:#7aa2ff}
button{background:linear-gradient(90deg,#7c5cff,#c084fc);color:#fff;border:0;border-radius:9px;padding:7px 12px;font-size:12px;font-weight:700;cursor:pointer;transition:.15s;white-space:nowrap}
button:hover{opacity:.9;transform:translateY(-1px)}
button.danger{background:#3a1d2e;color:#ffb3c1;border:1px solid #d14d6a}
button.danger:hover{background:#4a2538}
button.gray{background:#232a45;color:#c9d1f0}
.err{background:#3a1d2e;border:1px solid #d14d6a;color:#ffb3c1;padding:10px 14px;border-radius:10px;font-size:13px;margin-bottom:16px}
.ok{background:#15352a;border:1px solid #2fa36b;color:#a7f3d0;padding:10px 14px;border-radius:10px;font-size:13px;margin-bottom:16px}
input.amt{width:84px}
input.pw{width:100px}
.search{display:flex;gap:8px;margin-bottom:14px;flex-wrap:wrap;align-items:center}
.search input{flex:1;min-width:180px;max-width:320px}
.empty{color:#5b6380;text-align:center;padding:30px;font-size:13px}
.logline{font-size:12px;color:#8b93b5;padding:5px 0;border-bottom:1px solid #1e2440;display:flex;gap:10px}
.logline b{color:#c9d1f0}
.logline .t{color:#5b6380;white-space:nowrap}
.mono{font-family:ui-monospace,Menlo,monospace;font-size:12px}
/* ===== 會員列表響應式（2026-10-01 by 春）===== */
table.rt tr.rowhead{background:transparent}
td.mops{white-space:normal}
@media (max-width: 1130px){
  body{padding:12px}
  h1{font-size:20px}
  .panel{padding:14px;border-radius:14px}
  .cards{grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:10px}
  .card{padding:12px 14px}
  .card .v{font-size:20px}
  .search input{flex:1 1 100%;max-width:none;min-width:0}
  .search{gap:8px}
  .nav{padding:8px 10px}

  /* 表格 → 卡片 */
  .tablewrap{overflow:visible;padding-bottom:0}
  table{min-width:0;border-spacing:0;font-size:14px}
  thead{display:none}
  tbody{display:block}
  tbody tr{display:block;background:linear-gradient(160deg,#1b2438,#141a29);
    border:1px solid #27324a;border-radius:14px;padding:12px 14px 14px;
    margin-bottom:12px;box-shadow:0 8px 20px rgba(0,0,0,.28)}
  tbody tr:hover td{background:transparent}
  tbody tr:last-child{margin-bottom:0}
  tbody td{display:flex;justify-content:space-between;align-items:center;gap:12px;
    padding:7px 0;border-bottom:1px dashed #232b45;white-space:normal;text-align:right}
  tbody td::before{content:attr(data-l);flex:0 0 auto;color:#8ea0c0;
    font-size:12px;font-weight:700;letter-spacing:.5px;text-align:left}
  tbody td:first-child{border-bottom:1px solid #27324a;padding-bottom:10px;margin-bottom:4px}
  tbody td:first-child b{font-size:16px}
  tbody td:last-child{border-bottom:none}
  tbody td.mops{display:block;padding:12px 0 0;border-bottom:none}
  tbody td.mops::before{content:none;display:none}

  /* 管理操作：兩欄網格，輸入框撐滿、按鈕好按 */
  .ops{display:grid;grid-template-columns:1fr 1fr;gap:8px;max-width:none;width:100%}
  .ops form{display:flex;gap:6px;width:100%;min-width:0}
  .ops form:first-child{grid-column:1 / -1}
  .ops form input{flex:1 1 auto;min-width:0;width:auto}
  .ops form button{flex:0 0 auto}
  .ops form select{flex:1 1 auto;min-width:0}
  select,input[type=text],input[type=password],input[type=number]{font-size:16px;padding:9px 10px}
  button{padding:9px 14px;font-size:13px}
  input.amt,input.pw{width:auto;flex:1 1 auto}
}
@media (max-width: 380px){
  .ops{grid-template-columns:1fr}
  .ops form:first-child{grid-column:auto}
}

"""

def _page(title, inner, msg='', mtype=''):
    m_html = ''
    if msg:
        cls = 'ok' if mtype == 'ok' else 'err'
        m_html = '<div class="%s">%s</div>' % (cls, _esc(msg))
    return ('<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>%s</title><style>%s</style></head><body><div class="wrap">%s%s</div></body></html>'
            % (title, _CSS, m_html, inner))

# ---- 工具鍵權限設定（右側面板按鍵 x 會員等級矩陣）by 凜 2026-10-01 ----
@app.route('/admin/tools')
@app.route('/admin/ui-tools')
def admin_tools_perm():
    r = _require_admin()
    if r is not None:
        return r
    return redirect('/static/tools_perm.html')

@app.route('/admin/member')
def admin_member():
    r = _require_admin()
    if r is not None:
        return r
    admin = session.get('member_user')
    q = (request.args.get('q') or '').strip()
    users = _list_users(q)
    plans = _list_plans()
    plan_opts = ''.join('<option value="%s">%s</option>' % (_esc(p['plan']), _esc(p['plan']))
                        for p in (plans or [{'plan': 'free'}, {'plan': 'pro'}, {'plan': 'vip'}]))
    total = len(users)
    active = sum(1 for u in users if not u.get('disabled'))
    disabled = total - active
    vip = sum(1 for u in users if u.get('plan') == 'vip')
    total_bal = sum(u.get('balance_tokens') or 0 for u in users)
    csrf = _csrf_token()
    hidden = '<input type="hidden" name="csrf" value="%s">' % csrf

    rows = []
    for u in users:
        uname = _esc(u['username'])
        plan = _esc(u.get('plan') or 'free')
        dis = 1 if u.get('disabled') else 0
        status_badge = ('<span class="badge off">已停用</span>' if dis else '<span class="badge on">啟用中</span>')
        if u['username'] in ADMIN_USERS:
            status_badge += ' <span class="badge admin">管理員</span>'
        plan_select = ('<select name="plan">' + ''.join(
            '<option value="%s"%s>%s</option>' % (_esc(p['plan']), ' selected' if p['plan'] == u.get('plan') else '', _esc(p['plan']))
            for p in (plans or [{'plan': 'free'}, {'plan': 'pro'}, {'plan': 'vip'}])) + '</select>')
        is_protected = (u['username'] in ADMIN_USERS)
        toggle_btn = ('<button class="gray" onclick="return confirm(%s)">啟用</button>' % repr('確定啟用 ' + u['username'] + ' ？')
                      if dis else '<button class="danger" onclick="return confirm(%s)">停用</button>' % repr('確定停用 ' + u['username'] + ' ？此會員將無法登入與使用 AI'))
        del_btn = ''
        if not is_protected and u['username'] != admin:
            del_btn = ('<button class="danger" onclick="return confirm(%s)">刪除</button>'
                       % repr('確定刪除會員 ' + u['username'] + ' ？此操作無法復原'))
        rows.append(f"""<tr>
<td data-l="帳號"><b>{uname}</b></td>
<td data-l="方案"><span class="badge {plan}">{plan.upper()}</span></td>
<td class="mono" data-l="餘額">{_fmt_tokens(u.get('balance_tokens'))}</td>
<td class="mono" data-l="本月用量">{_fmt_tokens(u.get('monthly_used'))}</td>
<td data-l="註冊時間">{_fmt_ts(u.get('created_at'))}</td>
<td data-l="最後登入">{_fmt_ts(u.get('last_login'))}</td>
<td data-l="狀態">{status_badge}</td>
<td class="mops"><div class="ops">
  <form method="post" action="/admin/member/{uname}/plan">{hidden}{plan_select}<button>改方案</button></form>
  <form method="post" action="/admin/member/{uname}/balance">{hidden}<input class="amt" type="text" name="amount" placeholder="+1000 / -500"><button>調整</button></form>
  <form method="post" action="/admin/member/{uname}/password">{hidden}<input class="pw" type="text" name="new_password" placeholder="新密碼"><button>重設</button></form>
  <form method="post" action="/admin/member/{uname}/toggle">{hidden}{toggle_btn}</form>
  {('<form method="post" action="/admin/member/' + uname + '/delete">' + hidden + del_btn + '</form>') if del_btn else ''}
</div></td></tr>""")

    table = ('<div class="tablewrap"><table class="rt"><thead><tr><th>帳號</th><th>方案</th><th>餘額</th><th>本月用量</th><th>註冊時間</th><th>最後登入</th><th>狀態</th><th>管理操作</th></tr></thead><tbody>'
             + ''.join(rows) + '</tbody></table></div>') if rows else '<div class="empty">找不到會員</div>'

    logs = _recent_logs()
    log_html = ''.join('<div class="logline"><span class="t">%s</span><b>%s</b> %s → %s <span>%s</span></div>'
                       % (_fmt_ts(l['ts']), _esc(l['admin']), _esc(l['action']), _esc(l['target'] or '-'), _esc(l['detail'] or ''))
                       for l in logs) or '<div class="empty">尚無稽核紀錄</div>'

    msg = request.args.get('msg', '')
    mtype = request.args.get('type', 'ok')

    inner = f"""
<h1>🛡️ Admin 會員管理後台</h1>
<div class="nav"><span class="sub">管理員：{_esc(admin)}＋凜</span><span class="sp"></span>
<a href="/admin/member">會員列表</a>｜<a href="/admin/plans">方案設定</a>｜<a href="/admin/tools">🧩 工具鍵權限</a>｜<a href="/member">會員中心</a>｜<a href="/">聊天首頁</a>｜<a href="/logout">登出</a></div>
<div class="cards">
  <div class="card"><div class="v">{total}</div><div class="l">會員總數</div></div>
  <div class="card green"><div class="v">{active}</div><div class="l">啟用中</div></div>
  <div class="card red"><div class="v">{disabled}</div><div class="l">已停用</div></div>
  <div class="card purple"><div class="v">{vip}</div><div class="l">VIP 人數</div></div>
  <div class="card"><div class="v">{_fmt_tokens(total_bal)}</div><div class="l">總餘額</div></div>
</div>
<div class="panel"><h2>＋ 新增會員</h2>
<form method="post" action="/admin/member/add" style="display:flex;gap:8px;flex-wrap:wrap;align-items:center">
{hidden}
<input type="text" name="username" placeholder="帳號（英數字）" required pattern="[A-Za-z0-9]{{2,20}}">
<input type="text" name="password" placeholder="密碼（至少6碼）" required>
<select name="plan">{plan_opts}</select>
<input class="amt" type="number" name="balance" value="" min="0" placeholder="留空＝方案預設額度">
<button>建立會員</button>
</form></div>
<div class="panel"><h2>會員列表{('（搜尋：「' + _esc(q) + '」）') if q else ''}</h2>
<form class="search" method="get" action="/admin/member"><input type="text" name="q" value="{_esc(q)}" placeholder="搜尋帳號..."><button>搜尋</button><a href="/admin/member" style="color:#7aa2ff;font-size:12px;align-self:center">清除</a></form>
{table}</div>
<div class="panel"><h2>📋 最近稽核紀錄</h2>{log_html}</div>
"""
    return _page('Admin 會員管理後台', inner, msg=msg, mtype=mtype)

@app.route('/admin/member/add', methods=['POST'])
def admin_member_add():
    r = _require_admin()
    if r is not None:
        return r
    if not _check_csrf():
        return _page('錯誤', '<p class="err">CSRF 驗證失敗，請返回後台重新整理後再試。</p><p><a href="/admin/member">← 返回後台</a></p>')
    admin = session.get('member_user')
    username = (request.form.get('username') or '').strip()
    pw = request.form.get('password') or ''
    plan = (request.form.get('plan') or 'free').strip()
    # ★ indexPage 2026-09-30：初始餘額留空＝帶入該方案預設月額度（避免新會員一律 0）
    _raw_bal = (request.form.get('balance') or '').strip()
    if _raw_bal == '':
        balance = _plan_monthly_tokens(plan)
    else:
        try:
            balance = max(0, int(_raw_bal))
        except ValueError:
            balance = _plan_monthly_tokens(plan)
    if str(plan).lower() in ('admin', 'root'):
        balance = 10 ** 12
    if len(username) < 2 or not username.isalnum():
        return redirect('/admin/member?type=err&msg=' + urllib_quote('帳號需為 2 個以上英數字'))
    if len(pw) < 6:
        return redirect('/admin/member?type=err&msg=' + urllib_quote('密碼至少 6 碼'))
    try:
        with _db_lock, _connect() as conn:
            conn.execute('INSERT INTO users (username, password_hash, plan, balance_tokens, monthly_used, month_key, created_at, disabled) VALUES (?,?,?,?,?,?,?,0)',
                         (username, _hash_pw(pw), plan, balance, 0, _month_key(), time.time()))
            conn.commit()
    except sqlite3.IntegrityError:
        return redirect('/admin/member?type=err&msg=' + urllib_quote('帳號已被使用'))
    _log_action(admin, '新增會員', username, f'plan={plan} balance={balance}')
    return redirect('/admin/member?type=ok&msg=' + urllib_quote(f'會員 {username} 已建立'))

@app.route('/admin/member/<username>/plan', methods=['POST'])
def admin_member_plan(username):
    r = _require_admin()
    if r is not None:
        return r
    if not _check_csrf():
        return redirect('/admin/member?type=err&msg=' + urllib_quote('CSRF 驗證失敗'))
    admin = session.get('member_user')
    new_plan = (request.form.get('plan') or '').strip()
    u = _get_user(username)
    if not u:
        return redirect('/admin/member?type=err&msg=' + urllib_quote('會員不存在'))
    with _db_lock, _connect() as conn:
        # ★ indexPage 2026-09-30：改方案同時對齊餘額／月用量（否則餘額永遠停在舊值 → 三級都顯示 50000）
        _mb = _plan_monthly_tokens(new_plan)
        if str(new_plan).lower() in ('admin', 'root'):
            _mb = 10 ** 12
        else:
            # ★ 凜 2026-09-30：舊餘額累加 —— 新餘額 = 現在餘額 + 新方案月額度
            #   例：FREE→PRO = FREE 現在餘額 + 500,000；PRO→VIP = PRO 現在餘額 + 2,000,000
            try:
                _mb += max(0, int(u.get('balance_tokens') or 0))
            except Exception:
                pass
        conn.execute('UPDATE users SET plan=?, balance_tokens=?, monthly_used=0, month_key=? WHERE username=?',
                     (new_plan, _mb, _month_key(), username))
        conn.commit()
    _log_action(admin, '改方案', username, f'{u.get("plan")} → {new_plan}')
    return redirect('/admin/member?type=ok&msg=' + urllib_quote(f'{username} 方案已改為 {new_plan}'))

@app.route('/admin/member/<username>/balance', methods=['POST'])
def admin_member_balance(username):
    r = _require_admin()
    if r is not None:
        return r
    if not _check_csrf():
        return redirect('/admin/member?type=err&msg=' + urllib_quote('CSRF 驗證失敗'))
    admin = session.get('member_user')
    try:
        delta = int(request.form.get('amount') or 0)
    except ValueError:
        return redirect('/admin/member?type=err&msg=' + urllib_quote('餘額請輸入整數（如 +1000 或 -500）'))
    if delta == 0:
        return redirect('/admin/member?type=err&msg=' + urllib_quote('調整值不可為 0'))
    u = _get_user(username)
    if not u:
        return redirect('/admin/member?type=err&msg=' + urllib_quote('會員不存在'))
    new_bal = max(0, (u.get('balance_tokens') or 0) + delta)
    with _db_lock, _connect() as conn:
        conn.execute('UPDATE users SET balance_tokens=? WHERE username=?', (new_bal, username))
        conn.commit()
    _log_action(admin, '調整餘額', username, f'{u.get("balance_tokens")} → {new_bal} ({delta:+d})')
    return redirect('/admin/member?type=ok&msg=' + urllib_quote(f'{username} 餘額調整 {delta:+d} → {new_bal:,}'))

@app.route('/admin/member/<username>/password', methods=['POST'])
def admin_member_password(username):
    r = _require_admin()
    if r is not None:
        return r
    if not _check_csrf():
        return redirect('/admin/member?type=err&msg=' + urllib_quote('CSRF 驗證失敗'))
    admin = session.get('member_user')
    new_pw = request.form.get('new_password') or ''
    if len(new_pw) < 6:
        return redirect('/admin/member?type=err&msg=' + urllib_quote('新密碼至少 6 碼'))
    u = _get_user(username)
    if not u:
        return redirect('/admin/member?type=err&msg=' + urllib_quote('會員不存在'))
    with _db_lock, _connect() as conn:
        conn.execute('UPDATE users SET password_hash=? WHERE username=?', (_hash_pw(new_pw), username))
        conn.commit()
    _log_action(admin, '重設密碼', username)
    return redirect('/admin/member?type=ok&msg=' + urllib_quote(f'{username} 密碼已重設'))

@app.route('/admin/member/<username>/toggle', methods=['POST'])
def admin_member_toggle(username):
    r = _require_admin()
    if r is not None:
        return r
    if not _check_csrf():
        return redirect('/admin/member?type=err&msg=' + urllib_quote('CSRF 驗證失敗'))
    admin = session.get('member_user')
    u = _get_user(username)
    if not u:
        return redirect('/admin/member?type=err&msg=' + urllib_quote('會員不存在'))
    if username in ADMIN_USERS:
        return redirect('/admin/member?type=err&msg=' + urllib_quote('管理員帳號不可停用'))
    new_state = 0 if u.get('disabled') else 1
    with _db_lock, _connect() as conn:
        conn.execute('UPDATE users SET disabled=? WHERE username=?', (new_state, username))
        conn.commit()
    _log_action(admin, '停用會員' if new_state else '啟用會員', username)
    return redirect('/admin/member?type=ok&msg=' + urllib_quote(f'{username} 已' + ('停用' if new_state else '啟用')))

@app.route('/admin/member/<username>/delete', methods=['POST'])
def admin_member_delete(username):
    r = _require_admin()
    if r is not None:
        return r
    if not _check_csrf():
        return redirect('/admin/member?type=err&msg=' + urllib_quote('CSRF 驗證失敗'))
    admin = session.get('member_user')
    if username in ADMIN_USERS:
        return redirect('/admin/member?type=err&msg=' + urllib_quote('管理員帳號不可刪除'))
    if username == admin:
        return redirect('/admin/member?type=err&msg=' + urllib_quote('不可刪除自己'))
    u = _get_user(username)
    if not u:
        return redirect('/admin/member?type=err&msg=' + urllib_quote('會員不存在'))
    with _db_lock, _connect() as conn:
        conn.execute('DELETE FROM users WHERE username=?', (username,))
        conn.commit()
    _log_action(admin, '刪除會員', username, f'plan={u.get("plan")} balance={u.get("balance_tokens")}')
    return redirect('/admin/member?type=ok&msg=' + urllib_quote(f'會員 {username} 已刪除'))

@app.route('/api/admin/members')
def api_admin_members():
    r = _require_admin()
    if r is not None:
        return jsonify({'success': False, 'error': 'not_authorized'}), 403
    users = _list_users()
    return jsonify({'success': True, 'members': users})

# ---------- 停用會員攔截 ----------
_orig_login_fn = app.view_functions.get('member_login') if app is not None else None
if _orig_login_fn is not None:
    def _admin_login_gate(*a, **kw):
        try:
            if request.method == 'POST':
                username = (request.form.get('username') or '').strip()
                pw = request.form.get('password') or ''
                u = _get_user(username)
                if u and u.get('disabled') and _verify_pw(pw, u.get('password_hash')):
                    session.pop('member_user', None)
                    return _page('帳號已停用', '<p style="color:#ffb3c1;font-size:15px">⛔ 此帳號已被停用，請聯絡管理員。</p><p><a href="/login">← 返回登入</a></p>')
        except Exception:
            pass
        return _orig_login_fn(*a, **kw)
    app.view_functions['member_login'] = _admin_login_gate
    print('[管理後台] 已攔截 /login：停用會員無法登入')

_prev_process_message = getattr(_mokagi, 'process_message', None)
if _prev_process_message is not None and asyncio.iscoroutinefunction(_prev_process_message):
    async def _admin_gated_process_message(user_id, text, stream_callback=None, agent_name=None, agent_config=None,
                                       auto_mode=False, initial_prompt=None, context_files=None,
                                       **kwargs):
        try:
            if session is not None:
                username = session.get('member_user')
                if username:
                    u = _get_user(username)
                    if u and u.get('disabled'):
                        raise PermissionError('⛔ 此帳號已被停用，請聯絡管理員。')
        except PermissionError:
            raise
        except Exception:
            pass
        return await _prev_process_message(
        user_id=user_id, text=text, stream_callback=stream_callback,
        agent_name=agent_name, agent_config=agent_config, auto_mode=auto_mode,
        initial_prompt=initial_prompt, context_files=context_files,
        **kwargs)
    _mokagi.process_message = _admin_gated_process_message
    print('[管理後台] 已攔截 process_message：停用會員無法使用 AI')

print(f'[管理後台] 補丁已載入 | DB={MEMBER_DB} | 管理員={sorted(ADMIN_USERS)}')
