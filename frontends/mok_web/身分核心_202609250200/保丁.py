# -*- coding: utf-8 -*-
"""
統一會員身分核心 v1  (2026-09-25 by 凜)
=========================================
由 mok_web/保丁.py 載入器自動掃描載入（目錄名以「身」開頭，排在「會員系統」之後載入）。

【目的】建立「唯一真相來源」的身分解析層，任何前端/路徑皆可問：
        admin / vip / pro / free / guest，並附名稱與權限。
        同時把 admin 判定從「程式碼寫死帳號名」改為 member.db 驅動
        （名字可改、驗證不寫死於程式碼）。

【做什麼】
 1. 擴充 member.db 結構（冪等）：
      users + display_name, is_admin
      plans + tools, skills            （方案可用工具/skill，Step 7）
      settings(key,value)              （全域設定，如主 admin 顯示名）
      agent_owners(agent, owner)       （agent 擁有者，供會員自有 agent 用）
 2. 種子：admin/root -> is_admin=1；display_name 預設「主人」。
 3. resolve_identity()：回 {authed,username,role,plan,is_admin,display_name,tenant,guest_id}
 4. 覆蓋 main._is_privileged_session、main._can_read_all → 改為 DB 驅動。
 5. 覆蓋 /api/whoami（回 role/plan/display_name）。
 6. 新增 GET /api/identity/resolve（任何前端統一查身分）。
 7. 新增 GET/POST /api/admin/identity（僅 admin）：改主 admin 名、授權/取消 admin。
 8. 方案文案 seed（Step 8）：admin/free/pro/vip 定義寫入 plans.desc。
 9. wrapper main.process_message：主 admin 使用時，所有 agent 一律稱呼「主人」。

【安全】admin 判定 = users.is_admin=1（DB）；密碼維持 hash；改名只動 display_name。
【停用】目錄改名加底線開頭，重啟 web 即停用。
"""
import os, sys, glob, json, time, sqlite3, threading

main = sys.modules.get('__main__')
if main is None:
    raise RuntimeError('身分核心需由 mok_web 保丁載入器載入')

try:
    from flask import session, request, jsonify
except Exception:
    session = request = jsonify = None

app = getattr(main, 'app', None)


# ---------- 定位 member.db（會員系統補丁的資料庫） ----------
def _find_member_db():
    _here = os.path.dirname(os.path.abspath(__file__))   # .../mok_web/<本補丁>/
    _web = os.path.dirname(_here)                        # .../mok_web/
    cands = glob.glob(os.path.join(_web, '會員系統_*', 'member.db'))
    if cands:
        return sorted(cands)[-1]
    return os.path.join(_web, '會員系統_202608311340', 'member.db')


MEMBER_DB = _find_member_db()
_DB_LOCK = threading.Lock()


def _db():
    conn = sqlite3.connect(MEMBER_DB, timeout=10)
    conn.row_factory = sqlite3.Row
    return conn


def _ensure_schema():
    with _DB_LOCK:
        with _db() as conn:
            ucols = {r[1] for r in conn.execute("PRAGMA table_info(users)")}
            if 'display_name' not in ucols:
                conn.execute("ALTER TABLE users ADD COLUMN display_name TEXT DEFAULT ''")
            if 'is_admin' not in ucols:
                conn.execute("ALTER TABLE users ADD COLUMN is_admin INTEGER DEFAULT 0")
            pcols = {r[1] for r in conn.execute("PRAGMA table_info(plans)")}
            if 'tools' not in pcols:
                conn.execute("ALTER TABLE plans ADD COLUMN tools TEXT DEFAULT '[]'")
            if 'skills' not in pcols:
                conn.execute("ALTER TABLE plans ADD COLUMN skills TEXT DEFAULT '[]'")
            conn.execute("CREATE TABLE IF NOT EXISTS settings(key TEXT PRIMARY KEY, value TEXT)")
            conn.execute("CREATE TABLE IF NOT EXISTS agent_owners("
                         "agent TEXT PRIMARY KEY, owner TEXT NOT NULL, created_ts REAL)")
            n = conn.execute("SELECT COUNT(*) AS c FROM users WHERE is_admin=1").fetchone()['c']
            if not n:
                conn.execute("UPDATE users SET is_admin=1 WHERE username IN ('admin','root')")
            conn.execute("UPDATE users SET display_name='主人' "
                         "WHERE is_admin=1 AND (display_name IS NULL OR display_name='')")


def _seed_plan_meta():
    """方案文案（Step 8）與可用工具/skill 預設（Step 7）。僅在未經人手修改時覆寫。"""
    _defs = {
        'free': '免費版（無帳號免費體驗）：限額使用；可搜尋網頁、聊天、讀文件、生成語音、生成圖（免費 skill）；不增減主機內容、不修改系統。',
        'pro':  '專業版（註冊會員）：可建立自己的 agent（僅自己可用），計 token 扣費，每月贈送額度；可在自己房間生成/修改/刪除一切（除 .agent，須走 admin confirm）。',
        'vip':  '尊貴版（有充值、有可消費 token 餘額）：可上架自己的 agent 供他人使用，token 收入分佣 50%（上架需付上架費）。',
    }
    _old = {'free': '免費版：基礎 agent 可用',
            'pro':  '專業版：全部 agent 可用',
            'vip':  '尊貴版：全部 agent + 高額度'}
    with _DB_LOCK, _db() as conn:
        for _p, _d in _defs.items():
            row = conn.execute("SELECT desc FROM plans WHERE plan=?", (_p,)).fetchone()
            if row is not None and (row['desc'] in (None, '', _old.get(_p))):
                conn.execute("UPDATE plans SET desc=? WHERE plan=?", (_d, _p))
        if not conn.execute("SELECT 1 FROM plans WHERE plan='admin'").fetchone():
            conn.execute(
                "INSERT INTO plans(plan,agents,monthly_tokens,desc,requires_login,guest_quota) "
                "VALUES('admin','[\"*\"]',0,?,1,0)",
                ('系統管理（admin）：mokagi 最高權限，可管理全部 agent、工具、方案與系統設定；不受方案額度限制。',))
        conn.execute("UPDATE plans SET tools=? WHERE plan='free' AND (tools IS NULL OR tools='[]')",
                     ('["web_search","memory","tts","vision"]',))
        conn.execute("UPDATE plans SET tools=? WHERE plan IN ('pro','vip','admin') AND (tools IS NULL OR tools='[]')",
                     ('["*"]',))
        conn.execute("UPDATE plans SET skills=? WHERE plan='free' AND (skills IS NULL OR skills='[]')",
                     ('["免費生成圖"]',))
        conn.execute("UPDATE plans SET skills=? WHERE plan IN ('pro','vip','admin') AND (skills IS NULL OR skills='[]')",
                     ('["*"]',))


try:
    _ensure_schema()
    print("[身分核心] schema OK ->", MEMBER_DB)
except Exception as _e:
    print("[身分核心] schema 失敗:", _e)

try:
    _seed_plan_meta()
    print("[身分核心] 方案文案/工具 seed OK")
except Exception as _e:
    print("[身分核心] 方案 seed 失敗:", _e)


# ---------- 設定讀寫 ----------
def _setting(key, default=None):
    try:
        with _db() as conn:
            r = conn.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
            return r['value'] if r else default
    except Exception:
        return default


def _set_setting(key, value):
    try:
        with _DB_LOCK:
            with _db() as conn:
                conn.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                             "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, str(value)))
    except Exception as _e:
        print("[身分核心] set_setting 失敗:", _e)


# ---------- 查詢 ----------
def _admin_usernames():
    names = set()
    try:
        with _db() as conn:
            for r in conn.execute("SELECT username FROM users WHERE is_admin=1"):
                names.add(str(r['username']))
    except Exception:
        pass
    names.update({'admin', 'root'})   # 相容舊行為
    return names


def _user_row(username):
    try:
        with _db() as conn:
            r = conn.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            return dict(r) if r else None
    except Exception:
        return None


def _session_username():
    if session is None:
        return None
    try:
        return session.get('member_user')
    except Exception:
        return None


def _session_guest():
    if session is None:
        return None
    try:
        return session.get('mok_guest_id')
    except Exception:
        return None


# ---------- 唯一真相來源 ----------
def resolve_identity(data=None):
    """解析本次請求身分。回傳 dict（任何前端可用）。"""
    uname = _session_username()
    if uname:
        row = _user_row(uname) or {}
        is_admin = bool(row.get('is_admin')) or str(uname) in ('admin', 'root')
        plan = row.get('plan') or 'free'
        role = 'admin' if is_admin else plan
        disp = row.get('display_name') or ('主人' if is_admin else uname)
        return {
            'authed': True, 'username': uname, 'role': role, 'plan': plan,
            'is_admin': is_admin, 'display_name': disp,
            'tenant': uname, 'guest_id': None,
            'balance': row.get('balance_tokens'),
        }
    gid = _session_guest()
    if not gid and isinstance(data, dict):
        _c = data.get('user_id')
        if _c and (str(_c).startswith('web_guest_') or str(_c).startswith('guest:')):
            gid = str(_c)
    return {
        'authed': False, 'username': None,
        'role': 'guest' if gid else 'anon', 'plan': 'free',
        'is_admin': False, 'display_name': '訪客',
        'tenant': gid, 'guest_id': gid, 'balance': None,
    }


# ---------- 覆蓋核心判定 ----------
def _is_privileged_session():
    try:
        return bool(resolve_identity().get('is_admin'))
    except Exception:
        return False


def _can_read_all(tenant):
    if tenant is None:
        return False
    return str(tenant) in _admin_usernames()


try:
    main._is_privileged_session = _is_privileged_session
    main._can_read_all = _can_read_all
except Exception as _e:
    print("[身分核心] 覆蓋核心判定失敗:", _e)


# ---------- 覆蓋 /api/whoami ----------
def api_whoami():
    i = resolve_identity()
    return jsonify({
        'logged_in': i['authed'], 'username': i['username'],
        'is_admin': i['is_admin'], 'role': i['role'], 'plan': i['plan'],
        'display_name': i['display_name'], 'balance': i['balance'],
    })


try:
    if app and 'api_whoami' in getattr(app, 'view_functions', {}):
        app.view_functions['api_whoami'] = api_whoami
        print("[身分核心] /api/whoami 已改為 DB 驅動")
except Exception as _e:
    print("[身分核心] 覆蓋 whoami 失敗:", _e)


# ---------- 新增路由 ----------
if app:
    try:
        @app.route('/api/identity/resolve', methods=['GET'])
        def _api_identity_resolve():
            return jsonify({'success': True, 'identity': resolve_identity()})

        @app.route('/api/admin/identity', methods=['GET'])
        def _api_admin_identity_get():
            if not _is_privileged_session():
                return jsonify({'success': False, 'error': 'forbidden: admin only'}), 403
            with _db() as conn:
                rows = [dict(r) for r in conn.execute(
                    "SELECT username, plan, is_admin, display_name FROM users ORDER BY id")]
            return jsonify({'success': True,
                            'admins': sorted(_admin_usernames()),
                            'main_admin_display_name': _setting('main_admin_display_name', '主人'),
                            'users': rows})

        @app.route('/api/admin/identity', methods=['POST'])
        def _api_admin_identity_post():
            if not _is_privileged_session():
                return jsonify({'success': False, 'error': 'forbidden: admin only'}), 403
            data = request.get_json(silent=True) or {}
            action = (data.get('action') or '').strip()
            if action == 'rename_main':
                nm = (data.get('display_name') or '').strip()
                if not nm:
                    return jsonify({'success': False, 'error': '顯示名不可為空'}), 400
                _set_setting('main_admin_display_name', nm)
                me = _session_username()
                if me:
                    with _DB_LOCK, _db() as conn:
                        conn.execute("UPDATE users SET display_name=? WHERE username=?", (nm, me))
                return jsonify({'success': True, 'main_admin_display_name': nm})
            if action in ('grant', 'revoke'):
                target = (data.get('username') or '').strip()
                if not target:
                    return jsonify({'success': False, 'error': '需指定 username'}), 400
                val = 1 if action == 'grant' else 0
                with _DB_LOCK, _db() as conn:
                    cur = conn.execute("UPDATE users SET is_admin=? WHERE username=?", (val, target))
                    if cur.rowcount == 0:
                        return jsonify({'success': False, 'error': '找不到用戶 ' + target}), 404
                return jsonify({'success': True, 'username': target, 'is_admin': bool(val)})
            return jsonify({'success': False, 'error': '未知 action'}), 400
        print("[身分核心] 路由 /api/identity/resolve、/api/admin/identity 已註冊")
    except Exception as _e:
        print("[身分核心] 註冊路由失敗:", _e)


# ---------- 主 admin → 所有 agent 稱「主人」 ----------
_prev_pm = getattr(main, 'process_message', None)


async def _pm_wrapper(*args, **kwargs):
    try:
        ident = resolve_identity()
        cfg = kwargs.get('agent_config')
        if cfg is None and len(args) >= 5 and isinstance(args[4], dict):
            cfg = args[4]
        if ident.get('is_admin') and isinstance(cfg, dict):
            disp = ident.get('display_name') or _setting('main_admin_display_name', '主人') or '主人'
            cfg['MOK_ADMIN_NAME'] = disp
            cfg.setdefault('MOK_AUTO_APPROVE_ADMIN', '1')
    except Exception as _e:
        print("[身分核心] 主人稱呼 wrapper 警告:", _e)
    if _prev_pm is None:
        raise RuntimeError('process_message 不存在')
    return await _prev_pm(*args, **kwargs)


if _prev_pm is not None:
    try:
        main.process_message = _pm_wrapper
        print("[身分核心] process_message 已包裝（主 admin 一律稱主人）")
    except Exception as _e:
        print("[身分核心] 包裝 process_message 失敗:", _e)


# ---------- Step 4：Web 檔案端點 .agent 保護 ----------
_orig_get_file_content = getattr(main, 'get_file_content', None)


def _patched_get_file_content(sub_path):
    try:
        _bn = os.path.basename(os.path.normpath(sub_path))
        if _bn.startswith('.') and not _is_privileged_session():
            return {"error": "forbidden: 機密設定檔（如 .agent）僅限管理員"}, 403
    except Exception:
        pass
    if _orig_get_file_content is None:
        return {"error": "not available"}, 500
    return _orig_get_file_content(sub_path)


if _orig_get_file_content is not None:
    try:
        main.get_file_content = _patched_get_file_content
        if app and 'get_file_content' in getattr(app, 'view_functions', {}):
            app.view_functions['get_file_content'] = _patched_get_file_content
        print("[身分核心] /api/file 已加 .agent 保護")
    except Exception as _e:
        print("[身分核心] 覆蓋 get_file_content 失敗:", _e)

_orig_get_file_tree = getattr(main, 'get_file_tree', None)


def _patched_get_file_tree(path, depth=0):
    tree = _orig_get_file_tree(path, depth) if _orig_get_file_tree else []

    def _strip(nodes):
        out = []
        for n in nodes:
            try:
                if (not n.get('is_dir')) and str(n.get('name', '')).startswith('.'):
                    continue
                if isinstance(n.get('children'), list):
                    n['children'] = _strip(n['children'])
            except Exception:
                pass
            out.append(n)
        return out
    try:
        return _strip(tree)
    except Exception:
        return tree


if _orig_get_file_tree is not None:
    try:
        main.get_file_tree = _patched_get_file_tree
        print("[身分核心] 文件樹已隱藏 dotfile（.agent 等）")
    except Exception as _e:
        print("[身分核心] 覆蓋 get_file_tree 失敗:", _e)


# ---------- Step 5/6 基礎：記錄 agent 擁有者 ----------
_orig_create_agent = getattr(main, "create_agent", None)


def _patched_create_agent(*a, **k):
    _resp = _orig_create_agent(*a, **k) if _orig_create_agent else (
        {"status": "error", "message": "unavailable"}, 500)
    try:
        body, code = (_resp if isinstance(_resp, tuple) else (_resp, 200))
        if code == 200 and isinstance(body, dict) and body.get("status") == "ok":
            data = request.get_json(silent=True) or {}
            name = (data.get("name") or "").strip()
            ident = resolve_identity()
            owner = ident.get("username") or ident.get("guest_id") or ""
            if name and owner:
                with _DB_LOCK, _db() as conn:
                    conn.execute(
                        "INSERT INTO agent_owners(agent, owner, created_ts) VALUES(?,?,?) "
                        "ON CONFLICT(agent) DO UPDATE SET owner=excluded.owner",
                        (name, owner, time.time()))
                print("[身分核心] 記錄 agent owner: %s -> %s" % (name, owner))
    except Exception as _e:
        print("[身分核心] 記錄 agent owner 失敗:", _e)
    return _resp


if _orig_create_agent is not None:
    try:
        main.create_agent = _patched_create_agent
        if app and "create_agent" in getattr(app, "view_functions", {}):
            app.view_functions["create_agent"] = _patched_create_agent
        print("[身分核心] create_agent 已包裝（記錄擁有者）")
    except Exception as _e:
        print("[身分核心] 包裝 create_agent 失敗:", _e)


def _agent_owner(agent):
    try:
        with _db() as conn:
            r = conn.execute("SELECT owner, created_ts FROM agent_owners WHERE agent=?",
                             (agent,)).fetchone()
            return dict(r) if r else None
    except Exception:
        return None


if app:
    try:
        @app.route("/api/agent/owner/<path:agent>", methods=["GET"])
        def _api_agent_owner(agent):
            ident = resolve_identity()
            rec = _agent_owner(agent)
            if rec is None:
                return jsonify({"success": False, "error": "no owner record"}), 404
            if not (ident.get("is_admin") or ident.get("username") == rec.get("owner")):
                return jsonify({"success": False, "error": "forbidden"}), 403
            return jsonify({"success": True, "agent": agent, "owner": rec.get("owner")})
        print("[身分核心] 路由 /api/agent/owner 已註冊")
    except Exception as _e:
        print("[身分核心] 註冊 agent/owner 失敗:", _e)

print("[身分核心] 載入完成")
