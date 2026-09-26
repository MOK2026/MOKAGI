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

# ============================================================================
# v2.0 擴充（2026-09-23）：公開層（訪客免登入）＋ 訪客額度池
# v2.1（2026-09-26）：訪客計量改為月計（guest_usage 主鍵 day→month，額度取 free 每月 Token 額度）
#                     超額不再 429 硬擋 → 本地句型樣板池引導句（HTTP 200，零 LLM）
# ----------------------------------------------------------------------------
#   1. plans 表新增 requires_login（1=需登入 / 0=公開層）、guest_quota（舊欄位，額度已改由月額度決定）
#   2. 新增 guest_usage 表：記錄未登入訪客「每月」使用次數（guest:<uuid>）
#   3. 後台 /admin/plans 新增「第四欄：公開層 / 訪客池」
#   4. 權限閘改讀公開層：未登入者只能瀏覽「公開層」方案的 pages 白名單
#   5. 額度閘：訪客月額度＝free 層級「每月 Token 額度」；用完不再 429 硬擋，
#      改回一句引導句（HTTP 200，本地句型樣板池隨機取，零 LLM），數字＝pro 每月 Token 額度
#   設計原則：未設任何公開層時，行為與 v1 完全相同（零影響）。
# ============================================================================
import time as _v2_time
import random as _random

_BLOCK_NONPUBLIC_GUEST = True      # 訪客瀏覽非公開層頁面是否擋下（False=只記錄不擋）
_QUOTA_PATHS = ('/api/chat', '/api/chat/start')

# 超額體驗（零 LLM）：本地句型樣板池隨機取句；{n}=登入贈送 Token（＝pro 層級每月額度）
_LOGIN_BONUS_TEMPLATES = (
    '本月訪客額度已用完（{used}/{quota}），登入即送 {n} Token，馬上繼續剛剛的對話。',
    '訪客免費額度見底了，登入就送 {n} Token，不用等、接著聊。',
    '{n} Token 正在等你 —— 登入立刻領取，無縫接續剛才的話題。',
    '本月訪客池額度已用完，登入即享 {n} Token 月額度。',
    '訪客額度用完了，登入送 {n} Token，解鎖完整對話與更多功能。',
    '免費訪客額度已滿載，登入立刻獲得 {n} Token，繼續未完成的對話。',
)


def _v2_ensure_columns():
    if app is None:
        return
    try:
        with _db_lock, _conn() as c:
            cols = [r[1] for r in c.execute('PRAGMA table_info(plans)').fetchall()]
            if 'requires_login' not in cols:
                c.execute('ALTER TABLE plans ADD COLUMN requires_login INTEGER NOT NULL DEFAULT 1')
            if 'guest_quota' not in cols:
                c.execute('ALTER TABLE plans ADD COLUMN guest_quota INTEGER NOT NULL DEFAULT 0')
            # guest_usage 主鍵由 day 改為 month（月計）；舊表自動遷移（同月多日彙總）
            gcols = [r[1] for r in c.execute('PRAGMA table_info(guest_usage)').fetchall()]
            if gcols and 'month' not in gcols:
                c.execute('ALTER TABLE guest_usage RENAME TO guest_usage_daybak')
                gcols = []
            if not gcols:
                c.execute(
                    'CREATE TABLE IF NOT EXISTS guest_usage ('
                    ' guest_id TEXT NOT NULL,'
                    ' month TEXT NOT NULL,'
                    ' plan TEXT,'
                    ' used INTEGER NOT NULL DEFAULT 0,'
                    ' first_ts REAL,'
                    ' last_ts REAL,'
                    ' PRIMARY KEY (guest_id, month))')
            try:
                c.execute('SELECT 1 FROM guest_usage_daybak LIMIT 1')
                c.execute('INSERT INTO guest_usage (guest_id, month, plan, used, first_ts, last_ts) '
                          "SELECT guest_id, substr(day, 1, 7), MAX(plan), SUM(used), MIN(first_ts), MAX(last_ts) "
                          'FROM guest_usage_daybak GROUP BY guest_id, substr(day, 1, 7)')
                c.execute('DROP TABLE guest_usage_daybak')
            except Exception:
                pass
            c.execute('CREATE INDEX IF NOT EXISTS idx_guest_usage_month ON guest_usage(month)')
            c.commit()
    except Exception as e:
        print('[Plans公開層] 資料表擴充失敗:', e, flush=True)


_V2_ORDER = "CASE plan WHEN 'free' THEN 0 WHEN 'pro' THEN 1 WHEN 'vip' THEN 2 ELSE 9 END, plan"


def _public_plans():
    """回傳所有「公開層」方案（requires_login=0），依 free < pro < vip 排序。"""
    try:
        with _db_lock, _conn() as c:
            rows = c.execute('SELECT * FROM plans WHERE COALESCE(requires_login,1)=0 ORDER BY ' + _V2_ORDER).fetchall()
            return [dict(r) for r in rows]
    except Exception:
        return []


def _v2_guest_id():
    """取得未登入訪客身分（由「訪客身分綁定」補丁頒發：session 或簽章 cookie）。"""
    try:
        from flask import session as _S, request as _R
        g = _S.get('mok_guest_id')
        if isinstance(g, str) and g.startswith('guest:') and 8 <= len(g) <= 48:
            return g
        c = ''
        try:
            c = (_R.cookies.get('mok_guest') or '').strip()
        except Exception:
            c = ''
        if c.startswith('guest:') and 8 <= len(c) <= 48:
            return c
    except Exception:
        pass
    return None


def _v2_today():
    return _v2_time.strftime('%Y-%m-%d')


def _v2_this_month():
    return _v2_time.strftime('%Y-%m')


def _guest_used(g, month=None):
    month = month or _v2_this_month()
    try:
        with _db_lock, _conn() as c:
            r = c.execute('SELECT used FROM guest_usage WHERE guest_id=? AND month=?', (g, month)).fetchone()
            return int(r['used']) if r else 0
    except Exception:
        return 0


def _guest_bump(g, plan=None):
    month = _v2_this_month()
    now = _v2_time.time()
    try:
        with _db_lock, _conn() as c:
            cur = c.execute('UPDATE guest_usage SET used=used+1, last_ts=?, plan=COALESCE(?, plan) '
                            'WHERE guest_id=? AND month=?', (now, plan, g, month))
            if cur.rowcount == 0:
                c.execute('INSERT OR IGNORE INTO guest_usage (guest_id, month, plan, used, first_ts, last_ts) '
                          'VALUES (?,?,?,1,?,?)', (g, month, plan, now, now))
            c.commit()
    except Exception:
        pass


def _public_quota():
    """訪客月額度：直接採用 free 層級（公開層）的「每月 Token 額度」，月計。
       free 未設為公開層或額度 <= 0 → 0（不限）。"""
    for p in _public_plans():
        if str(p.get('plan') or '').lower() == 'free':
            try:
                return max(0, int(p.get('monthly_tokens') or 0))
            except Exception:
                return 0
    return 0


def _login_bonus_tokens():
    """登入贈送 Token：固定值＝pro 層級的「每月 Token 額度」（非隨機）。"""
    try:
        with _db_lock, _conn() as c:
            r = c.execute("SELECT monthly_tokens FROM plans WHERE plan='pro'").fetchone()
            return max(0, int(r['monthly_tokens'] or 0)) if r else 0
    except Exception:
        return 0


def _guest_exceeded_message(used, quota):
    """超額引導句：本地句型樣板池隨機取一句，套上固定贈送值（零 LLM 成本）。"""
    n = _login_bonus_tokens()
    try:
        tpl = _random.choice(_LOGIN_BONUS_TEMPLATES)
    except Exception:
        tpl = _LOGIN_BONUS_TEMPLATES[0]
    try:
        return tpl.format(n=n, used=used, quota=quota)
    except Exception:
        return tpl


def _public_pages():
    out = []
    for p in _public_plans():
        out += _to_list(p.get('pages'), ['*'])
    return out


_GUEST_SKIP_PREFIX = ('/api/', '/static', '/login', '/register', '/logout',
                      '/admin', '/plans', '/member', '/favicon', '/project')


def _public_page_check():
    """未登入訪客的瀏覽權限：只放行「公開層」的 pages 白名單。"""
    if app is None:
        return None
    try:
        from flask import request as _R, session as _S
        if _R.method != 'GET':
            return None
        if _S.get('member_user'):
            return None
        path = _R.path or '/'
        low = path.lower()
        if low.startswith(_GUEST_SKIP_PREFIX):
            return None
        seg = path.rsplit('/', 1)[-1]
        if '.' in seg:
            return None
        pubs = _public_plans()
        if not pubs:
            return None            # 尚未設定公開層 → 完全維持 v1 行為
        pages = _public_pages()
        if '*' in pages or _path_allowed(path, pages):
            return None
        if not _BLOCK_NONPUBLIC_GUEST:
            return None
        inner = ('<div class="back"><a href="/login">&#8594; 登入會員</a></div>'
                 '<p style="color:#ffb3c1;font-size:15px">&#128274; 訪客僅能瀏覽公開層頁面（%s）</p>'
                 '<p class="small">登入後可依方案解鎖更多頁面。</p>' % _esc(path))
        from flask import redirect as _Rd
        from urllib.parse import quote as _q
        return _Rd('/login?next=' + _q(path))
    except Exception:
        return None


def _guest_quota_check():
    """訪客呼叫 mokagi（/api/chat*）前，檢查公開層「每月」額度（＝free 每月 Token 額度）。"""
    if app is None:
        return None
    try:
        from flask import request as _R, session as _S, jsonify as _J
        if _R.method != 'POST':
            return None
        path = _R.path or ''
        if not any(path.startswith(p) for p in _QUOTA_PATHS):
            return None
        if _S.get('member_user'):
            return None
        g = _v2_guest_id()
        if not g:
            return None
        if not _public_plans():
            return None
        quota = _public_quota()
        if quota <= 0:
            return None
        used = _guest_used(g)
        if used >= quota:
            # 超額體驗：不再 429 硬擋，改回一句引導句（HTTP 200）；數字固定＝pro 每月額度
            msg = _guest_exceeded_message(used, quota)
            bonus = _login_bonus_tokens()
            try:
                return _J({'success': False, 'error': 'guest_quota_exceeded',
                           'message': msg, 'used': used, 'quota': quota,
                           'login_bonus': bonus, 'period': 'month'})
            except Exception:
                from flask import Response as _Resp
                return _Resp(msg, status=200, mimetype='text/plain; charset=utf-8')
        return None
    except Exception:
        return None


def _guest_quota_count(resp):
    """訪客呼叫成功後，累計 guest_usage。"""
    try:
        from flask import request as _R, session as _S
        if _R.method != 'POST':
            return resp
        path = _R.path or ''
        if not any(path.startswith(p) for p in _QUOTA_PATHS):
            return resp
        if _S.get('member_user'):
            return resp
        if getattr(resp, 'status_code', 500) >= 400:
            return resp
        g = _v2_guest_id()
        if g and _public_plans():
            _guest_bump(g)
    except Exception:
        pass
    return resp


def _public_admin_panel():
    """第四欄：公開層 / 訪客池（後台 UI）。"""
    plans = _all_plans()
    if not plans:
        return ''
    csrf = _csrf_token()
    blocks = []
    for p in plans:
        name = p.get('plan')
        req = p.get('requires_login')
        req = 1 if req is None else int(req)
        try:
            q = int(p.get('guest_quota') or 0)
        except Exception:
            q = 0
        is_pub = (req == 0)
        badge = ('<span class="pill" style="border-color:#3d5a9e">公開層（免登入）</span>'
                 if is_pub else '<span class="pill">需登入</span>')
        if is_pub:
            used = '0'
            try:
                with _db_lock, _conn() as c:
                    r = c.execute('SELECT COALESCE(SUM(used),0) AS u FROM guest_usage WHERE day=?', (_v2_today(),)).fetchone()
                    used = str(int(r['u']) if r else 0)
            except Exception:
                pass
            badge += '<span class="small">　今日訪客累計 %s 次</span>' % _esc(used)
        blocks.append(
            '<form method="post" action="/admin/plans/public/%s" style="margin:10px 0;padding:12px;'
            'border:1px solid #2a3550;border-radius:10px;background:#131829">'
            '<input type="hidden" name="csrf" value="%s">'
            '<div style="font-size:15px;margin-bottom:8px"><b>%s</b>　%s</div>'
            '<label class="chip"><input type="checkbox" name="is_public" value="1"%s> 設為公開層（無需登入）</label>'
            '　訪客月額度（舊欄位；實際額度取 free 每月 Token 額度）：<input type="number" name="guest_quota" value="%d" min="0" step="1" style="width:100px"> '
            '<span class="small">（舊欄位，保留相容）</span> '
            '<button type="submit" style="margin-left:8px">儲存</button>'
            '</form>'
            % (_esc(name), csrf, _esc(name), badge, ' checked' if is_pub else '', q))
    return ('<div class="card" style="margin-top:18px">'
            '<h2>第四欄：公開層 / 訪客池</h2>'
            '<div class="sub">勾選「設為公開層」後，該層級的 pages 白名單＝未登入訪客可瀏覽的範圍；'
            '訪客額度＝free 層級「每月 Token 額度」，未登入者呼叫 mokagi 會計入 guest_usage（月計）；用完改回一句引導句（登入送 pro 月額度 Token）。</div>'
            + ''.join(blocks) + '</div>')


# 讓 /admin/plans 既有頁面自動多出第四欄（包裝原 _cards_html，不動 v1 內容）
try:
    _v1_cards_html = _cards_html

    def _cards_html(msg=None, msg_type=None):
        return _v1_cards_html(msg, msg_type) + _public_admin_panel()
except Exception:
    pass


if app is not None:
    @app.route('/admin/plans/public/<plan>', methods=['POST'])
    def plans_admin_save_public(plan):
        from flask import request as _R, session as _S, redirect as _RD
        if not _S.get('member_user') or not _is_plans_admin():
            return _RD('/login')
        if not _csrf_ok():
            return _plans_page('CSRF 驗證失敗', '<p>請回上一頁重新操作。</p>', 400)
        is_public = 1 if ((_R.form.get('is_public') or '').strip() in ('1', 'on', 'true', 'yes')) else 0
        try:
            q = int((_R.form.get('guest_quota') or '0').strip() or '0')
        except Exception:
            q = 0
        q = max(q, 0)
        try:
            with _db_lock, _conn() as c:
                c.execute('UPDATE plans SET requires_login=?, guest_quota=? WHERE plan=?',
                          (0 if is_public else 1, q, plan))
                c.commit()
        except Exception as e:
            return _plans_page('儲存失敗', '<p>%s</p>' % _esc(e), 500)
        return _RD('/admin/plans')

    app.before_request(_public_page_check)
    app.before_request(_guest_quota_check)
    app.after_request(_guest_quota_count)

_v2_ensure_columns()
print('[Plans公開層] v2.1 已載入（訪客月額度＝free 月額度 / 超額引導句樣板池）', flush=True)
