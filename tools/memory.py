

# ------------------------------------------------------------------------------------ #
# 工具名稱: memory (長期記憶與知識庫管理)
# 用途: 提供 Agent 的持久化記憶、知識庫、對話歷史語義搜索等功能。
#       支持每個 Agent 完全隔離（記憶庫、知識庫、對話向量庫均按 agent_name 分庫）。
#
# 主要函數:
#   handle_memory(args, chat_id, agent_config)
#       - 入口函數，處理 /memory 及其子命令（remember/recall/list/delete/update/forgetall）
#       - 也處理知識庫操作（rebuild_kb/list_kb）和對話歷史搜索（semantic_search/get_conversation/get_full_history）
#       - 返回操作結果字符串（支持 HTML 標籤）
#
#   recall_memory(chat_id, query, n_results, include_kb, agent_config)
#       - 從用戶個人記憶和知識庫中檢索相關條目（向量相似度搜索）
#       - 用於構建上下文的自動記憶召回
#
#   semantic_search_conversation(chat_id, query, n_results, keywords, agent_config)
#       - 對話歷史全文搜索（FTS5 + 聯想詞擴展）
#       - 支持空格分隔的多關鍵詞，自動調用 associate 工具生成聯想詞
#
#   rebuild_knowledge_base(agent_name, mokagi_home)
#       - 掃描 Agent 目錄下的 .md 文件，按標題分塊後存入 ChromaDB（向量知識庫）
#
#   index_conversation(...) - 將一輪對話索引到 ChromaDB（供語義搜索使用）
#
# 數據隔離機制:
#   - ChromaDB collection 名稱通過 _get_safe_agent_name(agent_name) 生成 MD5 哈希值，
#     確保不同 Agent 的向量庫完全分離。
#   - SQLite 對話歷史表通過 user_key (user_id + agent_name) 隔離。
#
# 依賴:
#   chromadb, sentence-transformers（知識庫和語義搜索需要）
#   sqlite3（對話歷史 FTS5 搜索）
#   associate 工具（生成聯想詞）
#
# 更新記錄:
#   202608110022_出街版 - 初始版本。
#   20260614     - 增加配置診斷、多關鍵詞聯想詞擴展、agent_config 傳遞修復。
# ------------------------------------------------------------------------------------ #

# ------------------------------------------------------------------------------------ #
# 字典: PLUGIN_INFO
# 用途: 定義長期記憶工具與主程式、意圖辨識系統之間的介面。
#       主程式透過它來註冊 /memory 命令、建立自然語言關鍵詞映射、
#       提供給 LLM 的工具描述。
# 欄位說明:
#   command           : 直接命令 "/memory"，顯示於菜單。
#   icon              : 工具圖示。
#   handler           : 處理函數名稱 "handle_memory"。
#   description       : 簡短描述，用於命令選單。
#   intent_keywords   : 自然語言觸發詞列表，元組格式（關鍵詞, 完整命令）。
#   tool_schema       : 提供給 LLM 的工具定義，描述參數與用途。
#   update            : 最後更新日期。
# ------------------------------------------------------------------------------------ #
# 
# 





PLUGIN_INFO = {
    "command": "/memory",
    "icon":"🧠",
    "description": "長期記憶管理：記住信息(remember)、回憶記憶(recall)、列出記憶(list)、更新記憶(update)、刪除記憶(delete)、清空記憶(forgetall)、重建知識庫(rebuild_kb)、列出知識庫(list_kb)。對話歷史搜索：語義搜索對話歷史(semantic_search)、獲取完整對話(get_conversation)、查看全部對話摘要(get_full_history)。 **patch_conv_id** (不需要 content)：補全 chat_history 表中舊消息的 conv_id 字段。",
    "handler": "handle_memory",
    "intent_keywords": [
        ("/新知識", "/memory rebuild_kb"),
        ("/知識", "/memory list_kb"),
        ("/記", "/memory remember"),
        ("/之前", "/memory recall"),
        ("/剛剛", "/memory recall"),
        ("/回憶", "/memory list"),
        ("/全部歷史", "/memory get_full_history"),
        ("/完整對話", "/memory get_full_history"),
        ("/所有聊天記錄", "/memory get_full_history"),
        ("/語義搜索", "/memory semantic_search"),
        ("/對話搜索", "/memory semantic_search"),
        ("/搜索對話", "/memory semantic_search"),
        ("/獲取會話", "/memory get_conversation"),
        ("/會話ID", "/memory get_conversation"),
        ("/修id", "/memory patch_conv_id"),
    ],
    "update":"202608110022_出街版",


    "tool_schema": {
        "name": "memory",
        "description": (
            "管理長期記憶、知識庫以及對話歷史語義搜索。"
            "當用戶要求「記住」、「回憶」、「刪除記憶」、「重建知識庫」、「搜索歷史對話」時使用。\n\n"
            "【重要】哪些 action 必須提供 content？\n"
            "- remember, recall, delete, update, semantic_search, get_conversation → 必須提供 content\n"
            "- list, forgetall, rebuild_kb, list_kb, get_full_history → 禁止提供 content（提供會被忽略）\n\n"
            "【返回格式】\n"
            "- 成功：人類可讀字符串（例如「✅ 已記住 […]」或找到的對話列表）。\n"
            "- semantic_search 返回格式：「【ID】角色: 內容摘要」，其中 ID 是數字，可用於 get_conversation。\n"
            "- 錯誤時返回 JSON：{\"success\": false, \"error_type\": \"...\", \"error_message\": \"...\"}。\n\n"
            "【常見錯誤避免】\n"
            "1. get_conversation 的 ID 必須從 semantic_search 返回的【ID】中獲得，不可隨意填寫。\n"
            "2. 若 semantic_search 返回結果少於 2 條，可提高 assoc_count（例如設為 10）後再試一次。\n"
            "3. 不要在 update 或 delete 的 content 中加入多餘文字，嚴格按格式給出。\n"
            "4. 不要為 list, forgetall, rebuild_kb, list_kb, get_full_history 提供 content 參數。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": [
                        "remember", "recall", "list", "delete", "update",
                        "forgetall", "rebuild_kb", "list_kb",
                        "get_full_history", "semantic_search", "get_conversation",
                        "get_recent_summary",
                        "patch_conv_id"
                    ],
                    "description": (
                        "要執行的操作。各 action 的詳細說明（含是否需要 content）：\n\n"
                        "**remember** (需要 content)：記住用戶說的重要信息。\n"
                        "  示例：用戶說「記住我喜歡喝冰美式」 → action=remember, content=\"用戶喜歡喝冰美式\"\n\n"
                        "**recall** (需要 content)：根據關鍵詞回憶相關記憶。\n"
                        "  示例：用戶問「我之前喜歡喝什麼？」 → action=recall, content=\"喜歡喝\"\n\n"
                        "**list** (不需要 content)：列出最近 10 條記憶。\n\n"
                        "**delete** (需要 content，僅數字)：刪除指定序號的記憶。\n"
                        "  示例：用戶說「刪除第3條記憶」 → action=delete, content=\"3\"\n\n"
                        "**update** (需要 content，格式「序號 新內容」)：更新指定序號的記憶。\n"
                        "  示例：用戶說「把第3條改成我現在喜歡拿鐵」 → action=update, content=\"3 我現在喜歡拿鐵\"\n\n"
                        "**forgetall** (不需要 content)：清空所有記憶（危險操作，僅在用戶明確要求時使用）。\n\n"
                        "**rebuild_kb** (不需要 content)：重建知識庫（掃描 Agent 目錄下的 .md 文件）。\n\n"
                        "**list_kb** (不需要 content)：列出知識庫中的區塊。\n\n"
                        "**get_full_history** (不需要 content)：返回全部對話的摘要列表（格式：「輪次X (ID:Y): 用戶: ... Agent: ...」）。\n\n"
                        "**semantic_search** (需要 content)：搜索對話歷史中的相關對話。支持多個關鍵詞（空格分隔），系統會自動生成聯想詞擴充搜索。\n"
                        "  示例：用戶問「我們之前討論過車和飛機嗎？」 → action=semantic_search, content=\"車 飛機\"\n\n"
                        "**get_conversation** (需要 content，數字 ID)：根據會話 ID 獲取完整對話。\n"
                        "  ID 必須從 semantic_search 返回的【ID】中獲得。\n"
                        "  示例：用戶說「把ID為123的對話完整內容給我」 → action=get_conversation, content=\"123\""
                        
                        "**get_recent_summary** \n取得最近 N 輪對話的摘要列表（內容為 LLM 生成的關鍵詞與摘要）。參數 content 為可選數字（輪數），預設使用配置的 MAX_HISTORY_ROUNDS \n"

                        "**patch_conv_id** (不需要 content)：補全 chat_history 表中舊消息的 conv_id 字段。\n"
                        "  該操作會掃描 conversation_history.db 與 chat_history.db，按內容與時間戳匹配，\n"
                        "  將缺失的 conv_id 補齊，使所有歷史消息都能正確顯示對話 ID。\n"
                        "  執行完畢後會返回更新的消息總數。"
                    )
                },
                "content": {
                    "type": "string",
                    "description": (
                        "操作的具體內容，格式取決於 action。\n\n"
                        "特別注意：\n"
                        "- 對於 remember：系統會自動將「我」轉為用戶名，「你/妳/您」轉為 Agent 名。\n"
                        "- 對於 semantic_search：可輸入單個關鍵詞或多個詞（空格分隔）。例如「部署 錯誤」。\n"
                        "- 對於 update：必須嚴格遵循「序號 新內容」格式，中間有一個空格。\n"
                        "- 對於 delete 和 get_conversation：僅接受純數字，不要加任何其他字符。\n"
                        "- 對於 list, forgetall, rebuild_kb, list_kb, get_full_history：不要提供 content，提供會被忽略。"
                    )
                },
                "n_results": {
                    "type": "integer",
                    "description": (
                        "僅對 semantic_search 有效。指定返回的對話記錄最大條數。\n"
                        "- 默認 10，最小 1，無上限（但過大會消耗 token）。\n"
                        "- 若用戶要求「找幾條」、「最近相關」、「多一些」，建議設為 5~15。\n"
                        "- 若用戶要求「全部」、「所有相關」，建議設為 30 或更高（注意 token 消耗）。\n"
                        "- 範例：用戶說「幫我找5條關於車的對話」 → n_results=5"
                    ),
                    "default": 10,
                    "minimum": 1
                },
                "assoc_count": {
                    "type": "integer",
                    "description": (
                        "僅對 semantic_search 有效。每個原始關鍵詞生成多少個聯想詞（用於擴充搜索範圍）。\n\n"
                        "默認 5，最小 1，無上限。數值越大搜索越全面，但會增加 LLM 調用和 token 消耗。\n\n"
                        "**何時提高此值（例如設為 10~15）：**\n"
                        "- 用戶的關鍵詞很抽象或歧義（例如「蘋果」指水果還是手機）。\n"
                        "- 第一次搜索結果很少（少於 3 條）且用戶要求「多找找」。\n"
                        "- 用戶明確要求「搜索所有可能的相關內容」。\n\n"
                        "**何時降低此值（例如設為 3）：**\n"
                        "- 搜索結果太多幹擾，且用戶要求「只找最相關的」。\n"
                        "- 快速測試時希望節省 token。\n\n"
                        "**範例**：用戶說「搜一下關於『車』的對話，多擴充一點聯想」 → assoc_count=10"
                    ),
                    "default": 5,
                    "minimum": 1
                }
            },
            "required": ["action"]
        }
    },

}



























import jieba 
import logging, os, re, chromadb, hashlib, json, time, sqlite3, sys
from chromadb.config import Settings
from contextlib import closing
from typing import Optional, Dict, Callable
import subprocess



# 全局變量初始化
_EMBED_AVAILABLE = None
_embed_fn = None
_client = None
MISSING_DEPS = False



# ------------------------------------------------------------------------------------ #
# 函數: sanitize_name_for_chromadb
# 用途: 將任意原始名稱轉換為符合 ChromaDB collection 命名規則的安全名稱。
# 設計:
#   使用 MD5 哈希生成固定長度的十六進制字串，並加上 "agent_" 前綴確保首字符為字母。
#   避免因名稱包含特殊字符或過長導致 collection 建立失敗。
# 參數:
#   raw_name: 原始名稱（如 agent 名稱）。
# 返回:
#   str: 安全的 collection 名稱。
# ------------------------------------------------------------------------------------ #
def sanitize_name_for_chromadb(raw_name: str) -> str:
    """使用哈希生成唯一且合規的 ChromaDB collection 名稱"""
    import hashlib
    # 用 MD5 哈希確保不同名稱生成不同字符串（長度固定，只含十六進制字符）
    hash_hex = hashlib.md5(raw_name.encode()).hexdigest()[:16]
    # 確保首字符為字母（ChromaDB 要求）
    return f"agent_{hash_hex}"


def _get_safe_agent_name(agent_name: str) -> str:
    hash_hex = hashlib.md5(agent_name.encode()).hexdigest()[:16]
    return f"agent_{hash_hex}"

def _get_knowledge_dir(agent_name: str, mokagi_home: str) -> str:
    return os.path.expanduser(f"~/.{mokagi_home}/{agent_name}")

# 明確指定數據存儲路徑
MOKAGI_HOME = os.environ.get("MOKAGI_HOME", "mok")
CHROMA_PATH = os.path.join(os.path.expanduser("~"), f".{MOKAGI_HOME}", ".chroma_data")







'''
             %@*                            +.         #=     :@@-              
             @@-                            -@*        :@*    :@+               
            :@%                              *@#        @@  . :@+ -             
            +@=      .   .%-.......%@.        @@        #+ =@=:@+ +%            
            %@      =@=  .@@*******@@@        == :. %@@@@@@@@%+@+  @%           
           .@@******@@@* .@*       %@           :@@- .     -  :@+  =@=          
           =@:..@@:....  .@*       %@    :*********+ +=   :@%..@+   @+          
           %+   @@       .@*       %@                .@-  +@  .@+   :           
          -%    @@       .@*       %@           .     @@  #+  .@+               
          #.    @@       .@*       %@          .@@.   #%  %   .@+  .@-          
         -:     @@       .@*       %@     .+++++++=-==**=*#===*@%==%@@=         
                @@    :  .@*       %@              .::::::::::=@#:::::          
                @@   +@+ .@*       %@                          @*               
         =*****#@@#*#@@@*.@*       %@           @%   @=...-@%  @*  .:           
          .....:@@...... .@*       %@     .#####%%+  @%++++@@= @#  *@#          
                @#       .@*       %@                @+    @*  %@  @@           
               .@*       .@*       %@                @+    @*  #@ :@*           
               -@=       .@*       %@     .=    -%   @+    @*  +@ *@.           
               *@%=      .@*       %@     :@@%%%@@@  @@###%@*  -@+@#            
               @@ #%     .@*       %@     :@-   +@-  @+    @*  .@@@.            
              .@+  @@.   .@*       %@     :@-   +@.  @+    @*   @@*             
              +@   :@@   .@*       %@     :@-   +@.  @+    @*   @@              
              @#    *@#  .@*       %@     :@-   +@.  @*    @*  =@@.   :         
             +@      @@. .@*       %@     :@-   +@.  @@###%@* :@*@*   -         
             @-      =@: .@@*******@@     :@-   +@.  @+    @-.@= *@. :-         
            %+        -  .@#       %@     :@*---#@.  @+     .%:   @% *:         
           **            .@*       %@     :@*:::#@.  :     :#     =@@@.         
          *=             .@-       -      :@-   :-        =-       =@@*         
         =:                               .#             .          -@#         
'''

# -------------------- 嵌入支援與 ChromaDB 初始化 --------------------
# ------------------------------------------------------------------------------------ #
# 區塊: Embedding 支援檢查
# 用途: 嘗試載入 sentence-transformers 庫，判斷是否支援向量嵌入。
#       若未安裝，則 EMBED_AVAILABLE = False，知識庫功能將不可用。
# ------------------------------------------------------------------------------------ #
def _init_embedding():
    global _EMBED_AVAILABLE, _embed_fn, MISSING_DEPS
    if _EMBED_AVAILABLE is not None:
        return _EMBED_AVAILABLE
    try:
        from chromadb.utils import embedding_functions
        _embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(model_name="all-MiniLM-L6-v2")
        _EMBED_AVAILABLE = True
        logging.info("sentence-transformers 可用，知識庫功能已啟用")
    except ImportError:
        _EMBED_AVAILABLE = False
        MISSING_DEPS = True
        logging.warning("sentence-transformers 未安裝，知識庫功能將不可用")
    return _EMBED_AVAILABLE

def get_kb_collection(agent_name: str):
    """延遲初始化知識庫 collection，根據 Agent 名稱動態獲取"""
    if not _init_embedding():
        return None
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=CHROMA_PATH, settings=Settings(anonymized_telemetry=False))
    try:
        collection_name = f"{_get_safe_agent_name(agent_name)}_room"
        return _client.get_or_create_collection(
            name=collection_name,
            embedding_function=_embed_fn
        )
    except Exception as e:
        logging.error(f"創建知識庫 collection 失敗: {e}")
        return None

# ------------------------------------------------------------------------------------ #
# 函數: _col
# 用途: 取得使用者記憶的 ChromaDB collection 物件，若尚未初始化則自動建立。
# 設計:
#   因為全域變數可能在模組載入時因缺少依賴而為 None，此函數提供懶加載機制。
# 返回:
#   chromadb.Collection: 使用者記憶 collection。
# ------------------------------------------------------------------------------------ #
def _col(agent_name: str, MOK_ADMIN_NAME: str = None):
    """取得使用者記憶的 ChromaDB collection 物件，根據當前 Agent 名稱動態獲取"""
    global _client, MISSING_DEPS
    if MISSING_DEPS:
        return None
    try:
        if _client is None:
            _client = chromadb.PersistentClient(
                path=CHROMA_PATH,
                settings=Settings(anonymized_telemetry=False)
            )
        collection_name = f"{_get_safe_agent_name(agent_name)}_user_memory"
        return _client.get_or_create_collection(name=collection_name)
    except Exception as e:
        logging.error(f"初始化記憶 collection 失敗: {e}")
        MISSING_DEPS = True
        return None























# ------------------------------------------------------------------------------------ #
# 函數: chunk_markdown_by_headings
# 用途: 將 Markdown 文件內容按標題（#、##、###...）切分成多個區塊，每個區塊包含標題及其下的內容。
# 設計:
#   支援按標題分塊，若單個區塊內容過長（超過 max_chars），則進一步按段落切分。
#   無標題的開頭內容會被歸入一個 "(無標題)" 的區塊。
# 參數:
#   content: Markdown 原始內容字串。
#   max_chars: 每個區塊的最大字符數，預設 500。
# 返回:
#   list: 每個元素為 dict，包含 "heading" 和 "content"。
# ------------------------------------------------------------------------------------ #
def chunk_markdown_by_headings(content: str, max_chars=500) -> list:
    """將 Markdown 文件按 # 標題分塊，每個塊包含標題及內容。返回 [{heading, content}]"""
    import re
    lines = content.split('\n')
    chunks = []
    current_heading = None
    current_content = []
    heading_re = re.compile(r'^(#{1,6})\s+(.+)$')

    def flush():
        nonlocal current_heading, current_content
        if current_heading is None or not current_content:
            return
        full_text = f"{current_heading}\n\n" + "\n".join(current_content).strip()
        if len(full_text) <= max_chars:
            chunks.append({"heading": current_heading, "content": full_text})
        else:
            # 長內容：再按段落切分
            para_text = "\n".join(current_content)
            paragraphs = re.split(r'\n\s*\n', para_text)
            buffer = ""
            for para in paragraphs:
                candidate = (buffer + "\n\n" + para).strip()
                if len(candidate) <= max_chars:
                    buffer = candidate
                else:
                    if buffer:
                        chunks.append({"heading": current_heading, "content": f"{current_heading}\n\n{buffer}"})
                    buffer = para
            if buffer:
                chunks.append({"heading": current_heading, "content": f"{current_heading}\n\n{buffer}"})
        current_heading = None
        current_content = []

    for line in lines:
        match = heading_re.match(line)
        if match:
            flush()
            current_heading = line.strip()
        else:
            if current_heading is not None:
                current_content.append(line)
            else:
                # 無標題內容（檔案開頭）
                if not chunks and not current_content:
                    current_heading = "(無標題)"
                current_content.append(line)
    flush()
    return chunks

# ------------------------------------------------------------------------------------ #
# 函數: rebuild_knowledge_base
# 用途: 掃描 KNOWLEDGE_DIR 目錄下的所有 .md 文件，將其切塊後存入知識庫 ChromaDB collection。
# 設計:
#   先刪除舊的知識庫資料（where={"source": "kb"}），再重新建立。
#   每個 .md 文件獨立處理，按標題切塊，並記錄元數據（來源檔案、標題、區塊 ID）。
# 返回:
#   str: 操作結果訊息（成功或錯誤）。
# ------------------------------------------------------------------------------------ #
def rebuild_knowledge_base(agent_name: str, mokagi_home: str) -> str:
    kb = get_kb_collection(agent_name)
    if kb is None:
        return "❌ 知識庫功能未啟用，請安裝 sentence-transformers 並重啟。"
    knowledge_dir = _get_knowledge_dir(agent_name, mokagi_home)
    if not os.path.exists(knowledge_dir):
        return f"❌ 知識庫目錄不存在，請建立 {knowledge_dir} 並放入 .md 檔案。"
    try:
        kb.delete(where={"source": "kb"})
    except:
        pass
    count = 0
    for filename in os.listdir(knowledge_dir):
        if not filename.endswith('.md'):
            continue
        filepath = os.path.join(knowledge_dir, filename)
        with open(filepath, 'r', encoding='utf-8') as f:
            content = f.read()
        chunks = chunk_markdown_by_headings(content, max_chars=500)
        for idx, chunk in enumerate(chunks):
            doc_text = chunk["content"]
            heading = chunk["heading"]
            doc_id = hashlib.md5(f"{filename}_{idx}_{heading}_{doc_text[:50]}".encode()).hexdigest()
            kb.add(
                documents=[doc_text],
                metadatas=[{"source": "kb", "file": filename, "heading": heading, "chunk_id": idx}],
                ids=[doc_id]
            )
            count += 1
    safe_name = _get_safe_agent_name(agent_name)
    return f"✅ {safe_name} 已更新 {count} 個知識塊（按標題分塊）。"


























'''
                                              .                                 
                                             -@%.         %*                    
           **:....................#@-        -@=          .@%      .            
           *@*++++++++++++++++++++%@%        -@=           *@.    *@=           
           *@                     *@         -@=   :#######%@#####@@@-          
           *@                     *@         -@=      ::       -=               
           *@                     *@         -@=:      @#      %@%.             
           *@                     *@       + -@=-#     :@%    .@#               
           *@     +.        %=    *@       # -@= @+     %@    +#   -@:          
           *@     @@#######%@@+   *@       @ -@= *@+====%@+==+@*==+@@@-         
           *@     @%        @#    *@      .@ -@= =@-..................          
           *@     @%        @*    *@      +@ -@=  :  -            =.            
           *@     @%        @*    *@      @% -@=     @@**********#@@-           
           *@     @%        @*    *@     +@+ -@=     @%          :@#            
           *@     @%        @*    *@     :*  -@=     @#           @*            
           *@     @%        @*    *@         -@=     @%          :@*            
           *@     @%        @*    *@         -@=     @@++++++++++*@*            
           *@     @%        @*    *@         -@=     @#           @*            
           *@     @@:::::::=@*    *@         -@=     @#          .@*            
           *@     @@=======+@*    *@         -@=     @@###########@*            
           *@     @%        @*    *@         -@=     @+           *.            
           *@     @+        *     *@         -@=           *=                   
           *@                     *@         -@=    -  %#.  @#    =*            
           *@                     *@         -@=    # .@#   =@+    *@:          
           *@                     *@         -@=   .@ .@*    @@     @@.         
           *@                     *@         -@=   %# .@*    *#  :  =@#         
           *@%####################@@         -@=  %@- .@*        *   @@         
           *@.                    #@         -@= -@+  .@#.......-@-  +-         
           *@                     *@         -@=       @@@@@@@@@@@#             
           *#                     -:         -@-        --====--:.              
'''


# ------------------------------------------------------------------------------------ #
# 函數: recall_memory
# 用途: 從使用者記憶和（可選）知識庫中檢索與查詢相關的訊息。
# 設計:
#   先從使用者個人記憶（chat_id 限定）進行向量檢索，若 include_kb=True 且知識庫可用，
#   則同時檢索知識庫，將兩部分結果合併返回。
# 參數:
#   chat_id: 使用者 ID（整數）。
#   query: 檢索查詢字串。
#   n_results: 各來源返回的最大結果數量。
#   include_kb: 是否包含知識庫檢索。
# 返回:
#   str: 合併後的記憶文字，若無結果則返回空字串。
# ------------------------------------------------------------------------------------ #
async def recall_memory(chat_id: int, query: str, n_results: int = 1, include_kb: bool = False, agent_config: Optional[Dict] = None) -> str:
    if MISSING_DEPS:
        return ""
    if agent_config is None:
        import mokagi
        agent_config = mokagi._agent_config
    agent_name = agent_config.get("MOK_AGENT_NAME", "助手")
    parts = []
    try:
        col = _col(agent_name)
        if col is not None:
            results = col.query(
                query_texts=[query],
                n_results=n_results,
                where={"chat_id": str(chat_id)}
            )
            docs = results.get("documents", [[]])[0]
            if docs:
                parts.append("\n".join(docs))
    except Exception as e:
        logging.error(f"使用者記憶檢索錯誤: {e}")
    if include_kb:
        kb = get_kb_collection(agent_name)
        if kb is not None:
            try:
                kb_results = kb.query(
                    query_texts=[query],
                    n_results=n_results,
                    where={"source": "kb"}
                )
                kb_docs = kb_results.get("documents", [[]])[0]
                if kb_docs:
                    parts.append("【知識庫】\n" + "\n\n---\n\n".join(kb_docs))
            except Exception as e:
                logging.error(f"知識庫檢索錯誤: {e}")
    return "\n\n".join(parts) if parts else ""
















# 舊對話加入 語義搜索
def _get_conversation_collection(agent_name: str):
    """返回用於對話語義搜索的 ChromaDB collection（與知識庫共用 client）"""
    if not _init_embedding():
        return None
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(path=CHROMA_PATH, settings=Settings(anonymized_telemetry=False))
    collection_name = f"{_get_safe_agent_name(agent_name)}_conversation"
    return _client.get_or_create_collection(name=collection_name, embedding_function=_embed_fn)

def index_conversation(user_key: str, user_msg: str, assistant_reply: str, user_rowid: int, assistant_rowid: int, agent_config: Dict):
    """將一輪對話索引到 ChromaDB（供 mokagi.add_to_history 調用）"""
    agent_name = agent_config.get("MOK_AGENT_NAME", "助手")
    MOK_ADMIN_NAME = agent_config.get("MOK_ADMIN_NAME", "用戶")
    col = _get_conversation_collection(agent_name)
    if not col:
        return
    text = f"{MOK_ADMIN_NAME}: {user_msg}\n{agent_name}: {assistant_reply}"
    doc_id = f"{user_key}_{user_rowid}"
    col.upsert(
        documents=[text],
        metadatas=[{
            "user_key": user_key,
            "user_rowid": user_rowid,
            "assistant_rowid": assistant_rowid,
            "timestamp": time.time()
        }],
        ids=[doc_id]
    )
















# 相關對話記錄搜索
async def semantic_search_conversation(
    chat_id: str,
    query: str,
    n_results: int = 10,
    keywords: list = None,
    agent_config: Optional[Dict] = None,
    assoc_count: int = 5,
    stream_callback: Optional[Callable] = None
) -> str:
    """
    對話歷史全文搜索（FTS5 + 聯想詞擴展），支持自動切換 LIKE 搜索。

    參數:
        chat_id: 用戶 ID
        query: 搜索查詢
        n_results: 最大返回條數（預設 10）
        keywords: 外部傳入的關鍵詞列表（若為 None，則從 query 分詞取得）
        agent_config: Agent 配置
        assoc_count: 每個原始關鍵詞生成聯想詞的數量（預設 5）
        stream_callback: 異步回調，用於輸出思考過程

    返回:
        搜索結果字符串
    """
    # ----- 可調整的常量（寫死值集中於此）-----
    SEARCH_LIMIT_MULTIPLIER = 2   # 內部搜索時限制條數的倍數（用於提升召回率）
    KEYWORD_LIMIT = 15            # 聯想詞總數上限（當 keywords 為 None 時）
    DISPLAY_KEYWORD_LIMIT = 10    # 思考輸出中顯示的關鍵詞最大數量
    DISPLAY_SUMMARY_LIMIT = 5     # 思考輸出中顯示的摘要條數（超過則顯示 ...）
    # 如找到 10 條結果  會顯示 前 5 條的摘要 + ... 共 10 條
    # ----------------------------------------

    import mokagi
    import sqlite3
    import os
    from contextlib import closing

    final_keywords = []   # 初始化

    if agent_config is None:
        agent_name = os.environ.get("MOK_AGENT_NAME")
        if agent_name:
            agent_config = await mokagi.get_agent_config(agent_name)
        else:
            agent_config = mokagi._agent_config
    agent_name = agent_config.get("MOK_AGENT_NAME", "助手")
    MOK_ADMIN_NAME = agent_config.get("MOK_ADMIN_NAME", "用戶")

    # 配置快速校驗
    model_url = agent_config.get("MOK_MODEL_url", "")
    model_token = agent_config.get("MOK_MODEL_token", "")
    if model_url == "http://localhost:11434/v1" and not model_token:
        return f"❌ 語義搜索失敗：Agent「{agent_name}」模型端點配置為 localhost:11434 且無 API Token。請檢查配置文件 ~/.mok/.{agent_name}"

    if not query or not query.strip():
        return "請提供有效的搜索關鍵詞。"

    # ----- 分詞處理 -----
    if keywords is None:
        raw_tokens = [word.strip() for word in jieba.cut(query) if word.strip()]
        direct_keywords = [w for w in raw_tokens if len(w) >= 2 and not w.isdigit()]
        if not direct_keywords:
            direct_keywords = [query.strip()]
        raw_keywords = direct_keywords[:]
    else:
        direct_keywords = keywords
        raw_keywords = keywords

    unique_id = mokagi._get_unique_user_id(chat_id, agent_name)

    # ----- 內部搜索函數（自動切換 FTS5 / LIKE）-----
    async def _search_with_keywords(kw_list, limit_multiplier=SEARCH_LIMIT_MULTIPLIER):
        """使用給定的關鍵詞列表進行搜索，安全詞用 FTS5，不安全詞用 LIKE"""
        local_results = {}
        with closing(sqlite3.connect(mokagi.HISTORY_DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            for kw in kw_list:
                if not kw or len(kw) < 2:
                    continue
                # 判斷是否為安全詞（僅字母數字中文底線）
                if re.match(r'^[\w\u4e00-\u9fff]+$', kw):
                    # ----- FTS5 搜索 -----
                    safe_kw = kw.replace('"', '').replace("'", "")
                    try:
                        rows = cursor.execute('''
                            SELECT fts.rowid, fts.content, h.role, h.content as original_content, rank
                            FROM conversation_fts fts
                            JOIN conversation_history h ON fts.rowid = h.id
                            WHERE conversation_fts MATCH ? AND h.user_key = ?
                            ORDER BY rank
                            LIMIT ?
                        ''', (f'"{safe_kw}"', unique_id, n_results * limit_multiplier)).fetchall()
                    except sqlite3.OperationalError as e:
                        logging.warning(f"FTS5 查詢失敗: {kw} - {e}，改用 LIKE")
                        rows = []
                    for row in rows:
                        rid = row['rowid']
                        if rid not in local_results:
                            local_results[rid] = {
                                'content': row['original_content'],
                                'role': row['role'],
                                'rank': row['rank'],
                                'kw': kw
                            }
                        elif row['rank'] < local_results[rid]['rank']:
                            local_results[rid]['rank'] = row['rank']
                else:
                    # ----- LIKE 搜索（不安全詞）-----
                    try:
                        rows = cursor.execute('''
                            SELECT id, role, content FROM conversation_history
                            WHERE user_key = ? AND content LIKE ?
                            ORDER BY id DESC LIMIT ?
                        ''', (unique_id, f'%{kw}%', n_results * limit_multiplier)).fetchall()
                    except Exception as e:
                        logging.warning(f"LIKE 查詢失敗: {kw} - {e}")
                        continue
                    for row in rows:
                        rid = row['id']
                        if rid not in local_results:
                            local_results[rid] = {
                                'content': row['content'],
                                'role': row['role'],
                                'rank': 0,
                                'kw': 'LIKE'
                            }
        return local_results

    # ----- 嘗試 AND 查詢（僅用安全詞）-----
    safe_for_and = [kw for kw in direct_keywords if re.match(r'^[\w\u4e00-\u9fff]+$', kw) and len(kw) >= 2]
    and_query = ' AND '.join(f'"{kw}"' for kw in safe_for_and) if safe_for_and else None
    all_results = {}
    if and_query:
        with closing(sqlite3.connect(mokagi.HISTORY_DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()
            try:
                rows = cursor.execute('''
                    SELECT fts.rowid, fts.content, h.role, h.content as original_content, rank
                    FROM conversation_fts fts
                    JOIN conversation_history h ON fts.rowid = h.id
                    WHERE conversation_fts MATCH ? AND h.user_key = ?
                    ORDER BY rank
                    LIMIT ?
                ''', (and_query, unique_id, n_results * SEARCH_LIMIT_MULTIPLIER)).fetchall()
                for row in rows:
                    rid = row['rowid']
                    all_results[rid] = {
                        'content': row['original_content'],
                        'role': row['role'],
                        'rank': row['rank'],
                        'kw': 'AND'
                    }
            except sqlite3.OperationalError:
                pass
    if not all_results:
        all_results = await _search_with_keywords(direct_keywords)

    # ----- 聯想詞擴充 -----
    used_keywords = direct_keywords   # 預設
    keyword_type = "原始關鍵詞"
    if not all_results and keywords is None:
        from tools.associate import _generate_associations
        all_keywords = set()
        for kw in raw_keywords:
            all_keywords.add(kw)
            assoc_words = await _generate_associations(kw, count=assoc_count, agent_config=agent_config)
            all_keywords.update(assoc_words)
        final_keywords = list(all_keywords)[:max(KEYWORD_LIMIT, n_results)]
        all_results = await _search_with_keywords(final_keywords)
        if all_results:
            used_keywords = final_keywords
            keyword_type = "聯想詞"
        else:
            return f"沒有找到與「{query}」相關的對話記錄。"
    elif not all_results and keywords is not None:
        return f"沒有找到與「{query}」相關的對話記錄。"
    else:
        # 有結果，但未使用聯想詞，保持 direct_keywords
        used_keywords = direct_keywords
        keyword_type = "原始關鍵詞"

    # ----- 格式化輸出 -----
    sorted_items = sorted(all_results.items(), key=lambda x: x[1]['rank'])
    output = []
    seen_ids = set()
    for rid, info in sorted_items:
        if rid in seen_ids:
            continue
        seen_ids.add(rid)
        content = info['content'] or ''
        summary = content[:80].replace('\n', ' ')
        if len(content) > 80:
            summary += "..."
        role = "用戶" if info['role'] == 'user' else "助手"
        output.append(f"【{rid}】{role}: {summary}")
        if len(output) >= n_results:
            break

    if not output:
        return f"沒有找到與「{query}」相關的對話記錄。"

    # ----- 透過 stream_callback 輸出詳細思考 -----
    if stream_callback:
        # 顯示使用的關鍵詞（最多 DISPLAY_KEYWORD_LIMIT 個）
        kw_display = ", ".join(used_keywords[:DISPLAY_KEYWORD_LIMIT])
        if len(used_keywords) > DISPLAY_KEYWORD_LIMIT:
            kw_display += "..."
        await stream_callback({"type": "think", "content": f"🔍 使用{keyword_type}：{kw_display}\n"})
        # 顯示找到的條數和摘要（最多 DISPLAY_SUMMARY_LIMIT 條）
        await stream_callback({"type": "think", "content": f"📊 找到 {len(output)} 條相關對話：\n"})
        for i, line in enumerate(output[:DISPLAY_SUMMARY_LIMIT], 1):
            await stream_callback({"type": "think", "content": f"{i}. {line}\n"})
        if len(output) > DISPLAY_SUMMARY_LIMIT:
            await stream_callback({"type": "think", "content": f"... 共 {len(output)} 條\n"})

    keyword_hint = ""
    if keywords is None and final_keywords and len(final_keywords) > 1:
        keyword_hint = f"（使用聯想詞：{', '.join(final_keywords[:DISPLAY_KEYWORD_LIMIT])}）"
    elif keywords is None and len(direct_keywords) > 1:
        keyword_hint = f"（使用關鍵詞：{', '.join(direct_keywords[:DISPLAY_KEYWORD_LIMIT])}）"
    #return f"找到以下相關對話{keyword_hint}：\n\n" + "\n".join(output)
    return f"✅ 找到 {len(output)} 條相關對話{keyword_hint}：\n\n" + "\n".join(output)










# ========== 新增：重建對話索引 ==========
def rebuild_conversation_index(agent_name: str, mokagi_home: str = None) -> str:
    """
    重建指定 Agent 的對話向量索引（僅重建索引，不刪除原始對話）。
    若 agent_name 為 None，則重建所有 Agent。
    """
    import mokagi
    if mokagi_home is None:
        mokagi_home = mokagi.MOKAGI_home

    col = _get_conversation_collection(agent_name)
    if not col:
        return f"⚠️ Agent「{agent_name}」向量索引未啟用（請先安裝 sentence-transformers）"

    # 清空該 Agent 的舊索引（只刪向量，不碰對話）
    try:
        col.delete(where={"user_key": {"$exists": True}})
    except:
        pass

    history_db = mokagi.HISTORY_DB_PATH
    count = 0
    user_key_prefix = f"%_{agent_name}"  # 用於過濾該 Agent 的記錄

    with closing(sqlite3.connect(history_db)) as conn:
        # 取出所有用戶消息（按時間正序）
        rows = conn.execute(
            "SELECT id, content, user_key FROM conversation_history WHERE role = 'user' ORDER BY id"
        ).fetchall()

        for row in rows:
            user_key = row[2]
            # 只處理屬於當前 agent 的記錄（user_key 以 `_{agent_name}` 結尾）
            if not user_key.endswith(f"_{agent_name}"):
                continue

            # 查找對應的 assistant 回覆（緊接的下一條 assistant 消息）
            assistant_row = conn.execute(
                "SELECT content FROM conversation_history WHERE user_key = ? AND role = 'assistant' AND id > ? ORDER BY id LIMIT 1",
                (user_key, row[0])
            ).fetchone()
            assistant_text = assistant_row[0] if assistant_row else ""

            # 調用已有的 index_conversation 函數
            index_conversation(
                user_key,
                row[1],
                assistant_text,
                row[0],
                row[0] + 1,   # assistant_rowid 佔位，實際用不到
                {"MOK_AGENT_NAME": agent_name, "MOK_ADMIN_NAME": "用戶"}  # 需提供 Agent 名
            )
            count += 1

    return f"✅ Agent「{agent_name}」對話索引已重建，共處理 {count} 輪對話（原始對話未改動）"


def rebuild_all_agents_index() -> str:
    """
    遍歷所有 Agent，逐一重建對話向量索引。
    """
    import mokagi
    import os

    agent_base = os.path.expanduser(f"~/.{mokagi.MOKAGI_home}/agent")
    if not os.path.exists(agent_base):
        return "❌ Agent 目錄不存在"

    results = []
    for agent_name in os.listdir(agent_base):
        agent_dir = os.path.join(agent_base, agent_name)
        if not os.path.isdir(agent_dir):
            continue
        # 檢查是否有對應的 .{agent_name} 配置文件（確保是有效 Agent）
        config_file = os.path.join(agent_dir, f".{agent_name}")
        if not os.path.exists(config_file):
            continue

        try:
            msg = rebuild_conversation_index(agent_name)
            results.append(f"✅ {agent_name}: {msg}")
        except Exception as e:
            results.append(f"❌ {agent_name}: 失敗 - {str(e)}")

    return "\n".join(results) if results else "沒有找到任何 Agent"
# ========== 結束 ==========
























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
# 函數: handle_memory
# 用途: 記憶工具的主入口，處理 /memory 及其子命令。
# 設計:
#   1. 檢查依賴套件，若缺少則提示安裝。
#   2. 無參數時顯示幫助訊息。
#   3. 解析子命令，分別處理：
#      - remember: 記住資訊（人稱標準化替換）。
#      - recall: 搜尋相關記憶。
#      - list: 列出最近的記憶。
#      - delete: 刪除指定序號的記憶。
#      - update: 更新指定序號的記憶內容。
#      - forgetall: 清空所有記憶。
#      - rebuild_kb: 重建知識庫（掃描 .md 文件）。
#      - list_kb: 列出知識庫中的區塊。
# 參數:
#   args: 命令參數字串（包含子命令和內容）。
#   chat_id: 使用者 ID（由主程式傳入）。
# 返回:
#   str: 操作結果訊息（支援 HTML 標籤）。
# ------------------------------------------------------------------------------------ #













async def handle_memory(args, chat_id: str = None, agent_config: Optional[Dict] = None):
    import mokagi
    import os, sys, subprocess
    # 如果外部沒有傳入 agent_config，嘗試從環境變量獲取或使用全局配置
    if agent_config is None:
        agent_name_from_env = os.environ.get("MOK_AGENT_NAME")
        if agent_name_from_env:
            agent_config = await mokagi.get_agent_config(agent_name_from_env, force_reload=False)
        else:
            agent_config = mokagi._agent_config
    agent_name = agent_config.get("MOK_AGENT_NAME", "助手")
    MOK_ADMIN_NAME = agent_config.get("MOK_ADMIN_NAME", "用戶")
    mokagi_home = mokagi.MOKAGI_home
    
    # 參數標準化
    if args is None:
        args = ""
    if isinstance(args, dict):
        args = json.dumps(args, ensure_ascii=False)
    elif not isinstance(args, str):
        args = str(args)
    args = args.strip()
    try:
        parsed = json.loads(args)
        if isinstance(parsed, dict):
            args = parsed
    except:
        pass

    if MISSING_DEPS:
        msg = (
            "❌ 記憶工具缺少必要套件：`chromadb`、`sentence-transformers`\n\n"
            "請使用以下命令安裝（需要管理員權限）：\n"
            "<pre>/admin pip install chromadb sentence-transformers</pre>\n\n"
            "發送後會要求二次確認，輸入確認碼即可自動安裝。"
        )
        return msg

    if not chat_id:
        chat_id = agent_config.get("ADMIN_CHAT_ID", "default_user")

    # 統一轉為字串格式
    if isinstance(args, dict):
        action = args.get("action", "")
        content = args.get("content", "")
        if not action:
            return json.dumps({
                "success": False,
                "error_type": "missing_parameter",
                "error_message": "缺少 'action' 參數。請指定要執行的操作，如 'remember' 或 'recall'。",
                "tool": "memory",
                "original_args": str(args),
                "suggested_fix": "請提供 action 字段"
            }, ensure_ascii=False)
        if action in ["remember", "recall", "delete", "update"] and not content:
            return json.dumps({
                "success": False,
                "error_type": "missing_parameter",
                "error_message": f"執行 '{action}' 需要提供 'content' 參數。",
                "tool": "memory",
                "original_args": str(args),
                "suggested_fix": f"請添加 content 字段，例如 {{'action': '{action}', 'content': '...'}}"
            }, ensure_ascii=False)
        if action:
            args = f"{action} {content}".strip()
        else:
            args = json.dumps(args, ensure_ascii=False)
    elif not isinstance(args, str):
        args = str(args)

    if not isinstance(args, str):
        args = ""
    args = args.strip()

    if not args:
        help_text = f'''

📚 知識庫使用說明：
    (在 {mokagi_home} 目錄下建立 {agent_name} 資料夾，並放入 .md 檔案。)

    更新知識庫<pre>/memory rebuild_kb</pre>

    顯示知識庫<pre>/memory list_kb</pre>
=====
{PLUGIN_INFO["icon"]} 長期記憶使用說明：
    (使用下方按鈕快速操作，或直接輸入命令)

    <pre>/memory remember 內容</pre>
    例：/memory remember 我喜歡喝咖啡
    <pre>/memory recall 關鍵詞</pre>
    例：/memory recall 喜歡喝什麼

    列出所有記憶<pre>/memory list</pre>

    更新記憶<pre>/memory update 序號 新內容</pre>

    刪除記憶<pre>/memory delete 序號</pre>

    忘記所有記憶<pre>/memory forgetall</pre>

    查看全部對話記錄摘要<pre>/memory get_full_history</pre>

    語義搜索對話歷史<pre>/memory semantic_search 關鍵詞 [數量]</pre>
    例：/memory semantic_search 程式碼錯誤 5

    獲取指定會話的完整對話<pre>/memory get_conversation 會話ID</pre>
    例：/memory get_conversation 123

=====
🧩 自然語言意圖辨識：
'''
        for keyword, cmd in PLUGIN_INFO["intent_keywords"]:
            help_text += f'   "{keyword}" → {cmd}\n'
        return help_text

    parts = args.split(maxsplit=1)
    subcmd = parts[0].lower()
    content = parts[1] if len(parts) > 1 else ""

    try:
        col = _col(agent_name, MOK_ADMIN_NAME)
        if col is None:
            return json.dumps({
                "success": False,
                "error_type": "runtime_error",
                "error_message": "記憶功能未初始化，請檢查 chromadb 是否正常安裝。",
                "tool": "memory",
                "original_args": args
            }, ensure_ascii=False)

        # remember
        if subcmd == "remember":
            if not content:
                return "用法: /memory remember 內容\n  例：/memory remember 我喜歡喝咖啡\n\n"
            normalized = content
            normalized = re.sub(r'^(?:記住)\s*', '', normalized)
            normalized = re.sub(r'我', MOK_ADMIN_NAME, normalized)
            normalized = re.sub(r'你|妳|您', agent_name, normalized)
            col.add(
                documents=[normalized],
                metadatas=[{"chat_id": chat_id}],
                ids=[f"{chat_id}_{col.count()}"]
            )
            return f"✅ {agent_name} 已記住 [{normalized}]"

        # recall
        elif subcmd == "recall":
            if not content:
                return "用法: /memory recall 關鍵詞\n  例：/memory recall 喜歡喝什麼"
            results = col.query(
                query_texts=[content],
                n_results=3,
                where={"chat_id": chat_id}
            )
            docs = results.get("documents", [[]])[0]
            if not docs:
                return "沒有找到相關記憶。"
            reply = "🧠 回憶：\n"
            for d in docs:
                reply += f"· {d}\n"
            return reply

        # list
        elif subcmd == "list":
            results = col.get(where={"chat_id": chat_id}, limit=10)
            docs = results.get("documents", [])
            if not docs:
                return "目前沒有任何記憶。"
            reply = "📋 最近記憶：\n=====================\n"
            for i, d in enumerate(docs):
                reply += f"\n========= {i+1} =========\n{d}\n=====================\n"
            return reply

        # forgetall
        elif subcmd == "forgetall":
            col.delete(where={"chat_id": chat_id})
            return "🗑 記憶已清空。"

        # delete
        elif subcmd == "delete":
            if not content:
                return "用法: <pre>/memory delete 序號</pre>\n  例：/memory delete 1\n\n先用 <pre>/memory list</pre> 查看序號。"
            try:
                idx = int(content) - 1
            except ValueError:
                return "序號必須是數字。"
            all_mem = col.get(where={"chat_id": chat_id})
            ids = all_mem.get("ids", [])
            if idx < 0 or idx >= len(ids):
                return f"序號無效，請輸入 1 到 {len(ids)} 之間的數字。"
            target_id = ids[idx]
            col.delete(ids=[target_id])
            return f"✅ 已刪除第 {content} 條記憶。"

        # update
        elif subcmd == "update":
            parts = content.split(maxsplit=1)
            if len(parts) < 2:
                return "用法: <pre>/memory update 序號 新內容</pre>\n  例：/memory update 1 我是100歲"
            try:
                idx = int(parts[0]) - 1
            except ValueError:
                return "序號必須是數字。"
            new_content = parts[1]
            all_mem = col.get(where={"chat_id": chat_id})
            ids = all_mem.get("ids", [])
            docs = all_mem.get("documents", [])
            if idx < 0 or idx >= len(ids):
                return f"序號無效，請輸入 1 到 {len(ids)} 之間的數字。"
            target_id = ids[idx]
            old_doc = docs[idx]
            col.delete(ids=[target_id])
            new_id = f"{chat_id}_{col.count()}"
            col.add(
                documents=[new_content],
                metadatas=[{"chat_id": chat_id}],
                ids=[new_id]
            )
            return f"✅ 已將第 {parts[0]} 條記憶從「{old_doc}」更新為「{new_content}」。"

        # rebuild_kb
        elif subcmd == "rebuild_kb":
            return rebuild_knowledge_base(agent_name, mokagi_home)

        # list_kb
        elif subcmd == "list_kb":
            kb = get_kb_collection(agent_name)
            if kb is None:
                return "❌ 知識庫功能未啟用或尚未重建。"
            try:
                results = kb.get(limit=50)
                docs = results.get("documents", [])
                metadatas = results.get("metadatas", [])
                if not docs:
                    return "知識庫中尚無任何區塊，請先執行 /memory rebuild_kb。"
                reply = "📚 知識庫區塊列表：\n=====================\n"
                for i, (doc, meta) in enumerate(zip(docs, metadatas)):
                    heading = meta.get("heading", "無標題")
                    source_file = meta.get("file", "未知檔案")
                    preview = doc[:60].replace('\n', ' ')
                    reply += f"\n[{i+1}] {heading} (from {source_file})\n    {preview}...\n"
                return reply
            except Exception as e:
                logging.error(f"列出知識庫錯誤: {e}")
                return f"❌ 列出知識庫失敗: {e}"

        # get_full_history
        elif subcmd == "get_full_history":
            full_summary = mokagi.get_all_conversation_summary(chat_id, agent_config=agent_config)
            return full_summary

        # semantic_search
        elif subcmd == "semantic_search":
            # 如果 args 是字典（來自 LLM 工具調用），從中提取參數
            if isinstance(args, dict):
                query = args.get("content", "")
                n = args.get("n_results", 10)
                assoc_count = args.get("assoc_count", 5)
                if isinstance(n, str) and n.isdigit():
                    n = int(n)
                elif isinstance(n, int):
                    pass
                else:
                    n = 10
                # 確保 assoc_count 至少為 1（不設上限）
                if isinstance(assoc_count, int):
                    assoc_count = max(1, assoc_count)
                else:
                    assoc_count = 5
            else:
                # 命令模式：/memory semantic_search 關鍵詞 [數量]
                if not content:
                    return "用法: /memory semantic_search 關鍵詞 [數量]"
                parts = content.rsplit(maxsplit=1)
                n = 10
                query = content
                if len(parts) == 2 and parts[1].isdigit():
                    n = int(parts[1])
                    query = parts[0]
                assoc_count = 5
            return await semantic_search_conversation(chat_id, query, n_results=n, assoc_count=assoc_count, agent_config=agent_config)
        


        # get_conversation
        elif subcmd == "get_conversation":
            if not content:
                return "用法: /memory get_conversation 會話ID"
            try:
                conv_id = int(content)
            except ValueError:
                return "會話ID必須是數字"
            unique_id = mokagi._get_unique_user_id(chat_id, agent_name)
            with closing(sqlite3.connect(mokagi.HISTORY_DB_PATH)) as conn:
                conn.row_factory = sqlite3.Row
                user_row = conn.execute(
                    "SELECT id, role, content FROM conversation_history WHERE user_key = ? AND id = ? AND role = 'user'",
                    (unique_id, conv_id)
                ).fetchone()
                if not user_row:
                    return f"未找到會話ID {conv_id} 對應的用戶消息。"
                assistant_row = conn.execute(
                    "SELECT content FROM conversation_history WHERE user_key = ? AND id > ? AND role = 'assistant' ORDER BY id ASC LIMIT 1",
                    (unique_id, conv_id)
                ).fetchone()
                user_msg = user_row['content']
                assistant_reply = assistant_row['content'] if assistant_row else f"{agent_name}未找到回覆"
                result = f"{MOK_ADMIN_NAME}: {user_msg}\n{agent_name}: {assistant_reply}"
                return result

        elif subcmd == "get_recent_summary":
            # 可選參數：輪數（預設為 MAX_HISTORY_ROUNDS）
            try:
                limit = int(content) if content and content.isdigit() else mokagi.MAX_HISTORY_ROUNDS
            except:
                limit = mokagi.MAX_HISTORY_ROUNDS
            summary = mokagi.get_recent_conversation_summary(chat_id, limit=limit, agent_config=agent_config)
            return summary


        elif subcmd == "patch_conv_id":
            # 嘗試多個可能的位置
            possible_paths = [
                os.path.join(os.path.dirname(__file__), 'scripts', 'patch_conv_id.py'),  # ~/.mok/tools/scripts/
                os.path.join(os.path.dirname(__file__), 'patch_conv_id.py'),             # ~/.mok/tools/
                os.path.join(os.getcwd(), 'patch_conv_id.py'),                          # 當前工作目錄
                os.path.expanduser(f"~/.{mokagi_home}/tools/patch_conv_id.py"),         # 顯式 ~/.mok/tools/
            ]
            script_path = None
            for p in possible_paths:
                if os.path.exists(p):
                    script_path = p
                    break
            if not script_path:
                return "❌ 未找到補丁腳本 patch_conv_id.py，請確保它位於 ~/.mok/tools/ 或 ~/.mok/tools/scripts/ 目錄下。"
            
            try:
                result = subprocess.run(
                    [sys.executable, script_path],
                    capture_output=True,
                    text=True,
                    timeout=60
                )
                if result.returncode == 0:
                    return f"✅ 補丁執行成功：\n{result.stdout}"
                else:
                    return f"❌ 補丁執行失敗（返回碼 {result.returncode}）：\n{result.stderr}"
            except subprocess.TimeoutExpired:
                return "⏰ 補丁執行超時（超過 60 秒），請手動運行腳本。"
            except Exception as e:
                return f"❌ 執行補丁時出錯：{str(e)}"


        else:
            return f"未知子命令: {subcmd}"

    except Exception as e:
        logging.error(f"記憶工具錯誤: {e}")
        return json.dumps({
            "success": False,
            "error_type": "runtime_error",
            "error_message": str(e),
            "tool": "memory",
            "original_args": args
        }, ensure_ascii=False)
