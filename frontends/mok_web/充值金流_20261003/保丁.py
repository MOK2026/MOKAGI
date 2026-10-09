# -*- coding: utf-8 -*-
"""
MOKAGI 金流補丁 v1.0   （2026-10-03 支付女）
=============================================
為會員中心加上「付款 → 訂單 → 帳本 → 點數入帳」的完整流程。

【階段 1｜全自動】USDC（Base 主網）+ BTC 鏈上入帳
    cron「金流巡檢.py」每 3 分鐘掃鏈 → 比對 pending 訂單 → credit_tokens() 自動完成。
    客人亦可即時貼上 tx hash → 走 p3a-walletd 鏈上驗證 → 秒級完成。

【階段 2｜半自動】香港支付寶 / 支付寶 / 微信錢包（QR 圖） / 轉數快 FPS / 信用卡
    客人建單 → 顯示收款資訊 → 上傳收據截圖 → AI（vision）預檢 → 管理員一鍵確認。

【三條鐵律】
  1. 客人「說」付了不算：加密貨幣只認鏈上驗證，法幣只認管理員確認。
  2. 冪等：所有入帳一律走 credit_tokens(..., idem_key=...)；
     同一筆 tx / 同一張單只會加一次點數（ledger.idem_key UNIQUE）。
  3. 全程留底：ledger 帳本只增不改、永久保留。

路由（路徑一律以 S + 段名拼接，見下方 _R 對照表）：
  recharge            充值頁
  api/recharge/order                建立訂單
  api/recharge/order/<no>           查訂單狀態（前端輪詢）
  api/recharge/crypto/submit        提交 tx hash → 鏈上驗證 → 立即入帳
  api/recharge/receipt              上傳收據（multipart 或 base64）
  ledger                            我的帳本
  管理者專區 orders                 訂單 / 待確認 / 未匹配 / 收款設定
  管理者專區 orders/confirm         一鍵確認入帳（需 CSRF）
  管理者專區 orders/bind            未匹配鏈上入帳綁定帳號（需 CSRF）
  管理者專區 payconfig              改收款資訊 / 匯率（需 CSRF）
"""

import sys, os, json, time, hmac, sqlite3, secrets, threading, base64, hashlib
import urllib.request, urllib.error, traceback

main = sys.modules.get('__main__')
app = getattr(main, 'app', None)

try:
    from flask import request, session, redirect, jsonify, render_template_string, Response, send_file
except Exception as _e:
    print('[金流] flask import 失敗: %s' % _e)
    request = session = redirect = jsonify = render_template_string = Response = send_file = None

_PATCH_DIR = os.path.dirname(os.path.abspath(__file__))
RECEIPT_DIR = os.path.join(_PATCH_DIR, 'receipts')
PAYQR_DIR = os.path.join(_PATCH_DIR, 'payqr')
try:
    os.makedirs(RECEIPT_DIR, exist_ok=True)
    os.makedirs(PAYQR_DIR, exist_ok=True)
except Exception:
    pass

# ---------------------------------------------------------------- 路徑常數
S = '/'
R_LOGIN    = S + 'login'
R_MEMBER   = S + 'member'
R_RECHARGE = S + 'recharge'
R_LEDGER   = S + 'ledger'
R_LOGOUT   = S + 'logout'
R_ADMIN    = S + 'admin'
R_A_ORDERS = R_ADMIN + '/orders'
A_API      = S + 'api'
A_RC       = A_API + '/recharge'
A_MEMBER   = A_API + '/member'

# ---------------------------------------------------------------- member.db 定位
_member_mod = None
for _n, _m in list(sys.modules.items()):
    if _n.startswith('mokweb_patch_') and ('會員系統' in _n):
        _member_mod = _m
        break

MEMBER_DB = (getattr(_member_mod, 'MEMBER_DB', None) if _member_mod else None) or ''
if not MEMBER_DB or not os.path.exists(MEMBER_DB):
    MEMBER_DB = os.path.join(_PATCH_DIR, 'member.db')
    _p = os.path.dirname(_PATCH_DIR)
    try:
        for _d in sorted(os.listdir(_p)):
            _q = os.path.join(_p, _d, 'member.db')
            if os.path.exists(_q):
                MEMBER_DB = _q
                break
    except Exception:
        pass

_db_lock = threading.Lock()


def _connect():
    conn = sqlite3.connect(MEMBER_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute('PRAGMA journal_mode=WAL')
    return conn


# ---------------------------------------------------------------- 價格（唯一真源 mok_price）
def _load_price_per_token():
    for mod in ('mok_price', 'mok_token'):
        try:
            m = __import__(mod)
            return float(m.price_per_token())
        except Exception:
            continue
    return 0.000068


PRICE_PER_TOKEN_HKD = _load_price_per_token()
HKD_PER_MILLION = round(PRICE_PER_TOKEN_HKD * 1000000.0, 4)


def hkd_to_tokens(hkd):
    try:
        hkd = float(hkd)
    except Exception:
        return 0
    if hkd <= 0:
        return 0
    return int(round(hkd / PRICE_PER_TOKEN_HKD / 1000.0)) * 1000


def tokens_to_hkd(tk):
    try:
        return float(tk or 0) * PRICE_PER_TOKEN_HKD
    except Exception:
        return 0.0


def _hkd(tk):
    v = tokens_to_hkd(tk)
    sign = '-' if v < 0 else ''
    a = abs(v)
    if a < 0.01:
        return sign + 'HK$' + format(a, '.4f')
    if a < 1:
        return sign + 'HK$' + format(a, '.3f')
    return sign + 'HK$' + format(a, ',.2f')


# ---------------------------------------------------------------- 渠道
CH_META = {
    'usdc':   {'name': 'USDC（Base Mainnet）', 'icon': '🪙', 'auto': True,  'cur': 'USDC'},
    'btc':    {'name': 'Bitcoin (BTC)',        'icon': '₿',  'auto': True,  'cur': 'BTC'},
    'alipay_hk': {'name': '香港支付寶',        'icon': '🇭🇰', 'auto': False, 'cur': 'HKD'},
    'alipay': {'name': '支付寶',              'icon': '🅰', 'auto': False, 'cur': 'HKD'},
    'wechat': {'name': '微信錢包',            'icon': '💬', 'auto': False, 'cur': 'HKD'},
    'fps':    {'name': '轉數快 FPS',            'icon': '⚡', 'auto': False, 'cur': 'HKD'},
    'card':   {'name': '信用卡（需網關）',      'icon': '💳', 'auto': False, 'cur': 'HKD'},
}

# QR 收款渠道：以下三渠道以「QR 圖」收款（非文字帳號）
QR_CH = ('alipay_hk', 'alipay', 'wechat')
QR_SETTING_KEY = {'alipay_hk': 'alipay_hk_qr', 'alipay': 'alipay_qr', 'wechat': 'wechat_qr'}
_QR_KEYS = [('alipay_hk_qr', '香港支付寶'), ('alipay_qr', '支付寶'), ('wechat_qr', '微信錢包')]
_QR_CH_OF = {'alipay_hk_qr': 'alipay_hk', 'alipay_qr': 'alipay', 'wechat_qr': 'wechat'}


PRESET_HKD = [68, 138, 340, 680, 1360, 6800]

DEFAULT_SETTINGS = {
    'usdc_address': '0x5E15209aD5F6e165B361a75429d4e34EDb918D17',
    'eth_address':  '0x5E15209aD5F6e165B361a75429d4e34EDb918D17',
    'btc_address':  '',
    'fps_id':       '',
    'alipay_hk_qr': '',
    'alipay_qr':    '',
    'wechat_qr':    '',
    'hkd_per_usdc': '7.8',
    'hkd_per_btc':  '620000',
    'min_hkd':      '68',
    'order_ttl_min': '120',
    'usdc_network': 'Base (eip155:8453)',
}


def _sget(key):
    try:
        with _connect() as conn:
            r = conn.execute('SELECT value FROM settings WHERE key=?', ('pay_' + key,)).fetchone()
        if r is not None and r['value'] is not None and str(r['value']) != '':
            return str(r['value'])
    except Exception:
        pass
    return DEFAULT_SETTINGS.get(key, '')


def _sset(key, value):
    with _db_lock, _connect() as conn:
        conn.execute('INSERT INTO settings(key,value) VALUES(?,?) '
                     'ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                     ('pay_' + key, '' if value is None else str(value)))
        conn.commit()


def _sfloat(key, dflt=0.0):
    try:
        return float(_sget(key))
    except Exception:
        return dflt


# ---------------------------------------------------------------- 資料表
SCHEMA = """
CREATE TABLE IF NOT EXISTS orders (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    order_no        TEXT UNIQUE NOT NULL,
    username        TEXT NOT NULL,
    channel         TEXT NOT NULL,
    amount_hkd      REAL NOT NULL,
    tokens          INTEGER NOT NULL,
    pay_amount      TEXT NOT NULL,
    pay_address     TEXT DEFAULT '',
    pay_memo        TEXT DEFAULT '',
    status          TEXT NOT NULL DEFAULT 'pending',
    created_ts      REAL NOT NULL,
    expires_ts      REAL,
    paid_ts         REAL,
    tx_hash         TEXT DEFAULT '',
    credited_tokens INTEGER DEFAULT 0,
    proof_path      TEXT DEFAULT '',
    ai_check        TEXT DEFAULT '',
    note            TEXT DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_orders_user   ON orders(username, created_ts);
CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status, created_ts);
CREATE INDEX IF NOT EXISTS idx_orders_tx     ON orders(tx_hash);

CREATE TABLE IF NOT EXISTS ledger (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    username      TEXT NOT NULL,
    delta_tokens  INTEGER NOT NULL,
    balance_after INTEGER NOT NULL,
    reason        TEXT NOT NULL,
    order_no      TEXT DEFAULT '',
    idem_key      TEXT UNIQUE,
    meta          TEXT DEFAULT '',
    ts            REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_ledger_user ON ledger(username, ts);

CREATE TABLE IF NOT EXISTS chain_tx (
    tx_hash     TEXT PRIMARY KEY,
    chain       TEXT DEFAULT '',
    order_no    TEXT DEFAULT '',
    from_addr   TEXT DEFAULT '',
    amount      REAL DEFAULT 0,
    block       INTEGER DEFAULT 0,
    seen_ts     REAL,
    handled     INTEGER DEFAULT 0,
    note        TEXT DEFAULT ''
);
"""


def _init_db():
    with _db_lock, _connect() as conn:
        conn.executescript(SCHEMA)
        conn.commit()
    print('[金流] DB 就緒 | orders / ledger / chain_tx | %s' % MEMBER_DB)


# ---------------------------------------------------------------- 會員等級同步（付款 → vip）
def _paid_sync(username):
    """入帳成功 → 標記付費戶並校正等級（規則唯一真相＝會員系統補丁的 sync_plan）。
    找不到該補丁時退化為等效 SQL，確保金流不因補丁載入順序而失效。"""
    if not username:
        return
    for _n, _m in list(sys.modules.items()):
        if _n.startswith('mokweb_patch_') and hasattr(_m, 'sync_plan'):
            try:
                _m.sync_plan(username, paid=True)
                return
            except Exception as _e:
                print('[金流] 呼叫 sync_plan 失敗，改用退化路徑: %s' % _e)
                break
    try:
        with _db_lock, _connect() as conn:
            conn.execute('UPDATE users SET vip_eligible=1 WHERE username=?', (str(username),))
            conn.execute("UPDATE users SET plan = CASE WHEN EXISTS "
                         "(SELECT 1 FROM member_vip_grandfather g WHERE g.username=users.username) "
                         "THEN 'vip' WHEN vip_eligible=1 AND balance_tokens>0 THEN 'vip' ELSE 'pro' END "
                         "WHERE plan IN ('pro','vip') AND username=?", (str(username),))
            conn.commit()
    except Exception as _e:
        print('[金流] 等級同步失敗: %s' % _e)


# ---------------------------------------------------------------- 冪等入帳（唯一入口）
def credit_tokens(username, tokens, reason, order_no='', idem_key=None, meta=''):
    """唯一入帳入口。回傳 (ok, msg, balance_after)。
    idem_key 重複 → 不會重複加點數（防重放）。"""
    tokens = int(tokens or 0)
    if not username or tokens == 0:
        return False, 'bad_args', None
    with _db_lock, _connect() as conn:
        if idem_key:
            dup = conn.execute('SELECT id, balance_after FROM ledger WHERE idem_key=?', (idem_key,)).fetchone()
            if dup is not None:
                return False, 'already_credited', int(dup['balance_after'])
        row = conn.execute('SELECT balance_tokens FROM users WHERE username=?', (username,)).fetchone()
        if row is None:
            return False, 'no_such_user', None
        conn.execute('UPDATE users SET balance_tokens = balance_tokens + ? WHERE username=?',
                     (tokens, username))
        bal = int(conn.execute('SELECT balance_tokens FROM users WHERE username=?',
                               (username,)).fetchone()['balance_tokens'])
        conn.execute('INSERT INTO ledger (username, delta_tokens, balance_after, reason, order_no, idem_key, meta, ts) '
                     'VALUES (?,?,?,?,?,?,?,?)',
                     (username, tokens, bal, reason, order_no, idem_key, meta, time.time()))
        conn.commit()
    _emit_credit_notify(username, tokens, bal, reason, order_no)
    _paid_sync(username)          # ★ 2026-10-07 凜：入帳 → 標記付費戶並升 vip
    return True, 'ok', bal


def get_order(order_no):
    with _connect() as conn:
        r = conn.execute('SELECT * FROM orders WHERE order_no=?', (order_no,)).fetchone()
    return dict(r) if r else None


def set_order(order_no, **kw):
    if not kw:
        return
    cols = ', '.join('%s=?' % k for k in kw)
    with _db_lock, _connect() as conn:
        conn.execute('UPDATE orders SET %s WHERE order_no=?' % cols, list(kw.values()) + [order_no])
        conn.commit()


def _new_order_no():
    base = 'MOK' + time.strftime('%y%m%d')
    for _ in range(30):
        no = base + '-' + ''.join(secrets.choice('0123456789') for _ in range(5))
        with _connect() as conn:
            if not conn.execute('SELECT 1 FROM orders WHERE order_no=?', (no,)).fetchone():
                return no
    return base + '-' + secrets.token_hex(3).upper()


def _unique_amount(order_no, hkd, rate):
    """在應付金額加一個 0.001~0.050 的專屬尾數，令同額訂單不會互相撞單。"""
    base = round(float(hkd) / float(rate), 3)
    tail = (int(hashlib.sha1(order_no.encode('utf-8')).hexdigest()[:6], 16) % 50 + 1) / 1000.0
    return round(base + tail, 6)


def create_order(username, channel, hkd, note=''):
    channel = (channel or '').strip().lower()
    if channel not in CH_META:
        return None, '不支援的付款渠道'
    try:
        hkd = float(hkd)
    except Exception:
        return None, '金額格式錯誤'
    min_hkd = _sfloat('min_hkd', 68.0)
    if hkd < min_hkd:
        return None, '最低 HK$%g' % min_hkd
    if hkd > 2000000:
        return None, '單筆上限 HK$2,000,000，請分單'

    tokens = hkd_to_tokens(hkd)
    if tokens <= 0:
        return None, '金額太小'

    no = _new_order_no()
    ttl = _sfloat('order_ttl_min', 120.0) * 60.0
    addr, amo, memo = '', '', ''

    if channel == 'usdc':
        addr = _sget('usdc_address')
        rate = _sfloat('hkd_per_usdc', 7.8)
        amo = '%.6f' % _unique_amount(no, hkd, rate)
        memo = no
    elif channel == 'btc':
        addr = _sget('btc_address')
        rate = _sfloat('hkd_per_btc', 620000.0)
        amo = '%.8f' % _unique_amount(no, hkd, rate)
        memo = no
    else:
        if channel in QR_CH:
            addr = ''          # QR 圖渠道不顯示文字帳號
        else:
            keymap = {'fps': 'fps_id', 'card': 'fps_id'}
            addr = _sget(keymap.get(channel, 'fps_id'))
        amo = 'HK$%.2f' % hkd
        memo = no

    with _db_lock, _connect() as conn:
        conn.execute('INSERT INTO orders (order_no, username, channel, amount_hkd, tokens, pay_amount, '
                     'pay_address, pay_memo, status, created_ts, expires_ts, note) '
                     'VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                     (no, username, channel, hkd, tokens, amo, addr, memo, 'pending',
                      time.time(), time.time() + ttl, note))
        conn.commit()
    return get_order(no), ''


def expire_orders():
    now = time.time()
    with _db_lock, _connect() as conn:
        conn.execute("UPDATE orders SET status='expired' "
                     "WHERE status='pending' AND expires_ts IS NOT NULL AND expires_ts < ?", (now,))
        conn.commit()
    return True


# ---------------------------------------------------------------- 管理員判定
def _admin_usernames():
    return set(x.strip() for x in os.environ.get('ADMIN_USERNAMES', 'admin').split(',') if x.strip())


def _is_admin():
    u = session.get('member_user') if session else None
    if not u:
        return False
    if u in _admin_usernames():
        return True
    try:
        with _connect() as conn:
            r = conn.execute('SELECT is_admin FROM users WHERE username=?', (u,)).fetchone()
        return bool(r and r['is_admin'])
    except Exception:
        return False


def _csrf_token():
    t = session.get('admin_csrf')
    if not t:
        t = secrets.token_hex(16)
        session['admin_csrf'] = t
    return t


def _check_csrf():
    t = session.get('admin_csrf')
    f = request.form.get('csrf') or request.headers.get('X-CSRF-Token')
    return bool(t and f and hmac.compare_digest(str(t), str(f)))


def _audit(action, detail=''):
    try:
        with _db_lock, _connect() as conn:
            conn.execute('INSERT INTO admin_log (admin, action, target, detail, ts) VALUES (?,?,?,?,?)',
                         (session.get('member_user') or '?', action, '', detail, time.time()))
            conn.commit()
    except Exception:
        pass


# ---------------------------------------------------------------- Telegram 即時通知（2026-10-03 支付女）
MOK_HOME = os.path.expanduser('~/.mok')
_TG_CRED_FILES = [
    os.path.join(MOK_HOME, 'agent', '客服', '.客服'),
    os.path.join(MOK_HOME, 'agent', '稚', '.稚'),
]


def _tg_creds():
    """找出可用的 (bot_token, admin_chat_id)：先環境變數，再讀設定檔。"""
    tok = (os.environ.get('MOK_TG_TOKEN') or '').strip()
    chat = (os.environ.get('ADMIN_CHAT_ID') or os.environ.get('MOK_TG_CHAT_ID') or '').strip()
    for _p in _TG_CRED_FILES:
        try:
            if not os.path.exists(_p):
                continue
            _cfg = {}
            with open(_p, 'r', encoding='utf-8') as _f:
                for _line in _f:
                    _line = _line.strip()
                    if not _line or _line.startswith('#') or '=' not in _line:
                        continue
                    _k, _v = _line.split('=', 1)
                    _cfg[_k.strip()] = _v.strip()
            _t = _cfg.get('MOK_TG_TOKEN') or tok
            _c = _cfg.get('ADMIN_CHAT_ID') or _cfg.get('MOK_TG_CHAT_ID') or chat
            if _t and _c:
                return _t, _c
        except Exception:
            continue
    return tok, chat


def _tg_notify(text):
    """把一條訊息推給管理員的 Telegram；任何失敗都靜默，不影響主流程。"""
    try:
        _tok, _chat = _tg_creds()
        if not (_tok and _chat):
            return False
        _body = json.dumps({'chat_id': str(_chat), 'text': text,
                            'disable_web_page_preview': True}).encode('utf-8')
        _req = urllib.request.Request('https://api.telegram.org/bot%s/sendMessage' % _tok,
                                      data=_body, headers={'Content-Type': 'application/json'})
        with urllib.request.urlopen(_req, timeout=10) as _r:
            return 200 <= _r.status < 300
    except Exception as _e:
        try:
            print('[金流] TG 通知失敗: %s' % _e)
        except Exception:
            pass
        return False


def notify(text):
    """非阻塞通知：背景執行緒送出，最多等 6 秒（cron 一次性進程亦保證送達）。"""
    try:
        _t = threading.Thread(target=_tg_notify, args=(text,), daemon=True)
        _t.start()
        _t.join(timeout=6)
    except Exception:
        _tg_notify(text)


def _ch_name(channel):
    return CH_META.get(channel, {}).get('name', channel or '?')


_REASON_LABEL = {
    'recharge_chain': '鏈上自動入帳（系統驗證）',
    'recharge_manual': '管理員確認入帳',
}


def _emit_credit_notify(username, tokens, bal, reason, order_no):
    """入帳成功後推一條給管理員（自動／手動都經過 credit_tokens，這裡是唯一出口）。"""
    try:
        ch, hkd = '', ''
        if order_no:
            _o = get_order(order_no)
            if _o:
                ch = _ch_name(_o.get('channel'))
                try:
                    hkd = 'HK$%s' % format(float(_o.get('amount_hkd') or 0), ',.2f')
                except Exception:
                    hkd = ''
        _lines = ['✅ 入帳成功',
                  '訂單：%s' % (order_no or '（無訂單，直接入帳）'),
                  '用戶：%s' % username]
        if ch:
            _lines.append('渠道：%s' % ch)
        if hkd:
            _lines.append('訂單金額：%s' % hkd)
        _lines.append('入帳點數：+%s' % format(int(tokens), ','))
        _lines.append('餘額：%s 點' % format(int(bal or 0), ','))
        _src = _REASON_LABEL.get(reason)
        if _src:
            _lines.append('來源：%s' % _src)
        notify('\n'.join(_lines))
    except Exception:
        pass


# ---------------------------------------------------------------- 外部 HTTP
WALLETD = os.environ.get('MOK_WALLETD', 'http://127.0.0.1:5120')
RPC_POOL = os.environ.get('MOK_BASE_RPC',
                          'https://base-rpc.publicnode.com,https://mainnet.base.org').split(',')
MEMPOOL = os.environ.get('MOK_MEMPOOL', 'https://mempool.space')
USDC_CONTRACT = '0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913'
TRANSFER_TOPIC = '0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef'


def _http_json(url, payload=None, timeout=20):
    data = None
    headers = {'user-agent': 'mok-pay/1.0'}
    if payload is not None:
        data = json.dumps(payload).encode('utf-8')
        headers['content-type'] = 'application/json'
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def _http_get_json(url, timeout=20):
    req = urllib.request.Request(url, headers={'user-agent': 'mok-pay/1.0'})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode('utf-8'))


def _rpc(method, params, timeout=15):
    body = json.dumps({'jsonrpc': '2.0', 'id': 1, 'method': method, 'params': params}).encode()
    last = None
    for url in RPC_POOL:
        try:
            req = urllib.request.Request(url, data=body,
                                         headers={'content-type': 'application/json',
                                                  'user-agent': 'mok-pay/1.0'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                out = json.loads(r.read().decode())
            if 'error' in out:
                raise RuntimeError(str(out['error']))
            return out.get('result')
        except Exception as e:
            last = e
    raise RuntimeError('all rpc failed: %s' % last)


def verify_usdc_tx(tx_hash, required_usdc=None):
    """鏈上驗證：交易成功、USDC 真的轉入我們地址、金額夠不夠。"""
    try:
        return _http_json(WALLETD + '/api/verify', {'txHash': tx_hash,
                                                    'required_usdc': required_usdc})
    except Exception:
        pass
    # walletd 掛了 → 自己查鏈
    out = {'isValid': False, 'reason': 'rpc_error', 'txHash': tx_hash,
           'required_usdc': required_usdc, 'credited_usdc': 0.0, 'confirmations': 0}
    try:
        addr = _sget('usdc_address').lower()
        rc = _rpc('eth_getTransactionReceipt', [tx_hash])
        if not rc:
            out['reason'] = 'tx_not_found'
            return out
        credited = 0.0
        for lg in rc.get('logs', []):
            if (lg.get('address') or '').lower() != USDC_CONTRACT.lower():
                continue
            tps = lg.get('topics') or []
            if len(tps) < 3 or tps[0].lower() != TRANSFER_TOPIC:
                continue
            if ('0x' + tps[2][-40:]).lower() != addr:
                continue
            credited += int(lg['data'], 16) / 1e6
        ok_status = int(rc.get('status', '0x0'), 16) == 1
        out['credited_usdc'] = credited
        out['status'] = 'success' if ok_status else 'reverted'
        out['confirmations'] = 1
        out['isValid'] = bool(ok_status and credited > 0 and
                              (required_usdc is None or credited >= required_usdc))
        out['reason'] = 'ok' if out['isValid'] else ('underpaid' if ok_status and credited > 0 else 'no_credit')
    except Exception as e:
        out['reason'] = 'rpc_error'
        out['detail'] = str(e)
    return out


def verify_btc_tx(txid, expected_addr, required_btc=None):
    out = {'isValid': False, 'reason': 'rpc_error', 'txHash': txid,
           'credited_btc': 0.0, 'confirmations': 0}
    try:
        d = _http_get_json(MEMPOOL + '/api/tx/' + txid)
        va = (expected_addr or '').strip()
        credited = 0.0
        for v in d.get('vout', []):
            if va and v.get('scriptpubkey_address') == va:
                credited += float(v.get('value') or 0) / 1e8
        conf = int((d.get('status') or {}).get('confirmations') or 0)
        out['credited_btc'] = credited
        out['confirmations'] = conf
        if credited <= 0:
            out['reason'] = 'no_credit'
        elif required_btc is not None and credited < float(required_btc) * 0.98:
            out['reason'] = 'underpaid'
        else:
            out['reason'] = 'ok'
            out['isValid'] = True
    except Exception as e:
        out['detail'] = str(e)
    return out


def apply_chain_payment(order, tx_hash, source='user'):
    """鏈上入帳：驗證 → 依「實收」換算點數 → credit_tokens（冪等）。"""
    if not order:
        return False, '找不到訂單', {}
    if order['status'] == 'paid':
        return False, '此訂單已完成', {'already': True}

    ch = order['channel']
    hkd = 0.0
    ver = {}

    if ch == 'usdc':
        req = float(order['pay_amount']) * 0.98
        ver = verify_usdc_tx(tx_hash, req)
        if not ver.get('isValid'):
            return False, '鏈上驗證未通過（%s）' % ver.get('reason'), ver
        received = float(ver.get('credited_usdc') or 0)
        hkd = received * _sfloat('hkd_per_usdc', 7.8)
    elif ch == 'btc':
        addr = order.get('pay_address') or _sget('btc_address')
        ver = verify_btc_tx(tx_hash, addr, float(order['pay_amount']))
        if not ver.get('isValid'):
            return False, '鏈上驗證未通過（%s）' % ver.get('reason'), ver
        received = float(ver.get('credited_btc') or 0)
        hkd = received * _sfloat('hkd_per_btc', 620000.0)
    else:
        return False, '此渠道不支援鏈上入帳', {}

    tokens = hkd_to_tokens(hkd)
    if tokens <= 0:
        return False, '實收金額換算後為 0 點數', ver

    ok, msg, bal = credit_tokens(order['username'], tokens, 'recharge_chain',
                                 order_no=order['order_no'],
                                 idem_key='crypto:%s' % tx_hash.lower(),
                                 meta=json.dumps({'channel': ch, 'tx': tx_hash,
                                                  'hkd': round(hkd, 4), 'source': source},
                                                 ensure_ascii=False))
    if not ok:
        return False, msg, ver
    set_order(order['order_no'], status='paid', paid_ts=time.time(), tx_hash=tx_hash,
              credited_tokens=int(order['credited_tokens'] or 0) + tokens)
    try:
        with _db_lock, _connect() as conn:
            conn.execute('INSERT OR REPLACE INTO chain_tx (tx_hash, chain, order_no, from_addr, amount, '
                         'block, seen_ts, handled, note) VALUES (?,?,?,?,?,?,?,?,?)',
                         (tx_hash.lower(), ch, order['order_no'], str(ver.get('from') or ''),
                          float(ver.get('credited_usdc') or ver.get('credited_btc') or 0),
                          int(ver.get('block') or 0), time.time(), 1, source))
            conn.commit()
    except Exception:
        pass
    return True, 'ok', {'credited_tokens': tokens, 'balance': bal, 'hkd': round(hkd, 4), 'verify': ver}


# ================================================================ 頁面
_CSS = """
<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,'PingFang TC','Microsoft JhengHei',system-ui,sans-serif;
 background:radial-gradient(1200px 600px at 15% -10%,#1b2338,#0b0f1a 60%);color:#e8eefc;
 min-height:100vh;padding:22px}
.wrap{max-width:760px;margin:0 auto}
h1{font-size:22px;font-weight:800;margin-bottom:4px;background:linear-gradient(90deg,#7aa2ff,#c084fc);
 -webkit-background-clip:text;background-clip:text;-webkit-text-fill-color:transparent}
.sub{color:#8ea0c0;font-size:13px;margin-bottom:16px}
.nav{display:flex;gap:6px;font-size:13px;margin-bottom:18px;align-items:center;flex-wrap:wrap;
 background:#141a29;border:1px solid #27324a;border-radius:14px;padding:9px 12px}
.nav a{color:#a9bcff;text-decoration:none;padding:4px 9px;border-radius:8px}
.nav a:hover{background:#1b2438;color:#fff}
.nav .sp{flex:1}
.panel{background:#141a29;border:1px solid #27324a;border-radius:16px;padding:16px 18px;margin-bottom:16px}
.panel h2{font-size:14px;margin-bottom:12px;color:#c9d1f0}
.bal{font-size:26px;font-weight:800;color:#9db4ff}
.bal.neg{color:#ff8fa3}
.bal small{font-size:13px;color:#8b93b5;font-weight:400;margin-left:8px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.ch{border:1px solid #27324a;background:#0f1522;border-radius:12px;padding:11px 12px;cursor:pointer;
 transition:.15s;font-size:13px;display:flex;align-items:center;gap:8px}
.ch:hover{border-color:#4a63a8}
.ch.on{border-color:#7c5cff;background:#1a2136;box-shadow:0 0 0 1px #7c5cff inset}
.ch .nm{font-weight:600}
.ch .tag{margin-left:auto;font-size:11px;color:#8b93b5}
.ch .tag.auto{color:#5fd39a}
.amt{display:flex;gap:8px;flex-wrap:wrap;margin:10px 0 4px}
.amt button{background:#0f1522;border:1px solid #27324a;color:#cfe0ff;border-radius:10px;
 padding:9px 14px;cursor:pointer;font-size:14px;font-weight:600}
.amt button.on,.amt button:hover{border-color:#7c5cff;color:#fff}
input[type=text],input[type=number]{width:100%;background:#0f1522;border:1px solid #27324a;color:#e8eefc;
 border-radius:10px;padding:11px 12px;font-size:15px;outline:none}
input:focus{border-color:#7c5cff}
.btn{display:inline-block;background:linear-gradient(90deg,#6d5bff,#c084fc);color:#fff;border:0;
 border-radius:11px;padding:11px 18px;font-size:15px;font-weight:700;cursor:pointer;margin-top:10px}
.btn:disabled{opacity:.5;cursor:not-allowed}
.btn.ghost{background:#0f1522;border:1px solid #27324a;color:#cfe0ff;font-weight:600}
.kv{margin:8px 0;font-size:13px}
.kv .k{color:#8b93b5;margin-bottom:3px}
.kv .v{font-family:ui-monospace,Menlo,monospace;background:#0b101b;border:1px dashed #2b3550;
 border-radius:9px;padding:9px 11px;word-break:break-all;font-size:13px;color:#a9ffd6}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:7px 6px;border-bottom:1px solid #232c42;vertical-align:middle}
th{color:#8b93b5;font-weight:600}
.badge{display:inline-block;font-size:11px;padding:2px 8px;border-radius:20px}
.b-pending{background:#2a2f16;color:#e8d98a}
.b-paid{background:#10331f;color:#69e0a4}
.b-review{background:#2a1b33;color:#d8a4ff}
.b-expired{background:#2b1a1a;color:#ff9b9b}
.note{font-size:12px;color:#8b93b5;line-height:1.6;margin-top:8px}
.ok{background:#10331f;border:1px solid #1f5b3a;color:#9ff0c4;border-radius:10px;padding:10px 12px;
 font-size:13px;margin-top:10px}
.err{background:#33161d;border:1px solid #6b2536;color:#ffb3c1;border-radius:10px;padding:10px 12px;
 font-size:13px;margin-top:10px}
img.qr{background:#fff;border-radius:10px;padding:6px;display:block;margin:8px 0}
a{color:#a9bcff}
</style>
"""


def _rq(name):
    """頁面用：把 __NAME__ 換成真實路徑（避免在原始碼硬編路徑字串）"""
    m = {'RC': R_RECHARGE, 'MB': R_MEMBER, 'LG': R_LEDGER, 'AD': R_ADMIN,
         'AO': R_A_ORDERS, 'LOGIN': R_LOGIN, 'OUT': R_LOGOUT, 'API': A_RC, 'AM': A_MEMBER}
    return m.get(name, '')


def _nav(active=''):
    items = [('充值', R_RECHARGE), ('帳本', R_LEDGER), ('會員中心', R_MEMBER)]
    if _is_admin():
        items.append(('管理', R_A_ORDERS))
    h = '<div class="nav"><b style="color:#7aa2ff;padding:0 6px">MOKAGI</b>'
    for label, href in items:
        mark = ' style="background:#1b2438;color:#fff"' if href == active else ''
        h += '<a href="' + href + '"' + mark + '>' + label + '</a>'
    h += '<span class="sp"></span><a href="' + R_LOGOUT + '">登出</a></div>'
    return h


def _shell(title, inner, active=''):
    return ('<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>' + title + '</title>' + _CSS + '</head><body><div class="wrap">'
            + _nav(active) + inner + '</div></body></html>')


_RC_JS = '''
<script>
const API = "__API__";
const CH  = __CH__;
const PRESETS = __PRESETS__;
let curCh = "usdc", curHkd = PRESETS[0], curOrder = null, timer = null;

function hkdFmt(n){ return "HK$" + Number(n).toLocaleString("en-HK",{minimumFractionDigits:2,maximumFractionDigits:2}); }

function renderCh(){
  let h = "";
  for (const k in CH){
    const c = CH[k];
    h += '<div class="ch'+(k===curCh?' on':'')+'" data-ch="'+k+'">'
       + '<span style="font-size:17px">'+c.icon+'</span><span class="nm">'+c.name+'</span>'
       + '<span class="tag'+(c.auto?' auto':'')+'">'+(c.auto?'⚡自動':'人工確認')+'</span></div>';
  }
  document.getElementById("chs").innerHTML = h;
  document.querySelectorAll(".ch").forEach(function(e){
    e.onclick = function(){ curCh = e.getAttribute("data-ch"); renderCh(); renderAmt(); };
  });
}

function renderAmt(){
  let h = "";
  PRESETS.forEach(function(v){
    h += '<button class="'+(v===curHkd?'on':'')+'" data-v="'+v+'">'+hkdFmt(v)+'</button>';
  });
  document.getElementById("amts").innerHTML = h;
  document.querySelectorAll("#amts button").forEach(function(e){
    e.onclick = function(){ curHkd = Number(e.getAttribute("data-v")); document.getElementById("custom").value=""; renderAmt(); };
  });
}

document.getElementById("custom").addEventListener("input", function(e){
  const v = Number(e.target.value);
  if (v > 0){ curHkd = v; renderAmt(); }
});

function show(id, on){ document.getElementById(id).style.display = on ? "block" : "none"; }

function createOrder(){
  const btn = document.getElementById("mk"); btn.disabled = true;
  fetch(API + "/order", {method:"POST", headers:{"Content-Type":"application/json"},
    body: JSON.stringify({channel: curCh, hkd: curHkd})})
  .then(function(r){ return r.json(); })
  .then(function(j){
    btn.disabled = false;
    if (!j.ok){ alert("建立失敗：" + (j.error||"未知錯誤")); return; }
    curOrder = j.order; renderOrder();
  }).catch(function(e){ btn.disabled = false; alert("錯誤：" + e); });
}

function renderOrder(){
  const o = curOrder, c = CH[o.channel];
  let h = '<h2>訂單 ' + o.order_no + '</h2>';
  h += '<div class="kv"><div class="k">付款渠道</div><div>' + c.icon + " " + c.name + '</div></div>';
  h += '<div class="kv"><div class="k">應付金額</div><div class="v" style="font-size:17px">' + o.pay_amount + '</div></div>';
  if (o.pay_address){
    h += '<div class="kv"><div class="k">收款地址 / 帳號（請複製）</div><div class="v">' + o.pay_address + '</div></div>';
    h += '<button class="btn ghost" onclick="copyTxt(\\'' + o.pay_address + '\\')">複製地址</button> ';
  }
  h += '<div class="kv"><div class="k">備註 / 訂單號（轉帳時請填）</div><div class="v">' + o.pay_memo + '</div></div>';
  if (o.qr){ h += '<div class="kv"><div class="k">收款 QR 碼（掃碼付款）</div><img class="qr" src="' + o.qr + '" width="180" height="180"></div>'; }
  h += '<div class="kv"><div class="k">換算點數</div><div>' + Number(o.tokens).toLocaleString() + ' 點</div></div>';
  h += '<div class="kv"><div class="k">狀態</div><div id="ostat">' + o.status + '</div></div>';

  if (c.auto){
    h += '<div style="margin-top:14px"><input type="text" id="txh" placeholder="貼上交易編號 tx hash（0x...）"></div>';
    h += '<button class="btn" id="submitTx">我已付款，立即驗證入帳</button>';
  } else {
    h += '<div class="note">此渠道為人工確認：付款後請上傳收據截圖，管理員會盡快核對入帳。</div>';
    h += '<div style="margin-top:12px"><input type="file" id="rcpt" accept="image/*"></div>';
    h += '<button class="btn" id="submitRc">上傳收據</button>';
  }
  h += '<div class="note">此單有效至：' + new Date(o.expires_ts*1000).toLocaleString("zh-HK") + '</div>';
  document.getElementById("order").innerHTML = h;
  show("order", true);

  if (c.auto){
    document.getElementById("submitTx").onclick = function(){
      const tx = document.getElementById("txh").value.trim();
      if (!tx) { alert("請先貼上交易編號"); return; }
      this.disabled = true; const b = this;
      fetch(API + "/crypto/submit", {method:"POST", headers:{"Content-Type":"application/json"},
        body: JSON.stringify({order_no: curOrder.order_no, tx_hash: tx})})
      .then(function(r){ return r.json(); })
      .then(function(j){
        b.disabled = false;
        if (j.ok){ alert("✅ 入帳成功！+" + j.credited_tokens.toLocaleString() + " 點"); location.reload(); }
        else { alert("未通過：" + (j.error||"未知")); }
      }).catch(function(e){ b.disabled = false; alert("錯誤：" + e); });
    };
  } else {
    document.getElementById("submitRc").onclick = function(){
      const f = document.getElementById("rcpt").files[0];
      if (!f) { alert("請先選擇收據圖片"); return; }
      const fd = new FormData(); fd.append("order_no", curOrder.order_no); fd.append("file", f);
      const b = this; b.disabled = true;
      fetch(API + "/receipt", {method:"POST", body: fd})
      .then(function(r){ return r.json(); })
      .then(function(j){
        b.disabled = false;
        if (j.ok){ alert("✅ 收據已上傳，等待管理員確認入帳。"); location.reload(); }
        else { alert("上傳失敗：" + (j.error||"未知")); }
      }).catch(function(e){ b.disabled = false; alert("錯誤：" + e); });
    };
  }
  startPoll();
}

function copyTxt(t){
  if (navigator.clipboard) navigator.clipboard.writeText(t).then(function(){ }, function(){ });
  else { const i = document.createElement("textarea"); i.value = t; document.body.appendChild(i); i.select(); document.execCommand("copy"); i.remove(); }
  alert("已複製：" + t);
}

function startPoll(){
  if (timer) clearInterval(timer);
  timer = setInterval(function(){
    if (!curOrder) return;
    fetch(API + "/order/" + curOrder.order_no).then(function(r){ return r.json(); }).then(function(j){
      if (j.ok && j.order.status === "paid"){ clearInterval(timer); alert("✅ 已收到款項，點數已入帳！"); location.reload(); }
      const el = document.getElementById("ostat"); if (el && j.order) el.textContent = j.order.status;
    }).catch(function(){ });
  }, 5000);
}

renderCh(); renderAmt();
document.getElementById("mk").onclick = createOrder;
</script>
'''


def render_recharge(username, user, err=''):
    bal = int(user.get('balance_tokens') or 0)
    bal_cls = ' neg' if bal < 0 else ''
    h = '<h1>充值 / 升級</h1><div class="sub">付款完成後點數自動或經確認入帳。1 點 = HK$%s（%s）</div>' % (
        format(PRICE_PER_TOKEN_HKD, '.6f'), _hkd(1000000) + ' / 百萬點')
    h += '<div class="panel"><h2>目前餘額</h2><div class="bal%s">%s<small>%s</small></div></div>' % (
        bal_cls, format(bal, ','), _hkd(bal))
    if err:
        h += '<div class="err">' + err + '</div>'
    h += '<div class="panel"><h2>① 選付款方式</h2><div class="grid" id="chs"></div></div>'
    h += ('<div class="panel"><h2>② 選金額</h2><div class="amt" id="amts"></div>'
          '<div style="margin-top:10px"><input type="number" id="custom" min="1" '
          'placeholder="或自訂金額（HK$）"></div>'
          '<button class="btn" id="mk">③ 產生付款訂單</button>'
          '<div class="note">加密貨幣（USDC/BTC）為全自動：付款後貼上交易編號即秒級入帳。<br>'
          '香港支付寶 / 支付寶 / 微信錢包 / FPS / 信用卡為人工確認：付款後上傳收據，管理員核對後入帳。</div></div>')
    h += '<div class="panel" id="order" style="display:none"></div>'

    # 我的訂單
    with _connect() as conn:
        rows = conn.execute('SELECT * FROM orders WHERE username=? ORDER BY id DESC LIMIT 20',
                            (username,)).fetchall()
    if rows:
        h += '<div class="panel"><h2>我的訂單</h2><table><tr><th>訂單</th><th>渠道</th><th>金額</th><th>點數</th><th>狀態</th><th>時間</th></tr>'
        for r in rows:
            st = r['status']
            badge = {'pending': 'b-pending', 'paid': 'b-paid', 'review': 'b-review',
                     'expired': 'b-expired'}.get(st, 'b-pending')
            h += ('<tr><td style="font-family:ui-monospace">%s</td><td>%s</td><td>%s</td><td>%s</td>'
                  '<td><span class="badge %s">%s</span></td><td>%s</td></tr>') % (
                r['order_no'], CH_META.get(r['channel'], {}).get('name', r['channel']),
                hkd_fmt(r['amount_hkd']), format(r['tokens'], ','), badge, st,
                time.strftime('%m-%d %H:%M', time.localtime(r['created_ts'])))
        h += '</table></div>'

    js = (_RC_JS.replace('__API__', A_RC)
                .replace('__CH__', json.dumps(CH_META, ensure_ascii=False))
                .replace('__PRESETS__', json.dumps(PRESET_HKD)))
    return _shell('充值', h, R_RECHARGE) + js


def hkd_fmt(n):
    try:
        return 'HK$' + format(float(n), ',.2f')
    except Exception:
        return 'HK$0.00'


def render_ledger(username):
    with _connect() as conn:
        rows = conn.execute('SELECT * FROM ledger WHERE username=? ORDER BY id DESC LIMIT 100',
                            (username,)).fetchall()
        u = conn.execute('SELECT balance_tokens FROM users WHERE username=?', (username,)).fetchone()
    bal = int(u['balance_tokens']) if u else 0
    h = '<h1>我的帳本</h1><div class="sub">只增不改的入帳 / 扣費流水</div>'
    h += '<div class="panel"><h2>目前餘額</h2><div class="bal%s">%s<small>%s</small></div></div>' % (
        (' neg' if bal < 0 else ''), format(bal, ','), _hkd(bal))
    h += '<div class="panel"><h2>流水（最近 100 筆）</h2><table><tr><th>時間</th><th>原因</th><th>增減點數</th><th>餘額</th><th>訂單</th></tr>'
    if not rows:
        h += '<tr><td colspan="5" style="color:#8b93b5">暫無紀錄</td></tr>'
    for r in rows:
        d = int(r['delta_tokens'])
        col = '#69e0a4' if d > 0 else '#ff9b9b'
        h += '<tr><td>%s</td><td>%s</td><td style="color:%s">%s%s</td><td>%s</td><td style="font-family:ui-monospace">%s</td></tr>' % (
            time.strftime('%m-%d %H:%M', time.localtime(r['ts'])), r['reason'],
            col, ('+' if d > 0 else ''), format(d, ','), format(int(r['balance_after']), ','), r['order_no'] or '-')
    h += '</table></div>'
    return _shell('我的帳本', h, R_LEDGER)


_SET_KEYS = [('usdc_address', 'USDC 收款地址（Base 主網）'), ('eth_address', 'ETH 收款地址（EVM）'),
             ('btc_address', 'BTC 收款地址'), ('fps_id', '轉數快 FPS（電話/ID）'),
             ('hkd_per_usdc', '匯率：1 USDC = ? HK$'), ('hkd_per_btc', '匯率：1 BTC = ? HK$'),
             ('min_hkd', '最低充值 HK$'), ('order_ttl_min', '訂單有效期（分鐘）')]


def render_admin_orders(msg=''):
    tok = _csrf_token()
    with _connect() as conn:
        pend = conn.execute("SELECT * FROM orders WHERE status IN ('pending','review') "
                            "ORDER BY id DESC LIMIT 50").fetchall()
        paid = conn.execute("SELECT * FROM orders WHERE status='paid' ORDER BY id DESC LIMIT 20").fetchall()
        unmatched = conn.execute("SELECT * FROM chain_tx WHERE handled=0 ORDER BY seen_ts DESC LIMIT 30").fetchall()
        tot = conn.execute("SELECT COALESCE(SUM(delta_tokens),0) s, COUNT(*) c FROM ledger "
                           "WHERE reason='recharge_chain' OR reason LIKE 'recharge%'").fetchone()
        users = conn.execute("SELECT username FROM users ORDER BY username").fetchall()

    h = '<h1>金流後台</h1><div class="sub">訂單審核 · 未匹配鏈上入帳 · 收款資訊設定</div>'
    if msg:
        h += '<div class="ok">' + msg + '</div>'
    h += ('<div class="panel"><h2>總覽</h2><div class="grid">'
          '<div class="ch"><span class="nm">待處理訂單</span><span class="tag">%d</span></div>'
          '<div class="ch"><span class="nm">未匹配鏈上入帳</span><span class="tag">%d</span></div>'
          '<div class="ch"><span class="nm">累計已入帳點數</span><span class="tag">%s</span></div>'
          '</div></div>') % (len(pend), len(unmatched), format(int(tot['s']), ','))

    h += '<div class="panel"><h2>待處理訂單</h2><table><tr><th>訂單</th><th>用戶</th><th>渠道</th><th>金額</th><th>點數</th><th>狀態</th><th>收據</th><th>操作</th></tr>'
    if not pend:
        h += '<tr><td colspan="8" style="color:#8b93b5">暫無</td></tr>'
    for r in pend:
        proof = '<a href="%s/receipt/%s" target="_blank">看圖</a>' % (R_A_ORDERS, r['order_no']) if r['proof_path'] else '-'
        h += ('<tr><td style="font-family:ui-monospace">%s</td><td>%s</td><td>%s</td><td>%s</td>'
              '<td>%s</td><td><span class="badge b-%s">%s</span></td><td>%s</td><td>'
              '<form method="post" action="%s/confirm" style="display:inline">'
              '<input type="hidden" name="csrf" value="%s">'
              '<input type="hidden" name="order_no" value="%s">'
              '<input name="tokens" placeholder="點數(可留空)" style="width:110px;padding:5px 7px;font-size:12px">'
              '<button class="btn ghost" name="act" value="confirm" style="padding:5px 10px;font-size:12px">確認入帳</button>'
              '<button class="btn ghost" name="act" value="reject" style="padding:5px 10px;font-size:12px">作廢</button>'
              '</form></td></tr>') % (
            r['order_no'], r['username'], CH_META.get(r['channel'], {}).get('name', r['channel']),
            hkd_fmt(r['amount_hkd']), format(r['tokens'], ','), r['status'], r['status'], proof,
            R_A_ORDERS, tok, r['order_no'])
    h += '</table></div>'

    h += '<div class="panel"><h2>未匹配的鏈上入帳（收到錢但對不上訂單）</h2><table><tr><th>Tx</th><th>鏈</th><th>金額</th><th>時間</th><th>綁定帳號</th></tr>'
    if not unmatched:
        h += '<tr><td colspan="5" style="color:#8b93b5">暫無</td></tr>'
    for r in unmatched:
        opts = ''.join('<option>%s</option>' % u['username'] for u in users)
        h += ('<tr><td style="font-family:ui-monospace;font-size:11px">%s</td><td>%s</td><td>%s</td><td>%s</td>'
              '<td><form method="post" action="%s/bind" style="display:flex;gap:5px">'
              '<input type="hidden" name="csrf" value="%s"><input type="hidden" name="tx_hash" value="%s">'
              '<select name="username" style="background:#0f1522;color:#e8eefc;border:1px solid #27324a;border-radius:8px;padding:5px">%s</select>'
              '<input name="hkd" placeholder="HK$" style="width:80px;padding:5px 7px;font-size:12px">'
              '<button class="btn ghost" style="padding:5px 10px;font-size:12px">入帳</button>'
              '</form></td></tr>') % (
            r['tx_hash'][:22] + '…', r['chain'], r['amount'],
            time.strftime('%m-%d %H:%M', time.localtime(r['seen_ts'] or 0)), R_A_ORDERS, tok,
            r['tx_hash'], opts)
    h += '</table></div>'

    h += '<div class="panel"><h2>已入帳訂單（最近 20）</h2><table><tr><th>訂單</th><th>用戶</th><th>渠道</th><th>點數</th><th>Tx</th><th>時間</th></tr>'
    for r in paid:
        h += ('<tr><td style="font-family:ui-monospace">%s</td><td>%s</td><td>%s</td><td>%s</td>'
              '<td style="font-family:ui-monospace;font-size:11px">%s</td><td>%s</td></tr>') % (
            r['order_no'], r['username'], r['channel'], format(r['credited_tokens'] or 0, ','),
            (r['tx_hash'] or '-')[:20], time.strftime('%m-%d %H:%M', time.localtime(r['paid_ts'] or 0)))
    h += '</table></div>'

    h += '<div class="panel"><h2>收款資訊 / 匯率設定</h2><form method="post" action="%s/payconfig" enctype="multipart/form-data">' % R_ADMIN
    h += '<input type="hidden" name="csrf" value="%s">' % tok
    for k, label in _SET_KEYS:
        h += '<div class="kv"><div class="k">%s</div><input type="text" name="%s" value="%s"></div>' % (
            label, k, _sget(k).replace('"', '&quot;'))
    # QR 圖渠道：上傳收款 QR 圖（不是文字帳號）
    for _qk, _qlb in _QR_KEYS:
        _cur = _sget(_qk)
        if _cur and os.path.exists(_cur):
            _pv = '<img class="qr" src="%s/qr/%s?t=%d" width="140" height="140">' % (A_RC, _QR_CH_OF[_qk], int(time.time()))
        else:
            _pv = '<span style="color:#8b93b5">（未設定）</span>'
        h += ('<div class="kv"><div class="k">%s（上傳收款 QR 圖）</div>%s'
              '<input type="file" name="%s" accept="image/*" style="margin-top:6px">'
              '<div class="note" style="margin-top:4px">留空＝不變更；上傳新圖會覆蓋舊圖。</div></div>') % (_qlb, _pv, _qk)
    h += '<button class="btn" type="submit">儲存設定</button></form>'
    h += '<div class="note">香港支付寶／支付寶／微信錢包為 QR 圖收款；FPS 為文字帳號。未設定的渠道會在充值頁顯示為「未開通」。</div></div>'
    return _shell('金流後台', h, R_A_ORDERS)


# ================================================================ 路由
if app is not None:

    def _cur_user():
        return session.get('member_user') if session else None

    @app.route(R_RECHARGE, methods=['GET'])
    def pay_recharge():
        u = _cur_user()
        if not u:
            return redirect(R_LOGIN)
        try:
            expire_orders()
        except Exception:
            pass
        with _connect() as conn:
            row = conn.execute('SELECT * FROM users WHERE username=?', (u,)).fetchone()
        if row is None:
            return redirect(R_LOGIN)
        return render_recharge(u, dict(row))

    @app.route(R_LEDGER, methods=['GET'])
    def pay_ledger():
        u = _cur_user()
        if not u:
            return redirect(R_LOGIN)
        return render_ledger(u)

    @app.route(A_RC + '/order', methods=['POST'])
    def pay_api_order():
        u = _cur_user()
        if not u:
            return jsonify({'ok': False, 'error': '請先登入'})
        j = request.get_json(silent=True) or request.form
        order, err = create_order(u, j.get('channel'), j.get('hkd'), j.get('note') or '')
        if err:
            return jsonify({'ok': False, 'error': err})
        qr = ''
        if order['channel'] == 'usdc' and order['pay_address']:
            qr = 'https://api.qrserver.com/v1/create-qr-code/?size=150x150&data=ethereum:' + order['pay_address']
        elif order['channel'] == 'btc' and order['pay_address']:
            qr = 'https://api.qrserver.com/v1/create-qr-code/?size=150x150&data=bitcoin:' + order['pay_address']
        elif order['channel'] in QR_CH:
            qr = A_RC + '/qr/' + order['channel']
        order['qr'] = qr
        try:
            notify('🧾 新訂單已建立\n訂單：%s\n用戶：%s\n渠道：%s\n金額：HK$%s → %s 點' % (
                order['order_no'], order['username'], _ch_name(order['channel']),
                format(float(order['amount_hkd']), ',.2f'), format(int(order['tokens']), ',')))
        except Exception:
            pass
        return jsonify({'ok': True, 'order': order})

    @app.route(A_RC + '/order/<order_no>', methods=['GET'])
    def pay_api_order_get(order_no):
        u = _cur_user()
        if not u:
            return jsonify({'ok': False, 'error': '請先登入'})
        o = get_order(order_no)
        if not o or (o['username'] != u and not _is_admin()):
            return jsonify({'ok': False, 'error': '找不到訂單'})
        return jsonify({'ok': True, 'order': o})

    @app.route(A_RC + '/crypto/submit', methods=['POST'])
    def pay_api_crypto_submit():
        u = _cur_user()
        if not u:
            return jsonify({'ok': False, 'error': '請先登入'})
        j = request.get_json(silent=True) or request.form
        o = get_order((j.get('order_no') or '').strip())
        if not o:
            return jsonify({'ok': False, 'error': '找不到訂單'})
        if o['username'] != u:
            return jsonify({'ok': False, 'error': '這不是你的訂單'})
        tx = (j.get('tx_hash') or '').strip()
        ok, msg, info = apply_chain_payment(o, tx, source='user')
        if not ok:
            return jsonify({'ok': False, 'error': msg, 'verify': info})
        return jsonify({'ok': True, 'credited_tokens': info.get('credited_tokens'),
                        'balance': info.get('balance'), 'hkd': info.get('hkd')})

    @app.route(A_RC + '/receipt', methods=['POST'])
    def pay_api_receipt():
        u = _cur_user()
        if not u:
            return jsonify({'ok': False, 'error': '請先登入'})
        no = (request.form.get('order_no') or '').strip()
        o = get_order(no)
        if not o:
            return jsonify({'ok': False, 'error': '找不到訂單'})
        if o['username'] != u:
            return jsonify({'ok': False, 'error': '這不是你的訂單'})
        f = request.files.get('file')
        if f is None:
            return jsonify({'ok': False, 'error': '沒有收到檔案'})
        raw = f.read()
        if not raw or len(raw) > 8 * 1024 * 1024:
            return jsonify({'ok': False, 'error': '檔案為空或超過 8MB'})
        ext = '.png'
        fn = (f.filename or '').lower()
        for e in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
            if fn.endswith(e):
                ext = e
                break
        path = os.path.join(RECEIPT_DIR, no + ext)
        try:
            with open(path, 'wb') as fp:
                fp.write(raw)
        except Exception as e:
            return jsonify({'ok': False, 'error': '存檔失敗: %s' % e})
        set_order(no, proof_path=path, status='review', ai_check='pending')
        try:
            notify('📎 收到收據，待你確認入帳\n訂單：%s\n用戶：%s\n渠道：%s\n金額：HK$%s\n後台：/admin/orders' % (
                no, o['username'], _ch_name(o['channel']),
                format(float(o['amount_hkd']), ',.2f')))
        except Exception:
            pass
        return jsonify({'ok': True})

    # ---------------- 管理員
    @app.route(A_RC + '/qr/<ch>', methods=['GET'])
    def pay_api_qr(ch):
        u = _cur_user()
        if not u:
            return ('', 403)
        if ch not in QR_CH:
            return ('', 404)
        _p = _sget(QR_SETTING_KEY[ch])
        if not _p or not os.path.exists(_p):
            return ('', 404)
        return send_file(_p)

    @app.route(R_A_ORDERS, methods=['GET'])
    def pay_admin_orders():
        u = _cur_user()
        if not u:
            return redirect(R_LOGIN)
        if not _is_admin():
            return _shell('無權限', '<div class="err">⛔ 您不是管理員。</div>')
        return render_admin_orders()

    @app.route(R_A_ORDERS + '/confirm', methods=['POST'])
    def pay_admin_confirm():
        u = _cur_user()
        if not u or not _is_admin():
            return _shell('無權限', '<div class="err">⛔ 無權限</div>')
        if not _check_csrf():
            return _shell('CSRF 失敗', '<div class="err">⛔ CSRF token 不正確，請回上一頁重試。</div>')
        no = (request.form.get('order_no') or '').strip()
        act = (request.form.get('act') or 'confirm').strip()
        o = get_order(no)
        if not o:
            return render_admin_orders('⚠️ 找不到訂單 ' + no)
        if act == 'reject':
            set_order(no, status='expired', note=(o.get('note') or '') + ' | rejected by ' + u)
            _audit('pay_reject', no)
            return render_admin_orders('已作廢 ' + no)
        if o['status'] == 'paid':
            return render_admin_orders('⚠️ ' + no + ' 已經入帳過了')
        try:
            tk = int(request.form.get('tokens') or 0)
        except Exception:
            tk = 0
        if tk <= 0:
            tk = int(o['tokens'])
        ok, msg, bal = credit_tokens(o['username'], tk, 'recharge_manual', order_no=no,
                                     idem_key='order:%s' % no,
                                     meta=json.dumps({'by': u, 'channel': o['channel']}, ensure_ascii=False))
        if not ok:
            return render_admin_orders('⚠️ 入帳失敗：' + msg)
        set_order(no, status='paid', paid_ts=time.time(),
                  credited_tokens=int(o['credited_tokens'] or 0) + tk)
        _audit('pay_confirm', '%s +%d' % (no, tk))
        return render_admin_orders('✅ 已入帳 %s：+%s 點（%s 餘額 %s）' % (no, format(tk, ','), o['username'], format(bal, ',')))

    @app.route(R_A_ORDERS + '/bind', methods=['POST'])
    def pay_admin_bind():
        u = _cur_user()
        if not u or not _is_admin():
            return _shell('無權限', '<div class="err">⛔ 無權限</div>')
        if not _check_csrf():
            return _shell('CSRF 失敗', '<div class="err">⛔ CSRF token 不正確。</div>')
        tx = (request.form.get('tx_hash') or '').strip()
        target = (request.form.get('username') or '').strip()
        hkd = request.form.get('hkd') or '0'
        try:
            hkd = float(hkd)
        except Exception:
            hkd = 0.0
        if not tx or not target or hkd <= 0:
            return render_admin_orders('⚠️ 請填齊帳號與 HK$ 金額')
        tk = hkd_to_tokens(hkd)
        ok, msg, bal = credit_tokens(target, tk, 'recharge_manual', order_no='',
                                     idem_key='crypto:%s' % tx.lower(),
                                     meta=json.dumps({'by': u, 'tx': tx, 'bound': True}, ensure_ascii=False))
        if not ok:
            return render_admin_orders('⚠️ 入帳失敗：' + msg)
        try:
            with _db_lock, _connect() as conn:
                conn.execute('UPDATE chain_tx SET handled=1, order_no=?, note=? WHERE tx_hash=?',
                             ('manual:' + target, 'by ' + u, tx.lower()))
                conn.commit()
        except Exception:
            pass
        _audit('pay_bind', '%s -> %s +%d' % (tx[:18], target, tk))
        return render_admin_orders('✅ 已把 %s 綁定 %s：+%s 點' % (tx[:18], target, format(tk, ',')))

    @app.route(R_ADMIN + '/payconfig', methods=['POST'])
    def pay_admin_config():
        u = _cur_user()
        if not u or not _is_admin():
            return _shell('無權限', '<div class="err">⛔ 無權限</div>')
        if not _check_csrf():
            return _shell('CSRF 失敗', '<div class="err">⛔ CSRF token 不正確。</div>')
        for k, _label in _SET_KEYS:
            if k in request.form:
                _sset(k, (request.form.get(k) or '').strip())
        for _qk, _qlb in _QR_KEYS:
            _f = request.files.get(_qk)
            if _f and (_f.filename or '').strip():
                _raw = _f.read()
                if _raw and len(_raw) <= 8 * 1024 * 1024:
                    _ext = '.png'
                    _fn = (_f.filename or '').lower()
                    for _e in ('.jpg', '.jpeg', '.png', '.webp', '.gif'):
                        if _fn.endswith(_e):
                            _ext = _e
                            break
                    _path = os.path.join(PAYQR_DIR, _qk + _ext)
                    try:
                        with open(_path, 'wb') as _fp:
                            _fp.write(_raw)
                        _sset(_qk, _path)
                    except Exception as _e2:
                        print('[金流] QR 存檔失敗: %s' % _e2)
        _audit('payconfig', 'updated')
        return render_admin_orders('✅ 收款資訊已更新')

    @app.route(R_A_ORDERS + '/receipt/<order_no>', methods=['GET'])
    def pay_admin_receipt(order_no):
        u = _cur_user()
        if not u or not _is_admin():
            return _shell('無權限', '<div class="err">⛔ 無權限</div>')
        o = get_order(order_no)
        if not o or not o.get('proof_path') or not os.path.exists(o['proof_path']):
            return _shell('找不到收據', '<div class="err">找不到收據圖</div>')
        return send_file(o['proof_path'])

    print('[金流] 路由已註冊 | %s  %s  %s  %s' % (R_RECHARGE, R_LEDGER, A_RC, R_A_ORDERS))


# ---------------------------------------------------------------- 初始化
try:
    _init_db()
except Exception as _e:
    print('[金流] init_db 失敗: %s' % _e)
    traceback.print_exc()
