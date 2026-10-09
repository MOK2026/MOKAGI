# -*- coding: utf-8 -*-
"""
API 敏感端點閘 v1  (2026-10-03)  作者：API平台工程師
====================================================
載入：mok_web/保丁.py 載入器自動掃描（目錄名 9 個 ｚ 開頭，載入順序最後）。

【為什麼要這道閘】
  ｚｚ機密閘 是一張「黑名單」：
        for a in ADMIN_ONLY: ...
        return None          ← 最後一行＝放行
  而 z三級權限閘 與 ｚｚｚｚｚｚｚｚ公開白名單閘 都把「/api」整段併入 ALWAYS_OPEN
  （註解寫「各後台自行判斷」）。
  → 結果：任何「沒被列進黑名單」的 API 端點＝預設放行，連未登入訪客都能打。

  本補丁不改核心、不覆蓋既有補丁，只做兩件事：
    1) before_request：敏感端點分三層擋
         ADMIN_ONLY_API : 非 admin → 403
         AUTH_ONLY_API  : 未登入   → 403
         PLAN_GUARD_API : agent 必須在呼叫者方案白名單內（訪客＝free 方案）
    2) 覆蓋 models 視圖（/api/models）：非 admin 時遮蔽 url（不讓上游/內網端點外洩）

【與既有補丁分工】
  ｚｚ機密閘 的黑名單（/api/tree、/api/repair、/api/backup、/api/eml、/api/mkdir、/api/upload、/api/delete_file）仍由它處理；本閘只補它漏掉的部分。

【停用】目錄改名加底線開頭即失效（下次服務載入生效）。
"""
import os
import sys
import json
import time
import sqlite3

main = sys.modules.get('__main__')
app = getattr(main, 'app', None)

_PATCH_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_member_db():
    parent = os.path.dirname(_PATCH_DIR)
    try:
        for d in sorted(os.listdir(parent)):
            p = os.path.join(parent, d, 'member.db')
            if os.path.exists(p):
                return p
    except Exception:
        pass
    return os.path.join(parent, 'member.db')


MEMBER_DB = _find_member_db()

# ==== 規則表 ================================================================
# 系統級：非 admin 一律 403
ADMIN_ONLY_API = (
    '/api/set_model',       # 會改全站模型設定檔（MOK_CURRENT_MODEL）
    '/api/agent_voice',     # 可寫任一 agent 的設定檔與外觀/原聲.mp3
    '/api/agent_pets',      # 回傳全體 agent 名冊（無前端呼叫者）
    '/api/clear_all_jobs',  # 清空任務
    '/api/pm2panel',        # 轉發到 127.0.0.1:8331 的行程面板 API
)

# 需要登入（訪客不可）
AUTH_ONLY_API = (
    '/api/tools',           # 工具清單＝系統能力盤點
    '/api/create_folder',   # 建立目錄
    '/api/create_agent',    # 建立 agent 目錄 + 設定檔（2026-10-05：已登入會員可建、訪客不可）
)

# agent 必須在呼叫者方案白名單內（訪客視為 free 方案）
PLAN_GUARD_API = (
    '/api/set_env',         # 切換後端「當前 agent 設定」→ 跨方案切換＝越權＋費用風險
)

_plan_cache = {}


def _conn():
    c = sqlite3.connect(MEMBER_DB, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def _plan_agents(plan):
    """方案的 agent 白名單（plans.agents，JSON 陣列）。讀不到回 []（保守：不給）。"""
    if not plan:
        return []
    now = time.time()
    hit = _plan_cache.get(plan)
    if hit and (now - hit[1]) < 30:
        return hit[0]
    arr = []
    try:
        with _conn() as c:
            r = c.execute('SELECT agents FROM plans WHERE plan=?', (plan,)).fetchone()
        if r and r['agents']:
            v = json.loads(r['agents'])
            if isinstance(v, list):
                arr = [str(x) for x in v]
    except Exception as e:
        print('[API閘] 讀 plans 失敗: %r' % (e,), flush=True)
        arr = []
    _plan_cache[plan] = (arr, now)
    return arr


def _session_user():
    try:
        from flask import session
        return session.get('member_user') or None
    except Exception:
        return None


def _user_plan(username):
    if not username:
        return 'free'
    try:
        with _conn() as c:
            r = c.execute('SELECT plan FROM users WHERE username=?', (username,)).fetchone()
        return (r['plan'] or 'free') if r else 'free'
    except Exception:
        return 'free'


def _is_admin():
    """唯一真相：核心 main._is_privileged_session()；退化為 member.db users.is_admin。"""
    fn = getattr(main, '_is_privileged_session', None)
    if callable(fn):
        try:
            return bool(fn())
        except Exception:
            pass
    u = _session_user()
    if not u:
        return False
    try:
        with _conn() as c:
            r = c.execute('SELECT is_admin FROM users WHERE username=?', (u,)).fetchone()
        return bool(r and r['is_admin'])
    except Exception:
        return False


def _is_authed():
    return bool(_session_user()) or _is_admin()


def _own_agents(me):
    """會員「自創」的 agent（agent_owners）→ 不受方案白名單限制。
    2026-10-05 by mokagi說明：與三級權限閘／機密閘／會員系統同一真相。"""
    if not me:
        return set()
    try:
        with _conn() as c:
            return set(r['agent'] for r in c.execute(
                'SELECT agent FROM agent_owners WHERE owner=?', (str(me),)) if r['agent'])
    except Exception:
        return set()


def _agent_ok(agent, plan, me):
    if not agent:
        return False
    if me and str(agent) == str(me):          # 會員自己的同名房間 agent
        return True
    if me and str(agent) in _own_agents(me):  # 會員自創 agent（預設可用）
        return True
    arr = _plan_agents(plan)
    if '*' in arr:
        return True
    return agent in arr


def _path_hit(p, a):
    return p == a or p.startswith(a + '/') or p.startswith(a + '_')


def _guard():
    try:
        from flask import request, jsonify
    except Exception:
        return None
    if request.method == 'OPTIONS':
        return None
    p = request.path or ''
    if not p.startswith('/api'):
        return None

    for a in ADMIN_ONLY_API:
        if _path_hit(p, a):
            if not _is_admin():
                print('[API閘] 擋下非 admin：%s' % p, flush=True)
                return jsonify({'success': False, 'error': 'forbidden: admin only'}), 403
            return None

    for a in AUTH_ONLY_API:
        if _path_hit(p, a):
            if not _is_authed():
                print('[API閘] 擋下未登入：%s' % p, flush=True)
                return jsonify({'success': False, 'error': 'forbidden: login required'}), 403
            return None

    for a in PLAN_GUARD_API:
        if _path_hit(p, a):
            if _is_admin():
                return None
            try:
                body = request.get_json(silent=True) or {}
            except Exception:
                body = {}
            agent = str(body.get('agent') or '').strip()
            if not agent:
                agent = str(body.get('filename') or '').strip().lstrip('.')
            me = _session_user()
            plan = _user_plan(me)
            if not _agent_ok(agent, plan, me):
                print('[API閘] 擋下越方案 set_env：user=%s plan=%s agent=%s'
                      % (me, plan, agent), flush=True)
                return jsonify({'success': False,
                                'error': 'forbidden: agent 不在你的方案內'}), 403
            return None

    return None


def _wrap_models():
    """非 admin 時把 models 回應的 url 欄位拿掉（只留 name）。"""
    orig = (app.view_functions or {}).get('get_models')
    if not callable(orig):
        print('[API閘] 找不到 get_models，略過 models 遮蔽', flush=True)
        return

    def _wrapped(*a, **k):
        r = orig(*a, **k)
        if _is_admin():
            return r
        try:
            d = r.get_json() if hasattr(r, 'get_json') else r
            if isinstance(d, dict) and isinstance(d.get('models'), list):
                d = dict(d)
                d['models'] = [{'name': (m or {}).get('name')}
                               for m in d['models'] if isinstance(m, dict)]
            return d
        except Exception:
            return r

    app.view_functions['get_models'] = _wrapped
    print('[API閘] models 已對非 admin 遮蔽 url', flush=True)


if app is not None:
    try:
        app.before_request(_guard)
        print('[API閘] 掛載完成 admin_only=%d auth_only=%d plan_guard=%d db=%s'
              % (len(ADMIN_ONLY_API), len(AUTH_ONLY_API), len(PLAN_GUARD_API), MEMBER_DB),
              flush=True)
    except Exception as e:
        print('[API閘] 掛載錯誤: %r' % (e,), flush=True)
    try:
        _wrap_models()
    except Exception as e:
        print('[API閘] models 遮蔽失敗: %r' % (e,), flush=True)
else:
    print('[API閘] 載入失敗：找不到 main.app', flush=True)
