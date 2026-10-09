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

import sys, os, sqlite3, time, json, hashlib, secrets, threading, hmac
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

# ---------- 統一計費：HK$ 顯示（唯一價格源：core/mok_price.py） ----------
#   ⚠️ 收費標準只在 core/mok_price.py 定義，本檔不硬編價；讀不到才退回內建預設（HK$68 / 百萬 token）。
try:
    from mok_price import price_per_token as _mok_price_per_token
    PRICE_PER_TOKEN_HKD = _mok_price_per_token()
except Exception:
    try:
        from mok_token import price_per_token as _mok_price_per_token
        PRICE_PER_TOKEN_HKD = _mok_price_per_token()
    except Exception:
        PRICE_PER_TOKEN_HKD = 0.000068


def _hkd(tokens):
    """token 數 -> HK$ 顯示字串（算法同「MOKAGI 用量帳單」頁；負數照顯示不夾成 0）

    例：6,655,772 -> 'HK$452.59'；-6,155,772 -> '-HK$418.59'
    """
    try:
        v = float(tokens or 0) * PRICE_PER_TOKEN_HKD
    except Exception:
        return u'HK$0.00'
    sign = u'-' if v < 0 else u''
    a = abs(v)
    if a < 0.01:
        return sign + u'HK$' + format(a, u'.4f')
    if a < 1:
        return sign + u'HK$' + format(a, u'.3f')
    return sign + u'HK$' + format(a, u',.2f')


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
        # ★ 2026-10-07 凜：自動升降級用的持久旗標（vip_eligible）
        #   只在「新建立此欄」當下由帳本回填一次，之後永不回填 ——
        #   否則被降級(餘額<=0)的用戶會因舊充值紀錄再被標記，下月回補額度時誤彈回 vip。
        try:
            _cols = [r[1] for r in conn.execute('PRAGMA table_info(users)').fetchall()]
            if 'vip_eligible' not in _cols:
                conn.execute('ALTER TABLE users ADD COLUMN vip_eligible INTEGER DEFAULT 0')
                print('[會員系統] users.vip_eligible 已建立')
                _tbls = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                if 'ledger' in _tbls:
                    # 回填兩類：① 帳本有成功付款者 ② 現時已是 vip 者（祖父條款，
                    # 免得上線瞬間把「管理員手動設定的 vip 既有帳號」掃成 pro）。
                    conn.execute("UPDATE users SET vip_eligible=1 WHERE plan='vip' OR EXISTS ("
                                 "SELECT 1 FROM ledger l WHERE l.username=users.username "
                                 "AND l.delta_tokens>0 AND l.reason LIKE 'recharge%')")
                    print('[會員系統] vip_eligible 回填完成（一次性）')
        except Exception as _e:
            print('[會員系統] vip_eligible 遷移失敗: %s' % _e)
        # 2026-10-07 凜：祖父白名單（member_vip_grandfather）
        #   名單內帳號一律維持 vip（plan 屬於 {pro,vip}），不受「未付費」「餘額<=0」影響；
        #   用途＝保護上線前既有／管理員手動授予的 vip，不被自動降級掃走。
        try:
            _gtbls = [r[0] for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            # 只在「首次建立此表」當下回填一次；之後永不自動回填，
            # 否則付費戶餘額一旦歸零會被回填成祖父、永遠降不下來。
            if 'member_vip_grandfather' not in _gtbls:
                conn.execute('CREATE TABLE member_vip_grandfather ('
                             "username TEXT PRIMARY KEY, reason TEXT DEFAULT '', "
                             "added_at REAL, added_by TEXT DEFAULT '凜')")
                if 'ledger' in _gtbls:
                    conn.execute(
                        "INSERT OR IGNORE INTO member_vip_grandfather (username, reason, added_at, added_by) "
                        "SELECT username, '上線前既有vip且無付費帳本紀錄', ?, '凜' FROM users "
                        "WHERE plan='vip' AND NOT EXISTS (SELECT 1 FROM ledger l WHERE l.username=users.username "
                        "AND l.delta_tokens>0 AND l.reason LIKE 'recharge%')", (time.time(),))
        except Exception as _e:
            print('[會員系統] 祖父白名單初始化失敗: %s' % _e)
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

def _auth2():
    """優先取得 P0（會員認證）的 bcrypt 實作；沒有則回 None。"""
    a2 = getattr(main, 'mok_auth2', None)
    if a2 and callable(a2.get('hash_pw')):
        return a2
    return None


def _bcrypt_hash(pw):
    try:
        import bcrypt as _b
        return _b.hashpw(pw.encode('utf-8'), _b.gensalt(rounds=12)).decode()
    except Exception:
        return None


def _hash_pw(pw):
    """統一雜湊：優先 bcrypt（與 P0 一致），bcrypt 不可用才退回舊式 sha256。"""
    a2 = _auth2()
    if a2 is not None:
        try:
            return a2['hash_pw'](pw)
        except Exception:
            pass
    h = _bcrypt_hash(pw)
    if h:
        return h
    return hashlib.sha256(('mok_member_v1' + pw).encode()).hexdigest()


def _verify_pw(pw, stored):
    """驗證密碼，回傳 (是否正確, 是否為舊格式需升級為 bcrypt)。"""
    a2 = _auth2()
    if a2 is not None and callable(a2.get('verify_pw')):
        try:
            ok, up = a2['verify_pw'](pw, stored)
            return bool(ok), bool(up)
        except Exception:
            pass
    if not stored:
        return False, False
    if stored.startswith('$2'):
        try:
            import bcrypt as _b
            return _b.checkpw(pw.encode('utf-8'), stored.encode()), False
        except Exception:
            return False, False
    if hmac.compare_digest(stored, hashlib.sha256(('mok_member_v1' + pw).encode()).hexdigest()):
        return True, True
    return False, False


def _audit(username, action, detail=''):
    """寫入 auth_audit；原版登入原本完全不記，失敗無痕。"""
    a2 = _auth2()
    if a2 is not None and callable(a2.get('audit')):
        try:
            a2['audit'](username, action, detail)
            return
        except Exception:
            pass
    try:
        with _connect() as conn:
            conn.execute("""CREATE TABLE IF NOT EXISTS auth_audit (
                id INTEGER PRIMARY KEY AUTOINCREMENT, ts REAL, username TEXT,
                action TEXT, detail TEXT, ip TEXT)""")
            conn.execute('INSERT INTO auth_audit (ts, username, action, detail) VALUES (?,?,?,?)',
                         (time.time(), username, action, str(detail)[:500]))
            conn.commit()
    except Exception:
        pass

def _get_user(username):
    with _connect() as conn:
        row = conn.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
        return dict(row) if row else None

def _get_plan(plan):
    with _connect() as conn:
        row = conn.execute('SELECT * FROM plans WHERE plan=?', (plan,)).fetchone()
        return dict(row) if row else None

def _ensure_month(user):
    """跨月重置：monthly_used 歸零，並把 balance_tokens 重設為本月方案額度。
    ★ indexPage 2026-09-30：原本只重置 monthly_used、balance_tokens 只減不增，
      使「每月 Token 額度」實際上只發一次，用戶額度會逐月耗盡。"""
    if user.get('month_key') != _month_key():
        new_bal = user.get('balance_tokens')
        try:
            if str(user.get('plan')).lower() not in ('admin', 'root'):
                q = int((_get_plan(user.get('plan')) or {}).get('monthly_tokens') or 0)
                if q > 0:
                    new_bal = q
        except Exception:
            new_bal = user.get('balance_tokens')
        with _db_lock, _connect() as conn:
            conn.execute('UPDATE users SET month_key=?, monthly_used=0, balance_tokens=? WHERE username=?',
                         (_month_key(), new_bal, user['username']))
        user['month_key'] = _month_key()
        user['monthly_used'] = 0
        user['balance_tokens'] = new_bal
    return user

def _plan_agents(plan):
    p = _get_plan(plan)
    if not p:
        return []
    try:
        return json.loads(p['agents'])
    except Exception:
        return []

def _own_agents(username):
    """凜 2026-09-30：會員「自己創建」的 agent（agent_owners），不受方案白名單限制。"""
    if not username:
        return set()
    try:
        with _db_lock, _connect() as conn:
            rows = conn.execute('SELECT agent FROM agent_owners WHERE owner=?', (str(username),)).fetchall()
        return set(r['agent'] for r in rows if r['agent'])
    except Exception:
        return set()

def _agent_allowed(username, agent_name):
    """檢查用戶方案是否允許使用該 agent（會員自創的 agent 一律放行）"""
    user = _get_user(username)
    if not user:
        return False, '用戶不存在'
    user = _ensure_month(user)
    if agent_name and agent_name in _own_agents(username):
        return True, ''
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
    # ★ 2026-10-07 凜：扣到餘額 <= 0 → 即刻降回 pro（旗標歸零，下月免費額度不會再彈回 vip）
    sync_plan(username)

# ---------- 自動升降級（唯一真相，2026-10-07 凜） ----------
#   規則（主人核定）：
#     vip = 曾成功付款(vip_eligible=1) 且 餘額 > 0
#     餘額 <= 0 → vip_eligible=0，並把 plan 降回 pro
#     free / admin / root 一律不動；_ensure_month 的「每月回補方案額度」是正確機制，不改。
#   為什麼要用持久旗標：_ensure_month 每月會把 balance_tokens 重設為方案額度，
#   若只靠「餘額>0 且有充值紀錄」，降回 pro 的用戶下月一被回補就會誤彈回 vip，形成迴圈。
_PAID_PLANS = ('pro', 'vip')
# 祖父白名單：名單內帳號一律 vip（不受餘額／付費紀錄影響）。
_GF_EXPR = "EXISTS (SELECT 1 FROM member_vip_grandfather g WHERE g.username=users.username)"
_PLAN_EXPR = ("CASE WHEN " + _GF_EXPR + " THEN 'vip' "
              "WHEN vip_eligible=1 AND balance_tokens>0 THEN 'vip' ELSE 'pro' END")


def mark_paid(username):
    """入帳成功 → 標記為付費戶（冪等，只設旗標，等級交由 sync_plan 算）。"""
    if not username:
        return
    try:
        with _db_lock, _connect() as conn:
            conn.execute('UPDATE users SET vip_eligible=1 WHERE username=?', (str(username),))
            conn.commit()
    except Exception as _e:
        print('[會員系統] mark_paid 失敗: %s' % _e)


def sync_plan(username=None, paid=False):
    """把 plan 校正到與「旗標 + 餘額」一致；回傳實際被改的筆數。
    paid=True → 先把該用戶標記為付費戶（金流入帳路徑用）。
    username=None → 掃全表（cron / 定時兜底用）。
    只作用於 plan ∈ {pro, vip}；free / admin / root 不碰。"""
    changed = 0
    try:
        with _db_lock, _connect() as conn:
            if paid and username:
                conn.execute('UPDATE users SET vip_eligible=1 WHERE username=?', (str(username),))
            else:
                _sql = ('UPDATE users SET vip_eligible=0 WHERE balance_tokens<=0 AND plan IN (?,?) '
                        'AND username NOT IN (SELECT username FROM member_vip_grandfather)')
                _p = list(_PAID_PLANS)
                if username:
                    _sql += ' AND username=?'
                    _p.append(str(username))
                conn.execute(_sql, _p)
            _sql = ('UPDATE users SET plan = ' + _PLAN_EXPR +
                    ' WHERE plan IN (?,?) AND plan <> ' + _PLAN_EXPR)
            _p = list(_PAID_PLANS)
            if username:
                _sql += ' AND username=?'
                _p.append(str(username))
            changed = conn.execute(_sql, _p).rowcount or 0
            conn.commit()
    except Exception as _e:
        print('[會員系統] sync_plan 失敗: %s' % _e)
        return 0
    if changed:
        print('[會員系統] 自動升降級：更新 %d 筆%s' % (changed, ('（%s）' % username) if username else ''))
    return changed


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
                                   auto_mode=False, initial_prompt=None, context_files=None,
                                   **kwargs):
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
        auto_mode=auto_mode, initial_prompt=initial_prompt, context_files=context_files,
        **kwargs)
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
            ok, need_up = (False, False)
            if user:
                ok, need_up = _verify_pw(pw, user.get('password_hash'))
            if ok and user.get('disabled'):
                session.pop('member_user', None)
                _audit(username, 'login_blocked', 'disabled')
                err = '此帳號已被停用，請聯絡管理員'
            elif ok:
                if need_up:
                    try:
                        new_hash = _hash_pw(pw)
                        with _db_lock, _connect() as conn:
                            conn.execute('UPDATE users SET password_hash=? WHERE username=?', (new_hash, username))
                            conn.commit()
                        _audit(username, 'pw_upgrade', 'legacy sha256 -> bcrypt')
                    except Exception:
                        pass
                session['member_user'] = username
                session.permanent = True
                with _db_lock, _connect() as conn:
                    conn.execute('UPDATE users SET last_login=? WHERE username=?', (time.time(), username))
                    conn.commit()
                _audit(username, 'login_ok', 'legacy_login')
                return redirect('/member')
            else:
                err = '帳號或密碼錯誤'
                _audit(username, 'login_fail', 'bad password' if user else 'no such user')
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
            # 2026-09-27：系統/管理員保留字一律禁止註冊
            if username.lower() in ('admin', 'root', 'system', 'administrator',
                                    'mokagi', 'mok', 'sys', 'operator'):
                err = '此帳號為系統保留，請換一個'
            elif len(username) < 2 or not username.isalnum():
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

    @app.route('/logout', methods=['GET', 'POST'])
    def member_logout():
        session.pop('member_user', None)
        return member_login()

    _MEMBER_CSS = """
    <style>
      :root{--bg:#0f1220;--card:#181d31;--line:#2b3357;--txt:#e9ebf5;--dim:#8b93b5;--acc1:#7aa2ff;--acc2:#c084fc;--warn:#ff6b81;--ok:#34d399}
      *{box-sizing:border-box}
      body{margin:0;background:radial-gradient(1100px 560px at 50% -12%,#1c2450 0%,#0f1220 62%);color:var(--txt);font-family:-apple-system,'PingFang TC','Microsoft JhengHei',sans-serif;min-height:100vh}
      .mc{max-width:100%;padding:18px 18px 28px;display:flex;flex-direction:column;gap:14px}
      .mc-hero{position:relative;overflow:hidden;border-radius:18px;padding:18px;background:linear-gradient(135deg,#2a2f6e 0%,#3a2a63 52%,#4a2a5e 100%);border:1px solid #3b4377;box-shadow:0 10px 30px rgba(0,0,0,.35);display:flex;align-items:center;gap:14px}
      .mc-hero:before{content:"";position:absolute;top:-60px;right:-40px;width:220px;height:220px;background:radial-gradient(circle,rgba(192,132,252,.5),transparent 70%);pointer-events:none}
      .mc-avatar{position:relative;z-index:1;flex:0 0 auto;width:52px;height:52px;border-radius:50%;background:linear-gradient(135deg,var(--acc1),var(--acc2));display:flex;align-items:center;justify-content:center;font-size:22px;font-weight:800;color:#0f1220;box-shadow:0 6px 16px rgba(122,162,255,.35)}
      .mc-who{position:relative;z-index:1;flex:1;min-width:0}
      .mc-welcome{font-size:12px;color:#cfd4ff;opacity:.85;letter-spacing:.6px}
      .mc-name{font-size:20px;font-weight:800;margin-top:2px;color:#eaf0ff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
      .mc-planbadge{position:relative;z-index:1;font-size:12px;font-weight:800;padding:6px 12px;border-radius:999px;background:rgba(255,255,255,.13);border:1px solid rgba(255,255,255,.26);color:#fff;white-space:nowrap}
      .mc-desc{margin:0;font-size:12.5px;line-height:1.7;color:var(--dim);background:rgba(255,255,255,.03);border:1px solid var(--line);border-radius:12px;padding:10px 12px}
      .mc-bal{border:1px solid var(--line);border-radius:16px;padding:16px;background:linear-gradient(180deg,#1a2038,#151a2c)}
      .mc-bal-label{font-size:11.5px;color:var(--dim);letter-spacing:1.5px;text-transform:uppercase}
      .mc-bal-num{font-size:30px;font-weight:900;margin:5px 0 14px;color:#8fd0ff;font-variant-numeric:tabular-nums}
      .mc-bal-num.neg{color:var(--warn)}
      .mc-bal-hkd{font-size:15px;font-weight:800;margin:-11px 0 14px;color:#8fd0ff;opacity:.92;font-variant-numeric:tabular-nums}
      .mc-bal-hkd.neg{color:var(--warn);opacity:1}
      .mc-hkd{font-size:11.5px;font-weight:700;color:#8fd0ff;opacity:.85;margin-left:5px;font-variant-numeric:tabular-nums}
      .mc-hkd.q{color:var(--dim);opacity:.6}
      .mc-hkd.neg{color:var(--warn);opacity:1}
      .mc-usage-top{display:flex;justify-content:space-between;gap:8px;font-size:12px;color:var(--dim);margin-bottom:7px}
      .mc-usage-top b{color:#c7cdf0;font-variant-numeric:tabular-nums}
      .mc-bar{height:10px;border-radius:999px;background:#0d1020;border:1px solid var(--line);overflow:hidden}
      .mc-bar>i{display:block;height:100%;border-radius:999px;background:linear-gradient(90deg,var(--acc1),var(--acc2))}
      .mc-bar>i.over{background:linear-gradient(90deg,#ff8a5c,#ff4d6d)}
      .mc-note{margin-top:9px;font-size:11.5px;line-height:1.5;color:#ffb3c1}
      .mc-recharge{display:flex;align-items:center;justify-content:center;gap:8px;text-decoration:none;padding:14px;border-radius:14px;font-size:15px;font-weight:800;color:#241600;background:linear-gradient(90deg,#ffd166,#ff9f1c);box-shadow:0 8px 22px rgba(255,159,28,.28);transition:transform .12s,filter .12s}
      .mc-recharge:hover{transform:translateY(-1px);filter:brightness(1.05)}
      .mc-sec-title{font-size:13px;font-weight:700;color:#cfd4ff;margin-bottom:10px;display:flex;align-items:center;gap:8px}
      .mc-count{color:var(--dim);font-weight:500;font-size:12px}
      .mc-chips{display:flex;flex-wrap:wrap;gap:7px}
      .mc-chip{font-size:12px;padding:5px 11px;border-radius:999px;background:#20263f;border:1px solid var(--line);color:#c7cdf0;white-space:nowrap}
      .mc-chip.all{background:linear-gradient(90deg,rgba(122,162,255,.22),rgba(192,132,252,.22));border-color:#4a5590;color:#e2e7ff;font-weight:700}
      .mc-foot{display:flex;justify-content:center;padding-top:12px;border-top:1px solid var(--line)}
      .mc-foot a{color:var(--dim);font-size:13px;text-decoration:none}
      .mc-foot a:hover{color:#ffb3c1}
    </style>"""

    def _member_page(inner):
        return ("<!doctype html><html lang=\"zh-Hant\"><head><meta charset=\"utf-8\">"
                "<meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">"
                "<title>會員中心</title>" + _MEMBER_CSS + "</head><body>" + inner + "</body></html>")

    @app.route('/member', methods=['GET', 'POST'])
    def member_center():
        username = session.get('member_user')
        if not username:
            return member_login()
        user = _get_user(username)
        user = _ensure_month(user)
        if sync_plan(username):        # ★ 2026-10-07 凜：讀取即校正 pro/vip
            user = _get_user(username) or user
        plan = _get_plan(user['plan']) or {}
        plan_agents = _plan_agents(user['plan'])
        all_agents = ('*' in plan_agents)
        if all_agents:
            chips = '<span class="mc-chip all">✨ 全部 Agent</span>'
            agent_count = '全部'
        else:
            chips = ''.join('<span class="mc-chip">' + str(a) + '</span>' for a in plan_agents)
            if not chips:
                chips = '<span class="mc-chip">暫無可用 agent</span>'
            agent_count = len(plan_agents)
        balance = user.get('balance_tokens') or 0
        used = user.get('monthly_used') or 0
        quota = int(plan.get('monthly_tokens') or 0)
        _unlmt = str(user.get('plan') or '').lower() in ('admin', 'root')   # ★ 2026-10-03 indexPage：admin/root 為無限方案 → 額度顯示 ∞
        if _unlmt:
            quota_txt = '∞'; pct = 0
        else:
            quota_txt = format(quota, ',')
            pct = min(100, int(round(used * 100.0 / quota))) if quota else 0
        neg = balance < 0
        # ★ 2026-10-07 凜：警示與紅條一律以「Token 餘額為負」為判準，不再看免費月額度
        over = neg
        bal_hkd = _hkd(balance)                                   # ★ 2026-10-03 春：Token 餘額的 HK$ 金額（負數照顯示）
        used_hkd = _hkd(used)                                     # ★ 本月已用的 HK$ 金額
        quota_hkd = u'' if (_unlmt or not quota) else _hkd(quota)   # ★ 額度的 HK$ 金額（∞ 方案不顯示）
        if neg:
            note = '⚠️ Token 餘額為負，請充值或升級方案。'
        elif quota and pct >= 80:
            note = '本月用量已達 ' + str(pct) + '%，接近上限。'
        else:
            note = ''
        note_html = ('<div class="mc-note">' + note + '</div>') if note else ''
        bar_cls = ' class="over"' if over else ''
        plan_label = str(user.get('plan') or '').upper()
        initial = (str(username) or '?')[:1].upper()
        desc_html = ('<p class="mc-desc">' + str(plan.get('desc') or '') + '</p>') if plan.get('desc') else ''
        inner = (
            '<div class="mc">'
            '<div class="mc-hero">'
            '<div class="mc-avatar">' + initial + '</div>'
            '<div class="mc-who"><div class="mc-welcome">歡迎回來</div>'
            '<div class="mc-name">' + str(username) + '</div></div>'
            '<div class="mc-planbadge">' + plan_label + '</div>'
            '</div>'
            + desc_html +
            '<div class="mc-bal">'
            '<div class="mc-bal-label">Token 餘額</div>'
            '<div class="mc-bal-num' + (' neg' if neg else '') + '">' + format(balance, ',') + '</div>'
            '<div class="mc-bal-hkd' + (' neg' if neg else '') + '">' + bal_hkd + '</div>'
            '<div class="mc-usage-top"><span>本月已用</span><span><b>' + format(used, ',') + '</b>'
            + '<span class="mc-hkd">' + used_hkd + '</span> / ' + quota_txt
            + (('<span class="mc-hkd q">' + quota_hkd + '</span>') if quota_hkd else '') + '</span></div>'
            '<div class="mc-bar"><i' + bar_cls + ' style="width:' + str(pct) + '%"></i></div>'
            + note_html +
            '</div>'
            '<a class="mc-recharge" href="/recharge">⚡ 立即充值 / 升級方案</a>'
            '<div class="mc-sec"><div class="mc-sec-title">可用 Agent <span class="mc-count">(' + str(agent_count) + ')</span></div>'
            '<div class="mc-chips">' + chips + '</div></div>'
            '<div class="mc-foot"><a href="/logout">登出</a></div>'
            '</div>'
        )
        return _member_page(inner)

    @app.route('/api/member/me')
    def api_member_me():
        username = session.get('member_user')
        if not username:
            return jsonify({'logged_in': False})
        user = _get_user(username)
        user = _ensure_month(user)
        if sync_plan(username):        # ★ 2026-10-07 凜：讀取即校正 pro/vip
            user = _get_user(username) or user
        return jsonify({'logged_in': True, 'username': username, 'plan': user['plan'],
                        'balance_tokens': user['balance_tokens'], 'monthly_used': user['monthly_used']})

    print(f"[會員系統] 補丁已載入 | DB={MEMBER_DB} | 路由: /login /register /logout /member /api/member/me")

# ---------- 初始化 ----------
_init_db()


# ============================================================
# 【已停用】身分感知注入 Identity Awareness Injection（2026-09-27）
#   原舊身分塊已整段移出，改由唯一真相來源提供：
#     身分核心_202609250200/保丁.py → admin 判定 = member.db users.is_admin=1
#     （不再有帳號名白名單、不再有 plan == 'admin' 提權、不再有 MOK_MEMBER_ADMIN fallback）
#   本檔保留 /login /register /logout /member /api/member/me 與 token 扣費。
# ============================================================
