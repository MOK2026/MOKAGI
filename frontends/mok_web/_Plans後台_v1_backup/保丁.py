# -*- coding: utf-8 -*-
"""
Plans 層級後台（三欄權限）補丁 v1.0
====================================
由 mok_web/保丁.py 載入器自動掃描載入。
不覆蓋任何核心函式，僅：
  1. 擴充 member.db 的 plans 表 → pages(可瀏覽頁面) / pets(可用桌面寵物款式) 兩欄，
     與既有 agents 合成「三欄權限」。
  2. 新增後台編輯頁 /admin/plans（free/pro/vip 三欄卡片，管理員限定）。
  3. 新增前台方案展示 /plans（三欄對照）。
  4. 登入會員瀏覽 HTML 頁面前做 pages 白名單檢查（預設全放行）。
"""
import sys, os, sqlite3, json, threading
from urllib.parse import quote as _urlquote

main = sys.modules.get('__main__')
app = getattr(main, 'app', None)

_PATCH_DIR = os.path.dirname(os.path.abspath(__file__))
_MOK_DIR = os.path.dirname(os.path.dirname(os.path.dirname(_PATCH_DIR)))
AGENT_DIR = os.path.join(_MOK_DIR, 'agent')

_db_lock = threading.Lock()

def _find_member_db():
    parent = os.path.dirname(_PATCH_DIR)
    for d in sorted(os.listdir(parent)):
        p = os.path.join(parent, d, 'member.db')
        if os.path.exists(p):
            return p
    return os.path.join(_PATCH_DIR, 'member.db')

MEMBER_DB = _find_member_db()

def _conn():
    c = sqlite3.connect(MEMBER_DB, timeout=10)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c

# ============ plans 表擴充三欄（冪等） ============
def _ensure_plan_columns():
    if app is None:
        return
    with _db_lock, _conn() as c:
        tabs = [r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='plans'").fetchall()]
        if not tabs:
            # 若 plans 表尚未建立（補丁載入順序早於會員系統），先建含三欄權限的骨架，避免 ALTER 崩潰。
            c.execute("CREATE TABLE IF NOT EXISTS plans ("
                      "plan TEXT PRIMARY KEY,"
                      "agents TEXT NOT NULL DEFAULT '[]',"
                      "monthly_tokens INTEGER DEFAULT 0,"
                      "desc TEXT DEFAULT '',"
                      "pages TEXT NOT NULL DEFAULT '[\"*\"]',"
                      "pets TEXT NOT NULL DEFAULT '[\"*\"]')")
            c.commit()
        cols = [r[1] for r in c.execute('PRAGMA table_info(plans)').fetchall()]
        for col, ddl in (('pages', "ALTER TABLE plans ADD COLUMN pages TEXT DEFAULT '[]'"),
                         ('pets',  "ALTER TABLE plans ADD COLUMN pets  TEXT DEFAULT '[]'")):
            if col not in cols:
                c.execute(ddl)
        c.execute("UPDATE plans SET pages='[\"*\"]' WHERE pages='[]' OR pages IS NULL OR pages=''")
        c.execute("UPDATE plans SET pets='[\"*\"]' WHERE pets='[]' OR pets IS NULL OR pets=''")
        c.commit()

# ============ 讀取 plans ============
def _all_plans():
    try:
        with _db_lock, _conn() as c:
            rows = c.execute('SELECT * FROM plans ORDER BY CASE plan WHEN \'free\' THEN 0 WHEN \'pro\' THEN 1 WHEN \'vip\' THEN 2 ELSE 9 END, plan').fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []

def _get_plan_row(plan):
    try:
        with _db_lock, _conn() as c:
            r = c.execute('SELECT * FROM plans WHERE plan=?', (plan,)).fetchone()
            return dict(r) if r else None
    except Exception:
        return None

def _to_list(raw, default=None):
    if raw is None:
        return list(default) if default is not None else ['*']
    if isinstance(raw, list):
        return raw
    try:
        v = json.loads(raw) if isinstance(raw, str) else raw
        return v if isinstance(v, list) else [str(v)]
    except Exception:
        return ['*']

# ============ 清單來源 ============
def _avail_agents():
    out = []
    try:
        if os.path.isdir(AGENT_DIR):
            for d in sorted(os.listdir(AGENT_DIR)):
                p = os.path.join(AGENT_DIR, d)
                if os.path.isdir(p) and not d.startswith('.'):
                    out.append(d)
    except Exception:
        pass
    return out

def _avail_pets():
    seen, out = set(), []
    try:
        if os.path.isdir(AGENT_DIR):
            for d in sorted(os.listdir(AGENT_DIR)):
                p = os.path.join(AGENT_DIR, d)
                if not os.path.isdir(p) or d.startswith('.'):
                    continue
                dot = os.path.join(p, '.' + d)
                if not os.path.exists(dot):
                    continue
                try:
                    for line in open(dot, encoding='utf-8', errors='ignore'):
                        if line.startswith('MOK_L2D_MODEL='):
                            v = line.strip().split('=', 1)[1].strip()
                            if v and v not in seen:
                                seen.add(v); out.append(v)
                except Exception:
                    pass
    except Exception:
        pass
    return out

# ============ 管理員 / CSRF ============
def _admin_names():
    env = os.environ.get('ADMIN_USERNAMES') or ''
    cfg = getattr(main, 'ADMIN_USERNAMES', None) or ''
    s = (env + ',' + cfg + ',admin')
    return [x.strip() for x in s.split(',') if x.strip()]

def _is_plans_admin():
    try:
        from flask import session as _S
        return _S.get('member_user') in _admin_names()
    except Exception:
        return False

def _csrf_token():
    from flask import session as _S
    t = _S.get('plans_csrf')
    if not t:
        import secrets as _secrets
        t = _secrets.token_hex(16)
        _S['plans_csrf'] = t
    return t

def _csrf_ok():
    from flask import request as _R, session as _S
    t = _S.get('plans_csrf')
    f = _R.form.get('csrf') or _R.headers.get('X-CSRF-Token') or ''
    return bool(t and f and t == f)

# ============ 頁面框架（深色後台風格） ============
_CSS = """
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:-apple-system,'PingFang TC','Microsoft JhengHei',sans-serif;background:#0f1220;color:#e8e8f0;min-height:100vh;padding:22px}
a{color:#7aa2ff;text-decoration:none}
a:hover{text-decoration:underline}
.top{display:flex;justify-content:space-between;align-items:center;flex-wrap:wrap;gap:10px;margin-bottom:16px}
h1{font-size:22px;color:#fff}
.sub{color:#8b90a5;font-size:13px;margin-top:4px}
.nav a{margin-right:14px;font-size:13px;color:#8b90a5}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(300px,1fr));gap:16px;margin-top:14px}
.card{background:#171b2e;border:1px solid #262c48;border-radius:12px;padding:18px}
.card.vip{border-color:#c084fc66;box-shadow:0 0 22px #c084fc22}
.card h2{font-size:17px;margin-bottom:4px}
.tag{display:inline-block;font-size:11px;padding:2px 9px;border-radius:20px;margin-left:6px;vertical-align:middle}
.tag.free{background:#334155;color:#cbd5e1}
.tag.pro{background:#1e3a8a;color:#93c5fd}
.tag.vip{background:#581c87;color:#e9d5ff}
label.f{display:block;font-size:12px;color:#9aa0b8;margin:14px 0 5px}
input[type=text],input[type=number],textarea{width:100%;background:#0b0e1c;border:1px solid #2a3050;color:#e8e8f0;border-radius:8px;padding:8px 10px;font-size:13px;outline:none}
input:focus,textarea:focus{border-color:#7aa2ff}
textarea{min-height:64px;resize:vertical;font-family:inherit}
.checks{display:flex;flex-wrap:wrap;gap:6px;max-height:170px;overflow:auto;padding:4px 0}
.chip{display:inline-flex;align-items:center;gap:5px;background:#0e1122;border:1px solid #2a3050;border-radius:20px;padding:4px 11px;font-size:12px;cursor:pointer;user-select:none}
.chip input{accent-color:#7aa2ff}
.chip.all{border-color:#3d5a9e}
.btn{background:#7aa2ff;color:#0b0e1c;border:0;border-radius:9px;padding:9px 18px;font-size:14px;font-weight:700;cursor:pointer;margin-top:16px;width:100%}
.btn:hover{background:#93b4ff}
.msg{padding:9px 14px;border-radius:9px;font-size:13px;margin-bottom:12px}
.ok{background:#14532d;color:#bbf7d0}
.err{background:#7f1d1d;color:#fecaca}
.small{color:#8b90a5;font-size:12px;line-height:1.7}
table.pt{width:100%;border-collapse:collapse;font-size:13px}
table.pt td,table.pt th{border-bottom:1px solid #232846;padding:9px 8px;text-align:left;vertical-align:top}
table.pt th{color:#9aa0b8;font-weight:600}
.pill{display:inline-block;background:#1e2440;border:1px solid #2f3660;border-radius:14px;padding:1px 8px;font-size:11px;margin:1px 2px}
.back{margin-bottom:10px;font-size:13px}
"""

def _plans_page(title, inner, status=200):
    from flask import render_template_string, Response
    html = ('<!doctype html><html lang="zh-TW"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width,initial-scale=1">'
            '<title>%s</title><style>%s</style></head><body>%s</body></html>'
            % (title, _CSS, inner))
    return Response(html, status=status)

def _esc(v):
    if v is None:
        return ''
    return str(v).replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;').replace('"', '&quot;')

# ============ 後台編輯頁 /admin/plans ============
def _perm_checks(plan, agents_avail, pets_avail):
    """產生三欄權限區塊的 HTML（checkbox）。plan: dict"""
    ag = _to_list(plan.get('agents'), ['*'])
    pg = _to_list(plan.get('pages'), ['*'])
    pt = _to_list(plan.get('pets'), ['*'])
    csrf = _csrf_token()
    ag_all = '*' in ag
    pt_all = '*' in pt
    pg_all = '*' in pg
    # 可用 Agent 複選
    if ag_all:
        ag_html = '<label class="chip all"><input type="checkbox" name="agents" value="__all__" checked onchange="plansSyncAll(this)">全部 Agent(*)</label>'
    else:
        ag_html = '<label class="chip all"><input type="checkbox" name="agents" value="__all__" onchange="plansSyncAll(this)">全部 Agent(*)</label>'
    for a in agents_avail:
        checked = ' checked' if (ag_all or a in ag) else ''
        ag_html += '<label class="chip"><input type="checkbox" name="agents" value="%s"%s onchange="plansSyncOne(this)">%s</label>' % (_esc(a), checked, _esc(a))
    # 寵物款式複選
    if pt_all:
        pet_html = '<label class="chip all"><input type="checkbox" name="pets" value="__all__" checked onchange="plansSyncAll(this)">全部款式(*)</label>'
    else:
        pet_html = '<label class="chip all"><input type="checkbox" name="pets" value="__all__" onchange="plansSyncAll(this)">全部款式(*)</label>'
    for p in pets_avail:
        checked = ' checked' if (pt_all or p in pt) else ''
        pet_html += '<label class="chip"><input type="checkbox" name="pets" value="%s"%s onchange="plansSyncOne(this)">%s</label>' % (_esc(p), checked, _esc(p))
    if not pets_avail:
        pet_html += '<span class="small">（目前未偵測到已設定的寵物款式，可在下方直接填寫路徑）</span>'
    # 頁面白名單 textarea（每行一路徑；勾「全部」= 不限制）
    pg_txt = '\n'.join(p for p in pg if p != '*') if not pg_all else ''
    pg_all_cb = ' checked' if pg_all else ''
    pages_html = ('<label class="chip all" style="margin-bottom:8px"><input type="checkbox" name="pages_all" value="1"%s '
                  'onchange="document.getElementById(\'pages_txt_%s\').disabled=this.checked">全部頁面可瀏覽(*)</label>'
                  % (pg_all_cb, _esc(plan['plan'])))
    pages_html += ('<textarea id="pages_txt_%s" name="pages_txt" placeholder="每行一個路徑，例如：&#10;/&#10;/member&#10;/some/page"%s>%s</textarea>'
                   % (_esc(plan['plan']), ' disabled' if pg_all else '', _esc(pg_txt)))
    inner_js = """
<script>
function plansSyncAll(cb){var box=cb.closest('.card');box.querySelectorAll('input[name="agents"]').forEach(function(x){if(x!==cb)x.checked=false});if(cb.name==='pets'){box.querySelectorAll('input[name="pets"]').forEach(function(x){if(x!==cb)x.checked=false});}}
function plansSyncOne(cb){if(cb.checked){var box=cb.closest('.card');box.querySelectorAll('input[name="'+cb.name+'"][value="__all__"]').forEach(function(x){x.checked=false});}}
</script>"""
    return ag_html, pet_html, pages_html, inner_js

def _cards_html(msg=None, msg_type=None):
    plans = _all_plans()
    agents_avail = _avail_agents()
    pets_avail = _avail_pets()
    if not plans:
        return '<div class="err msg">plans 表無資料</div>'
    msg_html = ''
    if msg:
        msg_html = '<div class="msg %s">%s</div>' % (msg_type or 'ok', _esc(msg))
    cards = []
    for i, plan in enumerate(plans):
        pname = plan['plan']
        tagcls = pname if pname in ('free', 'pro', 'vip') else 'free'
        ag_html, pet_html, pages_html, js = _perm_checks(plan, agents_avail, pets_avail)
        agents_meta = ''
        cards.append(
            '<div class="card%s"><h2>%s<span class="tag %s">%s</span></h2>'
            '<div class="sub">層級權限編輯</div>'
            '<form method="post" action="/admin/plans/%s">'
            '<input type="hidden" name="csrf" value="%s">'
            '<label class="f">每月 Token 額度</label>'
            '<input type="number" name="monthly_tokens" value="%s" min="0" step="1">'
            '<label class="f">方案說明</label>'
            '<textarea name="desc">%s</textarea>'
            '<label class="f">① 可用 Agent（agents）</label><div class="checks">%s</div>'
            '<label class="f">② 可瀏覽頁面（pages）</label>%s'
            '<label class="f">③ 可用桌面寵物款式（pets）</label><div class="checks">%s</div>'
            '%s<button class="btn" type="submit">儲存 %s 層級</button>'
            '</form></div>'
            % (_esc(pname), _esc(pname.upper()), _esc(tagcls), _esc(pname),
               _esc(pname), _esc(_csrf_token()), _esc(plan.get('monthly_tokens') or 0),
               _esc(plan.get('desc') or ''), ag_html, pages_html, pet_html, js, _esc(pname)))
        agents_meta = ''
    header = ('<div class="top"><div><h1>層級方案管理（plans）</h1>'
              '<div class="sub">三欄權限：①可用 Agent　②可瀏覽頁面　③可用寵物款式（* = 全部）</div></div>'
              '<div class="nav"><a href="/admin/member">← 會員管理</a><a href="/plans" target="_blank">檢視前台方案 ↗</a></div></div>')
    return header + msg_html + '<div class="grid">' + ''.join(cards) + '</div>'

def _plans_route():
    from flask import request, session, redirect
    if not session.get('member_user'):
        return redirect('/login')
    if not _is_plans_admin():
        return _plans_page('無權限', '<div class="back"><a href="/member">← 返回會員中心</a></div>'
                           '<p style="color:#ffb3c1;font-size:15px">⛔ 您不是管理員，無法編輯層級方案。</p>')
    # GET 顯示編輯頁
    return _plans_page('層級方案管理', _cards_html())

if app is not None:
    @app.route('/admin/plans')
    def plans_admin_index():
        return _plans_route()

    @app.route('/admin/plans/<plan>', methods=['POST'])
    def plans_admin_save(plan):
        from flask import request, session, redirect
        if not session.get('member_user') or not _is_plans_admin():
            return redirect('/login')
        if not _csrf_ok():
            return _plans_page('層級方案管理', _cards_html('CSRF 驗證失敗，請重試', 'err'))
        try:
            agents = request.form.getlist('agents')
            pets = request.form.getlist('pets')
            pages_all = request.form.get('pages_all')
            if '__all__' in agents or not agents:
                agents = ['*']
            if '__all__' in pets or not pets:
                pets = ['*']
            if pages_all:
                pages = ['*']
            else:
                pages = [ln.strip() for ln in (request.form.get('pages_txt') or '').splitlines() if ln.strip()]
                if not pages:
                    pages = ['*']
            monthly = int(request.form.get('monthly_tokens') or 0)
            desc = (request.form.get('desc') or '').strip()
            with _db_lock, _conn() as c:
                cur = c.execute('UPDATE plans SET agents=?, pages=?, pets=?, monthly_tokens=?, desc=? WHERE plan=?',
                                (json.dumps(agents, ensure_ascii=False),
                                 json.dumps(pages, ensure_ascii=False),
                                 json.dumps(pets, ensure_ascii=False),
                                 monthly, desc, plan))
                c.commit()
                if cur.rowcount == 0:
                    # 若該層級不存在則建立
                    c.execute('INSERT OR IGNORE INTO plans (plan, agents, pages, pets, monthly_tokens, desc) VALUES (?,?,?,?,?,?)',
                              (plan, json.dumps(agents, ensure_ascii=False),
                               json.dumps(pages, ensure_ascii=False),
                               json.dumps(pets, ensure_ascii=False), monthly, desc))
                    c.commit()
            return _plans_page('層級方案管理', _cards_html('已儲存 %s 層級 ✅' % plan, 'ok'))
        except Exception as e:
            return _plans_page('層級方案管理', _cards_html('儲存失敗：%s' % e, 'err'))

# ============ 前台方案展示 /plans（三欄對照） ============
def _plans_public():
    plans = _all_plans()
    if not plans:
        return _plans_page('方案', '<p>暫無方案資料。</p>')
    cards = []
    for plan in plans:
        pname = plan['plan']
        tagcls = pname if pname in ('free', 'pro', 'vip') else 'free'
        ag = _to_list(plan.get('agents'), ['*'])
        pg = _to_list(plan.get('pages'), ['*'])
        pt = _to_list(plan.get('pets'), ['*'])
        ag_txt = '全部 Agent' if '*' in ag else ('、'.join(ag) if ag else '無')
        pg_txt = '全部頁面' if '*' in pg else ('、'.join(pg) if pg else '無')
        pt_txt = '全部款式' if '*' in pt else ('、'.join(pt) if pt else '無')
        mt = int(plan.get('monthly_tokens') or 0)
        mt_txt = ('%s / 月' % format(mt, ',')) if mt else '—'
        hl = ' vip' if pname == 'vip' else ''
        cards.append(
            '<div class="card%s"><h2>%s<span class="tag %s">%s</span></h2>'
            '<div style="font-size:26px;color:#fff;margin:10px 0 2px">%s</div>'
            '<div class="sub" style="margin-bottom:12px">%s</div>'
            '<table class="pt"><tr><th>可用 Agent</th><td>%s</td></tr>'
            '<tr><th>可瀏覽頁面</th><td>%s</td></tr>'
            '<tr><th>桌面寵物款式</th><td>%s</td></tr></table>'
            % (_esc(pname), _esc(pname.upper()), _esc(tagcls), _esc(pname),
               mt_txt, _esc(plan.get('desc') or ''),
               _fmt_pills(ag), _fmt_pills(pg), _fmt_pills(pt)))
    inner = ('<div class="top"><div><h1>選擇你的方案</h1>'
             '<div class="sub">三種層級，隨時可於會員中心查看權限</div></div></div>'
             + '<div class="grid">' + ''.join(cards) + '</div>'
             + '<p class="small" style="margin-top:16px"><a href="/register">註冊</a>　·　'
             '<a href="/login">登入</a>　·　<a href="/member">會員中心</a></p>')
    return _plans_page('方案', inner)

def _fmt_pills(items):
    if not items:
        return '<span class="small">—</span>'
    if items == ['*']:
        return '<span class="pill" style="border-color:#3d5a9e">全部</span>'
    return ''.join('<span class="pill">%s</span>' % _esc(x) for x in items)

# ============ 頁面權限檢查（預設全放行） ============
_PUBLIC_PREFIX = ('/api/', '/static', '/login', '/register', '/logout',
                  '/admin/', '/plans', '/member', '/favicon')

def _path_allowed(path, pages):
    if not pages or '*' in pages:
        return True
    for p in pages:
        if not p:
            continue
        if path == p:
            return True
        if len(p) > 1 and p.endswith('/') and path.startswith(p):
            return True
    return False

def _page_access_check():
    """登入會員瀏覽受保護 HTML 頁面時，依其層級 pages 白名單檢查。"""
    if app is None:
        return None
    try:
        from flask import request as _R, session as _S
        if _R.method != 'GET':
            return None
        uname = _S.get('member_user')
        if not uname:
            return None
        path = _R.path or '/'
        low = path.lower()
        if low.startswith(_PUBLIC_PREFIX):
            return None
        # 帶副檔名視為靜態資源
        seg = path.rsplit('/', 1)[-1]
        if '.' in seg:
            return None
        plan = _get_plan_row_by_user(uname)
        if not plan:
            return None
        pages = _to_list(plan.get('pages'), ['*'])
        if _path_allowed(path, pages):
            return None
        inner = ('<div class="back"><a href="/member">← 返回會員中心</a></div>'
                 '<p style="color:#ffb3c1;font-size:15px">🔒 您的 %s 層級無法瀏覽此頁面（%s）</p>'
                 '<p class="small">如需更多頁面權限，請升級方案或聯絡管理員。</p>' % (_esc(uname_plan_label(uname)), _esc(path)))
        return _plans_page('無權限', inner, 403)
    except Exception:
        return None

def _get_plan_row_by_user(uname):
    try:
        with _db_lock, _conn() as c:
            r = c.execute('SELECT plan FROM users WHERE username=?', (uname,)).fetchone()
            if not r:
                return None
            p = c.execute('SELECT * FROM plans WHERE plan=?', (r['plan'],)).fetchone()
            return dict(p) if p else None
    except Exception:
        return None

def uname_plan_label(uname):
    try:
        with _db_lock, _conn() as c:
            r = c.execute('SELECT plan FROM users WHERE username=?', (uname,)).fetchone()
            return (r['plan'] if r else 'unknown')
    except Exception:
        return 'unknown'

# ============ 註冊路由 & 初始化 ============
if app is not None:
    @app.route('/plans')
    def plans_public_page():
        return _plans_public()

    app.before_request(_page_access_check)

# 啟動時擴充 plans 表
_ensure_plan_columns()
print('[Plans後台] ✅ plans 表已擴充三欄權限（agents / pages / pets）')
