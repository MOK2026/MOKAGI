# -*- coding: utf-8 -*-
"""
會員認證強化補丁 P1：Passkey / WebAuthn（2026-09-22）作者：凜
==========================================================
載入方式：由 mok_web/保丁.py 掃描器自動載入（檔名固定 保丁.py）
實作方式：
 1) 新增 /security/passkey 管理頁與 /api/auth/webauthn/* 端點
 2) 以 before_request 惰性換裝 /login 與 /security 的回應內容（注入 Passkey 入口），
    因此不受補丁載入順序影響（P0 即使後載入也不會蓋掉本補丁）
 3) 核心 mok_web.py 不動、P0 補丁不動

P1 範圍：
 1. Passkey（WebAuthn / FIDO2）註冊：指紋、Face ID、Windows Hello、實體安全金鑰
 2. Passkey 登入（支援 discoverable credential／無帳號登入）
 3. 憑證管理：列表、改名、刪除；簽章計數檢查（偵測憑證被複製）
 4. 與 P0 共用 users / auth_audit；登入成功沿用同一 session（member_user）
依賴：webauthn 3.0.0（py_webauthn，開源免費、純 Python）
環境變數（可選）：
 MOK_WEB_RP_ID    預設 64071181.xyz     WebAuthn Relying Party ID（= 對外網域）
 MOK_WEB_ORIGINS  預設 https://<rp_id>  允許來源，逗號分隔
"""
import os
import sys
import time
import json
import base64
import sqlite3
from pathlib import Path

try:
    from flask import request, session, jsonify, redirect, make_response
except Exception:
    request = session = jsonify = redirect = None

try:
    import webauthn
    from webauthn import (
        generate_registration_options,
        verify_registration_response,
        generate_authentication_options,
        verify_authentication_response,
        options_to_json,
    )
    from webauthn.helpers.structs import (
        PublicKeyCredentialDescriptor,
        AuthenticatorSelectionCriteria,
        ResidentKeyRequirement,
        UserVerificationRequirement,
    )
    _WA_ERR = None
except Exception as _e:
    webauthn = None
    _WA_ERR = repr(_e)

MOK_ROOT = Path('/home/ubuntu/.mok')
RP_ID = os.environ.get('MOK_WEB_RP_ID', '64071181.xyz')
ORIGIN_ALLOW = [x.strip().rstrip('/') for x in
                os.environ.get('MOK_WEB_ORIGINS', 'https://%s' % RP_ID).split(',') if x.strip()]
CHALLENGE_TTL = 300


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
        print('P1：未找到 member.db，補丁停用')
        return False
    with _conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS webauthn_credentials (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT NOT NULL,
            cred_id TEXT NOT NULL UNIQUE,
            public_key TEXT NOT NULL,
            sign_count INTEGER DEFAULT 0,
            transports TEXT DEFAULT '',
            label TEXT DEFAULT '',
            rp_id TEXT DEFAULT '',
            aaguid TEXT DEFAULT '',
            device_type TEXT DEFAULT '',
            backed_up INTEGER DEFAULT 0,
            created_ts REAL,
            last_used_ts REAL,
            last_ip TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_wa_user ON webauthn_credentials(username);

        CREATE TABLE IF NOT EXISTS webauthn_challenges (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            kind TEXT NOT NULL,
            username TEXT DEFAULT '',
            challenge TEXT NOT NULL,
            rp_id TEXT DEFAULT '',
            origin TEXT DEFAULT '',
            created_ts REAL,
            exp_ts REAL
        );
        CREATE INDEX IF NOT EXISTS idx_wa_ch ON webauthn_challenges(challenge);
        """)
        conn.commit()
    return True


def _b64u(b):
    return base64.urlsafe_b64encode(bytes(b)).rstrip(b'=').decode()


def _b64u_dec(s):
    s = str(s or '')
    return base64.urlsafe_b64decode(s + '=' * (-len(s) % 4))


def _auth2():
    m = sys.modules.get('__main__')
    return (getattr(m, 'mok_auth2', None) or {}) if m else {}


def _client_ip():
    try:
        if request is None:
            return None
        return request.headers.get('X-Forwarded-For', request.remote_addr)
    except Exception:
        return None


def _audit(username, action, detail=''):
    fn = _auth2().get('audit')
    if fn:
        try:
            return fn(username, action, detail)
        except Exception:
            pass
    try:
        with _conn() as conn:
            conn.execute('INSERT INTO auth_audit (ts, username, action, detail, ip) VALUES (?,?,?,?,?)',
                         (time.time(), username, action, str(detail)[:500], _client_ip()))
            conn.commit()
    except Exception as e:
        print('P1 審計寫入失敗：', e)


def _current_user():
    if session is None:
        return None
    return session.get('member_user')


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
    _audit(username, 'login_ok', note)


def _user_exists(u):
    try:
        with _conn() as conn:
            return conn.execute('SELECT 1 FROM users WHERE username=?', (u,)).fetchone() is not None
    except Exception:
        return False


def _is_locked(u):
    fn = _auth2().get('is_locked')
    if fn:
        try:
            return bool(fn(u))
        except Exception:
            pass
    try:
        with _conn() as conn:
            row = conn.execute('SELECT COUNT(*) AS n FROM login_fail WHERE username=? AND ts>?',
                               (u, time.time() - 900)).fetchone()
            return bool(row and row['n'] >= 5)
    except Exception:
        return False


def _record_fail(u):
    fn = _auth2().get('record_fail')
    if fn:
        try:
            fn(u)
            return
        except Exception:
            pass
    try:
        with _conn() as conn:
            conn.execute('INSERT INTO login_fail (ts, username) VALUES (?,?)', (time.time(), u))
            conn.commit()
    except Exception:
        pass


def _ctx():
    """回傳 (rp_id, origin, err)；WebAuthn 的 rp_id 必須是 origin 的網域後綴。"""
    if request is None:
        return None, None, 'no request'
    org = request.headers.get('Origin')
    if org:
        origin = org.rstrip('/')
    else:
        proto = request.headers.get('X-Forwarded-Proto') or request.scheme
        origin = '%s://%s' % (proto, request.headers.get('Host') or '')
    host = origin.split('://', 1)[-1].split('/')[0].split(':')[0].lower()
    if host in ('localhost', '127.0.0.1'):
        return host, origin, None
    if host == RP_ID or host.endswith('.' + RP_ID):
        return RP_ID, origin, None
    if origin in ORIGIN_ALLOW:
        return host, origin, None
    return None, None, '來源 %s 不在允許清單（MOK_WEB_ORIGINS）' % origin


def _save_challenge(kind, username, challenge, rp_id, origin):
    now = time.time()
    with _conn() as conn:
        conn.execute('DELETE FROM webauthn_challenges WHERE exp_ts < ?', (now - 3600,))
        conn.execute('INSERT INTO webauthn_challenges (kind, username, challenge, rp_id, origin, created_ts, exp_ts) '
                     'VALUES (?,?,?,?,?,?,?)',
                     (kind, username or '', challenge, rp_id, origin, now, now + CHALLENGE_TTL))
        conn.commit()


def _take_challenge(kind, challenge):
    now = time.time()
    if not challenge:
        return None
    with _conn() as conn:
        row = conn.execute('SELECT * FROM webauthn_challenges WHERE kind=? AND challenge=? AND exp_ts>? '
                           'ORDER BY id DESC LIMIT 1', (kind, challenge, now)).fetchone()
        if not row:
            return None
        conn.execute('DELETE FROM webauthn_challenges WHERE id=?', (row['id'],))
        conn.commit()
        return dict(row)


def _challenge_in(cred):
    try:
        cd = json.loads(_b64u_dec(cred['response']['clientDataJSON']).decode('utf-8'))
        return cd.get('challenge')
    except Exception:
        return None


def _creds(username):
    with _conn() as conn:
        return [dict(r) for r in conn.execute(
            'SELECT * FROM webauthn_credentials WHERE username=? ORDER BY id DESC', (username,))]


def _cred_by_id(cred_id):
    try:
        with _conn() as conn:
            r = conn.execute('SELECT * FROM webauthn_credentials WHERE cred_id=?', (cred_id,)).fetchone()
            return dict(r) if r else None
    except Exception:
        return None


# ---------------- API：註冊（新增 Passkey）----------------


def api_wa_register_begin():
    if webauthn is None:
        return jsonify({'ok': False, 'error': 'webauthn 套件未安裝：%s' % _WA_ERR}), 500
    u = _current_user()
    if not u:
        return jsonify({'ok': False, 'error': '請先登入'}), 401
    rp, origin, err = _ctx()
    if err:
        return jsonify({'ok': False, 'error': err}), 400
    ex = [PublicKeyCredentialDescriptor(id=_b64u_dec(c['cred_id'])) for c in _creds(u)]
    opts = generate_registration_options(
        rp_id=rp, rp_name='MOKAGI',
        user_id=u.encode('utf-8'), user_name=u, user_display_name=u,
        exclude_credentials=ex,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.PREFERRED,
            user_verification=UserVerificationRequirement.PREFERRED),
    )
    j = json.loads(options_to_json(opts))
    _save_challenge('register', u, j['challenge'], rp, origin)
    _audit(u, 'passkey_register_begin', rp)
    return jsonify(j)


def api_wa_register_complete():
    if webauthn is None:
        return jsonify({'ok': False, 'error': 'webauthn 套件未安裝'}), 500
    u = _current_user()
    if not u:
        return jsonify({'ok': False, 'error': '請先登入'}), 401
    body = request.get_json(silent=True) or {}
    cred = body.get('credential') or {}
    label = (body.get('label') or '').strip()[:40] or ('Passkey %s' % time.strftime('%m-%d %H:%M'))
    tr = body.get('transports') or []
    c = _take_challenge('register', _challenge_in(cred))
    if not c:
        return jsonify({'ok': False, 'error': '註冊挑戰已失效，請重試'}), 400
    try:
        v = verify_registration_response(
            credential=cred,
            expected_challenge=_b64u_dec(c['challenge']),
            expected_rp_id=c['rp_id'],
            expected_origin=c['origin'],
        )
    except Exception as e:
        _audit(u, 'passkey_register_fail', str(e)[:200])
        return jsonify({'ok': False, 'error': '驗證失敗：%s' % str(e)[:200]}), 400
    cid = _b64u(v.credential_id)
    if _cred_by_id(cid):
        return jsonify({'ok': False, 'error': '這把 Passkey 已經註冊過了'}), 409
    dt = getattr(v, 'credential_device_type', '')
    dt = getattr(dt, 'value', dt)
    try:
        with _conn() as conn:
            conn.execute('INSERT INTO webauthn_credentials '
                         '(username,cred_id,public_key,sign_count,transports,label,rp_id,aaguid,'
                         'device_type,backed_up,created_ts,last_ip) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                         (u, cid, _b64u(v.credential_public_key), int(v.sign_count or 0), ','.join(tr),
                          label, c['rp_id'], str(getattr(v, 'aaguid', '') or ''), str(dt),
                          1 if getattr(v, 'credential_backed_up', False) else 0,
                          time.time(), _client_ip()))
            conn.commit()
    except Exception as e:
        return jsonify({'ok': False, 'error': '寫入失敗：%s' % str(e)[:200]}), 500
    _audit(u, 'passkey_registered', '%s %s' % (label, cid[:12]))
    return jsonify({'ok': True, 'cred_id': cid, 'label': label})


# ---------------- API：登入（用 Passkey）----------------


def api_wa_login_begin():
    if webauthn is None:
        return jsonify({'ok': False, 'error': 'webauthn 套件未安裝：%s' % _WA_ERR}), 500
    body = request.get_json(silent=True) or {}
    u = (body.get('username') or '').strip()
    rp, origin, err = _ctx()
    if err:
        return jsonify({'ok': False, 'error': err}), 400
    allow = []
    if u:
        if not _user_exists(u):
            return jsonify({'ok': False, 'error': '帳號不存在'}), 404
        if _is_locked(u):
            return jsonify({'ok': False, 'error': '嘗試次數過多，請 15 分鐘後再試'}), 429
        allow = [PublicKeyCredentialDescriptor(id=_b64u_dec(x['cred_id'])) for x in _creds(u)]
        if not allow:
            return jsonify({'ok': False, 'error': '此帳號尚未設定 Passkey'}), 404
    opts = generate_authentication_options(
        rp_id=rp, allow_credentials=allow,
        user_verification=UserVerificationRequirement.PREFERRED)
    j = json.loads(options_to_json(opts))
    _save_challenge('login', u, j['challenge'], rp, origin)
    return jsonify(j)


def api_wa_login_complete():
    if webauthn is None:
        return jsonify({'ok': False, 'error': 'webauthn 套件未安裝'}), 500
    body = request.get_json(silent=True) or {}
    cred = body.get('credential') or {}
    row = _take_challenge('login', _challenge_in(cred))
    if not row:
        return jsonify({'ok': False, 'error': '登入挑戰已失效，請重新整理再試'}), 400
    cid = cred.get('id') or cred.get('rawId')
    rec = _cred_by_id(cid)
    u = (body.get('username') or '').strip() or (rec['username'] if rec else '')
    if not rec or (row.get('username') and row['username'] != rec['username']):
        _record_fail(u or 'unknown')
        _audit(u or 'unknown', 'passkey_login_fail', 'credential not found')
        return jsonify({'ok': False, 'error': '找不到對應的 Passkey'}), 401
    try:
        v = verify_authentication_response(
            credential=cred,
            expected_challenge=_b64u_dec(row['challenge']),
            expected_rp_id=row['rp_id'],
            expected_origin=row['origin'],
            credential_public_key=_b64u_dec(rec['public_key']),
            credential_current_sign_count=int(rec['sign_count'] or 0),
        )
    except Exception as e:
        _record_fail(rec['username'])
        _audit(rec['username'], 'passkey_login_fail', str(e)[:200])
        return jsonify({'ok': False, 'error': '驗證失敗：%s' % str(e)[:200]}), 401
    old = int(rec['sign_count'] or 0)
    new = int(getattr(v, 'new_sign_count', 0) or 0)
    if old and new and new <= old:
        _audit(rec['username'], 'passkey_clone_suspect', 'counter %s -> %s' % (old, new))
    try:
        with _conn() as conn:
            conn.execute('UPDATE webauthn_credentials SET sign_count=?, last_used_ts=?, last_ip=? WHERE cred_id=?',
                         (new or old, time.time(), _client_ip(), cid))
            conn.commit()
    except Exception:
        pass
    _finish_login(rec['username'], 'passkey')
    return jsonify({'ok': True, 'user': rec['username']})


# ---------------- API：憑證管理 ----------------


def api_wa_list():
    u = _current_user()
    if not u:
        return jsonify({'ok': False, 'error': '請先登入'}), 401
    out = []
    for r in _creds(u):
        out.append({
            'cred_id': r['cred_id'], 'label': r['label'], 'created_ts': r['created_ts'],
            'last_used_ts': r['last_used_ts'], 'device_type': r['device_type'],
            'backed_up': bool(r['backed_up']), 'transports': r['transports'],
        })
    return jsonify({'ok': True, 'rp_id': RP_ID, 'credentials': out})


def api_wa_rename():
    u = _current_user()
    if not u:
        return jsonify({'ok': False, 'error': '請先登入'}), 401
    body = request.get_json(silent=True) or {}
    cid = (body.get('cred_id') or '').strip()
    label = (body.get('label') or '').strip()[:40]
    rec = _cred_by_id(cid)
    if not rec or rec['username'] != u:
        return jsonify({'ok': False, 'error': '找不到這把 Passkey'}), 404
    with _conn() as conn:
        conn.execute('UPDATE webauthn_credentials SET label=? WHERE cred_id=?', (label, cid))
        conn.commit()
    _audit(u, 'passkey_renamed', '%s -> %s' % (rec['label'], label))
    return jsonify({'ok': True})


def api_wa_delete():
    u = _current_user()
    if not u:
        return jsonify({'ok': False, 'error': '請先登入'}), 401
    body = request.get_json(silent=True) or {}
    cid = (body.get('cred_id') or '').strip()
    rec = _cred_by_id(cid)
    if not rec or rec['username'] != u:
        return jsonify({'ok': False, 'error': '找不到這把 Passkey'}), 404
    with _conn() as conn:
        conn.execute('DELETE FROM webauthn_credentials WHERE cred_id=?', (cid,))
        conn.commit()
    _audit(u, 'passkey_deleted', rec['label'])
    return jsonify({'ok': True})


# ---------------- 頁面 ----------------

PAGE_CSS = """
<style>
 body{font-family:-apple-system,'PingFang TC','Microsoft JhengHei',sans-serif;background:#0f1220;color:#e8e8f0;
      margin:0;padding:40px 16px;display:flex;justify-content:center}
 .card{background:#1a1f33;border:1px solid #2c3560;border-radius:16px;padding:32px 36px;width:560px;
       box-shadow:0 10px 40px rgba(0,0,0,.5)}
 h1{font-size:22px;margin:0 0 6px;background:linear-gradient(90deg,#7aa2ff,#c084fc);-webkit-background-clip:text;
    -webkit-text-fill-color:transparent}
 p.sub{color:#8b93b5;font-size:13px;margin:0 0 18px}
 button{cursor:pointer;margin-top:12px;padding:10px 16px;border-radius:10px;border:0;
        background:linear-gradient(90deg,#7aa2ff,#c084fc);color:#0f1220;font-weight:700}
 button.ghost{background:transparent;border:1px solid #2c3560;color:#aab2d6;font-weight:400;padding:6px 10px;font-size:12px}
 .row{border:1px solid #2c3560;border-radius:12px;padding:12px 14px;margin-top:10px;display:flex;
      justify-content:space-between;align-items:center;gap:10px}
 .mono{font-family:ui-monospace,Menlo,monospace;font-size:12px;color:#8b93b5}
 .ok{color:#7ee787}.bad{color:#ff7b72}
 a{color:#7aa2ff}
</style>
"""

PK_JS_COMMON = """
<script>
function b64uToBuf(s){s=String(s).replace(/-/g,'+').replace(/_/g,'/');var p=s.length%4;if(p)s+='='.repeat(4-p);
 var bin=atob(s),u=new Uint8Array(bin.length);for(var i=0;i<bin.length;i++)u[i]=bin.charCodeAt(i);return u.buffer;}
function bufToB64u(b){var u=new Uint8Array(b),s='';for(var i=0;i<u.length;i++)s+=String.fromCharCode(u[i]);
 return btoa(s).replace(/\\+/g,'-').replace(/\\//g,'_').replace(/=+$/,'');}
function pkPost(url,data){return fetch(url,{method:'POST',headers:{'Content-Type':'application/json'},
 body:JSON.stringify(data||{})}).then(function(r){return r.json().then(function(j){j._status=r.status;return j;});});}
function pkSay(id,t,ok){var e=document.getElementById(id);if(e){e.textContent=t;e.className='sub '+(ok?'ok':'bad');}}
</script>
"""


PASSKEY_PAGE_BODY = """
<h1>Passkey 管理</h1>
<p class="sub">支援指紋、Face ID、Windows Hello、實體安全金鑰。<br>
生物特徵只留在你自己的裝置，本站只保存公開金鑰；Passkey 綁定網域，換網站無效。</p>
<div id="msg" class="sub"></div>
<button onclick="pkAdd()">＋ 新增 Passkey</button>
<div id="list"><p class="sub">載入中…</p></div>
<p class="sub" style="margin-top:16px"><a href="/security">← 回安全設定</a>　<a href="/">回首頁</a></p>
"""

PASSKEY_PAGE_JS = """
<script>
function fmt(t){if(!t)return '未曾使用';var d=new Date(t*1000);return d.toLocaleString();}
function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){
 return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
function pkLoad(){
 fetch('/api/auth/webauthn/list').then(function(r){return r.json();}).then(function(j){
  var box=document.getElementById('list');
  if(!j.ok){box.innerHTML='<p class="sub bad">'+esc(j.error||'讀取失敗')+'</p>';return;}
  if(!j.credentials.length){box.innerHTML='<p class="sub">尚未新增任何 Passkey。</p>';return;}
  var h='';
  j.credentials.forEach(function(c){
   h+='<div class="row"><div><b>'+esc(c.label||'Passkey')+'</b><br>'+
      '<span class="mono">'+esc(c.cred_id.slice(0,18))+'… ｜ '+(c.backed_up?'已同步':'單機')+
      ' ｜ 上次：'+esc(fmt(c.last_used_ts))+'</span></div>'+
      '<div><button class="ghost" onclick="pkRename(\\''+esc(c.cred_id)+'\\')">改名</button> '+
      '<button class="ghost" onclick="pkDel(\\''+esc(c.cred_id)+'\\')">刪除</button></div></div>';
  });
  box.innerHTML=h;
 }).catch(function(e){document.getElementById('list').innerHTML='<p class="sub bad">'+esc(e)+'</p>';});
}
function pkAdd(){
 pkSay('msg','請在裝置上完成驗證（指紋／Face ID／PIN）…',true);
 pkPost('/api/auth/webauthn/register/begin',{}).then(function(o){
  if(!o.ok&&!o.challenge){pkSay('msg',o.error||'無法開始註冊',false);return;}
  o.challenge=b64uToBuf(o.challenge);
  if(o.user&&o.user.id)o.user.id=b64uToBuf(o.user.id);
  if(o.excludeCredentials)o.excludeCredentials=o.excludeCredentials.map(function(c){c.id=b64uToBuf(c.id);return c;});
  return navigator.credentials.create({publicKey:o}).then(function(cred){
   var t=[];try{t=cred.response.getTransports?cred.response.getTransports():[];}catch(e){}
   return pkPost('/api/auth/webauthn/register/complete',{label:prompt('為這把 Passkey 取個名字（可留空）','')||'',
    transports:t,credential:{id:cred.id,rawId:bufToB64u(cred.rawId),type:cred.type,
    response:{clientDataJSON:bufToB64u(cred.response.clientDataJSON),
              attestationObject:bufToB64u(cred.response.attestationObject)}}});
  }).then(function(j){
   if(j.ok){pkSay('msg','✅ 已新增：'+(j.label||''),true);pkLoad();}
   else{pkSay('msg',j.error||'註冊失敗',false);}
  });
 }).catch(function(e){pkSay('msg','已取消或失敗：'+(e&&e.message?e.message:e),false);});
}
function pkRename(id){
 var v=prompt('新的名稱','');if(v===null)return;
 pkPost('/api/auth/webauthn/rename',{cred_id:id,label:v}).then(function(j){
  pkSay('msg',j.ok?'✅ 已改名':(j.error||'改名失敗'),j.ok);pkLoad();});
}
function pkDel(id){
 if(!confirm('確定刪除這把 Passkey？刪除後將無法再用它登入。'))return;
 pkPost('/api/auth/webauthn/delete',{cred_id:id}).then(function(j){
  pkSay('msg',j.ok?'✅ 已刪除':(j.error||'刪除失敗'),j.ok);pkLoad();});
}
pkLoad();
</script>
"""


def passkey_page():
    u = _current_user()
    if not u:
        return redirect('/login')
    body = ('<!doctype html><html><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>Passkey 管理</title>%s%s</head><body><div class="card">%s%s</div>%s</body></html>'
            % (PAGE_CSS, PK_JS_COMMON, PASSKEY_PAGE_BODY, PASSKEY_PAGE_JS, ''))
    return body


LOGIN_BLOCK = """
<div style="margin-top:18px">
 <div style="display:flex;align-items:center;gap:10px;color:#8b93b5;font-size:12px;margin-bottom:10px">
  <span style="flex:1;height:1px;background:#2c3560"></span>或<span style="flex:1;height:1px;background:#2c3560"></span>
 </div>
 <button type="button" style="margin-top:8px" onclick="mokPasskeyLogin()">🔐 用 Passkey 登入（指紋／Face ID）</button>
 <div id="pkmsg" class="sub" style="margin-top:10px"></div>
</div>
<script>
async function mokPasskeyLogin(){
 try{
  pkSay('pkmsg','請在裝置上完成驗證…',true);
  var o=await pkPost('/api/auth/webauthn/login/begin',{});
  if(!o.challenge){pkSay('pkmsg',o.error||'此瀏覽器尚未註冊 Passkey',false);return;}
  o.challenge=b64uToBuf(o.challenge);
  if(o.allowCredentials)o.allowCredentials=o.allowCredentials.map(function(c){c.id=b64uToBuf(c.id);return c;});
  var cred=await navigator.credentials.get({publicKey:o});
  var j=await pkPost('/api/auth/webauthn/login/complete',{credential:{
    id:cred.id,rawId:bufToB64u(cred.rawId),type:cred.type,
    response:{clientDataJSON:bufToB64u(cred.response.clientDataJSON),
              authenticatorData:bufToB64u(cred.response.authenticatorData),
              signature:bufToB64u(cred.response.signature),
              userHandle:cred.response.userHandle?bufToB64u(cred.response.userHandle):null}}});
  if(j.ok){pkSay('pkmsg','✅ 登入成功，載入中…',true);location.href='/';}
  else{pkSay('pkmsg',j.error||'登入失敗',false);}
 }catch(e){pkSay('pkmsg','已取消或失敗：'+(e&&e.message?e.message:e),false);}
}
</script>
"""

SECURITY_BLOCK = """
<div style="margin-top:16px;border-top:1px solid #2c3560;padding-top:14px">
 <a href="/security/passkey" style="color:#7aa2ff;font-weight:700">🔐 Passkey（指紋／Face ID）管理 →</a>
 <p class="sub" style="margin-top:6px">支援指紋、Face ID、Windows Hello、實體安全金鑰。</p>
</div>
"""


# ---------------- 掛載 ----------------

ROUTES = [
    ('/security/passkey', 'mok_v2_passkey', passkey_page, ['GET']),
    ('/api/auth/webauthn/list', 'mok_v2_wa_list', api_wa_list, ['GET']),
    ('/api/auth/webauthn/register/begin', 'mok_v2_wa_reg_begin', api_wa_register_begin, ['POST']),
    ('/api/auth/webauthn/register/complete', 'mok_v2_wa_reg_done', api_wa_register_complete, ['POST']),
    ('/api/auth/webauthn/login/begin', 'mok_v2_wa_log_begin', api_wa_login_begin, ['POST']),
    ('/api/auth/webauthn/login/complete', 'mok_v2_wa_log_done', api_wa_login_complete, ['POST']),
    ('/api/auth/webauthn/rename', 'mok_v2_wa_rename', api_wa_rename, ['POST']),
    ('/api/auth/webauthn/delete', 'mok_v2_wa_delete', api_wa_delete, ['POST']),
]

INJECT_PATHS = ('/login', '/logout', '/member', '/security')
# 依「頁面內容」判斷是否為登入頁：/member、/logout 未登入時都會直接回傳同一份登入頁
LOGIN_MARK = '<title>會員登入</title>'
SECURITY_MARK = 'Passkey 管理'


def _make_wrapper(base, path):
    def _w(*a, **kw):
        try:
            resp = make_response(base(*a, **kw))
        except Exception:
            return base(*a, **kw)
        try:
            html = resp.get_data(as_text=True)
        except Exception:
            return resp
        # 冪等：已注入過就不再重複注入
        if not html or '</body>' not in html or 'mokPasskeyLogin' in html:
            return resp
        # 依內容判斷，而非只認網址（/member、/logout 未登入時回傳的是同一份登入頁）
        if path == '/security' or SECURITY_MARK in html:
            block = SECURITY_BLOCK
        elif LOGIN_MARK in html:
            block = PK_JS_COMMON + LOGIN_BLOCK
        else:
            return resp
        try:
            if '</div></body>' in html:
                # 必須插進 .card 內：登入頁 body 是 flex 容器，插在 </body> 前會跑到登入框右邊
                html = html.replace('</div></body>', block + '</div></body>', 1)
            else:
                html = html.replace('</body>', block + '</body>', 1)
            resp.set_data(html)
        except Exception:
            return resp
        return resp
    _w._mok_pk_wrapped = True
    _w._mok_pk_base = base
    return _w


def _wrap_pages(app):
    """惰性換裝：每次請求前確認 /login 與 /security 已被注入 Passkey 入口。
    這樣不論 P1 與 P0 誰先載入都不會被蓋掉。"""
    for rule in list(app.url_map.iter_rules()):
        p = str(rule)
        if p not in INJECT_PATHS:
            continue
        cur = app.view_functions.get(rule.endpoint)
        if cur is None or getattr(cur, '_mok_pk_wrapped', False):
            continue
        app.view_functions[rule.endpoint] = _make_wrapper(cur, p)


def install(app):
    if app is None:
        return False
    if not _init_db():
        return False
    for path, name, fn, methods in ROUTES:
        if name in app.view_functions:
            continue
        app.add_url_rule(path, name, fn, methods=methods)
    if not getattr(app, '_mok_passkey_hooked', False):
        @app.before_request
        def _mok_passkey_inject():
            _wrap_pages(app)
        app._mok_passkey_hooked = True
    print('✅ 會員認證P1 已載入：Passkey/WebAuthn（/security/passkey、rp_id=%s）' % RP_ID)
    return True


_main = sys.modules.get('__main__')
if _main is not None:
    try:
        if getattr(_main, 'app', None) is not None:
            install(_main.app)
        _main.mok_passkey = {'RP_ID': RP_ID, 'creds': _creds, 'install': install}
    except Exception as _e:
        print('⚠️ 會員認證P1 掛載失敗：', _e)
