"""
mok_web.py
網頁前端適配器（基於 mokagi）
提供文件瀏覽器、系統監控、聊天界面，所有 AI 對話能力調用 mokagi 模塊。
202608260224_我覺得可以版
"""

import os, sys
import re
import json
import asyncio
import threading
import time
import subprocess
import faulthandler
faulthandler.enable()  # 段錯誤時輸出 Python 棧到 stderr，便於診斷 SIGSEGV
from flask import Flask, render_template, request, send_from_directory, send_file, Response, jsonify, stream_with_context
from flask_socketio import SocketIO, join_room
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import sqlite3
from contextlib import closing




os.environ.setdefault("MOKAGI_HOME", "mok")


















# ========== Docker 自動檢測與啟動 ==========
def ensure_docker_running():
    """確保 Docker 服務已安裝且正在運行，若未安裝則自動安裝（僅限 Debian/Ubuntu）"""
    # 檢查 Docker 是否已安裝
    try:
        subprocess.run(['docker', '--version'], capture_output=True, check=True)
    except (subprocess.CalledProcessError, FileNotFoundError):
        print("⚠️ Docker 未安裝，嘗試自動安裝...")
        try:
            # 非交互式安裝（需 sudo 權限）
            subprocess.run(
                "sudo DEBIAN_FRONTEND=noninteractive apt update && "
                "sudo DEBIAN_FRONTEND=noninteractive apt install -y docker.io",
                shell=True, check=True, timeout=300
            )
            print("✅ Docker 安裝完成")
        except Exception as e:
            print(f"❌ Docker 安裝失敗: {e}")
            return

    # 啟動 Docker 服務（若未運行）
    try:
        subprocess.run(['sudo', 'systemctl', 'start', 'docker'], check=True, timeout=30)
        print("✅ Docker 服務已啟動")
    except subprocess.CalledProcessError:
        print("⚠️ 無法啟動 Docker 服務，請手動檢查")
    except FileNotFoundError:
        print("⚠️ systemctl 不可用，請手動啟動 Docker")

    # 將當前用戶加入 docker 群組（避免每次 sudo）
    try:
        user = os.environ.get('USER', 'ubuntu')
        subprocess.run(['sudo', 'usermod', '-aG', 'docker', user], check=True, timeout=10)
        print(f"✅ 已將用戶 {user} 加入 docker 群組（重新登入生效）")
    except Exception as e:
        print(f"⚠️ 無法加入 docker 群組: {e}")

# 強制啟用 Docker 沙箱（立即執行）
os.environ['MOK_USE_DOCKER_SANDBOX'] = '0'

# ===== 啟動速度優化：Docker 檢測移至背景執行 =====
# 不阻塞 Flask 啟動，避免 apt install 耗時數十秒
def _background_docker_check():
    """背景執行 Docker 檢測與安裝"""
    try:
        import time
        # 等 Flask 啟動完成再檢測（延遲 2 秒）
        time.sleep(2)
        ensure_docker_running()
    except Exception as e:
        print(f"⚠️ 背景 Docker 檢測失敗: {e}")

# 啟動背景線程
docker_thread = threading.Thread(target=_background_docker_check, daemon=True)
docker_thread.start()
print("🚀 Docker 檢測已移至背景，不阻塞啟動...")
# ===== 結束 =====

# ============================================

# 導入核心模塊
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'core'))

os.environ['AD_MOK_AGENT_NAME'] = 'default'
import mokagi
from mokagi import process_message, clear_history, reload_tools, MOKAGI_home
from config import _agent_config_cache, _agent_config

# 導入工具管理（用於獲取工具列表等）
import tool_handler
import base64
import tempfile

# 啟動時加載工具
tool_handler.load_tools()

# 定義模板目錄
BASE_DIR = os.path.expanduser(f"~/.{MOKAGI_home}/html")          # 你的 HTML 根目錄
template_dir = BASE_DIR
static_dir = os.path.join(BASE_DIR, "static")

app = Flask(__name__, template_folder=template_dir, static_folder=static_dir, static_url_path='/static')
app.config['SECRET_KEY'] = 'secret_dev_key'
ASSET_BUSTER = str(int(time.time()))
WEB_BUILD_ID = 'mok-web-sse-fix-20260822-02'

# ===== 方案二：檔案時間戳自動版本 ⭐️ =====
# 用檔案修改時間當版本號，改一次檔案、版本就自動變，不用再手動改 ?v=
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # 靜態檔不強緩存，靠 ETag/Last-Modified 每次驗證
app.config['TEMPLATES_AUTO_RELOAD'] = True  # 修改 HTML 模板立即生效，不需重啟

@app.context_processor
def inject_asset_version():
    '''模板用：{{ asset_url('api.js') }} 會自動輸出 /static/api.js?v=<檔案mtime>'''
    def asset_url(filename):
        fpath = os.path.join(static_dir, filename)
        if os.path.exists(fpath):
            mtime = int(os.path.getmtime(fpath))
            return f'/static/{filename}?v={mtime}&b={ASSET_BUSTER}'
        return f'/static/{filename}?b={ASSET_BUSTER}'
    return dict(asset_url=asset_url)

@app.context_processor
def inject_mokagi_price():
    """統一計費注入：所有模板可用 {{ MOKAGI_PRICE }}（唯一價格源：core/mok_price.py）"""
    try:
        import sys
        core_dir = os.path.join(os.path.expanduser(f"~/.{MOKAGI_home}"), "core")
        if core_dir not in sys.path:
            sys.path.insert(0, core_dir)
        from mok_price import to_dict
        return dict(MOKAGI_PRICE=to_dict())
    except Exception:
        return dict(MOKAGI_PRICE={"currency": "HKD", "price_per_million": 68, "price_per_token": 0.000068, "setup_fee": 5000, "github": "https://github.com/MOK2026/MOKAGI", "display": {"per_million": "HK$68 / 百萬 token", "setup_fee": "HK$5,000"}})


@app.route('/api/build_info', methods=['GET'])
def api_build_info():
    main_js = os.path.join(static_dir, 'main.js')
    main_mtime = int(os.path.getmtime(main_js)) if os.path.exists(main_js) else 0
    return jsonify({
        'ok': True,
        'build_id': WEB_BUILD_ID,
        'asset_buster': ASSET_BUSTER,
        'main_js_mtime': main_mtime,
        'pid': os.getpid(),
        'cwd': os.getcwd(),
    })

@app.after_request
def static_auto_version(resp):
    '''靜態資源與 HTML 頁面一律設 Cache-Control: no-cache，靠 ETag/Last-Modified 讓瀏覽器每次重新驗證：
    檔案 mtime 沒變 → 304 快取；變了 → 自動拿新版，實現「每次打開都更新」'''
    ct = resp.content_type or ''
    if ct.startswith(('text/javascript', 'application/javascript', 'text/css', 'text/html')):
        resp.headers['Cache-Control'] = 'no-cache'   # 每次重新驗證（檔案沒變仍走 304，省流量）
    return resp

socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading", ping_timeout=120, ping_interval=25)

@app.get('/api/health')
def api_health():
    return jsonify({"status": "ok", "service": "mok_web"})

# ===== VNC WebSocket Proxy：將 /novnc-ws 代理到 websockify (127.0.0.1:6080) =====
try:
    from core.vnc_proxy import VNCProxyMiddleware
    VNCProxyMiddleware.wrap_app(app)
    print("✅ VNC Proxy Middleware 已掛載，/novnc-ws → 127.0.0.1:6080")
except Exception as e:
    print(f"⚠️ VNC Proxy Middleware 掛載失敗: {e}")

# ===== 工作中侍女追蹤：記錄哪些 agent 正在處理訊息 =====
_running_agents = set()  # {agent_name, ...}

# ===== SSE 串流隊列（HTTP 串流備援，當 Socket.IO 不可用時） =====
import uuid as _uuid
import queue as _queue
_sse_queues = {}  # {session_id: queue.Queue}
_sse_lock = threading.Lock()
_sse_cleanup_timers = {}  # {session_id: threading.Timer}

def _schedule_sse_cleanup(session_id, delay_sec=180):
    """延遲清理 SSE session，給前端斷線後續流留出時間。"""
    with _sse_lock:
        old_timer = _sse_cleanup_timers.pop(session_id, None)
    if old_timer:
        try:
            old_timer.cancel()
        except Exception:
            pass

    def _cleanup():
        with _sse_lock:
            _sse_queues.pop(session_id, None)
            _sse_cleanup_timers.pop(session_id, None)
        print(f"[SSE cleanup] session={session_id} removed")

    timer = threading.Timer(delay_sec, _cleanup)
    timer.daemon = True
    with _sse_lock:
        _sse_cleanup_timers[session_id] = timer
    timer.start()

# ---------- 文件瀏覽相關（動態白名單）----------
WATCH_PATH = "/home/ubuntu/"
# 白名單使用動態 home 目錄名稱
ALLOWED_PATHS = (
    f'.{MOKAGI_home}',          # .mok
    'MOK_AI',
    '.openclaw/workspace',
    '.openclaw/agents',
    '.openclaw/cron',
    '.openclaw/skills',
    '.openclaw/openclaw.json',
    '.hermes/SOUL.md',
    '.hermes/config.yaml',
    '.hermes/skills'
)
ALLOWED_ITEMS_LIST = list(ALLOWED_PATHS)

class FileChangeHandler(FileSystemEventHandler):
    def __init__(self, socketio_instance):
        self.socketio = socketio_instance
        self.ALLOWED_PREFIXES = ALLOWED_PATHS

    def on_any_event(self, event):
        if event.is_directory:
            return
        rel_path = os.path.relpath(event.src_path, WATCH_PATH)
        if rel_path.startswith(self.ALLOWED_PREFIXES) and not rel_path.endswith('.tmp'):
            self.socketio.emit('file_change', {'path': rel_path})

SKIP_DIRS = {
    'node_modules', '.git', '__pycache__', '.venv', 'venv', 'env',
    'dist', 'build', '.cache', '.pytest_cache', '.mypy_cache',
    'backups', 'trash', '.trash', 'playwright-browsers', 'whisper_models',
    'mpt', 'browser_profile', 'browser_profile2', '.ollama', 'snap', 'go',
}
MAX_TREE_DEPTH = 6
MAX_TREE_ITEMS = 800

def get_file_tree(path, depth=0):
    if depth > MAX_TREE_DEPTH:
        return []
    tree = []
    try:
        current_path = os.path.normpath(path)
        base_path = os.path.normpath(WATCH_PATH)
        if current_path == base_path:
            items = [item for item in ALLOWED_PATHS if os.path.exists(os.path.join(current_path, item))]
        else:
            items = sorted([f for f in os.listdir(current_path)])   # 不再過濾隱藏文件
    except PermissionError:
        return []
    
    # ----- 統一排序規則：目錄優先，同類型按修改時間降序 -----
    def sort_key(item):
        full = os.path.join(current_path, item)
        is_dir = os.path.isdir(full)
        # 修改時間（若無法取得則設為 0）
        mtime = os.path.getmtime(full) if os.path.exists(full) else 0
        # 回傳 (是否為檔案, -修改時間) → 目錄 (False) 排前面，同類按時間新→舊
        return (not is_dir, -mtime)

    items.sort(key=sort_key)
    # ------------------------------------------------

    filtered = []
    for item in items:
        if 'web_viewer' in item:
            continue
        full_path = os.path.join(current_path, item)
        is_dir = os.path.isdir(full_path)
        # 跳過運行時/無用大目錄，避免掃描數萬檔案造成 /api/tree 超時（524）
        if is_dir and item in SKIP_DIRS:
            continue
        filtered.append(item)
        # 限制每層項目數，防止單一目錄過大拖垮響應
        if len(filtered) >= MAX_TREE_ITEMS:
            break

    for item in filtered:
        full_path = os.path.join(current_path, item)
        is_dir = os.path.isdir(full_path)
        node = {'name': item, 'path': os.path.relpath(full_path, WATCH_PATH), 'is_dir': is_dir}
        if is_dir:
            node['children'] = get_file_tree(full_path, depth + 1)
        tree.append(node)
    return tree

# ---------- 數據庫（聊天曆史，僅用於前端展示）----------
DB_PATH = os.path.expanduser(f"~/.{MOKAGI_home}/.memory/chat_history.db")

def init_db():
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS chat_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                agent TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                think_content TEXT,
                timestamp REAL NOT NULL,
                conv_id INTEGER
            )
        ''')
        # 檢查 conv_id 欄位是否存在，若無則新增
        cursor = conn.execute("PRAGMA table_info(chat_history)")
        columns = [row[1] for row in cursor.fetchall()]
        if 'conv_id' not in columns:
            conn.execute('ALTER TABLE chat_history ADD COLUMN conv_id INTEGER')
            print("✅ chat_history 表已新增 conv_id 欄位")
        if 'rounds' not in columns:
            conn.execute('ALTER TABLE chat_history ADD COLUMN rounds TEXT')
            print("✅ chat_history 表已新增 rounds 欄位")
        # ========== 新增 token_usage 表 ==========
        conn.execute('''
            CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT NOT NULL,
                agent_name TEXT NOT NULL,
                model_name TEXT NOT NULL,
                conversation_id TEXT,
                workflow_id TEXT,
                prompt_tokens INTEGER DEFAULT 0,
                completion_tokens INTEGER DEFAULT 0,
                total_tokens INTEGER DEFAULT 0,
                timestamp REAL NOT NULL,
                extra TEXT
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_token_agent ON token_usage (agent_name)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_token_model ON token_usage (model_name)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_token_user ON token_usage (user_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_token_conversation ON token_usage (conversation_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_token_workflow ON token_usage (workflow_id)')
        conn.commit()

init_db()

def _save_rounds_to_db(msg_id, rounds):
    """把輪次結構（思考/工具/回覆）以 JSON 持久化到 chat_history.rounds 欄位"""
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            conn.execute('UPDATE chat_history SET rounds = ? WHERE id = ?', (json.dumps(rounds, ensure_ascii=False), msg_id))
            conn.commit()
    except Exception as _e:
        print(f"[chat_history] save rounds failed: {_e}")

# ---------- 多 Agent 支持（配置文件切換）----------
ENV_DIR = os.path.expanduser(f"~/.{MOKAGI_home}/agent")
DOT_MING_PATH = os.path.expanduser(f"~/.{MOKAGI_home}/agent/客服/.客服")
CURRENT_ENV_PATH = DOT_MING_PATH
# 當前選中 Agent 的 ADMIN_CHAT_ID（請求級 fallback，避免污染全域 _agent_config / os.environ）
_current_admin_chat_id = None

def parse_dot_ming():
    """解析 .default 文件，返回配置字典和模型列表（與 mokagi 配置同步）"""
    global MOK_CONFIG
    config = {}
    models = []
    if not os.path.exists(DOT_MING_PATH):
        models = [{"name": "huihui_ai/qwen3-abliterated:1.7b", "url": "http://localhost:11434/v1"}]
        config = {
            "num_predict": 8192,
            "num_ctx": 16384,
            "temperature": 0.8,
            "top_p": 0.9,
            "top_k": 50,
            "repeat_penalty": 1.5,
            "presence_penalty": 0.6,
            "frequency_penalty": 0.5
        }
        MOK_CONFIG = {}
        return config, models
    with open(DOT_MING_PATH, 'r', encoding='utf-8') as f:
        content = f.read()
    for line in content.splitlines():
        line = line.strip()
        if not line or line.startswith('#'):
            continue
        if '=' in line:
            key, val = line.split('=', 1)
            key = key.strip()
            val = val.strip()
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            config[key] = val
    name_pattern = re.compile(r'^MOK_MODEL_NAME(\d*)$')
    url_pattern = re.compile(r'^MOK_MODEL_url(\d*)$')
    name_dict = {}
    url_dict = {}
    for key, val in config.items():
        m_name = name_pattern.match(key)
        if m_name:
            suffix = m_name.group(1) or "0"
            name_dict[suffix] = val
        m_url = url_pattern.match(key)
        if m_url:
            suffix = m_url.group(1) or "0"
            url_dict[suffix] = val
    all_suffixes = set(name_dict.keys()) | set(url_dict.keys())
    for suffix in all_suffixes:
        name = name_dict.get(suffix)
        url = url_dict.get(suffix)
        if name and url:
            models.append({"name": name, "url": url})
    if not models:
        models = [{"name": "huihui_ai/qwen3-abliterated:1.7b", "url": "http://localhost:11434/v1"}]
    ollama_options = {
        "num_predict": int(config.get("MOK_num_predict", 8192)),
        "num_ctx": int(config.get("MOK_num_ctx", 16384)),
        "temperature": float(config.get("MOK_temperature", 0.8)),
        "top_p": float(config.get("MOK_top_p", 0.9)),
        "top_k": int(config.get("MOK_top_k", 50)),
        "repeat_penalty": float(config.get("MOK_repeat_penalty", 1.5)),
        "presence_penalty": float(config.get("MOK_presence_penalty", 0.6)),
        "frequency_penalty": float(config.get("MOK_frequency_penalty", 0.5))
    }
    if "MOK_num_threads" in config:
        ollama_options["num_threads"] = int(config["MOK_num_threads"])
    MOK_CONFIG = {k: v for k, v in config.items() if k.startswith('MOK_')}
    return ollama_options, models

OLLAMA_OPTIONS, AVAILABLE_MODELS = parse_dot_ming()
CURRENT_MODEL_INDEX = 0

def get_current_model_config():
    return AVAILABLE_MODELS[CURRENT_MODEL_INDEX]

def get_env_files():
    if not os.path.exists(ENV_DIR):
        return []
    files = []
    for item in os.listdir(ENV_DIR):
        agent_dir = os.path.join(ENV_DIR, item)
        if os.path.isdir(agent_dir):
            dot_file = os.path.join(agent_dir, f'.{item}')
            if os.path.isfile(dot_file):
                files.append(f'.{item}')
    return files

def reload_config(env_path):
    global DOT_MING_PATH, OLLAMA_OPTIONS, AVAILABLE_MODELS, CURRENT_MODEL_INDEX, CURRENT_ENV_PATH, _current_admin_chat_id
    DOT_MING_PATH = env_path
    CURRENT_ENV_PATH = env_path
    options, models = parse_dot_ming()
    OLLAMA_OPTIONS = options
    AVAILABLE_MODELS = models

    # 讀取 MOK_CURRENT_MODEL 設置當前模型索引
    current_model_name = None
    try:
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('MOK_CURRENT_MODEL='):
                    current_model_name = line.split('=', 1)[1].strip()
                    if (current_model_name.startswith('"') and current_model_name.endswith('"')) or \
                       (current_model_name.startswith("'") and current_model_name.endswith("'")):
                        current_model_name = current_model_name[1:-1]
                    break
    except Exception:
        pass
    new_index = 0
    if current_model_name:
        for i, m in enumerate(AVAILABLE_MODELS):
            if m['name'] == current_model_name:
                new_index = i
                break
    CURRENT_MODEL_INDEX = new_index

    # 清除 mokagi 中的配置緩存，讓下次請求重新加載
    agent_name = os.path.basename(env_path).lstrip('.')
    if agent_name in _agent_config_cache:
        del _agent_config_cache[agent_name]

    # ===== 優化：工具只需載入一次，切換 Agent 時不重新掃描目錄 =====
    # 工具通常與 Agent 無關，MOKAGI_home 固定，無需重新載入模組
    # 移除 tool_handler.load_tools()，避免每次切換 Agent 都耗時掃描
    # 若工具確實需要 Agent 專屬配置，可讓工具從 agent_config 動態讀取
    # tool_handler.load_tools()  # 已移除

    # ===== 優化：memory 客戶端只在啟動時初始化一次 =====
    # memory 模塊的 chromadb 客戶端使用固定的 MOKAGI_home，切換 Agent 時不必重置
    # 清理邏輯已移除，工具可通過 agent_config 動態獲取當前 Agent 名稱
    # memory_mod = tool_handler.get_tools().get("memory")
    # if memory_mod and hasattr(memory_mod, '_client'):
    #     memory_mod._client = None
    #     memory_mod._collection = None
    #     memory_mod._kb_collection = None

    # 確保 ADMIN_CHAT_ID 正確同步到 mokagi._agent_config
    # 直接從當前配置文件中讀取 ADMIN_CHAT_ID
    admin_chat_id = None
    try:
        with open(env_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if line.startswith('ADMIN_CHAT_ID='):
                    admin_chat_id = line.split('=', 1)[1].strip()
                    # 去除可能的引號
                    if (admin_chat_id.startswith('"') and admin_chat_id.endswith('"')) or \
                       (admin_chat_id.startswith("'") and admin_chat_id.endswith("'")):
                        admin_chat_id = admin_chat_id[1:-1]
                    break
    except Exception:
        pass
    # 🔧 不再改寫全域 _agent_config / os.environ，改存到請求級 fallback 變量，避免多 Agent 並行污染
    _current_admin_chat_id = admin_chat_id

# ---------- 以下為原始 `import sqlite3` 之後的內容（此處省略，保持不變）----------
# 請確保替換時只複製上述部分，保留後續所有 SocketIO 和路由程式碼























































# ---------- HTTP SSE 串流端點（主要傳輸層，繞過 Socket.IO 502） ----------
def _start_sse_chat_session(data):
    user_msg = data.get('message', '').strip()
    agent_name = data.get('agent', '')
    user_id = data.get("user_id") or _current_admin_chat_id or _agent_config.get("ADMIN_CHAT_ID") or os.environ.get("ADMIN_CHAT_ID", "web_default")
    context_files = data.get("context_files", None)
    if not user_msg:
        raise ValueError("empty message")

    _running_agents.add(agent_name)
    session_id = str(_uuid.uuid4())[:8]
    q = _queue.Queue()
    with _sse_lock:
        old_timer = _sse_cleanup_timers.pop(session_id, None)
        if old_timer:
            try:
                old_timer.cancel()
            except Exception:
                pass
        _sse_queues[session_id] = q
    print(f"[SSE start] session={session_id} agent={agent_name} msg={user_msg[:50]}...")

    def _sse_bg_worker():
        accumulated_think = ""
        accumulated_reply = ""
        assistant_msg_id = None
        user_msg_id = None

        def update_assistant_in_db(msg_id, content, think_content):
            with closing(sqlite3.connect(DB_PATH)) as conn:
                conn.execute('UPDATE chat_history SET content = ?, think_content = ? WHERE id = ?', (content, think_content, msg_id))
                conn.commit()

        def stream_emit(event):
            nonlocal accumulated_think, accumulated_reply, assistant_msg_id
            event["agent"] = agent_name
            try:
                q.put(event)
            except Exception as _e:
                print(f"[SSE stream_emit] q.put failed: {_e}")
            if event["type"] == "think":
                accumulated_think += event["content"]
                if assistant_msg_id is None:
                    with closing(sqlite3.connect(DB_PATH)) as conn:
                        cursor = conn.execute("INSERT INTO chat_history (agent, role, content, think_content, timestamp) VALUES (?, ?, ?, ?, ?)", (agent_name, "assistant", "", "", time.time()))
                        assistant_msg_id = cursor.lastrowid
                        conn.commit()
                update_assistant_in_db(assistant_msg_id, accumulated_reply, accumulated_think)
            elif event["type"] == "reply":
                if event.get("subtype", "normal") in ("pending_list", "tool_process", "semantic_search", "experience"):
                    return
                accumulated_reply += event["content"]
                if assistant_msg_id is None:
                    with closing(sqlite3.connect(DB_PATH)) as conn:
                        cursor = conn.execute("INSERT INTO chat_history (agent, role, content, think_content, timestamp) VALUES (?, ?, ?, ?, ?)", (agent_name, "assistant", "", "", time.time()))
                        assistant_msg_id = cursor.lastrowid
                        conn.commit()
                update_assistant_in_db(assistant_msg_id, accumulated_reply, accumulated_think)
            elif event["type"] == "done":
                _running_agents.discard(agent_name)
                if event.get("final_reply"):
                    accumulated_reply = event["final_reply"]
                if assistant_msg_id is None:
                    with closing(sqlite3.connect(DB_PATH)) as conn:
                        cursor = conn.execute("INSERT INTO chat_history (agent, role, content, think_content, timestamp) VALUES (?, ?, ?, ?, ?)", (agent_name, "assistant", accumulated_reply, accumulated_think, time.time()))
                        assistant_msg_id = cursor.lastrowid
                        conn.commit()
                update_assistant_in_db(assistant_msg_id, accumulated_reply, accumulated_think)
                if event.get("rounds"):
                    _save_rounds_to_db(assistant_msg_id, event["rounds"])
                conv_id = event.get("conv_id")
                if conv_id:
                    with closing(sqlite3.connect(DB_PATH)) as conn:
                        conn.execute("UPDATE chat_history SET conv_id = ? WHERE id = ?", (conv_id, assistant_msg_id))
                        conn.commit()
                if conv_id:
                    _update_user_message_conv_id(agent_name, conv_id, user_msg_id, user_id)

        try:
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cursor = conn.execute('INSERT INTO chat_history (agent, role, content, timestamp) VALUES (?, ?, ?, ?)', (agent_name, 'user', user_msg, time.time()))
                user_msg_id = cursor.lastrowid
                conn.commit()
        except Exception as _e:
            print(f"[SSE] user msg insert failed: {_e}")

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def _bg_coro():
                async def async_stream_cb(event):
                    print(f"[SSE cb] type={event.get('type')} len={len(event.get('content', ''))}")
                    stream_emit(event)
                agent_config = await mokagi.get_agent_config(agent_name)
                from autofix2 import autofix_run
                from mokagi import find_tool_handler
                async def run(agent_config, context_files=None):
                    await mokagi.process_message(user_id=user_id, text=user_msg, stream_callback=async_stream_cb, agent_name=agent_name, agent_config=agent_config, context_files=context_files)
                result = await autofix_run(func=run, func_args=(agent_config, context_files), func_kwargs={}, max_attempts=3, autofix_handler=find_tool_handler("admin"), autofix_max_retries=2, original_text=user_msg, stream_callback=async_stream_cb)
                if result == "__ERROR_REPORTED__":
                    _running_agents.discard(agent_name)
                    stream_emit({"type": "reply", "content": "failed"})
                    stream_emit({"type": "done"})
            loop.run_until_complete(_bg_coro())
        except Exception as _e:
            print(f"[SSE bg] error: {_e}")
            import traceback
            traceback.print_exc()
            try:
                stream_emit({"type": "reply", "content": f"error: {_e}"})
                stream_emit({"type": "done"})
            except:
                pass
        finally:
            loop.close()
            _running_agents.discard(agent_name)

    threading.Thread(target=_sse_bg_worker, daemon=True).start()
    return {"session_id": session_id, "agent_name": agent_name, "queue": q}


@app.route('/api/chat', methods=['POST'])
def api_chat_sse():
    data = request.get_json(force=True)
    try:
        _started = _start_sse_chat_session(data)
    except ValueError as _e:
        return jsonify({"error": str(_e)}), 400
    session_id = _started["session_id"]
    agent_name = _started["agent_name"]
    q = _started["queue"]

    print(f"[SSE /api/chat] session={session_id} agent={agent_name} opened")

    def generate():
        stream_done = False
        try:
            # 先送出一個 prelude，確保代理層能立即拿到首字節，避免 524 等待超時。
            yield ": stream-open\n\n"
            yield f"data: {json.dumps({'type': 'stream_meta', 'agent': agent_name, 'sse_session_id': session_id}, ensure_ascii=False)}\n\n"
            heartbeat_count = 0
            while True:
                try:
                    event = q.get(timeout=5)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event.get('type') == 'done':
                        stream_done = True
                        break
                except _queue.Empty:
                    heartbeat_count += 1
                    if heartbeat_count > 120:
                        yield f"data: {json.dumps({'type': 'error', 'content': 'timeout', 'agent': agent_name}, ensure_ascii=False)}\n\n"
                        break
                    # 🔧 發送 SSE 心跳註解（keep-alive），防止 Cloudflare/Nginx 超時斷線
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            # 不立刻刪 queue，給 QUIC/代理斷線後的續流端點留時間接手。
            if stream_done:
                _schedule_sse_cleanup(session_id, delay_sec=20)
            else:
                _schedule_sse_cleanup(session_id, delay_sec=180)
            _running_agents.discard(agent_name)
            print(f"[SSE /api/chat] session={session_id} ended done={stream_done}")

    return Response(stream_with_context(generate()), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache, no-store, must-revalidate, no-transform',
        'X-Accel-Buffering': 'no',
        'Alt-Svc': 'clear',
        'Connection': 'keep-alive',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Expose-Headers': '*',
        'Content-Type': 'text/event-stream; charset=utf-8'
    })


@app.route('/api/chat/start', methods=['POST'])
def api_chat_start():
    """公網穩定模式：短 POST 啟動任務，客戶端再走 GET SSE 接收。"""
    data = request.get_json(force=True)
    try:
        _started = _start_sse_chat_session(data)
    except ValueError as _e:
        return jsonify({"error": str(_e)}), 400

    session_id = _started["session_id"]
    agent_name = _started["agent_name"]
    return jsonify({
        "ok": True,
        "agent": agent_name,
        "sse_session_id": session_id,
        "stream_url": f"/api/chat/stream/{session_id}"
    })


# ---------- SSE 備援串流端點（供 Socket.IO 斷線後客戶端降級使用）----------
@app.route('/api/chat/stream/<session_id>', methods=['GET'])
def api_chat_stream_sse(session_id):
    """讓客戶端在 Socket.IO 斷線後，仍能通過 SSE 接收流式回應。
    客戶端從 chat_stream 事件中獲取 sse_session_id 後，建立 EventSource 連接至此。"""
    with _sse_lock:
        q = _sse_queues.get(session_id)
    if q is None:
        return jsonify({"error": "session not found or expired"}), 404

    def generate_sse():
        heartbeat_count = 0
        stream_done = False
        try:
            # 同步送出 prelude，讓中間代理儘快確認串流已開始。
            yield ": stream-open\n\n"
            yield f"data: {json.dumps({'type': 'stream_meta', 'sse_session_id': session_id}, ensure_ascii=False)}\n\n"
            while True:
                try:
                    event = q.get(timeout=5)
                    yield f"data: {json.dumps(event, ensure_ascii=False)}\n\n"
                    if event.get('type') == 'done':
                        stream_done = True
                        break
                except _queue.Empty:
                    heartbeat_count += 1
                    if heartbeat_count > 120:  # 10 分鐘超時（5 秒心跳）
                        yield f"data: {json.dumps({'type': 'error', 'content': 'timeout'}, ensure_ascii=False)}\n\n"
                        break
                    yield ": heartbeat\n\n"
        except GeneratorExit:
            pass
        finally:
            if stream_done:
                _schedule_sse_cleanup(session_id, delay_sec=20)
            else:
                _schedule_sse_cleanup(session_id, delay_sec=180)

    return Response(stream_with_context(generate_sse()), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache, no-store, must-revalidate, no-transform',
        'X-Accel-Buffering': 'no',
        'Alt-Svc': 'clear',
        'Connection': 'keep-alive',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Expose-Headers': '*',
        'Content-Type': 'text/event-stream; charset=utf-8'
    })


# ---------- SocketIO 聊天（核心）- 支援多工並行與泡式輸出

# 🔧 客戶端註冊 user_id 房間（解決重連後 sid 變更導致收不到回應的問題）
@socketio.on('join_room')
def handle_join_room(data):
    user_id = data.get('user_id', '')
    agent_name = data.get('agent', '')
    if user_id:
        room = f'user_{user_id}'
        join_room(room)
        print(f'[join_room] {agent_name} user_id={user_id} 加入房間 {room} (sid={request.sid})')

@socketio.on('chat_message')
def handle_chat_message(data):
    user_msg = data.get('message', '').strip()
    agent_name = data.get('agent', '')
    if not user_msg:
        return

    # 🔧 標記此 agent 工作中（頁面刷新時可恢復狀態）
    _running_agents.add(agent_name)

    user_id = data.get("user_id") or _current_admin_chat_id or _agent_config.get("ADMIN_CHAT_ID") or os.environ.get("ADMIN_CHAT_ID", "web_default")
    context_files = data.get("context_files", None)  # 🔧 前端控制 soul 文件載入

    # 🔧 客服模式：自動預先抓取頁面內容（不依賴 LLM 自己調用 web_fetch）
    if "【客服頁面】" in user_msg:
        import re, json as _json
        match = re.search(r"網址：(.+?)(?:\n|$)", user_msg)
        if match:
            page_url = match.group(1).strip()
            try:
                from web_fetch import handle_web_fetch
                fetch_result = asyncio.run(handle_web_fetch(
                    {"url": page_url},
                    chat_id=user_id,
                    agent_config=_agent_config
                ))
                result_data = _json.loads(fetch_result)
                if result_data.get("success"):
                    page_content = result_data.get("content", "")
                    page_title = result_data.get("title", "")
                    fetch_context = (
                        f"【已自動抓取的網頁內容】\n"
                        f"標題：{page_title}\n"
                        f"網址：{page_url}\n\n"
                        f"{page_content[:4000]}\n\n"
                        f"請根據以上網頁內容回答用戶問題。\n\n"
                    )
                    user_msg = re.sub(
                        r"【客服頁面】.*?\n\n",
                        fetch_context,
                        user_msg,
                        count=1
                    )
                    print(f"✅ 客服模式：已自動抓取頁面內容 ({len(page_content)} 字)")
            except Exception as e:
                print(f"⚠️ 自動抓取客服頁面失敗: {e}")

    # 立即儲存使用者訊息（保證順序）
    import time
    with closing(sqlite3.connect(DB_PATH)) as conn:
        cursor = conn.execute(
            'INSERT INTO chat_history (agent, role, content, think_content, timestamp) VALUES (?, ?, ?, ?, ?)',
            (agent_name, 'user', user_msg, None, time.time())
        )
        user_msg_id = cursor.lastrowid
        conn.commit()

    # 🔧 關鍵修復：捕獲 sid 用於背景執行緒（避免 request context 遺失）
    sid = request.sid

    # 🔧 背景執行緒處理（不阻塞主事件循環，支援多工並行與泡式輸出）
    def _bg_worker():
        print(f"[DEBUG _bg_worker] 開始處理 agent={agent_name}, sid={sid}, user_msg={user_msg[:50]}...")
        accumulated_think = ""
        accumulated_reply = ""
        assistant_msg_id = None

        # 🔧 SSE 備援隊列：即使 Socket.IO 斷線也能通過 SSE 傳遞回應
        _sse_session_id = str(_uuid.uuid4())[:8]
        _sse_q = _queue.Queue()
        with _sse_lock:
            _sse_queues[_sse_session_id] = _sse_q
        _sse_session_sent = False  # 只在第一次 stream_emit 時通知客戶端

        def update_assistant_in_db(msg_id, content, think_content):
            with closing(sqlite3.connect(DB_PATH)) as conn:
                conn.execute(
                    'UPDATE chat_history SET content = ?, think_content = ? WHERE id = ?',
                    (content, think_content, msg_id)
                )
                conn.commit()

        def stream_emit(event):
            nonlocal accumulated_think, accumulated_reply, assistant_msg_id, _sse_session_sent
            event["agent"] = agent_name

            # 🔧 SSE 備援：將事件放入 SSE 隊列（客戶端可通過 /api/chat/stream/<id> 獲取）
            try:
                _sse_q.put(event.copy())
            except Exception as _sse_put_err:
                print(f"[stream_emit] SSE q.put 失敗: {_sse_put_err}")

            # 🔧 首次發送時附帶 SSE session_id，讓客戶端知道備援通道
            if not _sse_session_sent:
                _sse_session_sent = True
                event_with_sse = event.copy()
                event_with_sse["sse_session_id"] = _sse_session_id
                try:
                    if sid:
                        socketio.emit("chat_stream", event_with_sse, room=sid, namespace="/")
                except Exception as _emit_err:
                    print(f"[stream_emit] SSE session 通知失敗: {_emit_err}")
                return  # 已發送帶 session_id 的版本，跳過後續普通發送

            # 🔧 發送事件給前端（確保流式即時輸出），再寫 DB
            try:
                if sid:
                    socketio.emit("chat_stream", event, room=sid, namespace="/")
            except Exception as _emit_err:
                print(f"[stream_emit] socketio.emit 失敗: {_emit_err}")

            if event["type"] == "think":
                accumulated_think += event["content"]
                if assistant_msg_id is None:
                    with closing(sqlite3.connect(DB_PATH)) as conn:
                        cursor = conn.execute("INSERT INTO chat_history (agent, role, content, think_content, timestamp) VALUES (?, ?, ?, ?, ?)", (agent_name, "assistant", "", "", time.time()))
                        assistant_msg_id = cursor.lastrowid
                        conn.commit()
                update_assistant_in_db(assistant_msg_id, accumulated_reply, accumulated_think)

            elif event["type"] == "reply":
                if event.get("subtype", "normal") in ("pending_list", "tool_process", "semantic_search", "experience"):
                    return
                accumulated_reply += event["content"]
                if assistant_msg_id is None:
                    with closing(sqlite3.connect(DB_PATH)) as conn:
                        cursor = conn.execute(
                            "INSERT INTO chat_history (agent, role, content, think_content, timestamp) VALUES (?, ?, ?, ?, ?)",
                            (agent_name, "assistant", "", "", time.time())
                        )
                        assistant_msg_id = cursor.lastrowid
                        conn.commit()
                # DB 寫入延後到 emit 之後，不阻塞流式
                update_assistant_in_db(assistant_msg_id, accumulated_reply, accumulated_think)

            elif event["type"] == "done":
                _running_agents.discard(agent_name)
                if event.get("final_reply"):
                    accumulated_reply = event["final_reply"]
                if assistant_msg_id is None:
                    with closing(sqlite3.connect(DB_PATH)) as conn:
                        cursor = conn.execute(
                            "INSERT INTO chat_history (agent, role, content, think_content, timestamp) VALUES (?, ?, ?, ?, ?)",
                            (agent_name, "assistant", accumulated_reply, accumulated_think, time.time())
                        )
                        assistant_msg_id = cursor.lastrowid
                        conn.commit()
                update_assistant_in_db(assistant_msg_id, accumulated_reply, accumulated_think)
                if event.get("rounds"):
                    _save_rounds_to_db(assistant_msg_id, event["rounds"])
                conv_id = event.get("conv_id")
                if conv_id:
                    with closing(sqlite3.connect(DB_PATH)) as conn:
                        conn.execute(
                            "UPDATE chat_history SET conv_id = ? WHERE id = ?",
                            (conv_id, assistant_msg_id)
                        )
                        conn.commit()
                if conv_id:
                    _update_user_message_conv_id(agent_name, conv_id, user_msg_id, user_id)

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def _bg_coro():
                async def async_stream_cb(event):
                    print(f"[DEBUG async_stream_cb] type={event.get('type')}, content_len={len(event.get('content', ''))}")
                    stream_emit(event)

                agent_config = await mokagi.get_agent_config(agent_name)
                from autofix2 import autofix_run
                from mokagi import find_tool_handler

                async def run(agent_config, context_files=None):
                    await mokagi.process_message(
                        user_id=user_id,
                        text=user_msg,
                        stream_callback=async_stream_cb,
                        agent_name=agent_name,
                        agent_config=agent_config,
                        context_files=context_files
                    )

                result = await autofix_run(
                    func=run,
                    func_args=(agent_config, context_files),
                    func_kwargs={},
                    max_attempts=3,
                    autofix_handler=find_tool_handler("admin"),
                    autofix_extra_args={"agent_config": agent_config, "user_id": user_id},
                    llm_func=mokagi.call_llm,
                    agent_config=agent_config,
                    user_id=user_id,
                    original_text=user_msg,
                    stream_callback=async_stream_cb
                )
                if result == "__ERROR_REPORTED__":
                    _running_agents.discard(agent_name)
                    err_event1 = {"type": "reply", "content": "❌ 自動修復失敗，請稍後重試。", "agent": agent_name}
                    err_event2 = {"type": "done", "agent": agent_name}
                    if sid:
                        socketio.emit("chat_stream", err_event1, room=sid, namespace="/")
                        socketio.emit("chat_stream", err_event2, room=sid, namespace="/")
                else:
                    # 🔧 安全清理：確保 process_message 完成後清理狀態
                    _running_agents.discard(agent_name)

            loop.run_until_complete(_bg_coro())
        except Exception as e:
            _running_agents.discard(agent_name)
            err_event1 = {"type": "reply", "content": f"❌ 嚴重錯誤: {str(e)}", "agent": agent_name}
            err_event2 = {"type": "done", "agent": agent_name}
            if sid:
                socketio.emit("chat_stream", err_event1, room=sid, namespace="/")
                socketio.emit("chat_stream", err_event2, room=sid, namespace="/")
            # 🔧 SSE 備援：也將錯誤事件放入 SSE 隊列
            try:
                _sse_q.put(err_event1.copy())
                _sse_q.put(err_event2.copy())
            except Exception:
                pass
        finally:
            loop.close()
            # 🔧 清理 SSE 備援隊列（延遲 30 秒讓客戶端有時間讀取最後的事件）
            def _delayed_sse_cleanup():
                time.sleep(30)
                with _sse_lock:
                    _sse_queues.pop(_sse_session_id, None)
            threading.Thread(target=_delayed_sse_cleanup, daemon=True).start()

    threading.Thread(target=_bg_worker, daemon=True).start()

        





def _update_user_message_conv_id(agent, conv_id, user_msg_id, user_id=None):
    # 確保 user_id 有效
    if not user_id:
        user_id = "web_default"
        print(f"[DEBUG] user_id 為空，使用默認值: {user_id}")
    
    # 如果 conv_id 為 None，從 conversation_history 回退查詢（直接取最新記錄）
    if conv_id is None:
        hist_db = os.path.expanduser(f"~/.{MOKAGI_home}/.memory/conversation_history.db")
        conv_id = None
        with closing(sqlite3.connect(hist_db)) as conn:
            # 先用 user_key 查找
            cursor = conn.execute(
                'SELECT id FROM conversation_history WHERE user_key = ? AND role = "user" ORDER BY id DESC LIMIT 1',
                (f"{user_id}_{agent}",)
            )
            row = cursor.fetchone()
            if row:
                conv_id = row[0]
                print(f"[DEBUG] 從 conversation_history 回退查詢到最新的 user 記錄 ID: {conv_id}")
            else:
                # 用模糊匹配查找（兼容 web_default 等不同 user_id）
                cursor = conn.execute(
                    'SELECT id FROM conversation_history WHERE user_key LIKE ? AND role = "user" ORDER BY id DESC LIMIT 1',
                    (f"%_{agent}",)
                )
                row = cursor.fetchone()
                if row:
                    conv_id = row[0]
                    print(f"[DEBUG] 從 conversation_history 模糊查詢到最新的 user 記錄 ID: {conv_id}")
                else:
                    print(f"[DEBUG] 在 conversation_history 中未找到 user_key={user_id}_{agent} 的記錄")
    
    # 如果有 conv_id，更新 chat_history
    if conv_id is not None:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            conn.execute(
                'UPDATE chat_history SET conv_id = ? WHERE id = ?',
                (conv_id, user_msg_id)
            )
            conn.commit()
            print(f"[DEBUG] 更新 chat_history 記錄 id={user_msg_id} 的 conv_id 為 {conv_id}")
    else:
        print(f"[DEBUG] conv_id 仍為 None，跳過更新")
        
        
@socketio.on('stop_generation')
def handle_stop():
    sid = request.sid
    # 先通知前端服務即將重啟（可選）
    socketio.emit('stream_stopped', {'status': 'restarting'}, room=sid)
    # 立即執行 pm2 restart（不等待，後臺運行）
    subprocess.Popen("pm2 restart mok_agi", shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    print(f"立即停止所有服務及緊急重啟，發起者: {sid}")



































































# ---------- 網頁路由（保持不變）----------




def _game_asset_version(folder):
    '''回傳目錄內所有 js/css/html 檔案的最新 mtime 作為版本號'''
    ver = 0
    for root, dirs, files in os.walk(folder):
        for f in files:
            if f.endswith(('.js', '.css', '.html')):
                try:
                    ver = max(ver, int(os.path.getmtime(os.path.join(root, f))))
                except OSError:
                    pass
    return ver or int(time.time())

@app.route('/game/<path:filename>')
@app.route('/game2/<path:filename>')
def game_files(filename):
    folder = 'game2' if request.path.startswith('/game2/') else 'game'
    full = os.path.join(BASE_DIR, folder, filename)
    if filename.endswith('.html') and os.path.exists(full):
        html = open(full, encoding='utf-8').read()
        ver = _game_asset_version(os.path.join(BASE_DIR, folder))
        html = re.sub(r'\?v=\d+', '?v=' + str(ver), html)
        return html
    return send_from_directory(os.path.join(BASE_DIR, folder), filename)
@app.route('/')
def index():
    return render_template('index.html')

@app.route('/ASCII')
def ASCII():
    return render_template('webTools/ASCII.html')

@app.route('/monitor')
def monitor():
    return render_template('webTools/monitor.html')

@app.route('/tokenstats')
def tokenstats():
    return render_template('webTools/tokenstats.html')

@app.route('/webTools/novnc')
def novnc():
    return render_template('webTools/novnc/index.html')

@app.route('/backup')
def backup_page():
    return render_template('webTools/backup.html')

@app.route('/api/backup/items')
def api_backup_items():
    # 列出 ~/.mok 頂層項目，供備份時勾選要排除的內容
    mok_dir = os.path.expanduser('~/.mok')
    ALWAYS_EXCLUDE = {'backups', '__pycache__', '.git', 'node_modules', 'playwright-browsers', '.chroma_data', '.speech2text_models', '.pending_cron_confirm', 'mpt', 'browser_profile', 'browser_profile2', 'trash', 'whisper_models', 'CPU_上傳.bat', 'CPU_備份.bat'}
    items = []
    try:
        entries = sorted(os.listdir(mok_dir))
    except OSError as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    for name in entries:
        if name in ALWAYS_EXCLUDE:
            continue
        if '.bak' in name or name in ('CPU_上傳.bat', 'CPU_備份.bat'):
            continue
        fp = os.path.join(mok_dir, name)
        if not os.path.exists(fp):
            continue
        is_dir = os.path.isdir(fp)
        size_bytes = _backup_dir_size(fp) if is_dir else os.path.getsize(fp)
        items.append({'name': name, 'is_dir': is_dir, 'size': _backup_human_size(size_bytes), 'size_bytes': size_bytes})
    return jsonify({'items': items})

def _backup_dir_size(path):
    total = 0
    for root, dirs, files in os.walk(path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                total += os.path.getsize(fp)
            except OSError:
                pass
    return total

def _backup_human_size(n):
    if n < 1024:
        return f'{n} B'
    if n < 1024 * 1024:
        return f'{n / 1024:.1f} KB'
    if n < 1024 * 1024 * 1024:
        return f'{n / (1024 * 1024):.1f} MB'
    return f'{n / (1024 * 1024 * 1024):.2f} GB'

def _admin_tz_offset():
    """統一使用 MOK_ADMIN_TIME_ZONE (+8)"""
    off = 8
    try:
        env_path = os.path.expanduser('~/.mok/env.env')
        with open(env_path, encoding='utf-8', errors='replace') as f:
            for ln in f:
                ln = ln.strip()
                if ln.startswith('MOK_ADMIN_TIME_ZONE='):
                    v = ln.split('=', 1)[1].strip()
                    if v:
                        off = int(v)
                    break
    except Exception:
        pass
    return off


@app.route('/api/backup/create', methods=['POST'])
def api_backup_create():
    import tarfile, datetime, re
    mok_dir = os.path.expanduser('~/.mok')
    backup_dir = os.path.join(mok_dir, 'backups')
    os.makedirs(backup_dir, exist_ok=True)
    data = request.get_json(silent=True) or {}
    user_exclude = set(data.get('exclude', []) or [])
    # 支援自訂檔名（可選），否則統一用 MOK_ADMIN_TIME_ZONE (+8) 時間
    custom_name = str(data.get('name', '') or '').strip()
    _tz = datetime.timezone(datetime.timedelta(hours=_admin_tz_offset()))
    timestamp = datetime.datetime.now(_tz).strftime('%Y%m%d_%H%M%S')
    if custom_name:
        custom_name = re.sub(r'[^\w.\-]', '_', custom_name)
        if not custom_name.endswith('.tar.gz'):
            custom_name += '.tar.gz'
        filename = custom_name
    else:
        filename = f'mok_backup_{timestamp}.tar.gz'
    filepath = os.path.join(backup_dir, filename)
    try:
        nested_exclude = {'__pycache__', '.git', 'node_modules', 'logs', 'playwright-browsers', '.chroma_data', '.speech2text_models', 'whisper_models'}
        top_exclude = user_exclude | {'backups', '__pycache__', '.git', 'node_modules', 'playwright-browsers', '.chroma_data', '.speech2text_models', '.pending_cron_confirm', 'mpt', 'browser_profile', 'browser_profile2', 'trash', 'whisper_models', 'CPU_上傳.bat', 'CPU_備份.bat'}
        with tarfile.open(filepath, 'w:gz') as tar:
            for entry in sorted(os.listdir(mok_dir)):
                if entry in top_exclude:
                    continue
                full = os.path.join(mok_dir, entry)
                if not os.path.exists(full):
                    continue
                if os.path.isdir(full):
                    for root, dirs, files in os.walk(full):
                        dirs[:] = [d for d in dirs if d not in nested_exclude]
                        for f in files:
                            if '.bak' in f or f in ('CPU_上傳.bat', 'CPU_備份.bat'):
                                continue
                            fp = os.path.join(root, f)
                            arcname = os.path.relpath(fp, mok_dir)
                            try:
                                tar.add(fp, arcname=arcname)
                            except OSError:
                                pass
                else:
                    if '.bak' in entry or entry in ('CPU_上傳.bat', 'CPU_備份.bat'):
                        continue
                    try:
                        tar.add(full, arcname=entry)
                    except OSError:
                        pass
        size_bytes = os.path.getsize(filepath)
        if size_bytes < 1024:
            size_str = f'{size_bytes} B'
        elif size_bytes < 1024*1024:
            size_str = f'{size_bytes/1024:.1f} KB'
        else:
            size_str = f'{size_bytes/(1024*1024):.1f} MB'
        return jsonify({'success': True, 'message': f'備份完成: {filename}', 'filename': filename, 'size': size_str})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/backup/list')
def api_backup_list():
    import datetime
    backup_dir = os.path.expanduser('~/.mok/backups')
    if not os.path.exists(backup_dir):
        return jsonify({'backups': []})
    backups = []
    for f in sorted(os.listdir(backup_dir), reverse=True):
        if f.endswith('.tar.gz'):
            fp = os.path.join(backup_dir, f)
            stat = os.stat(fp)
            size_bytes = stat.st_size
            if size_bytes < 1024:
                size_str = f'{size_bytes} B'
            elif size_bytes < 1024*1024:
                size_str = f'{size_bytes/1024:.1f} KB'
            else:
                size_str = f'{size_bytes/(1024*1024):.1f} MB'
            backups.append({
                'name': f,
                'size': size_str,
                'time': datetime.datetime.fromtimestamp(stat.st_mtime, datetime.timezone(datetime.timedelta(hours=_admin_tz_offset()))).strftime('%Y-%m-%d %H:%M:%S')
            })
    return jsonify({'backups': backups})

@app.route('/api/backup/download/<path:filename>')
def api_backup_download(filename):
    backup_dir = os.path.expanduser('~/.mok/backups')
    safe_name = os.path.basename(filename)
    return send_from_directory(backup_dir, safe_name, as_attachment=True, download_name=safe_name)

@app.route('/api/backup/delete/<path:filename>', methods=['DELETE'])
def api_backup_delete(filename):
    backup_dir = os.path.expanduser('~/.mok/backups')
    safe_name = os.path.basename(filename)
    filepath = os.path.join(backup_dir, safe_name)
    if not os.path.exists(filepath):
        return jsonify({'error': 'File not found'}), 404
    try:
        os.remove(filepath)
        return jsonify({'success': True, 'message': f'已刪除 {safe_name}'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/tree')
def api_tree():
    return {'tree': get_file_tree(WATCH_PATH)}

@app.route('/api/file/<path:sub_path>')
def get_file_content(sub_path):
    # 規範化路徑，去除多餘的斜槓和..等
    normalized = os.path.normpath(sub_path)
    # 檢查規範化後的路徑是否以允許的前綴開頭
    if not any(normalized.startswith(p) for p in ALLOWED_PATHS):
        return {"error": "Unauthorized access"}, 403
    full_path = os.path.join(WATCH_PATH, normalized)
    if not os.path.exists(full_path) or os.path.isdir(full_path):
        return {"error": "File not found"}, 404
    try:
        with open(full_path, 'r', encoding='utf-8') as f:
            content = f.read()
        return {"content": content}
    except Exception as e:
        return {"error": str(e)}, 500

# ---------- 主機 AI 維修台（僅供本機 SSH 隧道使用）----------
REPAIR_ROOT = os.path.realpath(os.path.expanduser(f"~/.{MOKAGI_home}"))
REPAIR_COMMAND_BLOCKLIST = re.compile(r"[;&|`$<>]|(?:^|\s)(?:rm|mv|cp|dd|mkfs|shutdown|reboot|kill|pkill|sudo|chmod|chown)\b", re.I)

def _repair_path(relative_path):
    relative_path = (relative_path or '').replace('\\', '/').lstrip('/')
    full_path = os.path.realpath(os.path.join(REPAIR_ROOT, relative_path))
    if full_path != REPAIR_ROOT and not full_path.startswith(REPAIR_ROOT + os.sep):
        raise ValueError('路徑必須位於 .mok 目錄內')
    return full_path

@app.route('/repair')
def repair_page():
    return send_file(os.path.join(BASE_DIR, '建檔案', '主機維修台.html'))

@app.route('/api/repair/tree')
def repair_tree():
    def walk(path, depth=0):
        if depth > 8:
            return []
        result = []
        try:
            entries = sorted(os.scandir(path), key=lambda item: (not item.is_dir(follow_symlinks=False), item.name.lower()))
            for entry in entries:
                if entry.name in {'.git', '__pycache__'}:
                    continue
                rel = os.path.relpath(entry.path, REPAIR_ROOT).replace(os.sep, '/')
                item = {'name': entry.name, 'path': rel, 'is_dir': entry.is_dir(follow_symlinks=False)}
                if item['is_dir']:
                    item['children'] = walk(entry.path, depth + 1)
                result.append(item)
        except (OSError, PermissionError):
            pass
        return result
    return jsonify({'root': '.mok', 'tree': walk(REPAIR_ROOT)})

@app.route('/api/repair/file', methods=['GET', 'PUT'])
def repair_file():
    try:
        data = request.get_json(silent=True) or {} if request.method == 'PUT' else request.args
        path = data.get('path', '')
        full_path = _repair_path(path)
        if request.method == 'GET':
            if not os.path.isfile(full_path):
                return jsonify({'error': '找不到檔案'}), 404
            if os.path.getsize(full_path) > 2 * 1024 * 1024:
                return jsonify({'error': '檔案超過 2MB，請使用命令查看'}), 413
            with open(full_path, 'r', encoding='utf-8', errors='replace') as handle:
                return jsonify({'path': path, 'content': handle.read()})
        content = data.get('content', '')
        if not os.path.isfile(full_path):
            return jsonify({'error': '只能修改已存在的檔案'}), 404
        if len(content.encode('utf-8')) > 2 * 1024 * 1024:
            return jsonify({'error': '檔案超過 2MB'}), 413
        with open(full_path, 'w', encoding='utf-8') as handle:
            handle.write(content)
        return jsonify({'ok': True, 'path': path})
    except (ValueError, OSError) as exc:
        return jsonify({'error': str(exc)}), 400

@app.route('/api/repair/exec', methods=['POST'])
def repair_exec():
    command = (request.get_json(silent=True) or {}).get('command', '').strip()
    if not command:
        return jsonify({'error': '缺少 command'}), 400
    if len(command) > 500 or REPAIR_COMMAND_BLOCKLIST.search(command):
        return jsonify({'error': '命令被安全規則拒絕'}), 403
    try:
        result = subprocess.run(command, shell=True, cwd=REPAIR_ROOT, capture_output=True, text=True, timeout=20)
        return jsonify({'command': command, 'returncode': result.returncode, 'stdout': result.stdout[-12000:], 'stderr': result.stderr[-12000:]})
    except subprocess.TimeoutExpired as exc:
        return jsonify({'command': command, 'returncode': 124, 'stdout': (exc.stdout or '')[-12000:], 'stderr': '命令超過 20 秒，已停止'}), 408
    except OSError as exc:
        return jsonify({'error': str(exc)}), 500





# 保存檔案接口
@app.route('/api/save_file', methods=['POST'])
def save_file():
    data = request.get_json()
    path = data.get('path')
    content = data.get('content')
    if not path:
        return {"status": "error", "error": "Missing path"}, 400


# ===== 新增：建立檔案 =====
@app.route('/api/create_file', methods=['POST'])
def create_file():
    try:
        data = request.get_json()
        if data is None:
            return {"status": "error", "error": "Invalid JSON"}, 400
        path = data.get('path')
        content = data.get('content', '')
        if not path:
            return {"status": "error", "error": "Missing path"}, 400
        full_path = os.path.join(WATCH_PATH, path)
        # 安全檢查
        if not any(full_path.startswith(os.path.join(WATCH_PATH, p)) for p in ALLOWED_PATHS):
            return {"status": "error", "error": "Access denied"}, 403
        # 確保目錄存在
        os.makedirs(os.path.dirname(full_path), exist_ok=True)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(content)
        return {"status": "ok", "path": path}
    except Exception as e:
        return {"status": "error", "error": str(e)}, 500

@app.route('/api/create_folder', methods=['POST'])
def create_folder():
    try:
        data = request.get_json()
        if data is None:
            return {"status": "error", "error": "Invalid JSON"}, 400
        path = data.get('path')
        if not path:
            return {"status": "error", "error": "Missing path"}, 400
        full_path = os.path.join(WATCH_PATH, path)
        if not any(full_path.startswith(os.path.join(WATCH_PATH, p)) for p in ALLOWED_PATHS):
            return {"status": "error", "error": "Access denied"}, 403
        os.makedirs(full_path, exist_ok=True)
        return {"status": "ok", "path": path}
    except Exception as e:
        return {"status": "error", "error": str(e)}, 500
    
    










# ===== 暫存圖片上傳：貼上截圖後先落盤，AI 用 vision 分析完再刪除 =====
TMP_IMAGE_DIR = os.path.join(os.path.expanduser(f"~/.{MOKAGI_home}"), "_tmp", "images")

@app.route('/api/upload_image', methods=['POST'])
def upload_image():
    """暫存貼上的截圖/圖片，回傳磁碟路徑給前端，讓 AI 用 vision 工具分析後刪除"""
    try:
        data = request.get_json(silent=True) or {}
        file_data = data.get('data', '')
        filename = data.get('filename', '')
        mime_type = data.get('mime_type', 'image/png')
        if not file_data:
            return jsonify({"success": False, "error": "Missing data"}), 400

        ext_map = {
            'image/png': '.png', 'image/jpeg': '.jpg', 'image/jpg': '.jpg',
            'image/gif': '.gif', 'image/webp': '.webp', 'image/bmp': '.bmp',
        }
        ext = ext_map.get((mime_type or '').split(';')[0].lower(), '.png')

        # 安全檔名：只保留中英文、數字、底線、連字號、點
        safe_name = re.sub(r'[^\w.\-\u4e00-\u9fff]', '_', filename or '')
        if not safe_name:
            safe_name = f"image_{int(time.time() * 1000)}"
        if not safe_name.lower().endswith(('.png', '.jpg', '.jpeg', '.gif', '.webp', '.bmp')):
            safe_name += ext

        os.makedirs(TMP_IMAGE_DIR, exist_ok=True)

        # 自動清理：刪除超過 60 分鐘的暫存圖，避免累積
        try:
            _now = time.time()
            for _old in os.listdir(TMP_IMAGE_DIR):
                _old_path = os.path.join(TMP_IMAGE_DIR, _old)
                if os.path.isfile(_old_path) and _now - os.path.getmtime(_old_path) > 3600:
                    os.remove(_old_path)
        except Exception:
            pass

        full_path = os.path.join(TMP_IMAGE_DIR, safe_name)
        if os.path.exists(full_path):
            _stem, _e = os.path.splitext(safe_name)
            full_path = os.path.join(TMP_IMAGE_DIR, f"{_stem}_{int(time.time() * 1000)}{_e}")

        raw = base64.b64decode(file_data)
        with open(full_path, 'wb') as f:
            f.write(raw)

        return jsonify({"success": True, "path": full_path, "filename": os.path.basename(full_path)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

TMP_AUDIO_DIR = os.path.join(os.path.expanduser(f"~/.{MOKAGI_home}"), "_tmp", "audio")

TMP_TEXT_DIR = os.path.join(os.path.expanduser(f"~/.{MOKAGI_home}"), "_tmp", "text")

@app.route('/api/upload_text', methods=['POST'])
def upload_text():
    """暫存大量文字，讓對話只傳文件路徑；暫存文件與其他附件同樣自動清理。"""
    try:
        data = request.get_json(silent=True) or {}
        content = data.get('content', '')
        if not isinstance(content, str) or not content:
            return jsonify({"success": False, "error": "Missing content"}), 400

        os.makedirs(TMP_TEXT_DIR, exist_ok=True)
        now = time.time()
        for old_name in os.listdir(TMP_TEXT_DIR):
            old_path = os.path.join(TMP_TEXT_DIR, old_name)
            if os.path.isfile(old_path) and now - os.path.getmtime(old_path) > 3600:
                try:
                    os.remove(old_path)
                except OSError:
                    pass

        filename = f"chat_{int(now * 1000)}_{_uuid.uuid4().hex[:8]}.txt"
        full_path = os.path.join(TMP_TEXT_DIR, filename)
        with open(full_path, 'w', encoding='utf-8') as handle:
            handle.write(content)
        return jsonify({"success": True, "path": full_path, "filename": filename})
    except Exception as exc:
        return jsonify({"success": False, "error": str(exc)}), 500


@app.route('/api/upload_audio', methods=['POST'])
def upload_audio():
    """暫存錄音/音頻，回傳磁碟路徑給前端，讓 AI 用 stt 工具轉錄後刪除"""
    try:
        data = request.get_json(silent=True) or {}
        file_data = data.get('data', '')
        filename = data.get('filename', '')
        mime_type = data.get('mime_type', 'audio/webm')
        if not file_data:
            return jsonify({"success": False, "error": "Missing data"}), 400

        ext_map = {
            'audio/webm': '.webm', 'audio/ogg': '.ogg', 'audio/mp4': '.m4a',
            'audio/mpeg': '.mp3', 'audio/wav': '.wav', 'audio/x-wav': '.wav',
            'audio/mp3': '.mp3', 'audio/aac': '.aac', 'audio/opus': '.opus',
        }
        mime_key = (mime_type or '').split(';')[0].lower()
        ext = ext_map.get(mime_key, '.webm')

        safe_name = re.sub(r'[^\w.\-\u4e00-\u9fff]', '_', filename or '')
        if not safe_name:
            safe_name = f"audio_{int(time.time() * 1000)}"
        if not safe_name.lower().endswith(('.webm', '.ogg', '.m4a', '.mp3', '.wav', '.aac', '.opus', '.mp4')):
            safe_name += ext

        os.makedirs(TMP_AUDIO_DIR, exist_ok=True)

        try:
            _now = time.time()
            for _old in os.listdir(TMP_AUDIO_DIR):
                _old_path = os.path.join(TMP_AUDIO_DIR, _old)
                if os.path.isfile(_old_path) and _now - os.path.getmtime(_old_path) > 3600:
                    os.remove(_old_path)
        except Exception:
            pass

        full_path = os.path.join(TMP_AUDIO_DIR, safe_name)
        if os.path.exists(full_path):
            _stem, _e = os.path.splitext(safe_name)
            full_path = os.path.join(TMP_AUDIO_DIR, f"{_stem}_{int(time.time() * 1000)}{_e}")

        raw = base64.b64decode(file_data)
        with open(full_path, 'wb') as f:
            f.write(raw)

        return jsonify({"success": True, "path": full_path, "filename": os.path.basename(full_path)})
    except Exception as e:
        return jsonify({"success": False, "error": str(e)}), 500

@app.route('/api/raw/<path:sub_path>')
def send_raw_file(sub_path):
    if not sub_path.startswith(ALLOWED_PATHS):
        return "Unauthorized", 403
    directory = os.path.join(WATCH_PATH, os.path.dirname(sub_path))
    filename = os.path.basename(sub_path)
    return send_from_directory(directory, filename)

@app.route('/api/env_files')
def get_env_files_api():
    files = get_env_files()
    current = os.path.basename(CURRENT_ENV_PATH) if CURRENT_ENV_PATH else ""
    agents = []
    for f in files:
        agent_name = f.lstrip('.')
        icon = '🌸'
        post = ''
        desc = ''
        tags = ''
        group = ''
        config_path = os.path.join(ENV_DIR, agent_name, f)
        try:
            with open(config_path, 'r', encoding='utf-8') as cf:
                for line in cf:
                    line = line.strip()
                    if line.startswith('MOK_AGENT_ICON='):
                        val = line.split('=', 1)[1].strip()
                        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                            val = val[1:-1]
                        icon = val
                    if line.startswith('MOK_AGENT_POST='):
                        val = line.split('=', 1)[1].strip()
                        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                            val = val[1:-1]
                        post = val
                    if line.startswith('MOK_AGENT_DESC='):
                        val = line.split('=', 1)[1].strip()
                        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                            val = val[1:-1]
                        desc = val
                    if line.startswith('MOK_AGENT_TAGS='):
                        val = line.split('=', 1)[1].strip()
                        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                            val = val[1:-1]
                        tags = val
                    if line.startswith('MOK_AGENT_group='):
                        group = line.split('=', 1)[1].strip()
        except:
            pass

        # ----- 新增：查詢該 agent 最後一條消息的時間 -----
        last_active = 0
        try:
            with closing(sqlite3.connect(DB_PATH)) as conn:
                cursor = conn.execute(
                    'SELECT timestamp FROM chat_history WHERE agent = ? ORDER BY timestamp DESC LIMIT 1',
                    (agent_name,)
                )
                row = cursor.fetchone()
                if row:
                    last_active = row[0]
        except Exception as e:
            print(f"獲取 agent {agent_name} 最後活躍時間失敗: {e}")
        # ----- 結束 -----

        agents.append({"name": agent_name, "file": f, "icon": icon, "post": post, "desc": desc, "tags": tags, "group": group, "last_active": last_active, "is_running": agent_name in _running_agents})

    # ----- 按 last_active 降序排序（最新排最前）-----
    agents.sort(key=lambda x: x.get('last_active', 0), reverse=True)

    return {"agents": agents, "current": current}






# 一鍵清空所有 agent 的 _job.json 內容
@app.route('/api/clear_all_jobs', methods=['POST'])
def clear_all_jobs():
    """清空 ENV_DIR 下所有 agent 的 _job.json（寫入空物件 {}）"""
    cleared = []
    errors = []
    try:
        items = os.listdir(ENV_DIR)
    except Exception as e:
        return jsonify({"success": False, "error": "無法讀取 agent 目錄: " + str(e)}), 500
    for item in items:
        agent_dir = os.path.join(ENV_DIR, item)
        if not os.path.isdir(agent_dir):
            continue
        job_path = os.path.join(agent_dir, '_job.json')
        if not os.path.exists(job_path):
            continue
        try:
            with open(job_path, 'w', encoding='utf-8') as f:
                f.write('{}')
            cleared.append(item)
        except Exception as e:
            errors.append(item + ": " + str(e))
    return jsonify({
        "success": True,
        "cleared_count": len(cleared),
        "cleared": cleared,
        "errors": errors
    })


# 建新 agent 的 API，會在 ENV_DIR 下創建對應的 .文件 和 文件夾
@app.route('/api/create_agent', methods=['POST'])
def create_agent():
    import os
    import urllib.request
    data = request.get_json() or {}
    name = data.get('name', '').strip()
    if not name:
        return {"status": "error", "message": "名字不能為空"}, 400
    # 簡單校驗，防止路徑遍歷
    if not all(c.isalnum() or c in ('-', '_', '.') for c in name):
        return {"status": "error", "message": "名字只能包含字母、數字、下劃線、中劃線"}, 400
    # 新結構：根目錄為 ~/.mok/agent
    mok_home = os.path.expanduser(f"~/.{MOKAGI_home}/agent")
    agent_dir = os.path.join(mok_home, name)
    dot_file = os.path.join(agent_dir, f'.{name}')
    if os.path.exists(dot_file):
        return {"status": "error", "message": f"'{name}' 已存在，請更換名字"}, 400
    # 下載模板
    template_url = "https://raw.githubusercontent.com/MOK2026/MOKAGI/refs/heads/main/env.env"
    try:
        with urllib.request.urlopen(template_url) as resp:
            content = resp.read().decode('utf-8')
    except Exception as e:
        return {"status": "error", "message": f"下載模板失敗: {str(e)}"}, 500
    # 替換佔位符
    content = content.replace('__MOK_AGENT_NAME_PLACEHOLDER__', name)
    # 確保目錄存在
    os.makedirs(agent_dir, exist_ok=True)
    # 寫入配置文件
    with open(dot_file, 'w', encoding='utf-8') as f:
        f.write(content)
    # 創建 soul 子目錄
    soul_dir = os.path.join(agent_dir, 'soul')
    os.makedirs(soul_dir, exist_ok=True)
    # 處理角色模板（Agency Agents 遠端 .md / 女媧人物範例本地 SKILL.md）
    role_url = data.get('role_url', '').strip()
    agent_md_created = False
    if role_url:
        try:
            _is_local = role_url.startswith('/') or role_url.startswith('file://')
            if _is_local:
                # 本地角色模板（女媧人物範例）→ 直接讀取檔案，並複製附屬資料（references 等）
                import shutil
                _local_path = role_url.replace('file://', '')
                _nuwa_root = os.path.expanduser(f"~/.{MOKAGI_home}/skill/nuwa/nuwa-examples")
                if not _local_path.startswith(_nuwa_root):
                    raise ValueError(f"不允許讀取此路徑: {_local_path}")
                with open(_local_path, 'r', encoding='utf-8') as _lf:
                    _role_content = _lf.read()
                _ex_dir = os.path.dirname(_local_path)
                _ex_name = os.path.basename(_ex_dir)
                _soul_target = os.path.join(soul_dir, _ex_name)
                if os.path.isdir(_ex_dir) and not os.path.exists(_soul_target):
                    shutil.copytree(_ex_dir, _soul_target)
            else:
                with urllib.request.urlopen(role_url) as _resp:
                    _role_content = _resp.read().decode('utf-8')
            _agent_md_path = os.path.join(soul_dir, 'agent.md')
            with open(_agent_md_path, 'w', encoding='utf-8') as _f:
                _f.write(_role_content)
            agent_md_created = True
        except Exception as _e:
            print(f"下載角色模板失敗: {_e}")
    if not agent_md_created:
        _default_md = "# " + name + "\n\n## 角色設定\n\n等待主人設定...\n"
        _agent_md_path = os.path.join(soul_dir, 'agent.md')
        with open(_agent_md_path, 'w', encoding='utf-8') as _f:
            _f.write(_default_md)
    return {"status": "ok", "message": f"成功創建 Agent '{name}'，配置文件 {dot_file} 和目錄 {agent_dir}/ 已建立", "agent_md_created": agent_md_created}





# ========== Agency Agents 角色列表 API ==========
_AGENCY_ROLES_CACHE = None

def _fetch_agency_roles():
    """從 GitHub README 解析所有 agency-agents 角色，返回 [{name, division, specialty, raw_url}]"""
    global _AGENCY_ROLES_CACHE
    if _AGENCY_ROLES_CACHE is not None:
        return _AGENCY_ROLES_CACHE
    import urllib.request, re
    roles = []
    try:
        readme_url = "https://raw.githubusercontent.com/msitarzewski/agency-agents/main/README.md"
        with urllib.request.urlopen(readme_url) as resp:
            text = resp.read().decode('utf-8')
        # 匹配表格中的 agent 連結: [Name](path/to/file.md) | Specialty | Description
        pattern = re.compile(r'\[([^\]]+)\]\(([^)]+\.md)\)\s*\|\s*([^|]+?)\s*\|')
        divisions = {
            'engineering': '💻 Engineering',
            'security': '🛡️ Security', 
            'design': '🎨 Design',
            'marketing': '📣 Marketing',
            'product': '📦 Product',
            'finance': '💰 Finance',
            'healthcare': '🏥 Healthcare',
            'academic': '🎓 Academic',
            'game-development': '🎮 Game Development',
            'gis': '🗺️ GIS',
            'paid-media': '🎯 Paid Media',
            'project-management': '📋 Project Management',
            'sales': '💼 Sales',
            'spatial-computing': '🥽 Spatial Computing',
            'specialized': '✨ Specialized',
            'support': '🆘 Support',
            'testing': '🧪 Testing',
        }
        for m in pattern.finditer(text):
            name = m.group(1).strip()
            rel_path = m.group(2).strip()
            specialty = m.group(3).strip()
            raw_url = f"https://raw.githubusercontent.com/msitarzewski/agency-agents/main/{rel_path}"
            # 從路徑判斷 division
            division_key = rel_path.split('/')[0] if '/' in rel_path else 'engineering'
            division_label = divisions.get(division_key, f'📁 {division_key}')
            roles.append({
                "name": name,
                "division": division_label,
                "specialty": specialty,
                "raw_url": raw_url,
                "id": rel_path.replace('/', '_').replace('.md', '')
            })
        _AGENCY_ROLES_CACHE = roles
    except Exception as e:
        print(f"獲取 agency roles 失敗: {e}")
        _AGENCY_ROLES_CACHE = []
    return _AGENCY_ROLES_CACHE


@app.route('/api/agency_roles')
def agency_roles():
    """返回 agency-agents 角色列表"""
    roles = _fetch_agency_roles()
    return {"status": "ok", "roles": roles, "total": len(roles)}




@app.route('/api/set_env', methods=['POST'])
def set_env():
    data = request.get_json()
    filename = data.get('filename')
    if not filename:
        return {"status": "error", "message": "Missing filename"}, 400
    # filename 以 '.' 開頭，例如 '.客服'，提取 agent 名稱
    agent_name = filename.lstrip('.')
    new_path = os.path.join(ENV_DIR, agent_name, filename)
    if not os.path.exists(new_path):
        return {"status": "error", "message": "File not found"}, 404
    reload_config(new_path)
    return {"status": "ok", "current": filename, "models": AVAILABLE_MODELS, "options": OLLAMA_OPTIONS}

@app.route('/api/mok_config')
def get_mok_config():
    return MOK_CONFIG

@app.route('/api/models')
def get_models():
    return {"models": AVAILABLE_MODELS, "current_index": CURRENT_MODEL_INDEX}




@app.route('/api/set_model', methods=['POST'])
def set_model():
    global CURRENT_MODEL_INDEX
    data = request.get_json()
    model_name = None
    idx = None
    
    if 'index' in data:
        idx = int(data['index'])
        if 0 <= idx < len(AVAILABLE_MODELS):
            model_name = AVAILABLE_MODELS[idx]['name']
    elif 'name' in data:
        model_name = data['name']
        # 查找索引
        for i, m in enumerate(AVAILABLE_MODELS):
            if m['name'] == model_name:
                idx = i
                break
    
    if not model_name or idx is None:
        return {"status": "error", "message": "Invalid model"}, 400
    
    # 調用 admin 插件的 set_model_in_config 函數
    admin_mod = tool_handler.get_tools().get("admin")
    if not admin_mod or not hasattr(admin_mod, "set_model_in_config"):
        return {"status": "error", "message": "Admin module not loaded"}, 500
    
    # 獲取當前 Agent 名稱和配置
    current_agent_name = os.path.basename(CURRENT_ENV_PATH).lstrip('.')
    # 獲取該 Agent 的配置（從緩存或重新加載）
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    agent_config = loop.run_until_complete(mokagi.get_agent_config(current_agent_name))
    loop.close()
    result_message = admin_mod.set_model_in_config(model_name, agent_config=agent_config)
    
    # 如果成功（消息以 ✅ 開頭），則更新內存中的當前模型索引
    if result_message.startswith("✅"):
        CURRENT_MODEL_INDEX = idx
        # 清除該 agent 的配置緩存，讓下次請求重新加載（包含新模型）
        # 需要知道當前 agent 名稱
        current_agent_name = os.path.basename(CURRENT_ENV_PATH).lstrip('.')
        if current_agent_name in _agent_config_cache:
            del _agent_config_cache[current_agent_name]
        # 注意：不再直接修改 mokagi 的全局變量
    
        # 新增：異步重啟統一進程（2 秒後重啟，讓當前請求先返回）
        import subprocess
        subprocess.Popen(
            "(sleep 2 && pm2 restart mok_agi) > /dev/null 2>&1 &",
            shell=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL
        )
    return {"status": "ok" if result_message.startswith("✅") else "error", "message": result_message, "model": {"name": model_name}}










@app.route('/api/current_model')
def get_current_model():
    config = get_current_model_config()
    return {"model": config['name']}

@app.route('/api/tools')
def get_tools():
    """返回所有已加載的工具列表（用於前端展示）"""
    tools_list = []
    for mod in tool_handler.get_tools().values():
        if hasattr(mod, "PLUGIN_INFO"):
            info = mod.PLUGIN_INFO
            tools_list.append({
                "command": info.get("command", ""),
                "description": info.get("description", ""),
                "icon": info.get("icon", "🔧")
            })
    # 按命令名稱排序
    tools_list.sort(key=lambda x: x["command"])
    return {"tools": tools_list}


@app.route('/api/heart/status')
def heart_status():
    """返回心跳任務狀態"""
    import asyncio
    heart_mod = tool_handler.get_tools().get("heart")
    if not heart_mod:
        return {"status": "error", "message": "心跳工具未加載"}
    try:
        result = asyncio.run(heart_mod.get_status())
        return {"status": "ok", "result": result}
    except Exception as e:
        return {"status": "error", "message": str(e)}
# ---------- 系統監控 API（保持不變）----------
@app.route('/api/system/cpu')
def system_cpu():
    import subprocess
    try:
        result = subprocess.run(
            "grep 'cpu ' /proc/stat | awk '{print ($2+$4)*100/($2+$4+$5)}'",
            shell=True, capture_output=True, text=True, timeout=5
        )
        cpu_percent = float(result.stdout.strip()) if result.stdout else 0.0
        return {"success": True, "percent": round(cpu_percent, 1)}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.route('/api/system/top')
def system_top():
    import subprocess
    try:
        result = subprocess.run("top -bn1 -o %CPU", shell=True, capture_output=True, text=True, timeout=10)
        lines = result.stdout.splitlines()
        header = lines[:5] if len(lines) >= 5 else lines
        process_lines = [line for line in lines[5:] if line.strip()]
        return {"success": True, "header": header, "processes": process_lines[:20]}
    except Exception as e:
        return {"success": False, "error": str(e)}

@app.route('/api/system/meminfo')
def system_meminfo():
    import subprocess
    try:
        result = subprocess.run("cat /proc/meminfo", shell=True, capture_output=True, text=True, timeout=5)
        if result.returncode != 0:
            return {"success": False, "error": "無法讀取內存信息"}
        meminfo = {}
        for line in result.stdout.splitlines():
            if not line.strip():
                continue
            parts = line.split(':')
            if len(parts) == 2:
                key = parts[0].strip()
                value = parts[1].strip().split()[0]
                if value.isdigit():
                    meminfo[key] = int(value)
        mem_total = meminfo.get('MemTotal', 0)
        mem_free = meminfo.get('MemFree', 0)
        mem_available = meminfo.get('MemAvailable', 0)
        buffers = meminfo.get('Buffers', 0)
        cached = meminfo.get('Cached', 0)
        swap_total = meminfo.get('SwapTotal', 0)
        swap_free = meminfo.get('SwapFree', 0)
        def to_mb(kb): return round(kb / 1024, 1)
        return {
            "success": True,
            "total_mb": to_mb(mem_total),
            "used_mb": to_mb(mem_total - mem_free - buffers - cached),
            "buffers_mb": to_mb(buffers),
            "cached_mb": to_mb(cached),
            "free_mb": to_mb(mem_free),
            "available_mb": to_mb(mem_available),
            "swap_total_mb": to_mb(swap_total),
            "swap_used_mb": to_mb(swap_total - swap_free) if swap_total > 0 else 0
        }
    except Exception as e:
        return {"success": False, "error": str(e)}





# 查詢token API

# ===== 統一計費 API（唯一價格源：core/mok_price.py）=====
@app.route('/api/price')
def api_price():
    """返回 MOKAGI 統一收費標準（前端 JS 從此讀取，修改 mok_price.py 即全局生效）"""
    try:
        import sys
        core_dir = os.path.join(os.path.expanduser(f"~/.{MOKAGI_home}"), "core")
        if core_dir not in sys.path:
            sys.path.insert(0, core_dir)
        from mok_price import to_dict
        return jsonify(to_dict())
    except Exception as e:
        return jsonify({"currency": "HKD", "price_per_million": 68, "price_per_token": 0.000068, "setup_fee": 5000, "github": "https://github.com/MOK2026/MOKAGI", "display": {"per_million": "HK$68 / 百萬 token", "setup_fee": "HK$5,000"}})
@app.route('/api/token_stats')
def token_stats():
    agent = request.args.get('agent')
    model = request.args.get('model')
    user = request.args.get('user')
    
    where_clauses = []
    params = []
    if agent:
        where_clauses.append("agent_name = ?")
        params.append(agent)
    if model:
        where_clauses.append("model_name = ?")
        params.append(model)
    if user:
        where_clauses.append("user_id = ?")
        params.append(user)
    where = "WHERE " + " AND ".join(where_clauses) if where_clauses else ""
    
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        
        # 總用量
        total = conn.execute(f"SELECT COALESCE(SUM(total_tokens), 0) FROM token_usage {where}", params).fetchone()[0]
        
        # 按模型分組
        by_model = conn.execute(f"SELECT model_name, SUM(total_tokens) as tokens FROM token_usage {where} GROUP BY model_name ORDER BY tokens DESC", params).fetchall()
        
        # 按 Agent 分組
        by_agent = conn.execute(f"SELECT agent_name, SUM(total_tokens) as tokens FROM token_usage {where} GROUP BY agent_name ORDER BY tokens DESC", params).fetchall()
        
        # 最近 20 次調用
        recent = conn.execute(f"SELECT user_id, agent_name, model_name, prompt_tokens, completion_tokens, total_tokens, timestamp, conversation_id, workflow_id FROM token_usage {where} ORDER BY timestamp DESC LIMIT 20", params).fetchall()
        
        # 單次對話用量（按 conversation_id 彙總）
        if not agent and not model and not user:
            # 如果無篩選，返回最近 10 次會話的彙總
            conv_stats = conn.execute('''
                SELECT conversation_id, agent_name, SUM(total_tokens) as tokens, MIN(timestamp) as start_time
                FROM token_usage 
                WHERE conversation_id IS NOT NULL
                GROUP BY conversation_id
                ORDER BY start_time DESC
                LIMIT 10
            ''').fetchall()
        else:
            conv_stats = []
        
    return {
        "total_tokens": total,
        "by_model": [dict(row) for row in by_model],
        "by_agent": [dict(row) for row in by_agent],
        "recent": [dict(row) for row in recent],
        "conversations": [{"id": row[0], "agent": row[1], "tokens": row[2], "time": row[3]} for row in conv_stats]
    }










# ---------- 聊天曆史 API（供前端展示，不使用 mokagi 的歷史）----------
@app.route('/api/chat_history', methods=['GET'])
def get_chat_history():
    agent = request.args.get('agent', '')
    if not agent:
        return {"error": "Missing agent parameter"}, 400
    limit = request.args.get('limit', default=20, type=int)
    offset = request.args.get('offset', default=0, type=int)
    if limit > 100:
        limit = 100  # 防止一次性取過多

    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        # 總記錄數（用於判斷是否有更多）
        total = conn.execute(
            'SELECT COUNT(*) FROM chat_history WHERE agent = ?', (agent,)
        ).fetchone()[0]

        # 按 id 降序取最新數據（新消息在前）
        rows = conn.execute(
            '''SELECT id, conv_id, role, content, think_content, rounds, timestamp
               FROM chat_history WHERE agent = ?
               ORDER BY id DESC LIMIT ? OFFSET ?''',
            (agent, limit, offset)
        ).fetchall()

        messages = []
        for row in rows:
            _rounds = None
            if row["rounds"]:
                try:
                    _rounds = json.loads(row["rounds"])
                except Exception:
                    _rounds = None
            messages.append({
                "id": row["id"],
                "conv_id": row["conv_id"],
                "role": row["role"],
                "content": row["content"],
                "thinkContent": row["think_content"],
                "rounds": _rounds,
                "timestamp": row["timestamp"]
            })

        has_more = (offset + limit) < total

    return {"messages": messages, "has_more": has_more}

@app.route('/api/chat_history', methods=['POST'])
def post_chat_history():
    data = request.get_json()
    agent = data.get('agent')
    role = data.get('role')
    content = data.get('content')
    think_content = data.get('thinkContent')
    conv_id = data.get('conv_id')
    timestamp = data.get('timestamp', time.time())
    if not agent or not role:
        return {"error": "Missing required fields"}, 400
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute(
            'INSERT INTO chat_history (agent, role, content, think_content, conv_id, timestamp) VALUES (?, ?, ?, ?, ?, ?)',
            (agent, role, content, think_content, conv_id, timestamp)
        )
        conn.commit()
    return {"status": "ok"}

@app.route('/api/chat_history', methods=['DELETE'])
def delete_chat_history():
    agent = request.args.get('agent', '')
    if not agent:
        return {"error": "Missing agent parameter"}, 400
    with closing(sqlite3.connect(DB_PATH)) as conn:
        conn.execute('DELETE FROM chat_history WHERE agent = ?', (agent,))
        conn.commit()
    # 同時清除 mokagi 內存中的歷史
    clear_history(agent)
    return {"status": "ok"}

# ---------- 文件監控（保持不變）----------
def start_observer():
    event_handler = FileChangeHandler(socketio)
    observer = Observer()
    for item in ALLOWED_PATHS:
        target = os.path.join(WATCH_PATH, item)
        if os.path.exists(target):
            observer.schedule(event_handler, target, recursive=os.path.isdir(target))
    observer.start()
    observer.join()




@app.route('/api/chat_history/<int:msg_id>', methods=['PUT'])
def update_chat_history(msg_id):
    """更新指定 ID 的聊天消息內容（用於即時保存流式輸出）"""
    data = request.get_json()
    if not data:
        return {"error": "Missing JSON"}, 400
    content = data.get('content')
    think_content = data.get('think_content')
    if content is None and think_content is None:
        return {"error": "No content to update"}, 400
    with closing(sqlite3.connect(DB_PATH)) as conn:
        updates = []
        params = []
        if content is not None:
            updates.append("content = ?")
            params.append(content)
        if think_content is not None:
            updates.append("think_content = ?")
            params.append(think_content)
        params.append(msg_id)
        sql = f"UPDATE chat_history SET {', '.join(updates)} WHERE id = ?"
        conn.execute(sql, params)
        conn.commit()
    return {"status": "ok"}





@app.route('/api/search', methods=['POST'])
def api_search():
    data = request.get_json()
    if not data:
        return {"error": "Invalid JSON"}, 400
    query = data.get('query', '').strip()
    n_results = data.get('n_results', 10)
    assoc_count = data.get('assoc_count', 5)
    agent_name = data.get('agent')
    if not query:
        return {"error": "Missing query parameter"}, 400

    # 取得當前 Agent 名稱
    if not agent_name:
        agent_name = os.path.basename(CURRENT_ENV_PATH).lstrip('.')

    # 取得使用者識別碼（預設使用 ADMIN_CHAT_ID）
    chat_id = _current_admin_chat_id or _agent_config.get("ADMIN_CHAT_ID") or os.environ.get("ADMIN_CHAT_ID", "web_default")

    # 非同步呼叫 memory 的語義搜索
    import asyncio
    import mokagi
    from tools import memory

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        agent_config = loop.run_until_complete(mokagi.get_agent_config(agent_name))
        result = loop.run_until_complete(
            memory.semantic_search_conversation(
                chat_id=chat_id,
                query=query,
                n_results=n_results,
                assoc_count=assoc_count,
                agent_config=agent_config
            )
        )
    except Exception as e:
        return {"error": str(e)}, 500
    finally:
        loop.close()

    return {"result": result}






























































# ---------- 日誌監控（輪詢方式）----------
_log_subscribers = set()
_log_stop_event = threading.Event()
_log_thread = None

def _fetch_and_send_logs(sid=None):
    """立即獲取最近20行PM2日誌併發送給指定客戶端（或廣播）"""
    try:
        result = subprocess.run(
            ['pm2', 'logs', 'mok_agi', '--lines', '20', '--nostream'],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.stdout:
            for line in result.stdout.splitlines():
                if line.strip():
                    if sid:
                        socketio.emit('log_line', {'message': line}, room=sid)
                    else:
                        socketio.emit('log_line', {'message': line})
    except Exception as e:
        msg = f"❌ 獲取日誌失敗: {e}"
        if sid:
            socketio.emit('log_line', {'message': msg, 'type': 'err'}, room=sid)
        else:
            socketio.emit('log_line', {'message': msg, 'type': 'err'})

def _log_monitor_worker():
    """輪詢PM2日誌並廣播給訂閱者（每3秒一次）"""
    while not _log_stop_event.is_set():
        try:
            result = subprocess.run(
                ['pm2', 'logs', 'mok_agi', '--lines', '20', '--nostream'],
                capture_output=True,
                text=True,
                timeout=5
            )
            if result.stdout:
                for line in result.stdout.splitlines():
                    if line.strip():
                        socketio.emit('log_line', {'message': line})
        except Exception as e:
            socketio.emit('log_line', {'message': f"❌ 輪詢錯誤: {e}", 'type': 'err'})
        time.sleep(3)

@socketio.on('subscribe_logs')
def handle_subscribe_logs():
    global _log_thread, _log_stop_event   # 💦 關鍵修復
    sid = request.sid
    _log_subscribers.add(sid)
    # 立即發送當前日誌快照
    _fetch_and_send_logs(sid)
    # 啟動輪詢線程（僅當未啟動）
    if not _log_thread or not _log_thread.is_alive():
        _log_stop_event.clear()
        _log_thread = threading.Thread(target=_log_monitor_worker, daemon=True)
        _log_thread.start()
        socketio.emit('log_line', {'message': '✅ PM2 日誌監控已啟動（輪詢模式）'}, room=sid)

@socketio.on('unsubscribe_logs')
def handle_unsubscribe_logs():
    sid = request.sid
    _log_subscribers.discard(sid)
    if not _log_subscribers:
        _log_stop_event.set()


# ========== 🌸 Agent 資訊面板 ==========
@socketio.on('get_agent_soul')
def handle_get_agent_soul(data):
    agent_name = data.get('agent', '')
    if not agent_name:
        socketio.emit('agent_soul_result', {'error': '未指定 Agent'}, room=request.sid)
        return

    # 優先讀取 soul/ 目錄下所有 .md 檔案
    soul_dir = os.path.join(ENV_DIR, agent_name, 'soul')
    if os.path.isdir(soul_dir):
        try:
            md_files = sorted([f for f in os.listdir(soul_dir) if f.endswith('.md')])
            if md_files:
                lines = [f'<div style="padding:8px; border-bottom:1px solid #3e3e42; color:#4ec9b0;">📁 soul/ 共 {len(md_files)} 個檔案</div>']
                for fname in md_files:
                    fpath = os.path.join(soul_dir, fname)
                    try:
                        with open(fpath, 'r', encoding='utf-8') as f:
                            content = f.read()
                        if len(content) > 8000:
                            content = content[:8000] + '\n\n... (內容已截斷)'
                        safe = content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                        lines.append(f'<details style="margin:4px 0;"><summary style="cursor:pointer; color:#e0a800; padding:4px 0;">📄 {fname}</summary><pre style="background:#1e1e1e; padding:8px; margin:4px 0; border-radius:6px; white-space:pre-wrap; word-break:break-word; font-size:0.8rem; max-height:400px; overflow-y:auto;">{safe}</pre></details>')
                    except Exception as e:
                        lines.append(f'<div style="color:#ff6b6b;">⚠️ 無法讀取 {fname}: {str(e)}</div>')
                socketio.emit('agent_soul_result', {'content': ''.join(lines)}, room=request.sid)
                return
        except Exception as e:
            socketio.emit('agent_soul_result', {'error': f'讀取 soul 目錄失敗: {str(e)}'}, room=request.sid)
            return

    # 回退：讀取單一 soul.md 或 agent.md
    soul_path = os.path.join(ENV_DIR, agent_name, 'soul.md')
    if not os.path.exists(soul_path):
        soul_path = os.path.join(ENV_DIR, agent_name, 'agent.md')
    if os.path.exists(soul_path):
        try:
            with open(soul_path, 'r', encoding='utf-8') as f:
                content = f.read()
            if len(content) > 30000:
                content = content[:30000] + '\n\n... (內容過長已截斷)'
            socketio.emit('agent_soul_result', {'content': content}, room=request.sid)
        except Exception as e:
            socketio.emit('agent_soul_result', {'error': f'讀取失敗: {str(e)}'}, room=request.sid)
    else:
        socketio.emit('agent_soul_result', {'error': f'找不到 soul 檔案'}, room=request.sid)

@socketio.on('get_agent_jobs')
def handle_get_agent_jobs(data):
    agent_name = data.get('agent', '')
    if not agent_name:
        socketio.emit('agent_jobs_result', {'error': '未指定 Agent'}, room=request.sid)
        return

    # 優先讀取 jobs/ 目錄下所有子目錄（每個 job 一個目錄，內含 job.md）
    jobs_dir = os.path.join(ENV_DIR, agent_name, 'jobs')
    if os.path.isdir(jobs_dir):
        try:
            job_dirs = sorted([d for d in os.listdir(jobs_dir) if os.path.isdir(os.path.join(jobs_dir, d))])
            if job_dirs:
                lines = [f'<div style="padding:8px; border-bottom:1px solid #3e3e42; color:#4ec9b0;">📋 jobs/ 共 {len(job_dirs)} 個工作</div>']
                for jname in job_dirs:
                    job_path = os.path.join(jobs_dir, jname)
                    md_path = os.path.join(job_path, 'job.md')
                    if os.path.isfile(md_path):
                        try:
                            with open(md_path, 'r', encoding='utf-8') as f:
                                content = f.read()
                            if len(content) > 8000:
                                content = content[:8000] + '\n\n... (內容已截斷)'
                            safe = content.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                            lines.append(f'<details style="margin:4px 0;"><summary style="cursor:pointer; color:#e0a800; padding:4px 0;">📌 {jname}</summary><pre style="background:#1e1e1e; padding:8px; margin:4px 0; border-radius:6px; white-space:pre-wrap; word-break:break-word; font-size:0.8rem; max-height:400px; overflow-y:auto;">{safe}</pre></details>')
                        except Exception as e:
                            lines.append(f'<div style="color:#ff6b6b; padding:2px 8px;">⚠️ 無法讀取 job.md: {str(e)}</div>')
                    else:
                        try:
                            other_files = [f for f in os.listdir(job_path) if f != 'job.md']
                            if other_files:
                                lines.append(f'<div style="color:#888; padding:2px 8px; font-size:0.75rem;">📎 附檔: {", ".join(other_files)}</div>')
                        except:
                            pass
                socketio.emit('agent_jobs_result', {'content': ''.join(lines)}, room=request.sid)
                return
        except Exception as e:
            socketio.emit('agent_jobs_result', {'error': f'讀取 jobs 目錄失敗: {str(e)}'}, room=request.sid)
            return

    # 回退：使用 job.py 命令列工具
    try:
        import subprocess
        result = subprocess.run(
            ['python3', '/home/ubuntu/.mok/tools/job.py', 'list', agent_name],
            capture_output=True, text=True, timeout=10
        )
        output = result.stdout.strip() or result.stderr.strip()
        if not output:
            output = '(尚無工作記錄)'
        socketio.emit('agent_jobs_result', {'content': output}, room=request.sid)
    except Exception as e:
        socketio.emit('agent_jobs_result', {'error': f'獲取失敗: {str(e)}'}, room=request.sid)

# ========== 🌸 Agent Logs 面板（讀取 agent logs/ 目錄） ==========
@socketio.on('get_agent_logs')
def handle_get_agent_logs(data):
    agent_name = data.get('agent', '')
    if not agent_name:
        socketio.emit('agent_logs_result', {'error': '未指定 Agent'}, room=request.sid)
        return

    logs_dir = os.path.join(ENV_DIR, agent_name, 'logs')
    if os.path.isdir(logs_dir):
        try:
            all_files = sorted(os.listdir(logs_dir))
            if all_files:
                lines = [f'<div style="padding:8px; border-bottom:1px solid #3e3e42; color:#4ec9b0;">📜 logs/ 共 {len(all_files)} 個檔案</div>']
                for fname in all_files:
                    fpath = os.path.join(logs_dir, fname)
                    if os.path.isfile(fpath):
                        try:
                            with open(fpath, 'r', encoding='utf-8') as f:
                                fcontent = f.read()
                            if len(fcontent) > 8000:
                                fcontent = fcontent[:8000] + '\n\n... (內容過長，已截斷)'
                            escaped = fcontent.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
                            lines.append(f'<details style="margin:4px 0;"><summary style="cursor:pointer; color:#dcdcaa; padding:4px 0;">📄 {fname}</summary><pre style="background:#1e1e1e; padding:8px; margin:4px 0; border-radius:6px; white-space:pre-wrap; word-break:break-word; font-size:0.8rem; max-height:400px; overflow-y:auto;">{escaped}</pre></details>')
                        except Exception:
                            lines.append(f'<div style="padding:6px 8px; margin:2px 0; border-bottom:1px solid #2d2d30;">📄 <span style="color:#dcdcaa;">{fname}</span> <span style="color:#888;">(無法讀取)</span></div>')
                socketio.emit('agent_logs_result', {'content': ''.join(lines)}, room=request.sid)
            else:
                socketio.emit('agent_logs_result', {'content': '<div style="color:#888; padding:12px;">📜 logs/ 目錄為空</div>'}, room=request.sid)
        except Exception as e:
            socketio.emit('agent_logs_result', {'error': f'讀取 logs 目錄失敗: {str(e)}'}, room=request.sid)
    else:
        socketio.emit('agent_logs_result', {'content': '<div style="color:#888; padding:12px;">📜 尚無 logs/ 目錄</div>'}, room=request.sid)


# ========== 🌸 Agent Settings 面板 ==========
@socketio.on('get_agent_settings')
def handle_get_agent_settings(data):
    agent_name = data.get('agent', '')
    if not agent_name:
        socketio.emit('agent_settings_result', {'error': '未指定 Agent'}, room=request.sid)
        return

    raw_data = ''
    lines = [f'<div style="padding:8px; border-bottom:1px solid #3e3e42; color:#4ec9b0;">⚙️ 設定檔</div>']
    agent_dotfile = os.path.join(ENV_DIR, agent_name, f'.{agent_name}')
    if os.path.isfile(agent_dotfile):
        try:
            with open(agent_dotfile, 'r', encoding='utf-8') as f:
                raw = f.read()
            raw_data = raw

            escaped = raw_data.replace('&', '&amp;').replace('<', '&lt;').replace('>', '&gt;')
            lines.append(f'<div style="padding:4px 12px 12px; color:#d4d4d4; white-space:pre-wrap; font-family:monospace; font-size:0.75rem;">{escaped}</div>')
        except Exception as e:
            lines.append(f'<div style="color:#ff6b6b; padding:12px;">讀取設定檔失敗: {str(e)}</div>')
    else:
        lines.append(f'<div style="color:#888; padding:12px;">尚無 .{agent_name} 設定檔</div>')

    soul_dir = os.path.join(ENV_DIR, agent_name, 'soul')
    if os.path.isdir(soul_dir):
        lines.append(f'<div style="padding:8px; border-bottom:1px solid #3e3e42; color:#4ec9b0; margin-top:12px;">📁 soul/ 目錄設定檔案</div>')
        try:
            for fname in sorted(os.listdir(soul_dir)):
                fpath = os.path.join(soul_dir, fname)
                if os.path.isfile(fpath):
                    size = os.path.getsize(fpath)
                    lines.append(f'<div style="padding:4px 12px; color:#dcdcaa;">📄 {fname} <span style="color:#888;">({size} bytes)</span></div>')
        except Exception as e:
            lines.append(f'<div style="color:#ff6b6b; padding:12px;">讀取 soul 目錄失敗: {str(e)}</div>')

    socketio.emit('agent_settings_result', {'content': ''.join(lines), 'raw': raw_data}, room=request.sid)

@socketio.on('save_agent_settings')
def handle_save_agent_settings(data):
    agent_name = data.get('agent', '')
    content = data.get('content', '')
    if not agent_name:
        socketio.emit('agent_settings_saved', {'error': '未指定 Agent'}, room=request.sid)
        return
    agent_dotfile = os.path.join(ENV_DIR, agent_name, f'.{agent_name}')
    try:
        with open(agent_dotfile, 'w', encoding='utf-8') as f:
            f.write(content)
        socketio.emit('agent_settings_saved', {'success': True}, room=request.sid)
    except Exception as e:
        socketio.emit('agent_settings_saved', {'error': f'儲存失敗: {str(e)}'}, room=request.sid)

@app.before_request
def handle_static():
    if request.path.startswith('/static/'):
        filename = request.path[8:]  # 去掉 '/static/'
        return send_from_directory(static_dir, filename)
        
# ---------- 動態頁面路由（自動匹配 templates 下的 .html）----------
@app.route('/<path:page>')
def dynamic_page(page):
    # 嘗試渲染 HTML 模板（靜態資源已被 before_request 攔截）
    if page.endswith('.html'):
        template = page
    else:
        template = page + '.html'
    try:
        return render_template(template)
    except Exception:
        pass
    # 嘗試目錄/index.html（例如 mokAfight_OK_0725 → mokAfight_OK_0725/index.html）
    if not page.endswith('.html'):
        try:
            return render_template(page.rstrip('/') + '/index.html')
        except Exception:
            pass
    return "Page not found", 404
        
        
        
        
@app.route('/debug_static')
def debug_static():
    import os
    path = os.path.join(static_dir, 'style.css')
    return f"static_dir: {static_dir}<br>style.css exists: {os.path.exists(path)}"

















'''
@app.route('/api/pending_tasks')
def get_pending_tasks():
    agent = request.args.get('agent')
    if not agent:
        return {"error": "Missing agent parameter"}, 400

    user_id = _current_admin_chat_id or _agent_config.get("ADMIN_CHAT_ID") or os.environ.get("ADMIN_CHAT_ID", "web_default")
    
    # 直接讀取 _job.json
    import json
    import os
    task_file = os.path.expanduser(f"~/.{MOKAGI_home}/agent/{agent}/_job.json")
    tasks = []
    if os.path.exists(task_file):
        with open(task_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
        user_key = f"{user_id}_{agent}"
        tasks_dict = data.get(user_key, {})
        for code, task in tasks_dict.items():
            tasks.append({
                "code": code,
                "goal": task.get("goal", "未知任務")[:60],
                "progress": task.get("progress", ""),
                "summary": task.get("summary", "")
            })
    return {"tasks": tasks}



# ===== 前端效能優化：靜態資源快取 & 壓縮 =====
import gzip, io

STATIC_CACHE_SECONDS = 86400  # 24小時靜態資源快取

# ===== 方案二：檔案時間戳自動版本（每次打開都更新）=====
# 原理：HTML 由 Flask 動態渲染（render_template），每次請求頁面時，
#       自動把本地 CSS/JS 引用的版本號替換成該檔案的修改時間（mtime）。
#       改一次檔案 → 版本號自動變 → 瀏覽器重新下載最新檔案；
#       檔案沒改 → 版本號不變 → 瀏覽器繼續用快取。兼顧「最新」與「效能」。
_ASSET_VERSION_RE = re.compile(
    r"(?P<attr>(?:href|src)\s*=\s*[\"\'])(?P<url>(?!https?:|//|data:|#|javascript:)[^\"\']*?\.(?:css|js))(?P<query>\?[^\"\']*)?(?P<end>[\"\'])",
    re.IGNORECASE
)

def _resolve_local_asset(url):
    """將 HTML 中的資源 URL 解析為本地檔案絕對路徑；非本地資源回傳 None"""
    clean = url.split("?")[0]
    if clean.startswith("/static/"):
        return os.path.join(static_dir, clean[len("/static/"):])
    if clean.startswith("/"):
        return os.path.join(BASE_DIR, clean.lstrip("/"))
    # 相對路徑：相對於當前請求頁面的目錄
    req_dir = os.path.dirname(request.path)
    if req_dir == "/":
        return os.path.join(BASE_DIR, clean)
    return os.path.join(BASE_DIR, req_dir.lstrip("/"), clean)

def _inject_asset_versions(html):
    """掃描 HTML，為本地 css/js 引用注入 ?v=<檔案 mtime>"""
    def repl(m):
        url, query = m.group("url"), (m.group("query") or "")
        fs_path = _resolve_local_asset(url)
        if fs_path and os.path.isfile(fs_path):
            try:
                ver = time.strftime("%Y%m%d%H%M%S", time.localtime(os.path.getmtime(fs_path)))
            except OSError:
                return m.group(0)
            # 移除舊的版本參數（v / _v / version / t），避免疊加
            query = re.sub(r"[?&](?:v|_v|version|t)=[^&]*", "", query)
            sep = "&" if query else "?"
            query += f"{sep}v={ver}"
        return m.group("attr") + url + query + m.group("end")
    return _ASSET_VERSION_RE.sub(repl, html)

@app.after_request
def inject_asset_version(response):
    """方案二：HTML 回應自動注入檔案時間戳版本號（註冊在 add_static_cache 之前，確保先注入後 gzip）"""
    if response.content_type and "text/html" in response.content_type and not request.path.startswith("/api/"):
        try:
            data = response.get_data(as_text=True)
            new_data = _inject_asset_versions(data)
            if new_data != data:
                response.set_data(new_data)
        except Exception as e:
            print(f"[asset-version] 注入失敗: {e}")
    return response



@app.after_request
def add_static_cache(response):
    """為靜態資源加入快取標頭 + gzip 壓縮"""
    path = request.path
    # 跳過 API，避免檔案編輯器讀取被 24 小時快取（導致存檔後切換檔案仍顯示舊內容）
    if path.startswith("/api/"):
        return response
    # 跳過靜態資源，避免 passthrough 錯誤
    if path.startswith("/static/"):
        return response
    if path.endswith((".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff2", ".woff")):
        response.headers["Cache-Control"] = f"public, max-age={STATIC_CACHE_SECONDS}"
        response.headers["Vary"] = "Accept-Encoding"
        if path.endswith((".js", ".css", ".html")) and len(response.data) > 512:
            ae = request.headers.get("Accept-Encoding", "")
            if "gzip" in ae:
                buf = io.BytesIO()
                with gzip.GzipFile(fileobj=buf, mode="wb", compresslevel=6) as f:
                    f.write(response.data)
                c = buf.getvalue()
                if len(c) < len(response.data):
                    response.data = c
                    response.headers["Content-Encoding"] = "gzip"
                    response.headers["Content-Length"] = len(c)
    return response
'''












@socketio.on('upload_media')
def handle_upload_media(data):
    """處理 Web 端上傳的圖片/影片 → 調用 vision 工具分析"""
    sid = request.sid
    file_data = data.get('data', '')
    mime_type = data.get('mime_type', 'image/jpeg')
    
    if not file_data:
        socketio.emit('chat_stream', {'type': 'reply', 'content': '❌ 未收到檔案資料'}, room=sid)
        socketio.emit('chat_stream', {'type': 'done'}, room=sid)
        return
    
    if mime_type.startswith('video/'):
        ext = '.mp4'
        media_type = '影片'
    else:
        ext = '.jpg'
        media_type = '圖片'
    
    ts = int(time.time() * 1000)
    tmp_path = f"/tmp/web_upload_{sid}_{ts}{ext}"
    
    socketio.emit('chat_stream', {'type': 'reply', 'content': f'🔍 正在分析{media_type}...\\n'}, room=sid)
    
    try:
        raw = base64.b64decode(file_data)
        with open(tmp_path, 'wb') as f:
            f.write(raw)
        
        from tools.vision import handle_vision
        import asyncio as _asyncio
        vision_result = _asyncio.run(handle_vision(
            {"file_path": tmp_path},
            chat_id=sid,
            agent_config=_agent_config
        ))
        
        try:
            import json as _json
            result_json = _json.loads(vision_result)
            if result_json.get("success"):
                analysis = result_json["analysis"]
                model_used = result_json.get("model", "vision")
                reply = f"👁️ **視覺分析結果**（{model_used}）:\\n\\n{analysis}"
            else:
                reply = f"❌ 分析失敗: {result_json.get('error', '未知錯誤')}"
        except (_json.JSONDecodeError, TypeError):
            reply = vision_result
        
        try:
            os.remove(tmp_path)
        except:
            pass
        
        socketio.emit('chat_stream', {'type': 'reply', 'content': reply}, room=sid)
        socketio.emit('chat_stream', {'type': 'done'}, room=sid)
        
    except Exception as e:
        try: os.remove(tmp_path)
        except: pass
        socketio.emit('chat_stream', {'type': 'reply', 'content': f'❌ 處理{media_type}時出錯: {str(e)}'}, room=sid)
        socketio.emit('chat_stream', {'type': 'done'}, room=sid)









# ========== CORS 標頭 ==========
@app.before_request
def handle_options_request():
    if request.method == "OPTIONS":
        response = app.make_default_options_response()
        response.headers["Access-Control-Allow-Origin"] = "*"
        response.headers["Access-Control-Allow-Methods"] = "GET, POST, PUT, DELETE, OPTIONS"
        response.headers["Access-Control-Allow-Headers"] = "Content-Type"
        response.headers["Access-Control-Max-Age"] = "86400"
        return response

@app.after_request
def add_cors_headers(response):
    response.headers['Access-Control-Allow-Origin'] = '*'
    response.headers['Access-Control-Allow-Methods'] = 'GET, POST, PUT, DELETE, OPTIONS'
    response.headers['Access-Control-Allow-Headers'] = 'Content-Type'
    return response









# ========== 全域對話搜尋 API ==========
@app.route("/api/search_all_conversations", methods=["GET"])
def search_all_conversations():
    """搜尋全主機內所有 agent 與使用者的對話內容（LIKE 全文檢索）"""
    q = request.args.get("q", "").strip()
    if not q or len(q) < 1:
        return {"error": "Missing or too short query", "results": []}, 400

    limit = request.args.get("limit", default=50, type=int)
    if limit > 200:
        limit = 200

    results = []
    HISTORY_DB = os.path.expanduser(f"~/.{MOKAGI_home}/.memory/conversation_history.db")

    # --- 搜尋 chat_history（Web 端對話） ---
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT id, agent, role, content, timestamp
                   FROM chat_history
                   WHERE content LIKE ?
                   ORDER BY timestamp DESC
                   LIMIT ?""",
                (f"%{q}%", limit)
            ).fetchall()
            for row in rows:
                content = row["content"] or ""
                snippet = content[:200] + ("..." if len(content) > 200 else "")
                results.append({
                    "source": "chat_history",
                    "id": row["id"],
                    "agent": row["agent"],
                    "role": row["role"],
                    "snippet": snippet,
                    "timestamp": row["timestamp"]
                })
    except Exception as e:
        print(f"[search_all] chat_history 搜尋失敗: {e}")

    # --- 搜尋 conversation_history（後端完整對話記錄） ---
    try:
        with closing(sqlite3.connect(HISTORY_DB)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT id, user_key, role, content, timestamp
                   FROM conversation_history
                   WHERE content LIKE ?
                   ORDER BY timestamp DESC
                   LIMIT ?""",
                (f"%{q}%", limit)
            ).fetchall()
            for row in rows:
                content = row["content"] or ""
                snippet = content[:200] + ("..." if len(content) > 200 else "")
                results.append({
                    "source": "conversation_history",
                    "id": row["id"],
                    "agent": row["user_key"],
                    "role": row["role"],
                    "snippet": snippet,
                    "timestamp": row["timestamp"]
                })
    except Exception as e:
        print(f"[search_all] conversation_history 搜尋失敗: {e}")

    # --- 依時間倒序排列，取前 limit ---
    results.sort(key=lambda x: x["timestamp"], reverse=True)
    results = results[:limit]

    return {"query": q, "total": len(results), "results": results}


# ========== 📑 書籤 API ==========
BOOKMARK_FILE = os.path.expanduser(f"~/.{MOKAGI_home}/html/webTools/書籤/書籤.json")

def _load_bookmarks():
    if not os.path.exists(BOOKMARK_FILE):
        return []
    try:
        with open(BOOKMARK_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except:
        return []

def _save_bookmarks(bookmarks):
    try:
        os.makedirs(os.path.dirname(BOOKMARK_FILE), exist_ok=True)
        with open(BOOKMARK_FILE, "w", encoding="utf-8") as f:
            json.dump(bookmarks, f, ensure_ascii=False, indent=2)
        return True
    except Exception as e:
        print(f"[bookmark] 儲存失敗: {e}")
        return False

@app.route("/api/bookmark/add", methods=["POST"])
def bookmark_add():
    """加入書籤"""
    data = request.get_json(silent=True) or {}
    conv_id = str(data.get("conv_id", "")).strip()
    if not conv_id or conv_id == "?":
        return {"success": False, "error": "缺少 conv_id"}, 400

    bookmarks = _load_bookmarks()
    # 檢查是否已存在相同 conv_id
    for bm in bookmarks:
        if str(bm.get("conv_id", "")) == conv_id:
            return {"success": True, "message": "已存在"}

    bookmarks.append({
        "conv_id": conv_id,
        "snippet": data.get("snippet", "")[:200],
        "agent": data.get("agent", ""),
        "role": data.get("role", ""),
        "timestamp": data.get("timestamp", time.time())
    })
    if not _save_bookmarks(bookmarks):
        return {"success": False, "error": "書籤儲存失敗（請檢查檔案權限或磁碟空間）"}, 500
    return {"success": True, "message": "已加入書籤"}

@app.route("/api/bookmark/list", methods=["GET"])
def bookmark_list():
    """列出所有書籤（按時間倒序）"""
    bookmarks = _load_bookmarks()
    bookmarks.sort(key=lambda x: x.get("timestamp", 0), reverse=True)
    return {"success": True, "bookmarks": bookmarks}

@app.route("/api/bookmark/delete", methods=["POST"])
def bookmark_delete():
    """刪除書籤"""
    data = request.get_json(silent=True) or {}
    idx = data.get("index", -1)
    bookmarks = _load_bookmarks()
    if isinstance(idx, int) and 0 <= idx < len(bookmarks):
        bookmarks.pop(idx)
        _save_bookmarks(bookmarks)
        return {"success": True, "message": "已刪除"}
    return {"success": False, "error": "索引無效"}, 400

@app.route("/api/bookmark/conversation", methods=["GET"])
def bookmark_conversation():
    """根據 conv_id 取得完整對話"""
    conv_id = request.args.get("conv_id", "").strip()
    if not conv_id:
        return {"error": "缺少 conv_id"}, 400

    messages = []
    # 搜尋 chat_history
    try:
        with closing(sqlite3.connect(DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT role, content, think_content, timestamp FROM chat_history WHERE CAST(conv_id AS TEXT) = ? ORDER BY timestamp ASC",
                (conv_id,)
            ).fetchall()
            for row in rows:
                messages.append({
                    "role": row["role"],
                    "content": row["content"],
                    "think_content": row["think_content"],
                    "timestamp": row["timestamp"]
                })
    except Exception as e:
        print(f"[bookmark] chat_history 查詢失敗: {e}")

    # 也搜 conversation_history
    try:
        HISTORY_DB = os.path.expanduser(f"~/.{MOKAGI_home}/.memory/conversation_history.db")
        with closing(sqlite3.connect(HISTORY_DB)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT role, content, timestamp FROM conversation_history WHERE CAST(id AS TEXT) = ? ORDER BY timestamp ASC",
                (conv_id,)
            ).fetchall()
            for row in rows:
                messages.append({
                    "role": row["role"],
                    "content": row["content"],
                    "think_content": "",
                    "timestamp": row["timestamp"]
                })
    except Exception as e:
        print(f"[bookmark] conversation_history 查詢失敗: {e}")

    # 去重並按時間排序
    seen = set()
    unique_msgs = []
    for m in messages:
        key = (m["role"], m.get("content",""), m.get("timestamp"))
        if key not in seen:
            seen.add(key)
            unique_msgs.append(m)
    unique_msgs.sort(key=lambda x: x.get("timestamp", 0))

    return {"success": True, "messages": unique_msgs, "conv_id": conv_id}

@app.route("/bookmark.html")
def serve_bookmark_page():
    bookmark_path = os.path.expanduser(f"~/.{MOKAGI_home}/html/webTools/書籤/書籤.html")
    if os.path.exists(bookmark_path):
        return send_file(bookmark_path)
    return "<h1>書籤頁面不存在</h1>", 404


# ---------- 啟動 ----------
if __name__ == '__main__':
    # 同步初始配置到 mokagi
    reload_config(CURRENT_ENV_PATH)  # 確保 mokagi 配置與網頁一致
    threading.Thread(target=start_observer, daemon=True).start()
    socketio.run(app, host='127.0.0.1', port=5000, debug=False, allow_unsafe_werkzeug=True)
