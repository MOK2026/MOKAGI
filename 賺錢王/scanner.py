#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
============================================================
  賺錢王 - 香港商家 AI 客服機會自動掃描器 v2.0
============================================================
功能：
  1. 在 Google Maps 搜尋香港商家（指定行業 / 關鍵詞）
  2. 篩出兩類潛在客戶：
     A. 沒有網頁的商家
     B. 有網頁但網頁上「沒有 AI 客服」的商家
  3. 結果寫入 7 天記錄檔 leads_7days.jsonl（去重，已有資料不重複加）
  4. 自動提取每家商家的 WhatsApp / Email（不取電話、不取地址）
  5. 生成 報告.html（含每間商家狀態、WhatsApp/Email、AI客服檢測細節）

用法：
  python3 scanner.py                      # 預設掃描內建行業清單
  python3 scanner.py 寵物店               # 掃描指定行業
  python3 scanner.py 餐廳 美容 裝修       # 一次掃多個行業
  python3 scanner.py --limit 30           # 每行業最多處理商家數
  python3 scanner.py --headful            # 顯示瀏覽器視窗（除錯用）

輸出（本目錄）：
  leads_7days.jsonl   7天記錄（去重用，自動滾動清除 7 天前舊資料）
  報告.html           最新掃描報告
  scanner.log         執行日誌

依賴：pip install requests beautifulsoup4 playwright
============================================================
"""
import os
import re
import sys
import json
import time
import html
import hashlib
import logging
import datetime
import urllib.parse
import argparse

import requests
from bs4 import BeautifulSoup

# ==================== 路徑設定 ====================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
RECORD_FILE = os.path.join(BASE_DIR, "leads_7days.jsonl")
REPORT_FILE = os.path.join(BASE_DIR, "報告.html")
LOG_FILE = os.path.join(BASE_DIR, "scanner.log")
OUTDIR = os.path.expanduser("~/.mok/html/project/MokCs")  # 複製/一頁式網頁輸出目錄
RETENTION_DAYS = 7

# ==================== 日誌 ====================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    handlers=[
        logging.FileHandler(LOG_FILE, encoding="utf-8"),
        logging.StreamHandler(),
    ],
)
log = logging.getLogger("scanner")

# ==================== HTTP（網站抓取用） ====================
HEADERS = {
    "User-Agent": ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                   "AppleWebKit/537.36 (KHTML, like Gecko) "
                   "Chrome/126.0.0.0 Safari/537.36"),
    "Accept-Language": "zh-HK,zh-TW;q=0.9,zh;q=0.8,en;q=0.7",
}
SESSION = requests.Session()
SESSION.headers.update(HEADERS)
TIMEOUT = 15

# ==================== AI 客服平台檢測清單 ====================
AI_CHAT_PATTERNS = {
    "Tawk.to":      ["tawk.to", "tawk_api", "tawkto", "embed.tawk"],
    "Intercom":     ["intercom", "intercomSettings", "js.intercomcdn"],
    "Crisp":        ["crisp.chat", "$crisp", "crisp_", "client.crisp"],
    "Tidio":        ["tidio", "tidioChatApi", "code.tidio.co"],
    "Chatwoot":     ["chatwoot", "chatwoot_settings", "chatwoot.com"],
    "Zendesk":      ["zendesk", "webWidget", "zdassets", "ekr/snippet"],
    "LiveChat":     ["livechat", "lc_api", "cdn.livechatinc.com"],
    "Freshchat":    ["freshchat", "freshworks", "freshchat.com"],
    "HubSpot":      ["hs-script-loader", "js.hs-scripts", "hubspot"],
    "Drift":        ["drift", "drift-widget", "js.driftt.com"],
    "ManyChat":     ["manychat", "manychat.com"],
    "LivePerson":   ["liveperson", "lpcdn", "lptag"],
    "Salesforce":   ["embedded-messaging", "sfdc", "salesforceliveagent"],
    "智齒Sobot":     ["sobot", "sophia", "api.sobot.com"],
    "美洽Meiqia":    ["meiqia", "static.meiqia.com"],
    "七魚Qiyukf":    ["qiyukf", "qiyukf.com"],
    "環信Easemob":   ["easemob", "easemob.com"],
    "53客服":         ["53kf", "tb.53kf.com"],
    "LineOA":       ["line.me/ti/p", "line.me/R/ti/p"],
}

INSTANT_MSG_PATTERNS = {
    "WhatsApp":  ["wa.me", "api.whatsapp.com", "whatsapp.com"],
    "WeChat":    ["weixin.qq.com", "wechat", "open.weixin"],
    "FB Messenger": ["m.me/", "fb-messenger", "messenger.com"],
    "Telegram":  ["t.me/", "telegram.me"],
    "LINE":      ["line.me"],
}

DEFAULT_CATEGORIES = [
    "餐廳", "美容", "髮型屋", "裝修工程", "寵物店", "補習社",
    "健身室", "牙科診所", "汽車維修", "洗衣店", "花店", "攝影",
]

# ==================== 去重 ====================
def make_dedup_key(name, phone="", website=""):
    raw = "|".join([
        re.sub(r"\s+", "", str(name or "")).lower(),
        re.sub(r"\D", "", str(phone or "")),
        re.sub(r"^https?://(www\.)?", "", str(website or "").lower()).rstrip("/"),
    ])
    return hashlib.md5(raw.encode("utf-8")).hexdigest()

# ==================== 7 天記錄檔 ====================
def load_records():
    records = {}
    if os.path.exists(RECORD_FILE):
        cutoff = datetime.datetime.now() - datetime.timedelta(days=RETENTION_DAYS)
        kept = 0
        with open(RECORD_FILE, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                try:
                    t = datetime.datetime.fromisoformat(rec.get("found_at", ""))
                except Exception:
                    t = datetime.datetime.now()
                if t < cutoff:
                    continue
                records[rec["key"]] = rec
                kept += 1
        log.info("載入 7 天記錄 %d 筆（已自動清除 %d 天前舊資料）", kept, RETENTION_DAYS)
    return records

def save_record(rec):
    with open(RECORD_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")

# ==================== Google Maps 掃描（主來源） ====================
MAPS_JS_EXTRACT = """
() => {
  const out = {};
  // 名稱
  const nameSels = ['h1.DUwDvf','h1.fontHeadlineSmall','h1[class*="fontHeadline"]','div[role="main"] h1'];
  for (const s of nameSels) {
    const el = document.querySelector(s);
    if (el && el.innerText.trim() && el.innerText.trim() !== '結果') { out.name = el.innerText.trim(); break; }
  }
  // 網站按鈕
  const webSels = ['a[data-item-id="authority"]','a[aria-label*="網站"]','a[aria-label*="Website"]'];
  for (const s of webSels) {
    const el = document.querySelector(s);
    if (el) {
      const h = el.getAttribute('href') || '';
      if (h.startsWith('http')) { out.website = h; break; }
    }
  }
  // 地址
  const addrEl = document.querySelector('button[data-item-id="address"], div[data-item-id="address"]');
  if (addrEl) out.address = addrEl.innerText.trim().replace(/^\\s*\\S+\\s*\\n/, '');
  // 電話
  const phoneEl = document.querySelector('button[data-item-id*="phone"], div[data-item-id*="phone"]');
  if (phoneEl) out.phone = phoneEl.innerText.trim().replace(/^\\s*\\S+\\s*\\n/, '');
  // 評分
  const ratingEl = document.querySelector('div[role="main"] span[aria-label*="星"], div[role="main"] span[aria-label*="star"]');
  if (ratingEl) out.rating = ratingEl.getAttribute('aria-label');
  return out;
}
"""

# ==================== 香港商家過濾（排除日本誤抓） ====================
JP_PHONE_RE = re.compile(r"^(0[1-9]\d{0,2}-?\d|050-)")
JP_ADDR_RE = re.compile(r"[〒]|東京都|大阪府|神奈川県|千葉県|埼玉県|北海道|福岡県|愛知県|京都府|兵庫県|名古屋市|大阪市|横浜市|札幌市|福岡市|京都市|神戸市")
JP_DOMAIN_RE = re.compile(r"\.jp(/|$)", re.I)
JP_KANA_RE = re.compile(r"[\u3040-\u30ff]")

def clean_rating(rating):
    """從 Google Maps aria-label（如「4.7 つ星」「4.7 顆星」「4.7 stars」）抽出純數字"""
    if not rating:
        return ""
    m = re.search(r"\d+(?:\.\d+)?", str(rating))
    return m.group(0) if m else str(rating).strip()

def is_hk_business(biz):
    """判斷商家是否為香港商家（過濾日本地址/電話/網域/日文名）"""
    name = str(biz.get("name") or "")
    phone = str(biz.get("phone") or "")
    address = str(biz.get("address") or "")
    website = str(biz.get("website") or "")
    # 明確香港證據（+852 電話 / 地址含「香港」）→ 直接認定香港商家
    if "+852" in phone or "香港" in address:
        return True
    # 日本電話格式（03-、045-、050- 開頭）
    if JP_PHONE_RE.search(phone):
        return False
    # 日本地址（〒 郵遞區號 / 都道府縣 / 主要城市）
    if JP_ADDR_RE.search(address):
        return False
    # .jp 網域
    if JP_DOMAIN_RE.search(website):
        return False
    # 名稱含日文假名
    if JP_KANA_RE.search(name):
        return False
    return True

def search_google_maps(category, max_results, headless=True):
    """用 Playwright 在 Google Maps 搜尋香港商家，回傳 [{name,address,phone,website,rating,category,source}]"""
    from playwright.sync_api import sync_playwright

    results = []
    seen_names = set()   # 本次掃描名稱去重
    query = f"香港 {category}"
    # 鎖定香港座標，避免伺服器 IP（日本）導致 Google Maps 回傳日本商家
    url = ("https://www.google.com/maps/search/" + urllib.parse.quote(query)
           + "/@22.3193,114.1694,12z?hl=zh-HK")

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=headless,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--lang=zh-HK"],
        )
        ctx = browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36",
            locale="zh-HK",
            viewport={"width": 1280, "height": 900},
            extra_http_headers={"Accept-Language": "zh-HK,zh;q=0.9"},
        )
        page = ctx.new_page()
        try:
            page.goto(url, timeout=45000)
            page.wait_for_timeout(5000)

            # 滾動 feed 加載更多結果
            for _ in range(6):
                feed = page.query_selector('div[role="feed"]')
                if not feed:
                    break
                page.mouse.wheel(0, 4000)
                page.wait_for_timeout(1500)
                count = len(feed.query_selector_all('a[aria-label]'))
                log.debug("  已載入 %d 個商家…", count)
                if count >= max_results + 5:
                    break

            feed = page.query_selector('div[role="feed"]')
            if not feed:
                log.warning("Google Maps 未找到結果列表（可能被要求登入/驗證）")
                return results

            items = feed.query_selector_all('a[aria-label]')
            log.info("  Google Maps [%s] → %d 個商家", category, len(items))

            for i in range(min(len(items), max_results)):
                try:
                    # 重新取得 feed 中的條目（面板打開時 DOM 可能變化）
                    feed = page.query_selector('div[role="feed"]')
                    if not feed:
                        break
                    items = feed.query_selector_all('a[aria-label]')
                    if i >= len(items):
                        break
                    item = items[i]
                    label = item.get_attribute("aria-label") or ""
                    item.click()
                    page.wait_for_timeout(2800)

                    info = page.evaluate(MAPS_JS_EXTRACT) or {}
                    name = info.get("name") or label.strip()
                    # 名稱去重（Google Maps 點擊後 feed 順序會變化）
                    norm_name = re.sub(r"\s+", "", name).lower()
                    if norm_name in seen_names:
                        continue
                    seen_names.add(norm_name)
                    # 清理地址/電話中的圖標字符（私人使用區）
                    clean = lambda s: re.sub(r"[\ue000-\uf8ff\u200b-\u200f\u2060]", "", s or "").strip()
                    biz_item = {
                        "name": name,
                        "address": clean(info.get("address", "")),
                        "phone": clean(info.get("phone", "")),
                        "website": info.get("website", ""),
                        "rating": clean_rating(info.get("rating", "")),
                        "category": category,
                        "source": "google_maps",
                        "maps_label": label,
                    }
                    # 過濾非香港商家（日本誤抓）
                    if not is_hk_business(biz_item):
                        log.info("    [%d/%d] 過濾非香港商家: %s", i + 1, min(len(items), max_results), name)
                        continue
                    results.append(biz_item)
                    log.info("    [%d/%d] %s | 網站:%s | %s",
                             i + 1, min(len(items), max_results), name,
                             "有" if info.get("website") else "無",
                             info.get("phone", ""))
                except Exception as e:
                    log.warning("    商家 %d 提取失敗: %s", i + 1, e)
                    continue
                time.sleep(0.8)
        except Exception as e:
            log.error("Google Maps 掃描失敗 [%s]: %s", category, e)
        finally:
            browser.close()
    return results

# ==================== DuckDuckGo（備援來源） ====================
def search_duckduckgo(query, max_results=15):
    results = []
    params = {"q": query, "kl": "hk-tzh", "kp": "-2"}
    try:
        r = SESSION.get("https://html.duckduckgo.com/html/", params=params, timeout=TIMEOUT)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        for res in soup.select(".result"):
            a = res.select_one(".result__a")
            if not a:
                continue
            url = a.get("href", "")
            if url.startswith("//duckduckgo.com/l/"):
                m = re.search(r"uddg=([^&]+)", url)
                if m:
                    url = urllib.parse.unquote(m.group(1))
            snippet_el = res.select_one(".result__snippet")
            if url:
                results.append({"title": a.get_text(strip=True),
                                "url": url,
                                "snippet": snippet_el.get_text(strip=True) if snippet_el else ""})
            if len(results) >= max_results:
                break
    except Exception as e:
        log.warning("DuckDuckGo 搜尋失敗 [%s]: %s", query, e)
    return results

def extract_businesses_from_ddg(search_results):
    bizs = []
    seen = set()
    skip_domains = ["duckduckgo", "wikipedia", "youtube", "facebook.com", "instagram.com",
                    "linkedin.com", "hk01.com", "yahoo", "google.", "bing.com", "baidu.com",
                    "openrice", "tripadvisor", "yelp"]
    for res in search_results:
        url = res["url"]
        host = urllib.parse.urlparse(url).netloc.lower().replace("www.", "")
        if any(s in host for s in skip_domains):
            continue
        name = re.sub(r"\s*[-–—|]\s*(OpenRice|黃頁|Yelp|TripAdvisor|香港|Hong Kong).*$", "", res["title"]).strip()
        if not name or len(name) < 2:
            continue
        key = make_dedup_key(name, website=url)
        if key in seen:
            continue
        seen.add(key)
        bizs.append({"name": name, "website": url, "phone": "", "address": "",
                     "category": "", "source": "duckduckgo", "snippet": res.get("snippet", "")})
    return bizs

# ==================== 網站抓取 + AI 客服檢測 ====================
def fetch_html(url, max_bytes=800_000):
    try:
        r = SESSION.get(url, timeout=TIMEOUT, allow_redirects=True)
        r.raise_for_status()
        ctype = r.headers.get("Content-Type", "")
        if "html" not in ctype and "text" not in ctype:
            return "", r.status_code, "非 HTML: " + ctype
        r.encoding = r.apparent_encoding or "utf-8"
        return r.text[:max_bytes], r.status_code, None
    except Exception as e:
        return "", 0, str(e)

def detect_chatbot(html_text):
    if not html_text:
        return False, [], [], ["無法取得網站內容"]
    text_lower = html_text.lower()
    ai_found = False
    ai_platforms = []
    instant_msgs = []
    hints = []

    for platform, patterns in AI_CHAT_PATTERNS.items():
        for pat in patterns:
            if pat.lower() in text_lower:
                ai_platforms.append(platform)
                ai_found = True
                hints.append(f"偵測到客服平台: {platform}")
                break

    for platform, patterns in INSTANT_MSG_PATTERNS.items():
        for pat in patterns:
            if pat.lower() in text_lower:
                instant_msgs.append(platform)
                hints.append(f"偵測到即時通訊: {platform}")
                break

    for kw in ["在線客服", "在線諮詢", "線上客服", "在線咨詢", "客服機器人", "智能客服",
               "chat now", "chat with us", "live chat", "customer service"]:
        if kw.lower() in text_lower:
            hints.append(f"偵測到客服文字: {kw}")
            ai_found = True

    return ai_found, list(set(ai_platforms)), list(set(instant_msgs)), hints

def extract_contact(html_text):
    """從網站 HTML 提取 WhatsApp 與 Email（不取電話/地址）"""
    whatsapp = ""
    mail = ""
    if html_text:
        text_lower = html_text.lower()
        # WhatsApp 號碼：wa.me / api.whatsapp.com / whatsapp.com/send?phone=
        wa_patterns = [
            r"wa\.me/(\d{6,})",
            r"whatsapp\.com/(?:send|wa)\?phone=(\d{6,})",
            r"api\.whatsapp\.com/send\?phone=(\d{6,})",
            r"whatsapp[^\d]{0,30}(\+?\d[\d\s\-]{7,})",
        ]
        for pat in wa_patterns:
            m = re.search(pat, text_lower)
            if m:
                num = re.sub(r"\D", "", m.group(1))
                if len(num) >= 8:
                    whatsapp = num
                    break
        # Email（排除圖片/資源檔）
        for m in re.findall(r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}", html_text):
            low = m.lower()
            if low.endswith((".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".css", ".js", ".ico")):
                continue
            if any(x in low for x in ("example", "sentry", "wixpress", "domain", "schema", "yourname", "@2x", "@3x")):
                continue
            mail = m
            break
    return whatsapp, mail

def classify_business(biz):
    """回傳 (status, detail)：status ∈ no_website / no_ai / has_ai"""
    name = biz.get("name", "")
    website = biz.get("website", "")

    if not website:
        return "no_website", {
            "name": name, "phone": biz.get("phone", ""), "address": biz.get("address", ""),
            "whatsapp": "", "mail": "",
            "category": biz.get("category", ""), "website": "",
            "ai_platforms": [], "instant_msgs": [], "hints": ["Google Maps 沒有網站連結"],
            "source": biz.get("source", ""), "rating": biz.get("rating", ""),
        }

    html_text, status, err = fetch_html(website)
    whatsapp, mail = extract_contact(html_text)
    ai_found, ai_platforms, instant_msgs, hints = detect_chatbot(html_text)
    if err:
        hints = [f"網站抓取失敗: {err}"]
    host = urllib.parse.urlparse(website).netloc.lower().replace("www.", "")
    if "facebook.com" in host or "instagram.com" in host:
        hints.append("⚠️ 網站為社群專頁（非自有官網）")

    detail = {
        "name": name, "phone": biz.get("phone", ""), "address": biz.get("address", ""),
        "whatsapp": whatsapp, "mail": mail,
        "category": biz.get("category", ""), "website": website,
        "ai_platforms": ai_platforms, "instant_msgs": instant_msgs, "hints": hints,
        "source": biz.get("source", ""), "rating": biz.get("rating", ""),
        "http_status": status,
    }
    return ("has_ai" if ai_found else "no_ai"), detail

# ==================== 報告 HTML ====================
def esc(s):
    return html.escape(str(s or ""))

def _demo_filename(name):
    # 與 build_mokcs.safe_filename 相同規則
    s = re.sub(r'[\\/:*?"<>|\r\n\t]+', ' ', str(name or ''))
    s = re.sub(r'\s+', ' ', s).strip().replace(' ', '-')
    s = re.sub(r'[^\w\u4e00-\u9fff.-]+', '', s)
    s = s.strip('.-') or 'site'
    if len(s) > 60:
        s = s[:60].rstrip('.-')
    return s

def demo_status(name):
    # 檢查 OUTDIR 是否已有該商家的複製/一頁式網頁
    try:
        base = _demo_filename(name)
        if not os.path.isdir(OUTDIR):
            return '<span class="muted">-</span>'
        pat = re.compile(r'^' + re.escape(base.lower()) + r'(-\d+)?\.html$')
        for fn in os.listdir(OUTDIR):
            if pat.match(fn.lower()):
                return '<span class="badge b-ha">✅ 已做Demo</span>'
    except Exception:
        pass
    return '<span class="badge b-nw">⏳ 未做</span>'


def generate_report(no_website, no_ai, has_ai, categories):
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    total = len(no_website) + len(no_ai) + len(has_ai)
    cat_str = "、".join(categories) if categories else "全部"

    def biz_rows(items, badge):
        rows = []
        n = 0
        for b in items:
            # 用戶指示：報告只列「有 WhatsApp 或 Email」的潛在客，沒聯絡的不顯示
            if not ((b.get('whatsapp') or '').strip() or (b.get('mail') or '').strip()):
                continue
            n += 1
            demo = demo_status(b.get('name', ''))
            web = b.get("website", "")
            web_html = (f'<a href="{esc(web)}" target="_blank" rel="noopener">{esc(web)}</a>'
                        if web else '<span class="muted">無</span>')
            inst = "、".join(b.get("instant_msgs", [])) if b.get("instant_msgs") else "—"
            ai = "、".join(b.get("ai_platforms", [])) if b.get("ai_platforms") else "—"
            hints = "<br>".join(esc(h) for h in b.get("hints", [])[:5]) if b.get("hints") else "—"
            rating = esc(b.get("rating") or "—")
            wa = b.get('whatsapp') or ''
            em = b.get('mail') or ''
            wa_html = (f'<a href="https://wa.me/{esc(wa)}" target="_blank" rel="noopener">{esc(wa)}</a>'
                       if wa else '<span class="muted">—</span>')
            em_html = (f'<a href="mailto:{esc(em)}">{esc(em)}</a>'
                       if em else '<span class="muted">—</span>')
            rows.append(f"""<tr>
<td>{n}</td><td class="name">{esc(b.get('name',''))}</td>
<td>{badge}</td><td>{web_html}</td><td>{wa_html}</td>
<td>{em_html}</td><td>{rating}</td>
<td>{inst}</td><td>{ai}</td><td>{demo}</td><td class="hint">{hints}</td></tr>""")
        return "\n".join(rows)

    empty = '<tr><td colspan="11" class="empty">本次沒有找到</td></tr>'

    doc = f"""<!DOCTYPE html>
<html lang="zh-HK">
<head>
<meta charset="UTF-8"><meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>香港商家 AI 客服機會報告</title>
<style>
*{{box-sizing:border-box;margin:0;padding:0}}
body{{font-family:"PingFang HK","Microsoft JhengHei",sans-serif;background:#f0f4f8;color:#1a2b3c;padding:24px}}
.wrap{{max-width:1400px;margin:0 auto}}
h1{{font-size:26px;margin-bottom:6px;color:#0f2a43}}
.sub{{color:#5b7083;font-size:13px;margin-bottom:20px}}
.stats{{display:flex;gap:14px;flex-wrap:wrap;margin-bottom:24px}}
.stat{{background:#fff;border-radius:12px;padding:18px 22px;flex:1;min-width:150px;box-shadow:0 2px 8px rgba(0,0,0,.06)}}
.stat .num{{font-size:32px;font-weight:700}}
.stat .lbl{{font-size:13px;color:#5b7083;margin-top:4px}}
.nw .num{{color:#e74c3c}}.na .num{{color:#e67e22}}.ha .num{{color:#27ae60}}
h2{{font-size:19px;margin:26px 0 12px;padding-left:10px;border-left:4px solid #0f2a43}}
table{{width:100%;border-collapse:collapse;background:#fff;border-radius:12px;overflow:hidden;box-shadow:0 2px 8px rgba(0,0,0,.06);font-size:13px}}
th,td{{padding:9px 10px;text-align:left;border-bottom:1px solid #eef2f6;vertical-align:top}}
th{{background:#0f2a43;color:#fff;font-weight:600;white-space:nowrap}}
tr:hover td{{background:#f7fafc}}
.badge{{display:inline-block;padding:2px 10px;border-radius:20px;font-size:12px;font-weight:600;white-space:nowrap}}
.b-nw{{background:#fdecea;color:#c0392b}}.b-na{{background:#fef5e7;color:#d35400}}.b-ha{{background:#eafaf1;color:#1e8449}}
.name{{font-weight:600;min-width:140px}}.muted{{color:#98a6b3}}
.hint{{color:#6b7f94;font-size:12px;max-width:230px}}
.footer{{text-align:center;color:#98a6b3;font-size:12px;margin-top:30px}}
.empty{{padding:30px;text-align:center;color:#98a6b3}}
</style></head><body><div class="wrap">
<h1>🤖 香港商家 AI 客服機會報告</h1>
<div class="sub">掃描時間：{now} ｜ 行業：{esc(cat_str)} ｜ 掃描商家：{total} 間 ｜ 資料檔：leads_7days.jsonl（7 天去重）</div>
<div class="stats">
<div class="stat nw"><div class="num">{len(no_website)}</div><div class="lbl">🚫 沒有網頁（潛在客戶 A）</div></div>
<div class="stat na"><div class="num">{len(no_ai)}</div><div class="lbl">⚠️ 有網頁無 AI 客服（潛在客戶 B）</div></div>
<div class="stat ha"><div class="num">{len(has_ai)}</div><div class="lbl">✅ 已有 AI 客服（暫不開發）</div></div>
</div>

<h2>🚫 A. 沒有網頁的商家（{len(no_website)} 間）— 可推薦：建置網站 + AI 客服</h2>
<table><tr><th>#</th><th>商家名稱</th><th>狀態</th><th>網站</th><th>WhatsApp</th><th>Email</th><th>評分</th><th>即時通訊</th><th>AI客服</th><th>Demo 頁</th><th>檢測說明</th></tr>
{biz_rows(no_website, '<span class="badge b-nw">沒網頁</span>') if no_website else empty}</table>

<h2>⚠️ B. 有網頁但沒有 AI 客服（{len(no_ai)} 間）— 可推薦：加裝 AI 客服</h2>
<table><tr><th>#</th><th>商家名稱</th><th>狀態</th><th>網站</th><th>WhatsApp</th><th>Email</th><th>評分</th><th>即時通訊</th><th>AI客服</th><th>Demo 頁</th><th>檢測說明</th></tr>
{biz_rows(no_ai, '<span class="badge b-na">無AI客服</span>') if no_ai else empty}</table>

<h2>✅ C. 已有 AI 客服（{len(has_ai)} 間）— 參考對照</h2>
<table><tr><th>#</th><th>商家名稱</th><th>狀態</th><th>網站</th><th>WhatsApp</th><th>Email</th><th>評分</th><th>即時通訊</th><th>AI客服</th><th>Demo 頁</th><th>檢測說明</th></tr>
{biz_rows(has_ai, '<span class="badge b-ha">已有AI</span>') if has_ai else empty}</table>

<div class="footer">由 賺錢王 scanner.py 自動產生 ｜ 記錄保留 7 天，重複商家自動跳過 ｜ 生成時間 {now}</div>
</div></body></html>"""
    with open(REPORT_FILE, "w", encoding="utf-8") as f:
        f.write(doc)
    log.info("報告已生成: %s", REPORT_FILE)

def generate_report_from_db(categories=None):
    """從 leads_7days.jsonl 全量資料產生報告（只列有 WhatsApp/Email 的潛在客）"""
    nw, na, ha = [], [], []
    with open(RECORD_FILE, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                d = json.loads(line)
            except Exception:
                continue
            status = d.get("status", "")
            if status == "no_website":
                nw.append(d)
            elif status == "no_ai":
                na.append(d)
            elif status == "has_ai":
                ha.append(d)
    generate_report(nw, na, ha, categories or [])
    log.info("全量報告已生成（A沒網頁 %d｜B無AI客服 %d｜C已有AI %d）", len(nw), len(na), len(ha))

# ==================== 主流程 ====================
def run_category(category, limit, records, headless):
    log.info("===== 掃描行業：%s =====", category)
    nw, na, ha = [], [], []
    new_count = 0

    # 主來源：Google Maps
    bizs = search_google_maps(category, limit, headless=headless)

    # 備援：若 Google Maps 失敗，用 DuckDuckGo
    if not bizs:
        log.info("  Google Maps 無結果，改用 DuckDuckGo 備援…")
        results = []
        for q in [f"香港 {category}", f'"{category}" 香港 公司']:
            results.extend(search_duckduckgo(q, max_results=limit))
            time.sleep(1)
        bizs = extract_businesses_from_ddg(results)
        bizs = [b for b in bizs if is_hk_business(b)]
        log.info("  DuckDuckGo 過濾後剩 %d 間香港商家", len(bizs))

    log.info("  候選商家 %d 間，開始檢查…", len(bizs))
    for idx, biz in enumerate(bizs, 1):
        name = biz.get("name", "")
        dedup_key = make_dedup_key(name, biz.get("phone", ""), biz.get("website", ""))
        if dedup_key in records:
            log.info("  [%d/%d] 跳過（7天內已記錄）: %s", idx, len(bizs), name)
            continue
        try:
            status, detail = classify_business(biz)
        except Exception as e:
            log.warning("  檢查失敗 %s: %s", name, e)
            continue

        detail["category"] = category
        detail["found_at"] = datetime.datetime.now().isoformat(timespec="seconds")
        detail["key"] = dedup_key
        detail["status"] = status

        # 用戶指示：只有「有 WhatsApp 或 Email」的潛在客才記錄到 leads_7days.jsonl
        has_contact = bool((detail.get("whatsapp") or "").strip() or (detail.get("mail") or "").strip())
        if not has_contact:
            log.info("  ⏭️ 無 WhatsApp/Email，不記錄: %s", name)
            continue

        save_record(detail)
        records[dedup_key] = detail
        new_count += 1

        if status == "no_website":
            nw.append(detail)
            log.info("  🚫 [%d/%d] 沒網頁: %s | %s", idx, len(bizs), name, biz.get("phone", ""))
        elif status == "no_ai":
            na.append(detail)
            log.info("  ⚠️ [%d/%d] 有網頁無AI客服: %s (%s)", idx, len(bizs), name, detail.get("website"))
        else:
            ha.append(detail)
            log.info("  ✅ [%d/%d] 已有AI客服: %s", idx, len(bizs), name)

        time.sleep(0.5)
    return nw, na, ha, new_count

def main():
    parser = argparse.ArgumentParser(description="香港商家 AI 客服機會掃描器")
    parser.add_argument("categories", nargs="*", help="行業類別（可多個），預設內建清單")
    parser.add_argument("--limit", type=int, default=20, help="每行業最多處理商家數（預設 20）")
    parser.add_argument("--headful", action="store_true", help="顯示瀏覽器視窗（除錯用）")
    parser.add_argument("--type", choices=["A", "B", "AB"], default="AB",
                        help="要求內容：A=只找沒網頁商家；B=只找有網頁但無AI客服商家；AB=兩者都要（預設）")
    args = parser.parse_args()

    categories = args.categories if args.categories else DEFAULT_CATEGORIES
    log.info("=" * 50)
    log.info("賺錢王掃描器 v2.0 啟動｜行業: %s｜每行業上限: %d｜要求: %s",
             "、".join(categories), args.limit, args.type)
    log.info("=" * 50)

    records = load_records()
    all_nw, all_na, all_ha = [], [], []
    total_new = 0

    for cat in categories:
        try:
            nw, na, ha, new_count = run_category(cat, args.limit, records, not args.headful)
            if args.type == "A":
                nw, na, ha = nw, [], []
            elif args.type == "B":
                nw, na, ha = [], na, []
            all_nw.extend(nw); all_na.extend(na); all_ha.extend(ha)
            total_new += new_count
        except Exception as e:
            log.error("行業 %s 掃描失敗: %s", cat, e)

    generate_report_from_db(categories)

    log.info("=" * 50)
    log.info("掃描完成｜新增 %d 筆｜沒網頁 %d｜無AI客服 %d｜已有AI %d",
             total_new, len(all_nw), len(all_na), len(all_ha))
    log.info("報告: %s", REPORT_FILE)
    log.info("記錄: %s", RECORD_FILE)
    log.info("=" * 50)

if __name__ == "__main__":
    main()
