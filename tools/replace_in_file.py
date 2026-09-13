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
            "【建議】使用具名參數：path（檔案路徑）、search（要替換的原文）、replace（新文字）。\n"
            "search / replace 可含空白、引號、<option value=\"x\"> 等任何符號，原樣傳入即可，無需轉義。\n"
            "搜尋文字必須在檔案中**唯一出現一次**，否則拒絕執行。\n\n"
            "範例：\n"
            "- `/replace /home/ubuntu/.mok/agent/春/test.py 'old' 'new'`\n"
            "- 具名參數範例：{\"path\":\"/home/ubuntu/.mok/agent/春/test.py\",\"search\":\"old\",\"replace\":\"new\"}\n"
            "- 舊式 args 單字串：`/replace test.py 'old' 'new'`（含空白時須加引號）\n\n"
            "安全限制：僅能修改 ~/.mok/ 目錄內的檔案。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "要修改的檔案路徑（建議使用此具名參數；可避免含空白字串被切詞）"
                },
                "search": {
                    "type": "string",
                    "description": "要被替換的原始文字。可含空白、引號、<> 等任何符號，原樣傳入即可，無需轉義或加引號。"
                },
                "replace": {
                    "type": "string",
                    "description": "替換後的新文字。可含空白、換行等，原樣傳入。"
                },
                "args": {
                    "type": "string",
                    "description": "（舊式相容）格式：檔案路徑 搜尋文字 替換文字。若搜尋/替換文字含空白，請務必改用 path/search/replace 具名參數。"
                }
            },
            "required": ["path", "search", "replace"]
        }
    },
    "update": "20260910"
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
    #    優先支援具名參數（path / search / replace），可完全避免「含空白字串被切詞」的問題。
    filepath = search_text = replace_text = None
    arg_str = ""

    if isinstance(args, dict):
        filepath = args.get("path") or args.get("file") or args.get("filepath")
        search_text = args.get("search")
        if search_text is None:
            search_text = args.get("search_text") or args.get("old")
        replace_text = args.get("replace")
        if replace_text is None:
            replace_text = args.get("replace_text") or args.get("new")
        arg_str = args.get("args", "") or ""
    else:
        arg_str = str(args).strip()

    # 若未提供完整具名參數，回退到單一字串形式（以 shlex 解析，支援引號）
    if filepath is None or search_text is None or replace_text is None:
        if not arg_str:
            return (
                "🔧 用法：/replace <檔案路徑> <搜尋文字> <替換文字>\n"
                "建議改用具名參數：{path, search, replace}（含空白/標籤/引號時務必使用）\n"
                "範例：{\"path\":\"/home/ubuntu/.mok/agent/春/test.py\",\"search\":\"old\",\"replace\":\"new\"}\n"
                "注意：搜尋文字必須在檔案中唯一出現一次。"
            )

        try:
            parts = shlex.split(arg_str)
        except ValueError as e:
            return f"❌ 參數解析錯誤（請檢查引號）：{e}"

        if len(parts) == 3:
            filepath, search_text, replace_text = parts[0], parts[1], parts[2]
        elif len(parts) > 3:
            # 最常見的失敗：搜尋字串含空白卻未加引號。
            # 不再硬切猜測，明確要求改用具名參數，避免 LLM 反覆重試（鬼打牆）。
            return (
                "❌ 參數解析失敗：偵測到空白切分出多於 3 個片段，"
                "通常是『搜尋或替換文字含空白』卻未加引號所致。\n"
                "👉 請改用具名參數：{\"path\": ..., \"search\": ..., \"replace\": ...}\n"
                "   如此可原樣傳入含空白、標籤（如 <option value=\"x\">）、引號的任何文字。"
            )
        else:
            return (
                "❌ 參數不足。請提供：檔案路徑、搜尋文字、替換文字"
                "（或改用 path/search/replace 具名參數）。"
            )

    if not filepath or search_text is None or replace_text is None:
        return "❌ 參數不完整。請提供檔案路徑、搜尋文字、替換文字。"

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

    # 3.5 編輯登記鎖鉤子：對 ~/.mok 內檔案的寫入，必須先登記；沒登記就擋下
    try:
        import editlock_hook
        _agent = (agent_config or {}).get("MOK_AGENT_NAME") or (agent_config or {}).get("AGENT_NAME") or "unknown"
        _ok, _hook_msg = editlock_hook.guard(real_path, _agent, agent_config, purpose="replace_in_file")
        if not _ok:
            return _hook_msg
    except ImportError:
        pass
    except Exception as _e:
        logging.getLogger(__name__).warning("editlock_hook 檢查異常，放行：%s", _e)

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