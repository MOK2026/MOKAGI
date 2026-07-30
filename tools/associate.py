# ------------------------------------------------------------------------------------ #
# 工具名稱: associate (聯想詞生成器)
# 用途: 給定一個詞或短語，使用 LLM 生成語義相關的聯想詞列表。
#       可獨立於記憶搜索使用，適用於關鍵詞擴充、查詢重寫、意圖理解等場景。
#
# 主要函數:
#   handle_associate(args, user_id, agent_config)
#       - 入口函數，處理 /associate 命令或 LLM 工具調用。
#       - 解析參數（支持字典或字符串），調用 _generate_associations 生成聯想詞。
#       - 返回 JSON 格式: {"original": "xxx", "keywords": ["a","b","c"]}
#
#   _generate_associations(query, count, context, agent_config)
#       - 核心邏輯：調用 LLM 生成聯想詞列表。
#       - 如果 agent_config 為 None，會嘗試從環境變量 MOK_AGENT_NAME 獲取當前 Agent 配置。
#       - 會檢查模型配置是否有效（防止 404 錯誤）。
#       - 返回字符串列表（聯想詞）。
#
#   extract_keywords_from_sentence(sentence, agent_config)
#       - 從句子中提取核心關鍵詞（最多5個），用於後續聯想和語義搜索。
#       - 調用 LLM 分析句子，返回關鍵詞列表。
#
# 依賴:
#   mokagi.call_llm 用於調用 LLM，需要傳遞正確的 agent_config 以避免配置混亂。
#
# 更新記錄:
#   202606121500 - 增加配置診斷，防止無效的 Ollama 地址導致 404。
#   20260614     - 添加詳細文件頭說明。
# ------------------------------------------------------------------------------------ #

PLUGIN_INFO = {
    "command": "/associate",
    "icon": "🔗",
    "handler": "handle_associate",
    "description": "生成聯想詞：給定一個詞語，返回 3-7 個語義相關的詞語（同義、近義、相關概念），用於搜索擴充、創意發想等。",
    
    "intent_keywords": [
        ("/聯想", "/associate")
    ],
    "naturalize_func": "naturalize_admin_result",



    "tool_schema": {
        "name": "associate",
        "description": (
            "生成指定詞語或短語的語義相關聯想詞列表。適用於關鍵詞擴充、查詢重寫、意圖理解、搜索擴充等場景。\n\n"
            "【功能】給定一個詞語（例如「車」），返回一批語義相關的詞語（例如「飛機、輪胎、引擎、駕駛、交通」）。\n"
            "這些聯想詞會與原詞不同，但存在合理的語義關聯（功能、部件、場景、動作等）。\n\n"
            "【返回格式】\n"
            "- 成功時返回 JSON：{\"original\": \"原詞\", \"keywords\": [\"聯想詞1\", \"聯想詞2\", ...]}\n"
            "- 錯誤時返回 JSON：{\"error\": \"錯誤訊息\"}\n\n"
            "【注意】\n"
            "- 該工具是純函數式，不修改任何狀態，每次調用獨立。\n"
            "- 聯想詞數量由 `count` 參數控制，但不保證一定返回那麼多，若原詞概念極窄可能返回較少。\n"
            "- 生成的聯想詞不包含原詞本身。\n"
            "- 若需要按場景限制聯想（例如「寫程式」場景），可使用 `context` 參數。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {
                    "type": "string",
                    "description": (
                        "需要生成聯想詞的原始詞語或短語。\n"
                        "範例：\n"
                        "- 單詞：`車`\n"
                        "- 短語：`深度學習`\n"
                        "- 複合概念：`垃圾分類`\n\n"
                        "必須提供非空字符串。"
                    )
                },
                "count": {
                    "type": "integer",
                    "description": (
                        "期望生成的聯想詞數量（不含原詞）。\n"
                        "默認 5，最小 1，無上限（但 LLM 通常只能生成 3-15 個合理詞語）。\n"
                        "數值越大，返回的聯想詞可能越多，但耗費更多 token。\n"
                        "若原詞概念狹窄，實際返回數量可能少於此值。\n"
                        "範例：用戶要求「多聯想幾個詞」 → count=10"
                    ),
                    "default": 5,
                    "minimum": 1
                },
                "context": {
                    "type": "string",
                    "description": (
                        "可選，聯想的場景限制。用於讓生成的聯想詞更符合特定領域。\n"
                        "範例：\n"
                        "- `寫程式` → 對於「蘋果」，可能聯想「Swift、Xcode、iOS」而非水果。\n"
                        "- `醫療` → 對於「心臟」，可能聯想「支架、瓣膜、心律」等。\n"
                        "- `法律` → 對於「合同」，可能聯想「條款、違約、仲裁」。\n\n"
                        "若不提供，則不限場景，進行通用聯想。"
                    )
                }
            },
            "required": ["query"]
        }
    },




    "naturalize_func": "naturalize_associate_result",
    "update": "202606121500"
}

import logging, re, json, os
from typing import List
from typing import Optional, Dict








































async def handle_associate(args, user_id: str = None, agent_config: Optional[Dict] = None) -> str:
    """
    處理 /associate 命令或 LLM 工具調用。
    args 可以是 dict (tool call) 或 string (直接命令)。
    返回 JSON 字符串：{"original": "xxx", "keywords": ["a","b","c"]}
    """


    # 統一解析參數
    if isinstance(args, dict):
        query = args.get("query", "")
        count = args.get("count", 5)
        context = args.get("context", "")
    elif isinstance(args, str):
        # 直接命令格式: /associate 詞語 [數量]
        parts = args.strip().split()
        query = parts[0] if parts else ""
        count = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 5
        # 如果提供了第三個參數，視為場景（可能包含空格，但簡單命令暫不支持帶空格的場景）
        context = " ".join(parts[2:]) if len(parts) > 2 else ""
    else:
        return json.dumps({"error": "參數格式錯誤"}, ensure_ascii=False)

    if not query:
        return json.dumps({"error": "請提供 query 參數"}, ensure_ascii=False)

    # 限制數量
    count = max(1, count)   # 只保底下限，去掉 min(10, count)

    try:
        keywords = await _generate_associations(query, count, context, agent_config)
        result = {
            "original": query,
            "keywords": keywords
        }
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        logging.error(f"聯想詞生成失敗: {e}")
        return json.dumps({"error": str(e)}, ensure_ascii=False)


































async def _generate_associations(query: str, count: int = 5, context: str = "", agent_config: Optional[Dict] = None) -> List[str]:
    import mokagi
    import os
    if agent_config is None:
        agent_name = os.environ.get("MOK_AGENT_NAME")
        if agent_name:
            agent_config = await mokagi.get_agent_config(agent_name)
        else:
            agent_config = mokagi._agent_config
    agent_name = agent_config.get("MOK_AGENT_NAME", "助手")
    MOK_ADMIN_NAME = agent_config.get("MOK_ADMIN_NAME", "用戶")

    # ========== 新增：配置診斷 ==========
    model_url = agent_config.get("MOK_MODEL_url", "")
    model_token = agent_config.get("MOK_MODEL_token", "")
    if (not model_token and model_url == "http://localhost:11434/v1") or (model_url == "http://localhost:11434/v1" and not model_token):
        logging.error(f"聯想詞生成失敗：Agent「{agent_name}」配置錯誤，url={model_url}, token={'已設置' if model_token else '未設置'}")
        return []  # 返回空列表，避免調用 LLM 產生 404
    # =================================

    num_Max = 500

    if not query.strip():
        return [query]

    scene_hint = f"（場景限制：{context}）" if context else "" 
    # 構建 prompt
    prompt = f"""你是一位擅長發散聯想的關鍵詞擴充助手。
{MOK_ADMIN_NAME}說:{query}

{scene_hint}

請按語意分析句內有甚麼"關鍵詞"(可以有多個),為{agent_name}的思考聯想 ** {count} 個** 用於回答或全文搜索的關聯詞語。

核心要求：
- 必須避開原詞本身的字面，不重複原詞中的任何詞語。
- 從不同維度進行聯想：功能、部件、場景、相關動作、上古/未來替代品、使用人群、相關文化符號等。
- 所有詞語必須與原詞有清晰的語義關聯，但不要近義詞列表，而要跳躍又合理的聯想。
- 輸出純文本，一行一詞，無編號、無解釋、無標點。
- 如果原詞概念極度狹窄無法展開，只輸出原詞。
- 妳的 num_predict 只有 {num_Max}，必須盡快精簡回答，直接輸出結果，不要任何解釋或多餘內容。


輸出範例（當關鍵詞為「車」）：
船
飛機
坐駕
馬
交通
出行
駕駛
代步
輪胎
引擎
公路


現在請輸出："""

    try:
        response = await mokagi.call_llm(
            prompt=prompt,
            user_id="associate_tool",
            stream=False,
            temperature=0.4,
            num_predict=max(count * 15, 300),
            tools_def=[],
            agent_config=agent_config
        )
        # 調試：打印完整響應
        logging.info(f"associate LLM 響應: {response}")

        # 提取文本（優先 content，若為空則使用 reasoning）
        # 提取文本
        raw_text = ""
        if isinstance(response, dict):
            content = response.get("content", "")
            reasoning = response.get("reasoning", "")
            content = content or ""
            reasoning = reasoning or ""
            if content.strip():
                raw_text = content
            elif reasoning.strip():
                raw_text = reasoning
            else:
                # 嘗試其他可能字段
                raw_text = response.get("text", "") or response.get("output", "") or ""
        elif isinstance(response, str):
            raw_text = response
        else:
            raw_text = str(response) if response else ""

        if not raw_text.strip():
            logging.warning(f"associate: 無法提取有效文本，原始響應: {response}")
            return []

        words = []
        # 先按行拆分，過濾推理句
        for line in raw_text.strip().split('\n'):
            line = line.strip()
            line = re.sub(r'^[-*•]\s*', '', line)
            line = re.sub(r'^\d+\.\s*', '', line)
            if not line or line == "無":
                continue
            # 過濾推理句：太長或含標點的都拆開處理
            if len(line) > 8 or any(c in line for c in '。：；（）()【】『』「」，.'):
                parts = re.split(r'[,、，：。;：;\\\)\]）】」\'\"」』\s]+', line)
                for part in parts:
                    part = part.strip().strip('"').strip("'").strip('「').strip('」')
                    part = re.sub(r'^[-*•]\s*', '', part)
                    part = re.sub(r'^\d+\.\s*', '', part)
                    if part and 2 <= len(part) <= 8 and part not in words:
                        words.append(part)
            else:
                if line not in words:
                    words.append(line)
        if not words:
            return []
        return words[:count]
    except Exception as e:
        logging.warning(f"生成聯想詞失敗: {e}")
        return [query]


























async def extract_keywords_from_sentence(sentence: str, agent_config: Optional[Dict] = None) -> List[str]:
    """從句子中提取核心關鍵詞（最多5個），用於後續聯想和搜索。"""
    import mokagi
    if agent_config is None:
        agent_config = mokagi._agent_config
    if not sentence.strip():
        return []

    keywords_Max = 10
    num_Max = 500

    prompt = f"""你是一位文本分析專家。請從以下句子中提取核心關鍵詞（完整概念名詞或動詞短語），最多{keywords_Max}個。
輸出格式：每行一個關鍵詞，不要序號、不要解釋、不要標點。
- 妳的 num_predict 只有 {num_Max}，必須盡快精簡回答，直接輸出結果，不要任何解釋或多餘內容。
句子：{sentence}
輸出："""

    try:
        response = await mokagi.call_llm(
            prompt=prompt,
            user_id="keyword_extractor",
            stream=False,
            temperature=0.3,
            num_predict=num_Max,
            tools_def=[],
            agent_config=agent_config
        )
        raw_text = ""
        if isinstance(response, dict):
            raw_text = response.get("content", "") or response.get("reasoning", "")
        elif isinstance(response, str):
            raw_text = response
        else:
            raw_text = str(response) if response else ""

        keywords = []
        for line in raw_text.strip().split('\n'):
            line = line.strip()
            if line:
                line = re.sub(r'^[-*•]\s*', '', line)
                line = re.sub(r'^\d+\.\s*', '', line)
                if line:
                    keywords.append(line)
        return keywords[:keywords_Max] if keywords else [sentence]
    except Exception as e:
        logging.warning(f"提取關鍵詞失敗: {e}")
        return [sentence]












































