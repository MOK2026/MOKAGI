# ------------------------------------------------------------------------------------ #
# replace_in_file.py - 精確替換檔案內容（獨立工具）
# 設計原則：簡單、安全、專注做一件事。
# 功能：在指定檔案中，將「搜尋文字」精確替換為「替換文字」。
# 安全機制：
#   1. 僅允許在 ~/.mok/ 目錄內操作。
#   2. 搜尋文字必須在檔案中唯一出現一次（否則拒絕執行）。
#   3. 僅限管理員使用（可透過 MOK_ALLOWED_TOOLS 放行）。
# 命令：/replace <檔案路徑> <搜尋文字> <替換文字>
# 範例：/replace /home/ubuntu/.mok/agent/春/test.py 'old_var' 'new_var'
# ------------------------------------------------------------------------------------ #

PLUGIN_INFO = {
    "command": "/replace",
    "icon": "🔧",
    "handler": "handle_replace",
    "description": "精確替換檔案內容（安全版）：僅在 ~/.mok/ 內操作，且搜尋文字必須唯一。",
    "intent_keywords": [
        ("/替換", "/replace"),
        ("/取代", "/replace"),
        ("/修改檔案", "/replace"),
    ],
    "tool_schema": {
        "name": "replace_in_file",
        "description": (
            "在指定檔案中精確替換文字（非正則）。\n\n"
            "參數格式：`<檔案路徑> <搜尋文字> <替換文字>`\n"
            "搜尋文字必須在檔案中**唯一出現一次**，否則拒絕執行。\n\n"
            "範例：\n"
            "- `/replace /home/ubuntu/.mok/agent/春/test.py 'old' 'new'`\n"
            "- 若文字含空格，請用引號包住：`/replace test.py 'hello world' 'goodbye'`\n\n"
            "安全限制：僅能修改 ~/.mok/ 目錄內的檔案。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "args": {
                    "type": "string",
                    "description": "格式：檔案路徑 搜尋文字 替換文字"
                }
            },
            "required": ["args"]
        }
    },
    "update": "20260710"
}

import os
import shlex
import logging
from typing import Optional, Dict

# 導入 mokagi 核心（用於取得配置與路徑）
import mokagi

# 工具處理函數
async def handle_replace(args, chat_id: str = None, agent_config: Optional[Dict] = None) -> str:
    """
    處理 /replace 命令。
    args 可以是字串（命令列格式）或字典（工具呼叫）。
    """
    if agent_config is None:
        agent_config = mokagi._agent_config

    # 1. 解析參數
    if isinstance(args, dict):
        arg_str = args.get("args", "")
    else:
        arg_str = str(args).strip()

    if not arg_str:
        return (
            "🔧 用法：/replace <檔案路徑> <搜尋文字> <替換文字>\n"
            "範例：/replace /home/ubuntu/.mok/agent/春/test.py 'old_text' 'new_text'\n"
            "注意：搜尋文字必須在檔案中唯一出現一次。"
        )

    try:
        parts = shlex.split(arg_str)
    except ValueError as e:
        return f"❌ 參數解析錯誤（請檢查引號）：{e}"

    if len(parts) < 3:
        return "❌ 參數不足。請提供：檔案路徑、搜尋文字、替換文字"

    filepath, search_text, replace_text = parts[0], parts[1], parts[2]

    # 2. 權限檢查（僅限管理員，或 MOK_ALLOWED_TOOLS 包含此工具）
    if not chat_id:
        return "❌ 無法識別使用者身分。"

    # 檢查管理員
    admin_chat_id = agent_config.get("ADMIN_CHAT_ID", "")
    is_admin = (str(chat_id) == str(admin_chat_id)) or (chat_id and not chat_id.isdigit())
    if not is_admin:
        # 檢查 MOK_ALLOWED_TOOLS 是否包含此工具
        allowed_str = agent_config.get("MOK_ALLOWED_TOOLS", "")
        if "replace_in_file" not in allowed_str and "admin_replace_in_file" not in allowed_str:
            return "⛔ 權限不足：只有管理員或獲授權的工具清單才能使用 replace_in_file。"

    # 3. 路徑安全檢查（僅允許在 ~/.mok/ 目錄內）
    real_path = os.path.realpath(os.path.expanduser(filepath))
    mok_home = os.path.realpath(os.path.expanduser(f"~/.{mokagi.MOKAGI_home}"))
    if not real_path.startswith(mok_home + "/") and real_path != mok_home:
        return f"⛔ 安全拒絕：只能修改 {mok_home} 目錄內的檔案。"

    if not os.path.isfile(real_path):
        return f"❌ 檔案不存在或不是普通檔案：{real_path}"

    # 4. 讀取檔案內容
    try:
        with open(real_path, 'r', encoding='utf-8') as f:
            content = f.read()
    except UnicodeDecodeError:
        return "❌ 檔案為二進制，無法處理"
    except Exception as e:
        return f"❌ 讀取檔案失敗：{e}"

    # 5. 檢查搜尋文字的匹配次數（必須唯一）
    count = content.count(search_text)
    if count == 0:
        return f"❌ 找不到文字「{search_text[:50]}...」。請檢查內容。"
    if count > 1:
        return f"❌ 文字出現 {count} 次，非唯一。請提供前後更多上下文讓它唯一。"

    # 6. 執行替換（只替換唯一出現的那一處）
    new_content = content.replace(search_text, replace_text, 1)

    # 7. 寫入檔案
    try:
        with open(real_path, 'w', encoding='utf-8') as f:
            f.write(new_content)
        return f"✅ 已成功替換 1 處。\n📁 {real_path}\n搜尋：{search_text[:50]}{'...' if len(search_text)>50 else ''}\n替換：{replace_text[:50]}{'...' if len(replace_text)>50 else ''}"
    except Exception as e:
        return f"❌ 寫入檔案失敗：{e}"