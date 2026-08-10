# "update":"202608110022_出街版"


# 工具

---

## 下載工具 (cmd)

    curl -sL https://raw.githubusercontent.com/MOK2026/MOKAGI/refs/heads/main/tools/admin.py -o "~/.mok/tools/admin.py"

---

## 自製工具.py

    PLUGIN_INFO = {
        "command": "/mycommand",            # Telegram 命令
        "icon": "🔧",                       # 圖示
        "handler": "handle_mycommand",      # 異步處理函數名
        "description": "工具描述",
        "intent_keywords": [                # 自然語言觸發詞
            ("關鍵詞", "/mycommand subcmd")
        ],
        "tool_schema": {                    # 供 LLM 自動調用的 JSON Schema
            "name": "my_tool",
            "description": "...",
            "parameters": {...}
        },
        "update":"202604231241"
        "naturalize_func": "naturalize_result"  # 可選：結果自然化函數
    }

    # ================== python code ===================

    # if need pip install
    try:
        import chromadb
        print("chromadb 已安裝，版本：", chromadb.__version__)
    except ImportError:
        msg = (
                    "❌ need pip install：`chromadb`、`sentence-transformers`\n\n"
                    "請使用以下命令安裝（需要管理員權限）：\n"
                    "<pre>/admin pip install chromadb sentence-transformers</pre>\n\n"
                    "發送後會要求二次確認，輸入確認碼即可自動安裝。"
                )
        return msg

    # ================== python code ===================


工具實現後會自動被 tool_handler.load_tools() 加載，並注入到命令映射和 LLM 工具調用中。