# ------------------------------------------------------------------------------------ #
# 字典: PLUGIN_INFO
# 用途: 定義網頁抓取工具與主程序、意圖識別系統的接口。
# 設計:
#   - naturalize_func 指向 naturalize_fetch_result，讓抓取結果以自然口語呈現。
#   - tool_schema 遵循 JSON Schema 規範，可供 LLM 自動調用。
# ------------------------------------------------------------------------------------ #
PLUGIN_INFO = {
    "command": "/fetch",
    "icon": "🌐",
    "handler": "handle_web_fetch",
    "description": "抓取指定網址的網頁內容，返回標題和純文本摘要。",
    "intent_keywords": [
        ("/上網", "/fetch"),
    ],
    "update": "202608260224_我覺得可以版",
    "naturalize_func": "naturalize_fetch_result",



    "tool_schema": {
        "name": "web_fetch",
        "description": (
            "抓取指定網址的網頁內容，返回標題和純文本/Markdown 正文。\n\n"
            "【功能】給定一個 URL（http:// 或 https://），下載網頁，提取主要文本內容，轉換為易讀格式。\n"
            "自動處理重定向、編碼、反爬 UA 輪換，並會安裝缺失的依賴庫（需二次確認）。\n\n"
            "【返回格式】成功時返回 JSON：\n"
            "{\n"
            "  \"success\": true,\n"
            "  \"title\": \"網頁標題\",\n"
            "  \"content\": \"網頁正文（Markdown 格式，已截斷至 10000 字符）\",\n"
            "  \"url\": \"原始請求網址\"\n"
            "}\n\n"
            "失敗時返回 JSON：\n"
            "{\n"
            "  \"success\": false,\n"
            "  \"error\": \"錯誤訊息\"\n"
            "}\n"
            "或者如果缺少依賴庫，返回包含 `CONFIRM_SPLIT` 的特殊訊息，要求用戶確認安裝。\n\n"
            "【注意事項】\n"
            "- URL 必須以 http:// 或 https:// 開頭；若未提供協議，會自動補上 https://。\n"
            "- 網頁內容會自動轉為 Markdown，保留標題、鏈接、段落結構。\n"
            "- 內容最長返回約 10000 字符（可通過修改 `max_chars` 參數調整，但本工具未暴露該參數，如需調整需修改原始碼）。\n"
            "- 如果網站需要登錄或禁止爬取，可能抓取失敗。\n"
            "- 若系統缺少 `trafilatura`, `markdownify`, `readability-lxml`, `lxml_html_clean` 等庫，工具會返回安裝提示，LLM 應引導用戶執行 `/admin confirm <token>` 完成安裝。\n"
            "- 抓取結果會自動經由 `naturalize_fetch_result` 轉為自然語言輸出，LLM 無需額外處理。\n\n"
            "【何時使用】\n"
            "- 用戶要求「打開這個網頁看看內容」、「幫我讀取這篇文章」、「抓取網頁摘要」、「取得網頁標題和正文」時。\n"
            "- 需要從外部網站提取信息進行後續處理時。\n"
            "- 支援 JS 動態渲染：靜態抓取內容過少時會自動改用無頭瀏覽器（Playwright）渲染後再抓取；也可用 `mode` 參數強制控制。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "url": {
                    "type": "string",
                    "description": (
                        "要抓取的網頁地址，必須以 http:// 或 https:// 開頭。\n"
                        "範例：\n"
                        "- `https://example.com/article`\n"
                        "- `http://news.sina.com.cn`\n"
                        "- 如果用戶只給出 `example.com`，應自動補全為 `https://example.com` 再傳入。\n\n"
                        "注意：URL 中不能包含空格或特殊字符（除非已編碼）。"
                    )
                },
                "mode": {
                    "type": "string",
                    "enum": ["auto", "static", "render"],
                    "description": "抓取模式（可選）。auto=智能（預設）：靜態抓取內容太少時自動改用無頭瀏覽器渲染；static=僅靜態抓取（最快）；render=強制用無頭瀏覽器渲染 JS 後抓取（最完整，適合 SPA/動態頁面）。"
                }
            },
            "required": ["url"]
        }
    },




    "param_type": "dict"
}




# ---------- 全局導入 ----------
import logging
import html
import json
import time
import os
import re
import httpx
from typing import Union

import random
import asyncio
from typing import Optional, Dict


USER_AGENTS = [
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Firefox/120.0",
    "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
]




# ------------------------------------------------------------------------------------ #
# 函數: check_deps
# 用途: 一次性檢查網頁抓取所需的所有依賴庫（trafilatura, markdownify, lxml_html_clean）。
#       若缺少任何一項則返回清晰的安裝指引（附帶複製按鈕），否則返回 None 表示就緒。
# 設計:
#   在 fetch_webpage 一開始呼叫，確保抓取前環境完整。
#   如果檢查失敗，直接回傳錯誤訊息，不執行後續抓取邏輯，避免因缺失庫而崩潰。
#   特別注意 lxml_html_clean 是 trafilatura 的隱藏依賴，需一併檢查。
# 返回:
#   str | None: 若有缺失則回傳錯誤訊息（含安裝指令），否則 None。
# ------------------------------------------------------------------------------------ #
def check_deps():
    """檢查所需庫是否已安裝，返回錯誤信息或 None"""
    required_libs = ['trafilatura', 'markdownify', 'httpx', 'readability']
    missing = []
    for lib in required_libs:
        try:
            __import__(lib)
        except ImportError:
            missing.append(lib)
    if missing:
        missing.append("lxml_html_clean")
        # 修正包名：readability 的 pip 包是 readability-lxml
        corrected = []
        for pkg in missing:
            if pkg == "readability":
                corrected.append("readability-lxml")
            else:
                corrected.append(pkg)
        libs = " ".join(corrected)
        # 用反引號包裹庫名，防止 Markdown 吃掉下劃線
        libs_code = "`, `".join(corrected)
        return f"""❌ 缺少必要庫：`{libs_code}`

請執行以下命令安裝：
\n---CONFIRM_SPLIT---\n
/admin pip install {libs}
\n---CONFIRM_SPLIT---\n
完成後請輸入 /reload 重新加載工具。"""
    return None


# ------------------------------------------------------------------------------------ #
# 函數: _render_with_browser
# 用途: 使用 Playwright 無頭 Chromium 渲染網頁（執行 JS），提取完整正文。
#       適用於 SPA / JS 動態載入的頁面——靜態抓取只會看到空白殼或極少內容。
# 設計:
#   1. 設定 PLAYWRIGHT_BROWSERS_PATH 指向共享瀏覽器目錄（與 browser.py 一致）。
#   2. 啟動無頭 Chromium（no-sandbox，反偵測 init script）。
#   3. 導航到 URL，等待 domcontentloaded + networkidle + 額外等待 JS 渲染。
#   4. 自動滾動到底部觸發懶加載（lazy-load）。
#   5. 提取 <title>、meta description、document.body.innerText。
# 返回:
#   dict: { success, title, content, url, rendered }
# ------------------------------------------------------------------------------------ #
async def _render_with_browser(url: str, max_chars: int = 10000, wait_ms: int = 2500) -> dict:
    """用無頭瀏覽器渲染 JS 頁面並提取正文。"""
    try:
        from playwright.async_api import async_playwright
    except ImportError:
        return {"success": False, "error": "Playwright 未安裝，無法渲染 JS 頁面。請先 /browser install。"}

    os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/home/ubuntu/.mok/playwright-browsers")

    try:
        async with async_playwright() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                    "--disable-dev-shm-usage",
                ],
            )
            page = await browser.new_page(
                viewport={"width": 1280, "height": 900},
                user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            )
            # 反偵測腳本
            await page.add_init_script("""
                Object.defineProperty(navigator, 'webdriver', { get: () => false });
                Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
                Object.defineProperty(navigator, 'languages', { get: () => ['zh-TW', 'zh', 'en'] });
                window.chrome = { runtime: {} };
            """)

            # 導航（domcontentloaded 較快，networkidle 等待資源）
            try:
                await page.goto(url, wait_until="domcontentloaded", timeout=30000)
            except Exception:
                pass
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            await page.wait_for_timeout(wait_ms)

            # 滾動到底部觸發懶加載
            try:
                await page.evaluate("""
                    async () => {
                        for (let i = 0; i < 8; i++) {
                            window.scrollBy(0, document.body.scrollHeight);
                            await new Promise(r => setTimeout(r, 400));
                        }
                        window.scrollTo(0, 0);
                    }
                """)
                await page.wait_for_timeout(800)
            except Exception:
                pass

            # 提取正文
            title = (await page.title()) or ""
            body_text = (await page.evaluate("document.body ? document.body.innerText : ''")) or ""
            meta_desc = ""
            try:
                meta_desc = (await page.evaluate("""() => {
                    const m = document.querySelector('meta[name="description"]');
                    return m ? m.content : '';
                }""")) or ""
            except Exception:
                pass

            await browser.close()

        content = (body_text or "").strip()
        if meta_desc:
            content = f"{meta_desc}\n\n{content}"
        # 清理空白
        content = re.sub(r'\n\s*\n+', '\n\n', content)
        content = re.sub(r'[ \t]+', ' ', content)
        content = content.strip()

        if not content:
            return {"success": False, "error": "瀏覽器渲染後仍無法提取正文。"}

        if len(content) > max_chars:
            content = content[:max_chars] + "\n\n... (內容過長，已截斷)"

        return {
            "success": True,
            "title": title or "無標題",
            "content": content,
            "url": url,
            "rendered": True,
        }
    except Exception as e:
        return {"success": False, "error": f"瀏覽器渲染失敗: {str(e)}"}



# ------------------------------------------------------------------------------------ #
# 函數: fetch_webpage
# 用途: 使用 trafilatura 和 markdownify 下載網頁，提取正文並轉換為 Markdown 格式。
# 設計:
#   1. 先呼叫 check_deps() 確保所有依賴就緒，否則直接返回錯誤。
#   2. 自動補全 URL 協議（若缺失則添加 https://）。
#   3. 使用 trafilatura.fetch_url 獲取 HTML 原始內容。
#   4. 提取正文（保留 HTML 格式）或降級為純文本。
#   5. 使用 markdownify 將 HTML 正文轉換為 Markdown，保留標題、鏈接等結構。
#   6. 提取網頁標題（優先使用 trafilatura 的元數據，備用 <title> 標籤）。
#   7. 清理多餘空白和空行，並按 max_chars 截斷過長內容。
# 參數:
#   url: 目標網頁的 URL 字串。
#   max_chars: 最大返回字符數，預設 4000，防止輸出過長。
# 返回:
#   dict: 包含 success, title, content, url, error 等欄位的字典。
# ------------------------------------------------------------------------------------ #
async def fetch_webpage(url: str, max_chars: int = 10000, mode: str = "auto") -> dict:
    """
    使用 trafilatura 下載網頁，提取正文並轉換為 Markdown。
    :param url: 網頁地址
    :param max_chars: 最大返回字符數（避免過長）
    :return: dict { success, title, content, url, error }
    """

    # 先檢查依賴，未安裝則直接返回錯誤信息
    deps_error = check_deps()
    if deps_error:
        return {"success": False, "error": deps_error}

    # 依賴已滿足，此時才導入（確保安裝後可用）
    import trafilatura
    from markdownify import markdownify as md

    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url

    try:
        # 1. 下載 HTML（trafilatura 處理重定向、編碼等）
        # 手動 HTTP 請求（支持重試 + UA 輪換）
        downloaded = None
        last_error = None
        for attempt in range(3):
            ua = random.choice(USER_AGENTS)
            headers = {
                "User-Agent": ua,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "zh-TW,zh;q=0.9,en;q=0.8",
            }
            if attempt == 1:
                headers["Referer"] = "https://www.google.com/"
            try:
                async with httpx.AsyncClient(timeout=15.0, follow_redirects=True) as client:
                    resp = await client.get(url, headers=headers)
                    if resp.status_code == 200:
                        downloaded = resp.text
                        break
                    elif resp.status_code == 403:
                        last_error = f"HTTP 403 (嘗試 {attempt+1})"
                        await asyncio.sleep(1 * (attempt + 1))
                        continue
                    else:
                        last_error = f"HTTP {resp.status_code}"
                        break
            except Exception as e:
                last_error = str(e)
                await asyncio.sleep(1 * (attempt + 1))
        if not downloaded:
            # 回退到 trafilatura.fetch_url
            downloaded = trafilatura.fetch_url(url)
            if not downloaded:
                return {
                    "success": False,
                    "error_type": "network_error",
                    "error_message": f"無法下載網頁內容（最後錯誤: {last_error}）",
                    "tool": "web_fetch",
                    "original_args": url
                }

        # 2. 提取正文（保留基本格式的 HTML）
        # 2. 提取正文：優先使用 readability，回退到 trafilatura
        main_html = None
        try:
            from readability import Document
            doc = Document(downloaded)
            main_html = doc.summary()
        except ImportError:
            pass  # readability 未安裝，繼續使用 trafilatura

        if not main_html:
            # 回退到 trafilatura 提取 HTML
            main_html = trafilatura.extract(
                downloaded,
                include_formatting=True,
                include_links=True,
                include_images=False,
                output_format='html'
            )

        if main_html:
            # 將 HTML 轉為 Markdown
            content = md(main_html, heading_style="ATX", bullets="-")
        else:
            # 降級：嘗試 trafilatura 純文本
            main_text = trafilatura.extract(downloaded, include_formatting=False)
            if main_text:
                content = main_text
            else:
                # 最後手段：直接刪除所有 HTML 標籤
                content = re.sub(r'<[^>]+>', ' ', downloaded)
                content = re.sub(r'\s+', ' ', content).strip()
                if not content:
                    return {"success": False, "error": "無法提取網頁正文（可能頁面為空或全是廣告）"}
                









        # 4. 提取標題（優先使用 trafilatura 的元數據）
        title = trafilatura.extract(downloaded, include_formatting=False, output_format='txt')
        if title:
            # 取第一行作為標題
            title = title.split('\n')[0].strip()
        if not title:
            # 備用：從 HTML 提取 title 標籤
            title_match = re.search(r'<title[^>]*>(.*?)</title>', downloaded, re.IGNORECASE | re.DOTALL)
            title = title_match.group(1).strip() if title_match else "無標題"

        # 5. 清理內容（移除多餘空行，壓縮空白）
        content = re.sub(r'\n\s*\n', '\n\n', content)  # 保留段落間空行
        content = re.sub(r'[ \t]+', ' ', content)      # 壓縮行內空格
        content = content.strip()

        # 6. 截斷過長內容
        if len(content) > max_chars:
            content = content[:max_chars] + "\n\n... (內容過長，已截斷)"

        # 7. 智能回退：若靜態抓取內容太少（疑似 SPA / JS 動態載入），改用無頭瀏覽器渲染
        meaningful = len(re.sub(r"\s+", "", content))
        if mode == "render" or (mode == "auto" and meaningful < 200):
            rendered = await _render_with_browser(url, max_chars)
            if rendered.get("success") and len(re.sub(r"\s+", "", rendered.get("content", ""))) > meaningful:
                return rendered

        return {
            "success": True,
            "title": title,
            "content": content,
            "url": url
        }

    except httpx.TimeoutException:
        return {"success": False, "error": "請求超時，網站響應過慢。"}
    except httpx.HTTPStatusError as e:
        return {"success": False, "error": f"HTTP 錯誤: {e.response.status_code}"}
    except Exception as e:
        logging.exception("網頁抓取異常")
        return {"success": False, "error": f"抓取失敗: {str(e)}"}






















# ------------------------------------------------------------------------------------ #
# 函數: naturalize_fetch_result
# 用途: 將 fetch_webpage 返回的 JSON 結果轉換為口語化的繁體中文回覆。
# 設計:
#   1. 解析 JSON，若抓取失敗則直接返回錯誤訊息。
#   2. 取出標題、前 800 字內容，交給 LLM 生成 1-2 句總結。
#   3. 若 LLM 呼叫成功，返回「標題 + 總結 + 連結」的格式。
#   4. 若 LLM 失敗，降級返回「標題 + 前 300 字純文本預覽 + 連結」。
# 參數:
#   user_text: 使用者原始輸入（保留未使用，但簽名需匹配主程式）。
#   raw_result: fetch_webpage 返回的 JSON 字串。
#   ollama_api: Ollama API 端點，用於生成總結。
#   model_name: 使用的模型名稱。
#   temp_msg, context: 用於流式更新（此函數未實現流式，保留接口）。
# 返回:
#   str: 自然語言結果，可直接展示給使用者。
# ------------------------------------------------------------------------------------ #
async def naturalize_fetch_result(user_text: str, raw_result: str, ollama_api: str, model_name: str, temp_msg=None, context=None, agent_config: Optional[Dict] = None) -> str:
    """
    將 JSON 抓取結果轉為自然口語回覆（直接返回完整正文）。
    """
    try:
        data = json.loads(raw_result)
    except:
        return raw_result
    if not data.get("success"):
        return f"❌ 無法讀取網頁：{data.get('error', '未知錯誤')}"
    title = data.get("title", "無標題")
    content = data.get("content", "")
    url = data.get("url", "")

    # 直接返回完整內容，不經過 LLM 壓縮
    return f"📄 **{title}**\n\n{content}\n\n🔗 {url}"






















# ------------------------------------------------------------------------------------ #
# 函數: handle_web_fetch
# 用途: 網頁抓取工具的總入口，負責解析參數、呼叫核心抓取函數、返回 JSON 結果。
# 設計:
#   1. 無參數時顯示幫助訊息（含自然語言觸發詞）。
#   2. 支援兩種輸入格式：命令列字串（"/fetch https://..."）與字典（JSON 工具呼叫）。
#   3. 提取 URL 後直接呼叫 fetch_webpage，並將其返回值轉為 JSON 字串返回。
#   注意：依賴檢查已由 fetch_webpage 內部完成，此處無需重複檢查。
# 參數:
#   args: 可以是字串（直接輸入網址）或字典 {"url": "https://..."}。
#   chat_id: 使用者 ID（此處未使用，保留簽名一致性）。
# 返回:
#   str: JSON 字串，包含 success, title, content, url, error 等欄位。
# ------------------------------------------------------------------------------------ #
async def handle_web_fetch(args: Union[str, dict], chat_id: str = None, agent_config: Optional[Dict] = None) -> str:
    """
    處理 /fetch 命令或工具調用。
    args: 可以是字符串（直接輸入網址）或字典 {"url": "https://..."}
    """

    if not args:
        help_text = f'''
{PLUGIN_INFO["icon"]} 取網頁內容說明：
獲取指定網址的文本內容，返回頁面標題和純文本摘要。
<pre>/fetch url</pre>

=====
🧩 自然語言意圖辨識：
'''
        # 動態添加 intent_keywords（不轉義）
        for keyword, cmd in PLUGIN_INFO["intent_keywords"]:
            help_text += f'   "{keyword}" → {cmd}\n'
        return help_text

    mode = "auto"
    if isinstance(args, dict):
        url = args.get("url", "").strip()
        mode = args.get("mode", "auto")
    else:
        url = args.strip()


    result = await fetch_webpage(url, mode=mode)
    return json.dumps(result, ensure_ascii=False)


