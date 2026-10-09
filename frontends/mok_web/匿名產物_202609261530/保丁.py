# -*- coding: utf-8 -*-
"""
匿名產物沙盒 v1  (2026-09-26 by 凜)
=========================================
由 mok_web/保丁.py 載入器自動掃描載入（目錄名以 ｚｚｚ 開頭 → 最後載入、可覆蓋前人）。

落地順序：① 目錄+janitor+配額 → ② cookie 簽發+簽名 URL → ③ 產物頁倒數 banner → ④ 註冊 migrate hook

【設計】（不引入 cron、不新增主機常駐服務）
  ~/.mok/_tmp/anon/<sid>/
      meta.json        {created, last_seen, files:[{rel,bytes,ts}], gen_ts:[]}
      ...產物檔案...
  配額：每 session <=20MB / <=6 件；每 IP <=20 次生成/小時；總目錄 <=2GB。
  TTL：統一 6 小時（滑動閒置＝硬上限）；總量超 2GB 先砍最舊 last_seen。
  janitor：app 內背景 thread，每 10 分鐘掃一次（不用 crontab）。
  身分：匿名 cookie（HMAC 簽名）→ sid；註冊/登入後自動把整包 migrate 進會員房間。

【不影響核心】全部以新增路由 + before/after_request 實作；失敗只印訊息，不阻斷啟動。
【停用】目錄改名加底線開頭，重啟 web 即停用。
"""
import os, sys, time, json, hmac, hashlib, base64, threading, shutil, traceback, re, sqlite3
from contextlib import closing

main = sys.modules.get('__main__')
if main is None:
    raise RuntimeError('anon sandbox needs mok_web loader')

app = getattr(main, 'app', None)

try:
    from flask import (request, session, jsonify, Response, g,
                       send_from_directory, redirect, make_response)
except Exception:
    request = session = jsonify = g = None

# ---------------- 設定 ----------------
_HOME = os.path.expanduser('~/.%s' % getattr(main, 'MOKAGI_home', 'mok'))
ANON_ROOT       = os.path.join(_HOME, '_tmp', 'anon')
MEMBER_ROOT     = os.path.join(_HOME, 'user')   # 會員房間：2026-09-30 自 agent/ 移出
Q_SESSION_BYTES = 20 * 1024 * 1024
Q_SESSION_FILES = 6
Q_IP_HOURLY     = 20
Q_TOTAL_BYTES   = 2 * 1024 * 1024 * 1024
TTL_IDLE        = 6 * 3600          # 2026-09-30 統一 TTL：閒置窗＝硬上限＝6 小時
TTL_MAX         = 6 * 3600
JANITOR_EVERY   = 600
COOKIE_NAME     = 'mok_anon'
_URL_TTL        = TTL_IDLE

_LOCK = threading.RLock()
def _LOG(m):
    print('[anon] %s' % m)

# ---------------- 密鑰 / 簽章 ----------------
def _secret():
    try:
        k = app.config.get('SECRET_KEY')
        if k:
            return k.encode('utf-8') if isinstance(k, str) else k
    except Exception:
        pass
    return b'mok-anon-fallback-key'

def _b64(b):
    return base64.urlsafe_b64encode(b).decode('ascii').rstrip('=')

def _sign(data):
    return _b64(hmac.new(_secret(), data.encode('utf-8'), hashlib.sha256).digest())[:32]

def _sid_token(sid, exp):
    return '%d.%s' % (exp, _sign('%s|%d' % (sid, exp)))

def _verify_sid_token(tok, sid):
    try:
        exp_s, sig = tok.split('.', 1)
        if time.time() > int(exp_s):
            return False
        return hmac.compare_digest(sig, _sign('%s|%d' % (sid, int(exp_s))))
    except Exception:
        return False

def _artifact_token(sid, rel, exp):
    return '%d.%s' % (exp, _sign('a|%s|%s|%d' % (sid, rel, exp)))

def _verify_artifact_token(tok, sid, rel):
    try:
        exp_s, sig = tok.split('.', 1)
        if time.time() > int(exp_s):
            return False
        return hmac.compare_digest(sig, _sign('a|%s|%s|%d' % (sid, rel, int(exp_s))))
    except Exception:
        return False

def signed_url(sid, rel, ttl=None):
    exp = int(time.time()) + int(ttl or _URL_TTL)
    rel = rel.lstrip('/')
    return '/anon/%s/%s?t=%s' % (sid, rel, _artifact_token(sid, rel, exp))

# ---------------- 目錄 / meta ----------------
def _safe_sid(sid):
    if not sid:
        return None
    sid = str(sid)
    return sid if re.fullmatch(r'[A-Za-z0-9_\-]{6,64}', sid) else None

def _sid_dir(sid):
    return os.path.join(ANON_ROOT, sid)

def _meta_path(sid):
    return os.path.join(_sid_dir(sid), 'meta.json')

def _load_meta(sid):
    try:
        with open(_meta_path(sid), 'r', encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return None

def _save_meta(sid, meta):
    d = _sid_dir(sid)
    os.makedirs(d, exist_ok=True)
    tmp = _meta_path(sid) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        json.dump(meta, f, ensure_ascii=False)
    os.replace(tmp, _meta_path(sid))

def _new_meta():
    now = time.time()
    return {'created': now, 'last_seen': now, 'files': [], 'gen_ts': []}

def _touch(sid, save=True):
    with _LOCK:
        meta = _load_meta(sid) or _new_meta()
        meta['last_seen'] = time.time()
        if save:
            _save_meta(sid, meta)
        return meta

# ---------------- 配額 ----------------
def _dir_size(path):
    t = 0
    for root, _d, files in os.walk(path):
        for fn in files:
            try:
                t += os.path.getsize(os.path.join(root, fn))
            except OSError:
                pass
    return t

def _iter_disk_files(sid):
    """列出沙盒目錄內實際存在的檔案（相對路徑, 位元組）。

    agent 依 output_router 直接落盤時，磁碟才是事實來源。"""
    base = os.path.join(ANON_ROOT, sid)
    out = []
    if not os.path.isdir(base):
        return out
    for root, dirs, names in os.walk(base):
        dirs[:] = [d for d in dirs if not d.startswith('.') and d != '__pycache__']
        for fn in names:
            if fn.startswith('.'):
                continue
            full = os.path.join(root, fn)
            try:
                if not os.path.isfile(full):
                    continue
                rel = os.path.relpath(full, base).replace(os.sep, '/')
                out.append((rel, os.path.getsize(full)))
            except OSError:
                continue
    out.sort(key=lambda x: x[0])
    return out


def sync_disk_files(sid, save=True):
    """把磁碟上實際存在的檔案併入 meta（agent 直接落盤時的唯一補洞）。"""
    if not sid:
        return None
    with _LOCK:
        meta = _load_meta(sid) or _new_meta()
        known = {str(f.get('rel', '')) for f in meta.get('files', [])}
        added = 0
        now = time.time()
        for rel, nb in _iter_disk_files(sid):
            if rel in known:
                continue
            meta.setdefault('files', []).append({'rel': rel, 'bytes': int(nb), 'ts': now})
            known.add(rel)
            added += 1
        if added:
            meta['last_seen'] = now
            if save:
                _save_meta(sid, meta)
        return meta

def _ip():
    try:
        xf = request.headers.get('X-Forwarded-For', '')
        return (xf.split(',')[0].strip() or request.remote_addr or 'unknown')
    except Exception:
        return 'unknown'

def _ip_log_path():
    return os.path.join(ANON_ROOT, '_ip.json')

def ip_rate_ok(ip=None, consume=False):
    ip = ip or _ip()
    now = time.time()
    with _LOCK:
        try:
            with open(_ip_log_path(), 'r', encoding='utf-8') as f:
                log = json.load(f)
        except Exception:
            log = {}
        arr = [t for t in log.get(ip, []) if now - t < 3600]
        ok = len(arr) < Q_IP_HOURLY
        if consume and ok:
            arr.append(now)
        log[ip] = arr
        try:
            os.makedirs(ANON_ROOT, exist_ok=True)
            with open(_ip_log_path(), 'w', encoding='utf-8') as f:
                json.dump(log, f)
        except Exception:
            pass
        return ok

def check_quota(sid, add_bytes=0, add_files=0):
    with _LOCK:
        meta = _load_meta(sid) or _new_meta()
        used = sum(int(f.get('bytes', 0)) for f in meta.get('files', []))
        n = len(meta.get('files', []))
        if n + int(add_files) > Q_SESSION_FILES:
            return False, 'anon quota: max %d files per session' % Q_SESSION_FILES
        if used + int(add_bytes) > Q_SESSION_BYTES:
            return False, 'anon quota: max %d MB per session' % (Q_SESSION_BYTES // 1048576)
        return True, ''

def register_artifact(sid, rel, nbytes):
    with _LOCK:
        meta = _load_meta(sid) or _new_meta()
        meta.setdefault('files', []).append({'rel': rel, 'bytes': int(nbytes), 'ts': time.time()})
        meta.setdefault('gen_ts', []).append(time.time())
        meta['last_seen'] = time.time()
        _save_meta(sid, meta)
        return meta

# ---------------- janitor（步驟①） ----------------
def _purge(path):
    try:
        if os.path.isdir(path):
            shutil.rmtree(path)
        elif os.path.exists(path):
            os.remove(path)
    except Exception as e:
        _LOG('purge fail %s: %s' % (path, e))

def anon_total_bytes():
    return _dir_size(ANON_ROOT)

def janitor_once():
    """掃一遍匿名目錄：過期即清；再對總量做 2GB 硬上限（砍最舊）。"""
    os.makedirs(ANON_ROOT, exist_ok=True)
    now = time.time()
    removed = 0
    entries = []
    try:
        names = os.listdir(ANON_ROOT)
    except Exception:
        names = []
    for name in names:
        p = os.path.join(ANON_ROOT, name)
        if not os.path.isdir(p):
            continue
        meta = _load_meta(name) or {}
        try:
            mt = os.path.getmtime(p)
        except OSError:
            mt = now
        last = meta.get('last_seen') or mt
        created = meta.get('created') or last
        if (now - last) > TTL_IDLE or (now - created) > TTL_MAX:
            _purge(p)
            removed += 1
            continue
        entries.append((last, _dir_size(p), p))
    total = sum(s for _l, s, _p in entries)
    if total > Q_TOTAL_BYTES:
        entries.sort(key=lambda x: x[0])
        for _l, s, p in entries:
            if total <= Q_TOTAL_BYTES:
                break
            _purge(p)
            removed += 1
            total -= s
    if removed:
        _LOG('janitor swept %d session(s); remain %.1f MB' % (removed, total / 1048576.0))
    return removed

def _janitor_loop():
    while True:
        try:
            janitor_once()
        except Exception:
            _LOG('janitor error: %s' % traceback.format_exc().splitlines()[-1])
        time.sleep(JANITOR_EVERY)

_started = False
def start_janitor():
    global _started
    if _started:
        return
    _started = True
    try:
        os.makedirs(ANON_ROOT, exist_ok=True)
        threading.Thread(target=_janitor_loop, name='anon-janitor', daemon=True).start()
        _LOG('janitor started (every %ds)' % JANITOR_EVERY)
    except Exception as e:
        _LOG('janitor start fail: %s' % e)

start_janitor()

# ---------------- sid / cookie（步驟②） ----------------
def _new_sid():
    raw = base64.urlsafe_b64encode(os.urandom(18)).decode('ascii')
    return re.sub(r'[^A-Za-z0-9]', '', raw)[:24]

def _cookie_value(sid):
    return '%s|%s' % (sid, _sid_token(sid, int(time.time()) + TTL_MAX))

def _sid_from_cookie():
    try:
        val = request.cookies.get(COOKIE_NAME)
    except Exception:
        return None
    if not val or '|' not in val:
        return None
    sid, _sep, tok = val.partition('|')
    sid = _safe_sid(sid)
    if not sid or not _verify_sid_token(tok, sid):
        return None
    return sid

def _member_user():
    try:
        u = session.get('member_user')
        return str(u) if u else None
    except Exception:
        return None

# ---------------- migrate（步驟④） ----------------
def _member_room(username):
    return os.path.join(MEMBER_ROOT, _safe_name(username))

def _safe_name(n):
    n = re.sub(r'[^A-Za-z0-9\u4e00-\u9fff_\-]', '', str(n or ''))
    return n or 'member'

def migrate_to_member(sid, username):
    """註冊/登入後：把匿名沙盒整包搬進會員房間（冪等）。"""
    sid = _safe_sid(sid)
    if not sid:
        return None
    src = _sid_dir(sid)
    if not os.path.isdir(src):
        return None
    room = _member_room(username)
    dest_root = os.path.join(room, '匿名認領')
    dest = os.path.join(dest_root, sid)
    try:
        os.makedirs(dest_root, exist_ok=True)
        if os.path.isdir(dest):
            _purge(dest)
        shutil.move(src, dest)
        meta = {'sid': sid, 'owner': username, 'claimed_at': time.time()}
        try:
            with open(os.path.join(dest_root, '_claimed.jsonl'), 'a', encoding='utf-8') as f:
                f.write(json.dumps(meta, ensure_ascii=False) + '\n')
        except Exception:
            pass
        _LOG('migrated %s -> %s' % (sid, dest))
        return dest
    except Exception as e:
        _LOG('migrate fail %s: %s' % (sid, e))
        return None


def _conv_db_targets():
    """對話 DB 清單（與訪客清理補丁同兩個庫）。"""
    home = getattr(main, 'MOKAGI_home', 'mok')
    return [
        (getattr(main, 'DB_PATH', None), 'chat_history'),
        (os.path.expanduser('~/.{}/.memory/conversation_history.db'.format(home)), 'conversation_history'),
    ]


def migrate_conversations(sid, username):
    """登入／註冊後：把匿名 sid 的對話列轉移到會員 tenant（與產物 migrate 同語義、冪等）。
    - chat_history：tenant  guest:<sid> / web_guest_<sid>  →  username
    - conversation_history：tenant 同步更新，user_key 前綴一併改名（<tenant>_<agent> → <username>_<agent>）
    回傳轉移筆數。"""
    sid = _safe_sid(sid)
    if not sid or not username:
        return 0
    prefix = 'guest:' + sid
    wprefix = 'web_guest_' + sid
    total = 0
    for path, table in _conv_db_targets():
        if not path or not os.path.exists(path):
            continue
        try:
            with closing(sqlite3.connect(path, timeout=30)) as conn:
                if table == 'conversation_history':
                    cur = conn.execute(
                        'UPDATE conversation_history SET tenant=?, user_key=REPLACE(user_key, ?, ?) '
                        'WHERE tenant=? OR tenant=?',
                        (username, prefix, username, prefix, wprefix))
                else:
                    cur = conn.execute(
                        'UPDATE chat_history SET tenant=? WHERE tenant=? OR tenant=?',
                        (username, prefix, wprefix))
                total += cur.rowcount or 0
                conn.commit()
        except Exception as e:
            _LOG('conv migrate fail %s.%s: %s' % (path, table, e))
    if total:
        _LOG('conv migrated %d row(s): %s -> %s' % (total, prefix, username))
    return total


# ---------------- 產物頁（步驟③：倒數 banner） ----------------
_PAGE = """<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>匿名作品（暫時保存）</title>
<style>
 body{{font-family:system-ui,-apple-system,"Noto Sans TC",sans-serif;margin:0;background:#0f1115;color:#e8eaed}}
 .bar{{position:sticky;top:0;z-index:9;background:linear-gradient(90deg,#ff6b3d,#ff2d55);color:#fff;
      padding:10px 14px;font-weight:600;text-align:center;font-size:15px}}
 .bar a{{color:#fff;text-decoration:underline;margin-left:8px}}
 .wrap{{max-width:820px;margin:0 auto;padding:18px}}
 .card{{background:#181b22;border:1px solid #262a33;border-radius:12px;padding:14px;margin:12px 0}}
 .muted{{color:#9aa0a6;font-size:13px}}
 img,video{{max-width:100%;border-radius:8px;display:block}}
 h1{{font-size:18px}}
</style></head><body>
<div class="bar">⏳ 此作品將於 <span id="cd">--:--</span> 後消失 · <a href="/login">註冊永久保存</a></div>
<div class="wrap">
 <h1>匿名作品</h1>
 <p class="muted">session：{sid}　·　建立於 {created}</p>
 {items}
 <div class="card muted">註冊後，這裡的全部作品會自動搬進你的帳號，永久保存、可續編。</div>
</div>
<script>
 var remain={remain};
 function tick(){{
   if(remain<0)remain=0;
   var m=Math.floor(remain/60),s=remain%60;
   document.getElementById('cd').textContent=(m<10?'0':'')+m+':'+(s<10?'0':'')+s;
   if(remain>0){{remain--;setTimeout(tick,1000);}}
   else{{document.getElementById('cd').textContent='已過期';}}
 }}
 tick();
</script></body></html>"""

def _fmt(ts):
    try:
        return time.strftime('%Y-%m-%d %H:%M', time.localtime(ts))
    except Exception:
        return '-'

def _render_page(sid, meta):
    files = (meta or {}).get('files', [])
    items = []
    for f in files:
        rel = f.get('rel', '')
        url = signed_url(sid, rel)
        low = rel.lower()
        if low.endswith(('.mp4', '.webm', '.mov')):
            items.append('<div class="card"><video controls src="%s"></video><div class="muted">%s</div></div>' % (url, rel))
        elif low.endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp')):
            items.append('<div class="card"><img src="%s"><div class="muted">%s</div></div>' % (url, rel))
        else:
            items.append('<div class="card"><a href="%s">%s</a></div>' % (url, rel))
    if not items:
        items = ['<div class="card muted">目前尚無作品。</div>']
    last = (meta or {}).get('last_seen') or time.time()
    remain = max(0, int(TTL_IDLE - (time.time() - last)))
    return _PAGE.format(sid=sid, created=_fmt((meta or {}).get('created') or time.time()),
                        items='\n'.join(items), remain=remain)

# ---------------- 路由 ----------------
def _json(d, code=200):
    return Response(json.dumps(d, ensure_ascii=False), status=code, mimetype='application/json')

@app.route('/anon/<sid>')
def anon_page(sid):
    sid = _safe_sid(sid)
    if not sid:
        return _json({'error': 'bad sid'}, 400)
    meta = _load_meta(sid)
    if meta is None and not os.path.isdir(os.path.join(ANON_ROOT, sid)):
        return _json({'error': 'not found or expired', 'code': 'ANON_GONE'}, 404)
    meta = sync_disk_files(sid) or meta or _new_meta()
    _touch(sid)
    return _render_page(sid, meta)

@app.route('/anon/<sid>/<path:rel>')
def anon_artifact(sid, rel):
    sid = _safe_sid(sid)
    if not sid:
        return _json({'error': 'bad sid'}, 400)
    rel = rel.lstrip('/')
    if not _verify_artifact_token(request.args.get('t', ''), sid, rel):
        return _json({'error': 'invalid or expired url', 'code': 'BAD_SIG'}, 403)
    base = os.path.join(ANON_ROOT, sid)
    full = os.path.realpath(os.path.join(base, rel))
    if not full.startswith(os.path.realpath(base) + os.sep):
        return _json({'error': 'forbidden'}, 403)
    if not os.path.isfile(full):
        return _json({'error': 'not found'}, 404)
    _touch(sid)
    return send_from_directory(base, rel)

@app.route('/api/anon/state')
def anon_state():
    sid = _sid_from_cookie() or getattr(g, '_anon_new_sid', None)
    if not sid:
        return _json({'sid': None, 'guest': True})
    meta = sync_disk_files(sid) or _load_meta(sid) or _new_meta()
    used = sum(int(f.get('bytes', 0)) for f in meta.get('files', []))
    return _json({'sid': sid, 'files': len(meta.get('files', [])),
                  'used': used, 'quota_bytes': Q_SESSION_BYTES, 'quota_files': Q_SESSION_FILES,
                  'ttl_idle': TTL_IDLE, 'ttl_max': TTL_MAX,
                  'page': '/anon/%s' % sid})

@app.route('/api/anon/quota', methods=['POST'])
def anon_quota():
    """生成端先問：這個 session 還能不能再產出 N bytes / M 件。"""
    sid = _sid_from_cookie()
    if not sid:
        return _json({'ok': False, 'reason': 'no anon session'}, 200)
    d = request.get_json(silent=True) or {}
    ok, reason = check_quota(sid, int(d.get('bytes', 0) or 0), int(d.get('files', 0) or 0))
    ip_ok = ip_rate_ok(consume=False)
    return _json({'ok': bool(ok and ip_ok), 'reason': reason or ('' if ip_ok else 'IP rate limited')})

@app.route('/api/anon/claim', methods=['POST'])
def anon_claim():
    """把已落盤的產物登記進匿名 session，回傳簽名 URL 與作品頁。"""
    sid = _sid_from_cookie()
    if not sid:
        return _json({'ok': False, 'reason': 'no anon session'}, 400)
    d = request.get_json(silent=True) or {}
    rel = (d.get('rel') or '').lstrip('/')
    if not rel:
        return _json({'ok': False, 'reason': 'missing rel'}, 400)
    full = os.path.join(ANON_ROOT, sid, rel)
    if not os.path.isfile(full):
        return _json({'ok': False, 'reason': 'file not found in sandbox'}, 404)
    nbytes = os.path.getsize(full)
    ok, reason = check_quota(sid, nbytes, 1)
    if ok and not ip_rate_ok(consume=False):
        ok, reason = False, 'IP rate limited'
    if not ok:
        return _json({'ok': False, 'reason': reason}, 429)
    register_artifact(sid, rel, nbytes)
    ip_rate_ok(consume=True)
    return _json({'ok': True, 'url': signed_url(sid, rel), 'page': '/anon/%s' % sid,
                  'ttl': TTL_IDLE})

# ---------------- 請求掛鉤 ----------------
@app.before_request
def _anon_before():
    try:
        p = request.path or ''
        if p.startswith('/static/') or p.startswith('/socket.io') or p.startswith('/anon/'):
            return None
        sid = _sid_from_cookie()
        usr = _member_user()
        if usr and sid:
            migrate_to_member(sid, usr)
            migrate_conversations(sid, usr)
            g._anon_clear = True
            return None
        if usr:
            return None
        if sid:
            g._anon_sid = sid
        else:
            g._anon_new_sid = _new_sid()
    except Exception:
        pass
    return None

@app.after_request
def _anon_after(resp):
    try:
        if getattr(g, '_anon_clear', False):
            resp.delete_cookie(COOKIE_NAME)
            return resp
        nsid = getattr(g, '_anon_new_sid', None)
        if nsid and not request.cookies.get(COOKIE_NAME):
            resp.set_cookie(COOKIE_NAME, _cookie_value(nsid), max_age=TTL_MAX,
                            httponly=True, samesite='Lax')
    except Exception:
        pass
    return resp

# 對外暴露給其他補丁/工具使用
main.mok_anon = {
    'root': ANON_ROOT, 'signed_url': signed_url, 'check_quota': check_quota,
    'register_artifact': register_artifact, 'sync_disk_files': sync_disk_files, 'migrate_to_member': migrate_to_member,
    'migrate_conversations': migrate_conversations,
    'janitor_once': janitor_once, 'ip_rate_ok': ip_rate_ok,
    'cookie_name': COOKIE_NAME, 'ttl_idle': TTL_IDLE, 'ttl_max': TTL_MAX,
}

_LOG('loaded: root=%s  quota=%dMB/%d files  ttl idle=%dm max=%dh  total=%dGB'
     % (ANON_ROOT, Q_SESSION_BYTES // 1048576, Q_SESSION_FILES,
        TTL_IDLE // 60, TTL_MAX // 3600, Q_TOTAL_BYTES // (1024 ** 3)))
