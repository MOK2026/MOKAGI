"""
202607271012_暫時可用版
mokagi.py - 統一 AI 對話核心模塊

設計目標：
- 一套代碼同時支持 Telegram、Web 等多種前端
- 保持所有現有 tools（web_search, memory, workflow, admin, intent...）不變
- 統一處理：直接命令 → 意圖識別 → 多步工作流 → 工具調用 → 自然化
- 提供流式輸出接口，前端只需傳入異步回調即可

使用示例（Telegram 適配器）：
    await mokagi.process_message(
        user_id=str(chat_id),
        text=user_message,
        stream_callback=partial(telegram_stream_callback, context, message)
    )

使用示例（Web SocketIO 適配器）：
    await mokagi.process_message(
        user_id=session_id,
        text=user_message,
        stream_callback=web_stream_callback
    )
"""

import asyncio
import hashlib
from hashlib import md5
import html
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone, timedelta
import subprocess
import platform
import fcntl
import openai
from collections import defaultdict
from typing import Dict, List, Optional, Callable, Awaitable, Any, Tuple, Union, AsyncGenerator

import sqlite3
from contextlib import closing

import httpx

# 導入工具管理模塊（獨立於前端）
import tool_handler, recovery

















'''

                           +*               -           =:                        .%-               
                          =@@@.            #@#          *@%+++++++++++++++++++++++@@@+              
           =@@@@@@@@@@@@@@@@@@@ +@@@@@@@@@@@@@@.        *@#-----+@@+-----*@@=-----#@@#              
                 #@= :@@.                  #@%=         *@+      @@.     -@%      +@%               
                 *@.  @@                   *@*          *@+      @@.     -@%      +@%               
                 *@.  @@                   *@*          *@+      @@.     -@%      +@%               
                 *@.  @@                   *@*          *@+      @@.     -@%      +@%               
                 *@.  @@   ..              *@*          *@%*****#@@#*****%@@******%@%               
            +%-  #@= -@@: :@@+             *@*          *@#:::::::::::::::::::::::#@%               
            +@@@@@@@@@@@@@@@@@+            *@*          *@=         =#+-          +@%               
            +@*  =@- .@#   @@+             *@*          ..          *@@*           :@%.             
            +@*  =@.  @#   @@.             *@*       :::::::::::::::%@@-::::::::::-%@@@=            
            +@*  =@.  @#   @@.  -%-        *@*      .##############%@@%#################            
            +@*  =@.  @#   @@.  -@@%#######@@*                      @@                              
            +@*  +@   @#   @@.  -@@:       #@*                     :@#          +                   
            +@*  *@   @#   @@.  -@@        *@*             %#:     =@=         +@@:                 
            +@*  %*   @#   @@.  -@@        *@*             %@@@@@@@@@@@@@@@@@@@@@@@=                
            +@*  @-   @%..-@@.  -@@        -+              %@*                 =@@-                 
            +@* +@    @@@@@@@.  -@@                        %@+                 -@@                  
            +@* @-    :***+@@.  -@@                        %@+                 -@@                  
            +@**+          @@.  -@@                        %@%=================#@@                  
            +@%-           @@.  -@@                        %@#-----------------*@@                  
            +@*            @@.  -@@                        %@+                 -@@                  
            +@*            @@.  -@@                        %@+                 -@@                  
            +@%-----------=@@.  -@@                        %@#:::::::::::::::::*@@                  
            +@@############@@.  -@@                        %@@#################%@@                  
            +@*            @@.  -@@            =           %@+                 -@@                  
            +@*            @@.  -@@            +           %@+                 -@@                  
            +@*            @@.  -@@           :*           %@*                 +@@                  
            +@*            @@.  -@@           +*           %@@@@@@@@@@@@@@@@@@@@@@                  
            +@*            @@.  -@@           #*           %@*                 =@@                  
            +@%************@@.  -@@           @*           %@+                 -@@   =              
            +@#-----------=@@.  :@@*--------=#@@:          %@+                 -@@  #@%             
            +@*            @@.   @@@@@@@@@@@@@@@+   ::::::-@@#:::::::::::::::::*@@=*@@@%.           
            +@*            @%    .+**********+=:    =+++++++++++++++++++++++++++++++++++:           
            +%.            .                                                                        


'''

from config import (
    MOKAGI_home,
    _agent_config,
    MOK_MODEL_NAME,
    MOK_AGENT_NAME,
    OLLAMA_API,
    OLLAMA_OPTIONS,
    MAX_HISTORY_ROUNDS,
    MEMORY_RECALL_COUNT,
    get_agent_config,
    load_agent_config,
    _agent_config_cache
)



toolsBtn = 1



test = False  # 調試開關，控制是否輸出詳細調試信息
_pending_llm_confirm = {}  # {context_id: {"messages": [...], "params": {...}, "timestamp": float}}

TASK_COMPLETE_MARKER = "TASK_COMPLETE: true"
TASK_COMPLETE_ALT = "任務完成"

# 模型回應的最大等待時間（秒），超過則認定為失敗，避免{owner}長時間等待
_model_timeout = 300.0

# 確保工具目錄在 Python 路徑中
TOOLS_DIR = os.path.expanduser(f"~/.{MOKAGI_home}/tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)



















# ========== Agent 配置緩存（隔離不同 Agent）==========

_config_cache_lock = None

def get_config_lock():
    global _config_cache_lock
    if _config_cache_lock is None:
        _config_cache_lock = asyncio.Lock()
    return _config_cache_lock



# 回覆內容末尾追加模型標籤
def get_model_tag(model_name: str = None) -> str:
    if model_name is None:
        model_name = MOK_MODEL_NAME   # 兼容舊調用
    return f"\n\n---\n🧠 : {model_name}\n\n---\n"



def _get_unique_user_id(user_id: str, agent_name: str = None) -> str:
    """返回結合 Agent 名稱的唯一 ID，用於內部狀態隔離"""
    if agent_name is None:
        agent_name = _agent_config.get("MOK_AGENT_NAME", "default")
    return f"{user_id}_{agent_name}"


# 按 Agent 隔離
def _get_pending_key(user_id: str, agent_name: str = None) -> str:
    return _get_unique_user_id(user_id, agent_name)

# ----------------------------------------------------------------------
# 配置加載（從環境變量或 agent 專屬配置文件）
# ----------------------------------------------------------------------














# 讓每個 Agent 知道自己所在主機的配置狀態
_system_context_cache = {}  # {agent_name: (context_str, timestamp)}
_system_context_ttl = 60    # 緩存 60 秒
def get_system_context(agent_name: str, owner: str, owner_time: int=0, context_files: Optional[List[str]] = None) -> str:
    """獲取主機環境信息 + Agent 工作目錄，帶緩存
    
    context_files: 可選，指定要載入的 soul 文件列表（如 ["agent.md", "user.md"]）。
                   若為 None（預設），載入 soul/ 目錄下所有文件。
                   若為空列表 []，不載入任何 soul 文件。"""
    global _system_context_cache
    now = time.time()
    cache_key = f"{agent_name}:{'__ALL__' if context_files is None else ','.join(sorted(context_files))}"
    cached = _system_context_cache.get(cache_key)
    if cached and (now - cached[1]) < _system_context_ttl:
        return cached[0]

    # 收集系統信息（使用 safe 方法，避免命令失敗）
    try:
        uname = platform.uname()
        os_info = f"{uname.system} {uname.release} ({uname.machine})"
    except:
        os_info = "Unknown OS"

    try:
        cpu_model = subprocess.getoutput(
            "grep -m1 'model name' /proc/cpuinfo | cut -d':' -f2"
        ).strip() or "Unknown CPU"
    except:
        cpu_model = "Unknown CPU"

    try:
        mem_total = subprocess.getoutput("free -h | grep 'Mem:' | awk '{print $2}'") or "Unknown"
    except:
        mem_total = "Unknown"

    try:
        disk_usage = subprocess.getoutput("df -h / | tail -1 | awk '{print $2, $3, $4, $5}'") or "Unknown"
    except:
        disk_usage = "Unknown"

    work_dir = os.path.expanduser(f"~/.{MOKAGI_home}/agent/{agent_name}")
    tools_dir = os.path.expanduser(f"~/.{MOKAGI_home}/tools")

    # 時區
    utc_now = datetime.now(timezone.utc)
    try:
        hours_offset = int(owner_time)
    except (ValueError, TypeError):
        hours_offset = 0
    hk_time = utc_now + timedelta(hours=hours_offset)
    now_time = hk_time.strftime("%Y-%m-%d %H:%M:%S")

    # ===== 讀取 soul 目錄下所有文件 =====
    soul_dir = os.path.expanduser(f"{work_dir}/soul")
    parts = []

    if os.path.isdir(soul_dir):
        # 獲取目錄下所有文件（按文件名排序以保證確定性順序）
        for filename in sorted(os.listdir(soul_dir)):
            # 🔧 context_files 過濾：若指定了文件列表，只讀取列表中的文件
            if context_files is not None and filename not in context_files:
                continue
            file_path = os.path.join(soul_dir, filename)
            # 只讀取普通文件，跳過子目錄
            if os.path.isfile(file_path):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read().strip()
                        if content:
                            parts.append(f"## 來自 {filename}\n\n{content}")
                except Exception as e:
                    logging.warning(f"讀取靈魂文件 {filename} 失敗: {e}")

    # 添加主機環境信息（程序動態生成）
    # 濃縮環境資訊（節省 Token）
    env_info = f"""【環境】
時間: {now_time} | 系統: {os_info} | CPU: {cpu_model} | 記憶體: {mem_total}
磁碟: {disk_usage}
目錄: {work_dir} | 工具: {tools_dir}
- 妳的房間: ~/.{MOKAGI_home}/agent/{agent_name}/
- 權限: 可直接用 /admin exec 執行指令，無需請示。
- 需調用工具時，JSON 格式: {{"name": "工具名", "arguments": {{...}}}}
- 普通問候/閒聊 → 直接自然語言回覆。
"""
    parts.append(env_info)

    context = "\n\n---\n\n".join(parts)

    _system_context_cache[cache_key] = (context, now)
    return context



















































'''

            :  :                                                                  .                 
         .  @ :#  =     #*       #          -#@-     :             .@-           +#                 
         *: @ :* +#     #-       +#      :*@*-       @+===============           ++                 
         :%.@ :* %      #-        + -- ::.-@         @    .+.     -*             ++                 
          %.@ :*:       #-    ------**    .@         @ .=#@-:  -+@+:      -#-----#%-----%%          
         :-=@-+%:=@=    #-:               .@         @   :%      @        -#     ++     *+          
          +-   =@+ +====%#@#       .=     .@   *     @   =%:%-  .@-.%=    -#     ++     *=          
           %   +#       %=     ====+* :---+@--=*+    @.::@@-:.::%@#:::    -#     ++     *=          
           *=  #        #-                .@         @  .@%+-  .@@*.      -#     ++     *=          
           += .= #: -   #-         :=     .@         @  #*% #. #:@.#-     -#     **     #=          
         -===@*===-  %  #-     ====+*     .@        .@ =:.%   +  @ .@+    -%=====%#=====%=          
             %-      %- #-             +. -@. *=    .%.. .%  -   @  .     -#     #=     *-          
             %-      *+ #-     -    +  @=:::::##    :#       .*  .           :   @:                 
          ===@*=%@   :. #-     @+--+@: @.     *=    -+   :   :%              :  .@                  
             %=         #-     @   .@  @.     *=    +-   @:  :%   +%          + *+                  
             %-         #-     @   .@  @.     *=    *.   @.  :@:::::           #@                   
             %-  .:     #-     @   .@  @.     *=    #    @.  :%                %##:                 
         .-=+@%*=.      #-     @-  -@  @:     *=   .=    @.  :%              -#  :%%+:              
        .@@*-.       .-+@:     @-..=@  @*=====%=   =    .@-  -%    :@+     :+-     :*@@%*+=         
         .             #*      %       @      =:   :  ::::::::::::::::   :-.          .=*%+         


'''



# ----------------------------------------------------------------------
# 對話歷史管理（內存存儲，可按需擴展為持久化）
# ----------------------------------------------------------------------











# ========== 自動語義搜索（用於對話上下文）==========
async def auto_semantic_search_context(
    user_id: str,
    query: str,
    stream_callback: Optional[Callable] = None,
    n_results: int = 3,
    agent_config: Optional[Dict] = None
) -> str:
    """
    自動執行語義搜索，返回格式化的上下文文本，並通過 stream_callback 輸出 think 過程（包括聯想詞）。

    參數:
        user_id: 用戶 ID
        query: 查詢字串
        stream_callback: 異步回調，用於輸出思考過程
        n_results: 返回的對話記錄最大條數（預設 3）
        agent_config: Agent 配置字典
    """
    # ----- 可調整的常量（寫死值集中於此）-----
    ASSOC_COUNT = 5          # 每個核心關鍵詞生成的聯想詞數量
    KEYWORD_LIMIT = 15       # 最終用於搜索的聯想詞總數上限
    # ----------------------------------------

    async def _t(msg):
        if stream_callback:
            if isinstance(msg, dict):
                # 如果 msg 已經是字典（例如 {"type":"think","content":"..."}），直接傳遞
                await stream_callback(msg)
            else:
                # 否則當作字串，包裝成 think 事件
                await stream_callback({"type": "think", "content": msg})
    
    await _t(f"🔍 正在分析「{query[:30]}...」的關鍵詞...\n")
    try:
        final_keywords = []  # 預設值
        from tools.associate import extract_keywords_from_sentence, _generate_associations
        from tools.memory import semantic_search_conversation

        # 第一次搜索（傳入 stream_callback，讓它輸出結果細節）
        direct_result = await semantic_search_conversation(
            user_id, query, n_results=n_results, keywords=None, agent_config=agent_config,
            stream_callback=_t
        )
        if "沒有找到" not in direct_result and "搜索出錯" not in direct_result:
            # 有結果，直接返回（思考已由 semantic_search_conversation 輸出）
            return f"\n【相關歷史對話（語義搜索）】\n{direct_result}\n\n"

        # 直接搜索無結果，嘗試聯想詞
        await _t(f"⚠️ 直接搜索未找到，嘗試聯想詞擴充...\n")
        core_keywords = await extract_keywords_from_sentence(query, agent_config) or [query]
        await _t(f"📌 核心關鍵詞：{', '.join(core_keywords)}\n")

        all_keywords = set()
        for kw in core_keywords:
            assoc_words = await _generate_associations(kw, count=ASSOC_COUNT, context="", agent_config=agent_config)
            all_keywords.update([kw] + assoc_words)
        final_keywords = list(all_keywords)[:KEYWORD_LIMIT]   # 取前 KEYWORD_LIMIT 個

        keyword_str = "、".join(final_keywords[:10]) + ("…" if len(final_keywords) > 10 else "")
        await _t(f"💡 最終搜索詞：{keyword_str}\n")

        # 第二次搜索（帶聯想詞，同樣傳入 stream_callback）
        result = await semantic_search_conversation(
            user_id, query, n_results=n_results, keywords=final_keywords, agent_config=agent_config,
            stream_callback=_t
        )
        if "沒有找到" in result or "搜索出錯" in result:
            await _t(f"⚠️ {result}\n")
            return ""
        # 有結果，返回（思考已由 semantic_search_conversation 輸出）
        return f"\n【可能有關歷史對話（語義搜索）】\n{result}\n\n"
    except Exception as e:
        logging.warning(f"自動語義搜索流程出錯: {e}")
        await _t(f"⚠️ 搜索過程出現錯誤，跳過語義搜索: {str(e)}\n")
        return ""











# 對話歷史數據庫（永久存儲）
# 所有進程（Web/TG）共享對話歷史，並供給 AI 構建 prompt
HISTORY_DB_PATH = os.path.expanduser(f"~/.{MOKAGI_home}/.memory/conversation_history.db")

def _init_history_db():
    """創建對話歷史表，啟用 WAL 模式提高併發"""
    with closing(sqlite3.connect(HISTORY_DB_PATH, timeout=10.0)) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout = 5000')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS conversation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_key TEXT NOT NULL,
                role TEXT NOT NULL,       -- 'user' or 'assistant'
                content TEXT,
                timestamp REAL NOT NULL
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_user_key ON conversation_history (user_key)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON conversation_history (timestamp)')
        # ===== 新增：對話摘要與關鍵字欄位（LLM 生成）=====
        try:
            conn.execute('ALTER TABLE conversation_history ADD COLUMN summary TEXT')
        except sqlite3.OperationalError:
            pass  # 欄位已存在
        try:
            conn.execute('ALTER TABLE conversation_history ADD COLUMN keywords TEXT')
        except sqlite3.OperationalError:
            pass
        # ===== 結束 =====
        # 新增：FTS5 全文搜索虛擬表
        conn.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS conversation_fts USING fts5(
                content,
                tokenize = "unicode61"
            )
        ''')
        conn.commit()

# 在 mokagi.py 中，約第 100 行附近（在 MOK_MODEL_NAME 等變數定義之後）
_init_history_db()
_pending_task = {}
# ===== 啟動速度優化：程式碼索引延遲 5 秒重建 =====
# 每次重啟都重建，確保程式碼修改後能被正確索引
# 延遲 5 秒避免與啟動競爭 CPU
try:
    import threading
    def _init_code_index_async():
        try:
            import time
            time.sleep(5)  # 延遲 5 秒
            # 延遲導入，避免循環依賴
            from tools.code_index import rebuild_index
            result = rebuild_index()
            if result.startswith("✅"):
                logging.info(f"[code_index] {result}")
            else:
                logging.warning(f"[code_index] {result}")
        except ImportError:
            logging.info("[code_index] code_index.py 未安裝，跳過程式碼索引")
        except Exception as e:
            logging.error(f"[code_index] 初始化失敗: {e}")
    
    # 後臺執行，不阻塞啟動
    threading.Thread(target=_init_code_index_async, daemon=True).start()
    logging.info("[code_index] 後臺索引已啟動（延遲 5 秒重建）")
except Exception as e:
    logging.warning(f"[code_index] 啟動失敗: {e}")
# ===== 結束 =====


user_histories: Dict[str, List[Dict]] = defaultdict(list)  # {user_id: [{"user":..., "assistant":...}]}

def get_user_history(user_id: str, limit: int = None, agent_name: str = None) -> List[Dict]:
    """
    從數據庫讀取對話歷史，返回格式 [{"user": "...", "assistant": "..."}, ...]
    若 limit 為 None 則返回全部（按時間正序），否則返回最近 limit 輪。
    """
    unique_id = _get_unique_user_id(user_id, agent_name)
    _init_history_db()
    with closing(sqlite3.connect(HISTORY_DB_PATH, timeout=10.0)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            'SELECT role, content FROM conversation_history WHERE user_key = ? ORDER BY id ASC',
            (unique_id,)
        ).fetchall()
    
    pairs = []
    i = 0
    while i < len(rows):
        if rows[i]['role'] == 'user' and i+1 < len(rows) and rows[i+1]['role'] == 'assistant':
            pairs.append({
                'user': rows[i]['content'],
                'assistant': rows[i+1]['content']
            })
            i += 2
        else:
            i += 1
    
    if limit is not None and limit > 0:
        pairs = pairs[-limit:]
    return pairs




# ===== 新增：LLM 生成對話摘要與關鍵字 =====
async def _generate_conversation_summary(user_msg: str, assistant_reply: str, agent_config: Dict = None) -> tuple:
    """
    用輕量 LLM 生成對話摘要與關鍵字。
    返回 (summary: str, keywords: str)，失敗則返回 (None, None)
    """
    if agent_config is None:
        agent_config = _agent_config
    owner = agent_config.get("MOK_ADMIN_NAME", "用戶")
    agent_name = agent_config.get("MOK_AGENT_NAME", "助手")
    
    prompt = f"""用繁體中文，極簡總結這段對話的核心主題（≤25字），並給出2~4個關鍵詞（逗號分隔），方便llm日後看主題、關鍵詞找到有用的信息。

{owner}: {user_msg[:300]}
{agent_name}: {assistant_reply[:300]}

只輸出兩行，第一行摘要，第二行關鍵詞，不要其他內容。"""

    try:
        token = agent_config.get("MOK_MODEL_token", "")
        if not token:
            logging.warning("[摘要生成] MOK_MODEL_token 為空，無法呼叫 LLM 生成摘要")
            return None, None

        result = await call_llm(
            prompt=prompt,
            user_id="system",
            stream=False,
            temperature=0.3,
            agent_config=agent_config,
            include_soul=False,
            num_predict=80
        )
        text = result if isinstance(result, str) else result.get("content", "")
        lines = [l.strip() for l in text.strip().split('\n') if l.strip()]
        summary = lines[0] if len(lines) >= 1 else None
        keywords = lines[1] if len(lines) >= 2 else None
        if summary:
            logging.info(f"[摘要生成] 成功生成摘要：{summary[:30]}...")
        else:
            logging.warning(f"[摘要生成] LLM 回傳格式異常：{text[:100]}")
        return summary, keywords
    except Exception as e:
        logging.warning(f"[摘要生成] 失敗: {type(e).__name__}: {str(e)}")
        return None, None
# ===== 結束 =====




async def add_to_history(user_id: str, user_msg: str, assistant_reply: str, agent_config: Dict = None):
    """將一輪對話存入數據庫（永久保存）"""
    if agent_config is None:
        agent_config = _agent_config
    owner = agent_config.get("MOK_ADMIN_NAME", "用戶")
    agent_name = agent_config.get("MOK_AGENT_NAME", "助手")
    unique_id = _get_unique_user_id(user_id, agent_name)
    _init_history_db()
    now = time.time()
    try:
        with closing(sqlite3.connect(HISTORY_DB_PATH, timeout=10.0)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO conversation_history (user_key, role, content, timestamp) VALUES (?, ?, ?, ?)',
                (unique_id, 'user', user_msg, now)
            )
            user_rowid = cursor.lastrowid
            cursor.execute(
                'INSERT INTO conversation_history (user_key, role, content, timestamp) VALUES (?, ?, ?, ?)',
                (unique_id, 'assistant', assistant_reply, now + 0.001)
            )
            assistant_rowid = cursor.lastrowid

            full_text = f"{owner}: {user_msg}\n{agent_name}: {assistant_reply}"
            try:
                conn.execute('INSERT OR REPLACE INTO conversation_fts (rowid, content) VALUES (?, ?)', (user_rowid, full_text))
            except Exception as e:
                logging.error(f"FTS5 索引插入失敗: {e}, rowid={user_rowid}, text={full_text[:100]}")

            # 異步生成摘要（已經是非同步，但此處無法等待，可改為背景任務）
            try:
                summary, keywords = await _generate_conversation_summary(user_msg, assistant_reply, agent_config)
                if summary:
                    conn.execute(
                        'UPDATE conversation_history SET summary = ?, keywords = ? WHERE id = ?',
                        (summary, keywords, user_rowid)
                    )
            except Exception as e:
                logging.warning(f"更新對話摘要失敗: {e}")
            conn.commit()
            return user_rowid
    except Exception as e:
        logging.error(f"儲存對話歷史失敗: {e}", exc_info=True)
        raise  # 重新拋出，讓上層感知
        # ===== 結束 =====






        

def clear_history(user_id: str, agent_name: str = None):
    """清除指定使用者的所有對話歷史"""
    unique_id = _get_unique_user_id(user_id, agent_name)
    _init_history_db()
    with closing(sqlite3.connect(HISTORY_DB_PATH, timeout=10.0)) as conn:
        conn.execute('DELETE FROM conversation_history WHERE user_key = ?', (unique_id,))
        conn.commit()

def get_all_conversation_summary(user_id: str, agent_config: Dict = None):
    _init_history_db()
    if agent_config is None:
        agent_config = _agent_config
    owner = agent_config.get("MOK_ADMIN_NAME", "用戶")
    agent_name = agent_config.get("MOK_AGENT_NAME", "助手")
    unique_id = _get_unique_user_id(user_id, agent_name)
    with closing(sqlite3.connect(HISTORY_DB_PATH, timeout=10.0)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, role, content, summary, keywords FROM conversation_history WHERE user_key = ? ORDER BY id ASC",
            (unique_id,)
        ).fetchall()
        # ===== 新增：轉為 dict 列表 =====
        rows = [dict(row) for row in rows]
        # =============================
    pairs_with_id = []
    i = 0
    while i < len(rows):
        if rows[i]['role'] == 'user' and i+1 < len(rows) and rows[i+1]['role'] == 'assistant':
            pairs_with_id.append({
                'user_rowid': rows[i]['id'],
                'user': rows[i]['content'],
                'assistant': rows[i+1]['content'],
                'summary': rows[i].get('summary'),      # 新增
                'keywords': rows[i].get('keywords')      # 新增
            })
            i += 2
        else:
            i += 1
    if not pairs_with_id:
        return "沒有找到任何對話記錄。"
    lines = []
    for idx, pair in enumerate(pairs_with_id, 1):
        user_preview = pair['user'][:80].replace('\n', ' ')
        assistant_preview = pair['assistant'][:80].replace('\n', ' ')
        if len(pair['user']) > 80:
            user_preview += "..."
        if len(pair['assistant']) > 80:
            assistant_preview += "..."
        lines.append(f"【{idx}】 (ID:{pair['user_rowid']})\n{owner}: {user_preview}\n{agent_name}: {assistant_preview}\n---")
    return "\n".join(lines)


def get_recent_conversation_summary(user_id: str, limit: int = MAX_HISTORY_ROUNDS, agent_config: Dict = None) -> str:
    _init_history_db()
    if agent_config is None:
        agent_config = _agent_config
    owner = agent_config.get("MOK_ADMIN_NAME", "用戶")
    agent_name = agent_config.get("MOK_AGENT_NAME", "助手")
    unique_id = _get_unique_user_id(user_id, agent_name)
    with closing(sqlite3.connect(HISTORY_DB_PATH, timeout=10.0)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, role, content, summary, keywords FROM conversation_history WHERE user_key = ? ORDER BY id ASC",
            (unique_id,)
        ).fetchall()
        # ===== 新增：轉為 dict 列表 =====
        rows = [dict(row) for row in rows]
        # =============================
    pairs_with_id = []
    i = 0
    while i < len(rows):
        if rows[i]['role'] == 'user' and i+1 < len(rows) and rows[i+1]['role'] == 'assistant':
            pairs_with_id.append({
                'user_rowid': rows[i]['id'],
                'user': rows[i]['content'],
                'assistant': rows[i+1]['content'],
                'summary': rows[i].get('summary'),      # 新增
                'keywords': rows[i].get('keywords')      # 新增
            })
            i += 2
        else:
            i += 1
    pairs_with_id = pairs_with_id[-limit:]
    lines = []
    for idx, pair in enumerate(pairs_with_id, 1):
        # ===== 修改：優先使用 LLM 生成的摘要 =====
        summary = pair.get('summary')
        keywords = pair.get('keywords')
        if summary:
            kw_str = f" 🔑{keywords}" if keywords else ""
            lines.append(f"輪次{idx} (ID:{pair['user_rowid']}): 📌{summary}{kw_str}")
        else:
            # fallback: 原有截取方式
            user_preview = pair['user'][:80].replace('\n', ' ')
            assistant_preview = pair['assistant'][:80].replace('\n', ' ')
            if len(pair['user']) > 80:
                user_preview += "..."
            if len(pair['assistant']) > 80:
                assistant_preview += "..."
            lines.append(f"輪次{idx} (ID:{pair['user_rowid']}): {owner}: {user_preview}\n   {agent_name}: {assistant_preview}")
        # ===== 結束 =====
    return "\n".join(lines)




# ========== 聊天記錄持久化（供 Web 前端關閉後保留歷史）==========
async def save_conversation_message(agent_name: str, role: str, content: str, think_content: str = None):
    """將消息保存到 Web 前端使用的 chat_history 表中（由 mok_web 共用）"""
    # 避免循環導入，延遲導入 mok_web 的 DB 函數？不，直接在 mokagi 中實現 SQLite 操作
    db_path = os.path.expanduser(f"~/.{MOKAGI_home}/.memory/chat_history.db")
    with closing(sqlite3.connect(db_path)) as conn:
        conn.execute(
            'INSERT INTO chat_history (agent, role, content, think_content, timestamp) VALUES (?, ?, ?, ?, ?)',
            (agent_name, role, content, think_content, time.time())
        )
        conn.commit()














'''
                                                                                     .              
                         +#                                   #         .=     @+    %              
                         :@                                   -*   .....-@    -* #  :*:::%.         
          .              .@                                 ..:-.#=      #    #  -+ *    #          
         -=              .@                                              #   -= .# .%:::=+          
         +=              .@                                     =-       #   - #:       +:          
        -%#==   +-.-+    .@  .+*-   ==.:*   =@-.++@+               :    .#     *       .%-#=        
         #-    #-   -#   .@   =    ++   -*   %*:  .@            :  #=:::=#     * -..  *#  .         
         #-   .%     @-  .@  =     @     @   #=    @.       .:::-- #     =   ::%==- : =*  #.        
         #-   =*     #*  .@.*%    .@.   -@   #=    @:              #           * =: * =+.=.         
         #-   +*     **  .@+ %.   :%.        #=    @:       =+::** #         + * #  . +-+           
         #-   =#     #+  .@  -%   .@         #=    @:       =-  :- #         --*.=   -*-:-          
         #-    @     @:  .@   %-   @:        #=    @:       =-  :- #      .  :-*:  +* =- *:         
         *=    #-   -#   .@   -@   +%    :   #=    @:       =-  :- #      -    *-:.+  =-  %=        
          #+=   -=---    =*=   ==:  :##*=   -++:  -*=.      =*::*- #+----+#  #%=      *-            
                                                            -.      -----:.  -       =%             

'''











# token 記錄函數

# token 統計數據庫路徑
# memory/chat_history.db 用於前端（網頁）展示聊天記錄，不參與 AI 的 prompt 構建，只是供{owner}瀏覽歷史
TOKEN_DB_PATH = os.path.expanduser(f"~/.{MOKAGI_home}/.memory/chat_history.db")

def _ensure_token_table():
    """確保 token_usage 表存在"""
    with closing(sqlite3.connect(TOKEN_DB_PATH)) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                agent_name TEXT,
                model_name TEXT,
                conversation_id TEXT,
                workflow_id TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                timestamp REAL,
                extra TEXT
            )
        ''')
        conn.commit()

def log_token_usage(
    user_id: str,
    agent_name: str,
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    conversation_id: str = None,
    workflow_id: str = None,
    extra: dict = None
):
    """記錄單次 LLM 調用的 token 用量"""
    _ensure_token_table()
    with closing(sqlite3.connect(TOKEN_DB_PATH)) as conn:
        conn.execute(
            '''INSERT INTO token_usage 
               (user_id, agent_name, model_name, conversation_id, workflow_id,
                prompt_tokens, completion_tokens, total_tokens, timestamp, extra)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (user_id, agent_name, model_name, conversation_id, workflow_id,
             prompt_tokens, completion_tokens, total_tokens, time.time(),
             json.dumps(extra, ensure_ascii=False) if extra else None)
        )
        conn.commit()































'''
                                 -+     :+:                .==    .-+                               
                                -*@:   -+@=               .-%#    -%@                               
                                 :@.     @=                 ##     *@                               
                                 :@.     @=                 ##     *@                               
                                 :@.     @=                 ##     *@                               
                                 :@.     @=                 ##     *@                               
           .++==      -==*=      :@.     @=                 ##     *@     :+  :*#+   -*#+           
          =#   :%    #-   #*     :@.     @=                 ##     *@    +%@.+=.=@# *=.-@*          
         =@     @+  =@    :@     :@.     @=                 ##     *@     -@*    +@+    =@          
         @=     ::  .=    .@.    :@.     @=                 ##     *@     -@.    :@.    :@.         
        -@.               -@.    :@.     @=                 ##     *@     -@.    :@.    :@.         
        +@             :=-=@.    :@.     @=                 ##     *@     -@.    :@.    :@.         
        +@           -#.  .@.    :@.     @=                 ##     *@     -@.    :@.    :@.         
        =@:         =@    .@.    :@.     @=                 ##     *@     -@.    :@.    :@.         
        .@*         %*    .@.    :@.     @=                 ##     *@     -@.    :@.    :@.         
         *@:     .  %#    +@.    :@.     @=                 ##     *@     -@.    :@.    :@.         
          #@=  .+   +@= .+ @=    =@-    -@*                 %%     #@.    +@-    =@-    =@-         
           -*%#=     =##=  :#+  =====  =====              -====: .=====  ====-  ====-  =====        
                                             *%%%%%%%%%%%:                                          

'''



# 全局 OpenAI 客戶端（單例）
_openai_client = None

def _get_openai_client(api_key: str, base_url: str):
    global _openai_client
    if _openai_client is None:
        _openai_client = openai.AsyncOpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers={
                "HTTP-Referer": "https://github.com/MOK2026/MOKAGI",
                "X-Title": "MOK AGI"
            }
        )
    return _openai_client



# ----------------------------------------------------------------------
# call_llm 統一的 LLM 調用接口（支持流式與非流式，支持工具定義嵌入）
# ----------------------------------------------------------------------
async def call_llm(
    prompt: str = "",
    user_id: str = "",
    system_prompt: str = "",
    stream: bool = False,
    tools_def: Optional[List[dict]] = None,  # 新增：直接傳入工具定義列表（每個 dict 包含 name、description、parameters 等）
    messages: Optional[List[dict]] = None,   # 新增：可直接傳入完整消息列表
    auto_execute_tools: bool = False,        # 新增：是否自動執行工具調用並返回自然化結果（原默認行為為 True，但為了靈活改為 False）
    conversation_id: str = None,   # 新增：用於關聯單次對話的多輪調用
    workflow_id: str = None,        # 新增：用於關聯工作流的多步調用
    agent_config: Optional[Dict] = None,    # agent配置
    _test_mode_skip_confirm: bool = False,   # 內部參數，用於恢復時跳過確認
    include_soul: bool = False,          # 預設關閉，由前端完全控制上下文
    context_files: Optional[List[str]] = None,  # 🔧 前端控制：指定 soul 文件（僅 include_soul=True 時生效）
    **override_options
) -> Union[str, AsyncGenerator[dict, None]]:
    """
    統一的 LLM 調用接口。
    - 如果提供了 messages，則直接使用（此時 prompt/system_prompt 被忽略）。
    - 否則使用 prompt/system_prompt 構建消息。
    - 如果 auto_execute_tools 為 True，遇到工具調用時自動執行並返回自然化結果（兼容舊行為）。
    - 如果為 False，遇到工具調用時返回一個包含 tool_calls 的 dict。
    ---
    - 如果存在 MOK_MODEL_token 且非空，則使用 OpenAI 兼容
    - 否則使用 Ollama
    """

    print(f"""========== [call_llm 統一的 LLM 調用接口] ==========
========== [prompt] ==========
{prompt}
========== [system_prompt] ==========
{system_prompt}
""")

    if agent_config is None:
        agent_config = _agent_config   # 向後兼容
    agent_name = agent_config.get("MOK_AGENT_NAME", "助手")
    owner = agent_config.get("MOK_ADMIN_NAME", "用戶")

    # 獲取當前模型配置（從傳入的 agent_config 讀取）
    token = agent_config.get("MOK_MODEL_token", "")
    use_openai_api = bool(token)
    model_name = agent_config.get("MOK_MODEL_NAME", "")
    api_url = agent_config.get("MOK_MODEL_url", "")
    

    # 如果 include_soul 為 True，則自動獲取靈魂內容併合併到 system_prompt
    if include_soul and agent_config:
        soul_content = get_system_context(
            agent_config.get("MOK_AGENT_NAME", "助手"),
            agent_config.get("MOK_ADMIN_NAME", "用戶"),
            int(agent_config.get("MOK_ADMIN_TIME_ZONE", 0)),
            context_files=context_files
        )
        if soul_content:
            if system_prompt:
                system_prompt = soul_content + "\n\n" + system_prompt
            else:
                system_prompt = soul_content



    # 構建消息（OpenAI 格式）
    if messages is None:
        # 原有邏輯：構建 messages
        msgs = []
        if system_prompt:
            msgs.append({"role": "system", "content": system_prompt})
        msgs.append({"role": "user", "content": prompt})
    else:
        msgs = messages
    
    # 通用參數
    temperature = override_options.get("temperature", float(agent_config.get("MOK_temperature", 0.8)))
    max_tokens = override_options.get("num_predict", int(agent_config.get("MOK_num_predict", 8192)))
    


    # ---- 測試模式處理 ----
    test_mode = agent_config.get("MOK_TEST_MODE", "0") == "1"
    if test_mode and not _test_mode_skip_confirm:
        # 生成唯一 context_id
        import uuid
        context_id = f"test_{uuid.uuid4().hex[:8]}"
        # 儲存完整 messages 和調用參數
        _pending_llm_confirm[context_id] = {
            "messages": messages,
            "tools_def": tools_def,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "agent_config": agent_config,
            "timestamp": time.time(),
            "user_id": user_id,
            "conversation_id": conversation_id,
            "workflow_id": workflow_id,
            "stream": stream,
        }
        # 構建上下文預覽（用於顯示給用戶）
        preview = f"🧪 **測試模式：即將發送給 LLM 的上下文**\n"
        preview += f"🔑 確認碼：`{context_id}`\n"
        preview += f"📊 預估 Token 數：約 {len(json.dumps(messages)) // 4}\n"
        preview += "--- 上下文內容 ---\n"
        for msg in messages:
            role = msg.get("role", "unknown")
            content = msg.get("content", "")
            # 截斷過長內容，但保留關鍵部分
            if len(content) > 300:
                content = content[:300] + "..."
            preview += f"**{role}**: {content}\n"
        preview += "\n請確認是否繼續？\n"
        preview += f"✅ 輸入 `/confirm {context_id}` 繼續執行\n"
        preview += f"❌ 輸入 `/cancel {context_id}` 取消本次調用"
        # 返回特殊標記，由上層處理
        return f"__NEED_CONFIRM__:{context_id}:{preview}"



    if use_openai_api:
        # 從配置中獲取當前模型的 API 地址（而不是使用全局 OLLAMA_API）
        current_api = api_url
        if not current_api:
            raise ValueError(f"{current_api} 目前型號未設定")
        client = _get_openai_client(token, current_api)
        

        if stream:
            async def stream_gen():
                try:
                    response = await client.chat.completions.create(
                        model=model_name,
                        messages=msgs,
                        stream=True,
                        tools=tools_def,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    # 用於拼接 tool_calls
                    tool_calls_chunks = {}  # index -> {id, name, arguments}
                    async for chunk in response:
                        if not chunk.choices:
                            continue
                        delta = chunk.choices[0].delta
                        # 處理思考內容（reasoning）
                        if hasattr(delta, 'reasoning_content') and delta.reasoning_content:
                            yield {"type": "think", "content": delta.reasoning_content}
                        # 處理普通回覆內容
                        if delta.content:
                            yield {"type": "reply", "content": delta.content}
                        # 處理 tool_calls（流式需要拼接）
                        if delta.tool_calls:
                            for tc in delta.tool_calls:
                                idx = tc.index
                                if idx not in tool_calls_chunks:
                                    tool_calls_chunks[idx] = {
                                        "id": tc.id,
                                        "name": "",
                                        "arguments": ""
                                    }
                                if tc.function.name:
                                    tool_calls_chunks[idx]["name"] = tc.function.name
                                if tc.function.arguments:
                                    tool_calls_chunks[idx]["arguments"] += tc.function.arguments
                    # 流結束後，如果有完整的 tool_calls，發送一個特殊事件
                    if tool_calls_chunks:
                        tool_calls_list = []
                        for idx, tc in tool_calls_chunks.items():
                            try:
                                args = json.loads(tc["arguments"]) if tc["arguments"] else {}
                            except:
                                args = {}
                            tool_calls_list.append({
                                "id": tc["id"],
                                "name": tc["name"],
                                "arguments": args
                            })
                        yield {"type": "tool_calls", "calls": tool_calls_list}
                except Exception as e:
                    logging.exception("OpenAI 流式調用失敗")
                    yield {"type": "reply", "content": f"❌ 生成失敗: {str(e)}"}
            return stream_gen()


        else:
            # 非流式（增加重試）
            max_retries = 3
            last_exception = None
            for attempt in range(max_retries):
                try:
                    response = await client.chat.completions.create(
                        model=model_name,
                        messages=msgs,
                        stream=False,
                        tools=tools_def,
                        temperature=temperature,
                        max_tokens=max_tokens,
                    )
                    message = response.choices[0].message

                    # ========== 記錄 token 用量 ==========
                    if hasattr(response, 'usage') and response.usage:
                        usage = response.usage
                        prompt_tokens = usage.prompt_tokens
                        completion_tokens = usage.completion_tokens
                        total_tokens = usage.total_tokens
                        log_token_usage(
                            user_id=user_id,
                            agent_name=agent_config.get("MOK_AGENT_NAME", "unknown"),
                            model_name=model_name,
                            prompt_tokens=prompt_tokens,
                            completion_tokens=completion_tokens,
                            total_tokens=total_tokens,
                            conversation_id=conversation_id,
                            workflow_id=workflow_id,
                            extra={"purpose": "openai_api"}
                        )
                    # ===================================

                    if message.tool_calls:
                        if auto_execute_tools:
                            results = []
                            for tool_call in message.tool_calls:
                                tool_name = tool_call.function.name
                                tool_args = json.loads(tool_call.function.arguments)

                                handler = find_tool_handler(tool_name)
                                if handler:
                                    raw_result = await safe_autofix_retry(
                                        action_func=handler,
                                        action_args=(),
                                        action_kwargs={"args": tool_args, "chat_id": user_id, "agent_config": agent_config},
                                        error_info_builder=lambda e, kwargs: {
                                            "tool_name": tool_name,
                                            "original_args": json.dumps(kwargs.get("args", {}), ensure_ascii=False),
                                            "error": f"{type(e).__name__}: {str(e)}",
                                        },
                                        autofix_extra_args={"user_id": user_id, "agent_config": agent_config}
                                    )

                                    natural = await naturalize_tool_result("", tool_name, raw_result, agent_config=agent_config)
                                    results.append(natural)
                                else:
                                    results.append(f"❌ 未找到工具: {tool_name}")
                            return "\n\n".join(results)
                        else:
                            # 返回原始工具調用信息，供調用方循環處理
                            return {
                                "type": "tool_calls",
                                "calls": [
                                    {
                                        "id": tool_call.id,
                                        "name": tool_call.function.name,
                                        "arguments": json.loads(tool_call.function.arguments)
                                    }
                                    for tool_call in message.tool_calls
                                ]
                            }
                    else:
                        content = message.content or ""
                        reasoning = getattr(message, 'reasoning_content', None) or getattr(message, 'reasoning', None)
                        if not content:
                            print(f"警告: OpenAI 返回空內容，完整響應: {response}")
                        if reasoning:
                            return {"content": content, "reasoning": reasoning}
                        else:
                            return content
                except (httpx.TimeoutException, openai.APITimeoutError, openai.APIError, Exception) as e:
                    # ---- 使用 autofix_run 處理 ----
                    if attempt == max_retries - 1:
                        # 最後一次失敗，調用 autofix_run 進行深度修復
                        from autofix2 import autofix_run
                        # 嘗試自動修復（將當前調用重新包裝）
                        try:
                            result = await autofix_run(
                                func=client.chat.completions.create,
                                func_args=(),
                                func_kwargs={
                                    "model": model_name,
                                    "messages": msgs,
                                    "tools": tools_def,
                                    "temperature": temperature,
                                    "max_tokens": max_tokens,
                                },
                                max_attempts=1,#3,
                                autofix_handler=find_tool_handler("admin"),  # 使用 admin 工具執行修復
                                autofix_extra_args={"agent_config": agent_config, "user_id": user_id},
                                llm_func=call_llm,  # 傳遞 LLM 函數用於分析
                                agent_config=agent_config,
                                user_id=user_id,
                                original_text=prompt
                            )
                            if result == "__ERROR_REPORTED__":
                                return await recovery.handle_llm_error(e, agent_config=agent_config)
                            else:
                                # 處理 result（可能是一個完整的響應對象，需進一步解析）
                                # 這裡需要根據返回類型適配，為簡化，我們直接返回 result
                                return result
                        except Exception as autofix_e:
                            logging.error(f"autofix_run 也失敗: {autofix_e}")
                            # 回退到原有處理
                            return await recovery.handle_llm_error(e, agent_config=agent_config)
                    else:
                        wait = 2 ** attempt
                        logging.warning(f"OpenAI 調用失敗（第 {attempt+1} 次），{wait} 秒後重試: {e}")
                        await asyncio.sleep(wait)
            # 如果循環結束未返回（理論上不會）
            return await recovery.handle_llm_error(last_exception, agent_config=agent_config)
    
    else:
        
        # 從 agent_config 讀取 Ollama 參數
        ollama_options = {
            "num_ctx": int(agent_config.get("MOK_num_ctx", 16384)),
            "num_predict": int(agent_config.get("MOK_num_predict", 8192)),
            "temperature": float(agent_config.get("MOK_temperature", 0.8)),
            "top_p": float(agent_config.get("MOK_top_p", 0.9)),
            "top_k": int(agent_config.get("MOK_top_k", 50)),
            "repeat_penalty": float(agent_config.get("MOK_repeat_penalty", 1.5)),
            "presence_penalty": float(agent_config.get("MOK_presence_penalty", 0.6)),
            "frequency_penalty": float(agent_config.get("MOK_frequency_penalty", 0.5)),
        }
        options = ollama_options.copy()

        options.update(override_options)
        full_prompt = (system_prompt + "\n\n" + prompt) if system_prompt else prompt
        if tools_def:
            print("\n========== [工具調用] ==========")
            tools_desc = json.dumps(tools_def, ensure_ascii=False, indent=2)
            full_prompt = (
                f"{agent_name}妳可以調用以下工具來服侍{owner}。\n"
                f"**僅當{owner}明確表達了要執行某個操作（例如「搜尋」、「記住」、「讀取檔案」、「切換模型」等）時，才輸出工具調用 JSON。**\n"
                f"對於普通的問候、閒聊或沒有明確操作意圖的訊息，請直接用自然語言回覆，絕對不要輸出 JSON。\n\n"
                "如果需要調用工具，請只輸出一個 JSON 對象，格式如下：\n"
                '{"name": "工具名稱", "arguments": {...}}\n'
                f"如果不需要調用工具，請直接以自然語言回答。\n\n"
                f"可用的工具：{tools_desc}\n\n" + full_prompt
            )
            # full_prompt = "妳是一個善於思考的助手。在回答任何問題之前，請先用自然語言寫出妳的推理過程，然後另起一行輸出最終答案。\n\n" + full_prompt

        payload = {
            "model": model_name,
            "prompt": full_prompt,
            "stream": stream,
            "options": options
        }

        if stream:
            # 流式生成
            async def stream_gen():
                async with httpx.AsyncClient(timeout=httpx.Timeout(_model_timeout, connect=10.0)) as client:
                    try:
                        async with client.stream("POST", api_url, json=payload) as resp:
                            resp.raise_for_status()
                            async for line in resp.aiter_lines():
                                if not line:
                                    continue
                                try:
                                    chunk = json.loads(line)
                                    if 'thinking' in chunk and chunk['thinking']:
                                        yield {"type": "think", "content": chunk['thinking']}
                                    if 'response' in chunk and chunk['response']:
                                        yield {"type": "reply", "content": chunk['response']}
                                    if chunk.get('done'):
                                        break
                                except json.JSONDecodeError:
                                    logging.warning(f"Invalid JSON line: {line[:100]}")
                                    continue
                    except Exception as e:
                        logging.exception("流式生成異常")
                        error_msg = await recovery.handle_llm_error(e)
                        yield {"type": "reply", "content": error_msg}
            return stream_gen()
        else:
            # Ollama 非流式調用 -> 改為流式收集，以便捕獲 thinking 內容
            async with httpx.AsyncClient(timeout=httpx.Timeout(_model_timeout, connect=10.0)) as client:
                try:
                    payload["stream"] = True   # 強制流式
                    final_response = ""
                    thinking_content = ""
                    async with client.stream("POST", api_url, json=payload) as resp:
                        resp.raise_for_status()
                        async for line in resp.aiter_lines():
                            if not line:
                                continue
                            try:
                                chunk = json.loads(line)
                                if 'thinking' in chunk and chunk['thinking']:
                                    thinking_content += chunk['thinking']
                                if 'response' in chunk and chunk['response']:
                                    final_response += chunk['response']
                                if chunk.get('done'):
                                    break
                            except json.JSONDecodeError:
                                continue
                    # 記錄 token（如果有）
                    # 返回字典，包含 response 和 thinking
                    return {
                        "content": final_response.strip(),
                        "reasoning": thinking_content if thinking_content else None
                    }
                except Exception as e:
                    logging.exception("Ollama 調用異常")
                    return await recovery.handle_llm_error(e, agent_config=agent_config)


































'''

                                                                               =                    
                                                            @#:              .#@#                   
                                                            @@@@@@@@@@@@@@@@@@@@@@                  
                                         **                 @@=               #@%.                  
                                        *@@@:               @@:               #@#                   
              =#########################@@@@@:              @@:               #@#                   
               .............*@@-.............               @@:               #@#                   
                            +@@                             @@*:::::::::::::::%@#                   
                            +@@                             @@#===============%@#                   
                            +@@                             @@:               #@#                   
                            +@@                             @@:               #@#                   
                            +@@                             @@:               #@#                   
                            +@@                             @@:               #@#                   
                            +@@                             @@#===============@@#                   
                            +@@                             @@*---------------%@#                   
                            +@@                             @@:               #@#                   
                            +@@                             @@:               #@#                   
                            +@@                             @@:               #@#                   
                            +@@                             @@-               #@#                   
                            +@@                             @@@%%%%%%%%%%%%%%%@@#                   
                            +@@                             @@+:::::::::::::::%@#                   
                            +@@                             @@:               #@#                   
                            +@@                             @@:               #@#                   
                            +@@                             @@:               #@#   .#              
                            +@@                             @@:               #@#   %@@-            
                            +@@                     ++++++++@@#+++++++++++++++@@@++%@@@@-           
                            +@@                     ::::::::::::::::::::::::::::::::::::            
                            +@@                                 :                                   
                            +@@                                *@%=       ==:                       
                            +@@             =                 #@@@*=       -%@#-                    
                            +@@            =@%:             .%@@#            :%@@*.                 
            ::::::::::::::::#@@-::::::::::=@@@@+           =@@#:               -%@@#:               
           :%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%%#          %@@=                   *@@@+              
                                                        =@%=                      =@@@*             
                                                      =%*:                         .%@@:            
                                                    =#=                              @@:            
                                                                                                    

'''











# 工具定義快取
_cached_tool_defs = None

# ----------------------------------------------------------------------
# 工具調用相關函數（複用 tool_handler，並擴展 Function Calling）
# ----------------------------------------------------------------------

def build_tool_definitions() -> List[dict]:
    global _cached_tool_defs
    if _cached_tool_defs is not None:
        return _cached_tool_defs
    schemas = []
    tools_dict = tool_handler.get_tools()
    for name, mod in tools_dict.items():
        if hasattr(mod, "PLUGIN_INFO"):
            # 原始工具
            if "tool_schema" in mod.PLUGIN_INFO:
                original = mod.PLUGIN_INFO["tool_schema"]
                schemas.append({"type": "function", "function": original})
            # 子工具（新增）
            if "sub_tools" in mod.PLUGIN_INFO:
                for sub in mod.PLUGIN_INFO["sub_tools"]:
                    sub_schema = {
                        "name": sub["name"],
                        "description": sub["description"],
                        "parameters": sub["parameters"]
                    }
                    schemas.append({"type": "function", "function": sub_schema})
    _cached_tool_defs = schemas
    return schemas





def extract_tool_call(response_text: str) -> Optional[dict]:
    """從 LLM 回覆中提取 JSON 格式的工具調用"""
    try:
        start = response_text.find('{')
        end = response_text.rfind('}') + 1
        if start == -1 or end == 0:
            return None
        json_str = response_text[start:end]
        data = json.loads(json_str)
        if "name" in data and "arguments" in data:
            # 增加：檢查工具名稱是否真實存在
            if find_tool_handler(data["name"]) is not None:
                return data
            else:
                logging.warning(f"檢測到不存在的工具名稱: {data['name']}，忽略調用")
                return None
    except:
        pass
    return None





def find_tool_handler(tool_name: str):
    """根據工具名稱（tool_schema.name 或 sub_tools 中的 name）找到對應的 handler 函數"""
    for mod in tool_handler.get_tools().values():
        if not hasattr(mod, "PLUGIN_INFO"):
            continue
        # 1. 檢查父工具本身
        schema = mod.PLUGIN_INFO.get("tool_schema", {})
        if schema.get("name") == tool_name:
            handler_name = mod.PLUGIN_INFO.get("handler")
            if handler_name:
                return getattr(mod, handler_name, None)
        # 2. 檢查子工具（sub_tools）
        sub_tools = mod.PLUGIN_INFO.get("sub_tools", [])
        for sub in sub_tools:
            if sub.get("name") == tool_name:
                # 子工具使用父工具的 handler
                handler_name = mod.PLUGIN_INFO.get("handler")
                if handler_name:
                    return getattr(mod, handler_name, None)
    return None



async def naturalize_tool_result(
    user_text: str,
    tool_name: str,
    raw_result: str,
    temp_msg_callback: Optional[Callable] = None,
    agent_config: Optional[Dict] = None
) -> str:
    """
    將工具返回的 JSON 結果通過自然化函數轉為口語句子。
    如果工具定義了 naturalize_func，則調用之；否則返回原始結果。
    """
    if agent_config is None:
        agent_config = _agent_config
    print(f"自然化工具結果: tool={tool_name}, raw_result={raw_result[:100]}...")
    # 查找工具模塊
    target_mod = None
    for mod in tool_handler.get_tools().values():
        if hasattr(mod, "PLUGIN_INFO"):
            schema = mod.PLUGIN_INFO.get("tool_schema", {})
            if schema.get("name") == tool_name:
                target_mod = mod
                break
    if target_mod and hasattr(target_mod, "PLUGIN_INFO"):
        func_name = target_mod.PLUGIN_INFO.get("naturalize_func")
        if func_name:
            naturalize_func = getattr(target_mod, func_name, None)
            if naturalize_func:
                try:
                    result = await naturalize_func(
                        user_text=user_text,
                        raw_result=raw_result,
                        ollama_api=agent_config.get("MOK_MODEL_url", "http://localhost:11434/v1"),
                        model_name=agent_config.get("MOK_MODEL_NAME", "minimax-m3:cloud"),
                        temp_msg=None,
                        context=None
                    )
                    return result
                except Exception as e:
                    logging.warning(f"自然化函數調用失敗: {e}")
                    return await recovery.naturalize_tool_result_fallback(user_text, tool_name, raw_result, agent_config=agent_config,include_soul=False)
    # 備選：簡單的 JSON 轉文本
    try:
        data = json.loads(raw_result)
        if isinstance(data, dict) and "error" in data:
            return f"❌ 錯誤: {data['error']}"
        if isinstance(data, dict) and "results" in data:
            items = data["results"][:3]
            lines = [f"{i+1}. {item.get('title', '無標題')}\n   {item.get('body', '')[:100]}" for i, item in enumerate(items)]
            return "\n\n".join(lines)
    except:
        pass
    if len(raw_result) > 1000:
        raw_result = raw_result[:1000] + "..."
    return raw_result




# ----------------------------------------------------------------------
# 工具調用相關函數
# 1.  / 直接命令處理
#（複用 tool_handler.process_message）
# ----------------------------------------------------------------------
async def handle_direct_command(user_text: str, user_id: str, agent_config: Optional[Dict] = None) -> Optional[str]:
    if not user_text.startswith('/'):
        return None
    if agent_config is None:
        agent_config = _agent_config
    ollama_api = agent_config.get("MOK_MODEL_url", "http://localhost:11434/v1")
    model_name = agent_config.get("MOK_MODEL_NAME", "minimax-m3:cloud")
    result = await tool_handler.process_message(
        user_text=user_text,
        chat_id=user_id,
        ollama_api=ollama_api,
        model_name=model_name,
        cmd_map=tool_handler.get_cmd_map(),
        tools=tool_handler.get_tools(),
        agent_config=agent_config
    )
    return result




def extract_tool_and_text(response_text: str):
    """
    從 LLM 回覆中提取自然語言文本和第一個工具調用 JSON。
    返回 (text, tool_dict) 或 (response_text, None)
    支持的格式：
      - 純文本
      - 文本 + {"name": "tool", "arguments": {...}}
      - 文本 + {"tool": "tool_name", "args": {...}}
      - 或者單獨 JSON
    """
    if not response_text:
        return response_text, None
    # 查找 JSON 起始位置
    start = response_text.find('{')
    if start == -1:
        return response_text, None
    # 找到匹配的結束位置（簡單處理：從 start 往後找到第一個完整的 JSON 對象）
    brace_count = 0
    end = -1
    for i in range(start, len(response_text)):
        ch = response_text[i]
        if ch == '{':
            brace_count += 1
        elif ch == '}':
            brace_count -= 1
            if brace_count == 0:
                end = i
                break
    if end == -1:
        return response_text, None
    json_str = response_text[start:end+1]
    try:
        data = json.loads(json_str)
    except:
        return response_text, None
    # 檢查是否是有效的工具調用
    tool_name = None
    tool_args = None
    if "name" in data and "arguments" in data:
        tool_name = data["name"]
        tool_args = data["arguments"]
    elif "tool" in data and ("args" in data or "arguments" in data):
        tool_name = data["tool"]
        tool_args = data.get("args") or data.get("arguments", {})
    else:
        return response_text, None
    # 剩餘文本（JSON 之前和之後）
    before = response_text[:start].strip()
    after = response_text[end+1:].strip()
    # 合併前後自然語言
    text = before + ("\n" + after if after else "")
    return text, {"name": tool_name, "arguments": tool_args}


















# ----------------------------------------------------------------------
# 輔助函數：重新加載工具（供適配器調用）
# ----------------------------------------------------------------------
def reload_tools():
    """重新加載 tools 目錄下的所有插件"""
    global _cached_tool_defs
    _cached_tool_defs = None
    tool_handler.load_tools()

# 啟動時加載工具
tool_handler.load_tools()



















































'''
                                                                               .:                   
                                           -@#.   +@*               ++          %#                  
                                           #@=    %@:                #@-        .@*                 
                             .+            @@    :@#                  @@.        %@      =          
                            .@@*          -@=    *@:                  -@:        =+     #@*         
           +++++++++#@#+++++++++.         #@     @#         #*           :=====*@#=========         
                    -@:                  .@-    *@#++++++++%@@%                %@@:                 
                    -@:                  *%     @+  %@            =:     :    *@#    .              
                    -@:                  @*    +%   #%            .@%   =    *@+     +#             
                    -@:                 *@%    @.   #%             .@%  +   #%:       *@-           
                    -@:                :@@*   *-    #%              =@ -: -%#:.::-=++==%@:          
                    -@:                %-%*  -+     #%     %*          #  @@@@@#+=:     @#          
                    -@:               =+ %*  +      #@****%@@*        -+  ==:           *@          
                    -@:              .*  %* .       #@                %.    :    :    :. .          
                    -@:              +   %*         #%               :%    =@=  +@=  -@=            
                    -@:                  %*         #%               #=    =@.  +@   -@             
                    -@:                  %*         #%              .@     =@.  +@   -@             
                    -@:                  %*         #%     +%       *#     =@   +@   -@             
                    -@:                  %*         #@++++*@@@.   .-@=     +@   +@   -@             
                    -@:                  %*         #@            -%@-     *@   +@   -@             
                    -@:                  %*         #%             .@-     %*   +@   -@             
                    -@:                  %*         #%              @*     @:   +@   -@   -         
                    -@:        *#        %*         #%              @%    +%    +@   -@   +         
         -----------*@*-------+@@%       %*         #%              @@    @.    +@   -@   #         
         .........................       %*         #%              @@   *-     +@   -@=.-@:        
                                         %*         #%              @@  +-      +%   :@@@@@=        
                                         =.         -:              =. -                            


'''

# ----------------------------------------------------------------------
# 多步工作流執行（複用 workflow 工具）0619
# ----------------------------------------------------------------------

# ---------- 掛起任務管理函數 ----------
def _get_pending_task_file(agent_name):
    return os.path.expanduser(f"~/.{MOKAGI_home}/agent/{agent_name}/_job.json")

def save_pending_task(user_id, messages, goal, max_iterations, iteration, agent_name, continue_code=None):
    unique_key = _get_unique_user_id(user_id, agent_name)
    if continue_code is None:
        continue_code = hashlib.md5(f"{user_id}_{time.time()}_{goal}".encode()).hexdigest()[:12]
    task = {
        "goal": goal,
        "messages": messages,
        "max_iterations": max_iterations,
        "iteration": iteration,
        "timestamp": time.time()
    }
    if unique_key not in _pending_task:
        _pending_task[unique_key] = {}
    _pending_task[unique_key][continue_code] = task
    # 寫入文件
    task_file = _get_pending_task_file(agent_name)
    os.makedirs(os.path.dirname(task_file), exist_ok=True)
    all_data = {}
    if os.path.exists(task_file):
        with open(task_file, 'r', encoding='utf-8') as f:
            try:
                all_data = json.load(f)
            except:
                all_data = {}
    if unique_key not in all_data:
        all_data[unique_key] = {}
    all_data[unique_key][continue_code] = task
    with open(task_file, 'w', encoding='utf-8') as f:
        json.dump(all_data, f, ensure_ascii=False, indent=2)
    return f"📌 已保存任務進度，繼續碼：`{continue_code}`\n繼續執行：`/continue {continue_code}`"

def load_pending_task(user_id, continue_code, agent_name):
    unique_key = _get_unique_user_id(user_id, agent_name)
    # 先從內存讀取
    if unique_key in _pending_task and continue_code in _pending_task[unique_key]:
        return _pending_task[unique_key][continue_code]
    task_file = _get_pending_task_file(agent_name)
    if not os.path.exists(task_file):
        return None
    with open(task_file, 'r', encoding='utf-8') as f:
        all_data = json.load(f)
    if unique_key in all_data and continue_code in all_data[unique_key]:
        task = all_data[unique_key][continue_code]
        if unique_key not in _pending_task:
            _pending_task[unique_key] = {}
        _pending_task[unique_key][continue_code] = task
        return task
    return None

def delete_pending_task(user_id, continue_code, agent_name):
    unique_key = _get_unique_user_id(user_id, agent_name)
    if unique_key in _pending_task and continue_code in _pending_task[unique_key]:
        del _pending_task[unique_key][continue_code]
        if not _pending_task[unique_key]:
            del _pending_task[unique_key]
    task_file = _get_pending_task_file(agent_name)
    if not os.path.exists(task_file):
        return
    with open(task_file, 'r', encoding='utf-8') as f:
        all_data = json.load(f)
    if unique_key in all_data and continue_code in all_data[unique_key]:
        del all_data[unique_key][continue_code]
        if not all_data[unique_key]:
            del all_data[unique_key]
        with open(task_file, 'w', encoding='utf-8') as f:
            json.dump(all_data, f, ensure_ascii=False, indent=2)




def extract_continue_command(text: str) -> Optional[str]:
    """
    檢查用戶輸入是否為 /continue 命令。
    返回 continue_code（字符串），若不是 /continue 命令則返回 None。
    
    使用示例：
        code = extract_continue_command("/continue a1b2c3d4e5f6")
        if code:
            task = load_pending_task(user_id, code, agent_name)
    """
    match = re.match(r'^/continue\s+([^\s]+)', text.strip())
    if match:
        return match.group(1)
    return None


def _ensure_tool_response_completeness(messages: list) -> list:
    """
    確保 messages 中的 tool_calls 和 tool 回應配對完整。
    1. 移除孤立的 tool 消息（前面沒有對應的 assistant tool_calls）
    2. 補齊缺失的 tool 回應
    3. 確保順序正確：assistant(tool_calls) → tool → tool → ...
    """
    fixed = []
    i = 0
    while i < len(messages):
        msg = messages[i]
        
        # 如果是 tool 消息，檢查前面是否有對應的 assistant
        if msg.get("role") == "tool":
            tool_call_id = msg.get("tool_call_id")
            # 向前查找最近的 assistant 消息
            found_assistant = False
            for j in range(i - 1, -1, -1):
                if messages[j].get("role") == "assistant":
                    # 檢查該 assistant 是否有 tool_calls
                    tool_calls = messages[j].get("tool_calls")
                    if tool_calls and any(tc.get("id") == tool_call_id for tc in tool_calls):
                        found_assistant = True
                    break
            if not found_assistant:
                # 孤立的 tool 消息 → 跳過
                print(f"[_pending_task 修復] 移除孤立的 tool 消息: {tool_call_id}")
                i += 1
                continue
        
        fixed.append(msg)
        i += 1
    
    # 第二遍：檢查 assistant 的 tool_calls 是否都有對應的 tool 回應
    result = []
    i = 0
    while i < len(fixed):
        msg = fixed[i]
        result.append(msg)
        
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            tool_call_ids = [tc["id"] for tc in msg["tool_calls"]]
            # 收集後續已存在的 tool 回應 ID
            existing_ids = set()
            j = i + 1
            while j < len(fixed) and fixed[j].get("role") == "tool":
                existing_ids.add(fixed[j].get("tool_call_id"))
                j += 1
            
            # 找出缺失的
            missing_ids = set(tool_call_ids) - existing_ids
            if missing_ids:
                print(f"[_pending_task 修復] 補齊缺失的 tool 回應: {missing_ids}")
                for missing_id in missing_ids:
                    result.append({
                        "role": "tool",
                        "tool_call_id": missing_id,
                        "content": "（工具執行結果因記錄不完整而省略，請重新執行所需工具）"
                    })
        i += 1
    
    return result







# ========== 經驗學習機制 ==========
EXPERIENCE_DB_PATH = os.path.expanduser(f"~/.{MOKAGI_home}/.memory/conversation_history.db")

def _init_experience_db():
    """初始化經驗記錄表與 FTS5 虛擬表"""
    with closing(sqlite3.connect(EXPERIENCE_DB_PATH)) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS experience_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_key TEXT,
                agent_name TEXT,
                goal TEXT,
                outcome TEXT,           -- 'success' or 'failure'
                tool_sequence TEXT,     -- JSON 格式的工具調用序列
                error_message TEXT,
                summary TEXT,
                keywords TEXT,
                timestamp REAL
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_exp_agent ON experience_log (agent_name)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_exp_outcome ON experience_log (outcome)')
        # FTS5 全文搜索
        conn.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS experience_fts USING fts5(
                goal,
                summary,
                keywords,
                content=experience_log
            )
        ''')
        # 觸發器：自動同步 FTS（簡化版本，FTS5 支援 content= 可直接查詢原表）
        # 但為了簡單，我們手動插入時同時插入 FTS，或使用外部內容表。
        # 這裡改用更可靠的傳統方式：經驗表自己維護全文索引，每次記錄時手動插入 FTS。
        conn.commit()

def log_experience(
    user_id: str,
    agent_name: str,
    goal: str,
    outcome: str,  # 'success' or 'failure'
    messages: list,
    error_message: str = None,
    agent_config: dict = None
) -> None:
    """
    記錄任務經驗。
    - 從 messages 中提取工具調用序列。
    - 使用 LLM 生成簡短摘要（如果可用）。
    """
    if agent_config is None:
        agent_config = _agent_config
    _init_experience_db()
    
    # 提取工具調用序列
    tool_calls = []
    for msg in messages:
        if msg.get("role") == "assistant" and msg.get("tool_calls"):
            for tc in msg["tool_calls"]:
                tool_calls.append({
                    "name": tc.get("function", {}).get("name", "unknown"),
                    "args": tc.get("function", {}).get("arguments", "{}")
                })
        elif msg.get("role") == "tool":
            # 也可以記錄工具返回的摘要（但避免過長）
            pass
    
    tool_sequence_json = json.dumps(tool_calls, ensure_ascii=False)
    
    # 嘗試生成摘要
    summary = None
    keywords = None
    try:
        # 使用輕量 LLM 生成摘要
        import asyncio
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        prompt = f"用繁體中文總結這個任務的經驗（含成功/失敗原因），不超過30字：\n目標：{goal}\n結果：{outcome}"
        result = loop.run_until_complete(call_llm(
            prompt=prompt,
            user_id=user_id,
            stream=False,
            temperature=0.3,
            agent_config=agent_config,
            include_soul=False,
            num_predict=80
        ))
        loop.close()
        if isinstance(result, dict):
            result = result.get("content", "")
        lines = result.strip().split('\n') if result else []
        summary = lines[0] if lines else None
        keywords = lines[1] if len(lines) > 1 else None
    except Exception as e:
        logging.warning(f"[經驗學習] 生成摘要失敗: {e}")
    
    unique_key = _get_unique_user_id(user_id, agent_name)
    now = time.time()
    
    with closing(sqlite3.connect(EXPERIENCE_DB_PATH)) as conn:
        cursor = conn.cursor()
        cursor.execute('''
            INSERT INTO experience_log 
            (user_key, agent_name, goal, outcome, tool_sequence, error_message, summary, keywords, timestamp)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ''', (unique_key, agent_name, goal, outcome, tool_sequence_json, error_message, summary, keywords, now))
        rowid = cursor.lastrowid
        
        # 更新 FTS5
        fts_content = f"{goal} {summary or ''} {keywords or ''}"
        conn.execute('INSERT OR REPLACE INTO experience_fts (rowid, goal, summary, keywords) VALUES (?, ?, ?, ?)',
                     (rowid, goal, summary or '', keywords or ''))
        conn.commit()
    
    logging.info(f"[經驗學習] 記錄經驗: {outcome}, goal={goal[:30]}...")

def recall_experience(
    user_id: str,
    query: str,
    agent_name: str,
    n_results: int = 3,
    outcome_filter: str = None
) -> str:
    """
    根據查詢檢索相關經驗（優先返回成功經驗）。
    返回格式化的文字摘要。
    """
    _init_experience_db()
    unique_key = _get_unique_user_id(user_id, agent_name)
    results = []

    with closing(sqlite3.connect(EXPERIENCE_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        sql = '''
            SELECT e.id, e.goal, e.outcome, e.tool_sequence, e.error_message, e.summary, e.keywords
            FROM experience_log e
            JOIN experience_fts f ON e.id = f.rowid
            WHERE e.user_key = ? AND e.agent_name = ? AND experience_fts MATCH ?
        '''
        params = [unique_key, agent_name, query]
        if outcome_filter:
            sql += ' AND e.outcome = ?'
            params.append(outcome_filter)
        sql += ' ORDER BY e.timestamp DESC LIMIT ?'
        params.append(n_results * 2)

        rows = conn.execute(sql, params).fetchall()
        success_rows = [r for r in rows if r['outcome'] == 'success']
        failure_rows = [r for r in rows if r['outcome'] == 'failure']
        selected = success_rows[:n_results]
        if len(selected) < n_results:
            selected += failure_rows[:n_results - len(selected)]

        for row in selected:
            tool_seq = json.loads(row['tool_sequence']) if row['tool_sequence'] else []
            tool_names = [t.get('name', '?') for t in tool_seq[:3]]
            tool_str = ' → '.join(tool_names) if tool_names else '無工具'
            status_icon = "✅" if row['outcome'] == 'success' else "❌"
            summary_text = row['summary'] or row['goal'][:40]
            results.append(f"{status_icon} {summary_text} (工具: {tool_str})")

    if not results:
        return ""
    return "【📚 相關經驗參考】\n" + "\n".join(results) + "\n"
# ----------------------------------------------------------------------
# 多步工作流執行（複用 workflow 工具）0619 end
# ----------------------------------------------------------------------




























































































'''

                     :                ::                 .                                          
          :#-       =*   .            %*                 %%                                         
           .@=  +   =+   @=      :.  :%.    *.            :#.                                       
            -+  :%  =+  -*       +*::::::::-@*              *             .=.          :%=          
                 ** =+  #        +=         @.              %             .@:..........:@#          
         .    :  .# =+ :.        +*::::::::-@.              @.            .@            @.          
         +%.  :     =+   -       +*        .@.             :@=            .@            @.          
          *# = .@===++===@#      +=         @.             +@#            .@            @.          
             + .@        @.      +#=========@.             @=#            .@            @.          
            -: .@        @.      ++         @.            :@ -=           .@            @.          
            #  .@:......:@.      +=         @.            #+  %           .@            @.          
           :+  .@.      .@.      +#--------=@.           .@   *=          .@            @.          
           #.  .@        @.      +=         %            #+   .@          .@            @.          
          :%   .@        @.        *. ::                -%     *#         .@            @.          
         :%*   .@========@.     .  @   ++    -          %       @+        .@            @.          
          =#   .@        @.     +  @    @     #-       #-       =@-       .@            @.          
          :@   .@        @.    .*  @    -   : .@:     +=         #@-      .@=----------=@.          
          :@.  .@        @.    %:  @        +  +#    =-           %@+     .@            @.          
          -@.  .@      -+@.   %+   @%######%@   =   -:             %@%.   .@            #           
          :%   .#       #*          :::::::.       -                *.                              

'''

# ----------------------------------------------------------------------
# 處理{owner}消息的統一入口。
# ----------------------------------------------------------------------
_pending_clarification = {}   # 存儲待澄清的會話 {user_id: {"original":..., "question":..., "timestamp":...}}

async def process_message(
    user_id: str,
    text: str,
    stream_callback: Optional[Callable[[dict], Awaitable[None]]] = None,
    agent_name: Optional[str] = None,          # 新增：明確指定 Agent 名稱
    agent_config: Optional[Dict] = None,        # 新增：直接傳入配置（若提供則跳過緩存）
    auto_mode: bool = False,   # 新增
    initial_prompt: Optional[str] = None,   # ✨ 允許外部呼叫者（例如 job_manager.py） 直接指定「LLM 應該看到的初始上下文」，而不是由 process_message 內部自動從歷史紀錄 + 記憶 + 語義搜索去拼湊。
    context_files: Optional[List[str]] = None,  # 🔧 前端控制：指定要載入的 soul 文件（如 ["agent.md","user.md"]）。None=全部, []=無
) -> Optional[str]:
    """
    處理{owner}消息的統一入口。

    :param user_id: {owner}唯一標識（字符串）
    :param text: {owner}輸入文本
    :param stream_callback: 異步回調，接收事件字典：
        - {"type": "think", "content": "..."}  思考過程
        - {"type": "reply", "content": "..."} 回覆片段（流式）
        - {"type": "done"}                    完成
        若不提供，則返回完整字符串。
    :return: 若 stream_callback 為 None，則返回完整回覆；否則返回 None。
    """

    from recovery import ask_clarification


    # 優先使用傳入的 agent_name，若未傳則從全局配置讀取（向後兼容）
    if agent_name is None:
        agent_name = _agent_config.get("MOK_AGENT_NAME", "default")
    
    # 從傳入的 agent_config 獲取信息（避免全局汙染）
    if agent_config is None:
        agent_config = await get_agent_config(agent_name)
    
    MOK_AGENT_ICON = agent_config.get("MOK_AGENT_ICON", "🌸")   # agent icon
    owner = agent_config.get("MOK_ADMIN_NAME", "用戶")              # 用戶名
    owner_time = agent_config.get("MOK_ADMIN_TIME_ZONE", 0)         # 用戶時區
    model_name = agent_config.get("MOK_MODEL_NAME", "minimax-m3:cloud")     # 現用模型名
    api_url = agent_config.get("MOK_MODEL_url", "http://localhost:11434/v1")
    token = agent_config.get("MOK_MODEL_token", "")
    max_history_rounds = int(agent_config.get("MOK_MAX_HISTORY_ROUNDS", 6)) # 加入 prompt的最多對話歷史
    max_tack_rounds = int(agent_config.get("MOK_max_tack_rounds", 3))
    memory_recall_count = int(agent_config.get("MOK_MEMORY_RECALL_COUNT", 3))
    max_iterations = int(agent_config.get("MOK_max_iterations", 10))

    from logger import WorkflowLogger
    # 為本次會話創建一個日誌記錄器（不使用 goal，因為是普通對話）


    session_logger = WorkflowLogger(user_id, goal=text, agent_name=agent_name, title=None)
    print(f"創建日誌用 agent_name: {agent_name}")
    session_logger.log_info(text)






# 檢查是否有待澄清的對話（來自上次主動提問）
    # 檢查待澄清回覆
    pending = _pending_clarification.pop(user_id, None)
    if pending and (time.time() - pending["timestamp"]) < 300:
        from recovery import merge_and_reunderstand
        result = await merge_and_reunderstand(user_id, pending["original"], pending["question"], text, agent_config=agent_config)
        if result:
            cmd, args = result
            if cmd == "chat":
                # 當做普通聊天處理，繼續走原流程
                pass
            elif cmd.startswith("/"):
                # 直接執行命令並返回結果
                direct = await handle_direct_command(f"{cmd} {args}".strip(), user_id)
                if direct:
                    if stream_callback:
                        await stream_callback({"type": "reply", "content": direct + get_model_tag(model_name)})
                        await stream_callback({"type": "done"})
                    else:
                        return direct + get_model_tag(model_name)
                    return
            else:
                # 未知命令，走普通聊天
                pass
        # 清除 pending 避免重複處理
        _pending_clarification.pop(user_id, None)


    # 包裝 stream_callback，同時寫入日誌
    # 統一發送事件 + 日誌記錄
    original_callback = stream_callback
    pending_think = ""
    full_reply_collected = ""  # 非流式模式收集回覆




    # ===== 新增：防止 done 事件重複發送的標誌 =====
    _done_sent = False

    async def _send(event: dict):
        nonlocal pending_think, full_reply_collected, _done_sent
        
        # ===== 🛡️ 防止 done 事件重複發送 =====
        if event.get("type") == "done":
            if _done_sent:
                # 已發送過 done，忽略後續
                return
            _done_sent = True
        
        # ===== 新增：自動識別並添加 subtype =====
        if event.get("type") == "reply" and "subtype" not in event:
            content = event.get('content', '')
            # 檢測是否為未完成工作列表
            if "未完成的工作" in content and "繼續碼" in content:
                event["subtype"] = "pending_list"
            # 檢測是否為工具執行過程（包含迭代日誌）
            elif "### LLM 迭代" in content or "### 工具調用" in content or "工具調用已達上限" in content:
                event["subtype"] = "tool_process"
            # 檢測是否為工具執行結果（包含 CONFIRM_SPLIT 或 命令執行成功等）
            elif "CONFIRM_SPLIT" in content or "✅ 命令執行成功" in content or "❌ 執行失敗" in content:
                event["subtype"] = "tool_result"
            # ===== 新增：語義搜索 =====
            elif "相關歷史對話（語義搜索）" in content or "找到以下相關對話" in content:
                event["subtype"] = "semantic_search"
            # ===== 新增：經驗參考 =====
            elif "相關經驗參考" in content:
                event["subtype"] = "experience"
            else:
                event["subtype"] = "normal"
        # ============================================
        
        if event.get("type") == "think":
            pending_think += event.get('content', '')
        elif event.get("type") == "reply":
            full_reply_collected += event.get('content', '')
        elif event.get("type") == "done":
            # 所有回覆收集完成後，一次性寫入日誌
            # ===== 由同一個 LLM 的輸出決定標題 =====
            if full_reply_collected:
                title_line = full_reply_collected.strip().split(chr(10))[0][:20]
                if title_line:
                    session_logger.set_title(title_line)
            if pending_think:
                session_logger.append_raw(f"### 思考\n{pending_think}\n")
                pending_think = ""
            if full_reply_collected:
                session_logger.append_raw(f"### 回覆\n{full_reply_collected}\n")
        elif event.get("type") == "step_done":
            session_logger.append_raw(f"### 步驟完成\n{event.get('result', '')}\n")
        
        # ===== 🔥 核心修正：截斷發送給前端的巨量內容 =====
        # 僅對 reply 事件進行截斷，保留完整內容給 LLM（messages.append 用的是原始 event）
        if event.get("type") == "reply":
            content = event.get('content', '')
            MAX_DISPLAY_LEN = 2000  # 只顯示前 2000 字
            if len(content) > MAX_DISPLAY_LEN:
                # 複製 event，避免修改原始內容（因為原始內容要完整留給 LLM）
                truncated_event = dict(event)
                #truncated_event['content'] = content[:MAX_DISPLAY_LEN] + "\n\n... (內容過長，已截斷，但完整內容已提供給 AI 分析)"
                # 發送截斷版給前端
                if original_callback:
                    await original_callback(truncated_event)
                else:
                    if truncated_event.get("type") == "reply":
                        full_reply_collected += truncated_event.get("content", "")
                return  # 已處理，直接返回
        # ================================================

        # 發送給前端或收集回覆（原始內容）
        if original_callback:
            await original_callback(event)
        else:
            if event.get("type") == "reply":
                full_reply_collected += event.get("content", "")






    async def _get_all_pending_tasks() -> List[Dict[str, str]]:
        """獲取當前用戶在當前 Agent 的所有掛起任務列表（返回 [{code, goal_preview}, ...]）"""
        result = []
        unique_key = _get_unique_user_id(user_id, agent_name)
        found_codes = set()

        # 1️⃣ 從內存獲取
        if unique_key in _pending_task:
            for code, task in _pending_task[unique_key].items():
                goal = task.get("goal", "未知任務")
                goal_preview = goal[:60] + ("..." if len(goal) > 60 else "")
                result.append({"code": code, "goal": goal_preview})
                found_codes.add(code)

        # 2️⃣ 從檔案獲取（僅當前 agent 目錄）
        try:
            _task_dir = os.path.expanduser(f"~/.{MOKAGI_home}/agent/{agent_name}")
            _task_file = os.path.join(_task_dir, "_job.json")
            if os.path.exists(_task_file):
                with open(_task_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        all_tasks = json.loads(content)
                        if unique_key in all_tasks:
                            tasks_dict = all_tasks[unique_key]
                            if not isinstance(tasks_dict, dict):
                                tasks_dict = _upgrade_legacy_task(unique_key, tasks_dict)
                            for code, task in tasks_dict.items():
                                if code not in found_codes:
                                    goal = task.get("goal", "未知任務")
                                    goal_preview = goal[:60] + ("..." if len(goal) > 60 else "")
                                    result.append({"code": code, "goal": goal_preview})
                                    found_codes.add(code)
        except Exception as e:
            logging.warning(f"[_pending_task] 讀取掛起任務列表失敗: {e}")

        return result
    # ===== 結束 =====

    async def _run():

        

        _pending_task_dir = os.path.expanduser(f"~/.{MOKAGI_home}/agent/{agent_name}")
        _pending_task_file = os.path.join(_pending_task_dir, "_job.json")

                


        # ===== 🆕 重啟後檢查是否有未完成的 _pending_task（多任務版）=====
        ''' 工作流精髓 記錄最終目標並重上次失敗新 loop'''

        # ---------- 測試模式確認/取消 ----------
        if text.strip().startswith("/confirm"):
            parts = text.strip().split()
            if len(parts) == 2:
                context_id = parts[1]
                if context_id in _pending_llm_confirm:
                    ctx = _pending_llm_confirm.pop(context_id)
                    try:
                        if ctx.get("stream", False):
                            gen = await call_llm(
                                messages=ctx["messages"],
                                user_id=ctx["user_id"],
                                tools_def=ctx.get("tools_def"),
                                temperature=ctx.get("temperature", 0.8),
                                max_tokens=ctx.get("max_tokens", 8192),
                                agent_config=ctx.get("agent_config"),
                                conversation_id=ctx.get("conversation_id"),
                                workflow_id=ctx.get("workflow_id"),
                                _test_mode_skip_confirm=True,
                                stream=True
                            )
                            async for item in gen:
                                await _send(item)
                            await _send({"type": "done", "conv_id": None})
                        else:
                            result = await call_llm(
                                messages=ctx["messages"],
                                user_id=ctx["user_id"],
                                tools_def=ctx.get("tools_def"),
                                temperature=ctx.get("temperature", 0.8),
                                max_tokens=ctx.get("max_tokens", 8192),
                                agent_config=ctx.get("agent_config"),
                                conversation_id=ctx.get("conversation_id"),
                                workflow_id=ctx.get("workflow_id"),
                                _test_mode_skip_confirm=True,
                                stream=False
                            )
                            if isinstance(result, dict):
                                if result.get("reasoning"):
                                    await _send({"type": "think", "content": result["reasoning"]})
                                await _send({"type": "reply", "content": result.get("content", "")})
                            elif isinstance(result, str):
                                await _send({"type": "reply", "content": result})
                            await _send({"type": "done"})
                    except Exception as e:
                        await _send({"type": "reply", "content": f"❌ 執行 LLM 時出錯: {str(e)}"})
                        await _send({"type": "done"})
                    return
                else:
                    await _send({"type": "reply", "content": f"❌ 確認碼 `{context_id}` 無效或已過期"})
                    await _send({"type": "done"})
                    return
            else:
                await _send({"type": "reply", "content": "⚠️ 請提供確認碼，例如 `/confirm abc123`"})
                await _send({"type": "done"})
                return

        if text.strip().startswith("/cancel"):
            parts = text.strip().split()
            if len(parts) == 2:
                context_id = parts[1]
                if context_id in _pending_llm_confirm:
                    _pending_llm_confirm.pop(context_id)
                    await _send({"type": "reply", "content": f"🚫 已取消 LLM 調用 (ID: {context_id})"})
                    await _send({"type": "done"})
                    return
                else:
                    await _send({"type": "reply", "content": f"❌ 確認碼 `{context_id}` 無效"})
                    await _send({"type": "done"})
                    return
            else:
                await _send({"type": "reply", "content": "⚠️ 請提供確認碼，例如 `/cancel abc123`"})
                await _send({"type": "done"})
                return

        # ---------- 1. / 命令 ----------
        async def _run_direct_command():
            return await handle_direct_command(text, user_id, agent_config)

        try:
            direct_result = await _run_direct_command()
        except Exception as e:
            ''' qqq 換獨立 新debug.py '''
            direct_result = await with_autofix(
                _run_direct_command,
                max_attempts=1,#3,
                agent_config=agent_config,
                user_id=user_id,
                original_text=text
            )
            if direct_result == "__ERROR_REPORTED__":
                direct_result = "❌ 自動修復失敗，請稍後重試。"

        if direct_result:
            print("\n========== [處理 / 命令] ==========")
            await _send({"type": "think", "content": f"{MOK_AGENT_ICON}檢查到 / 命令...\n"})
            final_reply_text = ""
            if direct_result.startswith("CONFIRM_SPLIT:"):
                parts = direct_result.split("\n---CONFIRM_SPLIT---\n", 1)
                if len(parts) == 2:
                    warning_part = parts[0][len("CONFIRM_SPLIT:"):]
                    confirm_part = parts[1]
                    await _send({"type": "reply", "content": warning_part + get_model_tag(model_name)})
                    await _send({"type": "reply", "content": confirm_part})
                    final_reply_text = warning_part + "\n" + confirm_part
                else:
                    await _send({"type": "reply", "content": direct_result + get_model_tag(model_name)})
                    final_reply_text = direct_result
            else:
                await _send({"type": "reply", "content": direct_result + get_model_tag(model_name)})
                final_reply_text = direct_result
            # 保存本輪對話到歷史，並獲取 conv_id
            conv_id = await add_to_history(user_id, text, final_reply_text + get_model_tag(model_name), agent_config=agent_config)
            await _send({"type": "done", "conv_id": conv_id})



            # ===== 新增：如果是 /admin confirm 成功  qqq =====
            if text.strip().startswith('/admin confirm') and not direct_result.startswith('CONFIRM_SPLIT'):
                # 提取 token
                token = text.strip().split()[-1] if len(text.strip().split()) > 1 else None
                confirm_result = None
                if token:
                    try:
                        admin_mod = tool_handler.get_tools().get("admin")
                        if admin_mod and hasattr(admin_mod, "confirm_command"):
                            success, result = await admin_mod.confirm_command(user_id, token, agent_config)
                            if success:
                                confirm_result = f"✅ 確認成功，執行結果：\n{result}"
                            else:
                                confirm_result = f"❌ 確認失敗：{result}"
                        else:
                            confirm_result = "⚠️ 無法獲取確認結果（admin 模塊不可用）"
                    except Exception as e:
                        confirm_result = f"❌ 獲取確認結果時出錯：{str(e)}"

                # 發送確認結果給用戶
                if confirm_result:
                    await _send({"type": "reply", "content": confirm_result})

                # 檢查是否有掛起的任務需要恢復
                pending_list = await _get_all_pending_tasks()
                if pending_list:
                    if len(pending_list) == 1:
                        code = pending_list[0]["code"]
                        # 如果有確認結果且成功，將結果注入任務歷史，再恢復任務
                        if confirm_result and "✅" in confirm_result:
                            task = load_pending_task(user_id, code, agent_name)
                            if task:
                                messages = task["messages"]
                                # 注入執行結果（讓 LLM 看到 mkdir 已成功）
                                messages.append({
                                    "role": "assistant",
                                    "content": f"【系統執行結果】\n{confirm_result}\n\n請根據這個結果繼續執行任務。"
                                })
                                # 重新保存任務（含新消息）
                                save_pending_task(
                                    user_id, messages, task.get("goal", "未知任務"),
                                    max_iterations, 0, agent_name, continue_code=code
                                )
                        # 啟動任務恢復（會讀取最新的 messages）
                        asyncio.create_task(_resume_pending_task(code))
                    else:
                        msg = "發現多個未完成任務，請選擇要恢復的任務：\n"
                        for idx, item in enumerate(pending_list, 1):
                            msg += f"{idx}. `/continue {item['code']}` ({item['goal']})\n"
                        await _send({"type": "reply", "content": msg})
                else:
                    # 沒有掛起任務，但確認成功，直接結束
                    if confirm_result and "✅" in confirm_result:
                        await _send({"type": "done", "conv_id": conv_id})
                # ===== 結束 =====
                return





        
        '''
        
        qqq
        continue_code = extract_continue_command(text)
        if continue_code:
        轉為掛件工具 增加功能
        放在tools/
        刪除mokagi的
        
        '''
        # 檢查是否為繼續任務命令
        ''' 工作流精髓 記錄最終目標並重上次失敗新 loop '''
        continue_code = extract_continue_command(text)
        if continue_code:
            # 直接導入 job 工具（因為 job.py 在 tools/ 目錄下，已在 sys.path 中）
            try:
                from job import run_task
                result = await run_task(user_id, agent_name, continue_code, text, stream_callback=_send)
                # 將結果發送給前端
                await _send({"type": "reply", "content": result})
                await _send({"type": "done", "conv_id": None})
                return
            except ImportError as e:
                await _send({"type": "reply", "content": f"⚠️ 任務管理系統未就緒，請檢查 job.py 是否存在。\n錯誤: {e}"})
                await _send({"type": "done", "conv_id": None})
                return


        '''
        if not auto_mode:
            # ---------- 2. 構建上下文（記憶、語義搜索、摘要） ----------
            memory_context = ""
            memory_mod = tool_handler.get_tools().get("memory")
            if memory_mod and hasattr(memory_mod, "recall_memory"):
                try:
                    recalled = await with_autofix(
                        memory_mod.recall_memory,
                        int(user_id),
                        text,
                        memory_recall_count,
                        include_kb=True,
                        agent_config=agent_config,
                        user_id=user_id,
                        original_text=text
                    )
                    if recalled == "__ERROR_REPORTED__":
                        recalled = ""
                except Exception:
                    recalled = ""

            semantic_context = await auto_semantic_search_context(
                user_id, text, stream_callback=_send, n_results=max_tack_rounds, agent_config=agent_config
            )
            # ===== 新增：將語義搜索結果通過 reply 發送給前端 =====
            if semantic_context and semantic_context.strip():
                await _send({"type": "reply", "content": semantic_context, "subtype": "semantic_search"})
            # ===== 結束 =====

            # ===== � 經驗學習：檢索相關經驗 =====
            # 淨化查詢：移除特殊字符，避免 FTS5 解析錯誤
            safe_query = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff\s_]', ' ', text)
            safe_query = ' '.join(safe_query.split())  # 壓縮多餘空格
            experience_context = recall_experience(user_id, safe_query, agent_name, n_results=3)
            # 優先使用成功經驗，如果沒有則使用失敗經驗（但標注風險）
            if experience_context:
                experience_context = "【📚 相關經驗參考】\n" + experience_context + "\n"
            else:
                experience_context = ""
            # ===== 新增：將經驗參考通過 reply 發送給前端 =====
            if experience_context and experience_context.strip():
                await _send({"type": "reply", "content": experience_context, "subtype": "experience"})
            # ===== 結束 =====
            prompt = experience_context + memory_context + semantic_context

            # ===== 🆕 當有相關歷史對話時，指示 LLM 參考並提示用戶 =====
            if semantic_context.strip():
                prompt += (
                    "\n【📌 使用指示】\n"
                    f"以上「相關歷史對話」是與{owner}當前問題相關的舊對話記錄。\n"
                    "請你：\n"
                    "1. **仔細閱讀這些歷史對話摘要**，從中提取對回答有幫助的信息。\n"
                    "2.如果摘要中【ID】標記的對話看起來有用但資訊不完整，請主動調用 "
                    "`memory`工具（action=`get_conversation`，content=`該對話的數字ID`）"
                    "來獲取完整對話內容，再結合作出回答。\n"
                    "3. 結合歷史對話和當前問題給出完整、連貫的回答。\n\n"
                )


            # ===== 加入【最近對話摘要】=====
            try:
                recent_summary = get_recent_conversation_summary(user_id, limit=max_history_rounds, agent_config=agent_config)
                if recent_summary:
                    prompt += "【最近對話摘要】\n"
                    prompt += recent_summary
                    prompt += f"\n{owner}:{text}\n{agent_name}:"
                else:
                    # 如果沒有歷史對話，直接加入用戶訊息
                    prompt += f"\n{owner}:{text}\n{agent_name}:"
            except Exception as e:
                logging.warning(f"取得最近對話摘要失敗: {e}")
                prompt += f"\n{owner}:{text}\n{agent_name}:"

            tool_defs = build_tool_definitions()  # 保留工具定義
            agent_body = get_system_context(agent_name, owner, owner_time)
        '''


        # ---------- 2. 構建上下文（支援 initial_prompt 外部注入） ----------
        if initial_prompt is not None:
            # 外部指定的初始提示，直接使用（不添加額外的主人/助手前綴）
            prompt = initial_prompt
        else:
            # 簡化版：只包含用戶消息
            prompt = f"\n{owner}:{text}\n{agent_name}:"
        # 保留工具定義，讓 LLM 自行決定是否調用記憶/經驗等工具
        tool_defs = build_tool_definitions()
        # 系統提示：基本角色定義，由 context_files 控制載入哪些靈魂文件
        agent_body = get_system_context(agent_name, owner, owner_time, context_files=context_files)
        # 🔧 工具循環用的無 soul 版本（純工具推理，不加載 soul 文件）
        agent_body_no_soul = get_system_context(agent_name, owner, owner_time, context_files=[])
        # 注意：歷史對話、語義搜索、經驗學習等功能已轉為工具，由 LLM 主動調用。


        session_logger.append_raw(f"### 發送給 LLM 的完整上下文\n\n```用戶訊息與歷史摘要:\n{prompt}\n```\n")

        # ---------- 3. 多輪工具循環（最多 MOK_max_iterations 輪） ----------
        messages = [
            {"role": "system", "content": agent_body},
            {"role": "user", "content": prompt}
        ]
        final_reply_parts = []
        final_reply_text = ""
        use_openai_api = bool(agent_config.get("MOK_MODEL_token", ""))



        ''' qqq 計token 是否這裡寫程式? '''

        ''' llm 對話開始 '''

        # ---- 生成任務繼續碼（共用） ----
        task_code = md5(f"{user_id}_{time.time()}_{text}".encode()).hexdigest()[:12]

        # ---- 輔助：將 messages 轉為 Ollama 純文本 Prompt ----
        def format_messages_for_ollama(messages: list) -> str:
            lines = []
            for msg in messages:
                role = msg.get("role", "").capitalize()
                content = msg.get("content", "")
                if role == "Tool":
                    lines.append(f"[工具結果] {content}")
                else:
                    lines.append(f"{role}: {content}")
            return "\n".join(lines)

        if use_openai_api:
            # OpenAI 模式（流式）
            # 🔧 工具循環中替換 system message 為無 soul 版本（純粹工具推理）
            if messages and messages[0]["role"] == "system":
                messages[0]["content"] = agent_body_no_soul
            for iteration in range(max_iterations):
                # 調用流式 API（但我們不在此處流式輸出，而是收集後處理）
                # 為了流式輸出自然語言，我們仍然使用 stream=True，但要收集 tool_calls。
                # 這裡使用我們之前增強的 call_llm 流式（需要支持 tool_calls 事件）
                stream_gen = await call_llm(
                    messages=messages,
                    user_id=user_id,
                    tools_def=tool_defs,
                    stream=True,
                    temperature=0.7,
                    agent_config=agent_config
                )
                # 檢查是否為測試模式確認標記
                if isinstance(stream_gen, str) and stream_gen.startswith("__NEED_CONFIRM__"):
                    parts = stream_gen.split(":", 2)
                    if len(parts) == 3:
                        context_id = parts[1]
                        preview = parts[2]
                        await _send({"type": "think", "content": preview})
                        # 等待用戶確認，直接返回
                        return
                    else:
                        await _send({"type": "reply", "content": "⚠️ 測試模式返回格式錯誤"})
                        # 在調用 add_to_history 後保存 conv_id
                        conv_id = await add_to_history(user_id, text, final_reply_text + get_model_tag(model_name), agent_config=agent_config)
                        await _send({"type": "done", "conv_id": conv_id})
                        return
                full_reply = ""
                tool_calls = None
                async for item in stream_gen:
                    if item["type"] == "think":
                        await _send({"type": "think", "content": item["content"]})
                    elif item["type"] == "reply":
                        full_reply += item["content"]
                        # 流式發送自然語言
                        await _send({"type": "reply", "content": item["content"]})
                    elif item["type"] == "tool_calls":
                        tool_calls = item["calls"]

                # 記錄原始回覆
                session_logger.append_raw(f"### LLM 迭代 {iteration+1} 原始回覆\n```\n{full_reply}\n```\n")
                if tool_calls:
                    session_logger.append_raw(f"### 工具調用\n```json\n{json.dumps(tool_calls, ensure_ascii=False, indent=2)}\n```\n")

                # 將 assistant 消息加入歷史（包括 tool_calls）
                assistant_msg = {"role": "assistant", "content": full_reply}
                if tool_calls:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"], ensure_ascii=False)
                            }
                        }
                        for tc in tool_calls
                    ]
                messages.append(assistant_msg)

                # 如果沒有工具調用，結束循環
                if not tool_calls:
                    # � 沒有工具調用 → 需要判斷任務是否真正完成
                    # 先檢查 LLM 是否在回覆中標記了完成
                    if TASK_COMPLETE_MARKER in full_reply or TASK_COMPLETE_ALT in full_reply:
                        # 明確標記完成 → 刪除任務（如果有）
                        # � 記錄成功經驗
                        log_experience(user_id, agent_name, text, "success", messages, agent_config=agent_config)
                        delete_pending_task(user_id, task_code, agent_name)
                        final_reply_parts.append(full_reply)
                        break
                    
                    # � 沒有完成標記 → 主動詢問 LLM 是否完成
                    check_prompt = f"""
你剛剛的任務目標是：{text}

你剛剛的回答是：
{full_reply[:500]}

請判斷：這個任務是否已經完成？
- 如果完成，只輸出「已完成」
- 如果未完成，說明還需要做什麼，並輸出「未完成：需要...」
"""
                    try:
                        check_result = await call_llm(
                            prompt=check_prompt,
                            user_id=user_id,
                            stream=False,
                            temperature=0.3,
                            agent_config=agent_config,
                            include_soul=False,
                            num_predict=300
                        )
                        check_text = check_result if isinstance(check_result, str) else check_result.get("content", "")
                        
                        if "已完成" in check_text and "未完成" not in check_text:
                            # ✅ 確認完成 → 刪除任務
                            # � 記錄成功經驗
                            log_experience(user_id, agent_name, text, "success", messages, agent_config=agent_config)
                            delete_pending_task(user_id, task_code, agent_name)
                            final_reply_parts.append(full_reply)
                            break
                        else:
                            # ⚠️ 未完成 → 保存任務，讓用戶繼續
                            save_pending_task(user_id, messages, text, max_iterations, iteration, agent_name, continue_code=task_code)
                            await _send({"type": "reply", "content": f"📌 {check_text}\n\n💡 繼續執行：`/continue {task_code}`"})
                            final_reply_parts.append(full_reply)
                            final_reply_parts.append(check_text)
                            break
                    except Exception as e:
                        logging.warning(f"[完成檢查] 檢查失敗: {e}")
                        # 檢查失敗 → 保守處理：保存任務
                        save_pending_task(user_id, messages, text, max_iterations, iteration, agent_name, continue_code=task_code)
                        await _send({"type": "reply", "content": f"⚠️ 無法確認任務是否完成，請手動確認後繼續。\n\n💡 繼續執行：`/continue {task_code}`"})
                        final_reply_parts.append(full_reply)
                        break

                # 執行每個工具
                need_confirm = False
                for tc in tool_calls:
                    tool_name = tc["name"]
                    tool_args = tc["arguments"]
                    handler = find_tool_handler(tool_name)
                    if handler:
                        raw_result = await handler(tool_args, user_id, agent_config=agent_config)
                        natural_result = await naturalize_tool_result(text, tool_name, raw_result, agent_config=agent_config)
                        await _send({"type": "reply", "content": natural_result})
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": natural_result
                        })
                        final_reply_parts.append(natural_result)
                        # ===== 檢測是否需要確認 =====
                        if isinstance(raw_result, str) and raw_result.startswith("CONFIRM_SPLIT:"):
                            need_confirm = True
                            # � 使用新生成的 task_code 保存任務
                            save_pending_task(user_id, messages, text, max_iterations, iteration, agent_name, continue_code=task_code)
                            # 跳出工具迴圈
                            break
                    else:
                        err_msg = f"❌ 未找到工具: {tool_name}"
                        await _send({"type": "reply", "content": err_msg})
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": err_msg
                        })
                        final_reply_parts.append(err_msg)
                # ===== 如果觸發了確認，跳出迭代迴圈 =====
                if need_confirm:
                    break

                # 繼續下一輪
            else:

                ''' 工作流精髓 記錄最終目標並重上次失敗新 loop '''
                # � 記錄失敗經驗（達到最大迭代次數）
                log_experience(user_id, agent_name, text, "failure", messages, "達到最大迭代次數", agent_config=agent_config)
                # 超過最大輪次，保存狀態並提示用戶
                txt = save_pending_task(user_id, messages, text, max_iterations, iteration, agent_name, continue_code=task_code)
                await _send({"type": "reply", "content": txt})
                final_reply_parts.append(txt)




























        else:
            # ----- Ollama 模式（多步循環，與 OpenAI 分支行為一致）-----
            # 🔧 工具循環中替換 system message 為無 soul 版本（純粹工具推理）
            if messages and messages[0]["role"] == "system":
                messages[0]["content"] = agent_body_no_soul
            for iteration in range(max_iterations):
                # 將 messages 轉為純文本 Prompt
                prompt_text = format_messages_for_ollama(messages)
                
                # 調用本機 LLM（流式）
                stream_gen = await call_llm(
                    prompt=prompt_text,
                    user_id=user_id,
                    system_prompt="",
                    tools_def=tool_defs,
                    stream=True,
                    temperature=0.7,
                    agent_config=agent_config,
                    include_soul=False
                )
                # 檢查是否為測試模式確認標記
                if isinstance(stream_gen, str) and stream_gen.startswith("__NEED_CONFIRM__"):
                    parts = stream_gen.split(":", 2)
                    if len(parts) == 3:
                        context_id = parts[1]
                        preview = parts[2]
                        await _send({"type": "think", "content": preview})
                        return
                    else:
                        await _send({"type": "reply", "content": "⚠️ 測試模式返回格式錯誤"})
                        # 在調用 add_to_history 後保存 conv_id
                        conv_id = await add_to_history(user_id, text, final_reply_text + get_model_tag(model_name), agent_config=agent_config)
                        await _send({"type": "done", "conv_id": conv_id})
                        return
                full_reply = ""
                async for item in stream_gen:
                    if item["type"] == "think":
                        await _send({"type": "think", "content": item["content"]})
                    elif item["type"] == "reply":
                        full_reply += item["content"]
                        await _send({"type": "reply", "content": item["content"]})
                
                # 將助手回覆加入 messages
                messages.append({"role": "assistant", "content": full_reply})
                final_reply_parts.append(full_reply)
                
                # 提取工具調用
                natural_text, tool_info = extract_tool_and_text(full_reply)
                if natural_text:
                    # 已流式發送，無需重複
                    pass
                
                # 無工具調用 → 判斷是否完成
                if not tool_info:
                    if TASK_COMPLETE_MARKER in full_reply or TASK_COMPLETE_ALT in full_reply:
                        log_experience(user_id, agent_name, text, "success", messages, agent_config=agent_config)
                        delete_pending_task(user_id, task_code, agent_name)
                        break
                    
                    # 主動詢問 LLM 是否完成（與 OpenAI 分支相同）
                    check_prompt = f"""
        你剛剛的任務目標是：{text}
        你剛剛的回答是：
        {full_reply[:500]}
        請判斷：這個任務是否已經完成？
        - 如果完成，只輸出「已完成」
        - 如果未完成，說明還需要做什麼，並輸出「未完成：需要...」
        """
                    try:
                        check_result = await call_llm(
                            prompt=check_prompt,
                            user_id=user_id,
                            stream=False,
                            temperature=0.3,
                            agent_config=agent_config,
                            include_soul=False,
                            num_predict=300
                        )
                        check_text = check_result if isinstance(check_result, str) else check_result.get("content", "")
                        if "已完成" in check_text and "未完成" not in check_text:
                            log_experience(user_id, agent_name, text, "success", messages, agent_config=agent_config)
                            delete_pending_task(user_id, task_code, agent_name)
                            break
                        else:
                            save_pending_task(user_id, messages, text, max_iterations, iteration, agent_name, continue_code=task_code)
                            await _send({"type": "reply", "content": f"📌 {check_text}\n\n💡 繼續執行：`/continue {task_code}`"})
                            final_reply_parts.append(f"📌 {check_text}")
                            break
                    except Exception as e:
                        logging.warning(f"[完成檢查] 檢查失敗: {e}")
                        save_pending_task(user_id, messages, text, max_iterations, iteration, agent_name, continue_code=task_code)
                        await _send({"type": "reply", "content": f"⚠️ 無法確認任務是否完成，請手動確認後繼續。\n\n💡 繼續執行：`/continue {task_code}`"})
                        final_reply_parts.append(f"⚠️ 無法確認完成")
                        break
                
                # ----- 執行工具 -----
                need_confirm = False
                for tc in tool_info:  # tool_info 是單個工具，但為擴展仍用 for
                    tool_name = tc.get("name") if isinstance(tc, dict) else tool_info.get("name")
                    tool_args = tc.get("arguments") if isinstance(tc, dict) else tool_info.get("arguments", {})
                    handler = find_tool_handler(tool_name)
                    if handler:
                        raw_result = await handler(tool_args, user_id, agent_config=agent_config)
                        natural_result = await naturalize_tool_result(text, tool_name, raw_result, agent_config=agent_config)
                        await _send({"type": "reply", "content": natural_result})
                        messages.append({
                            "role": "tool",
                            "content": natural_result,
                            "tool_call_id": f"ollama_{iteration}_{tool_name}"
                        })
                        final_reply_parts.append(natural_result)
                        if isinstance(raw_result, str) and raw_result.startswith("CONFIRM_SPLIT:"):
                            need_confirm = True
                            save_pending_task(user_id, messages, text, max_iterations, iteration, agent_name, continue_code=task_code)
                            break
                    else:
                        err_msg = f"❌ 未找到工具: {tool_name}"
                        await _send({"type": "reply", "content": err_msg})
                        messages.append({"role": "tool", "content": err_msg})
                        final_reply_parts.append(err_msg)
                if need_confirm:
                    break
                # 繼續下一輪迭代
            else:
                # 達到最大迭代次數，保存掛起任務
                log_experience(user_id, agent_name, text, "failure", messages, "達到最大迭代次數", agent_config=agent_config)
                txt = save_pending_task(user_id, messages, text, max_iterations, iteration, agent_name, continue_code=task_code)
                await _send({"type": "reply", "content": txt})
                final_reply_parts.append(txt)

        # ===== 最後保存歷史 =====
        if not final_reply_text and final_reply_parts:
            final_reply_text = "\n".join(final_reply_parts)
        if not final_reply_text:
            final_reply_text = "（無回覆）"

        conv_id = None
        try:
            conv_id = await add_to_history(
                user_id,
                text,
                final_reply_text + get_model_tag(model_name),
                agent_config
            )
        except Exception as e:
            logging.error(f"保存歷史失敗: {e}", exc_info=True)
            conv_id = None
        finally:
            await _send({"type": "done", "conv_id": conv_id})




    # 執行事件處理器
    try:
        await _run()
    except Exception as e:
        # 嘗試自動修復整個 _run 流程
        try:
            await with_autofix(
                _run,
                max_attempts=1,#2,
                agent_config=agent_config,
                user_id=user_id,
                original_text=text
            )
        except Exception as fix_e:
            # 自動修復也失敗，發送錯誤消息
            await _send({"type": "reply", "content": f"❌ 處理消息時發生嚴重錯誤，自動修復未能解決：{str(fix_e)}"})
            await _send({"type": "done", "conv_id": None})
    return full_reply_collected if stream_callback is None else None



























































# 在 mokagi.py 頂部或 utils 中
async def with_autofix(
    func: Callable[..., Awaitable[Any]],
    *args,
    max_attempts: int = 1,
    autofix_handler=None,
    agent_config=None,
    user_id="",
    original_text="",
    **kwargs
) -> Any:
    from autofix2 import autofix_run
    return await autofix_run(
        func=func,
        func_args=args,
        func_kwargs=kwargs,
        max_attempts=max_attempts,
        autofix_handler=autofix_handler or find_tool_handler("admin"),
        autofix_extra_args={"agent_config": agent_config, "user_id": user_id},
        llm_func=call_llm,
        agent_config=agent_config,
        user_id=user_id,
        original_text=original_text
    )




# ========== 自動修復重試包裝器（安全導入 autofix2，無循環依賴）==========
async def safe_autofix_retry(
    action_func: Callable[..., Awaitable[Any]],
    action_args: tuple = (),
    action_kwargs: dict = None,
    max_retries_before_autofix: int = 2,   # 失敗2次後進入autofix
    error_info_builder: Optional[Callable[[Exception, dict], dict]] = None,
    autofix_extra_args: dict = None,
) -> Any:
    """
    統一的重試+自動修復包裝器。
    用法示例：
        result = await safe_autofix_retry(handler, args=(user_id,), kwargs={"code": "..."})
    """
    from autofix2 import retry_with_autofix   # 僅在調用時導入，避免頂層循環

    # 獲取 autofix 工具處理器（通過 tool_handler 動態查找，不直接 import autofix）
    autofix_handler = find_tool_handler("autofix")
    if autofix_handler is None:
        # 若 autofix 工具不可用，則使用普通重試（無修復）
        last_exception = None
        for attempt in range(1, max_retries_before_autofix + 1):
            try:
                return await action_func(*action_args, **(action_kwargs or {}))
            except Exception as e:
                last_exception = e
                logging.warning(f"[safe_autofix_retry] 第 {attempt} 次嘗試失敗: {e}")
                await asyncio.sleep(0.3)
        raise last_exception

    # 調用 autofix2 的通用重試器
    return await retry_with_autofix(
        action_func=action_func,
        action_args=action_args,
        action_kwargs=action_kwargs,
        max_retries_before_autofix=max_retries_before_autofix,
        error_info_builder=error_info_builder,
        autofix_handler=autofix_handler,
        autofix_extra_args=autofix_extra_args
    )

















# 舊碼:
# 
# 
# 
# 







# ===== 202607031420 泠：新增 get_latest_conversation_id =====
def get_latest_conversation_id(user_id: str, agent_name: str = None) -> Optional[int]:
    """獲取指定用戶/Agent 的最新對話 ID（user rowid），用於任務關聯。"""
    if agent_name is None:
        agent_name = _agent_config.get("MOK_AGENT_NAME", "助手") if _agent_config else "助手"
    unique_id = _get_unique_user_id(user_id, agent_name)
    _init_history_db()
    try:
        with closing(sqlite3.connect(HISTORY_DB_PATH, timeout=5.0)) as conn:
            cursor = conn.execute(
                'SELECT MAX(id) FROM conversation_history WHERE user_key = ? AND role = ?',
                (unique_id, 'user')
            )
            row = cursor.fetchone()
            return row[0] if row and row[0] else None
    except Exception as e:
        logging.warning(f"[get_latest_conversation_id] 查詢失敗: {e}")
        return None
# ===== 結束 =====
