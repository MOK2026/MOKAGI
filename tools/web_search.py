# ------------------------------------------------------------------------------------ #
# 字典: PLUGIN_INFO
# 用途: 定義搜尋工具與主程式、意圖辨識系統之間的介面。主程式透過它：
#         1. 註冊 Telegram 命令 /search
#         2. 建立自然語言關鍵詞對映 ("搜尋" → /search)
#         3. 提供給 AI 的工具描述 (tool_schema) 以便未來 LLM 自動呼叫
#         4. 指定執行函式 handle_web_search 與結果自然化函式 naturalize_search_result
# 設計:
#   - naturalize_func 指向本模組內的 naturalize_search_result，讓搜尋結果能以自然口語呈現。
#   - tool_schema 遵循 JSON Schema 規範，將來可讓 AI 判斷何時需要搜尋並填入引數。
# ------------------------------------------------------------------------------------ #
PLUGIN_INFO = {
    "command": "/search",
    "icon": "🔍",
    "handler": "handle_web_search",
    "description": "搜索網頁（DuckDuckGo + Tavily），可指定時間範圍(d/w/m/y)，返回標題、摘要和鏈接。",
    "intent_keywords": [
        ("/搜", "/search"),
        ("/查", "/search"),
        ("/找", "/search"),
    ],
    "update": "202608110022_出街版",
    "naturalize": True,
    "naturalize_func": "naturalize_search_result",

    "tool_schema": {
        "name": "web_search",
        "description": (
            "搜索網頁資訊，同時使用 DuckDuckGo 和 Tavily（需配置 API Key）兩個來源，合併並去重結果。\n\n"
            "【功能】根據用戶提供的查詢詞，返回相關網頁的標題、摘要和鏈接。\n"
            "可選時間範圍過濾（最近 24 小時 / 一週 / 一月 / 一年），提高時效性。\n\n"
            "【參數】\n"
            "- `query`（必需）：搜索關鍵詞，應清晰具體。例如：「Python 非同步程式設計」、「2026 世界盃 賽程」。\n"
            "- `timelimit`（可選）：時間範圍。有效值：`d`（24小時內）、`w`（一週內）、`m`（一月內）、`y`（一年內）。若不提供則不限時間。\n\n"
            "【返回格式】成功時返回 JSON：\n"
            "{\n"
            "  \"success\": true,\n"
            "  \"query\": \"原始查詢詞\",\n"
            "  \"total\": 結果數量,\n"
            "  \"results\": [\n"
            "    {\n"
            "      \"title\": \"網頁標題\",\n"
            "      \"body\": \"摘要（約 200 字符）\",\n"
            "      \"href\": \"完整鏈接 URL\"\n"
            "    }\n"
            "  ],\n"
            "  \"errors\": [\"可選，記錄某個來源的錯誤信息\"]\n"
            "}\n\n"
            "失敗時返回 JSON（例如依賴缺失、無結果）：\n"
            "{\n"
            "  \"success\": false,\n"
            "  \"error_type\": \"dependency_missing\" / \"no_results\" / ...,\n"
            "  \"error_message\": \"詳細錯誤說明\",\n"
            "  \"tool\": \"web_search\",\n"
            "  \"original_args\": \"原始參數\"\n"
            "}\n\n"
            "【依賴與配置】\n"
            "- 需要安裝 `duckduckgo-search` 和 `tavily-python`。若缺少，工具會返回包含 `/admin pip install ...` 安裝指令的錯誤信息，LLM 應引導用戶執行。\n"
            "- 若需要使用 Tavily 搜索（推薦），必須在 Agent 配置文件（如 `~/.mok/.稚`）中設置 `TAVILY_API_KEY=你的金鑰`。\n"
            "- 即使未配置 Tavily，工具仍會使用 DuckDuckGo 搜索，但結果可能較少。\n\n"
            "【何時使用】\n"
            "- 用戶要求「搜索」、「查詢」、「找一下」、「有沒有關於...的資訊」等。\n"
            "- 用戶想知道最新新聞、事實、產品資訊、技術問題解決方案等需要外部資訊的場景。\n"
            "- 用戶明確要求引用來源或網頁鏈接時。\n\n"
            "【注意】\n"
            "- 返回的 `body` 是網頁內容的摘要，可能不完整，如需詳細內容可結合 `web_fetch` 工具抓取完整網頁。\n"
            "- 時間範圍過濾在 DuckDuckGo 上效果較好，Tavily 的時間過濾可能不完全準確。\n"
            "- 如果兩個搜索源都失敗或返回空結果，工具會返回失敗 JSON，LLM 應告知用戶並建議更換關鍵詞。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": "搜索關鍵詞。應清晰、具體，避免過於模糊。若用戶輸入多個詞，直接拼接即可。範例：「香港 天氣 2026-06-15」、「Python asyncio 教學」。"
                },
                "timelimit": {
                    "type": "string",
                    "description": "可選，時間範圍過濾。`d` = 過去24小時，`w` = 過去一週，`m` = 過去一月，`y` = 過去一年。若用戶要求「最近」、「最新」、「今天」等，應設為 `d`；「本週」設為 `w`；「本月」設為 `m`；「今年」設為 `y`。若不確定則省略此參數。",
                    "enum": ["d", "w", "m", "y"]
                }
            },
            "required": ["query"]
        }
    },

}

import logging, html, json, time, os, asyncio, httpx
from typing import Union, Dict, List

from typing import Optional, Dict





















# ------------------------------------------------------------------------------------ #
# 函式: load_agent_config_value
# 用途: 從當前 agent 的配置檔案中讀取指定 key 的值（例如 TAVILY_API_KEY）。
# 設計:
#   利用主程式設定的環境變數 AD_MOK_AGENT_NAME 和 AD_AgiName 找到對應的配置檔。
#   逐行掃描，支援註解 (#) 和簡單的 key=value 格式，不回傳多餘空格。
#   這樣每個工具都能獨立讀取 agent 專屬的設定，無需修改主程式。
# 返回:
#   str: 找到的值；若找不到則回空字串。
# ------------------------------------------------------------------------------------ #
def load_agent_config_value(key: str, agent_config: Optional[Dict] = None) -> str:
    """從當前 agent 的配置檔案中讀取指定 key 的值"""
    if agent_config is None:
        import mokagi
        agent_config = mokagi._agent_config
    return agent_config.get(key, "")
































# ------------------------------------------------------------------------------------ #
# 全域變數: TAVILY_API_KEY
# 用途: 儲存從配置檔讀取的 Tavily API Key，避免每次搜尋都重新讀取。
#       會在 check_search_deps() 中被賦值，並由 _do_search_via_tavily 使用。
# ------------------------------------------------------------------------------------ #
#TAVILY_API_KEY = False



# ------------------------------------------------------------------------------------ #
# 函式: check_search_deps
# 用途: 一次性檢查所有搜尋依賴（Tavily 庫、API Key、DuckDuckGo 庫），
#       若缺少任何一項則返回清晰的安裝指引（附帶複製按鈕），否則返回 None 表示就緒。
# 設計:
#   在 handle_web_search 一開始呼叫，確保搜尋前環境完整。
#   如果檢查失敗，直接回傳錯誤訊息，不執行搜尋，避免後續不明錯誤。
#   同時將讀取到的 API Key 存入全域變數供後續使用。
# 返回:
#   str | None: 若有缺失則回傳錯誤訊息，否則 None。
# ------------------------------------------------------------------------------------ #
def check_search_deps(agent_config: Optional[Dict] = None) -> tuple[str | None, str | None]:
    """檢查搜尋依賴，返回 (error_message, api_key)"""
    missing = []
    if agent_config is None:
        import mokagi
        agent_config = mokagi._agent_config

    try:
        from tavily import TavilyClient
    except ImportError:
        missing.append("tavily-python 未安裝")

    api_key = agent_config.get("TAVILY_API_KEY", "")
    if not api_key:
        missing.append("Tavily API Key 未配置")

    try:
        from duckduckgo_search import DDGS
    except ImportError:
        missing.append("duckduckgo-search 未安裝")

    if not missing:
        return None, api_key

    msg = "❌ 以下搜尋依賴缺失，請依序處理：\n\n"
    for item in missing:
        if "tavily-python" in item:
            msg += "🔹 安裝 Tavily 庫：\n<pre>/admin pip install tavily-python</pre>\n\n"
        elif "Tavily API Key" in item:
            msg += "🔹 配置 Tavily API Key：\n在配置文件中新增：\n<pre>TAVILY_API_KEY=tvly-你的key</pre>\n\n"
        elif "duckduckgo-search" in item:
            msg += "🔹 安裝 DuckDuckGo 搜尋庫：\n<pre>/admin pip install duckduckgo-search</pre>\n\n"
    msg += "完成後請 /reload 重新載入工具。"
    return msg, None




















# ------------------------------------------------------------------------------------ #
# 函式: _do_search_via_tavily
# 用途: 使用 Tavily API 執行搜尋，回傳統一的 JSON 結構。
# 設計:
#   從全域變數 TAVILY_API_KEY 初始化 TavilyClient。
#   因為 TavilyClient.search 是同步函式，使用 asyncio.to_thread 以免阻塞事件迴圈。
#   回傳格式與 DuckDuckGo 搜尋一致，便於合併。
# 返回:
#   dict: {"success": bool, "query": ..., "total": ..., "results": [...]}
# ------------------------------------------------------------------------------------ #
async def _do_search_via_tavily(params: dict, api_key: str) -> dict:
    """使用 Tavily API 進行搜尋"""

    from tavily import TavilyClient

    # 初始化 Tavily 客戶端（API Key 從環境變數或配置檔案讀取）
    #TAVILY_API_KEY = os.environ.get("TAVILY_API_KEY", "")
    tavily_client = TavilyClient(api_key=api_key) if api_key else None

    if not tavily_client:
        return {"success": False, "error": "Tavily API Key 未配置，請在環境變數中設定 TAVILY_API_KEY"}

    query = params.get("query", "")
    if not query:
        return {"success": False, "error": "缺少搜尋關鍵詞"}

    # Tavily 深度：basic 較快，advanced 更深入但稍慢
    search_depth = params.get("search_depth", "basic")
    max_results = min(params.get("max_results", 5), 10)

    logging.info(f"Searching via Tavily: {query}, depth: {search_depth}")

    try:
        # TavilyClient.search 是同步的，線上程池中執行以避免阻塞
        response = await asyncio.to_thread(
            tavily_client.search,
            query,
            search_depth=search_depth,
            max_results=max_results
        )

        items = []
        for r in response.get("results", [])[:max_results]:
            items.append({
                "title": r.get("title", ""),
                "body": r.get("content", "")[:200],
                "href": r.get("url", "")
            })

        if not items:
            return {"success": False, "error": "未找到相關結果"}

        return {
            "success": True,
            "query": query,
            "total": len(items),
            "results": items
        }

    except Exception as e:
        logging.exception("Tavily 搜尋失敗")
        return {"success": False, "error": f"搜尋異常: {html.escape(str(e))}"}























# ------------------------------------------------------------------------------------ #
# 函式: _do_search_duckduckgo_async
# 用途: 這是為了不修改太多舊程式碼的方便操作，將同步的 DuckDuckGo 搜尋包裝成非同步，以便與 Tavily 併發執行。
# ------------------------------------------------------------------------------------ #
async def _do_search_duckduckgo_async(params: dict) -> dict:
    """非同步包裝 DuckDuckGo 搜尋，以便併發執行"""
    return await asyncio.to_thread(_do_search_duckduckgo, params)
# ------------------------------------------------------------------------------------ #
# 函式: _do_search_duckduckgo
# 用途: 使用 DuckDuckGo 執行搜尋，支援重試、時間範圍過濾與地區自動判斷。
# 設計:
#   當查詢包含中文時，自動使用香港繁體地區 (hk-tzh) 以提升相關性。
#   如果搜尋結果少於 3 條且有限制時間，會自動放寬時間限制再試一次。
#   遇到 HTTP 202/429 時會指數退避重試，最多重試 3 次。
# 返回:
#   dict: 與 Tavily 相同的標準結構。
# 每個結果的 body 截斷為 200 字符
# ------------------------------------------------------------------------------------ #
def _do_search_duckduckgo(params: dict, max_retries: int = 3) -> dict:
    """
    帶重試機制的搜尋執行器。
    處理 Rate Limit (HTTP 202/429) 和臨時性錯誤 (5xx)。
    """
    from duckduckgo_search import DDGS

    query = params.get("query", "")
    if not query:
        return {"success": False, "error": "缺少搜尋關鍵詞"}

    timelimit = params.get("timelimit")
    time_map = {"d": "d", "w": "w", "m": "m", "y": "y"}
    if timelimit and timelimit not in time_map:
        return {"success": False, "error": f"時間範圍無效（僅支援 d, w, m, y）"}

    tl = time_map.get(timelimit) if timelimit else None

    # 優先使用 params 中的 region，若無則根據查詢智慧判斷
    region = params.get("region", None)
    if not region:
        # 若查詢中包含中文，則預設使用香港地區設定
        if any('\u4e00' <= char <= '\u9fff' for char in query):
            region = 'hk-tzh'  # 香港繁體中文
        else:
            region = 'wt-wt'   # 其他情況保持全球

    logging.info(f"Web search JSON: {query}, timelimit: {tl}, region: {region}")

    last_error = None
    for attempt in range(max_retries):
        try:
            with DDGS() as ddgs:
                results = list(ddgs.text(
                    keywords=query,
                    region=region,        # <--- 使用動態設定的 region
                    timelimit=tl,
                    max_results=5
                ))
            
            # 搜尋成功，整理結果
            items = []
            for res in results:
                items.append({
                    "title": res.get("title", ""),
                    "body": res.get("body", "")[:200],
                    "href": res.get("href", "")
                })
                
            # 如果結果太少，可能是因為過濾太嚴格，嘗試放寬
            if len(items) < 3 and timelimit:
                logging.info(f"結果少於3條，嘗試放寬時間限制重新搜尋")
                # 直接以無時間限制再搜一次
                with DDGS() as ddgs:
                    results = list(ddgs.text(
                        keywords=query,
                        region=region,
                        max_results=5
                    ))
                items = []
                for res in results:
                    items.append({
                        "title": res.get("title", ""),
                        "body": res.get("body", "")[:200],
                        "href": res.get("href", "")
                    })

            return {
                "success": True,
                "query": query,
                "timelimit": timelimit if timelimit else "any",
                "total": len(items),
                "results": items
            }

        except Exception as e:
            last_error = e
            error_str = str(e).lower()
            
            # 判斷是否為可重試的錯誤
            is_ratelimit = any(code in error_str for code in ["202", "429", "ratelimit", "rate limit"])
            is_server_error = "5" in error_str
            
            if (is_ratelimit or is_server_error) and attempt < max_retries - 1:
                wait_time = 2 ** attempt  # 指數退避：2秒, 4秒, 8秒...
                logging.warning(f"搜尋遇到可重試錯誤，將在 {wait_time} 秒後進行第 {attempt + 2} 次嘗試。錯誤: {e}")
                time.sleep(wait_time)
                continue
            else:
                logging.exception(f"duckduckgo 搜尋失敗 (嘗試次數: {attempt + 1})")
                break
    
    # 所有重試都失敗
    return {"success": False, "error": f"搜尋異常: {html.escape(str(last_error))}"}































# ------------------------------------------------------------------------------------ #
# 函式: naturalize_search_result
# 用途: 將搜尋結果 JSON 轉換為自然口語的回覆，包含總結和完整連結列表。
# 設計:
#   取前三個標題讓 LLM 生成 1-2 句口語總結，減少 token 消耗並確保穩定性。
#   支援流式輸出：若提供 temp_msg 和 context，會即時更新 Telegram 訊息顯示思考過程。
#   若流式失敗則自動回退到非流式請求，若仍失敗則使用備用的規則摘要。
#   最後將所有結果的連結以編號清單附加在回覆結尾。
# 返回:
#   str: 包含自然語言總結與連結的最終回覆字串。
# "num_predict": 1000 對整個回覆的總長度大約能生成 500~1000 箇中文字
# ------------------------------------------------------------------------------------ #
async def naturalize_search_result(user_text: str, raw_result: str, ollama_api: str, model_name: str, temp_msg=None, context=None) -> str:
    """
    搜尋專用的自然化函式，支援流式顯示思考過程。
    返回包含概述和連結列表的最終回覆字串。
    """
    # 先嚐試解析 JSON
    try:
        data = json.loads(raw_result)
    except json.JSONDecodeError:
        # 不是 JSON，可能已經是幫助文本、錯誤提示等，直接返回
        return raw_result
    # 解析 JSON，提取標題和所有連結
    titles = []
    all_links = []
    try:
        data = json.loads(raw_result)
        if data.get("success") and data.get("total", 0) > 0:
            results = data.get("results", [])
            all_links = [item.get("href", "") for item in results]
            for item in results[:3]:
                titles.append(item.get("title", ""))
        else:
            return "這次搜尋沒有找到結果。"
    except Exception as e:
        logging.warning(f"解析 JSON 失敗: {e}")
        return "搜尋結果解析失敗。"

    if not titles:
        return "未找到相關結果。"

    # 給 LLM 的 prompt
    title_text = "、".join(titles)
    prompt = f"""搜尋查詢：{user_text}
相關標題：{title_text}
用2-3句自然語言總結搜尋結果（提及重點標題關鍵詞），不編造細節，最後給出建議。直接回復："""

    # 流式請求 Ollama
    payload = {
        "model": model_name,
        "prompt": prompt,
        "stream": True,
        "options": {
            "num_predict": 4000,
            "temperature": 0.7,
            "top_p": 0.9,
        }
    }

    print(f'''
============ web_search ============
    {prompt}
========================
    ''')
    accumulated = ""
    if temp_msg and context:
        last_update = 0
        try:
            async with httpx.AsyncClient(timeout=60) as client:
                async with client.stream("POST", ollama_api, json=payload) as resp:
                    resp.raise_for_status()
                    async for line in resp.aiter_lines():
                        if not line:
                            continue
                        try:
                            chunk = json.loads(line)
                            token = chunk.get("response", "")
                            if token:
                                accumulated += token
                        except:
                            continue

                        now = time.time()
                        if len(accumulated) - last_update > 50 or (now - last_update > 0.15):
                            display_text = accumulated + "\n\n⏳ 正在整理結果..."
                            try:
                                await context.bot.edit_message_text(
                                    chat_id=temp_msg.chat_id,
                                    message_id=temp_msg.message_id,
                                    text=display_text
                                )
                                last_update = len(accumulated)
                            except:
                                pass
        except Exception as e:
            logging.warning(f"流式 {e}")
            # 回退到非流式
            try:
                async with httpx.AsyncClient(timeout=30) as client:
                    resp = await client.post(ollama_api, json={**payload, "stream": False})
                    data_resp = resp.json()
                    accumulated = data_resp.get("response", "").strip()
            except:
                pass
    else:
        # 無 temp_msg 時直接非流式生成
        try:
            async with httpx.AsyncClient(timeout=30) as client:
                resp = await client.post(ollama_api, json={**payload, "stream": False})
                data_resp = resp.json()
                accumulated = data_resp.get("response", "").strip()
        except:
            pass

    # 組裝最終回覆
    reply_parts = []
    summary = accumulated.strip()
    if summary:
        reply_parts.append(summary)
    else:
        if len(titles) == 1:
            reply_parts.append(f"為你找到一篇關於“{titles[0]}”的文章。")
        else:
            reply_parts.append(f"為你找到 {len(all_links)} 篇相關文章，包括“{titles[0]}”等。")

    for i, link in enumerate(all_links, 1):
        reply_parts.append(f"{i}. {link}")

    return "\n\n".join(reply_parts)


































# ------------------------------------------------------------------------------------ #
# 函式: handle_web_search
# 用途: 搜尋工具的總入口，負責解析引數、檢查依賴、併發呼叫 Tavily 和 DuckDuckGo，
#       合併並去重結果，最後回傳標準 JSON 字串。
# 設計:
#   先呼叫 check_search_deps() 確保所有依賴就緒，否則直接返回指引訊息。
#   支援兩種輸入格式：命令列字串（"/search 關鍵詞 y"）與字典（JSON 工具呼叫）。
#   使用 asyncio.gather 併發兩個搜尋來源，提升速度，並設定 return_exceptions 防止單一失敗中斷流程。
#   合併結果時進行簡單 URL 去重，若無任何結果則回傳錯誤。
# 返回:
#   str: JSON 字串，包含 success、results 等欄位。
# ------------------------------------------------------------------------------------ #
async def handle_web_search(args: Union[str, dict], chat_id: str = None, agent_config: Optional[Dict] = None) -> str:
    if agent_config is None:
        import mokagi
        agent_config = mokagi._agent_config
    """
    處理搜尋請求，同時使用 Tavily 和 DuckDuckGo，合併結果。
    支援命令列字串 /search 關鍵詞 [d|w|m|y]
    以及 dict 引數（JSON 工具呼叫）
    """

    # ========== 新增：參數規範化與有效性檢查 ==========
    query = None
    timelimit = None

    if isinstance(args, dict):
        # 如果是字典，提取 query 和 timelimit
        query = args.get("query", "").strip()
        timelimit = args.get("timelimit")
    elif isinstance(args, str):
        # 如果是字符串，解析
        args = args.strip()
        if not args:
            # 空字符串 → 返回幫助
            return _get_search_help_text()
        parts = args.split()
        time_map = {"d": "d", "w": "w", "m": "m", "y": "y"}
        query_parts = []
        for p in parts:
            if p in time_map:
                timelimit = time_map[p]
            else:
                query_parts.append(p)
        query = " ".join(query_parts).strip()
    else:
        # 未知類型，返回幫助
        return _get_search_help_text()

    # 檢查是否有有效的查詢詞
    if not query:
        return _get_search_help_text()

    # ========== 依賴檢查 ==========
    dep_error, api_key = check_search_deps(agent_config)
    if dep_error:
        return json.dumps({
            "success": False,
            "error_type": "dependency_missing",
            "error_message": dep_error,
            "tool": "web_search",
            "original_args": str(args)
        }, ensure_ascii=False)

    # 構造參數
    params = {"query": query}
    if timelimit:
        params["timelimit"] = timelimit

    # 其餘邏輯保持不變（合併 Tavily 和 DuckDuckGo）
    ddg_params = params.copy()
    tavily_params = {"query": params["query"]}

    results_tavily, results_ddg = await asyncio.gather(
        _do_search_via_tavily(tavily_params, api_key),
        _do_search_duckduckgo_async(ddg_params),
        return_exceptions=True
    )

    all_items = []
    errors = []

    # 處理 Tavily 結果
    if isinstance(results_tavily, dict) and results_tavily.get("success"):
        all_items.extend(results_tavily.get("results", []))
    elif isinstance(results_tavily, Exception):
        errors.append(f"Tavily: {str(results_tavily)}")
    elif isinstance(results_tavily, dict) and not results_tavily.get("success"):
        errors.append(f"Tavily: {results_tavily.get('error', '未知錯誤')}")

    # 處理 DuckDuckGo 結果
    if isinstance(results_ddg, dict) and results_ddg.get("success"):
        all_items.extend(results_ddg.get("results", []))
    elif isinstance(results_ddg, Exception):
        errors.append(f"DuckDuckGo: {str(results_ddg)}")
    elif isinstance(results_ddg, dict) and not results_ddg.get("success"):
        errors.append(f"DuckDuckGo: {results_ddg.get('error', '未知錯誤')}")

    # 去重
    seen_urls = set()
    unique_items = []
    for item in all_items:
        url = item.get("href")
        if url and url not in seen_urls:
            seen_urls.add(url)
            unique_items.append(item)

    if unique_items:
        return json.dumps({
            "success": True,
            "query": params["query"],
            "total": len(unique_items),
            "results": unique_items,
            "errors": errors if errors else None
        }, ensure_ascii=False)
    else:
        return json.dumps({
            "success": False,
            "error_type": "no_results",
            "error_message": "所有搜尋源均未返回結果",
            "tool": "web_search",
            "original_args": str(args),
            "details": errors
        }, ensure_ascii=False)


def _get_search_help_text() -> str:
    """返回搜索幫助文本"""
    help_text = f'''
    {PLUGIN_INFO["icon"]} 搜尋使用說明：

        搜尋網頁（由 DuckDuckGo 、 TAVILY(需API)提供）
        返回標題、摘要與連結。可指定時間範圍
        <pre>搜尋 [關鍵詞] [時間篩選]</pre>
        例：
        <pre>搜尋 香港新聞 w</pre>
        <pre>/search 香港新聞 w</pre>
        時間篩選:
        "d": "天", "w": "週", "m": "月", "y": "年"

=====
🧩 自然語言意圖辨識：
'''
    # 動態添加 intent_keywords（不轉義）
    for keyword, cmd in PLUGIN_INFO["intent_keywords"]:
        help_text += f'   "{keyword}" → {cmd}\n'
    return help_text






