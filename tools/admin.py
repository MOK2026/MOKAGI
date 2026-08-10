# ------------------------------------------------------------------------------------ #
# 字典: PLUGIN_INFO
# 用途: 定義管理工具與主程式、意圖辨識系統之間的介面。
#       主程式透過它來註冊 /admin 命令、建立自然語言關鍵詞映射、
#       提供給 LLM 的工具描述，以及指定結果自然化函數。
# 欄位說明:
#   command           : 直接命令 "/admin"，顯示於菜單。
#   icon              : 工具圖示。
#   handler           : 處理函數名稱 "handle_admin"。
#   description       : 簡短描述，用於命令選單。
#   naturalize_func   : 結果自然化函數名 "naturalize_admin_result"。
#   tool_schema       : 提供給 LLM 的工具定義，描述參數與用途。
#   update            : 最後更新日期。
# ------------------------------------------------------------------------------------ #
PLUGIN_INFO = {
    "command": "/admin",  # 這個命令會出現在 TG 菜單
    "icon":"🤖",
    "handler": "handle_admin",
    "description": "系統管理工具：查看系統負載(cpu/htop)、讀取文件(read_file)、列出/切換/刪除Ollama模型(mode/set_model/ollama_rm)、安裝Python包(pip install)、查看日誌(logs)、執行Shell命令(exec)*所有需要新增工具如創建程式，修改一切主機內容，都有可能用這(exec)功能做到，必須小心使用*。",
    "intent_keywords": [
        ("/讀本機", "/admin read_file"),
        ("/查看", "/admin read_file"),

        ("/htop", "/admin htop"),

        ("/cpu", "/admin cpu"),

        ("/現在模型", "/admin mode"),

        ("/轉模型", "/admin set_model "),

        ("/logs", "/admin logs"),

        ("/pip", "/admin pip install"),

        ("/妳可以", "/admin exec"),
    ],
    "naturalize_func": "naturalize_admin_result",

    "tool_schema": {
        "name": "admin",
        "description": (
            "執行系統管理操作。支援以下動作：htop, cpu, mode, logs, read_file, set_model, ollama_rm, pip, exec。\n\n"
            "【重要】高風險操作（ollama_rm, pip install, exec）需要二次確認。系統會返回以 CONFIRM_SPLIT: 開頭的警告消息，"
            "LLM 必須原樣展示給用戶，並提示用戶發送 `/admin confirm <token>` 來確認執行。\n\n"
            "【返回格式】\n"
            "- 成功：返回人類可讀的字符串（或 JSON 包含 action 等字段）。\n"
            "- 需要確認時：返回格式「CONFIRM_SPLIT:警告內容\\n---CONFIRM_SPLIT---\\n/admin confirm <token>」。\n"
            "- 錯誤時返回 JSON：{\"success\": false, \"error_type\": \"...\", \"error_message\": \"...\"}。\n\n"
            "【權限】部分操作僅限管理員（`ADMIN_CHAT_ID` 配置的用戶），非管理員會返回權限錯誤。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "htop", "cpu", "mode", "logs", "read_file",
                        "set_model", "ollama_rm", "pip", "exec"
                    ],
                    "description": (
                        "要執行的操作類型。詳細說明：\n\n"
                        "**htop**：查看系統負載（top 前5行）。不需要 args。\n\n"
                        "**cpu**：查看 CPU 使用率。不需要 args。\n\n"
                        "**mode**：顯示當前使用的模型以及 Ollama 中已安裝的所有模型列表。不需要 args。\n\n"
                        "**logs**：查看 pm2 日誌。可選 args 為行數（數字），默認 15。例如「50」表示查看最近 50 行。\n\n"
                        "**read_file**：讀取檔案內容。args 格式：「檔案路徑 [行數]」。例如「admin.py」讀取全文，「admin.py 20」讀取前20行。\n\n"
                        "**set_model**：切換當前 Agent 使用的模型。args 為模型名稱（必須已在配置文件中定義）。例如「llama3.2:3b」。\n\n"
                        "**ollama_rm**：刪除 Ollama 模型（高風險，需二次確認）。args 為模型名稱。例如「llama3.2:3b」。\n\n"
                        "**pip**：安裝 Python 套件（高風險，需二次確認）。args 格式：「install 套件名」。例如「install requests」。\n\n"
                        "**exec**：執行任意 Shell 命令（高風險，需二次確認）。args 為完整的 Shell 命令。例如「ls -la」或「curl https://api.example.com」。"
                    )
                },
                "args": {
                    "type": "string",
                    "description": (
                        "操作的參數，格式取決於 action（見 action 描述）。\n"
                        "特別注意：\n"
                        "- 對於 read_file：若只給路徑則讀全文；若空格後跟數字則讀前 N 行。\n"
                        "- 對於 set_model：只給模型名稱，不要加其他字符。\n"
                        "- 對於 pip：必須以「install」開頭，後接套件名。\n"
                        "- 對於 exec：直接寫命令，不需要前綴。\n"
                        "- 其他 action 可省略 args。"
                    )
                }
            },
            "required": ["action"]
        }
    },
    "sub_tools": [
        {
            "name": "admin_read_room",
            "description": (
                "【凜の房間閱讀】讀取自己房間（agent目錄）內的文件。\n\n"
                "參數格式：\n"
                "- `文件名`                → 讀取房間內指定文件的全文（可能會截斷）\n"
                "- `文件名 行數`           → 讀取前 N 行\n"
                "- `文件名 起始行 行數`    → 從起始行開始讀取 N 行（行號從 1 開始）\n\n"
                "範例：\n"
                "- 讀取配置：`.客服`\n"
                "- 讀取前20行：`.客服 20`\n\n"
                "⚠️ 凜の權限管制：只能讀取自己房間（~/.mok/agent/{你的名字}/）內的文件，\n"
                "無法讀取房間外的任何文件。如需讀取其他文件，請聯絡管理員。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "args": {
                        "type": "string",
                        "description": "文件名（相對於自己房間目錄），可選行數。例如：`.客服 20`"
                    }
                },
                "required": ["args"]
            }
        },
        {
            "name": "admin_read_file",
            "description": (
                "讀取指定檔案內容。\n\n"
                "參數格式：\n"
                "- `檔案路徑`                → 讀取全文（可能會截斷）\n"
                "- `檔案路徑 行數`           → 讀取前 N 行\n"
                "- `檔案路徑 起始行 行數`    → 從起始行開始讀取 N 行（行號從 1 開始）\n\n"
                "範例：\n"
                "- 讀取全文：`admin.py`\n"
                "- 讀取前20行：`admin.py 20`\n"
                "- 讀取第 300 行開始的 30 行：`admin.py 300 30`\n\n"
                "返回結果：成功時返回檔案內容（超過3500字符會截斷），失敗時返回 JSON 錯誤。\n"
                "注意：只能讀取普通文本文件，超大文件可能超時。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "args": {
                        "type": "string",
                        "description": "檔案路徑 [行數] 或 檔案路徑 起始行 行數"
                    }
                },
                "required": ["args"]
            }
        },
        {
            "name": "admin_exec",
            "description": (
                "執行任意 Shell 命令。**高風險操作，需要用戶二次確認。**\n\n"
                "參數：完整的 Shell 命令。\n"
                "範例：\n"
                "- `ls -la`\n"
                "- `curl https://api.example.com`\n"
                "- `df -h`\n\n"
                "返回：系統會先返回確認碼（格式 `CONFIRM_SPLIT:...`），LLM 必須原樣展示給用戶，"
                "等待用戶發送 `/admin confirm <token>` 後才會真正執行。\n\n"
                "成功執行後返回命令的 stdout（前3000字符），失敗返回 stderr。\n"
                "若命令風險等級為 `safe` 或 `low` 且環境變量 `MOK_AUTO_APPROVE_ADMIN=1`，則可能直接執行無需確認。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "args": {
                        "type": "string",
                        "description": "Shell 命令"
                    }
                },
                "required": ["args"]
            }
        },
        {
            "name": "admin_pip",
            "description": (
                "安裝 Python 套件。**高風險操作，需要用戶二次確認。**\n\n"
                "參數格式：`install 套件名`（必須以 install 開頭）。\n"
                "範例：\n"
                "- `install requests`\n"
                "- `install numpy pandas`\n\n"
                "返回：系統會先返回確認碼，LLM 必須提示用戶確認。確認後執行 pip install --user。\n"
                "成功時返回安裝輸出摘要，失敗返回錯誤信息。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "args": {
                        "type": "string",
                        "description": "install 套件名"
                    }
                },
                "required": ["args"]
            }
        },
        {
            "name": "admin_set_model",
            "description": (
                "切換當前 Agent 使用的模型。\n\n"
                "參數：模型名稱（必須已存在於 Agent 配置文件的模型列表中）。\n"
                "範例：`llama3.2:3b`\n\n"
                "返回：成功時會修改配置文件並排程重啟 Agent（2秒後）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "args": {
                        "type": "string",
                        "description": "模型名稱"
                    }
                },
                "required": ["args"]
            }
        },
        {
            "name": "admin_ollama_rm",
            "description": (
                "刪除 Ollama 模型。**高風險操作，需要用戶二次確認。**\n\n"
                "參數：模型名稱。\n"
                "範例：`llama3.2:3b`\n\n"
                "注意：若模型正在使用中（由 `ollama ps` 檢查），刪除會失敗，需先停止使用。\n"
                "返回：先返回確認碼，確認後執行刪除。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "args": {
                        "type": "string",
                        "description": "模型名稱"
                    }
                },
                "required": ["args"]
            }
        },
        {
            "name": "admin_logs",
            "description": (
                "查看 pm2 日誌。\n\n"
                "參數：可選行數（數字），默認 15。\n"
                "範例：\n"
                "- 不帶參數（返回最近15行）\n"
                "- `50`（返回最近50行）\n\n"
                "返回日誌內容（最多4000字符）。"
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "args": {
                        "type": "string",
                        "description": "行數（可選）"
                    }
                },
                "required": []
            }
        },
        {
            "name": "admin_htop",
            "description": (
                "查看系統負載（top 前5行）。無需參數。\n\n"
                "返回 CPU、內存、進程等摘要信息。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        },
        {
            "name": "admin_cpu",
            "description": (
                "查看 CPU 使用率（從 /proc/stat 計算）。無需參數。\n\n"
                "返回當前 CPU 使用百分比。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        },
        {
            "name": "admin_mode",
            "description": (
                "顯示當前 Agent 正在使用的模型名稱以及 Ollama 中已安裝的所有模型列表。無需參數。\n\n"
                "返回格式示例：當前模型：llama3.2:3b；已安裝模型：...\n"
                "注意：當前模型可能與實際運行模型有延遲（重啟後生效）。"
            ),
            "parameters": {"type": "object", "properties": {}, "required": []}
        }
    ],
    # ========== 新增：子工具列表（每個子工具獨立）==========
    "sub_tools": [
        {
            "name": "admin_read_room",
            "description": "【凜の房間閱讀】讀取自己房間（agent目錄）內的文件。只能讀取 ~/.mok/agent/{agent_name}/ 下的文件。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["read_room"],
                        "description": "固定為 'read_room'"
                    },
                    "args": {
                        "type": "string",
                        "description": "文件名（相對於自己房間目錄），可選行數。例如 '.客服 20'"
                    }
                },
                "required": ["action", "args"]
            }
        },
        {
            "name": "admin_read_file",
            "description": "讀取檔案內容。例如：action='read_file', args='/home/ubuntu/.mok/core/mokagi.py' 或 args='/home/ubuntu/.mok/core/mokagi.py 50'。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["read_file"],
                        "description": "固定為 'read_file'"
                    },
                    "args": {
                        "type": "string",
                        "description": "檔案路徑，可選行數，例如 '/home/ubuntu/.mok/core/workflow.py'"
                    }
                },
                "required": ["action", "args"]
            }
        },
        {
            "name": "admin_exec",
            "description": "執行任意 Shell 命令（高風險，需二次確認）。例如：action='exec', args='ls -la'。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["exec"],
                        "description": "固定為 'exec'"
                    },
                    "args": {
                        "type": "string",
                        "description": "要執行的 Shell 命令"
                    }
                },
                "required": ["action", "args"]
            }
        },
        {
            "name": "admin_pip",
            "description": "安裝 Python 套件（高風險，需二次確認）。例如：action='pip', args='install requests'。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["pip"],
                        "description": "固定為 'pip'"
                    },
                    "args": {
                        "type": "string",
                        "description": "參數，必須以 'install' 開頭，例如 'install requests'"
                    }
                },
                "required": ["action", "args"]
            }
        },
        {
            "name": "admin_set_model",
            "description": "切換當前使用的模型。例如：action='set_model', args='llama3.2:3b'。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["set_model"],
                        "description": "固定為 'set_model'"
                    },
                    "args": {
                        "type": "string",
                        "description": "模型名稱，如 'llama3.2:3b'"
                    }
                },
                "required": ["action", "args"]
            }
        },
        {
            "name": "admin_ollama_rm",
            "description": "刪除 Ollama 模型（高風險，需二次確認）。例如：action='ollama_rm', args='llama3.2:3b'。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["ollama_rm"],
                        "description": "固定為 'ollama_rm'"
                    },
                    "args": {
                        "type": "string",
                        "description": "模型名稱"
                    }
                },
                "required": ["action", "args"]
            }
        },
        {
            "name": "admin_logs",
            "description": "查看 pm2 日誌。例如：action='logs', args='50'（可選，預設15行）。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["logs"],
                        "description": "固定為 'logs'"
                    },
                    "args": {
                        "type": "string",
                        "description": "可選，行數（數字）"
                    }
                },
                "required": ["action"]
            }
        },
        {
            "name": "admin_htop",
            "description": "查看系統負載（top 前5行）。無需參數。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["htop"],
                        "description": "固定為 'htop'"
                    }
                },
                "required": ["action"]
            }
        },
        {
            "name": "admin_cpu",
            "description": "查看 CPU 使用率。無需參數。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["cpu"],
                        "description": "固定為 'cpu'"
                    }
                },
                "required": ["action"]
            }
        },
        {
            "name": "admin_mode",
            "description": "顯示當前使用的模型以及 Ollama 中已安裝的所有模型列表。無需參數。",
            "parameters": {
                "type": "object",
                "properties": {
                    "action": {
                        "type": "string",
                        "enum": ["mode"],
                        "description": "固定為 'mode'"
                    }
                },
                "required": ["action"]
            }
        }
    ],
    "update": "202608110022_出街版"
}
import os,re, logging, html, time, hashlib, subprocess, json, httpx
import shlex
from typing import Optional, Dict
import docker
import tempfile
import mokagi
mokagi_name = mokagi.MOKAGI_home

# ---------- 安全目錄白名單 ----------
MOKAGI_HOME = os.path.expanduser(f"~/.{mokagi_name}")
# 當前 Agent 的工作目錄（由 agent_config 提供）
def get_allowed_dirs(agent_config=None):
    if agent_config is None:
        agent_config = mokagi._agent_config
    agent_name = agent_config.get("MOK_AGENT_NAME", "")
    agent_dir = os.path.join(MOKAGI_HOME, agent_name) if agent_name else None
    dirs = [MOKAGI_HOME]
    if agent_dir:
        dirs.append(agent_dir)
    return dirs
# ------------------------------------------------------------------------------------ #
# 輔助函數: get_config_file_path
# 用途: 返回當前 agent 的配置文件路徑。
# 設計:
#   根據環境變數 AD_AgiName 和 AD_MOK_AGENT_NAME 組合路徑 ~/.{mokagi_name}/.{agent_name}。
#   若 agent_name 不存在則返回 None。
# 返回:
#   str | None: 配置文件路徑或 None。
# ------------------------------------------------------------------------------------ #
def get_config_file_path(agent_name: str = None, agent_config: dict = None) -> str:
    """返回當前 agent 的配置文件路徑（如 ~/.mok/agent/客服/.客服）"""
    if agent_name is None:
        if agent_config is not None:
            agent_name = agent_config.get("MOK_AGENT_NAME")
        else:
            agent_name = mokagi.MOK_AGENT_NAME
    if not agent_name:
        return None
    config_path = os.path.join(os.path.expanduser(f"~/.{mokagi_name}/agent/{agent_name}"), f".{agent_name}")
    return config_path if os.path.exists(config_path) else None
'''
                      .                                -                        
                      #%.                             =@@%.                     
                       @@-                            @@#*                      
                       =@@                           *@@ .#                     
            :.          @@         .:               :@@.  +*                    
            -+..........#+........:@@:              @@-    %*                   
            ##====================*@@@             #@-      @#                  
           -@-        .           =@%             +@=       -@%.                
          -@@.       *@@-         @#             +@=         -@@=               
         -@@#        @@:         -*             *@-           -@@%:             
         -@*        =@#          .             *%.             .@@@#            
                    %@:                       ##               : *@@@*.         
                   :@%             @*        #-               +@* -@@@%         
          ---------%@#------------#@@#     :#  %%%%%%%%%%%%%%%@@@#  #%          
          --------*@%-------=@@*------    +-           @@                       
                  *@=       :@@          .             @@                       
                  @%        +@*                        @@                       
                 *@-        @@.                        @@                       
                .@%        =@#                         @@       :               
                #@:        @@:                         @@      =@#              
               :@#        *@*                 -########@@%#####@@@#             
               =@#-      :@@                           @@.                      
                  =%@#=.:@@:                           @@                       
                    .+%@@@%                            @@                       
                      =@@@@@#:                         @@                       
                     *@%  -%@@@+                       @@                       
                   +@%-     :#@@@+                     @@          +            
                :*@#:         :#@@@.                   @@.        =@@:          
             -*%+-              -%@@      -############%%#########%%%%          
         .+*+-                    #@.                                           
'''

# ------------------------------------------------------------------------------------ #
# 敏感命令二次確認機制
# 用途: 對於危險操作（刪除模型、pip install、shell exec）採用確認碼機制，
#       防止誤觸。使用者發送確認碼後才真正執行。
# 全域變數:
#   pending_confirmations : 儲存待確認的命令，key=token, value={cmd, args, chat_id, timestamp}

#方案 A：僅啟用自動批准（推薦）
# 在您的 Agent 設定檔（例如 ~/.mok/.default）中加入：
# MOK_AUTO_APPROVE_ADMIN=1

# ------------------------------------------------------------------------------------ #
pending_confirmations = {}
def generate_token(chat_id: str, cmd: str, args: str) -> str:
    """生成一次性確認 token"""
    raw = f"{chat_id}_{cmd}_{args}_{time.time()}_{os.urandom(4).hex()}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]

async def confirm_command(chat_id: str, token: str, agent_config: dict = None) -> tuple:
    """嘗試確認命令，返回 (成功標誌, 結果字串)"""
    # 檢查是否禁用了確認操作（MOK_AUTO_APPROVE_ADMIN=0 時永久禁止）
    if agent_config is None:
        agent_config = mokagi._agent_config
    auto_approve_env = os.environ.get("MOK_AUTO_APPROVE_ADMIN")
    auto_approve_cfg = agent_config.get("MOK_AUTO_APPROVE_ADMIN")
    if auto_approve_env == "0" or auto_approve_cfg == "0" or auto_approve_cfg == 0:
        return False, "❌ 權限不足。"
    # 原有確認邏輯
    if token not in pending_confirmations:
        return False, "❌ 確認碼無效或已過期。請重新發送原命令。"
    info = pending_confirmations[token]
    if str(info["chat_id"]) != str(chat_id):
        return False, "❌ 確認碼與用戶不匹配。"
    if time.time() - info["timestamp"] > 300:
        del pending_confirmations[token]
        return False, "❌ 確認碼已超時（5分鐘）。請重新發送原命令。"
    cmd_type = info["cmd"]
    args = info["args"]
    del pending_confirmations[token]
    if cmd_type == "ollama_rm":
        success, result = execute_ollama_rm(args)
    elif cmd_type == "pip_install":
        success, result = execute_pip_install(args)
    elif cmd_type == "shell_exec":
        success, result = execute_shell_command(args)
    elif cmd_type == "autofix_exec":
        from autofix import execute_code_safely
        success, out, err = await execute_code_safely(args)
        if success:
            result = f"✅ 修正後程式碼執行成功\n輸出：\n{out[:1000]}" if out else "✅ 執行成功（無輸出）"
            return True, result
        else:
            return False, f"❌ 執行修正程式碼時出錯：\n{err[:1000]}"
    elif cmd_type == "cron_add":
        from tools.cron_tool import confirm_cron_command
        return await confirm_cron_command(chat_id, args, agent_config)
    elif cmd_type == "cron_delete":
        from tools.cron_tool import confirm_cron_command
        return await confirm_cron_command(chat_id, args, agent_config)
    else:
        return False, "❌ 未知命令類型。"
    return success, result

def is_admin(chat_id: str, agent_config: dict = None) -> bool:
    """判斷當前用戶是否為管理員 網頁版自動放行"""
    if agent_config is None:
        agent_config = mokagi._agent_config
    admin_chat_id = agent_config.get("ADMIN_CHAT_ID", "")
    if chat_id and not chat_id.isdigit():
        return True
    return str(chat_id) == admin_chat_id
def request_confirmation(chat_id: str, cmd_type: str, args: str, description: str = "") -> str:
    token = generate_token(chat_id, cmd_type, args)
    pending_confirmations[token] = {
        "cmd": cmd_type,
        "args": args,
        "chat_id": chat_id,
        "timestamp": time.time(),
        "description": description
    }
    return token
# 評估命令風險等級
def assess_command_risk(command: str, agent_config: dict = None) -> str:
    if agent_config is None:
        agent_config = mokagi._agent_config
    cmd_lower = command.lower().strip()
    allowed_dirs = get_allowed_dirs(agent_config)
    
    # 安全命令（只讀，不修改系統）
    safe_commands = {
        # === 純唯讀命令（不會修改任何檔案）===
        'ls', 'cat', 'head', 'tail', 'grep', 'find', 'echo', 'wc', 'sort', 'uniq',
        'diff', 'which', 'top', 'df', 'free', 'ps', 'netstat', 'ss',
        'file', 'stat', 'du', 'ldd', 'readlink', 'basename', 'dirname', 'realpath',
        'awk', 'tr', 'cut', 'paste', 'join', 'comm',
        'date', 'uptime', 'whoami', 'id', 'groups', 'hostname', 'uname',
        'pgrep', 'pidof', 'lsof', 'fuser', 'vmstat', 'iostat',
        # === 網路診斷（唯讀）===
        'ping', 'nslookup', 'dig', 'host', 'traceroute',
        # === ollama / pm2 管理 ===
        'ollama', 'pm2'
    }
    first_word = cmd_lower.split()[0] if cmd_lower.split() else ''
    if first_word in safe_commands:
        return 'safe'
    
    # ----- 路徑檢查輔助函數 -----
    def is_path_allowed(path: str) -> bool:
        if not path:
            return True
        real = os.path.realpath(os.path.expanduser(path))
        for d in allowed_dirs:
            if real.startswith(os.path.realpath(d) + os.sep) or real == os.path.realpath(d):
                return True
        return False
    
    # ----- 解析命令中的路徑參數（簡易版）-----
    # 提取所有看起來像路徑的參數（以 / 或 ~ 開頭）
    import re
    path_pattern = re.compile(r'(?:^|\s)(/[^\s]+|~[^\s]*)')
    paths = [p.strip() for p in path_pattern.findall(command)]
    
    # 過濾掉選項（如 -rf）
    real_paths = []
    for p in paths:
        # 去除可能的前導選項符號（如 -rf / 中的 / 會單獨出現）
        if p.startswith('/') or p.startswith('~'):
            real_paths.append(os.path.expanduser(p))
    

    # ----- 判斷操作類型 -----
    # 刪除/移動類命令（無論路徑是否在白名單內，一律視為高風險）
    delete_patterns = [
        r'\brm\s+(-[rf]+|--)', r'\brmdir\s', r'\bmv\s',  # mv 會刪除來源
    ]
    for pat in delete_patterns:
        if re.search(pat, cmd_lower):
            return 'high'  # 永遠需要確認或拒絕
    # === 保護關鍵基礎設施檔案（禁止修改/刪除核心檔案）===
    protected_paths = [
        r'/home/ubuntu/\.mok/tools/admin\.py',
        r'/home/ubuntu/\.mok/core/mokagi\.py',
        r'/home/ubuntu/\.mok/core/config\.py',
        r'/home/ubuntu/\.mok/core/tool_handler\.py',
        r'/home/ubuntu/\.mok/MOKAGI\.sh',
        r'/home/ubuntu/\.mok/env\.env',
    ]
    for pp in protected_paths:
        if re.search(pp, command):
            if first_word not in safe_commands:
                return 'high'  # 修改核心檔案 → 拒絕


    # 其他危險模式（如 chmod, chown, kill, dd, sh, eval 等）
    dangerous_patterns = [
        r'\bchmod\s', r'\bchown\s',
        r'\bkilling\b', r'\bpkill\b', r'\bkillall\b', r'\bdd\s', r'\bmkfs\s',
        # === 系統關機/重啟/服務控制（絕對禁止）===
        r'\bshutdown\s', r'\breboot\s', r'\bhalt\s', r'\bpoweroff\s',
        r'\bsystemctl\s.*\b(stop|disable|mask|isolate|halt|poweroff|reboot|kexec)\b',
        r'\bservice\s.*\bstop\b',
        # === 修改系統配置 ===
        r'\bupdate-rc\.d\s', r'\bupdate-alternatives\s',
        r'\bapt\s.*\b(purge|remove|autoremove)\b', r'\bdpkg\s.*\b(purge|remove)\b',
        # === 磁碟/分割區操作 ===
        r'\bfdisk\s', r'\bparted\s', r'\bmount\s', r'\bumount\s',
        # === 修改使用者/群組 ===
        r'\buseradd\s', r'\buserdel\s', r'\busermod\s', r'\bgroupadd\s', r'\bgroupdel\s',
        r'\bpasswd\s',
        # === 防火牆/網路規則 ===
        r'\biptables\s', r'\bufw\s.*\b(deny|reject|delete)\b',
        r'\bshred\s', r'\bcurl.*\|\s*sh', r'\bwget.*\|\s*sh', r'\beval\s',
    ]
    for pat in dangerous_patterns:
        if re.search(pat, cmd_lower):
            # 檢查路徑是否在白名單內（但這類操作通常不應自動批准）
            if real_paths:
                if all(is_path_allowed(p) for p in real_paths):
                    return 'medium'   # 可考慮 medium，但建議仍需要確認
                else:
                    return 'high'
            else:
                return 'high'
    
    # 建立/修改類操作（touch, mkdir, cp, echo >, >>）
    create_patterns = [r'>', r'>>', r'\btouch\s', r'\bmkdir\s', r'\bcp\s']
    if any(re.search(pat, cmd_lower) for pat in create_patterns):
        if real_paths:
            if all(is_path_allowed(p) for p in real_paths):
                return 'low'   # 在白名單內 → 自動批准
            else:
                return 'high'  # 目標在系統目錄 → 拒絕/確認
        else:
            return 'medium'    # 無法判斷路徑，保守處理
    
    # 其餘命令（如 pip install）保持原有邏輯
    if 'pip install' in cmd_lower:
        if re.search(r'--index-url|-i', cmd_lower) and 'pypi.org' not in cmd_lower:
            return 'medium'
        if 'git+' in cmd_lower or '@' in cmd_lower:
            return 'medium'
        return 'low'
    
    # 其他（默認為 low）
    return 'low'

# ==============================================
# Docker 沙盒執行器（可選，需安裝 docker）
# ==============================================
def execute_docker_sandboxed(command: str, timeout: int = 300) -> tuple:
    """
    在一次性 Docker 容器中安全執行命令（使用 sudo docker run）。
    返回 (success, output)
    """
    # 1. 自動移除命令開頭的 sudo（容器內預設就是 root，不需要 sudo）
    command = re.sub(r'^sudo\s+', '', command.strip())
    
    image_name = "alpine:latest"

    # 定義一個內部函數來執行 docker run
    def run_container(cmd):
        docker_cmd = (
            f"sudo docker run --rm "
            f"--network none "
            f"-v /home/ubuntu/.{mokagi_name}:/home/ubuntu/.{mokagi_name} "
            f"-w /home/ubuntu/.{mokagi_name} "
            f"--memory=512m "
            f"--cpu-period=100000 --cpu-quota=50000 "
            f"{image_name} sh -c {shlex.quote(cmd)}"
        )
        return subprocess.run(
            docker_cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout + 10,
            executable="/bin/bash"
        )

    # 第一次嘗試執行
    result = run_container(command)

    # 2. 如果失敗原因是「找不到映像檔」，自動下載後重試
    if result.returncode != 0 and "Unable to find image" in result.stderr:
        # 下載 alpine 映像
        pull_cmd = f"sudo docker pull {image_name}"
        pull_result = subprocess.run(pull_cmd, shell=True, capture_output=True, text=True, timeout=120)
        if pull_result.returncode == 0:
            # 下載成功，重試執行命令
            result = run_container(command)
        else:
            return False, f"❌ 無法自動下載 Docker 映像 {image_name}，請手動執行：sudo docker pull alpine"

    # 處理執行結果
    stdout = result.stdout.strip()
    stderr = result.stderr.strip()
    if result.returncode == 0:
        return True, stdout if stdout else "命令執行成功（無輸出）"
    else:
        error_msg = stderr if stderr else stdout if stdout else "命令執行失敗（無錯誤訊息）"
        return False, f"❌ {error_msg}"
'''
                                                               -.                 -:                
                                                              -@*                 @#.               
             -=+=:    ...          ...   ......               @- +               ## +               
           =#:   :%   .#@.        +@=.   .#@:..-#+           %-  .#             =%   #              
          *#      %.   =%*        %@.     +@     =%.        #:    .%=          -%    :%.            
         =@       +.   -=@       --@.     +@      +%      :+      = *@+       -*      :@+           
         @+            - @+      + @.     +@       @=    =: =====+*- .%@=    ==   +=    %@+         
        :@:            - +@     ::.@.     +@       ##  .:              :    =.     @.    *@#        
        +@             = .@:    + .@.     +@       *%         :  .    :    -       =      :         
        +@             =  ##    = .@.     +@       +@    *+::+@  @===*%                 =           
        +@             =  -@   =  .@.     +@       +@    *.  .#  %   :*      ----------%@-          
        -@.            =   %+  +  .@.     +@       ##    *.  .#  %   :*               :%.           
         @=            =   =@ :.  .@.     +@       @=    *.  .#  %   :*              .#             
         *%       --   =    @-+   .@.     +@      =@     *.  .#  %   :*         .    *              
          @=      +-   =    *@-   .@.     +@     .@-     *+--+#  %   :*          *=.=               
           %=     %-   +    :@    .@.     +@    =#:      *.  .#  %  -@=           +@.               
            =*+==+:  .=*+=   -   =+*+=   =+*====.        =       %   :             +@:              
                                                                 #                  =#              
'''

# ------------------------------------------------------------------------------------ #
# 函數: is_model_running
# 用途: 檢查指定模型是否正在被 Ollama 使用（已載入記憶體）。
# 設計:
#   執行 ollama ps 取得當前運行中的模型列表，比對模型名稱。
#   若發生異常（如 ollama 未運行），返回 False 並記錄日誌。
# 參數:
#   model_name: 要檢查的模型名稱。
# 返回:
#   bool: True 表示正在運行，False 表示未運行或檢查失敗。
# ------------------------------------------------------------------------------------ #
def is_model_running(model_name: str) -> bool:
    try:
        result = subprocess.run("ollama ps", shell=True, capture_output=True, text=True, timeout=10)
        lines = result.stdout.strip().split('\n')[1:]
        for line in lines:
            parts = line.split()
            if parts and parts[0] == model_name:
                return True
        return False
    except Exception:
        return False

# ------------------------------------------------------------------------------------ #
# 函數: show_current_model
# 用途: 顯示當前 agent 正在使用的模型名稱（從配置檔讀取）。
# 設計:
#   透過 get_config_file_path 讀取設定檔，提取 MOK_MODEL_NAME 的值。
#   若配置檔不存在或無該變數，返回默認提示。
# 返回:
#   str: 模型名稱訊息。
# ------------------------------------------------------------------------------------ #
def show_current_model(agent_config: dict = None) -> str:
    if agent_config is None:
        agent_config = mokagi._agent_config
    config_path = get_config_file_path(agent_config.get("MOK_AGENT_NAME"))
    if not config_path:
        return "❌ 無法定位配置文件"
    try:
        with open(config_path, "r") as f:
            lines = f.readlines()
        current_model = None
        default_model = None
        for line in lines:
            if line.startswith("MOK_CURRENT_MODEL="):
                current_model = line.split("=", 1)[1].strip()
            if line.startswith("MOK_MODEL_NAME=") and not line.startswith("MOK_MODEL_NAME2"):
                default_model = line.split("=", 1)[1].strip()
        model = current_model if current_model else default_model
        if model:
            return f"🤖 當前使用的模型：{model}"
        else:
            return "🤖 當前使用的模型：未設置（將使用默認值 minimax-m3:cloud）"
    except Exception as e:
        return f"❌ 讀取配置失敗: {e}"

# ------------------ 模型切換相關函數 ------------------
# ------------------------------------------------------------------------------------ #
# 函數: set_model_in_config
# 用途: 修改配置檔中的 MOK_MODEL_NAME，並排程重啟 Agent。
# 設計:
#   讀取原配置檔，更新或新增 MOK_MODEL_NAME 行，寫回檔案。
#   使用 subprocess.Popen 在 2 秒後執行 pm2 restart，使新模型生效。
# 參數:
#   new_model: 新的模型名稱。
# 返回:
#   str: 操作結果訊息。
# ------------------------------------------------------------------------------------ #
def set_model_in_config(new_model: str, agent_config: dict = None) -> str:
    """修改配置文件中的當前激活模型（MOK_CURRENT_MODEL）"""
    if agent_config is None:
        import mokagi
        agent_config = mokagi._agent_config
    agent_name = agent_config.get("MOK_AGENT_NAME")
    config_path = get_config_file_path(agent_name, agent_config)
    if not config_path:
        return "❌ 無法定位配置文件。"
    try:
        with open(config_path, "r") as f:
            lines = f.readlines()
        # 校驗新模型是否在配置的模型列表中
        valid_models = []
        for line in lines:
            if line.startswith("MOK_MODEL_NAME"):
                model_name = line.split("=", 1)[1].strip()
                valid_models.append(model_name)
        if new_model not in valid_models:
            return f"❌ 模型 `{new_model}` 不在配置文件的模型列表中。\n可用的模型: \n" + ",\n".join(valid_models)
    except Exception as e:
        return f"❌ 讀取配置失敗: {e}"
    # 寫入 MOK_CURRENT_MODEL
    new_lines = []
    found = False
    for line in lines:
        if line.startswith("MOK_CURRENT_MODEL="):
            new_lines.append(f"MOK_CURRENT_MODEL={new_model}\n")
            found = True
        else:
            new_lines.append(line)
    if not found:
        new_lines.append(f"MOK_CURRENT_MODEL={new_model}\n")
    try:
        with open(config_path, "w") as f:
            f.writelines(new_lines)
        # 清除該 agent 的緩存，強制下次重新加載
        import mokagi
        if agent_name in mokagi._agent_config_cache:
            del mokagi._agent_config_cache[agent_name]
        # 重啟進程
        subprocess.Popen(
            "(sleep 2 && pm2 restart mok_agi) > /dev/null 2>&1 &",
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
        return f"✅ 已將當前激活模型設置為 `{new_model}`（配置已保存，2秒後自動重啟生效）。"
    except Exception as e:
        return f"❌ 寫入配置文件失敗: {e}"

# ==============================================
# ==============================================
# ================= 危險命令 ====================
# ==============================================
# ==============================================
# ------------------------------------------------------------------------------------ #
# 函數: execute_ollama_rm
# 用途: 實際執行 ollama rm 刪除模型。
# 設計:
#   使用 subprocess 執行 shell 命令，捕獲輸出。
#   此函數僅在二次確認後被呼叫。
# 參數:
#   model_name: 要刪除的模型名稱。
# 返回:
#   tuple (成功標誌, 結果訊息)
# ------------------------------------------------------------------------------------ #

def execute_ollama_rm(model_name: str) -> tuple:
    try:
        result = subprocess.run(f"ollama rm {model_name}", shell=True, capture_output=True, text=True, timeout=30)
        if result.returncode == 0:
            return True, f"✅ 模型 {model_name} 已刪除。"
        else:
            return False, f"❌ 刪除失敗: {result.stderr}"
    except Exception as e:
        return False, f"❌ 執行失敗: {e}"
# ------------------------------------------------------------------------------------ #
# 函數: execute_pip_install
# 用途: 實際執行 pip install 安裝 Python 套件。
# 設計:
#   自動添加 --user 參數避免權限問題，超時時間 300 秒。
#   輸出結果截取最後 3000 字符防止訊息過長。
# 參數:
#   rest: 套件名稱或其他 pip 參數。
# 返回:
#   tuple (成功標誌, 結果訊息)
# ------------------------------------------------------------------------------------ #
def execute_pip_install(rest: str) -> tuple:
    if "--user" not in rest:
        rest = "--user " + rest
    cmd = f"pip install {rest}"
    try:
        result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=300)
        if result.returncode == 0:
            output = html.escape(result.stdout[-3000:]) if result.stdout else "安裝成功，無控制檯輸出。"
            return True, f"✅ pip 安裝成功\n<pre>{output}</pre>"
        else:
            error = html.escape(result.stderr[-3000:]) if result.stderr else "未知錯誤。"
            return False, f"❌ pip 安裝失敗\n<pre>{error}</pre>"
    except subprocess.TimeoutExpired:
        return False, "❌ 安裝超時（超過300秒）。"
    except Exception as e:
        return False, f"❌ 執行失敗: {html.escape(str(e))}"
# ------------------------------------------------------------------------------------ #
# 函數: execute_shell_command
# 用途: 實際執行任意 Shell 命令（需二次確認）。
# 設計:
#   使用 /bin/bash 執行，超時 300 秒。成功時返回 stdout，失敗時返回 stderr。
#   輸出截取最後 3000 字符。
# 參數:
#   command: 要執行的 Shell 命令字串。
# 返回:
#   tuple (成功標誌, 結果訊息)
# ------------------------------------------------------------------------------------ #
def execute_shell_command(command: str) -> tuple:
	# 強制禁用 Docker 沙箱，直接使用宿主機 shell
    os.environ.pop('MOK_USE_DOCKER_SANDBOX', None)
    try:
        result = subprocess.run(
            ['bash', '-l', '-c', command],
            capture_output=True, text=True,
            timeout=300
        )
        stdout = result.stdout.strip()
        stderr = result.stderr.strip()
        if result.returncode == 0:
            output = stdout if stdout else "命令執行成功（無輸出）"
            return True, f"✅ 命令執行成功\n<pre>{html.escape(output[-3000:])}</pre>"
        else:
            err_msg = stderr if stderr else stdout
            return False, f"❌ 命令執行失敗 (返回碼 {result.returncode})\n<pre>{html.escape(err_msg[-3000:])}</pre>"
    except subprocess.TimeoutExpired:
        return False, "❌ 命令執行超時（超過 300 秒）"
    except Exception as e:
        return False, f"❌ 執行異常: {html.escape(str(e))}"
# ================= 自然化函數 =================

# ------------------------------------------------------------------------------------ #
# 函數: naturalize_admin_result
# 用途: 將 admin 工具返回的 JSON 結果轉換為自然口語的回覆。
# 設計:
#   根據 action 欄位分別構建結構化句子，然後讓 LLM 用更自然的口吻重新表達。
#   支援流式輸出（透過 temp_msg 和 context 參數，本函數目前僅做一次性轉換）。
#   若 LLM 呼叫失敗則返回結構化備用句子。
# 參數:
#   user_text: 原始使用者輸入。
#   raw_result: admin 工具返回的 JSON 字串。
#   ollama_api, model_name: Ollama 設定。
#   temp_msg, context: 用於流式更新（目前未使用，保留接口）。
# 返回:
#   str: 自然語言結果。
# ------------------------------------------------------------------------------------ #
async def naturalize_admin_result(user_text: str, raw_result: str, ollama_api: str, model_name: str, temp_msg=None, context=None, agent_config: dict = None) -> str:
    if agent_config is None:
        agent_config = mokagi._agent_config
    try:
        data = json.loads(raw_result)
    except:
        return raw_result
    action = data.get("action", "")
    structured = ""
    if action == "show_models":
        current = data.get("current_model", "")
        models = data.get("models", [])
        models_str = "、\n".join(models) if models else "無"
        structured = f"當前使用的模型是 {current}。\n主機上已安裝的模型有：{models_str}。"
    elif action == "set_model":
        model = data.get("model", "")
        structured = f"已成功將運行模型切換為 {model}，兩秒後自動重啟生效。"
    elif action == "ollama_rm":
        model = data.get("model", "")
        structured = f"已成功刪除模型 {model}。"
    elif action == "pip_install":
        package = data.get("package", "")
        output_summary = data.get("output", "")[:200]
        structured = f"已成功安裝 Python 套件 {package}。安裝摘要：{output_summary}"
    elif action == "shell_exec":
        command = data.get("command", "")
        output_summary = data.get("output", "")[:200]
        structured = f"Shell 命令執行成功。命令：{command}。輸出摘要：{output_summary}"
    elif action == "system_monitor":
        monitor_type = data.get("type", "")
        output = data.get("output", "")
        if monitor_type == "htop":
            structured = f"當前系統負載信息如下：\n{output[:500]}"
        elif monitor_type == "cpu":
            structured = f"當前 {output}"
        elif monitor_type == "logs":
            lines = data.get("lines", "")
            structured = f"最近的 {lines} 行日誌如下：\n\n{output[:1500]}"
        else:
            structured = f"系統信息：{output[:500]}"
    elif action == "read_file":
        path = data.get("path", "")
        lines = data.get("lines", "")
        content = data.get("content", "")
        if lines == "all":
            line_desc = "全文"
        else:
            line_desc = f"前 {lines} 行"
        preview = content[:500] + ("..." if len(content) > 500 else "")
        structured = f"已讀取文件 {path} 的 {line_desc}，內容如下：\n{preview}"
    else:
        return raw_result
    owner = agent_config.get("MOK_ADMIN_NAME", "用戶")
    prompt = f"""系統操作結果：
{structured}

請用自然口語向{owner}報告結果，每段使用 \n 分隔，直接說出結果，不要加開場白。"""
    try:
        payload = {
            "model": model_name,
            "prompt": prompt,
            "stream": False,
            "options": {
                "num_predict": 2000,
                "temperature": 0.5,
                "top_p": 0.9,
            }
        }
        async with httpx.AsyncClient(timeout=180) as client:
            resp = await client.post(ollama_api, json=payload)
            if resp.status_code == 200:
                data_resp = resp.json()
                reply = data_resp.get("response", "").strip()
                if reply:
                    return reply
    except Exception as e:
        logging.warning(f"管理結果自然化 LLM 調用失敗: {e}")
    return structured

'''
                           .                                                    
                @%+        @@=                                                  
               =@%         @%                =*.                 :@*            
               %@=         @%      ::        =@%*******@@%*******#@@#           
              .@@          @%      @@=       =@=       *@:        @#            
              =@* #%%%%%%%%@@%%%%%@@@@-      =@=       *@:        @*            
              %@           @%                =@=       *@:        @*            
             .@#           @%                =@=       *@:        @*            
             *@.           @%      :         =@=       *@:        @*            
             @@+   %*:::::=@@:::::*@*        =@=       *@:        @*            
            +@@:   %@=----=@@-----*@@:       =@#=======%@*=======+@*            
            @@@.   %@      @%     -@=        =@*::::::.#@+:::::::=@*            
           +#%@.   %@      @%     -@=        =@=       *@:        @*            
           @.*@.   %@      @%     -@=        =@=       *@:        @*            
          *- *@.   %@      @%     -@=        =@=       *@:        @*            
         .*  *@.   %@      @#     -@=        +@-       *@:        @*            
         =   *@.   %@+----+@%-----*@=        +@:       *@:        @*            
             *@.   %@-::::+@#:::::*@=        *@:       *@:        @*            
             *@.   %#     :@=     :%.        #@%#######@@%#######%@*            
             *@.     .    =@:                %@        #@=       :@*            
             *@.     +    #@                 @#        *@:        @*            
             *@.     .*   @#                 @+        *@:        @*            
             *@.      +# =@:                :@:        *@:        @*            
             *@.       ##@#                 +@         *@:        @*            
             *@.        @@+                 %*         *@:        @*            
             *@.       #@#@%-              .@          *@:        @*            
             *@.      ##  -@@@*-           *+          *@:        @*            
             *@.    -%-     +@@@@@*+-:.    %           *@:   -=+=*@*            
             *@.  -#=         -#@@@@@%    *.           *@:     :@@@-            
             *% =*-              :+%@.   ::            +*       #%=             
'''

# ------------------------------------------------------------------------------------ #
# 函數: handle_admin
# 用途: 管理命令的總入口，根據 args 路由到不同操作。
# 設計:
#   1. 無參數時顯示幫助訊息。
#   2. 處理確認命令 (/admin confirm token)。
#   3. 公開命令（htop, cpu, mode, logs, read_file）直接執行。
#   4. 敏感命令（set_model, ollama_rm, pip, exec）需檢查管理員權限及二次確認。
# 參數:
#   args: 命令參數字串。
#   chat_id: 使用者 Telegram ID（用於權限檢查）。
# 返回:
#   str: 執行結果，多為 JSON 字串或普通訊息。
# ------------------------------------------------------------------------------------ #

async def handle_admin(args, chat_id: str = None, agent_config: Optional[Dict] = None) -> str:
    if agent_config is None:
        agent_config = mokagi._agent_config
    if args is None:
        args = ""
    
    # 新增：將字典參數轉換為字符串命令格式（如 "read_file /path"）
    if isinstance(args, dict):
        action = args.get("action")
        rest = args.get("args", "")
        if action:
            args = f"{action} {rest}".strip()
        else:
            # 如果沒有 action，嘗試將整個字典轉為字符串（但最好提示）
            args = json.dumps(args, ensure_ascii=False)
    elif not isinstance(args, str):
        args = str(args)
    args = args.strip()
    logging.info(f"Admin plugin invoked: args='{args}', chat_id={chat_id}")

    sub_tools_help = {}
    for sub in PLUGIN_INFO.get("sub_tools", []):
        name = sub["name"]
        action = name.replace("admin_", "")
        desc = sub["description"]
        params = sub.get("parameters", {}).get("properties", {})
        required = sub.get("parameters", {}).get("required", [])
        if not params:
            usage = f"/admin {action}"
        else:
            args_list = []
            for param_name, param_info in params.items():
                if param_name in required:
                    args_list.append(f"<{param_name}>")
                else:
                    args_list.append(f"[{param_name}]")
            args_str = " ".join(args_list)
            usage = f"/admin {action} {args_str}"
        example = ""
        match = re.search(r"例如[：:]?\s*['\"`]?([^'\"`]+)['\"`]?", desc)
        if not match:
            match = re.search(r"`([^`]+)`", desc)
        if match:
            example = f"/admin {action} {match.group(1)}"
        elif args_list:
            placeholder = " ".join([p.strip("<>[]") for p in args_list])
            example = f"/admin {action} {placeholder}"
        sub_tools_help[action] = {
            "usage": usage,
            "example": example,
            "description": desc,
            "params": params,
            "required": required
        }
        sub_tools_help[f"/admin_{action}"] = sub_tools_help[action]

    if not args:
        help_text = f'''
{PLUGIN_INFO["icon"]} 管理命令說明：
'''
        for action, info in sorted(sub_tools_help.items()):
            if action.startswith("/"):
                continue
            help_text += f'\n{info["usage"]}\n   {info["description"]}'
            if info["example"]:
                help_text += f'\n   例：{info["example"]}'
        help_text += '''
=====
🧩 自然語言意圖辨識：
'''
        for keyword, cmd in PLUGIN_INFO["intent_keywords"]:
            help_text += f'   "{keyword}" → {cmd}\n'
        return help_text

    if args.startswith("confirm "):
        token = args.split(maxsplit=1)[1].strip()
        success, result = await confirm_command(chat_id, token, agent_config)
        return result

    if args.startswith("/admin_"):
        parts = args.split(maxsplit=1)
        full_cmd = parts[0][1:]
        action = full_cmd.replace("admin_", "")
        rest = parts[1] if len(parts) > 1 else ""
        args = f"{action} {rest}".strip()
    parts = args.split(maxsplit=1)
    action = parts[0].lower()
    rest = parts[1] if len(parts) > 1 else ""

    if action in sub_tools_help:
        info = sub_tools_help[action]
        required_params = info.get("required", [])
        if required_params and not rest:
            usage = info["usage"]
            example = info["example"]
            desc = info["description"]
            return f"用法: {usage}\n例：{example}\n\n{desc}"

    def _cmd_htop(rest: str) -> str:
        try:
            result = subprocess.run("top -bn1 | head -n 5", shell=True, capture_output=True, text=True, timeout=10)
            if result.stdout:
                return json.dumps({"action": "system_monitor", "type": "htop", "output": result.stdout.strip()}, ensure_ascii=False)
            return "無法獲取系統負載。"
        except Exception as e:
            return f"❌ 執行失敗: <pre>{e}</pre>"

    def _cmd_cpu(rest: str) -> str:
        try:
            result = subprocess.run(
                "grep 'cpu ' /proc/stat | awk '{print \"CPU使用率: \" ($2+$4)*100/($2+$4+$5) \"%\"}'",
                shell=True, capture_output=True, text=True, timeout=10
            )
            if result.stdout:
                return json.dumps({"action": "system_monitor", "type": "cpu", "output": result.stdout.strip()}, ensure_ascii=False)
            return "無法獲取 CPU 使用率。"
        except Exception as e:
            return f"❌ 執行失敗: <pre>{e}</pre>"

    def _cmd_mode(rest: str) -> str:
        try:
            now_model = show_current_model(agent_config)
            if "：" in now_model:
                now_model = now_model.split("：", 1)[1]
            result = subprocess.run("ollama list", shell=True, capture_output=True, text=True, timeout=30)
            if result.stdout:
                models = [line.split()[0] for line in result.stdout.strip().split('\n')[1:] if line.split()]
            else:
                models = []
            return json.dumps({"action": "show_models", "current_model": now_model, "models": models}, ensure_ascii=False)
        except Exception as e:
            return f"❌ 執行失敗: <pre>{e}</pre>"

    def _cmd_logs(rest: str) -> str:
        num = rest.strip() if rest else "15"
        if not num.isdigit():
            num = "15"
        try:
            current_agent = agent_config.get("MOK_AGENT_NAME", mokagi.MOK_AGENT_NAME)
            result = subprocess.run(
                f"pm2 logs {mokagi_name}_{current_agent} --lines {num} --nostream --raw",
                shell=True, capture_output=True, text=True, timeout=30
            )
            if result.stdout:
                return json.dumps({"action": "system_monitor", "type": "logs", "lines": int(num), "output": result.stdout.strip()[-4000:]}, ensure_ascii=False)
            return "沒有日誌。"
        except Exception as e:
            return f"❌ 執行失敗: <pre>{e}</pre>"
    def _cmd_read_room(rest: str) -> str:
        """凜の房間閱讀：只能讀取自己房間內的文件"""
        parts = rest.split()
        if len(parts) < 1:
            return json.dumps({"success": False, "error_type": "missing_parameter", "error_message": "請提供文件名（相對於自己房間目錄）", "tool": "admin", "original_args": args, "suggested_fix": "例如：'.客服 20'（讀取前20行）或 '.客服 300 30'（從第300行開始讀30行）"}, ensure_ascii=False)
        
        filename = parts[0]
        
        # 獲取自己房間目錄
        agent_name = agent_config.get("MOK_AGENT_NAME", "") if agent_config else ""
        if not agent_name:
            return json.dumps({"success": False, "error_type": "config_error", "error_message": "無法識別你的身份（MOK_AGENT_NAME 未設置）", "tool": "admin", "original_args": args}, ensure_ascii=False)
        
        mok_home = os.environ.get("MOKAGI_home", ".mok")
        agent_dir = os.path.realpath(os.path.expanduser(f"~/{mok_home}/agent/{agent_name}"))
        
        # 構建完整路徑：房間目錄 + 文件名
        full_path = os.path.realpath(os.path.join(agent_dir, filename))
        
        # ⚠️ 凜の鐵則：強制檢查只能在房間內
        if not (full_path.startswith(agent_dir + "/") or full_path == agent_dir):
            return json.dumps({
                "success": False, 
                "error_type": "permission_denied", 
                "error_message": f"⛔ 凜の權限管制：你在「{agent_name}」房間，只能讀取自己房間（{agent_dir}）內的文件！\n你想讀取的「{filename}」不在你的房間內。如需讀取其他文件，請聯絡管理員。",
                "tool": "admin", 
                "original_args": args
            }, ensure_ascii=False)
        # 凜の機密管制：僅封鎖特定機密檔案 .客戶（其他房間內文件可讀）
        # 凜の機密管制：禁止讀取 .客戶（無論是檔案還是目錄路徑）
        # 拆解路徑各層，檢查是否有任一部分等於 .客戶
        rel_path = full_path.replace(agent_dir, "").lstrip("/")
        path_parts = rel_path.split("/")
        if ".客戶" in path_parts:
            return json.dumps({
                "success": False,
                "error_type": "permission_denied",
                "error_message": "⛔ 凜の權限管制：你不能讀取「.客戶」，此為機密檔案/目錄，僅限管理員查閱。",
                "tool": "admin",
                "original_args": args
            }, ensure_ascii=False)
        # 檢查檔案是否存在
        if not os.path.exists(full_path):
            return json.dumps({"success": False, "error_type": "file_not_found", "error_message": f"檔案不存在於你的房間: {full_path}", "tool": "admin", "original_args": args}, ensure_ascii=False)
        
        # 檢查是否為目錄
        if os.path.isdir(full_path):
            try:
                items = os.listdir(full_path)
                file_list = "\n".join(items) if items else "（空目錄）"
                return f"📂 房間目錄內容（{agent_dir}）：\n{file_list}"
            except Exception as e:
                return json.dumps({"success": False, "error_type": "list_error", "error_message": f"無法列出目錄: {str(e)}", "tool": "admin", "original_args": args}, ensure_ascii=False)
        
        try:
            if len(parts) == 1:
                # 全文模式
                with open(full_path, 'r', encoding='utf-8') as f:
                    text = f.read()
                if len(text) > 3500:
                    text = text[:3500] + f"\n\n...（內容過長，已截斷至 3500 字符，全文共 {len(text)} 字符）"
                return text
            elif len(parts) == 2 and parts[1].isdigit():
                # 讀取前 N 行
                num_lines = int(parts[1])
                with open(full_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                text = "".join(lines[:num_lines])
                return text + (f"\n\n...（顯示前 {num_lines} 行，共 {len(lines)} 行）" if num_lines < len(lines) else "")
            elif len(parts) >= 2 and parts[1].isdigit() and parts[2].isdigit():
                # 讀取從某行開始的 N 行
                start_line = int(parts[1])
                num_lines = int(parts[2])
                with open(full_path, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                text = "".join(lines[start_line-1:start_line-1+num_lines])
                total = len(lines)
                return f"（第 {start_line}~{start_line+num_lines-1} 行，共 {total} 行）\n{text}"
            else:
                return json.dumps({"success": False, "error_type": "invalid_parameter", "error_message": "參數格式無效，請使用：文件名 [行數] 或 文件名 起始行 行數", "tool": "admin", "original_args": args}, ensure_ascii=False)
        except UnicodeDecodeError:
            return json.dumps({"success": False, "error_type": "binary_file", "error_message": f"無法讀取二進制文件: {filename}", "tool": "admin", "original_args": args}, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "error_type": "runtime_error", "error_message": f"讀取失敗: {str(e)}", "tool": "admin", "original_args": args}, ensure_ascii=False)

    def _cmd_read_file(rest: str) -> str:
        parts = rest.split()
        if len(parts) < 1:
            return json.dumps({"success": False, "error_type": "missing_parameter", "error_message": "缺少檔案路徑參數", "tool": "admin", "original_args": args, "suggested_fix": "請提供檔案路徑，例如 '/admin read_file admin.py 20' 或 '/admin read_file admin.py 300 30'"}, ensure_ascii=False)
        filepath = parts[0]
        real_path = os.path.realpath(filepath)
        
        # 凜の機密管制：禁止讀取以 . 開頭的配置檔（如 .客服、.客戶 等）
        basename = os.path.basename(real_path)
        if basename.startswith("."):
            return json.dumps({
                "success": False,
                "error_type": "permission_denied",
                "error_message": "⛔ 凜の權限管制：你不能讀取配置檔「" + basename + "」，此為機密設定檔，僅限管理員查閱。",
                "tool": "admin",
                "original_args": args
            }, ensure_ascii=False)

        # 檢查檔案是否存在
        if not os.path.exists(real_path):
            return json.dumps({"success": False, "error_type": "file_not_found", "error_message": f"檔案不存在: {real_path}", "tool": "admin", "original_args": args}, ensure_ascii=False)
        
        # 凜の路徑管制：檢查此 agent 是否有 MOK_ALLOWED_TOOLS 限制
        allowed_str = agent_config.get("MOK_ALLOWED_TOOLS", "") if agent_config else ""
        if allowed_str:
            agent_name = agent_config.get("MOK_AGENT_NAME", "")
            mok_home = os.environ.get("MOKAGI_home", ".mok")
            agent_dir = os.path.realpath(os.path.expanduser(f"~/{mok_home}/agent/{agent_name}"))
            # 允許讀取的目錄：自己的 agent 目錄
            allowed_dirs = [agent_dir]
            is_allowed = False
            for d in allowed_dirs:
                if real_path.startswith(d + "/") or real_path == d:
                    is_allowed = True
                    break
            if not is_allowed:
                return json.dumps({
                    "success": False, 
                    "error_type": "permission_denied", 
                    "error_message": f"⛔ 凜の權限管制：您只能讀取自己房間（{agent_dir}）內的文件。如需讀取其他文件，請聯絡管理員。",
                    "tool": "admin", 
                    "original_args": args
                }, ensure_ascii=False)
        
        # ===== 輔助函數：安全執行 shell 命令 =====
        def _run_cmd(cmd: str) -> tuple:
            try:
                result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=180)
                return result.returncode, result.stdout, result.stderr
            except subprocess.TimeoutExpired:
                return -1, "", "讀取超時（超過 180 秒）"
            except Exception as e:
                return -1, "", str(e)
        
        # ===== 輔助函數：獲取檔案總行數 =====
        def _get_total_lines(path: str) -> int:
            try:
                result = subprocess.run(f"wc -l < '{path}'", shell=True, capture_output=True, text=True, timeout=10)
                if result.returncode == 0 and result.stdout.strip().isdigit():
                    return int(result.stdout.strip())
            except:
                pass
            return 0
        
        try:
            # 解析參數
            if len(parts) == 1:
                # ===== 全文模式 → 檢查是否觸發自動分段 =====
                # 先快速檢查檔案大小（避免讀取超大檔案）
                file_size = os.path.getsize(real_path)
                MAX_LEN = 3500
                
                # 如果檔案小於 MAX_LEN * 2，直接讀取全文（通常不會被截斷）
                if file_size < MAX_LEN * 2:
                    cmd = f"cat '{real_path}'"
                    line_desc = "全文"
                    returncode, output, stderr = _run_cmd(cmd)
                    if returncode == 0:
                        output = output if output else "(檔案為空)"
                        if len(output) > MAX_LEN:
                            output = output[:MAX_LEN] + "\n\n... (內容過長，已截斷)"
                        return json.dumps({"action": "read_file", "path": filepath, "lines": "全文", "content": output, "actual_lines": len(output.splitlines())}, ensure_ascii=False)
                    else:
                        return json.dumps({"success": False, "error_type": "runtime_error", "error_message": f"讀取失敗: {stderr}", "tool": "admin", "original_args": args}, ensure_ascii=False)
                
                # ===== 檔案過大 → 自動分段模式 =====
                total_lines = _get_total_lines(real_path)
                if total_lines == 0:
                    # 無法取得行數，回退到普通讀取（可能被截斷）
                    cmd = f"cat '{real_path}'"
                    returncode, output, stderr = _run_cmd(cmd)
                    if returncode == 0:
                        output = output if output else "(檔案為空)"
                        if len(output) > MAX_LEN:
                            output = output[:MAX_LEN] + "\n\n... (內容過長，已截斷)"
                        return json.dumps({"action": "read_file", "path": filepath, "lines": "全文（已截斷）", "content": output, "actual_lines": len(output.splitlines()), "warning": "檔案過大，建議使用分段讀取"}, ensure_ascii=False)
                    else:
                        return json.dumps({"success": False, "error_type": "runtime_error", "error_message": f"讀取失敗: {stderr}", "tool": "admin", "original_args": args}, ensure_ascii=False)
                
                # ===== 計算分段參數 =====
                CHUNK_SIZE = 40  # 每段 40 行（確保每段 < 3500 字符）
                total_chunks = (total_lines + CHUNK_SIZE - 1) // CHUNK_SIZE
                
                # 讀取第一段
                start_line = 1
                end_line = min(CHUNK_SIZE, total_lines)
                cmd = f"sed -n '{start_line},{end_line}p' '{real_path}'"
                returncode, output, stderr = _run_cmd(cmd)
                
                if returncode != 0:
                    return json.dumps({"success": False, "error_type": "runtime_error", "error_message": f"讀取第一段失敗: {stderr}", "tool": "admin", "original_args": args}, ensure_ascii=False)
                
                content = output if output else "(檔案為空)"
                actual_lines = len(content.splitlines())
                
                # ===== 建構分段響應 =====
                response = {
                    "action": "read_file",
                    "status": "chunked",
                    "path": filepath,
                    "total_lines": total_lines,
                    "chunk_size": CHUNK_SIZE,
                    "total_chunks": total_chunks,
                    "current_chunk": 1,
                    "content": content,
                    "actual_lines": actual_lines,
                    "hint": f"📄 檔案共 {total_lines} 行，已返回第 1/{total_chunks} 段（每段 {CHUNK_SIZE} 行）。"
                }
                
                # 如果有後續段，加上 next_command
                if total_chunks > 1:
                    next_start = start_line + CHUNK_SIZE
                    response["next_command"] = f"/admin read_file {filepath} {next_start} {CHUNK_SIZE}"
                    response["hint"] += f" 使用 next_command 繼續讀取，或自行調整起始行。"
                
                return json.dumps(response, ensure_ascii=False)
            
            # ===== 參數模式：指定行數或範圍 =====
            elif len(parts) == 2 and parts[1].isdigit():
                # 前 N 行
                lines = int(parts[1])
                if lines <= 0:
                    return json.dumps({"success": False, "error_type": "invalid_parameter", "error_message": "行數必須大於 0", "tool": "admin", "original_args": args}, ensure_ascii=False)
                cmd = f"head -{lines} '{real_path}'"
                line_desc = f"前 {lines} 行"
                returncode, output, stderr = _run_cmd(cmd)
                if returncode == 0:
                    output = output if output else "(檔案為空)"
                    MAX_LEN = 3500
                    if len(output) > MAX_LEN:
                        output = output[:MAX_LEN] + "\n\n... (內容過長，已截斷)"
                    return json.dumps({"action": "read_file", "path": filepath, "lines": line_desc, "content": output, "actual_lines": len(output.splitlines())}, ensure_ascii=False)
                else:
                    return json.dumps({"success": False, "error_type": "runtime_error", "error_message": f"讀取失敗: {stderr}", "tool": "admin", "original_args": args}, ensure_ascii=False)
            
            elif len(parts) == 3 and parts[1].isdigit() and parts[2].isdigit():
                # 起始行 + 行數
                start = int(parts[1])
                count = int(parts[2])
                if start < 1:
                    start = 1
                if count <= 0:
                    return json.dumps({"success": False, "error_type": "invalid_parameter", "error_message": "行數必須大於 0", "tool": "admin", "original_args": args}, ensure_ascii=False)
                end = start + count - 1
                cmd = f"sed -n '{start},{end}p' '{real_path}'"
                line_desc = f"第 {start} 到 {end} 行"
                returncode, output, stderr = _run_cmd(cmd)
                if returncode == 0:
                    output = output if output else "(該範圍無內容)"
                    MAX_LEN = 3500
                    if len(output) > MAX_LEN:
                        output = output[:MAX_LEN] + "\n\n... (內容過長，已截斷)"
                    return json.dumps({"action": "read_file", "path": filepath, "lines": line_desc, "content": output, "actual_lines": len(output.splitlines())}, ensure_ascii=False)
                else:
                    return json.dumps({"success": False, "error_type": "runtime_error", "error_message": f"讀取失敗: {stderr}", "tool": "admin", "original_args": args}, ensure_ascii=False)
            
            else:
                return json.dumps({"success": False, "error_type": "invalid_parameter", "error_message": "參數格式錯誤，請提供 '檔案路徑'、'檔案路徑 行數' 或 '檔案路徑 起始行 行數'", "tool": "admin", "original_args": args}, ensure_ascii=False)
                
        except Exception as e:
            return json.dumps({"success": False, "error_type": "runtime_error", "error_message": f"執行失敗: {str(e)}", "tool": "admin", "original_args": args}, ensure_ascii=False)

    public_handlers = {
        "htop": _cmd_htop,
        "cpu": _cmd_cpu,
        "mode": _cmd_mode,
        "logs": _cmd_logs,
        "read_file": _cmd_read_file,
        "read_room": _cmd_read_room,
    }
    # 凜の工具權限管制：檢查 MOK_ALLOWED_TOOLS 白名單
    allowed_str = agent_config.get("MOK_ALLOWED_TOOLS", "") if agent_config else ""
    if allowed_str and action in public_handlers:
        allowed_list = [a.strip() for a in allowed_str.split(",")]
        action_tool_map = {
            "htop": "admin_htop",
            "cpu": "admin_cpu",
            "mode": "admin_mode",
            "logs": "admin_logs",
            "read_file": "admin_read_file",
            "read_room": "admin_read_room",
        }
        tool_name = action_tool_map.get(action, "admin_" + action)
        if tool_name not in allowed_list and action not in allowed_list:
            return json.dumps({
                "success": False,
                "error_type": "permission_denied",
                "error_message": "⛔ 凜の權限管制：你的工具權限僅限於【" + ", ".join(allowed_list) + "】\n你不能使用「" + tool_name + "」，如需擴權請聯絡管理員。",
                "tool": "admin",
                "original_args": args
            }, ensure_ascii=False)

    if action in public_handlers:
        return public_handlers[action](rest)

    if not chat_id or not is_admin(chat_id, agent_config):
        return json.dumps({
            "success": False,
            "error_type": "permission_denied",
            "error_message": "此操作僅限管理員執行。",
            "tool": "admin",
            "original_args": args
        }, ensure_ascii=False)

    # auto_approve: env -> agent_config -> direct file read
    auto_approve_env = os.environ.get("MOK_AUTO_APPROVE_ADMIN") == "1"
    auto_approve_cfg = agent_config.get("MOK_AUTO_APPROVE_ADMIN") == "1" if agent_config else False
    auto_approve = auto_approve_env or auto_approve_cfg

    if args.startswith("set_model"):
        parts = args.split(maxsplit=1)
        if len(parts) < 2:
            return json.dumps({
                "success": False,
                "error_type": "missing_parameter",
                "error_message": "缺少模型名稱",
                "tool": "admin",
                "original_args": args,
                "suggested_fix": "請提供模型名稱，例如 '/admin set_model llama3.2:3b'"
            }, ensure_ascii=False)
        new_model = parts[1].strip()
        return set_model_in_config(new_model, agent_config)

    if args.startswith("ollama_rm"):
        parts = args.split()
        if len(parts) < 2:
            return json.dumps({
                "success": False,
                "error_type": "missing_parameter",
                "error_message": "缺少模型名稱",
                "tool": "admin",
                "original_args": args,
                "suggested_fix": "請提供模型名稱，例如 '/admin ollama_rm llama3.2:3b'"
            }, ensure_ascii=False)
        model_name = parts[1]
        if is_model_running(model_name):
            return json.dumps({
                "success": False,
                "error_type": "conflict",
                "error_message": f"模型 {model_name} 正在使用中，無法刪除。請先停止使用該模型的應用。",
                "tool": "admin",
                "original_args": args,
                "suggested_fix": "請確保模型未被使用後重試，或使用其他模型。"
            }, ensure_ascii=False)
        token = generate_token(chat_id, "ollama_rm", model_name)
        pending_confirmations[token] = {
            "cmd": "ollama_rm",
            "args": model_name,
            "chat_id": chat_id,
            "timestamp": time.time()
        }
        warning = f"⚠️ 危險操作 ⚠️\n[刪除模型：{model_name}]"
        return f"CONFIRM_SPLIT:{warning}\n請在5分鐘內發送確認碼以執行：\n---CONFIRM_SPLIT---\n/admin confirm {token}"

    if args.startswith("pip"):
        rest = args[len("pip"):].strip()
        if rest.startswith("install"):
            rest = rest[len("install"):].strip()
        if not rest:
            return json.dumps({
                "success": False,
                "error_type": "missing_parameter",
                "error_message": "請提供要安裝的套件名稱",
                "tool": "admin",
                "original_args": args,
                "suggested_fix": "例如：/admin pip install requests"
            }, ensure_ascii=False)
        risk = assess_command_risk(rest, agent_config)
        if risk in ('safe', 'low', 'medium') and auto_approve:
            success, result = execute_pip_install(rest)
            return result if success else f"❌ 執行失敗: {result}"
        else:
            token = generate_token(chat_id, "pip_install", rest)
            pending_confirmations[token] = {
                "cmd": "pip_install",
                "args": rest,
                "chat_id": chat_id,
                "timestamp": time.time()
            }
            warning = f"⚠️ 危險操作 ⚠️\n[pip 安裝：{rest}]\n風險等級：{risk}"
            return f"CONFIRM_SPLIT:{warning}\n請在5分鐘內發送確認碼以執行：\n---CONFIRM_SPLIT---\n/admin confirm {token}"

    if args.startswith("exec"):
        rest = args[len("exec"):].strip()
        if not rest:
            return "用法: /admin exec Shell命令"
        risk = assess_command_risk(rest, agent_config)
        # 如果風險為 high，且自動批准未啟用，則直接拒絕（或要求確認）
        if risk == 'high':
            # 可選擇直接拒絕：
            return "❌ 此命令風險過高（涉及刪除/修改系統檔案），已拒絕執行。"
            # 或者保持原有確認邏輯（但這不符合「不可刪檔」的精神）
        # 其餘邏輯保持不變...
        
        # 決定執行方式
        use_docker = os.environ.get("MOK_USE_DOCKER_SANDBOX") == "1"
        
        if risk == 'safe':
            if use_docker:
                success, result = execute_docker_sandboxed(rest)
            else:
                success, result = execute_shell_command(rest)
            return result if success else f"❌ 執行失敗: {result}"
            
        # auto_approve: env -> agent_config -> direct file read
        auto_approve_env = os.environ.get("MOK_AUTO_APPROVE_ADMIN") == "1"
        auto_approve_cfg = agent_config.get("MOK_AUTO_APPROVE_ADMIN") == "1" if agent_config else False
        auto_approve = auto_approve_env or auto_approve_cfg
        if risk in ('low', 'medium') and auto_approve:
            if use_docker:
                success, result = execute_docker_sandboxed(rest)
            else:
                success, result = execute_shell_command(rest)
            return result if success else f"❌ 執行失敗: {result}"
        token = generate_token(chat_id, "shell_exec", rest)
        pending_confirmations[token] = {
            "cmd": "shell_exec",
            "args": rest,
            "chat_id": chat_id,
            "timestamp": time.time()
        }
        warning = f"⚠️ 危險操作 ⚠️\n[執行命令：{html.escape(rest)}]\n風險等級：{risk}"
        return f"CONFIRM_SPLIT:{warning}\n請在5分鐘內發送確認碼以執行：\n---CONFIRM_SPLIT---\n/admin confirm {token}"

    return json.dumps({
        "success": False,
        "error_type": "invalid_parameter",
        "error_message": f"未知管理命令: {args}",
        "tool": "admin",
        "original_args": args,
        "suggested_fix": "發送 /admin 查看可用命令。"
    }, ensure_ascii=False)