# ------------------------------------------------------------------------------------ #
# 字典: PLUGIN_INFO
# 用途: 定義一個工具外掛與主程式、意圖辨識系統之間的介面協定。
#       每個工具模組頂部都必須定義這個字典，主程式會透過它來：
#         1. 註冊 Telegram 命令 (command)
#         2. 建立自然語言關鍵詞映射 (intent_keywords)
#         3. 提供給 LLM 的工具描述 (tool_schema)
#         4. 指定執行函數 (handler) 與結果自然化函數 (naturalize_func)
# 設計:
#   - 所有欄位皆為選填（除 command、handler、description 外），但建議完整填寫以獲得最佳體驗。
#   - 若工具會回傳 JSON，建議實作 naturalize_func，讓結果以自然語言呈現。
#   - tool_schema 是給 LLM 看的「工具說明書」，用 JSON Schema 格式描述參數與用途，
#     讓 AI 能在需要時自動調用工具（未來擴展用）。
# 欄位說明:
#   command           : Telegram 命令，例如 "/search"。
#   icon              : 命令的圖示（目前僅供參考）。
#   handler           : 負責處理該命令的函數名稱（字串），主程式會用 getattr 取得實際函數。
#   description       : 簡短的功能描述，會出現在命令選單和說明訊息中。
#   intent_keywords   : 自然語言觸發關鍵詞列表，支援兩種格式：
#                         - 字串: "搜尋" → 自動對應到根命令。
#                         - 元組: ("記住", "/memory remember") → 精確指定子命令。
#   update            : 最後更新日期，純註記用。
#   naturalize        : (舊版欄位) 標記是否允許自然化，現已被 naturalize_func 取代。
#   naturalize_func   : (新版欄位) 指定結果自然化函數的名稱，必須是模組內的 async 函數。
#                       簽名: async def func(user_text, raw_result, ollama_api, model_name, temp_msg, context) -> str
#   tool_schema       : 提供給 AI 的工具定義 (JSON Schema)，包含 name、description、parameters。
#                       用於 LLM 工具調用，讓模型自行決定何時該使用此工具。
# ------------------------------------------------------------------------------------ #

PLUGIN_INFO = {
    "command": "/intent",
    "icon":"🧩",
    "description": "內部意圖識別引擎，非必要不需要直接調用。用於將自然語言轉換為命令。",
    "handler": "dummy_handler",
    "tool_schema": {
        "name": "intent",
        "description": "內部意圖識別引擎，用於將自然語言轉換為命令，非必要不需要直接調用。",
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "需要識別的用戶輸入文本"
                }
            },
            "required": ["text"]
        }
    },
    "update": "202605250320"
}





















import httpx, json, logging, re, html, time, os
import mokagi
from typing import Dict, Any
# 全域變數，由主程式透過鉤子傳入
_cmd_map = {}
_tools = {}
_ollama_api = ""
_model_name = ""
# 模型回應的最大等待時間（秒），超過則認定為失敗，避免用戶長時間等待
_model_timeout = 300.0












# ------------------------------------------------------------------------------------ #
# 函數: build_keyword_map
# 用途: 從所有已載入的工具外掛中，自動收集「自然語言關鍵詞 → 完整命令」的對應表。
# 設計:
#   每個工具可以在 PLUGIN_INFO 的 intent_keywords 中定義兩種格式:
#     1. 簡單字串:         "搜尋"            → 自動對應到該工具的根命令 (如 /search)
#     2. 元組 (關鍵詞, 命令): ("記住", "/memory remember") → 明確指定完整命令
#   若工具未定義 intent_keywords，則自動從工具的描述文字中提取中文字詞當作關鍵詞（較弱）。
#   這樣設計是為了讓使用者能夠用自然語言觸發工具，同時支援工具的獨立定義，無需修改意圖模組。
# 參數:
#   cmd_map: dict {命令字串: handler函數}，由主程式提供。
#   tools: dict {模組名: 模組物件}，包含所有已載入的外掛。
# 返回:
#   dict: { "關鍵詞": "/完整命令" }
# ------------------------------------------------------------------------------------ #
def build_keyword_map(cmd_map: dict, tools: dict) -> Dict[str, str]:
    """返回 {關鍵詞: 完整命令} 映射，支援兩種格式：
       - 簡單關鍵詞列表: ["關鍵詞"] → 映射到該插件的默認命令（cmd）
       - 元組列表: [("關鍵詞", "/完整 命令")] → 映射到指定的完整命令
    """
    kw_map = {}
    for cmd, handler in cmd_map.items():
        # 找到對應的 module
        mod = None
        for name, m in tools.items():
            if hasattr(m, "PLUGIN_INFO") and m.PLUGIN_INFO.get("command") == cmd:
                mod = m
                break
        if not mod or not hasattr(mod, "PLUGIN_INFO"):
            continue
        info = mod.PLUGIN_INFO
        keywords_raw = info.get("intent_keywords", [])
        if not keywords_raw:
            # 若無關鍵詞，可根據指令名稱和描述產生簡單詞（原有邏輯）
            desc = info.get("description", "")
            words = re.findall(r'[\u4e00-\u9fa5]+', desc)
            keywords = words if words else [cmd.lstrip("/")]
            for kw in keywords:
                kw_map[kw.lower()] = cmd
        else:
            # 處理新的元組格式或傳統列表
            for item in keywords_raw:
                if isinstance(item, tuple) and len(item) == 2:
                    keyword, full_cmd = item
                    kw_map[keyword.lower()] = full_cmd
                elif isinstance(item, str):
                    # 傳統格式：關鍵詞映射到插件的根命令
                    kw_map[item.lower()] = cmd
                #else:
                #    logging.warning(f"忽略錯誤格式的 intent_keywords 項目: {item}")
    return kw_map


















# ------------------------------------------------------------------------------------ #
# 函數: rule_based_intent
# 用途: 根據預先建立的關鍵字映射表，快速判斷使用者輸入是否命中某個工具的關鍵詞。
# 設計:
#   這層規則匹配比 LLM 快且免費，適合處理常見的簡單命令（如「搜尋」、「記住」、「日誌」）。
#   當關鍵詞出現在使用者訊息中，就提取掉關鍵詞後的剩餘文字作為參數。
#   注意：如果多個關鍵詞重疊，只會匹配第一個找到的，順序取決於工具載入順序。
# 參數:
#   user_text: 使用者輸入的原始字串。
#   kw_map: build_keyword_map 建立的關鍵詞映射表。
# 返回:
#   tuple (完整命令, 參數字串) 或 (None, None)
# ------------------------------------------------------------------------------------ #
async def rule_based_intent(user_text: str, kw_map: dict) -> tuple:
    """回傳 (完整命令, 參數) 或 (None, None)"""
    text_lower = user_text.lower()
    for kw, full_cmd in kw_map.items():
        if kw in text_lower:
            # 提取參數：移除第一個匹配的關鍵詞後的部分
            # 注意：用戶訊息可能包含關鍵詞的前後文，直接移除關鍵詞
            # 簡單做法：用正則替換第一個出現的關鍵詞（不區分大小寫）
            pattern = re.compile(re.escape(kw), re.IGNORECASE)
            args = pattern.sub('', user_text, count=1).strip()
            return full_cmd, args
    return None, None

























# ------------------------------------------------------------------------------------ #
# 函數: llm_intent
# 用途: 當規則匹配失敗時，使用本地 LLM (Ollama) 進行意圖分類，輸出 JSON 格式的命令。
# 設計:
#   動態生成 prompt，列出所有可用的命令及其說明，要求 LLM 輸出 {"command": ..., "args": ...}。
#   這樣即使使用者說法稍微變化，也能被正確識別，而不需要逐一維護大量規則。
#   使用較低的 temperature (0.1) 以保證輸出穩定性，並限制生成 token 數量以加速回應。
#   如果 LLM 失敗或回傳格式不正確，則返回 (None, None) 讓上層繼續處理。
# 參數:
#   user_text: 使用者輸入。
#   cmd_map: 命令映射表。
#   tools: 所有工具模組。
#   ollama_api: Ollama API 端點。
#   model_name: 使用的模型名稱。
# 返回:
#   tuple (完整命令, 參數字串) 或 (None, None)
# ------------------------------------------------------------------------------------ #
async def llm_intent(user_text: str, cmd_map: dict, tools: dict, ollama_api: str, model_name: str) -> tuple:
    """使用 LLM 分類意圖，動態生成 prompt（通用版，不硬編碼任何工具）"""

    agent_name = os.environ.get("MOK_AGENT_NAME")
    MOK_ADMIN_NAME = os.environ.get("MOK_ADMIN_NAME")# 用戶名稱
    MOK_AGENT_SPEAKING_STYLE = os.environ.get("MOK_AGENT_SPEAKING_STYLE")# 語氣風格
    MOK_AGENT_COMMON_LANGUAGE = os.environ.get("MOK_AGENT_COMMON_LANGUAGE")# 慣用語言


    # 建立指令描述列表
    cmd_desc = []
    for cmd, handler in cmd_map.items():
        if cmd in ["/start", "/clear", "/tools", "/reload"]:
            continue
        mod = None
        for name, m in tools.items():
            if hasattr(m, "PLUGIN_INFO") and m.PLUGIN_INFO.get("command") == cmd:
                mod = m
                break
        desc = cmd
        if mod and hasattr(mod, "PLUGIN_INFO"):
            desc = mod.PLUGIN_INFO.get("description", cmd)
        cmd_desc.append(f"- {cmd}: {desc}")

    prompt = f"""妳是{agent_name}，是一個意圖分類助手。根據{MOK_ADMIN_NAME}輸入，輸出 JSON：
- 無需工具且純聊天：{{"command": "chat"}}
- 需要單一工具：{{"command": "/命令", "args": "參數"}}
- 需要多步驟任務（如搜索後整理）：{{"command": "/workflow create", "args": "目標"}}
- 無法匹配：{{"command": "none"}}

可用命令說明：
{chr(10).join(cmd_desc)}

規則：
- 子命令放在 args 開頭，如 "remember 內容"
- 無參數時 args 為空字符串

用戶輸入：{user_text}
只輸出 JSON。"""

    try:
        # 使用統一 call_llm 接口
        response_text = await mokagi.call_llm(
            prompt=prompt,
            stream=False,
            temperature=0.1,
            num_predict=2000
        )
        output = response_text.strip()
        # 嘗試提取 JSON 對象
        start = output.find('{')
        end = output.rfind('}') + 1
        if start != -1 and end > start:
            json_str = output[start:end]
        else:
            if "chat" in output.lower():
                return "chat", ""
            json_str = ""

        if json_str:
            try:
                result = json.loads(json_str)
            except json.JSONDecodeError:
                logging.warning(f"JSON 解析失敗，原始響應: {output[:200]}")
                return None, None
            cmd = result.get("command", "none")
            args = result.get("args", "")
            if cmd == "chat":
                return "chat", ""
            if cmd != "none" and cmd in cmd_map:
                return cmd, args
        else:
            return "chat", ""
    except Exception as e:
        logging.warning(f"LLM意圖辨識失敗: {e}", exc_info=True)
    return None, None















































# ------------------------------------------------------------------------------------ #
# 函數: handle_intent
# 用途: 主意圖處理入口，由主程式 (MokAgi.py) 在收到使用者訊息且非直接命令時調用。
# 設計:
#   1. 先用 build_keyword_map 建立關鍵詞映射，再用 rule_based_intent 快速匹配。
#   2. 若規則未命中，則調用 llm_intent 讓 LLM 判斷意圖。
#   3. 得到命令後，通過 cmd_map 找到對應的 handler 函數，並解析參數（支援子命令如 /memory remember）。
#   4. 執行 handler，並對返回的 JSON 結果進行「自然化」處理：
#      - 若該工具定義了 naturalize_func，則調用它來產生自然語言回覆（支援流式顯示思考過程）。
#      - 若沒有，則直接顯示原始結果或給出失敗提示。
#   5. 所有過程通過 Telegram 的臨時消息 (temp_msg) 動態更新，讓用戶看到進度。
#   這樣的設計將意圖辨識、工具調用、結果呈現完全解耦，每個工具只需專注於自己的業務邏輯和呈現方式。
# 參數:
#   update, context: Telegram 訊息上下文。
#   user_text: 使用者輸入。
#   chat_id: 對話 ID。
#   cmd_map, tools, ollama_api, model_name: 主程式傳入的環境。
# 返回:
#   bool: True 表示已處理該消息，False 表示未命中任何意圖（應交由默認的對話處理）
# ------------------------------------------------------------------------------------ #
async def handle_intent(update, context, user_text: str, chat_id: int,cmd_map: dict, tools: dict,ollama_api: str, model_name: str) -> bool:
    """主鉤子函數，需要主程式傳入 tools 物件（需修改主程式呼叫）"""
    global _cmd_map, _tools, _ollama_api, _model_name
    _cmd_map = cmd_map
    _tools = tools

    # 將接收到的模型設定儲存或直接傳遞給內部函式
    # 可以設為模組變數供其他函式使用
    _ollama_api = ollama_api
    _model_name = model_name

    # 1. 規則比對
    kw_map = build_keyword_map(cmd_map, tools)
    cmd, args = await rule_based_intent(user_text, kw_map)
    if not cmd:
        # 2. LLM 後備
        cmd, args = await llm_intent(user_text, cmd_map, tools, ollama_api, model_name)

    if not cmd:
        return False

    # ========== 修改開始：處理完整命令（如 "/memory remember"）==========
    handler = None
    final_args = args

    # 如果 cmd 包含空格，表示是完整命令（例如 "/memory remember"）
    if " " in cmd.lstrip("/"):   # 避免根命令本身有空格
        parts = cmd.split(maxsplit=1)
        root_cmd = parts[0]      # 例如 "/memory"
        sub_cmd_part = parts[1]  # 例如 "remember"
        # 將子命令與原本的 args 合併（注意：rule_based 的 args 已經是去掉關鍵詞後的剩餘部分）
        if args:
            final_args = f"{sub_cmd_part} {args}"
        else:
            final_args = sub_cmd_part
        handler = cmd_map.get(root_cmd)
    else:
        # 一般情況，cmd 本身就是根命令（如 "/memory"），args 可能已含子命令（如 "remember 咖啡"）

        # 執行對應指令
        handler = cmd_map.get(cmd)
        final_args = args   # 直接使用原始參數
    if handler:
        # 顯示臨時狀態（使用 root_cmd 或 cmd 作為顯示名稱）
        display_cmd = root_cmd if 'root_cmd' in locals() else cmd
        temp_msg = await update.message.reply_text(f"⏳ 正在執行 {display_cmd} ...")
        try:
            result = await handler(final_args, str(chat_id))

            # ---------- 流式自然化：實時顯示思考過程 ----------
            if isinstance(result, str) and result.strip().startswith('{'):
                mod = None
                for name, m in tools.items():
                    if hasattr(m, "PLUGIN_INFO") and m.PLUGIN_INFO.get("command") == display_cmd:
                        mod = m
                        break
                naturalize_func = None
                if mod and hasattr(mod, "PLUGIN_INFO"):
                    func_name = mod.PLUGIN_INFO.get("naturalize_func")
                    if func_name:
                        naturalize_func = getattr(mod, func_name, None)

                # 發送原始 JSON 預覽
                raw_preview = result[:3500]
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"📄 原始結果：\n<pre>{html.escape(raw_preview)}</pre>",
                    parse_mode='HTML'
                )

                if naturalize_func:
                    # 調用工具自己的自然化函數，傳遞 temp_msg 支持流式
                    naturalized = await naturalize_func(
                        user_text=user_text,
                        raw_result=result,
                        ollama_api=ollama_api,
                        model_name=model_name,
                        temp_msg=temp_msg,
                        context=context
                    )
                else:
                    # 無專用函數時，使用通用自然化（可保留簡單的備選邏輯或直接返回失敗）
                    naturalized = None

                if naturalized:
                    # 安全處理：先轉義全部 HTML 字符，再還原我們需要的 <pre> 標籤
                    safe_text = html.escape(naturalized)
                    safe_text = safe_text.replace("&lt;pre&gt;", "<pre>").replace("&lt;/pre&gt;", "</pre>")
                    await context.bot.edit_message_text(
                        chat_id=temp_msg.chat_id,
                        message_id=temp_msg.message_id,
                        text=safe_text,
                        parse_mode='HTML'   # 確保傳入 parse_mode
                    )
                else:
                    await context.bot.edit_message_text(
                        chat_id=temp_msg.chat_id,
                        message_id=temp_msg.message_id,
                        text="⚠️ 自然化失敗，請查看上方原始數據。"
                    )
                return True
            # -----------------------------------------------------------

            if isinstance(result, tuple):
                text, markup = result
                await context.bot.edit_message_text(
                    chat_id=temp_msg.chat_id,
                    message_id=temp_msg.message_id,
                    text=text,
                    reply_markup=markup
                )
            elif result is not None:
                await context.bot.edit_message_text(
                    chat_id=temp_msg.chat_id,
                    message_id=temp_msg.message_id,
                    text=result,
                    parse_mode='HTML'
                )
            else:
                await context.bot.edit_message_text(
                    "✅ 完成",
                    chat_id=temp_msg.chat_id,
                    message_id=temp_msg.message_id
                )
                
        except Exception as e:
            await context.bot.edit_message_text(
                f"❌ 執行意圖命令失敗: {e}",
                chat_id=temp_msg.chat_id,
                message_id=temp_msg.message_id
            )
        return True
    return False

































# ------------------------------------------------------------------------------------ #
# 函數: dummy_handler
# 用途: 佔位用的工具處理函數，當使用者直接發送 /intent 命令時，給予提示訊息。
#       實際上意圖辨識功能是透過 handle_intent 被主程式調用，而不是由使用者直接發送 /intent。
# 設計: 簡單回覆一句說明，引導使用者用自然語言互動而非直接操作此命令。
# 參數:
#   args: 命令參數（未使用）。
#   chat_id: 對話 ID（未使用）。
# 返回:
#   str: 提示訊息。
# ------------------------------------------------------------------------------------ #
async def dummy_handler(args: str, chat_id: str = None):
    return "請使用自然語言觸發意圖辨識，例如「記得我喜歡喝咖啡」"


