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

def _agent_group_of(name):
    """2026-10-01 凜：讀 .<agent> 的 MOK_AGENT_group，回傳所屬組別（無則空字串）。"""
    try:
        dot = os.path.join(AGENT_DIR, name, '.' + name)
        if os.path.isfile(dot):
            for line in open(dot, encoding='utf-8', errors='ignore'):
                if line.startswith('MOK_AGENT_group='):
                    return line.strip().split('=', 1)[1].strip()
    except Exception:
        pass
    return ''

def _agent_groups():
    """2026-10-01 凜：以 MOK_AGENT_group 分組，回 {組別: [agent,...]}（僅含有組別者）。"""
    g = {}
    for _a in _avail_agents():
        _g = _agent_group_of(_a)
        if _g:
            g.setdefault(_g, []).append(_a)
    return g

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
            '<meta name="viewport" content="width=device-width,initial-scale=1"><meta name="color-scheme" content="dark">'
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
        _ag = _agent_group_of(a)
        _gattr = (' data-group="%s"' % _esc(_ag)) if _ag else ''
        ag_html += '<label class="chip"><input type="checkbox" name="agents" value="%s"%s%s onchange="plansSyncOne(this)">%s</label>' % (_esc(a), _gattr, checked, _esc(a))
    # 組別（2026-10-01 凜）：同 MOK_AGENT_group 一鍵全選／全不選（不提交，僅前端連動；與下方單選並存）
    _groups = _agent_groups()
    if _groups:
        ag_html += '<span class="small" style="flex-basis:100%;margin-top:6px">組別（同一組一次全選／全不選，可再個別微調＝組內試用）</span>'
        for _gn in sorted(_groups):
            _members = _groups[_gn]
            _gchk = ' checked' if (ag_all or all((_m in ag) for _m in _members)) else ''
            ag_html += ('<label class="chip grp" style="border-color:#c084fc" title="%s"><input type="checkbox" data-grp="%s"%s onchange="plansToggleGroup(this)">組別 ▸ %s（%d）</label>'
                        % (_esc('、'.join(_members)), _esc(_gn), _gchk, _esc(_gn), len(_members)))
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
function plansSyncAll(cb){var box=cb.closest('.card');box.querySelectorAll('input[name="agents"]').forEach(function(x){if(x!==cb)x.checked=false});if(cb.name==='pets'){box.querySelectorAll('input[name="pets"]').forEach(function(x){if(x!==cb)x.checked=false});}plansSyncGroupState(box);}
function plansSyncOne(cb){if(cb.checked){var box=cb.closest('.card');box.querySelectorAll('input[name="'+cb.name+'"][value="__all__"]').forEach(function(x){x.checked=false});}plansSyncGroupState(cb.closest('.card'));}
function plansToggleAll(btn,on){var card=btn.closest('.card');card.querySelectorAll('input[name="agents"]').forEach(function(x){x.checked=on;});plansSyncGroupState(card);}
function plansToggleGroup(cb){var card=cb.closest('.card'),g=cb.getAttribute('data-grp');card.querySelectorAll('input[name="agents"][data-group="'+g+'"]').forEach(function(x){x.checked=cb.checked;});if(cb.checked){card.querySelectorAll('input[name="agents"][value="__all__"]').forEach(function(x){x.checked=false;});}plansSyncGroupState(card);}
function plansSyncGroupState(card){if(!card)return;var allcb=card.querySelector('input[name="agents"][value="__all__"]');var allon=!!(allcb&&allcb.checked);card.querySelectorAll('input[data-grp]').forEach(function(gc){var g=gc.getAttribute('data-grp');var ms=card.querySelectorAll('input[name="agents"][data-group="'+g+'"]');var n=ms.length,c=0;ms.forEach(function(x){if(x.checked)c++;});if(allon){gc.checked=true;gc.indeterminate=false;}else{gc.checked=(n>0&&c===n);gc.indeterminate=(c>0&&c<n);}});}
try{document.querySelectorAll('.card').forEach(plansSyncGroupState);}catch(e){}
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
            '<label class="f">① 可用 Agent（agents）</label>'
            '<div style="display:flex;gap:6px;margin:2px 0 4px">'
            '<button type="button" onclick="plansToggleAll(this,true)" style="font-size:11px;padding:3px 10px;border-radius:14px;border:1px solid #2a3050;background:#0e1122;color:#cfd3e6;cursor:pointer">全選</button>'
            '<button type="button" onclick="plansToggleAll(this,false)" style="font-size:11px;padding:3px 10px;border-radius:14px;border:1px solid #2a3050;background:#0e1122;color:#cfd3e6;cursor:pointer">全不選</button>'
            '</div>'
            '<div class="checks">%s</div>'
            '<label class="f">② 可瀏覽頁面（pages）</label>%s'
            '<label class="f">③ 可用桌面寵物款式（pets）</label><div class="checks">%s</div>'
            '%s<button class="btn" type="submit">儲存 %s 層級</button>'
            '</form></div>'
            % (_esc(pname), _esc(pname.upper()), _esc(tagcls), _esc(pname),
               _esc(pname), _esc(_csrf_token()), _esc(plan.get('monthly_tokens') or 0),
               _esc(plan.get('desc') or ''), ag_html, pages_html, pet_html, js, _esc(pname)))
        agents_meta = ''
    header = ('<div class="top"><div><h1>層級方案管理（plans）</h1><div class="sub">管理員：admin＋凜</div>'
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
            if '__all__' in agents:
                agents = ['*']
            elif not agents:
                agents = []          # 凜 2026-09-30：全不選＝不開放任何共用 Agent（會員自創 Agent 不受限）
            if '__all__' in pets or not pets:
                pets = ['*']
            if pages_all:
                pages = ['*']
            else:
                from urllib.parse import unquote as _uq
                pages = [_uq(ln).strip() for ln in (request.form.get('pages_txt') or '').splitlines() if ln.strip()]
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
# 受保護頁面前綴：一律走白名單（不因 .html 等副檔名被當靜態資源放行）
_GATED_PAGES = ('/report',)

def _is_gated(path):
    return any(path == g or path.startswith(g + '/') for g in _GATED_PAGES)

_PUBLIC_PREFIX = ('/api/', '/static', '/login', '/register', '/logout',
                  '/admin/', '/plans', '/member', '/favicon')

def _norm_page(p):
    from urllib.parse import unquote as _uq
    p = _uq(p or '').strip().split('?')[0].split('#')[0]
    if not p:
        return ''
    return '/' + p.lstrip('/')

def _path_allowed(path, pages):
    if '*' in (pages or []):
        return True
    if not pages:
        return False                      # 未設定白名單 → 一律不可看
    np = _norm_page(path)
    for p in pages:
        e = _norm_page(p)
        if not e or e == '/':
            continue
        if np == e:
            return True
        if e.endswith('/') and np.startswith(e):
            return True                   # 目錄白名單
        if not e.endswith('/'):
            parent = e.rsplit('/', 1)[0] + '/'
            if np.startswith(parent):
                return True               # 同資料夾資源（css/js/圖/json）
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
        # 受保護頁面一律走白名單（不因 .html 等副檔名被當靜態資源放行）
        seg = path.rsplit('/', 1)[-1]
        if '.' in seg and not _is_gated(path):
            return None
        plan = _get_plan_row_by_user(uname) or {}
        pages = _to_list(plan.get('pages'), [])   # 未設定（空）＝ 全部不可看
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
# 【2026-09-27】匿名（未登入）專屬額度：每 sid 固定值，不套用 free 的月額度。
# 主機指令（2026-09-30 更新）：訪客額度改與 FREE 月計同額 → 每訪客身分每月 50000 tokens。
_GUEST_SID_QUOTA = 50000
# 【2026-10-02 凜｜主人交付】free／訪客硬擋：月 50k token（不變）＋ 每小時最多 3 次。
#   超額＝直接拒絕（完全不呼叫 LLM），回一句寫死的「註冊 / 登入會員」引導句。
_GUEST_HOUR_LIMIT = 3
_GUEST_BLOCK_MSG = {
    'hour': '⏳ 免費體驗每小時最多 3 次對話，本小時的次數已用完。\n'
            '請稍後再試，或立即「註冊 / 登入會員」繼續使用。',
    'month': '🔒 免費訪客額度（50,000 Token）已用完。\n'
             '請「註冊 / 登入會員」繼續未完成的對話。',
}


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
            # 2026-09-27：guest_quota 舊欄位停用；統一以 monthly_tokens 為單一額度來源（不再建立）
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
                    ' hour_key TEXT,'
                    ' hour_used INTEGER NOT NULL DEFAULT 0,'
                    ' PRIMARY KEY (guest_id, month))')
            try:
                c.execute('SELECT 1 FROM guest_usage_daybak LIMIT 1')
                c.execute('INSERT INTO guest_usage (guest_id, month, plan, used, first_ts, last_ts) '
                          "SELECT guest_id, substr(day, 1, 7), MAX(plan), SUM(used), MIN(first_ts), MAX(last_ts) "
                          'FROM guest_usage_daybak GROUP BY guest_id, substr(day, 1, 7)')
                c.execute('DROP TABLE guest_usage_daybak')
            except Exception:
                pass
            # 【2026-10-02】既有表補上「每小時次數」欄（hour_key='YYYY-MM-DDTHH'）
            try:
                _hc = [r[1] for r in c.execute('PRAGMA table_info(guest_usage)').fetchall()]
                if 'hour_key' not in _hc:
                    c.execute('ALTER TABLE guest_usage ADD COLUMN hour_key TEXT')
                if 'hour_used' not in _hc:
                    c.execute('ALTER TABLE guest_usage ADD COLUMN hour_used INTEGER NOT NULL DEFAULT 0')
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
    """取得未登入訪客身分。
    2026-09-27：改以「匿名產物沙盒」(ｚｚｚ匿名產物) 的 sid 作為單一身分來源 → guest:<sid>。
    找不到沙盒 sid 時才回落舊來源（session / 簽章 cookie）。"""
    try:
        from flask import g as _G
        _sid = getattr(_G, '_anon_sid', None) or getattr(_G, '_anon_new_sid', None)
        if _sid and 8 <= len('guest:' + str(_sid)) <= 48:
            return 'guest:' + str(_sid)
    except Exception:
        pass
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


def _v2_this_hour():
    """目前小時鍵 'YYYY-MM-DDTHH'（訪客每小時次數 bucket）。"""
    return _v2_time.strftime('%Y-%m-%dT%H')


def _guest_hour_used(g, hour_key=None):
    """本小時已用次數；跨小時自動視為 0。"""
    hour_key = hour_key or _v2_this_hour()
    try:
        with _db_lock, _conn() as c:
            r = c.execute('SELECT hour_key, hour_used FROM guest_usage WHERE guest_id=? AND month=?',
                          (g, _v2_this_month())).fetchone()
            if not r:
                return 0
            if str(r['hour_key'] or '') != hour_key:
                return 0
            return int(r['hour_used'] or 0)
    except Exception:
        return 0


def _guest_hour_bump(g, hour_key=None, plan=None):
    """通過檢查後，本小時次數 +1（換小時自動歸零重計）。"""
    hour_key = hour_key or _v2_this_hour()
    month = _v2_this_month()
    now = _v2_time.time()
    try:
        with _db_lock, _conn() as c:
            r = c.execute('SELECT hour_key FROM guest_usage WHERE guest_id=? AND month=?',
                          (g, month)).fetchone()
            if not r:
                c.execute('INSERT OR IGNORE INTO guest_usage '
                          '(guest_id, month, plan, used, first_ts, last_ts, hour_key, hour_used) '
                          'VALUES (?,?,?,?,?,?,?,?)', (g, month, plan, 0, now, now, hour_key, 1))
            elif str(r['hour_key'] or '') == hour_key:
                c.execute('UPDATE guest_usage SET hour_used=hour_used+1, last_ts=? '
                          'WHERE guest_id=? AND month=?', (now, g, month))
            else:
                c.execute('UPDATE guest_usage SET hour_key=?, hour_used=1, last_ts=? '
                          'WHERE guest_id=? AND month=?', (hour_key, now, g, month))
            c.commit()
    except Exception:
        pass


def _guest_bump(g, plan=None, tokens=0):
    """累加訪客用量（單位：token）。
    2026-09-27：改為累加「實際 token 數」——由 _guest_quota_count 讀 token_usage 增量後傳入，
    不再每次 +1（次數）。tokens<=0 時僅更新 last_ts。"""
    month = _v2_this_month()
    now = _v2_time.time()
    try:
        add = int(tokens) if tokens and int(tokens) > 0 else 0
        with _db_lock, _conn() as c:
            cur = c.execute('UPDATE guest_usage SET used=used+?, last_ts=?, plan=COALESCE(?, plan) '
                            'WHERE guest_id=? AND month=?', (add, now, plan, g, month))
            if cur.rowcount == 0:
                c.execute('INSERT OR IGNORE INTO guest_usage (guest_id, month, plan, used, first_ts, last_ts) '
                          'VALUES (?,?,?,?,?,?)', (g, month, plan, add, now, now))
            c.commit()
    except Exception:
        pass


def _guest_tokens_since(gid):
    """讀 token_usage 增量：本次請求期間（g._qt0 起）該訪客新增的 total_tokens 總和。"""
    try:
        from flask import g as _G
        t0 = float(getattr(_G, '_qt0', 0) or 0)
        if not t0 or not gid:
            return 0
        import sqlite3 as _sq
        db = os.path.expanduser('~/.mok/.memory/chat_history.db')
        with _sq.connect('file:%s?mode=ro' % db, uri=True, timeout=10) as c:
            r = c.execute('SELECT COALESCE(SUM(total_tokens),0) FROM token_usage '
                          'WHERE user_id=? AND timestamp>=?', (gid, t0 - 1)).fetchone()
            return int(r[0] or 0)
    except Exception:
        return 0


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


def _guest_block_response(reason, used, limit):
    """【2026-10-02】硬擋（直接拒絕、零 LLM）：回一句寫死的「註冊 / 登入會員」引導句。
    - /api/chat/start → 回 JSON（無 sse_session_id；新前端直接顯示，舊前端自動回退舊串流）
    - /api/chat       → 回單輪 SSE（iteration_start / reply / done），前端以既有管線渲染"""
    import json as _json
    from flask import request as _R, jsonify as _J, Response as _Resp
    msg = _GUEST_BLOCK_MSG.get(reason) or _GUEST_BLOCK_MSG.get('month')
    agent = ''
    try:
        _b = _R.get_json(silent=True) or {}
        agent = _b.get('agent') or ''
    except Exception:
        agent = ''
    if (_R.path or '').startswith('/api/chat/start'):
        return _J({'success': False, 'error': 'guest_quota_exceeded', 'blocked': True,
                   'need_login': True, 'reason': reason, 'message': msg,
                   'used': used, 'quota': limit,
                   'login_url': '/login', 'register_url': '/register'})

    def _ev(_o):
        return 'data: ' + _json.dumps(_o, ensure_ascii=False) + '\n\n'

    body = (_ev({'type': 'iteration_start', 'agent': agent, 'iteration': 1})
            + _ev({'type': 'reply', 'agent': agent, 'content': msg})
            + _ev({'type': 'done', 'agent': agent, 'final_reply': msg}))
    return _Resp(body, status=200, mimetype='text/event-stream')


def _guest_quota_check():
    """訪客呼叫 mokagi（/api/chat*）前，檢查「匿名每 sid 固定額度」。
    2026-09-27：由 free 每月額度改為匿名專屬固定值（_GUEST_SID_QUOTA）；
    2026-09-30：額度調整為 50000（與 FREE 月計同額），說明頁 03 同步。"""
    if app is None:
        return None
    try:
        from flask import request as _R, session as _S, jsonify as _J
        try:
            from flask import g as _G
            _G._qt0 = _v2_time.time()
        except Exception:
            pass
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
        quota = _GUEST_SID_QUOTA
        if quota <= 0:
            return None
        # 【2026-10-02】hard block：月額度（50k token）用完 或 本小時已滿 3 次 → 直接拒絕（零 LLM）
        used = _guest_used(g)
        if quota > 0 and used >= quota:
            return _guest_block_response('month', used, quota)
        _hkey = _v2_this_hour()
        _hused = _guest_hour_used(g, _hkey)
        if _GUEST_HOUR_LIMIT > 0 and _hused >= _GUEST_HOUR_LIMIT:
            return _guest_block_response('hour', _hused, _GUEST_HOUR_LIMIT)
        # 通過檢查：記一次「本小時次數」（月 token 由 after_request 依實際用量累加）
        _guest_hour_bump(g, _hkey)
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
            _guest_bump(g, None, _guest_tokens_since(g))
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
                c.execute('UPDATE plans SET requires_login=? WHERE plan=?',
                          (0 if is_public else 1, plan))
                c.commit()
        except Exception as e:
            return _plans_page('儲存失敗', '<p>%s</p>' % _esc(e), 500)
        return _RD('/admin/plans')

    app.before_request(_public_page_check)
    app.before_request(_guest_quota_check)
    app.after_request(_guest_quota_count)

_v2_ensure_columns()
print('[Plans公開層] v2.1 已載入（訪客月額度＝free 月額度 / 超額引導句樣板池）', flush=True)



# ██████████████████████████████████████████████████████████████████████████
# v3.0 UI 改版（2026-10-01 春｜主人交付）
# ──────────────────────────────────────────────────────────────────────────
# 問題：FREE/PRO/VIP 三張卡片全塞在同一頁；「可用 Agent」把「組別捷徑」與
#       「個別 agent」混雜在同一個 flex 容器裡 → 手機上擠成一團、看不清楚。
# 改法：① 上方分頁列（FREE / PRO / VIP 各自一個分頁，一次只顯示一個層級）
#       ②「可用 Agent」拆成兩塊：組別捷徑（大卡、一行一組）＋ 個別 Agent
#         （整列清單、一行一個、可即時搜尋）
#       ③ 每個權限一個獨立區塊、觸控目標 >=44px、儲存鈕黏在底部。
# 安全：只覆寫呈現層三個符號（_CSS / _perm_checks / _cards_html），
#       表單欄位名與後端 plans_admin_save 完全一致（csrf / monthly_tokens /
#       desc / agents / pets / pages_all / pages_txt），
#       不動資料庫、不動權限判定、不動任何路由。
# 還原：用 editlock 備份覆蓋即可回到舊版。
# ██████████████████████████████████████████████████████████████████████████

_CSS = """
*{box-sizing:border-box;margin:0;padding:0;-webkit-tap-highlight-color:transparent}
:root{--bg:#0f1220;--card:#171b2e;--line:#272d49;--line2:#2a3050;--txt:#e8e8f0;--dim:#8b90a5;--acc:#7aa2ff}
body{font-family:-apple-system,BlinkMacSystemFont,'PingFang TC','Noto Sans TC','Microsoft JhengHei',sans-serif;
     background:var(--bg);color:var(--txt);min-height:100vh;padding:14px 12px 96px;font-size:15px;line-height:1.55}
a{color:var(--acc);text-decoration:none}
a:hover{text-decoration:underline}
h1{font-size:20px;color:#fff;letter-spacing:.5px}
.sub{color:var(--dim);font-size:12.5px;margin-top:4px}
.small{font-size:12px;color:var(--dim)}
.top{display:flex;justify-content:space-between;align-items:flex-start;gap:10px;flex-wrap:wrap;margin-bottom:12px}
.nav{display:flex;gap:8px;flex-wrap:wrap}
.nav a{font-size:12.5px;background:#1a1f36;border:1px solid var(--line);border-radius:999px;padding:6px 12px;color:#b9c0d8}
.tabs{position:sticky;top:0;z-index:30;display:flex;gap:6px;background:var(--bg);
      padding:8px 0 10px;margin-bottom:6px;border-bottom:1px solid var(--line)}
.tab{flex:1;appearance:none;border:1px solid var(--line);background:#151a2d;color:#aeb6cf;
     border-radius:12px;padding:11px 6px;font-size:14px;font-weight:700;letter-spacing:.6px;cursor:pointer;transition:.15s}
.tab.active{color:#fff;background:#1d2540;box-shadow:0 0 0 1px #7aa2ff55 inset}
.tab.free.active{border-color:#8aa0c8}
.tab.pro.active{border-color:#60a5fa}
.tab.vip.active{border-color:#c084fc;box-shadow:0 0 18px #c084fc33}
.panel{display:none}
.panel.show{display:block;animation:fade .18s ease}
@keyframes fade{from{opacity:0;transform:translateY(4px)}to{opacity:1;transform:none}}
.card{background:var(--card);border:1px solid var(--line);border-radius:14px;padding:14px 13px;margin-bottom:14px}
.card-vip{border-color:#c084fc55}
.hero{display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;margin-bottom:10px}
.hero .nm{font-size:19px;font-weight:800;color:#fff}
.tag{display:inline-block;font-size:11px;padding:2px 9px;border-radius:999px;margin-left:6px;vertical-align:middle}
.tag.free{background:#334155;color:#cbd5e1}
.tag.pro{background:#1e3a8a;color:#93c5fd}
.tag.vip{background:#581c87;color:#e9d5ff}
label.f{display:block;font-size:12.5px;color:#9aa0b8;margin:14px 0 6px;font-weight:600}
input[type=text],input[type=number],textarea{width:100%;background:#0b0e1c;border:1px solid var(--line2);color:var(--txt);
     border-radius:10px;padding:10px 12px;font-size:15px;outline:none}
input:focus,textarea:focus{border-color:var(--acc)}
textarea{min-height:76px;resize:vertical;font-family:inherit;line-height:1.5}
.sec{border:1px solid var(--line);border-radius:12px;padding:11px;margin-top:12px;background:#131728}
.sech{display:flex;align-items:center;gap:8px;font-size:14.5px;font-weight:700;color:#fff}
.sech .num{display:inline-flex;width:22px;height:22px;align-items:center;justify-content:center;border-radius:50%;
     background:#243055;color:#9fc0ff;font-size:12px;font-weight:800;flex:none}
.sech .cnt{min-width:0;margin-left:auto;font-size:12px;font-weight:600;color:#8fb4ff;background:#1b2540;border:1px solid #2d3b63;
     border-radius:999px;padding:2px 10px;white-space:nowrap}
.subh{display:flex;align-items:baseline;gap:8px;flex-wrap:wrap;margin:14px 0 7px;font-size:12.5px;font-weight:700;color:#c3cae0}
.subh .subtip{font-weight:400;color:var(--dim);font-size:11.5px}
.allrow{min-width:0;display:flex;align-items:center;gap:10px;min-height:44px;margin-top:8px;padding:8px 11px;
     background:#0e1324;border:1px dashed #3d5a9e;border-radius:10px;cursor:pointer;font-weight:600}
.allrow .hint{margin-left:auto;font-size:11px;color:var(--dim);font-weight:400}
input[type=checkbox]{width:19px;height:19px;accent-color:var(--acc);flex:none}
.grps{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}
.grp{display:flex;align-items:center;gap:9px;min-height:48px;padding:9px 11px;background:#0e1324;
     border:1px solid #3b2f63;border-radius:11px;cursor:pointer}
.grp{min-width:0;overflow:hidden}
.grp .gn{min-width:0;font-size:13.5px;font-weight:700;color:#d9c8ff;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.grp .gm{margin-left:auto;font-size:11.5px;font-weight:700;color:#a78bfa;background:#241a3d;border-radius:999px;padding:2px 8px;flex:none}
.grp input{accent-color:#c084fc}
.search{margin-bottom:8px;font-size:14px;background:#0b0e1c}
.alist{display:flex;flex-direction:column;gap:6px;max-height:300px;overflow:auto;padding-right:2px}
.arow{display:flex;align-items:center;gap:10px;min-height:44px;padding:8px 11px;background:#0e1324;
     border:1px solid var(--line2);border-radius:10px;cursor:pointer}
.arow{min-width:0;overflow:hidden}
.arow .an{min-width:0;font-size:14px;color:#e2e6f3;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.arow .ag{margin-left:auto;font-size:11px;color:#a78bfa;background:#241a3d;border-radius:999px;padding:2px 8px;flex:none}
.checks{min-width:0;display:flex;flex-wrap:wrap;gap:7px;padding:2px 0}
.chip{max-width:100%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;display:inline-flex;align-items:center;gap:6px;background:#0e1324;border:1px solid var(--line2);
     border-radius:999px;padding:8px 12px;font-size:13px;cursor:pointer;min-height:40px}
.chip.all{border-color:#3d5a9e}
.empty{font-size:12.5px;color:var(--dim);padding:10px 2px}
.mini{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}
.btnrow{position:sticky;bottom:0;padding:10px 0 2px;background:linear-gradient(180deg,rgba(15,18,32,0),#0f1220 40%);margin-top:14px}
.btn{width:100%;background:var(--acc);color:#0b0e1c;border:0;border-radius:12px;padding:15px 18px;
     font-size:15.5px;font-weight:800;cursor:pointer;letter-spacing:.5px}
.btn.ghost{background:#1a1f36;color:#cfd3e6;border:1px solid var(--line2);font-weight:600;font-size:13px;padding:10px 14px;width:auto}
.msg{padding:11px 14px;border-radius:11px;font-size:13.5px;margin-bottom:12px}
.msg.ok{background:#14321f;color:#9ae6b4;border:1px solid #2f6b45}
.msg.err{background:#3a1522;color:#ffb3c1;border:1px solid #7a2b45}
.err{color:#ffb3c1}
.back{margin-bottom:10px;font-size:13px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(250px,1fr));gap:14px;margin-top:14px}
.pt{width:100%;border-collapse:collapse;font-size:13px}
.pt th{text-align:left;color:var(--dim);font-weight:600;width:96px;padding:6px 8px 6px 0;vertical-align:top}
.pt td{padding:6px 0;color:#dfe4f2}
.pill{display:inline-block;font-size:11.5px;background:#1a2340;border:1px solid var(--line2);border-radius:999px;padding:2px 9px;margin:2px 4px 2px 0;color:#c7d2ee}
@media (max-width:420px){
  h1{font-size:18px}
  .grps{grid-template-columns:repeat(2,minmax(0,1fr))}
  body{padding:12px 10px 92px}
}
"""


def _perm_checks(plan, agents_avail, pets_avail):
    """v3：① 可用 Agent（組別捷徑＋個別清單）② 可瀏覽頁面 ③ 可用寵物款式。"""
    ag = _to_list(plan.get('agents'), ['*'])
    pg = _to_list(plan.get('pages'), ['*'])
    pt = _to_list(plan.get('pets'), ['*'])
    ag_all = '*' in ag
    pt_all = '*' in pt
    pg_all = '*' in pg

    if ag_all:
        _all_cb = '<input type="checkbox" name="agents" value="__all__" checked onchange="plansAllToggle(this)">'
    else:
        _all_cb = '<input type="checkbox" name="agents" value="__all__" onchange="plansAllToggle(this)">'
    ag_html = ('<section class="sec"><div class="sech"><span class="num">①</span><span>可用 Agent</span>'
               '<span class="cnt">已選 0 個</span></div>'
               '<label class="allrow">' + _all_cb +
               '<span>全部 Agent（*）</span><span class="hint">＝目錄內所有 agent</span></label>')

    _groups = _agent_groups()
    if _groups:
        ag_html += ('<div class="subh">組別捷徑<span class="subtip">點一下＝整組開／關，之後仍可個別微調</span></div>'
                    '<div class="grps">')
        for _gn in sorted(_groups):
            _members = _groups[_gn]
            _gchk = ' checked' if (not ag_all and all((_m in ag) for _m in _members)) else ''
            ag_html += ('<label class="grp"><input type="checkbox" data-grp="%s"%s onchange="plansGroupToggle(this)">'
                        '<span class="gn">%s</span><span class="gm">0/%d</span></label>'
                        % (_esc(_gn), _gchk, _esc(_gn), len(_members)))
        ag_html += '</div>'

    ag_html += ('<div class="subh">個別 Agent<span class="subtip">共 %d 個</span></div>' % len(agents_avail))
    ag_html += ('<input class="search" type="text" placeholder="搜尋 agent 名稱…" '
                'oninput="plansFilter(this)" autocomplete="off">')
    ag_html += '<div class="alist">'
    for a in agents_avail:
        _checked = ' checked' if (ag_all or a in ag) else ''
        _g = _agent_group_of(a)
        _gtag = ('<span class="ag">%s</span>' % _esc(_g)) if _g else ''
        ag_html += ('<label class="arow" data-name="%s">'
                    '<input type="checkbox" name="agents" value="%s" data-group="%s"%s onchange="plansOneToggle(this)">'
                    '<span class="an">%s</span>%s</label>'
                    % (_esc(a).lower(), _esc(a), _esc(_g), _checked, _esc(a), _gtag))
    ag_html += ('</div>'
                '<div class="empty" style="display:none">沒有符合的 agent</div>'
                '<div class="mini">'
                '<button type="button" class="btn ghost" onclick="plansAgentSet(this,true)">全部勾選</button>'
                '<button type="button" class="btn ghost" onclick="plansAgentSet(this,false)">全部取消</button>'
                '</div></section>')

    if pt_all:
        _pet_cb = '<input type="checkbox" name="pets" value="__all__" checked onchange="plansPetsToggle(this)">'
    else:
        _pet_cb = '<input type="checkbox" name="pets" value="__all__" onchange="plansPetsToggle(this)">'
    pet_html = ('<section class="sec"><div class="sech"><span class="num">③</span><span>可用桌面寵物款式</span></div>'
                '<label class="allrow">' + _pet_cb + '<span>全部款式（*）</span></label>'
                '<div class="checks" style="margin-top:10px">')
    for p in pets_avail:
        _checked = ' checked' if (pt_all or p in pt) else ''
        pet_html += ('<label class="chip"><input type="checkbox" name="pets" value="%s"%s>%s</label>'
                     % (_esc(p), _checked, _esc(p)))
    if not pets_avail:
        pet_html += '<span class="small">（目前未偵測到已設定的寵物款式）</span>'
    pet_html += '</div></section>'

    pg_txt = '' if pg_all else '\n'.join(x for x in pg if x != '*')
    _pg_cb = ' checked' if pg_all else ''
    pages_html = ('<section class="sec"><div class="sech"><span class="num">②</span><span>可瀏覽頁面</span></div>'
                  '<label class="allrow"><input type="checkbox" name="pages_all" value="1"%s '
                  'onchange="plansPagesToggle(this)"><span>不限頁面（*）</span>'
                  '<span class="hint">取消才需填白名單</span></label>'
                  '<textarea name="pages_txt" style="margin-top:10px"'
                  ' placeholder="每行一個路徑，例如：&#10;/&#10;/member&#10;/some/page"%s>%s</textarea>'
                  '<div class="small" style="margin-top:6px">留空＝不限制（儲存時自動視為 *）。</div></section>'
                  % (_pg_cb, ' disabled' if pg_all else '', _esc(pg_txt)))

    inner_js = """
<script>
function _pf(e){return e.closest('form');}
function plansAllToggle(cb){var f=_pf(cb);f.querySelectorAll('input[name="agents"]').forEach(function(x){if(x.value!=='__all__'){x.checked=cb.checked;}});plansState(f);}
function plansOneToggle(cb){var f=_pf(cb);var a=f.querySelector('input[name="agents"][value="__all__"]');if(cb.checked&&a){a.checked=false;}plansState(f);}
function plansGroupToggle(cb){var f=_pf(cb);var g=cb.getAttribute('data-grp');f.querySelectorAll('input[name="agents"][data-group]').forEach(function(x){if(x.getAttribute('data-group')===g){x.checked=cb.checked;}});if(cb.checked){var a=f.querySelector('input[name="agents"][value="__all__"]');if(a){a.checked=false;}}plansState(f);}
function plansAgentSet(btn,on){var f=_pf(btn);var a=f.querySelector('input[name="agents"][value="__all__"]');if(a&&on){a.checked=false;}f.querySelectorAll('.arow').forEach(function(r){if(r.style.display!=='none'){var x=r.querySelector('input[name="agents"]');if(x){x.checked=on;}}});plansState(f);}
function plansPetsToggle(cb){var f=_pf(cb);f.querySelectorAll('input[name="pets"]').forEach(function(x){if(x.value!=='__all__'){x.checked=cb.checked;}});plansState(f);}
function plansPagesToggle(cb){plansState(_pf(cb));}
function plansFilter(inp){var f=_pf(inp);var q=(inp.value||'').trim().toLowerCase();var hit=0;f.querySelectorAll('.arow').forEach(function(r){var ok=(!q)||((r.getAttribute('data-name')||'').indexOf(q)>=0);r.style.display=ok?'':'none';if(ok){hit++;}});var e=f.querySelector('.empty');if(e){e.style.display=hit?'none':'block';}}
function plansState(f){
  var all=f.querySelector('input[name="agents"][value="__all__"]');var on=!!(all&&all.checked);
  var n=0;f.querySelectorAll('.arow input[name="agents"]').forEach(function(x){if(x.checked){n++;}});
  var c=f.querySelector('.sech .cnt');if(c){c.textContent=on?'全部 Agent':('已選 '+n+' 個');}
  f.querySelectorAll('.grp').forEach(function(g){
    var cb0=g.querySelector('input[data-grp]');if(!cb0){return;}
    var name=cb0.getAttribute('data-grp');var tot=0,sel=0;
    f.querySelectorAll('.arow input[data-group]').forEach(function(x){if(x.getAttribute('data-group')===name){tot++;if(x.checked){sel++;}}});
    var m=g.querySelector('.gm');if(m){m.textContent=sel+'/'+tot;}
    cb0.checked=(!on&&tot>0&&sel===tot);
  });
  var pa=f.querySelector('input[name="pages_all"]');var ta=f.querySelector('textarea[name="pages_txt"]');
  if(pa&&ta){ta.disabled=pa.checked;ta.style.opacity=pa.checked?'0.45':'1';}
}
function plansTab(i,btn){
  document.querySelectorAll('.panel').forEach(function(p){p.classList.remove('show');});
  var p=document.getElementById('plan-panel-'+i);if(p){p.classList.add('show');}
  document.querySelectorAll('.tab').forEach(function(b){b.classList.remove('active');});
  var t=btn||document.querySelectorAll('.tab')[i];if(t){t.classList.add('active');}
  try{history.replaceState(null,'','#plan-'+i);}catch(e){}
}
function plansInit(){
  var tabs=document.querySelectorAll('.tab');
  var m=(location.hash||'').match(/plan-(\\d+)/);var i=m?parseInt(m[1],10):0;
  if(isNaN(i)||i<0||i>=tabs.length){i=0;}
  plansTab(i,null);
  document.querySelectorAll('.panel form').forEach(function(f){plansState(f);});
}
if(document.readyState==='loading'){document.addEventListener('DOMContentLoaded',plansInit);}else{plansInit();}
</script>
"""
    return ag_html, pet_html, pages_html, inner_js


def _cards_html(msg=None, msg_type=None):
    """v3：FREE / PRO / VIP 分頁式方案管理（一次只看一個層級）。"""
    plans = _all_plans()
    agents_avail = _avail_agents()
    pets_avail = _avail_pets()
    if not plans:
        return '<div class="msg err">plans 表無資料</div>'
    msg_html = ''
    if msg:
        msg_html = '<div class="msg %s">%s</div>' % (_esc(msg_type or 'ok'), _esc(msg))
    csrf = _csrf_token()
    tabs, panels = [], []
    for i, plan in enumerate(plans):
        pname = str(plan['plan'])
        up = pname.upper()
        tagcls = pname if pname in ('free', 'pro', 'vip') else 'free'
        mt = int(plan.get('monthly_tokens') or 0)
        mt_txt = ('%s / 月' % format(mt, ',')) if mt else '—'
        ag_html, pet_html, pages_html, js = _perm_checks(plan, agents_avail, pets_avail)
        tabs.append('<button type="button" class="tab %s%s" onclick="plansTab(%d,this)">%s</button>'
                    % (_esc(tagcls), '' if i else ' active', i, _esc(up)))
        panels.append(
            "<div class='panel panel-%s' id='plan-panel-%d'>"
            "<form method='post' action='/admin/plans/%s#plan-%d'>"
            "<input type='hidden' name='csrf' value='%s'>"
            "<div class='card card-%s'>"
            "<div class='hero'><div class='nm'>%s<span class='tag %s'>%s</span></div>"
            "<div class='small'>目前額度 %s</div></div>"
            "<label class='f'>每月 Token 額度</label>"
            "<input type=number name=monthly_tokens value=%d min=0 step=1000 inputmode=numeric>"
            "<label class='f'>方案說明（會顯示在前台方案頁）</label>"
            "<textarea name=desc placeholder=方案說明文字>%s</textarea>"
            "%s%s%s%s"
            "<div class='btnrow'><button class='btn' type=submit>儲存 %s 層級</button></div>"
            "</div></form></div>"
            % (_esc(tagcls), i,
               _esc(pname), i,
               _esc(csrf),
               ' vip' if pname == 'vip' else '',
               _esc(up), _esc(tagcls), _esc(up), _esc(mt_txt),
               mt, _esc(plan.get('desc') or ''),
               ag_html, pages_html, pet_html, (js if i == 0 else ''),
               _esc(up)))
    header = ("<div class='top'><div><h1>層級方案管理</h1>"
              "<div class='sub'>分頁編輯 FREE / PRO / VIP 的權限與額度</div></div>"
              "<div class='nav'><a href='/admin/member'>← 會員管理</a>"
              "<a href='/plans' target='_blank'>前台方案 ↗</a></div></div>")
    return header + msg_html + ("<div class='tabs'>%s</div>" % ''.join(tabs)) + ''.join(panels)


print('[Plans後台] v3.0 手機分頁 UI 已載入')
