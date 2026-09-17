# -*- coding: utf-8 -*-
"""
對外全功能 API 補丁（key-gated /api/external/*）v1.0
位置: .mok/frontends/mok_web/對外API_<ts>/保丁.py
作者: 病毒引擎 2026-09-07 ｜ 掛載: 保丁載入器自動掃描（不改核心）
端點: POST /api/external/run  GET /api/external/me
      POST /api/external/keys GET /api/external/health
設計: API key + admin/member/guest 三級 + agent/工具白名單，與凜權限矩陣相容
      admin key -> 凜 member.db vip 服務帳號（全 agent）
      member key-> 綁凜會員帳號（agent 依凜 plans；工具剔除高風險）
      guest key -> 預設 403（guest 被擋）
"""
import os, sys, json, time, sqlite3, hashlib, secrets, threading

main = sys.modules.get('__main__')
try:
    from flask import request, jsonify, session
except Exception as _e:
    request = jsonify = session = None
    print('[對外API] flask import 失敗:', _e)

app = getattr(main, 'app', None)
_PATCH_DIR = os.path.dirname(os.path.abspath(__file__))
KEY_DB = os.path.join(_PATCH_DIR, 'external_keys.db')
LOG_FILE = os.path.join(_PATCH_DIR, 'external_access.log')
ADMIN_KEY_FILE = os.path.join(_PATCH_DIR, 'admin_key.txt')
_SALT = 'mok_external_v1'
_db_lock = threading.Lock()
_LIN_MEMBER_DB = os.path.join(os.path.dirname(_PATCH_DIR), '會員系統_202608311340', 'member.db')

# ========== 角色政策（可被 external_matrix.json 覆寫） ==========
_HIGH_RISK_PREFIX = ('admin',)  # admin* 系列高權限工具一律僅限 admin key
_HIGH_RISK_EXACT = {'backup', 'replace_in_file', 'crash_handler', 'autofix'}

def _is_high_risk(t):
    t = (t or '').strip()
    if not t:
        return False
    if t.startswith(_HIGH_RISK_PREFIX):
        return True
    return t in _HIGH_RISK_EXACT

_ROLE_POLICY = {
    'admin':  {'agents': '*', 'tools': 'inherit', 'run': True},
    'member': {'agents': '*', 'tools': 'restrict', 'run': True},
    'guest':  {'agents': [], 'tools': 'none', 'run': False},
}
_ADMIN_ONLY_AGENTS = []
_MATRIX_FILE = os.path.join(_PATCH_DIR, 'external_matrix.json')
_MATRIX_MTIME = 0.0

def _reload_matrix():
    """熱載入凜矩陣（凜 schema：levels + admin_only_*；本補丁亦相容 roles）。
    凜覆寫 external_matrix.json 後，下一請求自動生效，免重啟。"""
    global _ROLE_POLICY, _ADMIN_ONLY_AGENTS, _MATRIX_MTIME
    try:
        mt = os.path.getmtime(_MATRIX_FILE)
    except Exception:
        return
    if abs(mt - _MATRIX_MTIME) < 1e-9:
        return
    _MATRIX_MTIME = mt
    try:
        with open(_MATRIX_FILE, encoding='utf-8') as _f:
            _usr = json.load(_f)
    except Exception as _e:
        print('[對外API] matrix 讀取失敗:', _e, flush=True)
        return
    if not isinstance(_usr, dict):
        return
    # 本補丁 roles schema（直接覆寫三級政策）
    if 'roles' in _usr and isinstance(_usr['roles'], dict):
        _ROLE_POLICY.update(_usr['roles'])
    # 凜矩陣 schema：levels.admin/member/guest × {agents,tools,endpoints,chat}
    elif 'levels' in _usr and isinstance(_usr['levels'], dict):
        lv = _usr['levels']
        def _agents_of(x):
            if not isinstance(x, dict):
                return '*'
            a = x.get('agents', '*')
            return a if a in ('*', '@plan') else (a if isinstance(a, list) and a else '*')
        if isinstance(lv.get('admin'), dict):
            _ROLE_POLICY['admin'] = {'agents': '*', 'tools': 'inherit',
                                     'run': bool(lv['admin'].get('chat', True))}
        if isinstance(lv.get('member'), dict):
            _ROLE_POLICY['member'] = {'agents': _agents_of(lv.get('member')), 'tools': 'restrict',
                                      'run': bool(lv['member'].get('chat', True))}
        if isinstance(lv.get('guest'), dict):
            _ROLE_POLICY['guest'] = {'agents': [], 'tools': 'none',
                                     'run': bool(lv['guest'].get('chat', False))}
    # admin_only_agents / admin_only_tools 護欄清單
    if isinstance(_usr.get('admin_only_agents'), list):
        _ADMIN_ONLY_AGENTS = _usr['admin_only_agents']
    if isinstance(_usr.get('admin_only_tools'), list):
        for _t in _usr['admin_only_tools']:
            _HIGH_RISK_EXACT.add(_t)
    print('[對外API] matrix 已載入 (mtime=%.0f) policy=%s admin_only_agents=%d'
          % (mt, sorted(_ROLE_POLICY.keys()), len(_ADMIN_ONLY_AGENTS)), flush=True)

_reload_matrix()

def _connect(db_path=KEY_DB):
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn

def _init_db():
    with _db_lock, _connect() as conn:
        conn.executescript('''
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            key_hash TEXT UNIQUE NOT NULL,
            label TEXT DEFAULT '',
            role TEXT DEFAULT 'member',
            member_username TEXT DEFAULT '',
            allowed_agents TEXT DEFAULT '[]',
            allowed_tools TEXT DEFAULT '[]',
            enabled INTEGER DEFAULT 1,
            note TEXT DEFAULT '',
            created_at REAL,
            last_used REAL
        );''')
        conn.commit()
        row = conn.execute("SELECT COUNT(*) c FROM api_keys WHERE role='admin' AND enabled=1").fetchone()
        if row['c'] == 0:
            _raw = _issue_key_locked(conn, role='admin', label='root-admin', note='首次自動產生')
            try:
                with open(ADMIN_KEY_FILE, 'w', encoding='utf-8') as _f:
                    _f.write(_raw + '\n')
                os.chmod(ADMIN_KEY_FILE, 0o600)
            except Exception as _e:
                print('[對外API] 寫 admin key 檔失敗:', _e, flush=True)
            print('[對外API] 已產生 root admin key ->', ADMIN_KEY_FILE, flush=True)

def _issue_key_locked(conn, role='member', label='', member_username='',
                      allowed_agents=None, allowed_tools=None, note=''):
    raw = 'ext_' + role + '_' + secrets.token_urlsafe(18)
    h = hashlib.sha256((_SALT + raw).encode()).hexdigest()
    conn.execute(
        'INSERT OR IGNORE INTO api_keys (key_hash,label,role,member_username,allowed_agents,allowed_tools,enabled,note,created_at) VALUES (?,?,?,?,?,?,1,?,?)',
        (h, label or raw[:8], role, member_username or '',
         json.dumps(allowed_agents or [], ensure_ascii=False),
         json.dumps(allowed_tools or [], ensure_ascii=False),
         note, time.time()))
    conn.commit()
    return raw

def _find_key(raw_key):
    if not raw_key:
        return None
    h = hashlib.sha256((_SALT + raw_key).encode()).hexdigest()
    with _connect() as conn:
        row = conn.execute('SELECT * FROM api_keys WHERE key_hash=?', (h,)).fetchone()
        if row:
            conn.execute('UPDATE api_keys SET last_used=? WHERE key_hash=?', (time.time(), h))
            conn.commit()
    return dict(row) if row else None

def _extract_key():
    k = ''
    if request is not None:
        k = request.headers.get('X-API-Key') or ''
        if not k:
            auth = request.headers.get('Authorization', '')
            if auth.startswith('Bearer '):
                k = auth[7:]
        if not k:
            k = request.args.get('api_key', '')
    return (k or '').strip()

def _log(line):
    try:
        with open(LOG_FILE, 'a', encoding='utf-8') as f:
            f.write('[%s] %s\n' % (time.strftime('%Y-%m-%d %H:%M:%S'), line))
    except Exception:
        pass

def _ensure_lin_service_account():
    """於凜 member.db 建立 external_admin(vip) 服務帳號（INSERT OR IGNORE，不動既有資料）。"""
    if not os.path.exists(_LIN_MEMBER_DB):
        _log('WARN 凜 member.db 不存在，跳過服務帳號建立')
        return False
    try:
        conn = sqlite3.connect(_LIN_MEMBER_DB, timeout=10)
        pw_h = hashlib.sha256(('mok_member_v1' + secrets.token_urlsafe(12)).encode()).hexdigest()
        conn.execute(
            "INSERT OR IGNORE INTO users (username,password_hash,plan,balance_tokens,monthly_used,month_key,created_at) VALUES (?,?,?,?,?,?,?)",
            ('external_admin', pw_h, 'vip', 10**12, 0, time.strftime('%Y-%m'), time.time()))
        conn.commit()
        conn.close()
        return True
    except Exception as _e:
        _log('WARN 凜 member.db 服務帳號建立失敗: %s' % _e)
        return False

def _overlay_tools(cfg, role):
    """工具白名單 overlay：member/guest 剔除高風險工具（凜矩陣相容）。"""
    if role == 'admin':
        return cfg
    cfg = dict(cfg or {})
    orig = cfg.get('MOK_ALLOWED_TOOLS', '') or ''
    if isinstance(orig, str):
        base = [x.strip() for x in orig.split(',') if x.strip()]
    else:
        base = list(orig or [])
    keep = [t for t in base if not _is_high_risk(t)]
    cfg['MOK_ALLOWED_TOOLS'] = ','.join(keep)
    return cfg

# ========== 端點 ==========
if app is not None:
    @app.route('/api/external/health', methods=['GET'])
    def external_health():
        return jsonify(ok=True, service='mokagi-external-api', ts=time.time())

    @app.route('/api/external/me', methods=['GET'])
    def external_me():
        rec = _find_key(_extract_key())
        if not rec:
            return jsonify(ok=False, error='invalid_api_key'), 401
        try:
            aa = json.loads(rec['allowed_agents'] or '[]')
        except Exception:
            aa = []
        return jsonify(ok=True, role=rec['role'], label=rec['label'],
                       member_username=rec['member_username'],
                       allowed_agents=aa, enabled=bool(rec['enabled']))

    @app.route('/api/external/run', methods=['POST'])
    def external_run():
        """全功能呼叫：{agent, message, context_files?, auto_mode?, initial_prompt?, user_ref?}"""
        _t0 = time.time()
        rec = _find_key(_extract_key())
        if not rec:
            _log('run 401 invalid_key')
            return jsonify(ok=False, error='invalid_api_key', hint='請提供 X-API-Key'), 401
        if not rec['enabled']:
            _log("run 403 disabled key=%s" % rec['label'])
            return jsonify(ok=False, error='key_disabled'), 403
        _reload_matrix()
        role = rec['role']
        policy = _ROLE_POLICY.get(role, _ROLE_POLICY['guest'])
        if not policy.get('run', False):
            _log("run 403 role_forbidden role=%s label=%s" % (role, rec['label']))
            return jsonify(ok=False, error='role_%s_forbidden' % role,
                           hint='guest 層級不開放呼叫，請升級'), 403

        data = request.get_json(silent=True) or {}
        agent = (data.get('agent') or '').strip() or '客服'
        message = (data.get('message') or data.get('text') or '').strip()
        if not message:
            return jsonify(ok=False, error='empty_message'), 400

        # agent 白名單（key 層）
        try:
            ka = json.loads(rec['allowed_agents'] or '[]')
        except Exception:
            ka = []
        if policy.get('agents') != '*' and ka and agent not in ka:
            _log("run 403 agent_not_allowed label=%s agent=%s" % (rec['label'], agent))
            return jsonify(ok=False, error='agent_not_allowed: ' + agent), 403
        # 凜矩陣護欄：admin_only_agents 僅 admin key 可喚醒（guest/member 一律 403）
        if role != 'admin' and agent in _ADMIN_ONLY_AGENTS:
            _log("run 403 admin_only_agent label=%s agent=%s" % (rec['label'], agent))
            return jsonify(ok=False, error='admin_only_agent: ' + agent), 403

        member_user = rec['member_username'] or ('external_admin' if role == 'admin' else '')
        if not member_user:
            return jsonify(ok=False, error='no_member_binding'), 403

        try:
            import mokagi as _m
        except Exception as _e:
            return jsonify(ok=False, error='mokagi_unavailable: ' + str(_e)), 500

        # 橋接：讓凜會員補丁的 session 檢查通過（僅本次請求 context）
        try:
            if session is not None:
                session['member_user'] = member_user
        except Exception as _e:
            _log('run session 設定失敗: %s' % _e)

        user_ref = (data.get('user_ref') or '').strip() or ('ext:' + (rec['label'] or '?'))
        ctx_files = data.get('context_files')
        auto_mode = bool(data.get('auto_mode', False))
        init_prompt = data.get('initial_prompt')

        async def _coro():
            cfg = await _m.get_agent_config(agent)
            cfg = _overlay_tools(cfg, role)
            return await _m.process_message(
                user_id=user_ref, text=message, stream_callback=None,
                agent_name=agent, agent_config=cfg,
                auto_mode=auto_mode, initial_prompt=init_prompt,
                context_files=ctx_files)

        try:
            import asyncio
            loop = asyncio.new_event_loop()
            try:
                asyncio.set_event_loop(loop)
                reply = loop.run_until_complete(_coro())
            finally:
                try:
                    loop.close()
                except Exception:
                    pass
        except Exception as _e:
            _log("run ERROR label=%s agent=%s err=%s" % (rec['label'], agent, _e))
            return jsonify(ok=False, error='run_failed: ' + str(_e)), 500

        _log("run OK role=%s label=%s agent=%s ms=%d"
             % (role, rec['label'], agent, int((time.time() - _t0) * 1000)))
        return jsonify(ok=True, role=role, agent=agent, reply=reply or '')

    @app.route('/api/external/keys', methods=['POST'])
    def external_keys_admin():
        """admin key 簽發/停用子 key。body: {action, role, label, member_username, note, key_id}"""
        rec = _find_key(_extract_key())
        if not rec:
            return jsonify(ok=False, error='invalid_api_key'), 401
        if rec['role'] != 'admin':
            return jsonify(ok=False, error='admin_only'), 403
        data = request.get_json(silent=True) or {}
        action = data.get('action', 'issue')
        with _db_lock, _connect() as conn:
            if action == 'issue':
                role = data.get('role', 'member')
                if role not in ('admin', 'member', 'guest'):
                    return jsonify(ok=False, error='bad_role'), 400
                raw = _issue_key_locked(
                    conn, role=role, label=data.get('label', ''),
                    member_username=data.get('member_username', ''),
                    allowed_agents=data.get('allowed_agents'),
                    allowed_tools=data.get('allowed_tools'),
                    note=data.get('note', ''))
                _log("keys ISSUE by admin role=%s label=%s" % (role, data.get('label', '')))
                return jsonify(ok=True, api_key=raw, role=role, warning='請立即保存，明文僅此一次')
            elif action == 'revoke':
                key_id = data.get('key_id')
                lab = data.get('label', '')
                if key_id:
                    conn.execute('UPDATE api_keys SET enabled=0 WHERE id=?', (key_id,))
                elif lab:
                    conn.execute('UPDATE api_keys SET enabled=0 WHERE label=?', (lab,))
                else:
                    return jsonify(ok=False, error='need_key_id_or_label'), 400
                conn.commit()
                return jsonify(ok=True, revoked=True)
        return jsonify(ok=False, error='unknown_action'), 400

# ========== 初始化 ==========
_init_db()
_ensure_lin_service_account()
if app is not None:
    print('[對外API] /api/external/{run,me,keys,health} 已掛載', flush=True)
else:
    print('[對外API] 找不到 Flask app，僅初始化 DB', flush=True)
