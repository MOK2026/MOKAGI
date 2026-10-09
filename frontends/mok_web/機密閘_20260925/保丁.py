# -*- coding: utf-8 -*-
"""
機密閘補丁 v2（方案白名單版） 2026-09-28  作者：凜
====================================================
載入：由 mok_web/保丁.py 載入器自動掃描載入（目錄名 ｚ 開頭，載入順序最後）。
機制：一道 app.before_request（擋 admin-only 端點、擋非白名單房間路徑），
      並替換 /api/room_tree 視圖（依方案白名單＋永久排除清單過濾）。不改任何核心函數。

【v1 到 v2 的差別】
  v1（止血版）：非 admin 一律封鎖 /api/tree、/api/room_tree、/api/file、/api/raw、/api/repair。
  v2：非 admin（會員）改為「按方案白名單」：
      * 只可讀自己方案內 agent 的房間（plans.agents；["*"] 代表全部）
      * 永久排除：隱藏項（. 開頭）、logs、videos、資料庫檔（.db/-wal/-shm）、環境檔（env.env、.env）
      * /api/tree（全機文件樹）、/api/repair、寫入類 API（save/create/delete/mkdir/upload）、
        /api/backup、/api/eml 仍限 admin
      * 未登入訪客：房間與檔案一律拒絕

【停用】目錄改名加底線開頭（例：_ｚｚ機密閘_20260925）後重啟即恢復原狀。
"""
import os
import sys
import json
import time
import sqlite3

main = sys.modules.get('__main__')
app = getattr(main, 'app', None)

_PATCH_DIR = os.path.dirname(os.path.abspath(__file__))
_HOME_MOK = os.path.expanduser('~/.%s' % getattr(main, 'MOKAGI_home', 'mok'))
MEMBER_ROOT = os.path.join(_HOME_MOK, 'user')   # 會員房間（2026-09-30 自 agent/ 移出）


def _is_member_name(name):
    # 該名字是否為真人會員（member.db users.username）
    if not name:
        return False
    try:
        c = sqlite3.connect(MEMBER_DB, timeout=5)
        try:
            return bool(c.execute('SELECT 1 FROM users WHERE username = ?', (str(name),)).fetchone())
        finally:
            c.close()
    except Exception:
        return False


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

ADMIN_ONLY = (
    '/api/tree', '/api/repair',
    '/api/delete_file', '/api/mkdir', '/api/upload', '/api/backup', '/api/eml',
)

# 會員可寫（僅限自己房間 .mok/user/<自己帳號>/）的端點；其餘寫入端點仍限 admin
MEMBER_WRITE = ('/api/create_file', '/api/save_file')

SECRET_HINTS = (
    'env.env', '.env', '/logs/', '/videos/', 'browser_profile',
    'id_rsa', '/.git/', 'member.db',
)

EXCLUDE_NAMES = ('logs', 'videos')

_plan_cache = {}


def _conn():
    c = sqlite3.connect(MEMBER_DB, timeout=5)
    c.row_factory = sqlite3.Row
    return c


def _plan_agents(plan):
    """方案的可用 agent 白名單（plans.agents，JSON 陣列）。讀不到回 []（保守：不給）。"""
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
        print('[機密閘v2] 讀 plans 失敗: %r' % (e,), flush=True)
        arr = []
    _plan_cache[plan] = (arr, now)
    return arr


def _identity():
    try:
        fn = getattr(main, 'resolve_identity', None)
        if callable(fn):
            return fn() or {}
    except Exception:
        pass
    try:
        from flask import session
        u = session.get('member_user')
        return {'authed': bool(u), 'username': u, 'is_admin': False, 'plan': 'free'}
    except Exception:
        return {}


def _agent_ok(agent, plan, me=None):
    """agent 是否可用。

    2026-09-30 indexPage：會員「自己的房間」不該被方案白名單擋住——
    會員一律可讀自己同名房間（a01 → .mok/user/a01），
    以及 agent_owners 內自己擁有的 agent。
    """
    if not agent:
        return False
    # 2026-10-03 by 凜（L2/L3 硬禁讀）：非 admin 一律不得讀 agent 房間「命」與 user 房間「admin」
    _a = str(agent)
    if str(plan) != "admin" and _a in ("命", "admin"):
        return False
    if me and _a in _v22_own_agents(me):
        return True
    arr = _plan_agents(plan)
    if '*' in arr:
        return True
    return agent in arr


def _excluded(rel):
    """相對房間路徑是否命中永久排除項。"""
    parts = [p for p in str(rel or '').replace('\\', '/').split('/') if p not in ('', '.')]
    for p in parts:
        low = p.lower()
        if p.startswith('.'):
            return True
        if p in EXCLUDE_NAMES:
            return True
        if low.endswith('.db') or low.endswith('.db-wal') or low.endswith('.db-shm'):
            return True
        if low == 'env.env' or low.endswith('.env'):
            return True
    return False


def _room_owner(rel):
    """從 .mok/agent/（侍女）或 .mok/user/（會員）之後取出房間名；非房間路徑回 None。"""
    r = str(rel or '').replace('\\', '/')
    if r.startswith('/'):
        r = r[1:]
    for pre in ('.mok/agent/', '.mok/user/', 'home/ubuntu/.mok/agent/', 'home/ubuntu/.mok/user/'):
        if r.startswith(pre):
            rest = r[len(pre):]
            if rest:
                return rest.split('/')[0]
    return None


def _room_rel(rel):
    '''把 .mok/agent|user/<房間名>/<內層...> 轉成房間內相對路徑；非房間路徑原樣回傳。
    用途：排除清單判斷必須只看「房間內」的部分——開頭的 .mok 是隱藏前綴，
    若一併命中（. 開頭）會把所有房間檔案都誤擋成『此路徑已永久排除』。'''
    r = str(rel or '').replace(chr(92), '/').lstrip('/')
    for pre in ('.mok/agent/', '.mok/user/', 'home/ubuntu/.mok/agent/', 'home/ubuntu/.mok/user/'):
        if r.startswith(pre):
            rest = r[len(pre):]
            return rest.split('/', 1)[1] if '/' in rest else ''
    return r


def _deny(msg):
    from flask import jsonify
    return jsonify({'success': False, 'error': 'forbidden: ' + msg}), 403


def _guard():
    try:
        from flask import request
    except Exception:
        return None
    if request.method == 'OPTIONS':
        return None
    p = request.path or ''
    if not p.startswith('/api'):
        return None
    ident = _identity()
    if ident.get('is_admin'):
        return None
    low = p.lower()
    for h in SECRET_HINTS:
        if h in low:
            return _deny('機密資源已封鎖')
    if p in MEMBER_WRITE:
        # 會員可寫「自己房間」(.mok/user/<自己帳號>/)，其餘寫入端點仍限 admin
        if not ident.get('authed'):
            return _deny('請先登入')
        _me = str(ident.get('username') or '')
        try:
            _body = request.get_json(silent=True) or {}
        except Exception:
            _body = {}
        _wp = str(_body.get('path') or '').replace(chr(92), '/').lstrip('/')
        _pre = '.mok/user/' + _me + '/'
        if (not _me) or ('..' in _wp.split('/')) or not _wp.startswith(_pre):
            return _deny('僅能修改自己房間的文件')
        if _excluded(_room_rel(_wp)):
            return _deny('此路徑已永久排除')
        return None
    for a in ADMIN_ONLY:
        if p == a or p.startswith(a + '/') or p.startswith(a + '_'):
            return _deny('此功能僅限 admin')
    if p == '/api/room_tree':
        if not ident.get('authed'):
            return _deny('請先登入')
        agent = (request.args.get('agent') or '').strip()
        rel = request.args.get('path') or ''
        if not _agent_ok(agent, ident.get('plan'), ident.get('username')):
            return _deny('此 Agent 不在你的方案內')
        if _excluded(rel):
            return _deny('此路徑已永久排除')
        return None
    if p.startswith('/api/raw/') or p.startswith('/api/file/'):
        if not ident.get('authed'):
            return _deny('請先登入')
        from urllib.parse import unquote
        pre = '/api/raw/' if p.startswith('/api/raw/') else '/api/file/'
        rel = unquote(p[len(pre):])
        agent = _room_owner(rel)
        if not _agent_ok(agent, ident.get('plan'), ident.get('username')):
            return _deny('此路徑不在你的方案內')
        # 只看房間內相對路徑，避免把開頭的 .mok 誤判為機密
        if _excluded(_room_rel(rel)):
            return _deny('此路徑已永久排除')
        return None
    return None


def _v2_room_tree():
    from flask import request
    ident = _identity()
    is_admin = bool(ident.get('is_admin'))
    agent = (request.args.get('agent') or '').strip()
    rel = (request.args.get('path') or '').replace('\\', '/').strip()
    if not is_admin:
        if not ident.get('authed'):
            return {'error': 'forbidden: login required', 'tree': []}
        if not _agent_ok(agent, ident.get('plan'), ident.get('username')):
            return {'error': 'forbidden: agent not in your plan', 'tree': []}
        if _excluded(rel):
            return {'error': 'forbidden: path excluded', 'tree': []}
    agent_root = os.path.realpath(getattr(main, 'ENV_DIR', '') or '')
    base_root = os.path.realpath(MEMBER_ROOT) if _is_member_name(agent) else agent_root
    room = os.path.realpath(os.path.join(base_root, agent)) if agent else None
    if not agent or not room or room == base_root or not room.startswith(base_root + os.sep):
        return {'error': 'unknown agent', 'tree': []}
    if not os.path.isdir(room):
        return {'tree': [], 'room': agent, 'base': ('user' if _is_member_name(agent) else 'agent')}
    if rel:
        cur = os.path.realpath(os.path.join(room, rel))
        if cur != room and not cur.startswith(room + os.sep):
            return {'error': 'outside room', 'tree': []}
        if not os.path.isdir(cur):
            return {'error': 'not a folder', 'tree': []}
    else:
        cur = room
    names = []
    try:
        names = os.listdir(cur)
    except PermissionError:
        names = []
    skip = getattr(main, 'SKIP_DIRS', set())
    items = []
    for it in names:
        fp = os.path.join(cur, it)
        is_dir = os.path.isdir(fp)
        if is_dir and it in skip:
            continue
        if 'web_viewer' in it:
            continue
        rel_to_room = os.path.relpath(fp, room).replace('\\', '/')
        if (not is_admin) and _excluded(rel_to_room):
            continue
        items.append({'name': it, 'rel': rel_to_room, 'is_dir': is_dir})

    def sort_key(item):
        full = os.path.join(cur, item['name'])
        mtime = os.path.getmtime(full) if os.path.exists(full) else 0
        return (not item['is_dir'], -mtime)

    items.sort(key=sort_key)
    _base = 'user' if _is_member_name(agent) else 'agent'
    return {'tree': items, 'room': agent, 'base': _base}


if app is not None:
    try:
        app.view_functions['api_room_tree'] = _v2_room_tree
        print('[機密閘v2] /api/room_tree 已套用方案白名單與排除清單', flush=True)
    except Exception as e:
        print('[機密閘v2] 覆蓋 room_tree 失敗: %r' % (e,), flush=True)
    try:
        app.before_request(_guard)
        print('[機密閘v2] 掛載完成 db=%s admin_only=%d' % (MEMBER_DB, len(ADMIN_ONLY)), flush=True)
    except Exception as e:
        print('[機密閘v2] 掛載錯誤: %r' % (e,), flush=True)
else:
    print('[機密閘v2] 載入失敗：找不到 main.app', flush=True)


# ============================================================
# v2.1（2026-09-28 by 凜）：右側工具「日誌／監控／復活」等亦僅限 admin。
#   1) 頁面前綴追加到核心 _ADMIN_ONLY_PREFIXES（核心 guard 於請求時讀取，擴充即生效）
#   2) 日誌走 socket（subscribe_logs）：包裝覆蓋原 handler，非 admin 直接丟棄
# ============================================================
_EXTRA_PAGE_PREFIXES = (
    '/monitor', '/webTools/monitor', '/webTools/backup.html',
    '/report/mokagi說明/復活',
    # '/report/indexPage/頻道清單',  # 2026-09-30 indexPage：會員要求可見，移出 admin-only
    '/report/衍/夢',
    '/report/github/github使用',
    '/report/市場調查侍女/p3a-wallet',
)

try:
    _cur = tuple(getattr(main, '_ADMIN_ONLY_PREFIXES', ()) or ())
    _add = tuple(x for x in _EXTRA_PAGE_PREFIXES if x not in _cur)
    if _add:
        main._ADMIN_ONLY_PREFIXES = _cur + _add
    print('[機密閘v2.1] admin-only 頁面前綴新增: %s' % (','.join(_add) or '(無)',), flush=True)
except Exception as e:
    print('[機密閘v2.1] 擴充頁面前綴失敗: %r' % (e,), flush=True)


def _v21_admin_ok():
    try:
        fn = getattr(main, '_is_privileged_session', None)
        if callable(fn):
            return bool(fn())
    except Exception:
        pass
    try:
        return bool(_identity().get('is_admin'))
    except Exception:
        return False


try:
    _sio = getattr(main, 'socketio', None)
    _orig_sub = getattr(main, 'handle_subscribe_logs', None)
    if _sio is not None and callable(_orig_sub):
        def _guarded_subscribe_logs(*a, **k):
            if not _v21_admin_ok():
                print('[機密閘v2.1] 非 admin 訂閱日誌已攔下', flush=True)
                return None
            return _orig_sub(*a, **k)
        _sio.on('subscribe_logs')(_guarded_subscribe_logs)
        print('[機密閘v2.1] 日誌 socket(subscribe_logs) 已限 admin', flush=True)
    else:
        print('[機密閘v2.1] 找不到 socketio/handle_subscribe_logs，略過 socket 加鎖', flush=True)
except Exception as e:
    print('[機密閘v2.1] socket 加鎖失敗: %r' % (e,), flush=True)


# ============================================================
# v2.2（2026-09-28 by 凜）：/report/<agent>/<檔案>（Agent jobs/ 工作報告）身分閘
#   1) 未登入      → 一律 403（僅「公開層 pages + 系統已對外頁面」白名單例外）
#   2) 已登入會員  → 只可看「自己的 agent」（= 自己帳號房間 agent/<帳號>，
#                    或 agent_owners 內自己擁有的 agent）
#                    + 自己方案 plans.pages 白名單 + 系統已對外頁面
#   3) admin       → 全部
#   開關：member.db settings.report_gate_strict='1' → 嚴格模式
# ============================================================
_V22_GATE = {}


def _v22_norm(p):
    from urllib.parse import unquote as _uq
    p = _uq(p or '').strip().split('?')[0].split('#')[0]
    if not p:
        return ''
    return '/' + p.lstrip('/')


def _v22_allowed(path, pages):
    if '*' in (pages or []):
        return True
    np = _v22_norm(path)
    if not np:
        return False
    for e in pages or []:
        e = _v22_norm(e)
        if not e or e == '/':
            continue
        if np == e:
            return True
        if e.endswith('/') and np.startswith(e):
            return True
        d = e.rsplit('/', 1)[0]
        if len(d) > 1 and np.startswith(d + '/'):
            return True
    return False


def _v22_setting(key, default=None):
    try:
        with _conn() as c:
            r = c.execute('SELECT value FROM settings WHERE key=?', (key,)).fetchone()
            return r['value'] if r else default
    except Exception:
        return default


def _v22_pages_of_plan(plan):
    try:
        with _conn() as c:
            r = c.execute('SELECT pages FROM plans WHERE plan=?', (plan,)).fetchone()
        if r and r['pages']:
            v = json.loads(r['pages'])
            if isinstance(v, list):
                return [str(x) for x in v]
    except Exception:
        pass
    return []


def _v22_public_pages():
    out = []
    try:
        with _conn() as c:
            rows = c.execute('SELECT pages FROM plans WHERE COALESCE(requires_login,1)=0').fetchall()
        for r in rows:
            try:
                v = json.loads(r['pages'] or '[]')
                if isinstance(v, list):
                    out += [str(x) for x in v]
            except Exception:
                pass
    except Exception:
        pass
    return out


def _v22_config_pages():
    hit = _V22_GATE.get('cfg')
    if hit and (time.time() - hit[1]) < 60:
        return hit[0]
    out = []
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(getattr(main, '__file__', '') or '')))
        st = os.path.join(root, 'html', 'static')
        for fn, kind in (('tools.json', 'tools'), ('channels.json', 'channels')):
            fp = os.path.join(st, fn)
            if not os.path.exists(fp):
                continue
            try:
                with open(fp, 'r', encoding='utf-8') as fh:
                    d = json.load(fh)
            except Exception:
                continue
            items = d if isinstance(d, list) else d.get(kind, [])
            if not isinstance(items, list):
                continue
            for it in items:
                if not isinstance(it, dict) or it.get('adminOnly'):
                    continue
                pg = it.get('page')
                if pg and str(pg).startswith('/report'):
                    out.append(str(pg))
                hf = it.get('href')
                if hf:
                    s = str(hf)
                    if '64071181.xyz' in s:
                        s = s.split('64071181.xyz', 1)[1]
                    if s.startswith('/report'):
                        out.append(s)
    except Exception as e:
        print('[報告閘v2.2] 讀對外公佈頁面失敗: %r' % (e,), flush=True)
    _V22_GATE['cfg'] = (out, time.time())
    return out


def _v22_own_agents(uname):
    s = set()
    if uname:
        s.add(str(uname))
    try:
        with _conn() as c:
            for r in c.execute('SELECT agent FROM agent_owners WHERE owner=?', (str(uname),)):
                if r['agent']:
                    s.add(str(r['agent']))
    except Exception:
        pass
    return s


def _v22_deny(why):
    from flask import request as _R
    try:
        acc = _R.headers.get('Accept') or ''
        if 'text/html' not in acc.lower():
            return jsonify({'success': False, 'error': 'forbidden: ' + why}), 403
    except Exception:
        pass
    html = ('<html><head><meta charset="utf-8"><title>403</title></head>'
            '<body style="background:#1b1b1f;color:#ddd;font-family:sans-serif;padding:36px;line-height:1.8">'
            '<h2 style="color:#ffb3c1">403 無權瀏覽此工作報告</h2>'
            '<p>%s</p>'
            '<p><a style="color:#4ec9b0" href="/login">登入會員</a> | '
            '<a style="color:#4ec9b0" href="/member">會員中心</a> | '
            '<a style="color:#4ec9b0" href="/">回首頁</a></p>'
            '</body></html>' % why)
    return html, 403


def _v22_report_guard():
    try:
        from flask import request as _R
        if _R.method == 'OPTIONS':
            return None
        p = _R.path or ''
        if p != '/report' and not p.startswith('/report/'):
            return None
        ident = _identity()
        if ident.get('is_admin'):
            return None
        strict = str(_v22_setting('report_gate_strict', '0') or '0') == '1'
        cfg = [] if strict else _v22_config_pages()
        if not ident.get('authed'):
            pubs = [] if strict else (_v22_public_pages() + cfg)
            if _v22_allowed(p, pubs):
                return None
            print('[報告閘v2.2] 未登入攔下 %s' % p, flush=True)
            return _v22_deny('未登入不得瀏覽 Agent 工作報告，請先登入。')
        uname = ident.get('username')
        seg = p[len('/report/'):] if p.startswith('/report/') else ''
        agent = seg.split('/', 1)[0] if seg else ''
        if agent and agent in _v22_own_agents(uname):
            return None
        if not strict:
            pages = _v22_pages_of_plan(ident.get('plan') or 'free') + cfg
            if _v22_allowed(p, pages):
                return None
        if not strict and _v24_perm_ok(p, ident.get('plan')):
            return None
        print('[報告閘v2.2] 會員 %s 攔下 %s' % (uname, p), flush=True)
        return _v22_deny('只能瀏覽自己 Agent 的工作報告（或方案內已開放的頁面）。')
    except Exception as e:
        print('[報告閘v2.2] 判定失敗: %r' % (e,), flush=True)
        return None


# ============================================================
# v2.4（2026-10-02 by mokagi說明）：/report 報告閘接上「右側工具鍵權限矩陣」
#   症狀：頻道清單等頁在 tools_perm.json 已對 pro/vip 開放（前端按鈕看得到），
#         但 tools.json 仍標 adminOnly=true，報告閘的 cfg 不含它 → 會員點下去 403。
#   修法：報告閘額外讀 static/tools_perm.json 的 perms，配 tools.json／channels.json
#         的「鍵 → 頁面」對應；會員方案（free/pro/vip）該鍵為 1 即放行對應 /report 頁。
#   註：channels.json 的 href 常寫成不帶前導斜線（report/...），這裡一併正規化，
#       順帶補上 v2.2 漏掉的那批對外頁。
# ============================================================
_V24_GATE = {}


def _v24_static_dir():
    try:
        root = os.path.dirname(os.path.dirname(os.path.abspath(getattr(main, '__file__', '') or '')))
        return os.path.join(root, 'html', 'static')
    except Exception:
        return ''


def _v24_norm_href(h):
    s = str(h or '').strip()
    if '64071181.xyz' in s:
        s = s.split('64071181.xyz', 1)[1]
    if s.startswith('report/'):
        s = '/' + s
    return s


def _v24_map():
    """回傳 (pairs, perms)；pairs = [(頁面路徑, 工具鍵), ...]。60 秒快取。"""
    hit = _V24_GATE.get('map')
    if hit and (time.time() - hit[1]) < 60:
        return hit[0]
    perms = {}
    pairs = []
    st = _v24_static_dir()
    try:
        with open(os.path.join(st, 'tools_perm.json'), 'r', encoding='utf-8') as fh:
            perms = (json.load(fh) or {}).get('perms') or {}
    except Exception as e:
        print('[報告閘v2.4] 讀 tools_perm.json 失敗: %r' % (e,), flush=True)
    for fn, kind in (('tools.json', 'tools'), ('channels.json', 'channels')):
        try:
            with open(os.path.join(st, fn), 'r', encoding='utf-8') as fh:
                d = json.load(fh) or {}
        except Exception:
            continue
        items = d if isinstance(d, list) else (d.get(kind) or [])
        if not isinstance(items, list):
            continue
        for it in items:
            if not isinstance(it, dict):
                continue
            key = str(it.get('id') or it.get('key') or '')
            if not key or key not in perms:
                continue
            for cand in (it.get('page'), it.get('href')):
                pg = _v24_norm_href(cand)
                if pg.startswith('/report'):
                    pairs.append((pg, key))
    _V24_GATE['map'] = ((pairs, perms), time.time())
    return pairs, perms


def _v24_perm_ok(path, plan):
    """已登入會員（依方案 free/pro/vip）是否被工具鍵權限矩陣允許讀此 /report 頁。"""
    try:
        lvl = str(plan or 'free').strip().lower()
        if lvl not in ('free', 'pro', 'vip'):
            lvl = 'free'
        # _V24_ADMIN_PREF_GUARD：核心仍列為 admin-only 的頁面前綴一律不放行
        # （例：/report/衍/夢 在 tools_perm 對 pro/vip 開，但核心清單仍列 admin-only → 保持擋住）
        _pref = tuple(getattr(main, '_ADMIN_ONLY_PREFIXES', ()) or ())
        for _p in _pref:
            _p = str(_p or '')
            if _p and str(path).startswith(_p):
                return False
        pairs, perms = _v24_map()
        for pg, key in pairs:
            pv = perms.get(key) or {}
            if not isinstance(pv, dict) or not pv.get(lvl):
                continue
            if _v22_allowed(path, [pg]):
                print('[報告閘v2.4] 會員等級 %s 放行（鍵 %s）%s' % (lvl, key, path), flush=True)
                return True
    except Exception as e:
        print('[報告閘v2.4] 判定失敗: %r' % (e,), flush=True)
    return False


if app is not None:
    try:
        app.before_request(_v22_report_guard)
        print('[報告閘v2.2] /report 身分閘已掛載（admin=全部 / 會員=自己的 agent / 未登入=403）', flush=True)
    except Exception as e:
        print('[報告閘v2.2] 掛載失敗: %r' % (e,), flush=True)
else:
    print('[報告閘v2.2] 找不到 app，略過', flush=True)



# ==================== 機密閘 v2.3：socket 事件身分閘（2026-09-28 by 凜）====================
# 前情：v2.1 管住日誌訂閱事件；v2.2 管住網頁報告頁。
# 本次補上未登入即可經 socket 讀取他人 agent 的三個事件。
# 規則與 v2.2 一致：admin 全通；會員只通自己的 agent；未登入一律擋。
# 停用：把本補丁目錄改名，前面加底線，再重啟即恢復原狀。
_V23_SIO = getattr(main, 'socketio', None)


def _v23_identity():
    # socket 事件內的身分判定。回 dict(is_admin, authed, username)。
    is_admin = False
    uname = None
    try:
        fn = getattr(main, '_is_privileged_session', None)
        if callable(fn):
            is_admin = bool(fn())
    except Exception:
        pass
    try:
        fn = getattr(main, '_session_member_user', None)
        if callable(fn):
            _u = fn()
            uname = str(_u) if _u else None
    except Exception:
        pass
    if uname is None and not is_admin:
        try:
            ident = _identity() or {}
            uname = ident.get('username') or None
            is_admin = bool(ident.get('is_admin'))
        except Exception:
            pass
    return {'is_admin': bool(is_admin), 'authed': bool(uname), 'username': uname}


def _v23_deny(result_ev, why):
    _sid = None
    try:
        from flask import request as _R
        _sid = getattr(_R, 'sid', None)
    except Exception:
        _sid = None
    if not _sid:
        return
    try:
        _V23_SIO.emit(result_ev, {'error': 'forbidden: ' + why}, room=_sid)
    except Exception as e:
        print('[機密閘v2.3] 拒絕回覆失敗: %r' % (e,), flush=True)


def _v23_wrap(ev, orig_name, result_ev, member_ok=True):
    # 把 socket 事件包上 v2.2 同款身分閘；member_ok=False 表示僅 admin。
    orig = getattr(main, orig_name, None)
    if _V23_SIO is None or not callable(orig):
        print('[機密閘v2.3] 找不到處理函式，略過 %s' % ev, flush=True)
        return False

    def _guarded(data=None, *a, **k):
        if not isinstance(data, dict):
            data = {}
        agent = str(data.get('agent') or '')
        ident = _v23_identity()
        if ident.get('is_admin'):
            return orig(data)
        if ident.get('authed') and member_ok and agent and (agent in _v22_own_agents(ident.get('username'))):
            return orig(data)
        who = ident.get('username') or '未登入'
        print('[機密閘v2.3] 攔下 %s 對 %s 的 %s' % (who, agent or '?', ev), flush=True)
        return _v23_deny(result_ev, '只能查看自己的 Agent')

    _V23_SIO.on(ev)(_guarded)
    print('[機密閘v2.3] socket %s 已掛身分閘' % ev, flush=True)
    return True


if _V23_SIO is not None:
    _v23_ok = 0
    for _ev, _orig, _res, _mok in (
        ('get_agent_soul',     'handle_get_agent_soul',     'agent_soul_result',     True),
        ('get_agent_jobs',     'handle_get_agent_jobs',     'agent_jobs_result',     True),
        ('get_agent_logs',     'handle_get_agent_logs',     'agent_logs_result',     True),
        ('get_agent_settings', 'handle_get_agent_settings', 'agent_settings_result', False),
    ):
        try:
            if _v23_wrap(_ev, _orig, _res, _mok):
                _v23_ok += 1
        except Exception as e:
            print('[機密閘v2.3] %s 掛載失敗: %r' % (_ev, e), flush=True)
    print('[機密閘v2.3] 完成：%d/4 個 socket 事件已加鎖' % _v23_ok, flush=True)
else:
    print('[機密閘v2.3] 找不到 socketio，略過加鎖', flush=True)
