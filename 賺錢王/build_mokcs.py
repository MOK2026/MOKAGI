#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
💰 賺錢王 — MokCs 商家首頁克隆 + 客服 Agent 注入器
====================================================
需求（用戶指示）：
  1. 讀取 leads_7days.jsonl 內每一個商家
  2. 只複製「有 WhatsApp 或 Email」的商家（沒有 whatsapp/mail 一律不複製）
  3. 完整複製 每一位商家自己的首頁（原網站 HTML）
  4. 各自加上 .mok/html/project/api/index.md 的 MOKAGI 客服 agent（api.js）
  5. 儲存到 ~/.mok/html/project/MokCs/
  6. 用途：給潛在客戶的「AI 客服體驗 Demo」——
     每個網頁看起來就像對方公司自己的網站，只是右下角多了一個 AI 客服氣泡。
     聯絡方式只帶 WhatsApp / Email，不帶電話、不帶地址。

策略：
  - 先用 requests（瀏覽器 UA）抓取原始 HTML → 快
  - 若失敗 / 內容太少 / 純 JS 殼 → 自動用 Playwright 無頭瀏覽器渲染後抓完整 DOM
  - 將頁面內所有相對路徑 (src/href/srcset/action/poster/data-src...) 改寫為絕對 URL，
    使克隆頁面獨立開啟時版面、圖片、樣式都與原站一致（熱連結原站資源）
  - 在 </body> 前注入 MOKAGI 客服配置 + api.js
  - 每個商家的 agent_soul 會帶入 商家名稱/行業，讓 AI 以「該公司客服」身份回答

用法：
  python3 build_mokcs.py                 # 全部（跳過已存在的）
  python3 build_mokcs.py --force         # 全部（強制重新抓取）
  python3 build_mokcs.py --limit 5       # 只做前 5 個（測試）
  python3 build_mokcs.py --url https://xxx  # 只做指定網址
"""
import json, os, re, sys, time, html as html_mod, warnings, argparse
from urllib.parse import urljoin, urlparse
from datetime import datetime

warnings.filterwarnings('ignore')

BASE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(BASE, 'leads_7days.jsonl')
OUTDIR = os.path.expanduser('~/.mok/html/project/MokCs')
LOG = os.path.join(BASE, 'mokcs_build.log')

SERVER = 'https://64071181.xyz'          # MOKAGI Socket.IO 後端 / api.js 主機
AGENT  = '客服'                           # 客服 Agent 名稱（~/.mok/agent/客服/）
AGENT_ICON = '💬'
THEME = '#4A90D9'

UA = ('Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
      '(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36')

# ── 行業 → 快速查詢按鈕 ──
CATEGORY_LINKS = {
    '補習社': [('📚 課程查詢', '你們有什麼課程？時間和價錢是？'),
               ('📝 報名方式', '我想報名，要怎麼申請？'),
               ('💰 學費優惠', '現在有學費優惠或試堂嗎？')],
    '攝影':   [('📷 預約拍攝', '我想預約拍攝，怎麼安排？'),
               ('💵 套餐價格', '你們的拍攝套餐和價錢？'),
               ('📅 檔期查詢', '最近有什麼檔期可以預約？')],
    '寵物店': [('🐶 商品查詢', '店裏有賣什麼寵物用品？'),
               ('✂️ 寵物美容', '寵物美容服務怎麼預約？'),
               ('🏠 寄養服務', '有寵物寄養服務嗎？收費如何？')],
    '裝修工程': [('🔨 免費報價', '我想裝修，可以報價嗎？'),
                ('📐 預約丈量', '可以約時間上門度尺嗎？'),
                ('🏠 案例參考', '有沒有以往的裝修案例？')],
    '牙科診所': [('🦷 預約檢查', '我想預約牙科檢查/洗牙'),
                ('💰 收費查詢', '洗牙和補牙的收費是？'),
                ('🦷 種牙諮詢', '種牙/植牙的流程和價錢？')],
    '汽車維修': [('🔧 預約保養', '我想預約汽車保養/維修'),
                ('💰 報價查詢', '維修報價大概多少？'),
                ('🚨 緊急拖車', '車壞了，有緊急拖車服務嗎？')],
    '洗衣店':  [('👕 收費查詢', '乾洗/洗衣服務的收費？'),
                ('🕐 取件時間', '多久可以取回衣物？'),
                ('🚚 上門收衣', '有上門收送衣服服務嗎？')],
    '美容':    [('✨ 預約療程', '我想預約美容療程'),
                ('💆 價目查詢', '你們有哪些療程和價錢？'),
                ('🎁 會員優惠', '有會員優惠或新客體驗嗎？')],
    '餐廳':    [('🍽️ 訂位', '我想訂位，幾點有位？'),
                ('🥡 外賣外送', '有外賣或外送服務嗎？'),
                ('📖 菜單查詢', '可以看看菜單嗎？')],
    '髮型屋':  [('💇 預約剪髮', '我想預約剪髮/造型'),
                ('🎨 染燙查詢', '染髮/燙髮的價錢和時間？'),
                ('💰 價格查詢', '剪髮價格是多少？')],
    '健身室':  [('🏋️ 免費試堂', '我想預約免費試堂'),
                ('💳 會籍查詢', '會籍收費和方案是？'),
                ('🕐 開放時間', '開放時間是幾點到幾點？')],
    '花店':    [('💐 訂花服務', '我想訂花束/花籃'),
                ('🚚 送貨安排', '可以送貨嗎？多久送到？'),
                ('💵 價錢查詢', '花束大概多少錢？')],
}
DEFAULT_LINKS = [('💬 服務查詢', '你們提供什麼服務？'),
                 ('🕐 營業時間', '營業時間是？'),
                 ('📍 地址電話', '你們的地址和電話是？')]

def log(msg):
    line = f'[{datetime.now():%Y-%m-%d %H:%M:%S}] {msg}'
    print(line, flush=True)
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass

def safe_filename(name, used):
    """商家名稱 → 安全檔名（保留中文，去非法字元）"""
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', ' ', str(name))
    s = re.sub(r'\s+', ' ', s).strip().replace(' ', '-')
    s = re.sub(r'[^\w\u4e00-\u9fff.-]+', '', s)
    s = s.strip('.-') or 'site'
    if len(s) > 60:
        s = s[:60].rstrip('.-')
    base, i = s, 1
    while s.lower() in used:
        i += 1
        s = f'{base}-{i}'
    used.add(s.lower())
    return s

def read_leads():
    """讀取 leads，回傳 [ {name, website, category, whatsapp, mail} ]（有網站的，唯一 URL）"""
    seen = {}
    with open(DATA, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            w = html_mod.unescape((d.get('website') or '').strip())
            if not w:
                continue
            if not re.match(r'^https?://', w):
                w = 'http://' + w
            key = w.rstrip('/').lower()
            if key in seen:
                continue
            seen[key] = {
                'name': d.get('name') or urlparse(w).netloc,
                'website': w,
                'category': d.get('category') or '',
                'phone': d.get('phone') or '',
                'address': d.get('address') or '',
                'whatsapp': d.get('whatsapp') or '',
                'mail': d.get('mail') or '',
                'rating': d.get('rating') or '',
            }
    return list(seen.values())

# ── 讀取「沒網站但有聯絡」的商家（用戶指示：沒網的做一個一頁式網頁給她） ──
def read_nosite_leads():
    """讀取沒有網站但有 WhatsApp/Email 的商家（潛在客戶 A）"""
    seen = set()
    out = []
    with open(DATA, encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            if (d.get('website') or '').strip():
                continue  # 有網站的不屬於這裡
            wa = (d.get('whatsapp') or '').strip()
            mail = (d.get('mail') or '').strip()
            if not (wa or mail):
                continue  # 沒聯絡方式不處理
            key = (d.get('name') or '').strip().lower()
            if key in seen:
                continue
            seen.add(key)
            out.append({
                'name': d.get('name') or '商家',
                'category': d.get('category') or '',
                'phone': d.get('phone') or '',
                'address': d.get('address') or '',
                'whatsapp': wa,
                'mail': mail,
                'rating': d.get('rating') or '',
                'hints': d.get('hints') or [],
            })
    return out

# ── 抓取 ──
def fetch_requests(url, timeout=25):
    """requests 抓原始 HTML；回傳 (html, info) 或 (None, err)"""
    try:
        import requests
        r = requests.get(url, headers={
            'User-Agent': UA,
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8',
            'Accept-Language': 'zh-TW,zh;q=0.9,en;q=0.8,zh-HK;q=0.7',
            'Accept-Encoding': 'gzip, deflate, br',
        }, timeout=timeout, verify=False, allow_redirects=True)
        if r.status_code != 200:
            return None, f'HTTP {r.status_code}'
        ctype = (r.headers.get('content-type') or '').lower()
        if 'html' not in ctype and 'text' not in ctype and 'xml' not in ctype:
            return None, f'非 HTML ({ctype})'
        # 編碼處理
        if r.encoding and r.encoding.lower() not in ('utf-8', 'utf8'):
            try:
                r.encoding = r.apparent_encoding or r.encoding
            except Exception:
                pass
        html = r.text
        if not html or len(html) < 500:
            return None, '內容過少'
        return html, f'requests {len(html)}B'
    except Exception as e:
        return None, f'requests ERR {e}'

def fetch_playwright(url, timeout=35000):
    """Playwright 無頭渲染抓完整 DOM；回傳 (html, info) 或 (None, err)"""
    try:
        from playwright.sync_api import sync_playwright
    except Exception as e:
        return None, f'playwright 不可用: {e}'
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, args=[
                '--no-sandbox', '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled'])
            ctx = browser.new_context(
                user_agent=UA,
                viewport={'width': 1366, 'height': 900},
                locale='zh-HK')
            page = ctx.new_page()
            try:
                page.goto(url, timeout=timeout, wait_until='domcontentloaded')
            except Exception:
                pass
            try:
                page.wait_for_load_state('networkidle', timeout=10000)
            except Exception:
                pass
            # 處理 Cloudflare / 反爬驗證：等 title 離開 "Checking your browser..."
            t0 = time.time()
            while time.time() - t0 < 28:
                try:
                    t = page.title()
                    if t and 'checking your browser' not in t.lower():
                        break
                except Exception:
                    pass
                time.sleep(3)
            time.sleep(2)  # 讓 lazy-load 圖片/腳本多跑一下
            # content() 偶爾會因驗證跳轉取不到，重試
            html = ''
            for _ in range(3):
                try:
                    html = page.content()
                    if len(html) > 5000:
                        break
                except Exception:
                    html = ''
                time.sleep(3)
            browser.close()
            if not html or len(html) < 500:
                return None, '渲染後仍無內容'
            return html, f'playwright {len(html)}B'
    except Exception as e:
        try:
            browser.close()
        except Exception:
            pass
        return None, f'playwright ERR {e}'

# ── URL 改寫（相對路徑 → 絕對路徑）──
_ATTRS = {'src', 'href', 'action', 'poster', 'data-src', 'data-original', 'data-href', 'data-url', 'srcset'}
def _abs(base, u):
    u = u.strip()
    if not u or u.startswith(('#', 'data:', 'javascript:', 'mailto:', 'tel:', 'whatsapp:', 'about:')):
        return u
    if u.startswith('//'):
        return urljoin(base, u)
    return urljoin(base, u)

def rewrite_urls(html, base_url):
    """把 HTML 中相對 URL 改寫為絕對 URL"""
    # srcset / data-srcset 特殊處理（逗號分隔的多個候選）
    def fix_srcset(m):
        # srcset 的候選以「逗號+空白」分隔；URL 本身可能含逗號（如 Wix），不可直接按逗號拆
        parts = []
        for part in re.split(r',\s+', m.group(1).strip()):
            part = part.strip()
            if not part:
                continue
            toks = part.split()
            if len(toks) == 1:
                parts.append(_abs(base_url, toks[0]))
            else:
                parts.append(_abs(base_url, toks[0]) + ' ' + toks[1])
        return 'srcset="' + ', '.join(parts) + '"'
    html = re.sub(r'srcset="([^"]*)"', fix_srcset, html, flags=re.I)
    html = re.sub(r'data-srcset="([^"]*)"', fix_srcset, html, flags=re.I)

    def fix_attr(m):
        attr, val = m.group(1), m.group(2)
        return f'{attr}="{_abs(base_url, val)}"'
    html = re.sub(r'(src|href|action|poster|data-src|data-original|data-href|data-url)="([^"]*)"', fix_attr, html, flags=re.I)
    return html

# ── 客服 Agent 注入 ──
def make_embed(biz):
    name = biz['name']
    cat = biz['category'] or '公司'
    links = CATEGORY_LINKS.get(cat, DEFAULT_LINKS)
    ql = ',\n'.join('        { text: %r, query: %r }' % (t, q) for t, q in links)
    soul = (
        f'你是「{name}」的 AI 線上客服。這是一間位於香港的{cat}。'
        '請以親切、專業、友善的語氣，即時回覆客戶關於營業時間、服務項目、預約、價格、聯絡方式等問題。'
        '回答使用繁體中文，簡潔有禮。若客戶問到網站上沒有的資訊，請引導他們透過 WhatsApp 或 Email 聯絡店家。'
    )
    contact_parts = []
    if biz.get('whatsapp'):
        contact_parts.append('WhatsApp：' + biz['whatsapp'])
    if biz.get('mail'):
        contact_parts.append('Email：' + biz['mail'])
    contact = '；'.join(contact_parts)
    sayhi = f'✅ 已連接！我是「{name}」的 AI 客服，有什麼可以幫您？'
    embed = f'''
<!-- 🔧 配置 MOKAGI 客服 Agent（參考 api/index.md） -->
<script>
window.MOKAGI_AGENT = '{AGENT}';
window.MOKAGI_SERVER = '{SERVER}';
window.MOKAGI_USER_ID = localStorage.getItem('mokagi_user_id') || undefined;
window.MOKAGI_AGENT_ICON = '{AGENT_ICON}';
window.MOKAGI_THEME = '{THEME}';
window.position = 'bottom-right';
window.agent_soul = {json.dumps(soul, ensure_ascii=False)};
window.contact = {json.dumps(contact, ensure_ascii=False)};
window.quickLinks = [
{ql}
];
window.title = {json.dumps(name + ' 在線客服', ensure_ascii=False)};
window.sayHi = {json.dumps(sayhi, ensure_ascii=False)};
</script>
<!-- 🚀 載入 MOKAGI 聊天組件 -->
<script src="{SERVER}/static/api.js"></script>
'''
    return embed

def inject_agent(html, biz):
    embed = make_embed(biz)
    m = re.search(r'</body\s*>', html, flags=re.I)
    if m:
        return html[:m.start()] + embed + '\n' + html[m.start():]
    # 沒有 body 就整個包一層
    return html + '\n' + embed

# ── 一頁式自適應網頁（沒網站的商家用） ──
def build_one_page(biz):
    """為沒有網站的商家快速建立一頁式自適應網頁，並注入 MOKAGI 客服。
    格式與複製頁一致：都放 .mok/html/project/MokCs/客戶.html，都加 mokagi api 客服。"""
    name = (biz.get('name') or '商家').strip()
    cat = (biz.get('category') or '').strip()
    wa = (biz.get('whatsapp') or '').strip()
    mail = (biz.get('mail') or '').strip()
    rating = (biz.get('rating') or '').strip()
    address = (biz.get('address') or '').strip()
    phone = (biz.get('phone') or '').strip()
    hints = biz.get('hints') or []

    info_lines = []
    if cat:
        info_lines.append('<span class="tag">🏷️ ' + html_mod.escape(cat) + '</span>')
    if rating:
        info_lines.append('<span class="tag">⭐ ' + html_mod.escape(rating) + '</span>')
    if address:
        info_lines.append('<span class="tag">📍 ' + html_mod.escape(address) + '</span>')
    if phone:
        info_lines.append('<span class="tag">📞 ' + html_mod.escape(phone) + '</span>')

    contact_btns = []
    if wa:
        wa_digits = re.sub(r'\D', '', wa)
        wa_link = 'https://wa.me/' + wa_digits if wa_digits else '#'
        contact_btns.append('<a class="btn wa" href="' + wa_link + '" target="_blank" rel="noopener">💬 WhatsApp 聯絡</a>')
    if mail:
        contact_btns.append('<a class="btn mail" href="mailto:' + html_mod.escape(mail) + '">✉️ Email 聯絡</a>')

    note = ' '.join(html_mod.escape(h) for h in hints[:2])

    page = f"""<!DOCTYPE html>
<html lang="zh-HK">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{html_mod.escape(name)}</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:"PingFang HK","Microsoft JhengHei","Noto Sans TC",sans-serif;background:linear-gradient(160deg,#0f2a43 0%,#1a4a7a 55%,#2d6da3 100%);color:#fff;min-height:100vh;display:flex;flex-direction:column;align-items:center;justify-content:center;padding:32px 20px;text-align:center}}
.card{{max-width:680px;width:100%;background:rgba(255,255,255,.08);backdrop-filter:blur(12px);border:1px solid rgba(255,255,255,.18);border-radius:24px;padding:44px 34px;box-shadow:0 24px 60px rgba(0,0,0,.35)}}
.logo{{font-size:52px;margin-bottom:14px}}
h1{{font-size:30px;font-weight:800;letter-spacing:.5px;line-height:1.3;word-break:break-word}}
.sub{{color:#cfe3f5;font-size:15px;margin-top:10px}}
.tags{{display:flex;flex-wrap:wrap;gap:8px;justify-content:center;margin:22px 0 8px}}
.tag{{background:rgba(255,255,255,.14);border:1px solid rgba(255,255,255,.22);padding:6px 14px;border-radius:20px;font-size:13px}}
.desc{{color:#e8f1fa;font-size:15px;line-height:1.8;margin:20px 0 26px;max-width:520px}}
.btns{{display:flex;flex-wrap:wrap;gap:14px;justify-content:center}}
.btn{{display:inline-block;padding:14px 26px;border-radius:14px;font-size:16px;font-weight:700;text-decoration:none;transition:.2s}}
.btn.wa{{background:#25D366;color:#fff;box-shadow:0 8px 24px rgba(37,211,102,.35)}}
.btn.wa:hover{{transform:translateY(-2px);box-shadow:0 12px 28px rgba(37,211,102,.5)}}
.btn.mail{{background:#4A90D9;color:#fff;box-shadow:0 8px 24px rgba(74,144,217,.35)}}
.btn.mail:hover{{transform:translateY(-2px);box-shadow:0 12px 28px rgba(74,144,217,.5)}}
.foot{{color:#9fc0dd;font-size:12px;margin-top:30px}}
@media(max-width:480px){{h1{{font-size:23px}}.card{{padding:30px 20px}}.btn{{width:100%;text-align:center}}}}
</style>
</head>
<body>
<div class="card">
  <div class="logo">🏢</div>
  <h1>{html_mod.escape(name)}</h1>
  <div class="sub">{html_mod.escape(name)} 官方網站（建置中）</div>
  <div class="tags">{''.join(info_lines)}</div>
  <div class="desc">我們提供專業優質的服務，歡迎透過以下方式聯絡我們，即時為你解答查詢！{'<br>' + note if note else ''}</div>
  <div class="btns">{''.join(contact_btns)}</div>
  <div class="foot">© {datetime.now().year} {html_mod.escape(name)} · 網站由 MokCs 製作</div>
</div>
</body>
</html>"""
    return inject_agent(page, biz)


def build_nosite(force=False, skip_existing=True):
    """為「沒網站但有 WhatsApp/Email」的商家建立一頁式網頁"""
    leads = read_nosite_leads()
    log(f'== 開始為 {len(leads)} 間「沒網站但有聯絡」的商家建立一頁式網頁 → {OUTDIR} ==')
    used_names = set(os.listdir(OUTDIR))
    ok = 0
    for biz in leads:
        fname = safe_filename(biz['name'], used_names) + '.html'
        out_path = os.path.join(OUTDIR, fname)
        if skip_existing and not force and os.path.exists(out_path) and os.path.getsize(out_path) > 2000:
            log(f'  ↳ 已存在，跳過 ({fname})')
            ok += 1
            continue
        html = build_one_page(biz)
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(html)
        sz = os.path.getsize(out_path)
        log(f'  ✓ 一頁式網頁已建立 {fname} ({sz//1024}KB)')
        ok += 1
    log(f'== 一頁式網頁完成：{ok}/{len(leads)} ==')
    return ok

# ── 主流程 ──
def build(force=False, limit=None, only_url=None, skip_existing=True, do_nosite=True):
    os.makedirs(OUTDIR, exist_ok=True)
    leads = read_leads()
    # 只複製有 WhatsApp 或 Email 的商家（用戶指示：沒 whatsapp/mail 不要複製）
    before = len(leads)
    leads = [l for l in leads if (l.get('whatsapp') or '').strip() or (l.get('mail') or '').strip()]
    log(f'  過濾：{before} 間 → 保留 {len(leads)} 間有 WhatsApp/Email 的商家')
    if only_url:
        leads = [l for l in leads if only_url.rstrip('/').lower() in l['website'].rstrip('/').lower()]
    if limit:
        leads = leads[:limit]
    log(f'== 開始克隆 {len(leads)} 個商家首頁 → {OUTDIR} ==')

    used_names = set(os.listdir(OUTDIR))
    results = {'ok': [], 'fail': []}
    for i, biz in enumerate(leads, 1):
        url = biz['website']
        fname = safe_filename(biz['name'], used_names) + '.html'
        out_path = os.path.join(OUTDIR, fname)
        log(f'[{i}/{len(leads)}] {biz["name"][:30]} | {url}')
        if skip_existing and not force and os.path.exists(out_path) and os.path.getsize(out_path) > 2000:
            log(f'  ↳ 已存在，跳過 ({fname})')
            results['ok'].append((fname, 'skip'))
            continue
        # 1) requests
        html, info = fetch_requests(url)
        method = info
        # 2) 判斷是否需要 Playwright：內容太少 / 無 </body> / 可能純 JS
        if html:
            body_ok = bool(re.search(r'</body\s*>', html, flags=re.I))
            text_len = len(re.sub(r'<[^>]+>', '', html).strip())
            if not body_ok or text_len < 1500 or 'playwright' in info:
                html2, info2 = fetch_playwright(url)
                if html2:
                    html, method = html2, f'requests({info})→playwright({info2})'
                else:
                    log(f'  ⚠ playwright 失敗: {info2}（保留 requests 結果）')
                    method = f'requests({info}) [無渲染]'
        if not html:
            # 最後試 Playwright
            html, info = fetch_playwright(url)
            if html:
                method = f'playwright({info})'
        if not html:
            log(f'  ✗ 抓取失敗: {info}')
            results['fail'].append((biz['name'], url, info))
            continue
        # 3) 改寫相對 URL → 絕對 URL
        html = rewrite_urls(html, url)
        # 4) 注入客服 Agent
        html = inject_agent(html, biz)
        # 5) 儲存
        with open(out_path, 'w', encoding='utf-8') as f:
            f.write(html)
        sz = os.path.getsize(out_path)
        log(f'  ✓ 已儲存 {fname} ({sz//1024}KB, {method})')
        results['ok'].append((fname, method))
        time.sleep(0.3)

    log('== 完成 ==')
    log(f'成功 {len(results["ok"])} 個，失敗 {len(results["fail"])} 個')
    for name, url, err in results['fail']:
        log(f'  失敗: {name} | {url} | {err}')
    print('\n── 失敗清單 ──')
    for name, url, err in results['fail']:
        print(f'  ✗ {name} | {url} | {err}')
    if do_nosite:
        try:
            build_nosite(force=force, skip_existing=skip_existing)
        except Exception as e:
            log(f'  ⚠ 一頁式網頁建置失敗: {e}')
    return results

if __name__ == '__main__':
    ap = argparse.ArgumentParser()
    ap.add_argument('--force', action='store_true', help='強制重新抓取全部')
    ap.add_argument('--limit', type=int, default=None, help='只處理前 N 個')
    ap.add_argument('--url', default=None, help='只處理指定網址')
    ap.add_argument('--no-skip', action='store_true', help='不跳過已存在檔案')
    ap.add_argument('--nosite-only', action='store_true', help='只做「沒網站但有聯絡」商家的一頁式網頁')
    ap.add_argument('--skip-nosite', action='store_true', help='跳過「沒網站」商家的一頁式網頁')
    args = ap.parse_args()
    if args.nosite_only:
        build_nosite(force=args.force, skip_existing=not args.no_skip)
    else:
        build(force=args.force, limit=args.limit, only_url=args.url,
              skip_existing=not args.no_skip, do_nosite=not args.skip_nosite)
