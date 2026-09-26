# -*- coding: utf-8 -*-
"""
會員認證強化補丁 P0（2026-09-22）作者：凜
==========================================
載入方式：由 mok_web/保丁.py 掃描器自動載入（檔名固定為 保丁.py）。
實作方式：新增 /security 與 /api/auth/* 路由，並以同端點視圖函數換裝
          的方式升級 /login（Flask app.view_functions 對應）。
核心 mok_web.py 不動，會員系統補丁原檔不動。

P0 範圍：
 1. 密碼雜湊 sha256('mok_member_v1'+pw) 升級為 bcrypt(12)，舊雜湊於登入成功時自動升級
 2. TOTP 兩步驗證（RFC 6238，標準庫自實作，不依賴 pyotp）
 3. Telegram OTP 登入（使用 env.env 的 MOK_TG_TOKEN 發送）
 4. identities 身分表（telegram / email / phone / passkey 欄位預留）
 5. 登入失敗限速（15 分內 5 次即暫時拒登）+ 認證審計
"""
import os
import sys
import time
import hmac
import json
import base64
import struct
import sqlite3
import hashlib
import secrets
from pathlib import Path

try:
    import bcrypt
except Exception:
    bcrypt = None

try:
    import requests
except Exception:
    requests = None

try:
    import qrcode
    import qrcode.image.svg as _qr_svg
except Exception:
    qrcode = None

try:
    from flask import request, session, redirect, jsonify
except Exception:
    request = session = redirect = jsonify = None

MOK_ROOT = Path('/home/ubuntu/.mok')
ENV_FILE = MOK_ROOT / 'env.env'
LEGACY_SALT = 'mok_member_v1'
TOTP_ISSUER = 'MOKAGI'
MAX_FAILS = 5
FAIL_WINDOW = 900
LOCK_SECONDS = 900
OTP_TTL = 300
OTP_MAX_TRIES = 5


def _find_member_db():
    cands = sorted((MOK_ROOT / 'frontends' / 'mok_web').glob('會員系統_*/member.db'))
    return str(cands[0]) if cands else None


MEMBER_DB = _find_member_db()


def _conn():
    conn = sqlite3.connect(MEMBER_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    if not MEMBER_DB:
        print('⚠️ 會員認證P0：未找到 member.db，補丁停用')
        return False
    with _conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS identities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            verified INTEGER DEFAULT 0,
            created_ts REAL,
            meta TEXT,
            UNIQUE(kind, value)
        );
        CREATE INDEX IF NOT EXISTS idx_identities_user ON identities(username);

        CREATE TABLE IF NOT EXISTS totp_secrets (
            username TEXT PRIMARY KEY,
            secret TEXT NOT NULL,
            enabled INTEGER DEFAULT 0,
            created_ts REAL,
            enabled_ts REAL
        );

        CREATE TABLE IF NOT EXISTS backup_codes (
            username TEXT NOT NULL,
            code_hash TEXT NOT NULL,
            used INTEGER DEFAULT 0,
            created_ts REAL
        );
        CREATE INDEX IF NOT EXISTS idx_backup_user ON backup_codes(username);

        CREATE TABLE IF NOT EXISTS login_otp (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            purpose TEXT NOT NULL,
            code_hash TEXT NOT NULL,
            expires REAL,
            tries INTEGER DEFAULT 0,
            created_ts REAL
        );
        CREATE INDEX IF NOT EXISTS idx_otp_user ON login_otp(username, purpose);

        CREATE TABLE IF NOT EXISTS auth_audit (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            ts REAL,
            username TEXT,
            action TEXT,
            detail TEXT,
            ip TEXT
        );

        CREATE TABLE IF NOT EXISTS login_fail (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT,
            ip TEXT,
            ts REAL
        );
        """)
        conn.commit()
    return True


DB_READY = _init_db()


def _env(key, default=None):
    try:
        for line in ENV_FILE.read_text(encoding='utf-8', errors='ignore').splitlines():
            line = line.strip()
            if line.startswith(key + '='):
                return line.split('=', 1)[1].strip().strip('"').strip("'")
    except Exception:
        pass
    return default


def tg_send(chat_id, text):
    token = _env('MOK_TG_TOKEN')
    if not (token and chat_id and requests):
        return False
    try:
        r = requests.post(
            'https://api.telegram.org/bot%s/sendMessage' % token,
            json={'chat_id': str(chat_id), 'text': text},
            timeout=10,
        )
        return r.status_code == 200
    except Exception as e:
        print('⚠️ 會員認證P0 TG 發送失敗:', e)
        return False


def _client_ip():
    try:
        if request is None:
            return None
        return request.headers.get('X-Forwarded-For', request.remote_addr)
    except Exception:
        return None


def audit(username, action, detail=''):
    try:
        with _conn() as conn:
            conn.execute(
                'INSERT INTO auth_audit (ts, username, action, detail, ip) VALUES (?,?,?,?,?)',
                (time.time(), username, action, str(detail)[:500], _client_ip()),
            )
            conn.commit()
    except Exception as e:
        print('⚠️ 會員認證P0 審計寫入失敗:', e)


def recent_audit(limit=12):
    try:
        with _conn() as conn:
            rows = conn.execute(
                'SELECT * FROM auth_audit ORDER BY id DESC LIMIT ?', (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def record_fail(username):
    try:
        with _conn() as conn:
            conn.execute(
                'INSERT INTO login_fail (username, ip, ts) VALUES (?,?,?)',
                (username, _client_ip(), time.time()),
            )
            conn.commit()
    except Exception:
        pass


def is_locked(username):
    if not username:
        return False
    try:
        with _conn() as conn:
            row = conn.execute(
                'SELECT COUNT(*) AS c, MAX(ts) AS last FROM login_fail WHERE username=? AND ts > ?',
                (username, time.time() - FAIL_WINDOW),
            ).fetchone()
            if (row['c'] or 0) >= MAX_FAILS and (time.time() - (row['last'] or 0)) < LOCK_SECONDS:
                return True
    except Exception:
        pass
    return False


def clear_fails(username):
    try:
        with _conn() as conn:
            conn.execute('DELETE FROM login_fail WHERE username=?', (username,))
            conn.commit()
    except Exception:
        pass


def _sha(txt):
    return hashlib.sha256(str(txt).encode('utf-8')).hexdigest()


def legacy_hash(pw):
    return hashlib.sha256((LEGACY_SALT + pw).encode()).hexdigest()


def hash_pw(pw):
    if bcrypt:
        return bcrypt.hashpw(pw.encode('utf-8'), bcrypt.gensalt(rounds=12)).decode()
    return legacy_hash(pw)


def verify_pw(pw, stored):
    """回傳 (是否正確, 是否為舊格式需升級)"""
    if not stored:
        return False, False
    if stored.startswith('$2') and bcrypt:
        try:
            return bcrypt.checkpw(pw.encode('utf-8'), stored.encode()), False
        except Exception:
            return False, False
    if hmac.compare_digest(stored, legacy_hash(pw)):
        return True, True
    return False, False


def _b32pad(s):
    s = str(s).upper().replace(' ', '').strip('=')
    return s + '=' * ((8 - len(s) % 8) % 8)


def totp_at(secret, ts, digits=6, step=30):
    key = base64.b32decode(_b32pad(secret))
    msg = struct.pack('>Q', int(ts // step))
    h = hmac.new(key, msg, hashlib.sha1).digest()
    off = h[-1] & 0x0F
    code = (struct.unpack('>I', h[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def totp_new_secret():
    return base64.b32encode(secrets.token_bytes(20)).decode().rstrip('=')


def totp_verify(secret, code, window=1):
    if not (secret and code):
        return False
    code = str(code).strip().replace(' ', '')
    now = time.time()
    for i in range(-window, window + 1):
        if hmac.compare_digest(totp_at(secret, now + i * 30), code):
            return True
    return False


def otpauth_url(username, secret):
    from urllib.parse import quote
    label = quote('%s:%s' % (TOTP_ISSUER, username))
    return ('otpauth://totp/%s?secret=%s&issuer=%s&algorithm=SHA1&digits=6&period=30'
            % (label, secret, TOTP_ISSUER))


def qr_data_uri(data):
    if not qrcode:
        return None
    try:
        import io
        img = qrcode.make(data, image_factory=_qr_svg.SvgPathImage, box_size=6)
        buf = io.BytesIO()
        img.save(buf)
        return 'data:image/svg+xml;base64,' + base64.b64encode(buf.getvalue()).decode()
    except Exception as e:
        print('⚠️ 會員認證P0 QR 產生失敗:', e)
        return None


# ---------------- 會員與身分 ----------------


def get_user(username):
    try:
        with _conn() as conn:
            row = conn.execute('SELECT * FROM users WHERE username=?', (username,)).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


def set_password_hash(username, pw_hash):
    with _conn() as conn:
        conn.execute('UPDATE users SET password_hash=? WHERE username=?', (pw_hash, username))
        conn.commit()


def totp_state(username):
    try:
        with _conn() as conn:
            row = conn.execute('SELECT * FROM totp_secrets WHERE username=?', (username,)).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


def totp_start_setup(username):
    secret = totp_new_secret()
    with _conn() as conn:
        conn.execute(
            'INSERT INTO totp_secrets (username, secret, enabled, created_ts) VALUES (?,?,0,?) '
            'ON CONFLICT(username) DO UPDATE SET secret=excluded.secret, enabled=0, created_ts=excluded.created_ts',
            (username, secret, time.time()),
        )
        conn.commit()
    audit(username, 'totp_setup_start', '')
    return secret


def totp_enable(username):
    codes = []
    with _conn() as conn:
        conn.execute('UPDATE totp_secrets SET enabled=1, enabled_ts=? WHERE username=?',
                     (time.time(), username))
        conn.execute('DELETE FROM backup_codes WHERE username=?', (username,))
        for _ in range(10):
            raw = '%s-%s' % (secrets.token_hex(2).upper(), secrets.token_hex(2).upper())
            codes.append(raw)
            conn.execute('INSERT INTO backup_codes (username, code_hash, used, created_ts) VALUES (?,?,0,?)',
                         (username, _sha(raw), time.time()))
        conn.commit()
    audit(username, 'totp_enabled', '')
    return codes


def totp_disable(username):
    with _conn() as conn:
        conn.execute('DELETE FROM totp_secrets WHERE username=?', (username,))
        conn.execute('DELETE FROM backup_codes WHERE username=?', (username,))
        conn.commit()
    audit(username, 'totp_disabled', '')


def use_backup_code(username, code):
    if not code:
        return False
    h = _sha(str(code).strip().upper())
    with _conn() as conn:
        row = conn.execute('SELECT rowid FROM backup_codes WHERE username=? AND code_hash=? AND used=0',
                           (username, h)).fetchone()
        if not row:
            return False
        conn.execute('UPDATE backup_codes SET used=1 WHERE rowid=?', (row['rowid'],))
        conn.commit()
    audit(username, 'backup_code_used', '')
    return True


def check_second_factor(username, code):
    t = totp_state(username)
    if t and t.get('enabled') and totp_verify(t['secret'], code):
        return True
    return use_backup_code(username, code)


def get_identity(username, kind):
    try:
        with _conn() as conn:
            row = conn.execute(
                'SELECT * FROM identities WHERE username=? AND kind=? ORDER BY id DESC LIMIT 1',
                (username, kind)).fetchone()
            return dict(row) if row else None
    except Exception:
        return None


def bind_identity(username, kind, value, verified=0, meta=None):
    with _conn() as conn:
        conn.execute(
            'INSERT OR REPLACE INTO identities (username, kind, value, verified, created_ts, meta) '
            'VALUES (?,?,?,?,?,?)',
            (username, kind, str(value), verified, time.time(), json.dumps(meta or {}, ensure_ascii=False)),
        )
        conn.commit()


def find_identity_user(kind, value):
    try:
        with _conn() as conn:
            row = conn.execute('SELECT username FROM identities WHERE kind=? AND value=? AND verified=1',
                               (kind, str(value))).fetchone()
            return row['username'] if row else None
    except Exception:
        return None


def issue_otp(username, purpose='login', ttl=OTP_TTL):
    code = ''.join(secrets.choice('0123456789') for _ in range(6))
    with _conn() as conn:
        conn.execute('DELETE FROM login_otp WHERE username=? AND purpose=?', (username, purpose))
        conn.execute(
            'INSERT INTO login_otp (username, purpose, code_hash, expires, tries, created_ts) '
            'VALUES (?,?,?,?,0,?)',
            (username, purpose, _sha(code), time.time() + ttl, time.time()),
        )
        conn.commit()
    return code


def verify_otp(username, code, purpose='login'):
    with _conn() as conn:
        row = conn.execute(
            'SELECT * FROM login_otp WHERE username=? AND purpose=? ORDER BY id DESC LIMIT 1',
            (username, purpose)).fetchone()
        if not row:
            return False
        if row['expires'] < time.time() or row['tries'] >= OTP_MAX_TRIES:
            conn.execute('DELETE FROM login_otp WHERE id=?', (row['id'],))
            conn.commit()
            return False
        conn.execute('UPDATE login_otp SET tries=tries+1 WHERE id=?', (row['id'],))
        conn.commit()
        ok = hmac.compare_digest(row['code_hash'], _sha(str(code).strip()))
        if ok:
            conn.execute('DELETE FROM login_otp WHERE id=?', (row['id'],))
            conn.commit()
        return ok


# ---------------- 頁面 ----------------


PAGE_CSS = """
<style>
 body{font-family:-apple-system,'PingFang TC','Microsoft JhengHei',sans-serif;background:#0f1220;color:#e8e8f0;
      margin:0;padding:40px 16px;display:flex;justify-content:center}
 .card{background:#1a1f33;border:1px solid #2c3560;border-radius:16px;padding:32px 36px;width:520px;
       box-shadow:0 10px 40px rgba(0,0,0,.5)}
 h1{font-size:22px;margin:0 0 6px;background:linear-gradient(90deg,#7aa2ff,#c084fc);-webkit-background-clip:text;
    -webkit-text-fill-color:transparent}
 p.sub{color:#8b93b5;font-size:13px;margin:0 0 18px}
 label{display:block;font-size:13px;color:#aab2d6;margin:14px 0 6px}
 input{width:100%;box-sizing:border-box;padding:11px 14px;border-radius:10px;border:1px solid #2c3560;
       background:#12162a;color:#e8e8f0;font-size:14px;outline:none}
 input:focus{border-color:#7aa2ff}
 button{margin-top:18px;width:100%;padding:12px;border:0;border-radius:10px;cursor:pointer;font-size:15px;
        background:linear-gradient(90deg,#7aa2ff,#c084fc);color:#0f1220;font-weight:700}
 .err{background:#3a1620;border:1px solid #7a2436;color:#ffb4c0;padding:10px 12px;border-radius:10px;font-size:13px}
 .ok{background:#102a1e;border:1px solid #245c3d;color:#a8f0c6;padding:10px 12px;border-radius:10px;font-size:13px}
 .sec{border-top:1px solid #2c3560;margin-top:22px;padding-top:16px}
 .sec h2{font-size:15px;margin:0 0 8px;color:#cfd6f5}
 .tag{display:inline-block;font-size:12px;padding:2px 8px;border-radius:999px;border:1px solid #2c3560;color:#9fb0e8}
 .mono{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:#9fb0e8;word-break:break-all}
 table{width:100%;border-collapse:collapse;font-size:12px;color:#aab2d6}
 td{padding:4px 0;border-bottom:1px solid #232a49}
 a{color:#7aa2ff}
</style>
"""


def _page(title, inner):
    return ('<!doctype html><html><head><meta charset="utf-8"><title>%s</title>%s</head><body>'
            '<div class="card">%s</div></body></html>' % (title, PAGE_CSS, inner))


def _login_page(err=None, need2fa=False, ok=None):
    if need2fa:
        inner = ("<h1>兩步驗證</h1><p class='sub'>請輸入驗證器 App 的 6 位數驗證碼，或使用備用碼。</p>"
                 + ("<div class='err'>%s</div>" % err if err else "")
                 + "<form method='post'><input type='hidden' name='step' value='totp'>"
                   "<label>驗證碼</label><input name='totp' inputmode='numeric' autocomplete='one-time-code' required>"
                   "<label>或備用碼</label><input name='backup' placeholder='XXXX-XXXX'>"
                   "<button type='submit'>驗證</button></form>")
        return _page('兩步驗證', inner)
    inner = ("<h1>會員登入</h1><p class='sub'>登入後即可使用 AI 服務</p>"
             + ("<div class='err'>%s</div>" % err if err else "")
             + ("<div class='ok'>%s</div>" % ok if ok else "")
             + "<form method='post'><label>帳號</label>"
               "<input name='username' required autocomplete='username'>"
               "<label>密碼</label><input name='password' type='password' required autocomplete='current-password'>"
               "<button type='submit'>登入</button></form>"
               "<p class='sub' style='margin-top:14px'>"
               "以 Telegram 驗證碼登入：<span class='mono'>POST /api/auth/otp/request {\"username\":\"...\"}</span></p>")
    return _page('會員登入', inner)


def _finish_login(username, note=''):
    if session is None:
        return
    session['member_user'] = username
    session.permanent = True
    session.pop('pending_2fa', None)
    try:
        with _conn() as conn:
            conn.execute('UPDATE users SET last_login=? WHERE username=?', (time.time(), username))
            conn.commit()
    except Exception:
        pass
    audit(username, 'login_ok', note)


def member_login_v2():
    """升級版 /login：bcrypt（舊雜湊自動升級）+ 限速 + TOTP 兩步"""
    err = None
    if request.method == 'POST':
        step = (request.form.get('step') or '').strip()
        if step == 'totp':
            pend = session.get('pending_2fa') or {}
            u = pend.get('u')
            if not u or (time.time() - float(pend.get('ts') or 0)) > 300:
                session.pop('pending_2fa', None)
                return _login_page('兩步驗證逾時，請重新登入')
            code = (request.form.get('totp') or '').strip()
            backup = (request.form.get('backup') or '').strip()
            if check_second_factor(u, code) or (backup and use_backup_code(u, backup)):
                _finish_login(u, 'totp')
                return redirect('/member')
            record_fail(u)
            audit(u, 'totp_fail', '')
            session['pending_2fa'] = {'u': u, 'ts': time.time()}
            return _login_page('驗證碼錯誤', need2fa=True)

        username = (request.form.get('username') or '').strip()
        pw = request.form.get('password') or ''
        if is_locked(username):
            audit(username, 'login_blocked', 'too many failures')
            return _login_page('嘗試次數過多，請 15 分鐘後再試')
        user = get_user(username)
        ok, need_up = verify_pw(pw, user.get('password_hash') if user else None)
        if not ok:
            record_fail(username)
            audit(username, 'login_fail', 'bad password')
            return _login_page('帳號或密碼錯誤')
        clear_fails(username)
        if need_up:
            set_password_hash(username, hash_pw(pw))
            audit(username, 'pw_upgrade', 'legacy sha256 -> bcrypt')
        t = totp_state(username)
        if t and t.get('enabled'):
            session['pending_2fa'] = {'u': username, 'ts': time.time()}
            audit(username, 'pw_ok_2fa_required', '')
            return _login_page(None, need2fa=True)
        _finish_login(username, 'password')
        return redirect('/member')
    return _login_page()


def _current_user():
    if session is None:
        return None
    return session.get('member_user')


def security_page():
    u = _current_user()
    if not u:
        return redirect('/login')
    user = get_user(u) or {}
    ph = user.get('password_hash') or ''
    kind = {'$2': 'bcrypt(12)', 'x': 'sha256 舊格式'}.get((ph[:2] if ph else 'x'), 'sha256 舊格式')
    if ph.startswith('$2'):
        kind = 'bcrypt(12)'
    t = totp_state(u)
    tg = get_identity(u, 'telegram')
    email = get_identity(u, 'email')
    phone = get_identity(u, 'phone')
    try:
        with _conn() as conn:
            used = conn.execute('SELECT COUNT(*) c FROM backup_codes WHERE username=? AND used=0', (u,)).fetchone()['c']
    except Exception:
        used = 0

    def _st(v):
        return "<span class='tag'>已綁定 %s</span>" % v if v else "<span class='tag'>未綁定</span>"

    parts = ["<h1>安全中心</h1><p class='sub'>%s · 密碼雜湊：%s</p>" % (u, kind)]
    q = request.args.get('msg')
    if q:
        parts.append("<div class='ok'>%s</div>" % q)

    # TOTP
    parts.append("<div class='sec'><h2>TOTP 兩步驗證</h2>")
    if t and t.get('enabled'):
        parts.append("<p class='sub'>狀態：<b>已啟用</b> · 未使用備用碼 %d 組</p>" % used)
        parts.append("<form method='post' action='/security/totp/disable'>"
                     "<label>輸入密碼以停用</label><input name='password' type='password' required>"
                     "<button type='submit'>停用 TOTP</button></form>")
    else:
        secret = (t or {}).get('secret')
        if secret:
            uri = otpauth_url(u, secret)
            qr = qr_data_uri(uri)
            parts.append("<p class='sub'>用驗證器 App 掃描，或手動輸入密鑰；輸入 6 位碼後啟用。</p>")
            if qr:
                parts.append("<img src='%s' style='width:190px;background:#fff;border-radius:10px;padding:8px'>" % qr)
            parts.append("<p class='mono'>密鑰：%s</p>" % secret)
            parts.append("<form method='post' action='/security/totp/enable'>"
                         "<label>驗證器 6 位碼</label><input name='code' inputmode='numeric' required>"
                         "<button type='submit'>啟用 TOTP</button></form>")
        parts.append("<form method='post' action='/security/totp/setup'><button type='submit'>產生 TOTP 密鑰</button></form>")
    parts.append("</div>")

    # Telegram
    parts.append("<div class='sec'><h2>Telegram OTP 登入</h2><p class='sub'>%s %s</p>" % (_st(tg), (('Chat ID: %s' % tg['value']) if tg else '')))
    parts.append("<form method='post' action='/security/bind_telegram'>"
                 "<label>Telegram Chat ID</label><input name='chat_id' placeholder='例如 123456789' required>"
                 "<button type='submit'>發送驗證碼</button></form>")
    parts.append("<form method='post' action='/security/bind_telegram_verify'>"
                 "<label>Chat ID</label><input name='chat_id' required>"
                 "<label>收到的 6 位碼</label><input name='code' inputmode='numeric' required>"
                 "<button type='submit'>確認綁定</button></form></div>")

    # email / phone
    parts.append("<div class='sec'><h2>Email / 手機號</h2><p class='sub'>Email：%s　手機：%s（P2 階段補上驗證碼確認）</p>"
                 % (_st(email), _st(phone)))
    parts.append("<form method='post' action='/security/bind_contact'>"
                 "<label>類型</label><input name='kind' placeholder='email 或 phone' required>"
                 "<label>值</label><input name='value' required>"
                 "<button type='submit'>登記</button></form></div>")

    # 改密碼
    parts.append("<div class='sec'><h2>變更密碼</h2>"
                 "<form method='post' action='/security/password'>"
                 "<label>目前密碼</label><input name='old' type='password' required>"
                 "<label>新密碼（至少 8 碼）</label><input name='new' type='password' required>"
                 "<button type='submit'>更新密碼</button></form></div>")

    # 審計
    rows = recent_audit(12)
    if rows:
        tr = ''.join("<tr><td>%s</td><td>%s</td><td>%s</td></tr>"
                     % (time.strftime('%m-%d %H:%M', time.localtime(r['ts'])), r['action'], (r['detail'] or '')[:40])
                     for r in rows)
        parts.append("<div class='sec'><h2>最近認證紀錄</h2><table>%s</table></div>" % tr)

    parts.append("<div class='sec'><p class='sub'><a href='/member'>回會員中心</a> · <a href='/logout'>登出</a></p></div>")
    return _page('安全中心', ''.join(parts))


def _back(msg):
    from urllib.parse import quote
    return redirect('/security?msg=' + quote(msg))


def _guard():
    u = _current_user()
    if not u:
        return None, redirect('/login')
    return u, None


def security_totp_setup():
    u, r = _guard()
    if r:
        return r
    totp_start_setup(u)
    return _back('已產生 TOTP 密鑰，請掃描 QR 後輸入 6 位碼啟用')


def security_totp_enable():
    u, r = _guard()
    if r:
        return r
    code = (request.form.get('code') or '').strip()
    t = totp_state(u)
    if not (t and totp_verify(t['secret'], code)):
        audit(u, 'totp_enable_fail', '')
        return _back('驗證碼不正確，請確認 App 時間同步後重試')
    codes = totp_enable(u)
    html = ("<h1>TOTP 已啟用</h1><p class='sub'>以下備用碼只顯示這一次，請務必抄下來（每組只能用一次）。</p>"
            "<p class='mono'>%s</p><div class='sec'><p class='sub'><a href='/security'>回安全中心</a></p></div>"
            % '<br>'.join(codes))
    return _page('TOTP 已啟用', html)


def security_totp_disable():
    u, r = _guard()
    if r:
        return r
    pw = request.form.get('password') or ''
    user = get_user(u) or {}
    ok, _up = verify_pw(pw, user.get('password_hash'))
    if not ok:
        return _back('密碼不正確，未停用 TOTP')
    totp_disable(u)
    return _back('已停用 TOTP 兩步驗證')


def security_password():
    u, r = _guard()
    if r:
        return r
    old = request.form.get('old') or ''
    new = request.form.get('new') or ''
    user = get_user(u) or {}
    ok, _up = verify_pw(old, user.get('password_hash'))
    if not ok:
        audit(u, 'pw_change_fail', 'bad old password')
        return _back('目前密碼不正確')
    if len(new) < 8:
        return _back('新密碼至少 8 碼')
    set_password_hash(u, hash_pw(new))
    audit(u, 'pw_changed', 'bcrypt')
    return _back('密碼已更新（bcrypt）')


def security_bind_telegram():
    u, r = _guard()
    if r:
        return r
    chat_id = (request.form.get('chat_id') or '').strip()
    if not chat_id:
        return _back('請填 Telegram Chat ID')
    code = issue_otp(u, 'bind_tg', ttl=600)
    sent = tg_send(chat_id, '[MOKAGI] 綁定驗證碼：%s（10 分鐘內有效）' % code)
    audit(u, 'tg_bind_send', 'chat_id=%s sent=%s' % (chat_id, sent))
    if not sent:
        return _back('Telegram 發送失敗，請確認 Chat ID 是否已與 Bot 對話過')
    return _back('驗證碼已發送到 Telegram，請回填完成綁定')


def security_bind_telegram_verify():
    u, r = _guard()
    if r:
        return r
    chat_id = (request.form.get('chat_id') or '').strip()
    code = (request.form.get('code') or '').strip()
    if not verify_otp(u, code, 'bind_tg'):
        return _back('驗證碼錯誤或已過期')
    bind_identity(u, 'telegram', chat_id, verified=1)
    audit(u, 'tg_bind_ok', 'chat_id=%s' % chat_id)
    return _back('Telegram 綁定完成，之後可免密碼用 OTP 登入')


def security_bind_contact():
    u, r = _guard()
    if r:
        return r
    kind = (request.form.get('kind') or '').strip().lower()
    value = (request.form.get('value') or '').strip()
    if kind not in ('email', 'phone'):
        return _back('類型只能是 email 或 phone')
    if not value:
        return _back('請填值')
    bind_identity(u, kind, value, verified=0)
    audit(u, 'contact_registered', '%s=%s' % (kind, value))
    return _back('%s 已登記（待 P2 驗證碼確認）' % kind)


def api_otp_request():
    data = request.get_json(silent=True) or request.form or {}
    username = (data.get('username') or '').strip()
    idt = get_identity(username, 'telegram') if username else None
    if not (idt and idt.get('verified')):
        audit(username, 'otp_request_denied', 'no verified telegram')
        return jsonify({'ok': False, 'error': '該帳號未綁定 Telegram'}), 400
    if is_locked(username):
        return jsonify({'ok': False, 'error': '嘗試次數過多，請稍後再試'}), 429
    code = issue_otp(username, 'login')
    sent = tg_send(idt['value'], '[MOKAGI] 登入驗證碼：%s（5 分鐘內有效）' % code)
    audit(username, 'otp_request', 'sent=%s' % sent)
    if not sent:
        return jsonify({'ok': False, 'error': 'Telegram 發送失敗'}), 502
    return jsonify({'ok': True, 'message': '驗證碼已發送'})


def api_otp_login():
    data = request.get_json(silent=True) or request.form or {}
    username = (data.get('username') or '').strip()
    code = (data.get('code') or '').strip()
    if is_locked(username):
        return jsonify({'ok': False, 'error': '嘗試次數過多，請稍後再試'}), 429
    if not get_user(username):
        return jsonify({'ok': False, 'error': '帳號不存在'}), 404
    if not verify_otp(username, code, 'login'):
        record_fail(username)
        audit(username, 'otp_login_fail', '')
        return jsonify({'ok': False, 'error': '驗證碼錯誤或已過期'}), 401
    clear_fails(username)
    _finish_login(username, 'telegram_otp')
    return jsonify({'ok': True, 'user': username})


def api_auth_status():
    u = _current_user()
    t = totp_state(u) if u else None
    return jsonify({
        'ok': True,
        'login': bool(u),
        'user': u,
        'totp_enabled': bool(t and t.get('enabled')),
        'telegram_bound': bool((get_identity(u, 'telegram') or {}).get('verified')) if u else False,
    })


ROUTES = [
    ('/security', 'mok_v2_security', security_page, ['GET']),
    ('/security/totp/setup', 'mok_v2_totp_setup', security_totp_setup, ['POST']),
    ('/security/totp/enable', 'mok_v2_totp_enable', security_totp_enable, ['POST']),
    ('/security/totp/disable', 'mok_v2_totp_disable', security_totp_disable, ['POST']),
    ('/security/password', 'mok_v2_password', security_password, ['POST']),
    ('/security/bind_telegram', 'mok_v2_tg_bind', security_bind_telegram, ['POST']),
    ('/security/bind_telegram_verify', 'mok_v2_tg_bind_verify', security_bind_telegram_verify, ['POST']),
    ('/security/bind_contact', 'mok_v2_bind_contact', security_bind_contact, ['POST']),
    ('/api/auth/otp/request', 'mok_v2_otp_request', api_otp_request, ['POST']),
    ('/api/auth/otp/login', 'mok_v2_otp_login', api_otp_login, ['POST']),
    ('/api/auth/status', 'mok_v2_auth_status', api_auth_status, ['GET']),
]


def install(app):
    if app is None:
        return False
    swapped = []
    for rule in list(app.url_map.iter_rules()):
        if str(rule) == '/login':
            app.view_functions[rule.endpoint] = member_login_v2
            swapped.append(rule.endpoint)
    for path, name, fn, methods in ROUTES:
        if name in app.view_functions:
            continue
        app.add_url_rule(path, name, fn, methods=methods)
    print('✅ 會員認證P0 已載入：bcrypt + TOTP + TG OTP（/login 已升級：%s）' % (','.join(swapped) or '無'))
    return True


_main = sys.modules.get('__main__')
if _main is not None:
    try:
        if getattr(_main, 'app', None) is not None:
            install(_main.app)
        _main.mok_auth2 = {
            'hash_pw': hash_pw, 'verify_pw': verify_pw, 'totp_verify': totp_verify,
            'audit': audit, 'issue_otp': issue_otp, 'bind_identity': bind_identity,
            'member_db': MEMBER_DB,
        }
    except Exception as e:
        print('⚠️ 會員認證P0 掛載失敗:', e)
