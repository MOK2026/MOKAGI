# ------------------------------------------------------------------------------------ #
# 工具名稱: experience (經驗學習)
# 用途: 讓 Agent 回憶過去執行類似任務的成功/失敗經驗，提高任務成功率。
# ------------------------------------------------------------------------------------ #

PLUGIN_INFO = {
    "command": "/experience",
    "icon": "📚",
    "handler": "handle_experience",
    "description": "查閱過往任務經驗（成功/失敗），用於改善當前任務執行。",
    "intent_keywords": [
        ("/經驗", "/experience recall"),
        ("/之前怎麼做", "/experience recall"),
        ("/有類似經驗嗎", "/experience recall"),
    ],
    "tool_schema": {
        "name": "experience",
        "description": (
            "查詢過往執行類似任務的經驗記錄，幫助你決定當前任務的最佳執行方式。\n"
            "當你面對一個複雜任務，不確定如何下手時，可以先用此工具查閱經驗。\n"
            "返回結果會包含成功與失敗的案例摘要，以及使用過的工具序列。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["recall"],
                    "description": "目前僅支援 'recall'（回憶經驗）。"
                },
                "query": {
                    "type": "string",
                    "description": "查詢關鍵詞，描述當前任務的目標或相關內容。"
                },
                "n_results": {
                    "type": "integer",
                    "description": "最多回傳幾條經驗（預設 3）。",
                    "default": 3,
                    "minimum": 1
                }
            },
            "required": ["action", "query"]
        }
    },
    "update": "202607271012_暫時可用版"
}

import logging, json
from typing import Optional, Dict
import mokagi

async def handle_experience(args, chat_id: str = None, agent_config: Optional[Dict] = None):
    """處理 /experience 命令，僅支援 recall 子命令"""
    if agent_config is None:
        agent_config = mokagi._agent_config
    agent_name = agent_config.get("MOK_AGENT_NAME", "助手")

    # 若 args 為 dict（來自 LLM 工具呼叫）
    if isinstance(args, dict):
        action = args.get("action", "recall")
        query = args.get("query", "")
        n_results = args.get("n_results", 3)
        if not query:
            return json.dumps({
                "success": False,
                "error_type": "missing_parameter",
                "error_message": "請提供 'query' 參數。",
                "tool": "experience"
            }, ensure_ascii=False)
    else:
        # 命令模式：/experience recall 關鍵詞 [數量]
        parts = args.split(maxsplit=1) if args else []
        if len(parts) < 2:
            return "用法: /experience recall 關鍵詞 [數量]"
        subcmd = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        if subcmd != "recall":
            return f"未知子命令: {subcmd}，目前僅支援 recall。"
        # 解析參數
        tokens = rest.split()
        if tokens[-1].isdigit():
            n_results = int(tokens[-1])
            query = " ".join(tokens[:-1])
        else:
            n_results = 3
            query = rest

    # 呼叫 mokagi 的 recall_experience 函數
    result = mokagi.recall_experience(chat_id, query, agent_name, n_results=n_results)
    if not result:
        return "沒有找到相關的經驗記錄。"
    return result