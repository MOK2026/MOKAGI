"""
202608260224_我覺得可以版
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
import inspect
import threading
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
try:
    import output_router
except Exception:
    output_router = None
try:
    import fcntl
except ImportError:  # Windows host compatibility
    import msvcrt

    class _CompatFcntl:
        LOCK_EX = 1
        LOCK_SH = 2
        LOCK_NB = 4
        LOCK_UN = 8

        @staticmethod
        def flock(handle, op):
            if handle is None or not hasattr(handle, "fileno"):
                return
            try:
                handle.seek(0, os.SEEK_END)
                size = max(1, handle.tell())
                handle.seek(0)
                if op == _CompatFcntl.LOCK_UN:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, size)
                elif op & _CompatFcntl.LOCK_EX:
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, size)
            except Exception:
                pass

    fcntl = _CompatFcntl()

import openai
from collections import defaultdict
from typing import Dict, List, Optional, Callable, Awaitable, Any, Tuple, Union, AsyncGenerator

import sqlite3
from db_conn import connect
from contextlib import closing

import httpx

# 導入工具管理模塊（獨立於前端）
import tool_handler, recovery; [logging.getLogger(_n).setLevel(logging.WARNING) for _n in ("httpx", "httpcore", "urllib3", "openai", "watchdog", "werkzeug")]; MOK_DEBUG_LLM = str(os.environ.get("MOK_DEBUG_LLM", "0")).strip().lower() in ("1", "true", "yes", "on"); _dbg = (print if MOK_DEBUG_LLM else (lambda *a, **k: None))  # [D 2026-09-26 衍] 日誌精簡：第三方 INFO 降級 + LLM prompt dump 收進 MOK_DEBUG_LLM 開關（預設關）

















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

from context import _agent_config_ctx


def _resolve_agent_config(agent_config=None):
    """解析當前應用的 agent_config。
    優先序：傳入值 -> 當前協程 contextvar -> 全局 _agent_config（向後兼容保底）。
    """
    if agent_config is not None:
        return agent_config
    try:
        ctx_cfg = _agent_config_ctx.get()
    except Exception:
        ctx_cfg = None
    return ctx_cfg if ctx_cfg is not None else _agent_config



toolsBtn = 1



test = False  # 調試開關，控制是否輸出詳細調試信息
_pending_llm_confirm = {}  # {context_id: {"messages": [...], "params": {...}, "timestamp": float}}
_PENDING_CONFIRM_TTL = 3600.0  # 待確認 LLM 呼叫的過期時間（秒），避免長期常駐記憶體持續增長

def _cleanup_pending_confirm():
    """清理過期的待確認 LLM 呼叫（惰性清理：儲存/讀取時觸發）。"""
    now = time.time()
    expired = [cid for cid, ctx in _pending_llm_confirm.items()
               if now - ctx.get("timestamp", 0) > _PENDING_CONFIRM_TTL]
    for cid in expired:
        _pending_llm_confirm.pop(cid, None)

TASK_COMPLETE_MARKER = "TASK_COMPLETE: true"
TASK_COMPLETE_ALT = "任務完成"

# 模型回應的最大等待時間（秒），超過則認定為失敗，避免{owner}長時間等待
_model_timeout = 300.0

# 確保工具目錄在 Python 路徑中
TOOLS_DIR = os.path.expanduser(f"~/.{MOKAGI_home}/tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)



















# ========== Agent 配置緩存（隔離不同 Agent）==========
# 註：實際配置載入統一走 config.get_agent_config()；此鎖僅供向後兼容，用線程鎖避免跨 loop 崩潰
_config_cache_lock = threading.Lock()

def get_config_lock():
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
# ── Agent 歸屬（誰建立／擁有這個 agent）───────────────────────────────
# 來源：web 會員系統 member.db → agent_owners(agent, owner, created_ts)
#   · 新 agent 由補丁 身分核心_202609250200 的 create_agent 寫入
#   · 歷史 agent 於 2026-09-29 回填（owner=admin）
# 目的：讓 agent 在 system prompt 裡就知道「我屬於誰」，被問到時不必猜。
_agent_owner_cache = {}          # {agent_name: (block, ts)}
_agent_owner_ttl = 300           # 快取 5 分鐘，避免每次組 prompt 都查 DB
_member_db_cache = {"path": None, "ts": 0.0}


def _find_member_db_path():
    """找出會員系統的 member.db（多個帶時間戳目錄時取最新）。失敗回預設路徑。"""
    import glob as _glob
    now = time.time()
    if _member_db_cache["path"] and (now - _member_db_cache["ts"]) < 600:
        return _member_db_cache["path"]
    base = os.path.expanduser(f"~/.{MOKAGI_home}/frontends/mok_web")
    try:
        cands = _glob.glob(os.path.join(base, "會員系統_*", "member.db"))
    except Exception:
        cands = []
    path = sorted(cands)[-1] if cands else os.path.join(
        base, "會員系統_202608311340", "member.db")
    _member_db_cache["path"] = path
    _member_db_cache["ts"] = now
    return path


def _agent_owner_lookup(agent_name):
    """查 agent 的擁有者 → (owner, display_name)；查不到或出錯一律 (None, None)。"""
    if not agent_name:
        return None, None
    try:
        with closing(connect(_find_member_db_path(), timeout=5.0, readonly=True)) as conn:
            row = conn.execute(
                "SELECT owner FROM agent_owners WHERE agent=?", (agent_name,)).fetchone()
            owner = row[0] if row else None
            disp = None
            if owner:
                r2 = conn.execute(
                    "SELECT display_name FROM users WHERE username=?", (owner,)).fetchone()
                disp = (r2[0] if r2 else None) or None
        return owner, disp
    except Exception:
        return None, None


def _agent_owner_block(agent_name):
    """組【Agent 歸屬】注入區塊；查不到也明講「未記錄」，不編造。"""
    if not agent_name:
        return ""
    now = time.time()
    _c = _agent_owner_cache.get(agent_name)
    if _c and (now - _c[1]) < _agent_owner_ttl:
        return _c[0]
    owner, disp = _agent_owner_lookup(agent_name)
    if owner:
        _who = f"{owner}（{disp}）" if disp and disp != owner else owner
        _lines = [
            "【Agent 歸屬】（系統自動注入，唯讀）",
            f"- 本 agent：{agent_name}",
            f"- 建立者／擁有者：{_who}",
            "- 說明：這是本 agent 在會員系統中的歸屬紀錄；被問到「你是誰建的／你屬於誰」時直接照此回答，不要臆測。",
        ]
    else:
        _lines = [
            "【Agent 歸屬】（系統自動注入，唯讀）",
            f"- 本 agent：{agent_name}",
            "- 建立者／擁有者：未記錄（早於歸屬機制上線，或非經網頁建立）",
        ]
    block = "\n".join(_lines)
    _agent_owner_cache[agent_name] = (block, now)
    return block


_SOUL_EXP_TAIL_LIMIT = 4000  # [稚 2026-10-04] EXP.md 尾段回退上限（僅在整檔無標題時使用）
_SOUL_EXP_INDEX_N = 6        # [稚 2026-10-07／P0-1] EXP.md 只注入最近 N 條經驗「標題」
_PROFILE_MAX_AGE_DAYS = 7    # [稚 2026-10-07／P0-3] 動態近況注入時效（天），可用 MOK_PROFILE_MAX_AGE_DAYS 覆寫
_PROFILE_MAX_LINES = 12      # [稚 2026-10-07／P0-3] 動態近況注入行數上限


def _exp_index_block(content: str) -> str:
    """[P0-1] EXP.md 退出常駐：只注入最近 N 條經驗「標題」索引，全文按需由房間讀檔工具取。

    整檔找不到 ## 開頭標題時，才回退舊行為（尾段 _SOUL_EXP_TAIL_LIMIT 字元），保證不更差。
    """
    try:
        heads = [ln.strip()[3:].strip() for ln in (content or "").splitlines()
                 if ln.strip().startswith("## ")]
    except Exception:
        heads = []
    if not heads:
        if len(content or "") > _SOUL_EXP_TAIL_LIMIT:
            return ("（EXP 索引無法解析，僅顯示尾段；完整檔請用房間讀檔工具取 soul/EXP.md）\n\n"
                    + content[-_SOUL_EXP_TAIL_LIMIT:])
        return content
    tail = heads[-_SOUL_EXP_INDEX_N:]
    lines = ["（EXP.md 索引：只列最近 %d 條經驗標題；完整內容請用房間讀檔工具取 soul/EXP.md）" % len(tail)]
    lines += ["- " + h for h in tail]
    return "\n".join(lines)


def _profile_decay_filter(body: str) -> str:
    """[P0-3] 動態近況注入端的時效衰減＋去重（只影響注入，不改磁碟檔）。

    超過 MOK_PROFILE_MAX_AGE_DAYS（預設 7）天的條目不再注入；重複內容只留一次；
    總行數上限 _PROFILE_MAX_LINES。任何異常一律原樣返回，不影響主流程。
    """
    try:
        try:
            max_age = float(os.environ.get("MOK_PROFILE_MAX_AGE_DAYS") or _PROFILE_MAX_AGE_DAYS)
        except Exception:
            max_age = float(_PROFILE_MAX_AGE_DAYS)
        cutoff = time.time() - max_age * 86400.0
        out, seen = [], set()
        for ln in (body or "").splitlines():
            s = ln.strip()
            if not s:
                continue
            m = re.match(r"^-\s*\((\d{4})-(\d{2})-(\d{2})\)\s*(.+)$", s)
            key = s.lower()
            if m:
                try:
                    if time.mktime(time.strptime("%s-%s-%s" % (m.group(1), m.group(2), m.group(3)),
                                                 "%Y-%m-%d")) < cutoff:
                        continue
                except Exception:
                    pass
                key = m.group(4).strip().lower()
            if key in seen:
                continue
            seen.add(key)
            out.append(s)
            if len(out) >= _PROFILE_MAX_LINES:
                break
        return "\n".join(out)
    except Exception:
        return body


def _soul_gate_ok(agent_name: str, content: str) -> bool:
    """[P1-5] 靈魂檔「條件注入」閘門：無標記者一律注入（對其他 agent 零影響）。

    檔案首 400 字若含 MOK_SOUL_GATE 註解並指定 group 清單，則只有該 agent 的
    MOK_AGENT_group 命中清單時才注入。已知 agent 未命中即不注入；只有「讀不到該 agent
    設定」時才 fail-open 放行，避免鎖死。
    """
    try:
        m = re.search(r"<!--\s*MOK_SOUL_GATE:\s*group=([^>]*?)-->", (content or "")[:400])
        if not m:
            return True
        want = [g.strip() for g in re.split(r"[,，/|\s]+", m.group(1)) if g.strip()]
        if not want:
            return True
        try:
            from config import load_agent_config
            _cfg = load_agent_config(agent_name) or {}
            grp = str(_cfg.get("MOK_AGENT_group") or "").strip()
            _known = bool(_cfg.get("MOK_AGENT_NAME")) or os.path.isfile(
                os.path.expanduser("~/.mok/agent/{}/.{}".format(agent_name, agent_name)))
        except Exception:
            return True          # 讀不到設定 → fail-open（向後相容、不鎖死）
        if not _known:
            return True          # 未知 agent → fail-open
        return grp in want       # 已知 agent：群組未命中 → 不注入
    except Exception:
        return True


def _strip_profile_dynamic(content: str) -> str:
    """剝除 soul 檔內的「動態近況」段（標題＋標記區塊）。記憶分庫用。"""
    if not content:
        return content
    try:
        b = "<!-- MOK_PROFILE_DYNAMIC_BEGIN -->"
        e = "<!-- MOK_PROFILE_DYNAMIC_END -->"
        i = content.find(b)
        j = content.find(e)
        if i != -1 and j != -1 and j > i:
            head = content[:i]
            k = head.rfind("##")
            if k != -1 and "動態近況" in head[k:]:
                head = head[:k]
            content = head + content[j + len(e):]
    except Exception:
        pass
    return content.strip()

def _safe_profile_key(s) -> str:
    out = []
    for ch in str(s or ""):
        out.append(ch if (ch.isalnum() or ch in "._-") else "_")
    return ("".join(out).strip("._") or "_")

def _load_user_profile_block(agent_name: str, uid: str) -> str:
    """載入「當前對話者」專屬 profile（記憶分庫）。找不到回空字串。"""
    if not uid:
        return ""
    path = os.path.expanduser(
        "~/.mok/user/{}/profile/{}.md".format(_safe_profile_key(uid), _safe_profile_key(agent_name)))
    if not os.path.isfile(path):
        return ""
    try:
        with open(path, "r", encoding="utf-8") as f:
            body = f.read().strip()
    except Exception:
        return ""
    if not body:
        return ""
    body = _profile_decay_filter(body)  # [P0-3] 7 天衰減＋去重（只影響注入）
    if not body:
        return ""
    return f"## 關於當前對話者（{uid}）\n\n{body}"

_env_info_cache = {}  # 稚 2026-10-05：環境塊小時級凍結快取（key = 時間|agent|work_dir）

def get_system_context(agent_name: str, owner: str, owner_time: int=0, context_files: Optional[List[str]] = None, output_dir: Optional[str] = None, output_role: Optional[str] = None, current_user: Optional[str] = None) -> str:
    """獲取主機環境信息 + Agent 工作目錄，帶緩存
    
    context_files: 可選，指定要載入的 soul 文件列表（如 ["agent.md", "user.md"]）。
                   若為 None（預設），載入 soul/ 目錄下所有文件。
                   若為空列表 []，不載入任何 soul 文件。
    current_user: 可選，當前對話者 uid（記憶分庫：只載入該對話者專屬的 profile，
                  並剝除 soul 檔內的動態段，避免任何人的私料被夾帶進他人對話）。"""
    global _system_context_cache
    now = time.time()
    # 稚 2026-10-05：cache_key 納入 output_dir/output_role，避免換 job 後 60 秒內回傳舊的【產出位置】
    cache_key = f"{agent_name}:{'__ALL__' if context_files is None else ','.join(sorted(context_files))}" + ":" + str(current_user or "") + ":" + str(output_dir or "") + ":" + str(output_role or "")
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
    now_time = hk_time.strftime("%Y-%m-%d %H:00")  # 稚 2026-10-05：降至「小時」精度（每小時才變一次），護前綴快取；需精確時間用 /admin exec date

    # ===== 讀取 soul 目錄下所有文件 =====
    soul_dir = os.path.expanduser(f"{work_dir}/soul")
    parts = []
    _exp_tail = None    # 稚 2026-10-05：EXP.md 殿後塊（做夢每日變動，移出可命中前綴）
    _tail_parts = []    # 稚 2026-10-05：殿後區（EXP.md / 產出位置），排在穩定塊之後

    if os.path.isdir(soul_dir):
        # 獲取目錄下所有文件（按文件名排序以保證確定性順序）
        for filename in sorted(os.listdir(soul_dir)):
            # 🔧 context_files 過濾：若指定了文件列表，只讀取列表中的文件
            if context_files is not None and filename not in context_files:
                continue
            # 🔧 [稚 2026-10-04] 隱藏狀態檔排除：檔名以點開頭者（如 .dream.json）一律不注入
            if filename.startswith("."):
                continue
            file_path = os.path.join(soul_dir, filename)
            # 只讀取普通文件，跳過子目錄
            if os.path.isfile(file_path):
                try:
                    with open(file_path, 'r', encoding='utf-8') as f:
                        content = f.read().strip()
                        if not _soul_gate_ok(agent_name, content):
                            continue  # [P1-5] 條件注入：群組未命中 → 本檔不注入
                        content = _strip_profile_dynamic(content)
                        # 🔧 [稚 2026-10-07／P0-1] EXP.md 退出常駐：只注入「最近經驗標題索引」，
                        #     全文改由房間讀檔工具按需取 soul/EXP.md（省約 2.4k tokens/輪）。
                        if filename == "EXP.md":
                            content = _exp_index_block(content)
                        if content:
                            # 稚 2026-10-05：EXP.md 不進可命中前綴（做夢每日改動），改收進殿後塊
                            if filename == "EXP.md":
                                _exp_tail = f"## 來自 {filename}\n\n{content}"
                            else:
                                parts.append(f"## 來自 {filename}\n\n{content}")
                except Exception as e:
                    logging.warning(f"讀取靈魂文件 {filename} 失敗: {e}")

    # 記憶分庫：只載入「當前對話者」專屬的 profile
    if current_user and (context_files is None or len(context_files) > 0):
        try:
            _ub = _load_user_profile_block(agent_name, current_user)
            if _ub:
                parts.append(_ub)
        except Exception as e:
            logging.warning(f"載入對話者 profile 失敗: {e}")

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
【工具使用守則】
- 呼叫任何工具前，請先把你「要對主人說的話」完整說完（用句號收尾），不要在「：」「以下」「我來…」這種半句後面就丟出工具呼叫。
- 高風險操作（exec / pip install / ollama_rm / cron）會先回傳確認碼，這是正常流程：請把確認訊息完整轉述給主人（務必原樣附上 /admin confirm <token> 那一行），不要自行改寫、省略或當成錯誤。
"""
    # P0(2026-10-01 侍女)：環境塊不再此處 append，改到 system prompt 最尾端（見 join(parts + [env_info])）

    # 產物三層落點注入（2026-09-28, output_router）
    if output_dir:
        try:
            _role = output_role
            if not _role and output_router:
                _role = output_router.classify_role(owner)
            _rule = output_router.human_rule(_role) if (output_router and _role) else ''
            _blk = (
                '【產出位置｜本回合所有產物一律寫這裡】\n'
                f'{output_dir}\n'
                '- 本回合任何新建檔案（報告、程式、JSON、圖片、暫存…）都寫進此目錄。\n'
                '- 除非主人明確指定其它路徑，否則不要寫進 agent 房間根目錄或其它位置。\n'
            )
            if _rule:
                _blk += f'- 分層規則：{_rule}\n'
            _tail_parts.append(_blk)
        except Exception:
            pass

    # ── P0：技能索引常駐注入（2026-09-27 衍，E1786）──
    # 把一行一句話的技能索引寫進 system prompt：找技能先看這張表，命中直接用，
    # 不要再一輪輪 skill list / search 亂翻。任何失敗都不得影響主流程。
    try:
        import skill_index as _skill_index
        _skill_blk = _skill_index.block()
        if _skill_blk:
            parts.append(_skill_blk)
    except Exception as _e:
        try:
            logging.getLogger(__name__).debug("skill_index 注入略過: %s", _e)
        except Exception:
            pass

    # ── P0：Agent 歸屬常駐注入（2026-09-30 靜）──
    # 讓 agent 知道自己「被誰建立／屬於誰」。任何失敗都不得影響主流程。
    try:
        _own_blk = _agent_owner_block(agent_name)
        if _own_blk:
            parts.append(_own_blk)
    except Exception as _e:
        try:
            logging.getLogger(__name__).debug("agent_owner 注入略過: %s", _e)
        except Exception:
            pass

    # 稚 2026-10-05：殿後區（EXP.md 每日變、產出位置每 job 變）→ 排在穩定塊之後、環境塊之前，護住可命中前綴
    if _exp_tail:
        _tail_parts.insert(0, _exp_tail)
    # 稚 2026-10-05：環境塊「小時級凍結」——同一小時內 env_info 逐字沿用快取，
    #   避免磁碟用量等抖動值每輪重寫而破壞可命中前綴（時間精度已為小時，與之一致）。
    try:
        _env_key = "%s|%s|%s" % (now_time, agent_name, work_dir)
        if _env_info_cache.get("key") == _env_key:
            env_info = _env_info_cache.get("text", env_info)
        else:
            _env_info_cache["key"] = _env_key
            _env_info_cache["text"] = env_info
    except Exception:
        pass
    context = "\n\n---\n\n".join(parts + _tail_parts + [env_info])  # P0：穩定塊在前、殿後塊次之、環境塊最尾

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
    # ===== 20261004 靜：語義搜尋/聯想詞每輪開銷預設關閉（要開請在 agent 設定加 MOK_AUTO_SEMANTIC=1）=====
    _sem_on = str((agent_config or {}).get("MOK_AUTO_SEMANTIC", os.environ.get("MOK_AUTO_SEMANTIC", "0"))).strip().lower() in ("1", "true", "yes", "on")
    if not _sem_on:
        return ""
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
    db_dir = os.path.dirname(HISTORY_DB_PATH)
    if db_dir:
        os.makedirs(db_dir, exist_ok=True)
    with closing(connect(HISTORY_DB_PATH)) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout = 30000')
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
        # ===== 多租戶（tenant）20260921：舊資料一律歸 'admin' =====
        try:
            conn.execute('ALTER TABLE conversation_history ADD COLUMN tenant TEXT')
            conn.execute("UPDATE conversation_history SET tenant = 'admin' WHERE tenant IS NULL OR tenant = ''")
            print('[tenant] conversation_history 新增 tenant 欄位（舊資料 -> admin）')
        except sqlite3.OperationalError:
            pass  # 欄位已存在（舊資料早已歸戶）
        conn.execute('CREATE INDEX IF NOT EXISTS idx_conv_tenant ON conversation_history (tenant, user_key)')
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
    _disable_code_index = str(os.environ.get("MOK_DISABLE_CODE_INDEX", "")).strip().lower() in {"1", "true", "yes", "on"}
    _web_only_mode = str(os.environ.get("MOK_WEB_ONLY", "")).strip().lower() in {"1", "true", "yes", "on"}
    _rebuild_code_index = str(os.environ.get("MOK_REBUILD_CODE_INDEX", "0")).strip().lower() in {"1", "true", "yes", "on"}

    if _disable_code_index or _web_only_mode or not _rebuild_code_index:
        logging.info("[code_index] 已停用（MOK_DISABLE_CODE_INDEX 或 MOK_WEB_ONLY 生效）")
    else:
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
    with closing(connect(HISTORY_DB_PATH)) as conn:
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
    agent_config = _resolve_agent_config(agent_config)
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
            num_predict=1024,
            disable_thinking=True,
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




# ===== 批次：日誌檔名改「LLM 一句話標題」（2026-09-27 衍）=====
async def _generate_log_title(user_msg: str, assistant_reply: str, agent_config: Dict = None) -> Optional[str]:
    """用輕量 LLM 產生一句話日誌標題（≤14 字），失敗回 None。

    目的：讓日誌檔名像 20260927_header餘額鈕改版.md，一眼看出這輪在做什麼，
    取代舊版「取回覆首行前 20 字」的粗糙做法。
    """
    agent_config = _resolve_agent_config(agent_config)
    owner = agent_config.get("MOK_ADMIN_NAME", "用戶")
    agent_name = agent_config.get("MOK_AGENT_NAME", "助手")
    prompt = f"""用繁體中文替這段對話取一個「一句話標題」，最多 14 個字，要像日誌檔名一樣精煉。
範例：header餘額鈕改版、修復登入閃退、新增語音按鈕、調整選單排序
只輸出標題本身：不要引號、不要標點、不要換行、不要解釋。

{owner}: {(user_msg or "")[:300]}
{agent_name}: {(assistant_reply or "")[:300]}"""
    try:
        token = agent_config.get("MOK_MODEL_token", "")
        if not token:
            return None
        result = await call_llm(
            prompt=prompt,
            user_id="system",
            stream=False,
            temperature=0.2,
            agent_config=agent_config,
            include_soul=False,
            num_predict=256,
            disable_thinking=True,
        )
        text = result if isinstance(result, str) else (result or {}).get("content", "")
        lines = [l.strip() for l in (text or "").strip().split("\n") if l.strip()]
        line = lines[0] if lines else ""
        line = re.sub(r"[^\w\u4e00-\u9fff-]", "", line)
        return line[:20] or None
    except Exception as e:
        logging.warning(f"[日誌標題] LLM 生成失敗: {type(e).__name__}: {str(e)}")
        return None
# ===== 結束 =====


async def add_to_history(user_id: str, user_msg: str, assistant_reply: str, agent_config: Dict = None):
    """將一輪對話存入數據庫（永久保存）"""
    agent_config = _resolve_agent_config(agent_config)
    owner = agent_config.get("MOK_ADMIN_NAME", "用戶")
    agent_name = agent_config.get("MOK_AGENT_NAME", "助手")
    unique_id = _get_unique_user_id(user_id, agent_name)
    _init_history_db()
    now = time.time()
    try:
        with closing(connect(HISTORY_DB_PATH)) as conn:
            cursor = conn.cursor()
            cursor.execute(
                'INSERT INTO conversation_history (user_key, role, content, timestamp, tenant) VALUES (?, ?, ?, ?, ?)',
                (unique_id, 'user', user_msg, now, str(user_id) if user_id else None)
            )
            user_rowid = cursor.lastrowid
            cursor.execute(
                'INSERT INTO conversation_history (user_key, role, content, timestamp, tenant) VALUES (?, ?, ?, ?, ?)',
                (unique_id, 'assistant', assistant_reply, now + 0.001, str(user_id) if user_id else None)
            )
            assistant_rowid = cursor.lastrowid

            full_text = f"{owner}: {user_msg}\n{agent_name}: {assistant_reply}"
            try:
                conn.execute('INSERT OR REPLACE INTO conversation_fts (rowid, content) VALUES (?, ?)', (user_rowid, full_text))
            except Exception as e:
                logging.error(f"FTS5 索引插入失敗: {e}, rowid={user_rowid}, text={full_text[:100]}")

            conn.commit()  # 【根治 database is locked】交易一在此先放鎖：以上 inserts 到此結束，之後 await AI 不再持有寫鎖
            try:
                summary, keywords = await _generate_conversation_summary(user_msg, assistant_reply, agent_config)
                if summary:
                    conn.execute(
                        'UPDATE conversation_history SET summary = ?, keywords = ? WHERE id = ?',
                        (summary, keywords, user_rowid)
                    )
                    conn.commit()  # 【根治 database is locked】交易二在此放鎖：UPDATE 完成即 commit，不再跨後續 await 持寫鎖
            except Exception as e:
                logging.warning(f"更新對話摘要失敗: {e}")
            # ===== 自動抽取記憶 facts（衝突消解寫入 user_memory）=====
            try:
                _mem_mod = tool_handler.get_tools().get("memory")
                if _mem_mod and hasattr(_mem_mod, "auto_extract_facts"):
                    await _mem_mod.auto_extract_facts(unique_id, user_msg, assistant_reply, agent_config)
                # ===== 同步維護 soul/user.md 雙段（static 長期事實 / dynamic 近期動態）=====
                #       統一入口：memory._maybe_refresh_profile（內含節流，且只動已 profile 化
                #       或 MOK_MEM_PROFILE_AUTO=1 的 agent，避免誤改其他 agent 的 soul/user.md）
                if _mem_mod and hasattr(_mem_mod, "_maybe_refresh_profile"):
                    _ag = agent_config.get("MOK_AGENT_NAME") if isinstance(agent_config, dict) else None
                    if _ag:
                        _mem_mod._maybe_refresh_profile(_ag, agent_config, unique_id)
            except Exception as e:
                logging.warning(f"自動記憶抽取失敗: {e}")
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
    with closing(connect(HISTORY_DB_PATH)) as conn:
        conn.execute('DELETE FROM conversation_history WHERE user_key = ?', (unique_id,))
        conn.commit()

def get_all_conversation_summary(user_id: str, agent_config: Dict = None):
    _init_history_db()
    agent_config = _resolve_agent_config(agent_config)
    owner = agent_config.get("MOK_ADMIN_NAME", "用戶")
    agent_name = agent_config.get("MOK_AGENT_NAME", "助手")
    unique_id = _get_unique_user_id(user_id, agent_name)
    with closing(connect(HISTORY_DB_PATH)) as conn:
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
    agent_config = _resolve_agent_config(agent_config)
    owner = agent_config.get("MOK_ADMIN_NAME", "用戶")
    agent_name = agent_config.get("MOK_AGENT_NAME", "助手")
    unique_id = _get_unique_user_id(user_id, agent_name)
    with closing(connect(HISTORY_DB_PATH)) as conn:
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
    with closing(connect(db_path)) as conn:
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
    with closing(connect(TOKEN_DB_PATH)) as conn:
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
    with closing(connect(TOKEN_DB_PATH)) as conn:
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



# 全局 OpenAI 客戶端（按 event loop 隔離，避免多線程多 loop 互相覆蓋導致跨 loop 呼叫崩潰）
_openai_clients = {}          # {loop_id: client}
_openai_clients_lock = threading.Lock()

# ============ 🩹 2026-10-01 稚：上游內容風控（Content Exists Risk）自癒 ============
_CONTENT_RISK_SIGNS = (
    "Content Exists Risk",
    "content_policy_violation",
    "content_filter",
    "data_inspection_failed",
)


class _ContentRiskError(RuntimeError):
    """上游內容風控攔截（與網路中斷、額度問題區分，可精簡上下文後重試）。"""
    pass


def _is_content_risk_error(err) -> bool:
    """判斷例外是否為上游內容風控攔截。"""
    try:
        s = str(err)
    except Exception:
        return False
    low = s.lower()
    return any(sign.lower() in low for sign in _CONTENT_RISK_SIGNS)


def _is_rate_limit_error(err) -> bool:
    """判斷例外是否為上游 429 / rate limit（供同 key 退避重試用；嚴禁轉 key）。"""
    try:
        if getattr(err, "status_code", None) == 429:
            return True
    except Exception:
        pass
    _n = type(err).__name__.lower()
    if "ratelimit" in _n or "toomany" in _n:
        return True
    try:
        _s = str(err).lower()
    except Exception:
        return False
    return ("429" in _s) or ("too many requests" in _s) or ("rate limit" in _s)


def _rate_limit_backoff(attempt, err=None) -> float:
    """429 退避秒數：優先尊重上游 Retry-After，否則指數退避（上限 30s）。"""
    _ra = None
    try:
        _resp = getattr(err, "response", None)
        if _resp is not None:
            _ra = _resp.headers.get("retry-after") or _resp.headers.get("Retry-After")
    except Exception:
        _ra = None
    if _ra:
        try:
            return min(float(_ra) + 0.5, 60.0)
        except Exception:
            pass
    return min(2.0 ** min(int(attempt), 5), 30.0)


def _shrink_tool_messages(msgs, keep_chars: int = 800, min_len: int = 2000, max_msgs: int = 3) -> int:
    """風控自癒：把上下文中最肥的 tool 訊息截短（只動 role='tool'），回傳處理過的訊息數。

    上游（如 DeepSeek）對超長/敏感的工具輸出回 400 Content Exists Risk 時，
    不必讓整條任務死掉——先把最佔空間的工具輸出縮成摘要再重試一次。
    完整內容仍可用 code_index get_chunk / grep 分段取回。
    """
    if not isinstance(msgs, list):
        return 0
    cands = [
        m for m in msgs
        if isinstance(m, dict)
        and m.get("role") == "tool"
        and isinstance(m.get("content"), str)
        and len(m["content"]) > min_len
    ]
    if not cands:
        return 0
    cands.sort(key=lambda m: len(m["content"]), reverse=True)
    done = 0
    for m in cands[:max_msgs]:
        original = m["content"]
        m["content"] = (
            original[:keep_chars]
            + u"\n…（原始 %d 字元：因上游內容風控已截斷；需要細節請用 code_index get_chunk 分段讀取，勿一次塞入整份檔案）" % len(original)
        )
        done += 1
    return done
# ============ 風控自癒結束 ============


def _get_openai_client(api_key: str, base_url: str):
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    lid = id(loop)
    with _openai_clients_lock:
        client = _openai_clients.get(lid)
        if client is None or (loop is not None and loop.is_closed()):
            client = openai.AsyncOpenAI(
                api_key=api_key,
                base_url=base_url,
                default_headers={
                    "HTTP-Referer": "https://github.com/MOK2026/MOKAGI",
                    "X-Title": "MOK AGI"
                }
            )
            _openai_clients[lid] = client
    return client



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

    _dbg(f"""========== [call_llm 統一的 LLM 調用接口] ==========
========== [prompt] ==========
{prompt}
========== [system_prompt] ==========
{system_prompt}
""")

    agent_config = _resolve_agent_config(agent_config)  # 向後兼容
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
    max_tokens = override_options.get("num_predict", int(agent_config.get("MOK_num_predict", 32768)))
    # P2（2026-10-01 靜）：結構化輸出轉發 —— OpenAI 相容路徑用 response_format、Ollama 原生路徑用 format
    _rf_kwargs = {}
    if override_options.get("response_format"):
        _rf_kwargs["response_format"] = override_options["response_format"]
    # 2026-10-04 凜：關推理旗標（供輔助呼叫使用）。disable_thinking=True 時，
    # 對 OpenAI 相容上游以 extra_body 傳 thinking.type=disabled（DeepSeek 官方 API 實測推理歸零）；
    # 先 pop 掉，避免被下面的 Ollama options.update 吃到。未傳時一切不變。
    if override_options.pop("disable_thinking", False):
        _rf_kwargs["extra_body"] = {"thinking": {"type": "disabled"}}
    


    # ---- 測試模式處理 ----
    test_mode = agent_config.get("MOK_TEST_MODE", "0") == "1"
    if test_mode and not _test_mode_skip_confirm:
        # 生成唯一 context_id
        import uuid
        context_id = f"test_{uuid.uuid4().hex[:8]}"
        # 儲存前先清理過期條目，避免記憶體無限增長
        _cleanup_pending_confirm()
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
            async def _stream_gen_once(_msgs_override=None, _kw_override=None):
                try:
                    _create_kwargs = dict(
                        model=model_name,
                        messages=(_msgs_override if _msgs_override is not None else msgs),
                        stream=True, timeout=httpx.Timeout(connect=10.0, read=180.0, write=30.0, pool=180.0),
                        tools=tools_def,
                        temperature=temperature,
                        max_tokens=max_tokens,
                        **_rf_kwargs,
                    )
                    # P1（2026-10-01 侍女）：要求尾端回傳 usage 才能量測快取命中；
                    # 上游若不支援 stream_options，自動退回不帶此參數再試一次。
                    # (2026-10-04 空正文自癒) 續答時可覆寫 tools / max_tokens
                    if _kw_override:
                        _create_kwargs.update(_kw_override)
                    _create_kwargs.update(_stream_options_kwargs())
                    try:
                        response = await client.chat.completions.create(**_create_kwargs)
                    except Exception as _so_err:
                        if "stream_options" in str(_so_err) and _create_kwargs.pop("stream_options", None) is not None:
                            _disable_stream_options(str(_so_err))
                            logging.warning("上游不支援 stream_options(include_usage)，已退回：%s", _so_err)
                            response = await client.chat.completions.create(**_create_kwargs)
                        else:
                            raise
                    # 用於拼接 tool_calls
                    tool_calls_chunks = {}  # index -> {id, name, arguments}
                    # (2026-10-08 稚) thinking 模式：本輪思考需隨 tool_calls 一起回傳上游，
                    # 否則第二輪（工具續答）上游會 400 invalid_request_error。
                    _round_reasoning = ""
                    async for chunk in _stream_with_usage(response, log_token_usage, {"user_id": user_id, "agent_name": agent_config.get("MOK_AGENT_NAME", "unknown"), "model_name": model_name, "conversation_id": conversation_id, "workflow_id": workflow_id}):
                        if not chunk.choices:
                            continue
                        delta = chunk.choices[0].delta
                        # 處理思考內容（reasoning）
                        # (2026-10-04) 思考欄位三路兜底（部分上游用 reasoning / model_extra）
                        _rc = getattr(delta, 'reasoning_content', None) or getattr(delta, 'reasoning', None)
                        if not _rc:
                            _extra_d = getattr(delta, 'model_extra', None) or {}
                            _rc = _extra_d.get('reasoning_content') or _extra_d.get('reasoning')
                        if _rc:
                            _round_reasoning += _rc
                            yield {"type": "think", "content": _rc}
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
                        yield {"type": "tool_calls", "calls": tool_calls_list,
                               "reasoning": _round_reasoning or None}
                except Exception as e:
                    if isinstance(e, (httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError)):
                        # 可重試的串流讀取中斷：往上拋，交由外層包裝器決定是否重試
                        raise
                    if _is_content_risk_error(e):
                        # 🩹 2026-10-01 稚：上游內容風控（如 DeepSeek 400 Content Exists Risk）
                        #    往上拋，由 stream_gen 精簡工具輸出後重試，別讓整條任務直接死在風控上。
                        raise _ContentRiskError(str(e))
                    logging.exception("OpenAI 流式調用失敗")
                    yield {"type": "reply", "content": f"❌ 生成失敗: {str(e)}"}

            async def stream_gen():
                # (C) 自癒 v2（2026-10-02 稚）：上游串流中斷 → 自動重試 + 斷點續寫
                #   ① ReadError / ReadTimeout / RemoteProtocolError 最多重試 3 次（指數退避 1s/2s/4s）
                #   ② 若「已輸出部分內容才中斷」→ 續寫模式：把已輸出內容當 assistant 前綴請模型接續，
                #      並對新串流做重疊去重，避免畫面出現重複文字。
                def _trim_stream_overlap(prev_text, new_text, max_k=600):
                    if not prev_text or not new_text:
                        return new_text
                    _tail = prev_text[-max_k:]
                    _m = min(len(_tail), len(new_text))
                    for _k in range(_m, 0, -1):
                        if _tail[-_k:] == new_text[:_k]:
                            return new_text[_k:]
                    return new_text
                _emitted = False
                _cr_used = False   # 🩹 2026-10-01：內容風控自癒每輪只做一次
                _emitted_text = ""          # 已輸出的正文（續寫前綴）
                _max_attempts = 3
                _attempt = 0
                _salvaged = False   # (2026-10-04) 空正文自癒每輪只做一次
                while _attempt < _max_attempts:
                    _attempt += 1
                    _resume_msgs = None
                    if _emitted_text:
                        _resume_msgs = list(msgs) + [
                            {"role": "assistant", "content": _emitted_text},
                            {"role": "user", "content": "（系統自動續寫）上一則回覆因網路中斷被截斷。請直接從斷點接續輸出，不要重複任何已輸出的內容，也不要加前言或道歉。"},
                        ]
                    try:
                        _buf = ""
                        _pending_trim = bool(_resume_msgs) and bool(_emitted_text)
                        _round_think = ""    # (2026-10-04) 本輪思考（判斷是否被 reasoning 吃光額度）
                        _round_tools = 0     # (2026-10-04) 本輪工具呼叫數
                        async for _ev in _stream_gen_once(_resume_msgs):
                            if not isinstance(_ev, dict) or _ev.get("type") != "reply":
                                if isinstance(_ev, dict):
                                    if _ev.get("type") == "think":
                                        _round_think += _ev.get("content") or ""
                                    elif _ev.get("type") == "tool_calls":
                                        _round_tools += 1
                                yield _ev
                                continue
                            _emitted = True
                            _c = _ev.get("content") or ""
                            if _pending_trim:
                                _buf += _c
                                if len(_buf) < 400:
                                    continue
                                _c = _trim_stream_overlap(_emitted_text, _buf)
                                _pending_trim = False
                                _buf = ""
                            if _c:
                                _emitted_text += _c
                                yield {"type": "reply", "content": _c}
                        if _pending_trim and _buf:
                            _c2 = _trim_stream_overlap(_emitted_text, _buf)
                            if _c2:
                                _emitted_text += _c2
                                yield {"type": "reply", "content": _c2}
                        # (2026-10-04 根治空正文 by mokagi說明) 整輪只吐思考、無正文亦無工具呼叫
                        #   -> 上游把輸出額度全用在 reasoning（finish_reason='length'）。
                        #   自動停用工具、補一句「直接作答」續答一次；仍空則明示使用者並停止。
                        if (not _emitted) and (not _round_tools) and (not _salvaged):
                            _salvaged = True
                            logging.warning("上游整輪無正文（僅思考 %d 字）→ 關閉工具續答一次", len(_round_think))
                            yield {"type": "think", "content": "⚠️ 上游本輪只回思考、正文為空 → 自動關閉工具、要求直接作答一次…"}
                            _nudge_msgs = list(msgs)
                            if _round_think:
                                _nudge_msgs.append({"role": "assistant", "content": _round_think})
                            _nudge_msgs.append({"role": "user", "content": (
                                "（系統自動續答）你上一輪只產出了思考、沒有正文（輸出額度被思考吃光）。"
                                "請直接輸出要給使用者看的最終答案：不要輸出推理過程、不要道歉、不要呼叫工具。"
                            )})
                            try:
                                _bump = max(int(max_tokens or 0), 8192)
                                async for _ev2 in _stream_gen_once(_nudge_msgs, {"tools": None, "max_tokens": _bump}):
                                    if not isinstance(_ev2, dict) or _ev2.get("type") != "reply":
                                        yield _ev2
                                        continue
                                    _c3 = _ev2.get("content") or ""
                                    if _c3:
                                        _emitted = True
                                        _emitted_text += _c3
                                        yield {"type": "reply", "content": _c3}
                            except Exception as _se:
                                logging.warning("空正文續答失敗：%s", _se)
                            if not _emitted:
                                yield {"type": "reply", "content": (
                                    "⚠️ 上游連續兩輪只產出思考、正文為空（輸出額度被 reasoning 吃光）。\n"
                                    "這次沒有可顯示的答案，已停止以免繼續燒 token。\n"
                                    "建議：① 直接說「請直接回答、不要思考」；② 或換模型（/admin set_model）。"
                                )}
                        return
                    except Exception as _e:
                        if isinstance(_e, _ContentRiskError):
                            if (not _cr_used) and (not _emitted):
                                _cr_used = True
                                _n = _shrink_tool_messages(msgs)
                                if _n:
                                    logging.warning(f"上游內容風控攔截，已精簡 {_n} 段工具輸出後重試")
                                    yield {"type": "think", "content": f"⚠️ 上游內容風控（Content Exists Risk）：已精簡 {_n} 段工具輸出後重試"}
                                    _emitted = False
                                    continue
                            logging.warning(f"上游內容風控攔截且無可精簡的工具輸出：{_e}")
                            yield {"type": "reply", "content": (
                                "⚠️ 上游內容風控（Content Exists Risk）擋下了這一輪請求，本次任務在此停止。\n"
                                "建議：① 不要一次把整份檔案塞進上下文，改用 grep / code_index get_chunk 分段讀取；"
                                "② 或換一個模型（/admin set_model）再試一次。\n"
                                "任務完成"
                            )}
                            return
                        _is_429 = _is_rate_limit_error(_e)
                        if _is_429:
                            # 429：同一把 key 退避重試（絕不轉 key，以免打掉 prompt 快取命中）；上限放寬到 5 次
                            _max_attempts = max(_max_attempts, 5)
                        _retryable = isinstance(_e, (httpx.ReadError, httpx.ReadTimeout, httpx.RemoteProtocolError)) or _is_429
                        _tag = "續寫" if _emitted_text else "整輪"
                        if _retryable and _attempt < _max_attempts:
                            if _is_429:
                                _wait = _rate_limit_backoff(_attempt, _e)
                                logging.warning("上游 429 限流（同 key 退避，第 %d 次）→ %.1fs 後重試：%s", _attempt, _wait, _e)
                                yield {"type": "think", "content": f"⚠️ 上游限流（429），同一把 key 退避 {_wait:.0f}s 後重試…（{_attempt}/{_max_attempts - 1}）"}
                            else:
                                _wait = min(2 ** (_attempt - 1), 4)
                                logging.warning("OpenAI 串流中斷（%s，第 %d 次）→ %s自動重試", type(_e).__name__, _attempt, "續寫" if _emitted_text else "整輪")
                                yield {"type": "think", "content": f"⚠️ 上游連線中斷（{type(_e).__name__}），{_tag}自動重試中…（{_attempt}/{_max_attempts - 1}）"}
                            await asyncio.sleep(_wait)
                            continue
                        logging.exception("OpenAI 流式調用失敗")
                        if _is_429:
                            _msg = "上游限流（429）持續：已用同一把 key 退避重試多次仍失敗，請稍後再試。"
                        else:
                            _msg = "上游串流連線中斷，請重試或稍後再試" if _retryable else str(_e)
                        if _emitted_text:
                            _msg += "（已輸出的內容會保留，回「繼續」可讓我從斷點接續）"
                        yield {"type": "reply", "content": f"❌ 生成失敗: {_msg}"}
                        return
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
                        **_rf_kwargs,
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
                            extra=_cache_extra(usage, "openai_api")  # P1：附帶快取命中 token
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
                                    async def _run_tool(_h=handler, _ta=tool_args, _tn=tool_name):
                                        return await safe_autofix_retry(
                                            action_func=_h,
                                            action_args=(),
                                            action_kwargs={"args": _ta, "chat_id": user_id, "agent_config": agent_config},
                                            error_info_builder=lambda e, kwargs: {
                                                "tool_name": _tn,
                                                "original_args": json.dumps(kwargs.get("args", {}), ensure_ascii=False),
                                                "error": f"{type(e).__name__}: {str(e)}",
                                            },
                                            autofix_extra_args={"user_id": user_id, "agent_config": agent_config}
                                        )
                                    try:
                                        from audit_layer import audited_call as _audited_call
                                        raw_result = await _audited_call(tool_name, tool_args, user_id, agent_config, _run_tool)
                                    except ImportError:
                                        raw_result = await _run_tool()

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
                                ],
                                # (2026-10-08 稚) thinking 模式：帶 tool_calls 的 assistant 訊息
                                # 必須連 reasoning_content 一起回傳，否則下一輪 400。
                                "reasoning": (getattr(message, "reasoning_content", None)
                                              or getattr(message, "reasoning", None))
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
                                    **_rf_kwargs,
                                },
                                max_attempts=4,   # L2：transient 退避重試（不進 autofix，避免 autofix 遞歸）
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
        
        # 兼容舊配置：若 api_url 為 OpenAI 兼容的 /v1 端點，轉為 Ollama 原生 /api/generate
        if api_url and '/v1' in api_url:
            api_url = api_url.split('/v1')[0] + '/api/generate'
        
        # 從 agent_config 讀取 Ollama 參數
        ollama_options = {
            "num_ctx": int(agent_config.get("MOK_num_ctx", 16384)),
            "num_predict": int(agent_config.get("MOK_num_predict", 32768)),
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
            _dbg("\n========== [工具調用] ==========")
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
        # P2（2026-10-01 靜）：Ollama 原生結構化輸出（呼叫方傳 format 時才加）
        if override_options.get("format"):
            payload["format"] = override_options["format"]

        # 思考開關：模型名稱命中 MOK_no_think_models（逗號分隔子字串）時關閉 thinking
        _no_think = str(agent_config.get("MOK_no_think_models", "") or "")
        if _no_think:
            _m = (model_name or "").lower()
            if any(_t.strip() and _t.strip().lower() in _m for _t in _no_think.split(",")):
                payload["think"] = False

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
# P1（2026-10-01 市場調查侍女）：快取命中量測 + 串流 usage 收集
from mok_cache_metrics import (
    cache_extra as _cache_extra,
    stream_with_usage as _stream_with_usage,
    stream_options_kwargs as _stream_options_kwargs,
    disable_stream_options as _disable_stream_options,
)
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


async def call_tool_handler(handler, *args, **kwargs):
    """調用工具 handler；同步工具移到背景執行緒，避免阻塞串流服務。"""
    if inspect.iscoroutinefunction(handler):
        return await handler(*args, **kwargs)
    try:
        return await asyncio.wait_for(
            asyncio.to_thread(handler, *args, **kwargs),
            timeout=120
        )
    except asyncio.TimeoutError:
        return "❌ 工具執行逾時（120 秒），已停止等待；請縮小搜尋範圍後重試。"
    except Exception as exc:
        return f"❌ 工具執行失敗：{type(exc).__name__}: {exc}"



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
    agent_config = _resolve_agent_config(agent_config)
    print(f"自然化工具結果: tool={tool_name}, raw_result={raw_result[:100]}...")
    # 查找工具模塊
    target_mod = None
    for mod in tool_handler.get_tools().values():
        if hasattr(mod, "PLUGIN_INFO"):
            schema = mod.PLUGIN_INFO.get("tool_schema", {})
            if schema.get("name") == tool_name:
                target_mod = mod
                break
            for _sub in mod.PLUGIN_INFO.get("sub_tools", []) or []:
                if isinstance(_sub, dict) and _sub.get("name") == tool_name:
                    target_mod = mod
                    break
            if target_mod:
                break
    if target_mod and hasattr(target_mod, "PLUGIN_INFO"):
        func_name = target_mod.PLUGIN_INFO.get("naturalize_func")
        if func_name:
            naturalize_func = getattr(target_mod, func_name, None)
            if naturalize_func:
                try:
                    result = await call_tool_handler(
                        naturalize_func,
                        user_text=user_text,
                        raw_result=raw_result,
                        ollama_api=agent_config.get("MOK_MODEL_url", "http://localhost:11434/api/generate"),
                        model_name=agent_config.get("MOK_MODEL_NAME", "minimax-m3:cloud"),
                        temp_msg=None,
                        context=None
                    )
                    return result
                except Exception as e:
                    logging.warning(f"自然化函數調用失敗: {e}")
                    return await recovery.naturalize_tool_result_fallback(user_text, tool_name, raw_result, agent_config=agent_config)
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
    if len(raw_result) > 3500:
        # 保留頭尾（前2000 + 中略 + 後1000），避免只取頭造成「中間窗口」
        raw_result = raw_result[:2000] + " …中略… " + raw_result[-1000:]
    return raw_result




# ----------------------------------------------------------------------
# 工具調用相關函數
# 1.  / 直接命令處理
#（複用 tool_handler.process_message）
# ----------------------------------------------------------------------
async def handle_direct_command(user_text: str, user_id: str, agent_config: Optional[Dict] = None, platform: Optional[str] = None) -> Optional[str]:
    if not user_text.startswith('/'):
        return None
    agent_config = _resolve_agent_config(agent_config)
    ollama_api = agent_config.get("MOK_MODEL_url", "http://localhost:11434/api/generate")
    model_name = agent_config.get("MOK_MODEL_NAME", "minimax-m3:cloud")
    result = await tool_handler.process_message(
        user_text=user_text,
        chat_id=user_id,
        ollama_api=ollama_api,
        model_name=model_name,
        cmd_map=tool_handler.get_cmd_map(),
        tools=tool_handler.get_tools(),
        agent_config=agent_config,
        platform=platform
    )
    return result




# ===== 人話化：將 admin 確認/執行結果轉為當前 agent 角色口吻（任何侍女通用） =====
async def _humanize_admin_message(raw_text: str, agent_config: dict, purpose: str = "confirm") -> str:
    """把 admin 的機械訊息轉成當前 agent 角色的人話。LLM 失敗時回傳空字串，由調用方 fallback 到原文字。"""
    try:
        agent_name = agent_config.get("MOK_AGENT_NAME", "助手")
        owner = agent_config.get("MOK_ADMIN_NAME", "主人")
        if purpose == "confirm":
            sys_prompt = (
                f"你是{agent_name}，正在向{owner}匯報一個需要授權的操作。\n"
                "請用你自己的角色口吻（人話、自然親切、符合角色性格），把下面的操作內容轉述給主人，"
                "說清楚這是什麼操作、為何需要確認，並請主人回覆確認。\n"
                "【硬性要求】最後必須原樣附上確認指令那一行（以 /admin confirm 開頭），一字不改。\n"
                "不要使用 Markdown 程式碼塊，不要添加額外解釋。"
            )
        else:
            sys_prompt = (
                f"你是{agent_name}，正在向{owner}匯報剛才高風險操作的執行結果。\n"
                "請用你自己的角色口吻（人話、自然親切、符合角色性格），簡潔地把執行結果轉述給主人，"
                "讓主人清楚事情辦好了或失敗了。\n"
                "不要使用 Markdown 程式碼塊，不要添加額外解釋。"
            )
        result = await call_llm(
            prompt=f"【原始訊息】\n{raw_text}\n\n請用人話轉述。",
            system_prompt=sys_prompt,
            agent_config=agent_config,
            include_soul=True,
            stream=False,
        )
        if isinstance(result, dict):
            result = result.get("content", "") or ""
        if isinstance(result, str):
            result = result.strip()
            if len(result) > 3:
                return result
    except Exception:
        pass
    return ""


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

def save_pending_task(user_id, messages, goal, max_iterations, iteration, agent_name, continue_code=None, status="running", waiting_for=None, pending_input=None):
    unique_key = _get_unique_user_id(user_id, agent_name)
    if continue_code is None:
        continue_code = hashlib.md5(f"{user_id}_{time.time()}_{goal}".encode()).hexdigest()[:12]
    task = {
        "goal": goal,
        "messages": messages,
        "max_iterations": max_iterations,
        "iteration": iteration,
        "timestamp": time.time(),
        "status": status,
        "waiting_for": waiting_for,
        "pending_input": pending_input,
    }
    if unique_key not in _pending_task:
        _pending_task[unique_key] = {}
    _pending_task[unique_key][continue_code] = task
    # 寫入文件（MOK_ENABLE_PENDING_TASK=0 停用，_job.json 不再生成/膨脹；預設 0，/continue 暫不使用）
    if str(_agent_config.get("MOK_ENABLE_PENDING_TASK", "0")) != "0":
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
    if status == "waiting_for_user":
        return f"📌 已暫停任務，等待你的補充。\n繼續碼：`{continue_code}`\n請直接補充內容，或輸入：`/continue {continue_code} 你的補充`"
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


def mark_task_waiting_for_user(user_id, agent_name, continue_code, waiting_for, details=None):
    """把 task 設成 waiting_for_user，方便 browser / captcha / 路徑修正等場景。"""
    task = load_pending_task(user_id, continue_code, agent_name)
    if task is None:
        return False
    task["status"] = "waiting_for_user"
    task["waiting_for"] = waiting_for
    task["pending_input"] = details
    task["timestamp"] = time.time()
    save_pending_task(
        user_id,
        task.get("messages", []),
        task.get("goal", "未知任務"),
        task.get("max_iterations", 10),
        task.get("iteration", 0),
        agent_name,
        continue_code=continue_code,
        status="waiting_for_user",
        waiting_for=waiting_for,
        pending_input=details,
    )
    return True


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
    with closing(connect(EXPERIENCE_DB_PATH)) as conn:
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
    agent_config = _resolve_agent_config(agent_config)
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
        # 🔧 修復 Event loop is closed：保存原事件循環，避免污染主線程 loop
        try:
            old_loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                old_loop = asyncio.get_event_loop()
            except Exception:
                old_loop = None
        # 🔧 OpenAI client 已按 event loop 隔離（見 _get_openai_client），無需手動重置單例
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            prompt = f"用繁體中文總結這個任務的經驗（含成功/失敗原因），不超過30字：\n目標：{goal}\n結果：{outcome}"
            result = loop.run_until_complete(call_llm(
                prompt=prompt,
                user_id=user_id,
                stream=False,
                temperature=0.3,
                agent_config=agent_config,
                include_soul=False,
                num_predict=512,
                disable_thinking=True,
            ))
        finally:
            loop.close()
            # 🔧 恢復原事件循環，避免主線程 get_event_loop() 指向已關閉的 loop
            if old_loop is not None:
                asyncio.set_event_loop(old_loop)
        # 🔧 清理臨時 loop 對應的 client，避免綁定已關閉循環的 client 殘留
        try:
            _openai_clients.pop(id(loop), None)
        except Exception:
            pass
        if isinstance(result, dict):
            result = result.get("content", "")
        lines = result.strip().split('\n') if result else []
        summary = lines[0] if lines else None
        keywords = lines[1] if len(lines) > 1 else None
    except Exception as e:
        logging.warning(f"[經驗學習] 生成摘要失敗: {e}")
    
    unique_key = _get_unique_user_id(user_id, agent_name)
    now = time.time()
    
    with closing(connect(EXPERIENCE_DB_PATH)) as conn:
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

    with closing(connect(EXPERIENCE_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        sql = '''
            SELECT e.id, e.goal, e.outcome, e.tool_sequence, e.error_message, e.summary, e.keywords
            FROM experience_log e
            JOIN experience_fts f ON e.id = f.rowid
            WHERE e.user_key = ? AND e.agent_name = ? AND experience_fts MATCH ?
        '''
        _safe = re.sub(r"[^\w\u4e00-\u9fff\s]", " ", query or "", flags=re.UNICODE); query = " ".join("\"" + t + "\"" for t in _safe.split() if t) if _safe.split() else ""; params = [unique_key, agent_name, query]
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
# ======================================================================
# 🩹 2026-10-03 by 稚：文字版工具呼叫的後備解析（治本補丁 B / C）
# ----------------------------------------------------------------------
# 事故：模型把工具呼叫「當成文字寫出來」（重啟後的無工具續寫、或上游 proxy
#       沒回 tool_calls 事件），主迴圈因 tool_calls 為空，就把這段 JSON 當成
#       一般回覆 → 反問「是否完成」→ 模型自答完成 → 結案，任務斷在半路。
# 對策：正文尾端若是一段（可解析的）工具呼叫 JSON，還原成真正的工具呼叫；
#       解析不出來但明顯是工具呼叫文字時，一律「不得判定為完成」。
# ======================================================================
_MOK_TC_ARG_KEYS = ("arguments", "parameters", "args", "tool_args")


def _mok_scan_json_objects(s):
    """掃出字串中所有『頂層』JSON 物件，回傳 [(start, end, raw), ...]。"""
    out = []
    depth = 0
    start = -1
    in_str = False
    esc = False
    for i, ch in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif ch == "\\":
                esc = True
            elif ch == '"':
                in_str = False
            continue
        if ch == '"':
            in_str = True
        elif ch == "{":
            if depth == 0:
                start = i
            depth += 1
        elif ch == "}":
            if depth > 0:
                depth -= 1
                if depth == 0 and start >= 0:
                    out.append((start, i + 1, s[start:i + 1]))
                    start = -1
    return out


def _mok_is_toolcall_obj(obj):
    if not isinstance(obj, dict):
        return False
    nm = obj.get("name")
    if not (isinstance(nm, str) and nm.strip()):
        return False
    return any(k in obj for k in _MOK_TC_ARG_KEYS)


def _mok_normalize_tool_args(name, args):
    """把模型手寫的參數正規化成工具吃得下的形狀（例：admin 的 command → args）。"""
    if not isinstance(args, dict):
        return {}
    args = dict(args)
    try:
        nm = str(name or "")
        if nm.startswith("admin"):
            for k in ("command", "cmd", "shell", "sh"):
                if k in args and "args" not in args:
                    args["args"] = args.pop(k)
                    break
            if "action" not in args and "args" in args:
                args["action"] = "exec"
    except Exception:
        pass
    return args


def _mok_extract_json_text_tool_calls(text):
    """把「被寫成文字的工具呼叫」還原成 [{'id','name','arguments'}]。

    只在正文【尾端】是工具呼叫 JSON 時成立（可含 markdown 圍欄、可連續多物件），
    避免誤判正文中間的 JSON 範例。取不到就回傳 []。
    """
    try:
        if not text or not isinstance(text, str):
            return []
        s = text.rstrip()
        while s.endswith("```"):
            s = s[:-3].rstrip()
        objs = _mok_scan_json_objects(s)
        if not objs:
            return []
        picked = []
        cursor = len(s)
        for st, en, raw in reversed(objs):
            if s[en:cursor].strip(" \t\r\n,;`[]"):
                break
            try:
                obj = json.loads(raw)
            except Exception:
                break
            if isinstance(obj, list):
                if not obj or not all(_mok_is_toolcall_obj(o) for o in obj):
                    break
                picked = list(obj) + picked
                cursor = st
                continue
            if not _mok_is_toolcall_obj(obj):
                break
            picked.insert(0, obj)
            cursor = st
        calls = []
        for i, o in enumerate(picked, 1):
            nm = str(o.get("name") or "").strip().strip('`"\'')
            args = None
            for k in _MOK_TC_ARG_KEYS:
                if o.get(k) is not None:
                    args = o.get(k)
                    break
            if isinstance(args, str):
                try:
                    args = json.loads(args)
                except Exception:
                    args = {"_raw": args}
            if not isinstance(args, dict):
                args = {}
            calls.append({"id": "call_text_%d" % i, "name": nm,
                          "arguments": _mok_normalize_tool_args(nm, args)})
        return calls
    except Exception:
        return []


# ===== DSML 兜底解析（2026-10-04 by 稚 E2589）=====
_MOK_DSML_TAG = re.compile(
    r'<\s*(/?)\s*\|*｜*DSML｜*\|*\s*(invoke|parameter)\b([^>]*)>',
    re.I)


def _mok_dsml_value(v):
    '''DSML 參數值清理：去頭尾空白；若明顯被 HTML 轉義則還原常見實體。'''
    if not isinstance(v, str):
        return v
    v = v.strip('\n')
    if ('&lt;' in v or '&gt;' in v or '&quot;' in v or '&#39;' in v
            or '&#34;' in v or '&apos;' in v):
        try:
            import html as _html
            v = _html.unescape(v)
        except Exception:
            pass
    return v.strip()


def _mok_scan_dsml_calls(text):
    '''DSML 兜底：把模型吐出的 DSML invoke/parameter 標記還原成工具呼叫。'''
    if not text or not isinstance(text, str) or 'DSML' not in text:
        return []
    calls = []
    cur_name = None
    cur_args = None
    cur_pname = None
    cur_pstart = None
    for m in _MOK_DSML_TAG.finditer(text):
        closing = bool(m.group(1))
        tagname = (m.group(2) or '').lower()
        rest = m.group(3) or ''
        if tagname == 'invoke' and not closing:
            nm = re.search(r'name\s*=\s*[\x22\x27]([^\x22\x27]+)[\x22\x27]', rest)
            cur_name = nm.group(1).strip() if nm else ''
            cur_args = {}
        elif tagname == 'parameter' and not closing:
            pm = re.search(r'name\s*=\s*[\x22\x27]([^\x22\x27]+)[\x22\x27]', rest)
            cur_pname = pm.group(1).strip() if pm else ''
            cur_pstart = m.end()
        elif tagname == 'parameter' and closing:
            if cur_args is not None and cur_pname and cur_pstart is not None:
                cur_args[cur_pname] = _mok_dsml_value(text[cur_pstart:m.start()])
            cur_pname = None
            cur_pstart = None
        elif tagname == 'invoke' and closing:
            if cur_name:
                calls.append((cur_name, cur_args or {}))
            cur_name = None
            cur_args = None
    if cur_name:
        calls.append((cur_name, cur_args or {}))
    out = []
    for i, (nm, args) in enumerate(calls, 1):
        out.append({'id': 'call_dsml_%d' % i, 'name': nm,
                    'arguments': _mok_normalize_tool_args(nm, args)})
    return out


def extract_text_tool_calls(text):
    '''向後相容入口：先試 JSON 文字工具呼叫，再試 DSML 標記。'''
    calls = _mok_extract_json_text_tool_calls(text)
    if calls:
        return calls
    return _mok_scan_dsml_calls(text)


def looks_like_trailing_tool_call(text):
    """寬鬆版：正文尾端「看起來是」未執行的工具呼叫（連 JSON 破損也認）。"""
    try:
        if not text or not isinstance(text, str):
            return False
        if extract_text_tool_calls(text):
            return True
        s = text.rstrip().rstrip('`').rstrip()
        i = max(s.rfind('"name"'), s.rfind("'name'"))
        if i < 0 or (len(s) - i) > 500:
            return False
        tail = s[i:]
        if not any(k in tail for k in ('"arguments"', '"parameters"', '"args"',
                                       "'arguments'", "'parameters'", "'args'")):
            return False
        return not any(p2 in tail for p2 in ('。', '！', '？'))
    except Exception:
        return False

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
    output_dir: Optional[str] = None,    # 產物落點（權威來源 output_router）；None=自動依身分推導
    anon_sid: Optional[str] = None,      # 匿名沙盒 sid（未登入時第 1 層落點的 key）
    output_job: Optional[str] = None,    # owner 第 3 層 jobs/<job> 名稱（未給=當日日期）
    platform: Optional[str] = None,      # 來源平台旗標（web/telegram…），供工具層分流（2026-10-08 indexPage 修法B）
) -> Optional[str]:
    """(P2-10 S1) 薄殼: 回合運算已抽至 core/turn_engine, 這裡僅轉呼叫, 行為與抽離前一致。"""
    import importlib as _il2
    turn_engine = _il2.import_module("turn_engine")
    return await turn_engine.process_message(
        user_id=user_id,
        text=text,
        stream_callback=stream_callback,
        agent_name=agent_name,
        agent_config=agent_config,
        auto_mode=auto_mode,
        initial_prompt=initial_prompt,
        context_files=context_files,
        output_dir=output_dir,
        anon_sid=anon_sid,
        output_job=output_job,
        platform=platform
    )



























































# 在 mokagi.py 頂部或 utils 中
async def with_autofix(
    func: Callable[..., Awaitable[Any]],
    *args,
    max_attempts: Optional[int] = None,   # L2 重試層：None/0 = 由錯誤分類決定（transient 首次+5 次退避重試）
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
        # 若 autofix 工具不可用 → L2 重試層：依錯誤分類決定是否退避重試
        try:
            from retry_layer import run_with_retry, AutofixNeeded
        except ImportError:
            from core.retry_layer import run_with_retry, AutofixNeeded

        async def _l2_once():
            return await call_tool_handler(action_func, *action_args, **(action_kwargs or {}))

        try:
            return await run_with_retry(_l2_once, label="safe_autofix_retry(no-autofix)")
        except AutofixNeeded as _need:
            # 沒有 autofix 可用 → 原樣拋出最後一次的真實例外（維持舊行為）
            raise _need.exc

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
        with closing(connect(HISTORY_DB_PATH)) as conn:
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
