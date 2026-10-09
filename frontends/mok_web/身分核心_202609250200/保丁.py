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
    # 2026-09-27：不再用帳號名 fallback；admin 名單一律來自 users.is_admin=1
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
        is_admin = bool(row.get('is_admin'))   # 2026-09-27：唯一真相＝DB，不看帳號名
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


def _patched_get_file_tree(path, depth=0, one_level=False):
    tree = _orig_get_file_tree(path, depth, one_level=one_level) if _orig_get_file_tree else []

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


# ============================================================
# 【身分感知】注入 v2（2026-09-27 由舊「會員系統」身分塊整併）
#   單一真相來源：member.db users.is_admin / users.plan（本補丁）
#   注入點 A：get_system_context（system prompt 末尾）
#   注入點 B：call_llm（每輪即時刷新）
#   注入點 C：process_message（決定本次請求身分）
# ============================================================
import mokagi as _mokagi


def _get_user(username):
    """相容層：一律讀本補丁的 member.db（users）。"""
    return _user_row(username)


def _get_plan(plan):
    try:
        with _db() as conn:
            r = conn.execute('SELECT * FROM plans WHERE plan=?', (plan,)).fetchone()
            return dict(r) if r else {}
    except Exception:
        return {}


def _ensure_month(user):
    """月切換：month_key 不同則把 monthly_used 歸零。"""
    try:
        u = dict(user or {})
        mk = time.strftime('%Y-%m')
        if u.get('month_key') != mk:
            with _DB_LOCK, _db() as conn:
                conn.execute('UPDATE users SET monthly_used=0, month_key=? WHERE username=?',
                             (mk, u.get('username')))
                conn.commit()
            u['monthly_used'] = 0
            u['month_key'] = mk
        return u
    except Exception:
        return user


# ============================================================
# ============ 身分感知注入 Identity Awareness Injection =========
# ------------------------------------------------------------
# 目的：讓 LLM 在每次對話都能「知道」對面是誰、什麼會員等級、
#       餘額與可用範圍，並據此調整「稱呼語氣」與「權限判斷」。
#
# 設計（不動核心，純 wrap 疊加）：
#   注入點 C  process_message   → 決定「當前請求」的身分（存進 ContextVar）
#   注入點 A  get_system_context → 在 system prompt 末尾追加身分區塊
#   注入點 B  call_llm          → 每一輪即時刷新身分區塊（餘額、等級變動同步）
#
# 開關：環境變數 MOK_IDENTITY_INJECT=0 可整體關閉。
# 管理員名單：MOK_MEMBER_ADMIN（逗號分隔，預設 admin）。
# ============================================================

import contextvars

_IDENTITY_ENABLED = (os.environ.get('MOK_IDENTITY_INJECT', '1').strip().lower()
                     not in ('0', 'false', 'no', 'off'))
_IDENTITY_MARK = '【身分感知】'
_IDENTITY_MAX_CHARS = 1500
# 2026-09-27 停用：不再用帳號名白名單（唯一真相＝member.db users.is_admin=1）
_IDENTITY_ADMIN_USERS = set()   # 永久空集合（保留變數名以免外部引用出錯）

# 每個請求獨立的當前身分（async、多線程安全）
_identity_ctx = contextvars.ContextVar('mok_member_identity', default=None)


def get_identity():
    return _identity_ctx.get()


def set_identity(username, agent_name=None):
    return _identity_ctx.set({'username': username, 'agent_name': agent_name})


def clear_identity(token=None):
    try:
        if token is not None:
            _identity_ctx.reset(token)
        else:
            _identity_ctx.set(None)
    except Exception:
        pass


# 各等級的稱呼語氣與政策（可自由調整）
_IDENTITY_PLAN_POLICY = {
    'free': {'label': '免費會員', 'tone': '親切友善、標準服務',
             'note': '僅能使用基礎 agent；高階功能與大量額度需升級。',
             'upsell': '若對方要求超額或高階功能，可提示升級 PRO / VIP。'},
    'pro': {'label': 'PRO 專業會員', 'tone': '更主動、更詳細、可提供進階協助',
            'note': '可使用全部 agent，額度較高。', 'upsell': ''},
    'vip': {'label': 'VIP 尊貴會員', 'tone': '尊榮、貼心、優先且主動',
            'note': '最高等級與額度，優先處理所有需求。', 'upsell': ''},
    'admin': {'label': '管理員', 'tone': '直接、精確、可執行系統操作',
              'note': '擁有最高權限。', 'upsell': ''},
}


def _identity_plan_key(username, plan):
    # 2026-09-27：admin 唯一真相＝member.db users.is_admin=1（不看帳號名、不看 plan 名稱）
    try:
        if (_get_user(username) or {}).get('is_admin'):
            return 'admin'
    except Exception:
        pass
    return plan if plan in _IDENTITY_PLAN_POLICY else 'free'


def _collect_identity(username, agent_name=None):
    """收集某會員的完整身分快照；非會員回傳 None。"""
    if not username:
        return None
    try:
        user = _get_user(username)
    except Exception:
        user = None
    if not user:
        return None
    try:
        user = _ensure_month(user)
    except Exception:
        pass
    plan = (user.get('plan') or 'free')
    plan_key = _identity_plan_key(username, plan)
    try:
        planrow = _get_plan(plan) or {}
    except Exception:
        planrow = {}
    try:
        agents = json.loads(planrow.get('agents') or '[]')
    except Exception:
        agents = []
    quota = planrow.get('monthly_tokens') or 0
    used = user.get('monthly_used') or 0
    remaining_month = max(0, quota - used) if quota else 0
    return {
        'username': username,
        'plan': plan,
        'plan_key': plan_key,
        'plan_label': _IDENTITY_PLAN_POLICY.get(plan_key, {}).get('label', plan),
        'plan_desc': planrow.get('desc', '') or '',
        'is_admin': plan_key == 'admin',
        'is_vip': plan_key == 'vip',
        'agents': agents,
        'agents_str': '全部 agent' if '*' in agents else ('、'.join(agents) if agents else '無'),
        'balance_tokens': user.get('balance_tokens') or 0,
        'monthly_used': used,
        'month_quota': quota,
        'remaining_month': remaining_month,
        'last_login': user.get('last_login') or 0,
        'agent_name': agent_name,
        'ts': time.time(),
    }


def _build_identity_block(ident):
    """把身分快照變成要注入 system prompt 的文字區塊。"""
    if not ident:
        return ''

    def _fmt(n):
        try:
            return f"{int(n):,}"
        except Exception:
            return str(n)

    pol = _IDENTITY_PLAN_POLICY.get(ident.get('plan_key'), {})
    desc = ident.get('plan_desc') or ''
    lines = [
        _IDENTITY_MARK,
        '（本區塊由系統自動注入，為當前對話對象的真實身分，請務必據此回應。）',
        f"- 帳號：{ident.get('username')}",
        f"- 會員等級：{ident.get('plan')}（{ident.get('plan_label')}）" + (f"｜{desc}" if desc else ''),
        f"- 身分：{'管理員' if ident.get('is_admin') else ident.get('plan_label')}",
        f"- 可用範圍：{ident.get('agents_str')}",
        f"- Token 餘額：{_fmt(ident.get('balance_tokens'))}",
        f"- 本月已用：{_fmt(ident.get('monthly_used'))} / 額度 {_fmt(ident.get('month_quota'))}（本月剩 {_fmt(ident.get('remaining_month'))}）",
        f"- 當前 agent：{ident.get('agent_name') or '（預設）'}",
    ]
    if pol:
        if pol.get('tone'):
            lines.append(f"- 互動語氣：{pol.get('tone')}")
        if pol.get('note'):
            lines.append(f"- 權限提示：{pol.get('note')}")
        if pol.get('upsell'):
            lines.append(f"- 升級提示：{pol.get('upsell')}")
    lines.append('遵守：'
                 '1) 以符合該等級的語氣稱呼對方；'
                 '2) 不得承諾超出其權限的功能，遇越權請禮貌說明並視情況提示升級；'
                 '3) 對方詢問方案/額度/身分時，直接引用上述數據，切勿編造。')
    block = '\n'.join(lines)
    if len(block) > _IDENTITY_MAX_CHARS:
        block = block[:_IDENTITY_MAX_CHARS] + '…'
    return block


# ---- 注入點 A：包裝 get_system_context（system prompt 末尾追加身分區塊） ----
if _IDENTITY_ENABLED and not getattr(_mokagi.get_system_context, '_identity_aware', False):
    _orig_get_system_context = _mokagi.get_system_context

    def _identity_aware_get_system_context(agent_name, owner, owner_time=0, context_files=None,output_dir=None, output_role=None, **kwargs):
        body = _orig_get_system_context(agent_name, owner, owner_time, context_files=context_files,
                                    output_dir=output_dir, output_role=output_role, **kwargs)
        try:
            cur = _identity_ctx.get()
            if cur and cur.get('username'):
                ident = _collect_identity(cur.get('username'), agent_name=agent_name or cur.get('agent_name'))
                block = _build_identity_block(ident)
                if block:
                    body = (body or '') + '\n\n' + block
        except Exception:
            pass
        return body

    _identity_aware_get_system_context._identity_aware = True
    _mokagi.get_system_context = _identity_aware_get_system_context


# ---- 注入點 B：包裝 call_llm（每一輪即時刷新身分區塊） ----
if _IDENTITY_ENABLED and not getattr(_mokagi.call_llm, '_identity_aware', False):
    _orig_call_llm = _mokagi.call_llm

    async def _identity_aware_call_llm(*args, **kwargs):
        try:
            cur = _identity_ctx.get()
            messages = kwargs.get('messages')
            if messages is None and len(args) >= 6:
                messages = args[5]
            if cur and cur.get('username') and messages:
                ident = _collect_identity(cur.get('username'), agent_name=cur.get('agent_name'))
                block = _build_identity_block(ident)
                if block:
                    for m in messages:
                        if m.get('role') == 'system' and isinstance(m.get('content'), str):
                            # 稚 2026-10-05：找不到 mark 時也「補上」身分塊（不再只換不補），
                            #   否則 system 版型一變（soul 版/無 soul 版切換）身分塊會整塊消失。
                            idx = m['content'].find(_IDENTITY_MARK)
                            if idx >= 0:
                                m['content'] = m['content'][:idx].rstrip() + '\n\n' + block
                            else:
                                m['content'] = m['content'].rstrip() + '\n\n' + block
                            break
        except Exception:
            pass
        return await _orig_call_llm(*args, **kwargs)

    _identity_aware_call_llm._identity_aware = True
    _mokagi.call_llm = _identity_aware_call_llm


# ---- 注入點 C：包裝 process_message（決定當前請求的身分） ----
if _IDENTITY_ENABLED and not getattr(_mokagi.process_message, '_member_identity_wrapper', False):
    # 2026-09-27：接在既有 main.process_message 之後，避免蓋掉身分核心的包裝
    _member_base_process_message = getattr(main, 'process_message', None) or _mokagi.process_message

    async def _identity_process_message(user_id, text, stream_callback=None, agent_name=None,
                                    agent_config=None, auto_mode=False, initial_prompt=None,
                                    context_files=None,
                                    output_dir=None, anon_sid=None, output_job=None,
                                    **kwargs):
        token = None
        username = None
        try:
            if session is not None:
                username = session.get('member_user')
        except Exception:
            username = None
        if not username:
            try:
                if _get_user(user_id):
                    username = user_id
            except Exception:
                username = None
        if username:
            token = _identity_ctx.set({'username': username, 'agent_name': agent_name})
        try:
            return await _member_base_process_message(
            user_id=user_id, text=text, stream_callback=stream_callback,
            agent_name=agent_name, agent_config=agent_config, auto_mode=auto_mode,
            initial_prompt=initial_prompt, context_files=context_files,
            output_dir=output_dir, anon_sid=anon_sid, output_job=output_job,
            **kwargs)
        finally:
            if token is not None:
                clear_identity(token)

    _identity_process_message._member_identity_wrapper = True
    _mokagi.process_message = _identity_process_message
    try:
        main.process_message = _identity_process_message
    except Exception:
        pass


# ---- 驗證/檢視端點 ----
if app is not None and request is not None:
    @app.route('/api/member/identity')
    def member_identity_view():
        username = None
        try:
            username = session.get('member_user')
        except Exception:
            username = None
        if not username:
            return jsonify({'logged_in': False})
        ident = _collect_identity(username)
        return jsonify({'logged_in': True, 'enabled': _IDENTITY_ENABLED,
                        'mark': _IDENTITY_MARK, 'identity': ident,
                        'block': _build_identity_block(ident)})

    @app.route('/api/member/identity/selftest')
    def member_identity_selftest():
        username = None
        try:
            username = session.get('member_user')
        except Exception:
            username = None
        if not username:
            return jsonify({'ok': False, 'reason': '未登入'}), 401
        if username not in _IDENTITY_ADMIN_USERS:
            return jsonify({'ok': False, 'reason': '僅管理員可執行'}), 403
        checks = {}
        ident = _collect_identity(username, agent_name='selftest')
        checks['collect_identity'] = bool(ident)
        block = _build_identity_block(ident)
        checks['block_has_mark'] = bool(block and _IDENTITY_MARK in block)
        checks['get_system_context_patched'] = bool(getattr(_mokagi.get_system_context, '_identity_aware', False))
        checks['call_llm_patched'] = bool(getattr(_mokagi.call_llm, '_identity_aware', False))
        checks['process_message_patched'] = bool(getattr(_mokagi.process_message, '_member_identity_wrapper', False))
        token = set_identity(username, agent_name='selftest')
        try:
            body = _mokagi.get_system_context('凜', '用戶', 0)
            checks['mark_in_system_context'] = _IDENTITY_MARK in (body or '')
        except Exception as e:
            checks['mark_in_system_context'] = False
            checks['error'] = str(e)
        finally:
            clear_identity(token)
        return jsonify({'ok': all(checks.values()), 'enabled': _IDENTITY_ENABLED, 'checks': checks})


# ---- 對外暴露給其他補丁使用 ----
if main is not None:
    for _n, _o in (('member_collect_identity', _collect_identity),
                   ('member_build_identity_block', _build_identity_block),
                   ('member_identity_ctx', _identity_ctx)):
        try:
            setattr(main, _n, _o)
        except Exception:
            pass

# 2026-09-27：把唯一真相 API 掛到 main，供其他補丁查詢（不再各自比對帳號名）
try:
    main.resolve_identity = resolve_identity
except Exception:
    pass


print(f"[會員系統] 身分感知注入已載入 | enabled={_IDENTITY_ENABLED} | mark={_IDENTITY_MARK}")
