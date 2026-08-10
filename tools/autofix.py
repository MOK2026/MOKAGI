'''

autofix.py 的調用時機與流程
1. 工作流中步驟失敗後自動修復參數
文件：workflow.py

函數：execute_agent_loop 內部的 run_step

觸發條件：某一步驟執行失敗（工具返回錯誤或拋異常），且第一次失敗後。

調用代碼（位於 run_step 中）：

python
try:
    autofix_handler = find_tool_handler("autofix")
    if autofix_handler:
        autofix_args = {
            "tool_name": tool_name,
            "original_args": json.dumps(params, ensure_ascii=False),
            "error": last_error,
            "context": f"目標: {goal}\n步驟描述: {description}\n成功標準: {success_criteria}"
        }
        autofix_result = await autofix_handler(autofix_args, user_id)
        # 解析返回的 JSON，若 fixed=true 則使用修正後的參數重試
作用：autofix 工具內部調用 LLM 分析錯誤，返回修正後的參數（如將 {"command":"ls"} 修正為 {"args":"ls"}），工作流用新參數重試原步驟。

2. 用戶主動使用 /autofix 命令
文件：autofix.py

觸發條件：用戶在聊天中輸入 /autofix 命令（例如 /autofix code=... error=...）。

處理流程：tool_handler.process_message 通過命令映射調用 autofix.handle_autofix，執行代碼或工具參數的自動修正。

作用：用戶可以手動請求修復某段 Python 代碼或上次失敗的工具調用。

'''

# ------------------------------------------------------------------------------------ #
# 字典: PLUGIN_INFO
# 用途: 定義自動修正工具與主程式、意圖辨識系統之間的介面。
#       主程式透過它來註冊 /autofix 命令、建立自然語言關鍵詞映射、
#       提供給 LLM 的工具描述。
# 欄位說明:
#   command           : 直接命令 "/autofix"，顯示於菜單。
#   icon              : 工具圖示。
#   handler           : 處理函數名稱 "handle_autofix"。
#   description       : 簡短描述，用於命令選單。
#   tool_schema       : 提供給 LLM 的工具定義，描述參數與用途。
# ------------------------------------------------------------------------------------ #

PLUGIN_INFO = {
    "command": "/autofix",
    "icon": "🔧",
    "handler": "handle_autofix",
    "description": "自動修正程式碼錯誤（提供錯誤訊息和程式碼，AI 嘗試生成修正版本）",
    "intent_keywords": [
        ("修正", "/autofix"),
        ("修復", "/autofix"),
        ("除錯", "/autofix"),
        ("debug", "/autofix"),
    ],







    "tool_schema": {
        "name": "autofix",
        "description": (
            "自動修正程式碼錯誤或工具調用參數錯誤。根據提供的錯誤信息，AI 會分析問題並返回修正後的版本。\n\n"
            "【使用場景與參數組合（二選一，不可混用）】\n\n"
            "1. **修正 Python 程式碼錯誤**：\n"
            "   - 提供 `code`（原始程式碼）和 `error`（錯誤訊息，包含 traceback）\n"
            "   - 可選 `context` 提供額外說明\n"
            "   - 返回修正後的完整程式碼\n\n"
            "2. **修正工具調用參數**：\n"
            "   - 提供 `tool_name`（工具名稱，例如 'admin', 'memory', 'web_search'）\n"
            "   - 提供 `original_args`（原始參數，可以是 JSON 字符串或字典對象）\n"
            "   - 提供 `error`（錯誤訊息，例如 'missing required parameter'）\n"
            "   - 可選 `context` 提供額外說明\n"
            "   - 返回修正後的參數 JSON 對象\n\n"
            "【注意】\n"
            "- 不要同時提供 `code` 和 `tool_name`，請根據錯誤類型選擇其中一種場景。\n"
            "- 如果錯誤是 `ModuleNotFoundError`，autofix 會嘗試自動 pip install 缺失的包，無需 LLM 修正。\n"
            "- 如果錯誤是 `dict` 與 `strip` 相關的類型錯誤，autofix 會自動將字典轉為字符串，無需 LLM 修正。\n"
            "- 返回的修正結果為 JSON 格式，包含 `fixed` 布爾值、修正後的內容和說明。\n\n"
            "【返回格式】\n"
            "- 成功修正：{\"fixed\": true, \"args\": <修正後的參數>}（工具參數場景）或直接返回修正後的程式碼（代碼場景）\n"
            "- 無法修正：{\"fixed\": false, \"error\": \"原因說明\"}\n"
            "- 若自動修正時已安裝依賴或轉換格式，也會返回相應的 JSON。\n\n"
            "【何時使用】\n"
            "- 當工作流中的某個工具調用失敗時，應自動調用此工具嘗試修正參數。\n"
            "- 當用戶提供了一段出錯的 Python 程式碼並要求「修正」、「除錯」時，應調用此工具。\n"
            "- 當 LLM 看到工具返回的錯誤信息且不確定如何修正時，可主動調用此工具。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "code": {
                    "type": "string",
                    "description": "【僅代碼修正場景】原始的 Python 程式碼（出錯的代碼）。應包含完整的函數或腳本。範例：`print('hello'`（缺少右括號）"
                },
                "error": {
                    "type": "string",
                    "description": "【必需】錯誤訊息，應包含完整的 traceback 或 API 返回的錯誤詳情。例如：`SyntaxError: unexpected EOF while parsing` 或 `'dict' object has no attribute 'strip'`"
                },
                "context": {
                    "type": "string",
                    "description": "【可選】額外的上下文信息，例如用戶的意圖、預期行為、環境限制等。可幫助 LLM 更準確地修正。範例：`用戶想要列出當前目錄的文件`"
                },
                "tool_name": {
                    "type": "string",
                    "description": "【僅工具參數修正場景】出錯的工具名稱，例如 'admin', 'memory', 'web_search', 'workflow' 等。必須與實際工具名一致。"
                },
                "original_args": {
                    "type": "string",
                    "description": "【僅工具參數修正場景】調用工具時使用的原始參數。可以是 JSON 字符串（如 `'{\"action\":\"exec\",\"args\":\"ls\"}'`）或直接是字符串。若原始參數是字典，建議轉為 JSON 字符串傳入。"
                }
            },
            "required": ["error"]
        }
    },












    "update": "202608110022_出街版"
}

import logging
import subprocess
import tempfile
import os
import json
import asyncio
from typing import Optional, Tuple, Dict

import mokagi
from autofix2 import generate_fix

# ------------------------------------------------------------------------------------ #
# 輔助函數: execute_code_safely
# 用途: 在臨時檔案中執行程式碼，並捕獲輸出和錯誤，超時限制 30 秒。
# 參數:
#   code: 要執行的 Python 程式碼字串。
# 返回:
#   (success, output, error)
#     success: bool 是否執行成功（無未捕獲異常）
#     output: stdout 輸出
#     error: stderr 輸出（如果發生異常則包含 traceback）
# ------------------------------------------------------------------------------------ #
async def execute_code_safely(code: str) -> Tuple[bool, str, str]:
    """在隔離環境中執行 Python 程式碼，返回 (成功與否, stdout, stderr)"""
    with tempfile.NamedTemporaryFile(mode='w', suffix='.py', delete=False, encoding='utf-8') as f:
        f.write(code)
        tmp_path = f.name
    
    try:
        proc = await asyncio.create_subprocess_exec(
            'python3', tmp_path,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            limit=10*1024*1024  # 10MB 限制
        )
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
        success = (proc.returncode == 0)
        return success, stdout.decode('utf-8', errors='replace'), stderr.decode('utf-8', errors='replace')
    except asyncio.TimeoutError:
        return False, "", "執行超時（超過 30 秒）"
    except Exception as e:
        return False, "", str(e)
    finally:
        try:
            os.unlink(tmp_path)
        except:
            pass

# ------------------------------------------------------------------------------------ #
# ------------------------------------------------------------------------------------
# 函數: handle_autofix
# 用途: 工具的主要入口，處理 /autofix 命令或 LLM 工具調用。
# 參數:
# args: 可以是字串（命令行格式 "/autofix <code_and_error>"）或字典（tool call）。
# chat_id: 使用者 ID（用於權限檢查，可選）。
# 返回:
# str: 結果訊息，支援 HTML 格式。
# ------------------------------------------------------------------------------------
async def handle_autofix(args, chat_id: str = None, agent_config: Optional[Dict] = None) -> str:
    if agent_config is None:
        agent_config = mokagi._agent_config

    # 解析參數
    code = ""
    error = ""
    context = ""
    tool_name = ""
    original_args = ""

    if isinstance(args, dict):
        code = args.get("code", "")
        error = args.get("error", "")
        context = args.get("context", "")
        tool_name = args.get("tool_name", "")
        original_args = args.get("original_args", "")
    elif isinstance(args, str):
        lines = args.strip().split('\n')
        traceback_start = -1
        for i, line in enumerate(lines):
            if "Traceback (most recent call last)" in line:
                traceback_start = i
                break
        if traceback_start >= 0:
            code = "\n".join(lines[:traceback_start])
            error = "\n".join(lines[traceback_start:])
        else:
            # 沒有找到 traceback，假設整個輸入就是錯誤信息
            error = args
            code = "(未提供程式碼)"

    # 處理工具調用修正場景
    if tool_name and not code:
        # 1. 嘗試自動修復常見錯誤
        fixed_args = None
        explanation = None
        
        # 1a. 缺少依賴（ModuleNotFoundError）
        if "ModuleNotFoundError" in error or "No module named" in error:
            import re
            match = re.search(r"ModuleNotFoundError: No module named '([^']+)'", error)
            if match:
                package = match.group(1)
                # 嘗試自動 pip install
                result = subprocess.run(f"pip install --user {package}", shell=True, capture_output=True, text=True, timeout=300)
                if result.returncode == 0:
                    fixed_args = original_args   # 參數不變，直接重試
                    explanation = f"已自動安裝缺失套件 {package}。"
                else:
                    return json.dumps({"fixed": False, "error": f"無法自動安裝 {package}，請手動安裝。"})
        # 1b. 參數格式錯誤（例如 admin 工具收到 dict）
        elif "strip" in error and "dict" in error:
            # 嘗試將 original_args 中的字典轉換為字符串
            try:
                if isinstance(original_args, dict):
                    # 對於 admin 工具，特殊處理
                    if tool_name == "admin":
                        # original_args 可能已經是 {"action":"exec","args":"..."}
                        action = original_args.get("action", "")
                        args_val = original_args.get("args", "")
                        if action:
                            fixed_args = f"{action} {args_val}".strip()
                        else:
                            fixed_args = json.dumps(original_args)
                    else:
                        fixed_args = json.dumps(original_args)
                    explanation = "已將參數從字典轉換為字符串。"
                else:
                    # 嘗試用 LLM 生成修正參數
                    fixed_args, explanation = await generate_fix("", error, context, tool_name, json.dumps(original_args), llm_func=mokagi.call_llm, agent_config=agent_config, mokagi_home=mokagi.MOKAGI_home)
            except:
                fixed_args, explanation = await generate_fix("", error, context, tool_name, json.dumps(original_args), llm_func=mokagi.call_llm, agent_config=agent_config, mokagi_home=mokagi.MOKAGI_home)
        else:
            # 其他錯誤，使用 LLM 生成修正參數
            fixed_args, explanation = await generate_fix("", error, context, tool_name, json.dumps(original_args), llm_func=mokagi.call_llm, agent_config=agent_config, mokagi_home=mokagi.MOKAGI_home)

        if fixed_args is None:
            return json.dumps({"fixed": False, "error": explanation})
        
        # 返回可直接重試的修正參數
        return json.dumps({"fixed": True, "args": fixed_args, "explanation": explanation})

    # 代碼修正場景
    if not code or code == "(未提供程式碼)":
        """返回 自動修正程式碼錯誤 幫助文本"""
        help_text = f'''
{PLUGIN_INFO["icon"]} 自動修正程式碼錯誤說明：

自動修正程式碼錯誤
（提供錯誤訊息和程式碼，AI 嘗試生成修正版本）

使用方法：
    <pre>/autofix [錯誤訊息]</pre>  
    或
    在工具調用失敗時自動觸發

參數說明：
    - 可提供原始程式碼和錯誤訊息
    - 或提供 tool_name、original_args 和 error 來修正工具調用參數

=====
🧩 自然語言意圖辨識：
'''
        # 動態添加 intent_keywords（不轉義）
        for keyword, cmd in PLUGIN_INFO["intent_keywords"]:
            help_text += f'   "{keyword}" → {cmd}\n'
        return help_text

    if not error:
        return json.dumps({
            "success": False,
            "error_type": "missing_parameter",
            "error_message": "請提供錯誤訊息。",
            "tool": "autofix",
            "original_args": str(args)
        }, ensure_ascii=False)

    # 產生修正版本
    fixed_code, explanation = await generate_fix(code, error, context, llm_func=mokagi.call_llm, agent_config=agent_config, mokagi_home=mokagi.MOKAGI_home)
    if fixed_code is None:
        return f"❌ 無法生成修正程式碼：{explanation}"

    #可選：自動執行修正後的程式碼以驗證（需使用者確認，這裡先不自動執行）
    # 改為提供修正程式碼，讓使用者複製或確認後再執行
    result = f"""🔧 自動修正建議

{explanation}
修正後的程式碼：
```python
{fixed_code}
```
請複製上方程式碼並再次執行。如果需要我自動執行修正後的程式碼。
"""

    # autofix.py - handle_autofix 函數末尾
    from admin import request_confirmation, is_admin
    if chat_id and is_admin(chat_id, agent_config):
        token = request_confirmation(
            chat_id=chat_id,
            cmd_type="autofix_exec",
            args=fixed_code,
            description="執行自動修正後的 Python 程式碼"
        )
        result += f"\n\n⚠️ 將執行修正後的程式碼，請確認：\n<pre>/admin confirm {token}</pre>"
    else:
        result += "\n\n（非管理員無法執行修正程式碼）"

    return result

# ------------------------------------------------------------------------------------
# 額外功能：處理使用者確認執行修正程式碼
# 這需要掛接到 mokagi.process_message 的確認流程，或者單獨提供一個命令 /confirm_fix
# 但為了最小改動，我們不在本工具中實現完整確認邏輯，僅提供修正建議。
# 如果希望集成確認機制，可以擴展 admin.py 的確認碼機制或使用 _pending_confirm。
# ------------------------------------------------------------------------------------

async def execute_autofix_code(code: str) -> tuple:
    """實際執行修正後的 Python 代碼"""
    from autofix import execute_code_safely
    success, out, err = await execute_code_safely(code)
    if success:
        result = f"✅ 修正代碼執行成功\n輸出：\n{out[:1000]}" if out else "✅ 修正代碼執行成功（無輸出）"
        return True, result
    else:
        return False, f"❌ 執行修正代碼時出錯：\n{err[:1000]}"
