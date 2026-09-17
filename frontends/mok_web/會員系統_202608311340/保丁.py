# -*- coding: utf-8 -*-
"""
會員系統補丁 v1.0
=================
【方案 B 實作】不動 mok_web.py / mokagi.py 核心，以保丁（補丁）方式掛載。

功能：
  1. 多用戶帳密登入（註冊 / 登入 / 登出），取代匿名 UUID 隔離
  2. 付費分級：free / pro / vip 方案，不同方案可用不同 agent
  3. token 用量統計 + 餘額不足自動停止

架構：
  - 資料庫：本目錄 member.db（users / plans / usage）
  - 攔截：patch mokagi.process_message → 檢查登入、權限、餘額，用完扣款
  - 頁面：/login /register /logout /member（render_template_string 內嵌，無需改 Flask 模板設定）

管理：
  - 預設管理員 admin / admin123（請登入後盡快在 member.db 改密碼）
  - 加方案：INSERT INTO plans ...
"""

import sys, os, sqlite3, time, json, hashlib, secrets, threading
from functools import wraps

main = sys.modules.get('__main__')          # mok_web 核心模組（被 exec 載入時 __main__ 即 mok_web）
import mokagi as _mokagi                     # 原始 mokagi 模組

# ---------- Flask 物件 ----------
try:
    from flask import request, session, redirect, url_for, jsonify, render_template_string, abort, flash
except Exception as _e:
    print(f"[會員系統] flask import 失敗: {_e}")
    request = session = redirect = url_for = jsonify = render_template_string = abort = flash = None

app = getattr(main, 'app', None)             # Flask app 實例

# ---------- 路徑與設定 ----------
_PATCH_DIR = os.path.dirname(os.path.abspath(__file__))
MEMBER_DB = os.path.join(_PATCH_DIR, 'member.db')

# 固定 session secret：避免每次重啟後 session 全失效
# 這不會影響多用戶 / 多 agent 並行；它只影響 Flask 註記與驗簽 session cookie。
# 若要更換，請在環境變數中設定：MEMBER_SECRET_KEY（或 MOK_MEMBER_SECRET_KEY）
if app is not None:
    try:
        secret = (
            os.environ.get('MEMBER_SECRET_KEY')
            or os.environ.get('MOK_MEMBER_SECRET_KEY')
            or 'mok_member_fixed_secret_20260917'
        )
        app.config['SECRET_KEY'] = secret
    except Exception:
        pass

# 方案預設（可於 member.db plans 表覆寫）
DEFAULT_PLANS = {
    'free': {'agents': ['凜', '客服', '稚', '春', '備', '卓', '現'], 'monthly_tokens': 50000, 'desc': '免費版：基礎 agent 可用'},
    'pro':  {'agents': ['*'], 'monthly_tokens': 500000, 'desc': '專業版：全部 agent 可用'},
    'vip':  {'agents': ['*'], 'monthly_tokens': 2000000, 'desc': '尊貴版：全部 agent + 高額度'},
}

# 訪客模式：False=未登入不能聊天（嚴格多用戶）；True=未登入可用預設額度
ALLOW_GUEST_CHAT = False
GUEST_DAILY_TOKENS = 10000

# ---------- 資料庫 ----------
_db_lock = threading.Lock()

def _connect():
    conn = sqlite3.connect(MEMBER_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn

def _init_db():
    with _db_lock, _connect() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            plan TEXT DEFAULT 'free',
            balance_tokens INTEGER DEFAULT 0,
            monthly_used INTEGER DEFAULT 0,
            month_key TEXT DEFAULT '',
            created_at REAL NOT NULL,
            last_login REAL
        );
        CREATE TABLE IF NOT EXISTS plans (
            plan TEXT PRIMARY KEY,
            agents TEXT NOT NULL DEFAULT '[]',
            monthly_tokens INTEGER DEFAULT 0,
            desc TEXT DEFAULT ''
        );
        CREATE TABLE IF NOT EXISTS usage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            agent TEXT,
            tokens INTEGER DEFAULT 0,
            ts REAL NOT NULL
        );
        ''')
        # 種子方案
        for p, cfg in DEFAULT_PLANS.items():
            conn.execute('INSERT OR IGNORE INTO plans (plan, agents, monthly_tokens, desc) VALUES (?,?,?,?)',
                         (p, json.dumps(cfg['agents']), cfg['monthly_tokens'], cfg['desc']))
        # 種子管理員 admin/admin123
        cur = conn.execute('SELECT id FROM users WHERE username=?', ('admin',))
        if cur.fetchone() is None:
            conn.execute('INSERT INTO users (username, password_hash, plan, balance_tokens, monthly_used, month_key, created_at) VALUES (?,?,?,?,?,?,?)',
                         ('admin', _hash_pw('admin123'), 'vip', 10**12, 0, _month_key(), time.time()))
        conn.commit()

def _month_key():
    return time.strftime('%Y-%m')

def _hash_pw(pw):
    salt = 'mok_member_v1'
    return hashlib.sha256((salt + pw).encode()).hexdigest()

def _get_user(username):
    with _connect() as conn:
        row = conn.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        return dict(row) if row else None

def _get_plan(plan):
    with _connect() as conn:
        row = conn.execute('SELECT * FROM plans WHERE plan=?', (plan,)).fetchone()
        return dict(row) if row else None

def _ensure_month(user):
    """跨月重置 monthly_used"""
    if user.get('month_key') != _month_key():
        with _db_lock, _connect() as conn:
            conn.execute('UPDATE users SET month_key=?, monthly_used=0 WHERE username=?', (_month_key(), user['username']))
        user['month_key'] = _month_key()
        user['monthly_used'] = 0
    return user

def _plan_agents(plan):
    p = _get_plan(plan)
    if not p:
        return []
    try:
        return json.loads(p['agents'])
    except Exception:
        return []

def _agent_allowed(username, agent_name):
    """檢查用戶方案是否允許使用該 agent"""
    user = _get_user(username)
    if not user:
        return False, '用戶不存在'
    user = _ensure_month(user)
    agents = _plan_agents(user['plan'])
    if '*' in agents or agent_name in agents:
        return True, ''
    return False, f"你的方案（{user['plan']}）不允許使用 agent「{agent_name}」。請升級方案。"

def _check_balance(username, need_tokens=1):
    """檢查餘額是否足夠"""
    user = _get_user(username)
    if not user:
        return False, '用戶不存在'
    user = _ensure_month(user)
    if user['balance_tokens'] <= 0:
        return False, f"餘額不足（剩 {user['balance_tokens']} tokens）。請充值或升級方案。"
    return True, ''

def _deduct_tokens(username, tokens):
    """扣除用量（實際 token 用量）"""
    if tokens <= 0:
        return
    with _db_lock, _connect() as conn:
        conn.execute('UPDATE users SET balance_tokens = balance_tokens - ?, monthly_used = monthly_used + ? WHERE username=?',
                     (tokens, tokens, username))
        conn.execute('INSERT INTO usage (username, agent, tokens, ts) VALUES (?,?,?,?)',
                     (username, None, tokens, time.time()))
        conn.commit()

def _sum_tokens(username, after_ts=None):
    """統計 token_usage 表中該用戶的總用量"""
    try:
        import mokagi as _m
        db_path = getattr(_m, 'TOKEN_DB_PATH', None)
        if not db_path or not os.path.exists(db_path):
            return 0
        with sqlite3.connect(db_path) as conn:
            if after_ts:
                row = conn.execute('SELECT COALESCE(SUM(total_tokens),0) FROM token_usage WHERE user_id=? AND timestamp>?', (username, after_ts)).fetchone()
            else:
                row = conn.execute('SELECT COALESCE(SUM(total_tokens),0) FROM token_usage WHERE user_id=?', (username,)).fetchone()
            return row[0] if row else 0
    except Exception as _e:
        print(f"[會員系統] sum_tokens 失敗: {_e}")
        return 0

# ---------- 攔截 process_message ----------
_orig_process_message = _mokagi.process_message

async def _patched_process_message(user_id, text, stream_callback=None, agent_name=None, agent_config=None,
                                   auto_mode=False, initial_prompt=None, context_files=None):
    """包裝 process_message：登入檢查 → 權限檢查 → 餘額檢查 → 呼叫 → 結算"""
    # 判斷是否網頁 API 請求（有 Flask request context 且路徑是 /api）
    is_web = False
    try:
        if request is not None and request.path.startswith('/api'):
            is_web = True
    except Exception:
        is_web = False

    username = None
    try:
        if session is not None:
            username = session.get('member_user')
    except Exception:
        username = None

    if is_web:
        # ---- 網頁請求：會員管控 ----
        if not username:
            if ALLOW_GUEST_CHAT:
                username = 'guest'
            else:
                raise PermissionError('⛔ 請先登入會員系統（/login）才能使用 AI 服務。')
        # 權限
        ok, err = _agent_allowed(username, agent_name or '凜')
        if not ok:
            raise PermissionError('⛔ ' + err)
        # 餘額
        ok, err = _check_balance(username)
        if not ok:
            raise PermissionError('⛔ ' + err)
        # 強制 user_id = 會員帳號，讓 token 統計歸戶
        user_id = username

    # 記錄調用前 token 總量（供差額結算）
    before = _sum_tokens(username if is_web and username else None)

    try:
        result = await _orig_process_message(
            user_id=user_id, text=text, stream_callback=stream_callback,
            agent_name=agent_name, agent_config=agent_config,
            auto_mode=auto_mode, initial_prompt=initial_prompt, context_files=context_files)
    finally:
        if is_web and username:
            after = _sum_tokens(username)
            used = after - before
            if used > 0:
                _deduct_tokens(username, used)
    return result

_mokagi.process_message = _patched_process_message
# 同步覆蓋 mok_web 命名空間的綁定（112 行 from mokagi import process_message 之後若被引用）
if hasattr(main, 'process_message'):
    main.process_message = _patched_process_message

# ---------- Flask 路由 ----------
if app is not None and request is not None:
    _PAGE_CSS = """
    <style>
      body{font-family:-apple-system,'PingFang TC','Microsoft JhengHei',sans-serif;background:#0f1220;color:#e8e8f0;margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center}
      .card{background:#1a1f33;border:1px solid #2c3560;border-radius:16px;padding:36px 40px;width:380px;box-shadow:0 10px 40px rgba(0,0,0,.5)}
      h1{font-size:22px;margin:0 0 6px;background:linear-gradient(90deg,#7aa2ff,#c084fc);-webkit-background-clip:text;-webkit-text-fill-color:transparent}
      p.sub{color:#8b93b5;font-size:13px;margin:0 0 22px}
      label{display:block;font-size:13px;color:#aab2d6;margin:14px 0 6px}
      input{width:100%;box-sizing:border-box;padding:11px 14px;border-radius:10px;border:1px solid #2c3560;background:#12162a;color:#e8e8f0;font-size:14px;outline:none}
      input:focus{border-color:#7aa2ff}
      button{width:100%;margin-top:20px;padding:12px;border:0;border-radius:10px;background:linear-gradient(90deg,#7aa2ff,#c084fc);color:#0f1220;font-size:15px;font-weight:700;cursor:pointer}
      button:hover{opacity:.9}
      a{color:#7aa2ff;text-decoration:none;font-size:13px}
      .err{background:#3a1d2e;border:1px solid #d14d6a;color:#ffb3c1;padding:10px 12px;border-radius:10px;font-size:13px;margin-top:14px}
      .ok{background:#15352a;border:1px solid #2fa36b;color:#a7f3d0;padding:10px 12px;border-radius:10px;font-size:13px;margin-top:14px}
      .meta{display:flex;justify-content:space-between;font-size:12px;color:#8b93b5;margin-top:16px}
    </style>"""

    def _page(title, inner, err=None, ok=None):
        err_html = f'<div class="err">{err}</div>' if err else ''
        ok_html = f'<div class="ok">{ok}</div>' if ok else ''
        return render_template_string(f"""<!doctype html><html><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{{{{ title }}}}</title>{_PAGE_CSS}</head>
        <body><div class="card"><h1>{{{{ title }}}}</h1>{{{{ inner|safe }}}}{err_html}{ok_html}</div></body></html>""",
                                      title=title, inner=inner, err=err, ok=ok)

    @app.route('/login', methods=['GET', 'POST'])
    def member_login():
        err = None
        if request.method == 'POST':
            username = (request.form.get('username') or '').strip()
            pw = request.form.get('password') or ''
            user = _get_user(username)
            if user and user['password_hash'] == _hash_pw(pw):
                session['member_user'] = username
                session.permanent = True
                with _db_lock, _connect() as conn:
                    conn.execute('UPDATE users SET last_login=? WHERE username=?', (time.time(), username))
                    conn.commit()
                return redirect('/member')
            err = '帳號或密碼錯誤'
        inner = f"""<p class="sub">登入後即可使用 AI 服務</p>
        <form method="post">
          <label>帳號</label><input name="username" required autocomplete="username">
          <label>密碼</label><input name="password" type="password" required autocomplete="current-password">
          <button type="submit">登入</button>
        </form>
        <div class="meta"><span>還沒有帳號？</span><a href="/register">立即註冊</a></div>"""
        return _page('會員登入', inner, err=err)

    @app.route('/register', methods=['GET', 'POST'])
    def member_register():
        err = ok = None
        if request.method == 'POST':
            username = (request.form.get('username') or '').strip()
            pw = request.form.get('password') or ''
            pw2 = request.form.get('password2') or ''
            if len(username) < 2 or not username.isalnum():
                err = '帳號需為 2 個以上英數字'
            elif len(pw) < 6:
                err = '密碼至少 6 碼'
            elif pw != pw2:
                err = '兩次密碼不一致'
            else:
                with _db_lock, _connect() as conn:
                    try:
                        conn.execute('INSERT INTO users (username, password_hash, plan, balance_tokens, monthly_used, month_key, created_at) VALUES (?,?,?,?,?,?,?)',
                                     (username, _hash_pw(pw), 'free', DEFAULT_PLANS['free']['monthly_tokens'], 0, _month_key(), time.time()))
                        conn.commit()
                        ok = '註冊成功！請登入。'
                    except sqlite3.IntegrityError:
                        err = '帳號已被使用'
        inner = f"""<p class="sub">建立免費帳號，立即開始</p>
        <form method="post">
          <label>帳號（英數字）</label><input name="username" required autocomplete="username">
          <label>密碼（至少 6 碼）</label><input name="password" type="password" required>
          <label>確認密碼</label><input name="password2" type="password" required>
          <button type="submit">註冊</button>
        </form>
        <div class="meta"><span>已有帳號？</span><a href="/login">回去登入</a></div>"""
        return _page('註冊會員', inner, err=err, ok=ok)

    @app.route('/logout')
    def member_logout():
        session.pop('member_user', None)
        return redirect('/login')

    @app.route('/member')
    def member_center():
        username = session.get('member_user')
        if not username:
            return redirect('/login')
        user = _get_user(username)
        user = _ensure_month(user)
        plan = _get_plan(user['plan'])
        plan_agents = _plan_agents(user['plan'])
        agents_str = '全部 agent' if '*' in plan_agents else ('、'.join(plan_agents) or '無')
        inner = f"""<p class="sub">歡迎回來，{username}</p>
        <div style="font-size:14px;line-height:2">
          <div>方案：<b style="color:#c084fc">{user['plan'].upper()}</b>　{plan['desc'] if plan else ''}</div>
          <div>Token 餘額：<b style="color:#7aa2ff">{user['balance_tokens']:,}</b></div>
          <div>本月已用：{user['monthly_used']:,} / {plan['monthly_tokens'] if plan else 0:,}</div>
          <div>可用 agent：{agents_str}</div>
        </div>
        <div class="meta"><span><a href="/">← 返回聊天</a></span><a href="/logout">登出</a></div>"""
        return _page('會員中心', inner)

    @app.route('/api/member/me')
    def api_member_me():
        username = session.get('member_user')
        if not username:
            return jsonify({'logged_in': False})
        user = _get_user(username)
        user = _ensure_month(user)
        return jsonify({'logged_in': True, 'username': username, 'plan': user['plan'],
                        'balance_tokens': user['balance_tokens'], 'monthly_used': user['monthly_used']})

    print(f"[會員系統] 補丁已載入 | DB={MEMBER_DB} | 路由: /login /register /logout /member /api/member/me")

# ---------- 初始化 ----------
_init_db()
