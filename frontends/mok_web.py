"""
mok_web.py
網頁前端適配器（基於 mokagi）
提供文件瀏覽器、系統監控、聊天界面，所有 AI 對話能力調用 mokagi 模塊。
202608260224_我覺得可以版
"""

import os, sys, fnmatch, mimetypes; mimetypes.add_type('image/webp', '.webp')
import secrets
import re
import json
import asyncio
import threading
import time
import subprocess
import faulthandler
import copy
faulthandler.enable()  # 段錯誤時輸出 Python 棧到 stderr，便於診斷 SIGSEGV
from flask import Flask, render_template, request, send_from_directory, send_file, Response, jsonify, stream_with_context
from flask_socketio import SocketIO, join_room
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler
import sqlite3
from contextlib import closing




os.environ.setdefault("MOKAGI_HOME", "mok")
# 2026-10-08 indexPage（修法B）：本進程＝網頁前端，宣告平台旗標，供工具層（tts…）分流。
os.environ["MOK_PLATFORM"] = "web"


















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
from global_gate import gated as gate_call, gate_held, gate_stats
from config import _agent_config_cache, _agent_config

# 導入工具管理（用於獲取工具列表等）
import tool_handler
import base64
import tempfile

# 啟動時加載工具
tool_handler.load_tools()

# 定義模板目錄
# 兼容：優先使用專案本地 html/，其次使用 ~/.mok/html
_project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), os.pardir))
_project_html_dir = os.path.join(_project_root, 'html')
_default_html_dir = os.path.expanduser(f"~/.{MOKAGI_home}/html")
if os.path.isdir(_project_html_dir):
    BASE_DIR = _project_html_dir
elif os.path.isdir(_default_html_dir):
    BASE_DIR = _default_html_dir
else:
    BASE_DIR = _project_html_dir if os.path.isdir(_project_root) else _default_html_dir

template_dir = BASE_DIR
static_dir = os.path.join(BASE_DIR, "static")

app = Flask(__name__, template_folder=template_dir, static_folder=static_dir, static_url_path='/static')
def _load_web_secret_key():
    """Flask session 密鑰（2026-09-21 安全修補）。
    優先讀環境變數 MOK_WEB_SECRET_KEY；否則使用持久化隨機密鑰（首次自動生成、0600）。
    禁止硬編碼弱密鑰——否則可偽造 session cookie 冒充 admin，直接繞過 tenant 隔離。"""
    _env = os.environ.get('MOK_WEB_SECRET_KEY')
    if _env and len(_env) >= 32:
        return _env
    _path = os.path.expanduser('~/.mok/gateway/.web_secret_key')
    _legacy_path = os.path.expanduser('~/.mok/.web_secret_key')
    if not os.path.exists(_path) and os.path.exists(_legacy_path):
        # 舊路徑自動遷移（2026-09-26 搬至 gateway/）
        try:
            os.makedirs(os.path.dirname(_path), exist_ok=True)
            os.rename(_legacy_path, _path)
            os.chmod(_path, 0o600)
        except Exception:
            _path = _legacy_path
    try:
        if os.path.exists(_path):
            _k = open(_path, 'r', encoding='utf-8').read().strip()
            if len(_k) >= 32:
                return _k
        _k = secrets.token_hex(32)
        _fd = os.open(_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(_fd, 'w', encoding='utf-8') as _f:
            _f.write(_k)
        return _k
    except Exception as _e:
        print('SECRET_KEY 無法持久化，暫用隨機密鑰：', _e)
        return secrets.token_hex(32)


app.config['SECRET_KEY'] = _load_web_secret_key()
ASSET_BUSTER = str(int(time.time()))
WEB_BUILD_ID = 'mok-web-sse-fix-20260822-02'

# ===== 方案二：檔案時間戳自動版本 ⭐️ =====
# 用檔案修改時間當版本號，改一次檔案、版本就自動變，不用再手動改 ?v=
app.config['SEND_FILE_MAX_AGE_DEFAULT'] = 0  # 靜態檔不強緩存，靠 ETag/Last-Modified 每次驗證
app.config['TEMPLATES_AUTO_RELOAD'] = True  # 修改 HTML 模板立即生效，不需重啟

# ===== 暫停補丁（pause_patch）：仿 ChatGPT 暫停/繼續，不改 mokagi.py =====
try:
    import sys as _pp_sys, os as _pp_os
    _pp_dir = _pp_os.path.join(_pp_os.path.dirname(_pp_os.path.dirname(_pp_os.path.abspath(__file__))), 'core', '暫停補丁')
    if _pp_os.path.isdir(_pp_dir) and _pp_dir not in _pp_sys.path:
        _pp_sys.path.insert(0, _pp_dir)
    import pause_patch
    pause_patch.register_routes(app)
    print('[pause_patch] 已載入：/api/chat/pause（暫停/繼續生成）')
except Exception as _pp_e:
    print('[pause_patch] load failed:', _pp_e)

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
    if not (resp.headers.get('Cache-Control') or '').startswith('public') and ct.startswith(('text/javascript', 'application/javascript', 'text/css', 'text/html')):
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

# ===== 插話補丁（interject_patch）：工作中輸入框仍可用，訊息併入當前工作輪（9/13 的「補充輸入」） =====
try:
    import sys as _ij_sys
    import os as _ij_os
    _ij_dir = _ij_os.path.join(_ij_os.path.dirname(_ij_os.path.dirname(_ij_os.path.abspath(__file__))), 'core', '插話補丁')
    if _ij_os.path.isdir(_ij_dir) and _ij_dir not in _ij_sys.path:
        _ij_sys.path.insert(0, _ij_dir)
    import interject_patch
    interject_patch.install_call_llm_hook()
    interject_patch.register_routes(app, is_running_fn=lambda a: (a in _running_agents) or _agent_has_live_session(a),
                                    resolve_tenant_fn=lambda d: resolve_tenant(d))
    print("[interject_patch] 已載入：工作中可補充輸入 /api/chat/interject")
except Exception as _ij_e:
    print(f"[interject_patch] 載入失敗（不影響主服務）: {_ij_e}")

# ===== SSE 串流隊列（HTTP 串流備援，當 Socket.IO 不可用時） =====
import uuid as _uuid
import queue as _queue
_sse_queues = {}  # {session_id: queue.Queue}
_sse_lock = threading.Lock()
_sse_cleanup_timers = {}  # {session_id: threading.Timer}
_sse_agents = {}   # {session_id: agent_name}  用於查詢進行中的 session（頁面刷新後續流）
_sse_buffers = {}  # {session_id: [event,...]} 事件緩衝（權威來源，供刷新後重放思考/工具/回答）
_sse_done = {}     # {session_id: bool}         該 session 是否已結束（done/error）
_sse_disconnected = {}  # {session_id: bool}    客戶端是否已斷線（供中止無用生成用）


class _SSEClientGone(BaseException):
    """客戶端 SSE 連線已斷（事件緩衝已被清理）。
    繼承 BaseException，避免被 except Exception / autofix 重試機制攔截。"""
_sse_agg = {}      # {session_id: {rounds,think,reply,n}} 聚合快照（供刷新後一鍵重建 + 只續流尾巴）
_sse_agg_last = {} # {session_id: float} 上次聚合快照時間（節流 0.5s）
_sse_users = {}    # {session_id: user_id}  同一 (agent,user) 開新一輪時用來收掉舊輪


def _agent_has_live_session(agent_name):
    """該 agent 是否仍有「未結束」的 SSE session（多用戶/多輪並行下比 _running_agents 更準）。"""
    if not agent_name:
        return False
    try:
        with _sse_lock:
            for _sid, _ag in _sse_agents.items():
                if _ag == agent_name and not _sse_done.get(_sid, False):
                    return True
    except Exception:
        pass
    return False

def _agent_live_tenants(agent_name):
    """20260929（凜）：回傳目前正在跑該 agent 的 tenant 集合（供側欄 own/others 燈號）。"""
    _ts = set()
    if not agent_name:
        return _ts
    try:
        with _sse_lock:
            for _sid, _ag in _sse_agents.items():
                if _ag == agent_name and not _sse_done.get(_sid, False):
                    _ts.add(_sse_users.get(_sid) or "")
    except Exception:
        pass
    return _ts


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
            _sse_agents.pop(session_id, None)
            _sse_buffers.pop(session_id, None)
            _sse_done.pop(session_id, None)
            _sse_disconnected.pop(session_id, None)
            _sse_agg.pop(session_id, None)
            _sse_agg_last.pop(session_id, None)
            _sse_users.pop(session_id, None)
            _sse_cleanup_timers.pop(session_id, None)
        print(f"[SSE cleanup] session={session_id} removed")

    timer = threading.Timer(delay_sec, _cleanup)
    timer.daemon = True
    with _sse_lock:
        _sse_cleanup_timers[session_id] = timer
    timer.start()


def _trace_reply_event(event, session_id):
    """(b) 追蹤 log 2026-09-24：記錄每個 reply 事件的 len/hash，用於定位偶發回覆重複。
    開關：存在 ~/.mok/logs/.reply_trace_off 即停用；超過 5MB 自動輪替。"""
    try:
        if not event or event.get("type") != "reply":
            return
        import os as _os, hashlib as _hl
        if _os.path.exists("/home/ubuntu/.mok/logs/.reply_trace_off"):
            return
        _p = "/home/ubuntu/.mok/logs/reply_trace.jsonl"
        if _os.path.exists(_p) and _os.path.getsize(_p) > 5242880:
            _os.replace(_p, _p + ".1")
        _tc = event.get("content", "") or ""
        with open(_p, "a", encoding="utf-8") as _tf:
            _tf.write(json.dumps({"t": round(time.time(), 3), "ag": event.get("agent"), "s": session_id,
                                  "n": len(_tc), "h": _hl.sha1(_tc.encode("utf-8", "ignore")).hexdigest()[:12]},
                                 ensure_ascii=False) + "\n")
    except Exception:
        pass


def _maybe_schedule_cleanup_after_disconnect(session_id):
    """前端斷線時呼叫。關鍵：若 agent 仍在跑（尚未 done），就不排清理，
    保留 _sse_buffers / _sse_agents，讓頁面刷新後 /api/chat/active 仍能找到 session，
    EventSource 可重放思考/工具/回答並繼續續流。只有確定跑完才延遲清理。"""
    with _sse_lock:
        _finished = _sse_done.get(session_id, False)
    if _finished:
        _schedule_sse_cleanup(session_id, delay_sec=120)
    # 若仍在進行中：不排清理；交由 _sse_bg_worker 結束(finally done)時統一排清理

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
            _tree_cache['data'] = None  # 檔案有變動 → 清除文件樹快取
            # 2026-09-30：前端沒有任何 socket.on('file_change') 監聽 → 這條廣播純浪費，關掉。
            # self.socketio.emit('file_change', {'path': rel_path})

# 文件樹 TTL 快取：避免每次請求都重新掃描整個 home（曾造成 2MB JSON / 1.5s+ 延遲）
_tree_cache = {'data': None, 'ts': 0.0}
TREE_CACHE_TTL = 15  # 秒；file_change 事件會即時清除

def get_file_tree_cached():
    import time
    now = time.time()
    if _tree_cache['data'] is not None and (now - _tree_cache['ts']) < TREE_CACHE_TTL:
        return _tree_cache['data']
    # 2026-09-30：一律惰性 —— 根層只回一層，不再遞迴掃到 depth 5（前端展開時逐層要）
    tree = get_file_tree(WATCH_PATH, 0, one_level=True)
    _tree_cache['data'] = tree
    _tree_cache['ts'] = now
    return tree

SKIP_DIRS = {
    'node_modules', '.git', '__pycache__', '.venv', 'venv', 'env',
    'dist', 'build', '.cache', '.pytest_cache', '.mypy_cache',
    'backups', 'trash', '.trash', 'playwright-browsers', 'whisper_models',
    'mpt', 'browser_profile', 'browser_profile2', '.ollama', 'snap', 'go',
    # 內部/暫存目錄：不顯示在文件樹，減少掃描量
    '.chroma_data', '.memory', '__MACOSX',
}
MAX_TREE_DEPTH = 5
MAX_TREE_ITEMS = 500

def get_file_tree(path, depth=0, one_level=False):
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
        if is_dir and not one_level:
            node['children'] = get_file_tree(full_path, depth + 1)
        tree.append(node)
    return tree

# ---------- 數據庫（聊天曆史，僅用於前端展示）----------
DB_PATH = os.path.expanduser(f"~/.{MOKAGI_home}/.memory/chat_history.db")

# ===== 多租戶隔離（tenant）20260921：身分一律以「登入 session」為準 =====
# 未登入者只接受訪客 id（web_guest_* / guest:*）；嚴禁回落成 ADMIN_CHAT_ID / web_default，
# 否則任何訪客都會被當成 admin（跨會員資料外洩）。
_PRIVILEGED_TENANTS = frozenset({'admin', 'root'})  # 2026-09-21 收斂：移除死條目 web_default / guest


def _session_member_user():
    '''目前請求已登入的會員帳號；未登入回 None。'''
    try:
        from flask import session as _fs
        _u = _fs.get('member_user')
        if _u:
            return str(_u)
    except Exception:
        pass
    return None


def _is_guest_id(_c):
    return bool(_c) and (_c.startswith('web_guest_') or _c.startswith('guest:'))


def resolve_tenant(data=None):
    '''解析本次請求的租戶（tenant）。

    1) 已登入      -> 一律採用 session 的會員帳號（前端無法偽造）
    2) 未登入      -> 只接受訪客 id（web_guest_* / guest:*）
    取不到合法身分 -> 回 None（呼叫端必須回 401 / 擋掉，絕不可回落成 admin）
    '''
    _u = _session_member_user()
    if _u:
        return _u
    if isinstance(data, dict):
        _c = data.get('user_id')
        if _c:
            _c = str(_c).strip()
            if _is_guest_id(_c):
                return _c
    return None


# ---------- 產物落點（權威來源：core/output_router.py） ----------
# 2026-09-29 凜：把「依身分決定輸出目錄」正式接進前端兩個呼叫點（不再只靠補丁）。
# 註：mok_web/產物三層落點_* 補丁保留為備援（2026-10-03 方案A改名），其包裝層看到已有 output_dir 就不再覆蓋。
def resolve_output_ctx(user_id, agent_name=None, job=None):
    """回傳 (output_dir, anon_sid)：依身分決定本回合產物落點。

    ⚠ 必須在「請求執行緒」呼叫（背景執行緒讀不到 flask.g / cookie）。
    任何失敗都回 (None, None)，交給 core 自行推導，絕不影響主流程。
    """
    sid = None
    try:
        from flask import g as _g
        sid = (getattr(_g, "_anon_sid", None)
               or getattr(_g, "_anon_new_sid", None) or None)
    except Exception:
        sid = None
    if not sid:
        try:
            from flask import request as _rq
            _cv = _rq.cookies.get("mok_anon") or ""
            sid = (_cv.split("|", 1)[0] or None)
        except Exception:
            sid = None
    try:
        import sys as _osys
        _core = os.path.join(os.path.expanduser(f"~.{MOKAGI_home}"), "core")
        if _core not in _osys.path:
            _osys.path.insert(0, _core)
        from output_router import output_dir_for_request, ensure_dir
        _path, _role = output_dir_for_request(user_id, agent_name, sid=sid, job=job)
        ensure_dir(_path)
        return _path, sid
    except Exception:
        return None, None


def resolve_tenant_arg():
    '''GET / DELETE 等無 body 的請求：先看 session，再看 ?user_id=（只接受訪客 id）。'''
    _u = _session_member_user()
    if _u:
        return _u
    try:
        _q = (request.args.get('user_id') or '').strip()
    except Exception:
        _q = ''
    return _q if _is_guest_id(_q) else None


def _can_read_all(tenant):
    '''只有 admin / root 可讀全部；一般租戶只看得到自己 tenant 的資料；未識別（None）不得讀全部。'''
    if tenant is None:
        return False
    return str(tenant) in _PRIVILEGED_TENANTS


def init_db():
    with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
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
        # ===== 多租戶隔離（tenant）20260921：舊資料一律歸 'admin' =====
        if 'tenant' not in columns:
            conn.execute('ALTER TABLE chat_history ADD COLUMN tenant TEXT')
            print('✅ chat_history 表已新增 tenant 欄位（舊資料 -> admin）')
            conn.execute("UPDATE chat_history SET tenant = 'admin' WHERE tenant IS NULL OR tenant = ''")
        conn.execute('CREATE INDEX IF NOT EXISTS idx_chat_tenant ON chat_history (tenant, agent, id)')
        # conversation_history 同步（由 core/mokagi.py 寫入；舊資料同樣歸 'admin'）
        try:
            _conv_db = os.path.expanduser(f'~/.{MOKAGI_home}/.memory/conversation_history.db')
            with closing(sqlite3.connect(_conv_db, timeout=30)) as _c2:
                _c3 = [r[1] for r in _c2.execute('PRAGMA table_info(conversation_history)').fetchall()]
                if _c3 and 'tenant' not in _c3:
                    _c2.execute('ALTER TABLE conversation_history ADD COLUMN tenant TEXT')
                    _c2.execute("UPDATE conversation_history SET tenant = 'admin' WHERE tenant IS NULL OR tenant = ''")
                    print('✅ conversation_history 表已新增 tenant 欄位（舊資料 -> admin）')
                _c2.execute('CREATE INDEX IF NOT EXISTS idx_conv_tenant ON conversation_history (tenant, user_key)')
                _c2.commit()
        except Exception as _e:
            print(f'[tenant] conversation_history 遷移略過: {_e}')
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

# ---------- 重工離線化（2026-10-04 稚）：chat_history 寫入改由單一背景寫入器批次落盤 ----------
# 問題：stream_emit 每個事件都同步 UPDATE 同一個 chat_history.db（全體侍女共用一把 DB 寫鎖），
#       而且是在事件迴圈裡做 → 多侍女並行時互相排隊、拖慢回合。
# 作法：事件只把「最新內容」登記進記憶體；背景執行緒每 0.5s 批次寫一次（同 msg_id 去重）；
#       回合結束(done)強制立即落盤。寫入器若異常 → 自動退回同步直寫（fail-safe，不會丟資料）。
_db_pending = {}
_db_dirty = set()
_db_lock = threading.Lock()
_db_wake = threading.Event()


def _db_write_one_sync(msg_id, content, think_content):
    try:
        with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
            conn.execute("UPDATE chat_history SET content = ?, think_content = ? WHERE id = ?",
                         (content, think_content, msg_id))
            conn.commit()
        return True
    except Exception as _e:
        print(f"[db_writer] sync write failed: {_e}")
        return False


def _db_writer_loop():
    while True:
        try:
            _db_wake.wait(timeout=0.5)
            _db_wake.clear()
            with _db_lock:
                ids = list(_db_dirty)
                _db_dirty.clear()
                items = [(i, _db_pending.pop(i, None)) for i in ids]
            items = [(i, v) for i, v in items if v is not None]
            if not items:
                continue
            try:
                with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
                    for i, (c, t) in items:
                        conn.execute("UPDATE chat_history SET content = ?, think_content = ? WHERE id = ?", (c, t, i))
                    conn.commit()
            except Exception as _e:
                print(f"[db_writer] batch flush failed, fallback sync: {_e}")
                for i, (c, t) in items:
                    _db_write_one_sync(i, c, t)
        except Exception as _e:
            print(f"[db_writer] loop error: {_e}")


threading.Thread(target=_db_writer_loop, name="mok-db-writer", daemon=True).start()


def enqueue_chat_update(msg_id, content, think_content):
    """登記最新內容，交由背景寫入器批次落盤（不卡事件迴圈、不與其他侍女搶 DB 寫鎖）。"""
    if not msg_id:
        return
    with _db_lock:
        _db_pending[msg_id] = (content, think_content)
        _db_dirty.add(msg_id)
    _db_wake.set()


def flush_chat_update(msg_id):
    """立即把某 msg_id 的最新內容寫入（回合結束／需要立即可讀時）。"""
    with _db_lock:
        v = _db_pending.pop(msg_id, None)
        _db_dirty.discard(msg_id)
    if v is not None:
        _db_write_one_sync(msg_id, v[0], v[1])


def _save_rounds_to_db(msg_id, rounds):
    """把輪次結構（思考/工具/回覆）以 JSON 持久化到 chat_history.rounds 欄位"""
    try:
        with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
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
    # P0 止血：不再回落成 admin / web_default；未登入且非訪客 -> 401
    user_id = resolve_tenant(data)
    if not user_id:
        raise PermissionError('unauthorized: 請先登入會員（/login）才能使用 AI 服務。')
    # 產物落點（第 1/2/3 層）：在請求執行緒先算好，再帶進背景執行緒
    _out_dir, _anon_sid = resolve_output_ctx(user_id, agent_name)
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
        _sse_agents[session_id] = agent_name
        _sse_buffers[session_id] = []
        _sse_done[session_id] = False
        _sse_disconnected[session_id] = False
        _sse_agg[session_id] = {"rounds": [], "think": "", "reply": "", "n": 0}
        _sse_agg_last[session_id] = 0.0
        _sse_users[session_id] = user_id
    # 🔧 同一 (agent,user) 開新一輪前，先收掉上一條「未完成」的舊 session：
    #    否則舊輪殘留會令前端重播/重連（看起來像「不停刷新」），且兩輪並寫同一對話會報錯。
    #    注意：只收「同一使用者的同一 agent」，不同使用者對同一 agent 仍可真正並行。
    try:
        _sup_ev = {"type": "done", "agent": agent_name, "superseded": True}
        with _sse_lock:
            _old_sids = [s for s, a in _sse_agents.items()
                         if s != session_id and a == agent_name
                         and _sse_users.get(s) == user_id
                         and not _sse_done.get(s, False)]
            for _os_ in _old_sids:
                _sse_done[_os_] = True
                _ob = _sse_buffers.get(_os_)
                if _ob is not None:
                    _ob.append(dict(_sup_ev))
                _oq = _sse_queues.get(_os_)
                if _oq is not None:
                    try:
                        _oq.put(dict(_sup_ev))
                    except Exception:
                        pass
        for _os_ in _old_sids:
            print(f"[SSE] supersede old session={_os_} agent={agent_name} user={user_id}")
    except Exception as _sup_e:
        print(f"[SSE] supersede failed: {_sup_e}")
    print(f"[SSE start] session={session_id} agent={agent_name} msg={user_msg[:50]}...")

    def _sse_bg_worker():
        accumulated_think = ""
        accumulated_reply = ""
        assistant_msg_id = None
        user_msg_id = None
        agg_rounds = []   # 聚合輪次（鏡像 mokagi accumulated_rounds），供刷新後一鍵重建

        # ===== 無縫接回（2026-10-03 稚）：把本輪「續寫座標」交給補丁持久化 =====
        # 補丁未載入時 globals().get 取不到 -> 機制自動失效，零副作用。
        def _resume_note(_ev=None):
            try:
                _h = globals().get("mok_resume_hook")
                if _h:
                    _h(_ev if isinstance(_ev, dict) else {},
                       agent=agent_name, session_id=session_id, user_id=user_id,
                       user_msg=user_msg, user_msg_id=user_msg_id,
                       assistant_msg_id=assistant_msg_id,
                       accumulated_reply=accumulated_reply,
                       accumulated_think=accumulated_think)
            except Exception:
                pass


        def update_assistant_in_db(msg_id, content, think_content):
            # 重工離線化（2026-10-04 稚）：改登記進背景寫入器批次落盤，不在事件迴圈裡同步寫 DB
            enqueue_chat_update(msg_id, content, think_content)

        def _feed_agg(event):
            try:
                _et = event.get("type")
                if _et == "iteration_start":
                    _it = event.get("iteration", len(agg_rounds) + 1)
                    _prev = agg_rounds[-1] if agg_rounds else None
                    _prev_empty = bool(_prev) and not (_prev.get("think") or _prev.get("reply") or _prev.get("tool_calls") or _prev.get("tool_results"))
                    if _prev_empty:
                        # 方案C：前置的語義搜索/經驗參考已落在這一輪，沿用不另開新輪
                        _prev["iteration"] = _it
                    else:
                        agg_rounds.append({"think": "", "tool_calls": [], "tool_results": [], "reply": "", "iteration": _it})
                elif _et == "think":
                    if not agg_rounds:
                        agg_rounds.append({"think": "", "tool_calls": [], "tool_results": [], "reply": "", "iteration": 1})
                    agg_rounds[-1]["think"] += event.get("content", "")
                elif _et == "tool_calls":
                    if not agg_rounds:
                        agg_rounds.append({"think": "", "tool_calls": [], "tool_results": [], "reply": "", "iteration": 1})
                    agg_rounds[-1]["tool_calls"] = event.get("calls", [])
                elif _et == "tool_result":
                    if not agg_rounds:
                        agg_rounds.append({"think": "", "tool_calls": [], "tool_results": [], "reply": "", "iteration": 1})
                    agg_rounds[-1]["tool_results"].append({"name": event.get("tool_name", "未知工具"), "content": event.get("content", "")})
                elif _et == "reply":
                    _sub = event.get("subtype", "normal")
                    if _sub in ("tool_process", "semantic_search", "experience"):
                        if not agg_rounds:
                            agg_rounds.append({"think": "", "tool_calls": [], "tool_results": [], "reply": "", "iteration": 1})
                        _ak = {"tool_process": "tool_process", "semantic_search": "semantic", "experience": "experience"}[_sub]
                        agg_rounds[-1][_ak] = agg_rounds[-1].get(_ak, "") + event.get("content", "") + "\n\n"
                    elif _sub != "pending_list":
                        if not agg_rounds:
                            agg_rounds.append({"think": "", "tool_calls": [], "tool_results": [], "reply": "", "iteration": 1})
                        # 2026-09-19：工具執行結果（subtype=tool_result）改歸入工具結果，不黏進回覆文字
                        if _sub == "tool_result":
                            agg_rounds[-1]["tool_results"].append({"name": event.get("tool_name", "工具"), "content": event.get("content", "")})
                        else:
                            agg_rounds[-1]["reply"] += event.get("content", "")
                # 節流快照：每 >=0.5s 或關鍵事件即時更新，供刷新後「一鍵重建 + 只續流尾巴」
                _now = time.time()
                _force = _et in ("iteration_start", "tool_calls", "tool_result", "done")
                if _force or (_now - _sse_agg_last.get(session_id, 0.0)) >= 0.5:
                    _sse_agg_last[session_id] = _now
                    _sse_agg[session_id] = {
                        "rounds": copy.deepcopy(agg_rounds),
                        "think": accumulated_think,
                        "reply": accumulated_reply,
                        "n": len(_sse_buffers.get(session_id, [])),
                    }
            except Exception as _ae:
                print(f"[SSE agg] feed failed: {_ae}")

        def stream_emit(event):
            _emit_event(event)
            _feed_agg(event)
            _resume_note(event)

        def _emit_event(event):
            nonlocal accumulated_think, accumulated_reply, assistant_msg_id
            event["agent"] = agent_name
            # 🔧 事件緩衝：同步寫入 session 緩衝（供頁面刷新後重放思考/工具/回答）
            try:
                _buf_gone = False
                with _sse_lock:
                    _buf = _sse_buffers.get(session_id)
                    if _buf is None:
                        _buf_gone = True
                    else:
                        _buf.append(event.copy())
                        _trace_reply_event(event, session_id)
                if _buf_gone:
                    # (A) 止血：客戶端已斷線（緩衝已被延遲清理）→ 靜默丟棄，不再刷成千上萬行錯誤
                    _sse_disconnected[session_id] = True
            except Exception as _be:
                print(f"[SSE stream_emit] buffer append failed: {_be}")
            try:
                q.put(event)
            except Exception as _e:
                print(f"[SSE stream_emit] q.put failed: {_e}")
            if event["type"] == "think":
                accumulated_think += event["content"]
                if assistant_msg_id is None:
                    with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
                        cursor = conn.execute("INSERT INTO chat_history (agent, role, content, think_content, timestamp, tenant) VALUES (?, ?, ?, ?, ?, ?)", (agent_name, "assistant", "", "", time.time(), user_id))
                        assistant_msg_id = cursor.lastrowid
                        conn.commit()
                update_assistant_in_db(assistant_msg_id, accumulated_reply, accumulated_think)
            elif event["type"] == "reply":
                if event.get("subtype", "normal") in ("pending_list", "tool_process", "semantic_search", "experience", "tool_result"):
                    return
                accumulated_reply += event["content"]
                if assistant_msg_id is None:
                    with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
                        cursor = conn.execute("INSERT INTO chat_history (agent, role, content, think_content, timestamp, tenant) VALUES (?, ?, ?, ?, ?, ?)", (agent_name, "assistant", "", "", time.time(), user_id))
                        assistant_msg_id = cursor.lastrowid
                        conn.commit()
                update_assistant_in_db(assistant_msg_id, accumulated_reply, accumulated_think)
            elif event["type"] == "done":
                _running_agents.discard(agent_name)
                if event.get("final_reply"):
                    accumulated_reply = event["final_reply"]
                if assistant_msg_id is None:
                    with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
                        cursor = conn.execute("INSERT INTO chat_history (agent, role, content, think_content, timestamp, tenant) VALUES (?, ?, ?, ?, ?, ?)", (agent_name, "assistant", accumulated_reply, accumulated_think, time.time(), user_id))
                        assistant_msg_id = cursor.lastrowid
                        conn.commit()
                update_assistant_in_db(assistant_msg_id, accumulated_reply, accumulated_think)
                flush_chat_update(assistant_msg_id)   # 回合結束：強制立即落盤
                if event.get("rounds"):
                    _save_rounds_to_db(assistant_msg_id, event["rounds"])
                conv_id = event.get("conv_id")
                if conv_id:
                    with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
                        conn.execute("UPDATE chat_history SET conv_id = ? WHERE id = ?", (conv_id, assistant_msg_id))
                        conn.commit()
                if conv_id:
                    _update_user_message_conv_id(agent_name, conv_id, user_msg_id, user_id)

        try:
            with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
                cursor = conn.execute('INSERT INTO chat_history (agent, role, content, timestamp, tenant) VALUES (?, ?, ?, ?, ?)', (agent_name, 'user', user_msg, time.time(), user_id))
                user_msg_id = cursor.lastrowid
                conn.commit()
        except Exception as _e:
            print(f"[SSE] user msg insert failed: {_e}")

        _resume_note({})   # 無縫接回：本輪起點先落一次盤（此刻尚無正文）

        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            async def _bg_coro():
                async def async_stream_cb(event):
                    # ===== 暫停補丁（pause_patch）：暫停時阻塞此回調，上游因背壓停住（不重送、不重複計費）=====
                    try:
                        await pause_patch.wait_if_paused(agent_name)
                    except Exception:
                        pass
                    # (B) 治本：偵測到客戶端已斷線 → 丟出中止例外，讓生成迴圈提前結束（連帶取消上游請求）
                    if _sse_disconnected.get(session_id):
                        raise _SSEClientGone()
                    pass  # [log精簡] 原每 SSE chunk 印 log，曾使日誌漲到 856MB，已移除
                    # ✨ girl gate：偵測一般模型安全拒答 → GIRL_AUTO=1 直接開 girl，否則送確認按鈕
                    try:
                        if event.get("type") == "done":
                            import girl_gate
                            _fr = (accumulated_reply or event.get("final_reply") or event.get("content") or "")
                            _g = girl_gate.check(user_msg, _fr, agent_name)
                            if _g and _g.get("needs_girl"):
                                girl_gate.save_pending(str(user_id), user_text=user_msg)
                                if _g.get("auto"):
                                    try:
                                        import girl_engine as _ge_auto
                                        _ge_auto.spawn_auto_start(str(user_id), agent_name)
                                        _auto_txt = "🚦 偵測到一般模型安全拒答（色情過濾）→ 已直接幫主人打開 vast girl 模型（qwen-Claude 27B），上線後會自動切換。"
                                    except Exception as _ae:
                                        _auto_txt = f"🚦 偵測到安全拒答，但自動開 girl 失敗：{_ae}"
                                    event["girl_confirm"] = {
                                        "text": _auto_txt,
                                        "actions": [{"id": "girl_go", "label": "🖥️ 查看 vast girl 狀態"}],
                                    }
                                else:
                                    event["girl_confirm"] = {
                                        "text": "🧠 一般模型出現安全拒答。要切到「vast girl」引擎（qwen-Claude 27B）重新回答嗎？",
                                        "actions": [{"id": "girl_go", "label": "🖥️ 開 vast girl（qwen-Claude）"},
                                                    {"id": "girl_no", "label": "不用"}],
                                    }
                    except Exception:
                        pass
                    stream_emit(event)
                agent_config = await mokagi.get_agent_config(agent_name)
                from autofix2 import autofix_run
                from mokagi import find_tool_handler
                async def run(agent_config, context_files=None):
                    await gate_call(mokagi.process_message, user_id=user_id, text=user_msg, stream_callback=async_stream_cb, agent_name=agent_name, agent_config=agent_config, context_files=context_files, output_dir=_out_dir, anon_sid=_anon_sid, platform="web")
                result = await autofix_run(func=run, func_args=(agent_config, context_files), func_kwargs={}, max_attempts=3, autofix_handler=find_tool_handler("admin"), autofix_max_retries=2, original_text=user_msg, stream_callback=async_stream_cb)
                if result == "__ERROR_REPORTED__":
                    _running_agents.discard(agent_name)
                    stream_emit({"type": "reply", "content": "failed"})
                    stream_emit({"type": "done"})
            loop.run_until_complete(_bg_coro())
        except _SSEClientGone:
            print(f"[SSE /api/chat] session={session_id} 客戶端已斷線，中止生成（連帶取消上游請求）")
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
            with _sse_lock:
                _sse_done[session_id] = True
            # 🔧 agent 已結束：排定延遲清理，給最後一次重放 / DB 落盤留時間
            _schedule_sse_cleanup(session_id, delay_sec=120)
            # ===== 補充輸入（插話補丁）：本輪結束仍有未消費的插話 → 保留待下一輪併入 =====
            try:
                _ij_left = interject_patch.peek_count(agent_name, user_id)
                if _ij_left:
                    print(f"[interject_patch] leftover {_ij_left} kept for {agent_name} (no new conversation)")
            except Exception as _ij_le:
                print("[interject_patch] leftover check failed:", _ij_le)

    threading.Thread(target=_sse_bg_worker, daemon=True).start()
    return {"session_id": session_id, "agent_name": agent_name, "queue": q}


def _latest_conv_id(agent, tenant=None):
    '''取該侍女最近一筆對話的 conv_id。20260929（凜）：一律比對 tenant，
    連 admin 亦不再跨租戶，避免「當前對話」指針跳去別人那條而混流。'''
    try:
        with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
            row = conn.execute(
                'SELECT conv_id FROM chat_history WHERE agent = ? AND conv_id IS NOT NULL '
                'AND (tenant = ? OR tenant IS NULL) ORDER BY id DESC LIMIT 1',
                (agent, tenant)).fetchone()
        return row[0] if row else None
    except Exception:
        return None


def _save_girl_maid_reply(agent, content, conv_id=None, tenant=None):
    '''把侍女對「開 vast girl」的對話回答落盤到 chat_history（含 tenant 歸戶）。

    因為是存進對話紀錄（而非只是彈出提示），所以重新整理、換侍女、
    重開頁面後都還看得到開機狀態。回傳新訊息 id（失敗回 None）。
    '''
    if not content:
        return None
    if tenant is None:
        try:
            tenant = resolve_tenant(request.get_json(force=True, silent=True) or {})
        except Exception:
            tenant = None
    if conv_id is None:
        conv_id = _latest_conv_id(agent, tenant)
    try:
        with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
            cur = conn.execute(
                'INSERT INTO chat_history (agent, role, content, think_content, conv_id, timestamp, tenant) '
                'VALUES (?, ?, ?, ?, ?, ?, ?)',
                (agent, 'assistant', content, None, conv_id, time.time(), tenant))
            conn.commit()
            return cur.lastrowid
    except Exception as e:
        print(f'[girl] 落盤侍女回答失敗: {e}')
        return None


def _girl_maid_reply_text(ok, engine_msg, agent):
    '''把 vast girl 引擎狀態包成侍女口吻的『對話回答』。'''
    if ok:
        head = f'🖥️ 好的主人～{agent}已經把 vast girl 引擎開好了，也替您切換過去。'
    else:
        head = f'🖥️ 收到主人～{agent}正在幫您開機 vast girl 引擎（qwen-Claude 27B）。'
    out = [head, '', f'【開機狀態】{engine_msg}']
    if not ok:
        out += ['', '☝️ 這則開機狀態已寫進對話裡，重新整理或換侍女後都還看得到～']
    return '\n'.join(out)


@app.route('/api/girl/confirm', methods=['POST'])
def api_girl_confirm():
    '''C 里程碑：Web 端「開 vast girl / 不用」按鈕回調'''
    d = request.get_json(force=True, silent=True) or {}
    action = d.get('action') or 'girl_no'
    agent = d.get('agent') or '稚'
    # ✨ 盡量把侍女這則「開機狀態」歸到主人當前那串對話（前端會帶 conv_id）
    try:
        _cv = d.get('conv_id')
        conv_id = int(_cv) if _cv not in (None, '', 'null', 'undefined') else None
    except Exception:
        conv_id = None
    try:
        import girl_engine
        import girl_switch
    except Exception as e:
        return jsonify({'ok': False, 'error': f'無法載入 girl 模組: {e}'})
    if action != 'girl_go':
        reply_text = f'👌 好的主人，{agent}維持原來的模型，不開 vast girl 了。需要時再叫{agent}～'
        _mid = _save_girl_maid_reply(agent, reply_text, conv_id)
        return jsonify({'ok': True, 'msg': reply_text, 'reply_text': reply_text,
                        'message_id': _mid, 'agent': agent})
    try:
        ok, msg = asyncio.run(girl_engine.request_girl_start(str(d.get('user_id') or 'web'), agent))
    except Exception as e:
        ok, msg = False, f'❌ girl_engine 呼叫失敗: {e}'
    engine_msg = msg
    if ok:
        # 只有 girl 模型「真的在線」才立即切換；若還在開機中，則等 e_flip 上線後自動切換。
        # 全程絕不 pm2 restart mok_agi，改用「清配置緩存 + 通知前端刷新頁面」。
        _girl_now = False
        try:
            import girl_engine as _ge
            _models = _ge._tags()
            _names = [m.get('name', '') for m in (_models or [])]
            _girl_now = _models is not None and any(_ge.GIRL_MODEL in n for n in _names)
        except Exception:
            _girl_now = False
        if _girl_now:
            try:
                import girl_switch
                s_ok, s_msg = girl_switch.switch(agent)
                if s_ok:
                    msg = msg + '\n' + s_msg
            except Exception as se:
                msg = msg + f'\n（切換模型失敗: {se}）'
            try:
                if agent in _agent_config_cache:
                    del _agent_config_cache[agent]
                socketio.emit('reload_page', {'reason': 'girl_online', 'agent': agent})
            except Exception:
                pass
        engine_msg = msg
    reply_text = _girl_maid_reply_text(ok, engine_msg, agent)
    _mid = _save_girl_maid_reply(agent, reply_text, conv_id)
    return jsonify({'ok': ok, 'msg': reply_text, 'reply_text': reply_text,
                    'message_id': _mid, 'engine_msg': engine_msg, 'agent': agent})

def _read_json(path):
    try:
        if os.path.exists(path):
            with open(path, 'r', encoding='utf-8') as f:
                return json.load(f)
    except Exception:
        pass
    return {}


@app.route('/api/girl/status', methods=['GET'])
def api_girl_status():
    '''vast girl 引擎開機狀態（給前端即時更新侍女那則開機訊息）。'''
    agent = request.args.get('agent', '稚')
    home = os.path.expanduser('~')
    online = False
    iface_ok = False
    try:
        import girl_engine
        models = girl_engine._tags()
        if models is not None:
            iface_ok = True
            names = [m.get('name', '') for m in models]
            online = any(girl_engine.GIRL_MODEL in n for n in names)
    except Exception:
        pass
    agent_state = _read_json(os.path.join(home, '.mok', 'agent', agent, 'girl_state.json'))
    vast_state = _read_json(os.path.join(home, '.mok', 'agent', agent, 'jobs', 'vastai', 'llm_state.json'))
    try:
        _ts = max(float(vast_state.get('ts') or 0), float(agent_state.get('ts') or 0))
    except Exception:
        _ts = 0
    state_fresh = (time.time() - _ts) < 600  # 已烘焙範本 0 秒開機（多租競速），10 分鐘內視為最新
    phase = vast_state.get('phase') or agent_state.get('phase') or ('online' if online else 'unknown')
    if (not state_fresh) and (not online) and (not iface_ok):
        phase = 'none'
    fresh = state_fresh
    if online:
        text = '✅ 已開機完成，vast girl（girl:qwen-Claude）已可使用。'
        booting = False
    elif (not iface_ok) and agent_state.get('online') and fresh:
        text = '⏳ 開機中：實例已就緒，反向隧道仍在建立。'
        booting = True
    elif phase in ('created', 'deploying', 'loading', 'starting', 'initializing', 'offering', 'running'):
        text = f'⏳ 開機中：目前階段 {phase}（已烘焙範本・0 秒開機／多租競速挑最穩，通常 1–5 分鐘）。'
        booting = True
    elif phase == 'online':
        text = '⏳ 開機中：模型已載入，反向隧道尚未接通。'
        booting = True
    else:
        text = '⚪ 目前沒有 vast girl 引擎在開機。'
        booting = False
    return jsonify({'ok': True, 'agent': agent, 'online': online, 'booting': booting,
                    'phase': phase, 'ts': agent_state.get('ts') or vast_state.get('ts'),
                    'text': text})


@app.route('/api/girl/reload', methods=['POST'])
def api_girl_reload():
    '''e_flip 上線後呼叫：清配置緩存 + 通知所有前端刷新頁面（不重啟 mok_agi）。'''
    d = request.get_json(force=True, silent=True) or {}
    agent = d.get('agent') or '稚'
    try:
        if agent in _agent_config_cache:
            del _agent_config_cache[agent]
    except Exception:
        pass
    try:
        reload_config(CURRENT_ENV_PATH)
    except Exception:
        pass
    try:
        socketio.emit('reload_page', {'reason': 'girl_ready', 'agent': agent})
    except Exception:
        pass
    return jsonify({'ok': True, 'agent': agent})


@app.route('/api/chat', methods=['POST'])
def api_chat_sse():
    data = request.get_json(force=True)
    try:
        _started = _start_sse_chat_session(data)
    except ValueError as _e:
        return jsonify({"error": str(_e)}), 400
    except PermissionError as _e:
        return jsonify({"error": str(_e), "code": "UNAUTHORIZED"}), 401
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
                    yield "data: " + json.dumps({"type": "ping"}, ensure_ascii=False) + "\n\n"
        except GeneratorExit:
            pass
        finally:
            # 🔧 刷新/斷線時：若 agent 仍在跑（尚未 done）就不清 session，保留緩衝供續流重放
            _maybe_schedule_cleanup_after_disconnect(session_id)
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
    except PermissionError as _e:
        return jsonify({"error": str(_e), "code": "UNAUTHORIZED"}), 401

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
    客戶端從 chat_stream 事件中獲取 sse_session_id 後，建立 EventSource 連接至此。
    🔧 支援「刷新後續流」：連接時先重放緩衝區已發生的事件（思考/工具/回答），再繼續實時接收。"""
    with _sse_lock:
        q = _sse_queues.get(session_id)
        _owner = _sse_users.get(session_id)
    _caller = resolve_tenant_arg()
    # 多租戶隔離（2026-09-27 凜）：SSE session 只允許「開啟該輪的同一 tenant」續流。
    #   否則任何人取得 session_id（例如 /api/chat/active 洩漏）就能讀別人的思考/回覆，
    #   並因共用同一條 queue 把事件吃掉 -> 原發問者（未登入訪客）反而收不到回覆。
    if q is None or not _caller or _owner != _caller:
        # 🔧 未知/已過期的 session 不再回 404：404 會令前端 EventSource 反覆重連 → 像「不停刷新」。
        #    改為回一個「立即結束」的 SSE 串流，讓前端乾淨收尾（收起工作中動畫）。
        def _expired_stream():
            yield ": stream-open\n\n"
            yield f"data: {json.dumps({'type': 'stream_meta', 'sse_session_id': session_id, 'expired': True}, ensure_ascii=False)}\n\n"
            yield f"data: {json.dumps({'type': 'done', 'sse_session_id': session_id, 'expired': True}, ensure_ascii=False)}\n\n"
        return Response(stream_with_context(_expired_stream()), mimetype='text/event-stream', headers={
            'Cache-Control': 'no-cache, no-store, must-revalidate, no-transform',
            'X-Accel-Buffering': 'no',
            'Alt-Svc': 'clear',
            'Connection': 'close',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Expose-Headers': '*',
            'Content-Type': 'text/event-stream; charset=utf-8'
        })

    def generate_sse():
        heartbeat_count = 0
        stream_done = False
        try:
            # 同步送出 prelude，讓中間代理儘快確認串流已開始。
            yield ": stream-open\n\n"
            yield f"data: {json.dumps({'type': 'stream_meta', 'sse_session_id': session_id}, ensure_ascii=False)}\n\n"

            # ===== 第 1 段：重放緩衝區（刷新/斷線後恢復已發生的輸出） =====
            # 🔧 after>0 表示客戶端已用聚合快照重建至 buf[:after]，只續流 after 之後的尾巴
            _after = request.args.get("after", default=0, type=int)
            with _sse_lock:
                buf = list(_sse_buffers.get(session_id, []))
                done_flag = _sse_done.get(session_id, False)
            if _after < 0:
                _after = 0
            # 🔧 以「絕對游標」sent_abs 追蹤已送出進度：after>0 時直接跳過已由快照重建的部分
            sent_abs = _after if _after <= len(buf) else len(buf)
            for ev in buf[sent_abs:]:
                # 🔧C（2026-09-29 稚）：重播事件標記 replay=True，前端對重播只「重建」不「增量 append」。
                try:
                    _ev = ev.copy() if isinstance(ev, dict) else ev
                    _ev["replay"] = True
                except Exception:
                    _ev = ev
                yield f"data: {json.dumps(_ev, ensure_ascii=False)}\n\n"
                sent_abs += 1
                if ev.get('type') in ('done', 'error'):
                    stream_done = True
            if stream_done:
                _schedule_sse_cleanup(session_id, delay_sec=20)
                return

            # ===== 第 2 段：實時續流（queue 僅作「有新資料」喚醒信號，事件一律以緩衝區為權威） =====
            while True:
                try:
                    q.get(timeout=5)
                except _queue.Empty:
                    heartbeat_count += 1
                    if heartbeat_count > 120:  # 10 分鐘超時（5 秒心跳）
                        yield f"data: {json.dumps({'type': 'error', 'content': 'timeout'}, ensure_ascii=False)}\n\n"
                        break
                    yield "data: " + json.dumps({"type": "ping"}, ensure_ascii=False) + "\n\n"
                    continue
                with _sse_lock:
                    buf = list(_sse_buffers.get(session_id, []))
                    done_flag = _sse_done.get(session_id, False)
                while sent_abs < len(buf):
                    ev = buf[sent_abs]
                    sent_abs += 1
                    yield f"data: {json.dumps(ev, ensure_ascii=False)}\n\n"
                    if ev.get('type') in ('done', 'error'):
                        stream_done = True
                if done_flag and sent_abs >= len(buf):
                    if not stream_done:
                        # 緩衝區無 done 事件的極少數情況，補發結束訊號
                        yield f"data: {json.dumps({'type': 'done'}, ensure_ascii=False)}\n\n"
                        stream_done = True
                    break
        except GeneratorExit:
            pass
        finally:
            # 🔧 刷新/斷線時：若 agent 仍在跑（尚未 done）就不清 session，保留緩衝供續流重放
            _maybe_schedule_cleanup_after_disconnect(session_id)

    return Response(stream_with_context(generate_sse()), mimetype='text/event-stream', headers={
        'Cache-Control': 'no-cache, no-store, must-revalidate, no-transform',
        'X-Accel-Buffering': 'no',
        'Alt-Svc': 'clear',
        'Connection': 'keep-alive',
        'Access-Control-Allow-Origin': '*',
        'Access-Control-Expose-Headers': '*',
        'Content-Type': 'text/event-stream; charset=utf-8'
    })


# ---------- 查詢進行中的 SSE session（供頁面刷新後自動續流重放） ----------
@app.route('/api/chat/active', methods=['GET'])
def api_chat_active():
    """查詢指定 agent 目前「進行中」的 SSE session。
    頁面刷新後前端可調用此 API 得知還有哪個 session 在跑，
    再以 EventSource 重新連接 /api/chat/stream/<session_id>，後端會先重放緩衝區再續流。"""
    agent = request.args.get('agent', '').strip()
    # 多租戶隔離（2026-09-27 凜）：只回傳「本請求 tenant 自己」的進行中 session。
    #   否則 A 重新載入頁面會接上 B 的串流（看到 B 的思考/回覆，並把事件從共用 queue 吃掉）。
    _caller = resolve_tenant_arg()
    if not _caller:
        return jsonify({'ok': True, 'sessions': []})
    with _sse_lock:
        sessions = []
        for sid, ag in _sse_agents.items():
            if agent and ag != agent:
                continue
            if _sse_users.get(sid) != _caller:
                continue
            if _sse_done.get(sid, False):
                continue  # 已完成的不算「進行中」
            buf = _sse_buffers.get(sid, [])
            _agg = _sse_agg.get(sid)
            _st = None
            if _agg:
                try:
                    _st = {
                        "rounds": _agg.get("rounds") or [],
                        "think": _agg.get("think", ""),
                        "reply": _agg.get("reply", ""),
                        "n": _agg.get("n", len(buf)),
                    }
                except Exception:
                    _st = None
            sessions.append({
                'session_id': sid,
                'agent': ag,
                'buffered': len(buf),
            })
            if _st is not None:
                sessions[-1]["state"] = _st
    # 依緩衝事件數排序（愈多代表進度愈新），讓前端優先接續最新進度
    sessions.sort(key=lambda s: s['buffered'])
    return jsonify({'ok': True, 'sessions': sessions})


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

    # P0 止血：不再回落成 admin / web_default；未登入且非訪客 -> 擋掉
    user_id = resolve_tenant(data)
    if not user_id:
        try:
            socketio.emit('chat_stream', {'type': 'reply', 'agent': agent_name,
                          'content': '⛔ 請先登入會員（/login）才能使用 AI 服務。'},
                          room=request.sid, namespace='/')
            socketio.emit('chat_stream', {'type': 'done', 'agent': agent_name},
                          room=request.sid, namespace='/')
        except Exception:
            pass
        _running_agents.discard(agent_name)
        return
    # 產物落點（第 1/2/3 層）：在請求執行緒先算好，再帶進背景執行緒
    _out_dir, _anon_sid = resolve_output_ctx(user_id, agent_name)
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
    with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
        cursor = conn.execute(
            'INSERT INTO chat_history (agent, role, content, think_content, timestamp, tenant) VALUES (?, ?, ?, ?, ?, ?)',
            (agent_name, 'user', user_msg, None, time.time(), user_id)
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

        # ===== 無縫接回（2026-10-03 稚）：把本輪「續寫座標」交給補丁持久化 =====
        # 補丁未載入時 globals().get 取不到 -> 機制自動失效，零副作用。
        def _resume_note(_ev=None):
            try:
                _h = globals().get("mok_resume_hook")
                if _h:
                    _h(_ev if isinstance(_ev, dict) else {},
                       agent=agent_name, session_id=_sse_session_id, user_id=user_id,
                       user_msg=user_msg, user_msg_id=user_msg_id,
                       assistant_msg_id=assistant_msg_id,
                       accumulated_reply=accumulated_reply,
                       accumulated_think=accumulated_think)
            except Exception:
                pass


        # 🔧 SSE 備援隊列：即使 Socket.IO 斷線也能通過 SSE 傳遞回應
        _sse_session_id = str(_uuid.uuid4())[:8]
        _sse_q = _queue.Queue()
        with _sse_lock:
            _sse_queues[_sse_session_id] = _sse_q
            _sse_agents[_sse_session_id] = agent_name
            _sse_buffers[_sse_session_id] = []
            _sse_done[_sse_session_id] = False
        _sse_session_sent = False  # 只在第一次 stream_emit 時通知客戶端

        _resume_note({})   # 無縫接回：本輪起點先落一次盤（此刻尚無正文）

        def update_assistant_in_db(msg_id, content, think_content):
            # 重工離線化（2026-10-04 稚）：改登記進背景寫入器批次落盤，不在事件迴圈裡同步寫 DB
            enqueue_chat_update(msg_id, content, think_content)

        def stream_emit(event):
            nonlocal accumulated_think, accumulated_reply, assistant_msg_id, _sse_session_sent
            event["agent"] = agent_name
            _resume_note(event)

            # 🔧 SSE 備援：將事件放入 SSE 隊列（客戶端可通過 /api/chat/stream/<id> 獲取）
            try:
                with _sse_lock:
                    _sse_buffers[_sse_session_id].append(event.copy())
                    _trace_reply_event(event, _sse_session_id)
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
                    with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
                        cursor = conn.execute("INSERT INTO chat_history (agent, role, content, think_content, timestamp, tenant) VALUES (?, ?, ?, ?, ?, ?)", (agent_name, "assistant", "", "", time.time(), user_id))
                        assistant_msg_id = cursor.lastrowid
                        conn.commit()
                update_assistant_in_db(assistant_msg_id, accumulated_reply, accumulated_think)

            elif event["type"] == "reply":
                if event.get("subtype", "normal") in ("pending_list", "tool_process", "semantic_search", "experience", "tool_result"):
                    return
                accumulated_reply += event["content"]
                if assistant_msg_id is None:
                    with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
                        cursor = conn.execute(
                            "INSERT INTO chat_history (agent, role, content, think_content, timestamp, tenant) VALUES (?, ?, ?, ?, ?, ?)",
                            (agent_name, "assistant", "", "", time.time(), user_id)
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
                    with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
                        cursor = conn.execute(
                            "INSERT INTO chat_history (agent, role, content, think_content, timestamp, tenant) VALUES (?, ?, ?, ?, ?, ?)",
                            (agent_name, "assistant", accumulated_reply, accumulated_think, time.time(), user_id)
                        )
                        assistant_msg_id = cursor.lastrowid
                        conn.commit()
                update_assistant_in_db(assistant_msg_id, accumulated_reply, accumulated_think)
                flush_chat_update(assistant_msg_id)   # 回合結束：強制立即落盤
                if event.get("rounds"):
                    _save_rounds_to_db(assistant_msg_id, event["rounds"])
                conv_id = event.get("conv_id")
                if conv_id:
                    with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
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
                    await gate_call(mokagi.process_message,
                        user_id=user_id,
                        text=user_msg,
                        stream_callback=async_stream_cb,
                        agent_name=agent_name,
                        agent_config=agent_config,
                        context_files=context_files,
                        output_dir=_out_dir,
                        anon_sid=_anon_sid,
                        platform="web",
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
            with _sse_lock:
                _sse_done[_sse_session_id] = True
            # 🔧 清理 SSE 備援隊列（延遲 30 秒讓客戶端有時間讀取最後的事件）
            def _delayed_sse_cleanup():
                time.sleep(30)
                with _sse_lock:
                    _sse_queues.pop(_sse_session_id, None)
                    _sse_agents.pop(_sse_session_id, None)
                    _sse_buffers.pop(_sse_session_id, None)
                    _sse_done.pop(_sse_session_id, None)
            threading.Thread(target=_delayed_sse_cleanup, daemon=True).start()

    threading.Thread(target=_bg_worker, daemon=True).start()

        





def _update_user_message_conv_id(agent, conv_id, user_msg_id, user_id=None):
    # 確保 user_id 有效
    if not user_id:
        # 多租戶隔離(20260921)：嚴禁回落成共享身分 web_default（等同任何人共用一份歷史）
        print("[DEBUG] user_id 為空，跳過 conv_id 回退，避免跨用戶污染")
    
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
                # 多租戶隔離(20260921)：禁止 LIKE "%_agent" 模糊匹配（會撈到其他用戶的最新訊息，
                # 導致 a01 的訊息被綁上 admin 的 conv_id → 前端讀到別人的歷史）。
                # 改為僅以 tenant 精確回退。
                try:
                    cursor = conn.execute(
                        'SELECT id FROM conversation_history WHERE tenant = ? AND role = "user" ORDER BY id DESC LIMIT 1',
                        (user_id,)
                    )
                    row = cursor.fetchone()
                    if row:
                        conv_id = row[0]
                        print(f"[DEBUG] 以 tenant={user_id} 精確回退到 user 記錄 ID: {conv_id}")
                except Exception as _e:
                    print(f"[DEBUG] tenant 精確回退失敗（忽略）: {_e}")
                else:
                    print(f"[DEBUG] 在 conversation_history 中未找到 user_key={user_id}_{agent} 的記錄")
    
    # 如果有 conv_id，更新 chat_history
    if conv_id is not None:
        with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
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
    try:
        from flask import session as _fs
        _su = _fs.get('member_user')
    except Exception:
        _su = None
    if not (_su and str(_su) in _PRIVILEGED_TENANTS):
        try:
            socketio.emit('stream_stopped', {'status': 'forbidden'}, room=sid)
        except Exception:
            pass
        print("stop_generation rejected (not admin): %s" % sid)
        return
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

@app.route('/vtuber/<path:filename>')
def vtuber_files(filename):
    """Open-LLM-VTuber 3D 頁面（VRM 模型 + 打字串流 + 表情動作）"""
    folder = 'vtuber'
    full = os.path.join(BASE_DIR, folder, filename)
    if filename.endswith('.html') and os.path.exists(full):
        html = open(full, encoding='utf-8').read()
        ver = _game_asset_version(os.path.join(BASE_DIR, folder))
        html = re.sub(r'\?v=\d+', '?v=' + str(ver), html)
        return html
    return send_from_directory(os.path.join(BASE_DIR, folder), filename)

# ========== VoxCPM 語音克隆（agent/外觀/原聲.mp3 → 合成；失敗自動退回 edge-tts） ==========
VOXCPM_BASE = 'https://openbmb-voxcpm-demo.hf.space/gradio_api'
_VOXCPM_REF_CACHE = {}   # agent -> (file_mtime, server_path)

def _voxcpm_upload_ref(agent, ref_path):
    """轉 mono 16kHz 並上傳參考音檔到 VoxCPM demo，回傳 server_path（含快取）"""
    import tempfile
    try:
        mtime = os.path.getmtime(ref_path)
        cached = _VOXCPM_REF_CACHE.get(agent)
        if cached and cached[0] == mtime:
            return cached[1]
    except OSError:
        mtime = 0
    tmp_wav = os.path.join(tempfile.gettempdir(), 'voxcpm_ref_' + agent + '_' + str(int(time.time())) + '.wav')
    subprocess.run(['ffmpeg', '-y', '-i', ref_path, '-ac', '1', '-ar', '16000', tmp_wav],
                   check=True, capture_output=True, timeout=60)
    try:
        out = subprocess.run(
            ['curl', '-s', '-X', 'POST', VOXCPM_BASE + '/upload',
             '-F', 'files=@' + tmp_wav, '-A', 'Mozilla/5.0'],
            capture_output=True, text=True, timeout=90).stdout
        arr = json.loads(out)
        server_path = arr[0]
        _VOXCPM_REF_CACHE[agent] = (mtime, server_path)
        return server_path
    finally:
        try:
            os.remove(tmp_wav)
        except OSError:
            pass

def _voxcpm_generate_download(text, ref_server_path, out_path,
                              control='自然流暢地朗讀這句話，語氣溫柔自然，聲音自然清晰'):
    """呼叫 VoxCPM generate + 輪詢 SSE + 下載音檔到 out_path，成功回傳 True"""
    import urllib.request
    payload = {
        'data': [
            text,
            control,
            {
                'path': ref_server_path,
                'url': VOXCPM_BASE + '/file=' + ref_server_path,
                'orig_name': os.path.basename(ref_server_path),
                'meta': {'_type': 'gradio.FileData'},
            },
            False, '', 2.0, False, False,
        ]
    }
    req = urllib.request.Request(
        VOXCPM_BASE + '/call/generate',
        data=json.dumps(payload).encode('utf-8'),
        headers={'Content-Type': 'application/json', 'User-Agent': 'Mozilla/5.0'})
    with urllib.request.urlopen(req, timeout=90) as r:
        resp = json.loads(r.read().decode('utf-8'))
    event_id = resp['event_id']
    poll_url = VOXCPM_BASE + '/call/generate/' + event_id
    p_req = urllib.request.Request(poll_url, headers={'User-Agent': 'Mozilla/5.0'})
    audio_url = None
    with urllib.request.urlopen(p_req, timeout=240) as r:
        for raw in r:
            line = raw.decode('utf-8', 'ignore').strip()
            if line.startswith('event:'):
                if line[6:].strip() == 'error':
                    break
                continue  # 'complete' 只是標記，data 在下一行
            if line.startswith('data:'):
                try:
                    arr = json.loads(line[5:].strip())
                    if arr and isinstance(arr, list) and arr[0].get('url'):
                        audio_url = arr[0]['url']
                        break
                except Exception:
                    pass
    if not audio_url:
        return False
    subprocess.run(['curl', '-s', '-o', out_path, '-A', 'Mozilla/5.0', audio_url],
                   check=True, capture_output=True, timeout=120)
    return os.path.exists(out_path) and os.path.getsize(out_path) > 100

@app.route('/vtuber_tts', methods=['POST'])
def vtuber_tts():
    """VTuber 語音：優先 VoxCPM 原聲克隆（agent/外觀/原聲.mp3），失敗退回 edge-tts"""
    import uuid
    data = request.get_json(silent=True) or {}
    text = (data.get('text') or '').strip()
    if not text:
        return jsonify({'success': False, 'error': 'empty text'}), 400
    text = re.sub(r'[*_#`~>|]', '', text)[:500]
    if not text.strip():
        return jsonify({'success': False, 'error': 'empty text'}), 400
    tts_dir = os.path.join(BASE_DIR, 'vtuber', 'tts')
    try:
        os.makedirs(tts_dir, exist_ok=True)
        now = time.time()
        for f in os.listdir(tts_dir):
            fp = os.path.join(tts_dir, f)
            try:
                if f.endswith('.mp3') and now - os.path.getmtime(fp) > 1800:
                    os.remove(fp)
            except Exception:
                pass
        voice = (data.get('voice') or 'zh-TW-HsiaoChenNeural').strip() or 'zh-TW-HsiaoChenNeural'
        agent = (data.get('agent') or '').strip()
        fname = 'tts_' + uuid.uuid4().hex[:10] + '.mp3'
        fpath = os.path.join(tts_dir, fname)

        # 優先：VoxCPM 原聲克隆（agent/外觀/<MOK_L2D_VOICE 指定檔名 或 原聲.mp3>）
        ref_path = None
        if agent:
            _agent_root = os.path.expanduser(f'~/.{MOKAGI_home}/agent')
            _voice_cfg = ''
            _dot = os.path.join(_agent_root, agent, f'.{agent}')
            try:
                if os.path.exists(_dot):
                    with open(_dot, 'r', encoding='utf-8', errors='ignore') as _cf:
                        for _l in _cf:
                            if _l.strip().startswith('MOK_L2D_VOICE='):
                                _voice_cfg = _l.strip().split('=', 1)[1].strip()
                                break
            except Exception:
                pass
            _cands = []
            if _voice_cfg and os.path.splitext(_voice_cfg)[1].lower() in ('.mp3', '.m4a', '.wav', '.ogg', '.flac', '.aac', '.opus', '.webm', '.wma'):
                _cands.append(_voice_cfg)
            _cands.append('原聲.mp3')
            for _c in _cands:
                _p = os.path.join(_agent_root, agent, '外觀', _c)
                if os.path.exists(_p):
                    ref_path = _p
                    break
        if ref_path:
            try:
                server_path = _voxcpm_upload_ref(agent, ref_path)
                if server_path and _voxcpm_generate_download(text, server_path, fpath):
                    if os.path.getsize(fpath) > 100:
                        print('[vtuber_tts] VoxCPM 原聲克隆成功 agent=' + agent)
                        return jsonify({'success': True, 'url': '/vtuber/tts/' + fname, 'engine': 'voxcpm'})
            except Exception as e:
                print('[vtuber_tts] VoxCPM 失敗，退回 edge-tts: ' + str(e))
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                except OSError:
                    pass

        # 備援：edge-tts（預設語音）
        import edge_tts
        async def _gen():
            com = edge_tts.Communicate(text, voice, rate='+0%')
            await com.save(fpath)
        asyncio.run(_gen())
        if not os.path.exists(fpath) or os.path.getsize(fpath) < 100:
            return jsonify({'success': False, 'error': 'tts generate failed'}), 500
        return jsonify({'success': True, 'url': '/vtuber/tts/' + fname, 'engine': 'edge'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


    return send_from_directory(os.path.join(BASE_DIR, folder), filename)

@app.route('/report/<agent>/<path:filename>')
def report_files(agent, filename):
    """提供 Agent jobs/ 目錄下的工作報告 HTML（防路徑穿越）"""
    if not agent or '/' in agent or '\\' in agent or '..' in agent:
        return 'Invalid agent', 400
    jobs_dir = os.path.join(ENV_DIR, agent, 'jobs')
    jobs_real = os.path.realpath(jobs_dir)
    full = os.path.realpath(os.path.join(jobs_dir, filename))
    if not full.startswith(jobs_real + os.sep) or not os.path.isfile(full):
        return 'Not found', 404
    _r = send_from_directory(jobs_dir, filename); _e = os.path.splitext(filename)[1].lower(); _r.headers['Cache-Control'] = ('public, max-age=86400' if _e in ('.webp', '.png', '.jpg', '.jpeg', '.gif', '.moc3', '.mp3', '.ogg', '.mp4', '.webm', '.wasm') else _r.headers.get('Cache-Control', 'no-cache')); return _r

# ================= P3A 自管收款後台 API 反向代理（工作區同源 → 本機 127.0.0.1:5120） =================
_P3AW_UPSTREAM = 'http://127.0.0.1:5120'

@app.route('/p3aw/api/<path:sub>', methods=['GET', 'POST', 'OPTIONS'])
def p3aw_wallet_api(sub=''):
    """工作區的 /p3aw/api/* → 本機 walletd(5120) 的 /api/*；只在主機迴路內轉發，不對外暴露。"""
    if request.method == 'OPTIONS':
        return Response('', status=204)
    return _proxy_social(_P3AW_UPSTREAM + '/api/' + sub, timeout=30)

# ================= mokagi 社交平台 API 反向代理（轉發到 8787） =================
_SOCIAL_UPSTREAM = 'http://127.0.0.1:8787'

def _proxy_social(upstream, timeout=60):
    import urllib.request, urllib.error
    if request.query_string:
        upstream += '?' + request.query_string.decode('utf-8')
    body = request.get_data() if request.method in ('POST', 'DELETE') else None
    headers = {}
    if request.content_type:
        headers['Content-Type'] = request.content_type
    req = urllib.request.Request(upstream, data=body, method=request.method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return Response(resp.read(), status=resp.status,
                            content_type=resp.headers.get('Content-Type', 'application/json'))
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code,
                        content_type=e.headers.get('Content-Type', 'application/json'))
    except Exception as e:
        return jsonify({'success': False, 'error': '社交平台 API 連線失敗: ' + str(e)}), 502

@app.route('/api/posts', methods=['GET', 'POST'])
@app.route('/api/posts/<path:sub>', methods=['GET', 'POST', 'DELETE'])
def social_posts_proxy(sub=''):
    return _proxy_social(_SOCIAL_UPSTREAM + '/api/posts' + ('/' + sub if sub else ''))

@app.route('/api/config', methods=['GET', 'POST'])
def social_config_proxy():
    return _proxy_social(_SOCIAL_UPSTREAM + '/api/config')

@app.route('/api/scrape/run', methods=['POST'])
@app.route('/api/scrape/log', methods=['GET'])
@app.route('/api/scrape/<path:sub>', methods=['GET', 'POST', 'DELETE'])
def social_scrape_proxy(sub=''):
    return _proxy_social(_SOCIAL_UPSTREAM + '/api/scrape' + ('/' + sub if sub else ''), timeout=120)

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
    # 精確名稱或 glob 樣式（見下方 any(fnmatch...)）；非核心/可重建者永久排除，避免把數 GB 大目錄包進備份
    ALWAYS_EXCLUDE = {'backups', '__pycache__', '.git', 'node_modules', 'playwright-browsers', '.speech2text_models', '.pending_cron_confirm', 'mpt', 'trash', '.trash', 'whisper_models', 'CPU_上傳.bat', 'CPU_備份.bat', 'desktop_setup.log', 'browser_profile', 'browser_profile2', 'browser_profile_fb', '_restore_bak_*', '_fix_bak_*'}
    # 「完整備份」必須要有的核心資料 → 預設勾選(=要備份)；其餘項目一律預設不勾選
    DEFAULT_CHECKED = {'agent', 'core', 'frontends', 'gateway', 'html', 'sandbox', 'skill', 'tools', 'work', '.chroma_data', '.memory', 'MOKAGI.sh', 'README.md', 'env.env'}
    items = []
    try:
        entries = sorted(os.listdir(mok_dir))
    except OSError as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    for name in entries:
        if any(fnmatch.fnmatch(name, _pat) for _pat in ALWAYS_EXCLUDE):
            continue
        if '.bak' in name or name in ('CPU_上傳.bat', 'CPU_備份.bat'):
            continue
        fp = os.path.join(mok_dir, name)
        if not os.path.exists(fp):
            continue
        is_dir = os.path.isdir(fp)
        size_bytes = _backup_dir_size(fp) if is_dir else os.path.getsize(fp)
        items.append({'name': name, 'is_dir': is_dir, 'size': _backup_human_size(size_bytes), 'size_bytes': size_bytes, 'default_off': name not in DEFAULT_CHECKED})
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


# ============================================================
# 背景備份（非同步）— 2026-09-05 修復 HTTP 524
# 原因：tar+gzip 大目錄(agent 等) 超過代理 100 秒逾時 → 524
# 作法：HTTP 立即回應，打包丟背景執行緒；前端輪詢 /api/backup/status
# ============================================================
_BACKUP_STATE = {'running': False, 'message': ''}

def _run_backup_task(mok_dir, backup_dir, user_exclude, filename, filepath):
    import tarfile
    try:
        nested_exclude = {'__pycache__', '.git', 'node_modules', 'logs', 'playwright-browsers', '.speech2text_models', 'whisper_models', 'videos', '聲音工作'}
        nested_glob = {'live2d*'}
        top_exclude = user_exclude | {'backups', '__pycache__', '.git', 'node_modules', 'playwright-browsers', '.speech2text_models', '.pending_cron_confirm', 'mpt', 'browser_profile', 'browser_profile2', 'browser_profile_fb', 'trash', '.trash', 'desktop_setup.log', 'whisper_models', 'CPU_上傳.bat', 'CPU_備份.bat'}
        top_glob = {'_restore_bak_*', '_fix_bak_*'}
        with tarfile.open(filepath, 'w:gz', compresslevel=6) as tar:
            for entry in sorted(os.listdir(mok_dir)):
                if entry in top_exclude or any(fnmatch.fnmatch(entry, _p) for _p in top_glob):
                    continue
                full = os.path.join(mok_dir, entry)
                if not os.path.exists(full):
                    continue
                if os.path.isdir(full):
                    for root, dirs, files in os.walk(full):
                        dirs[:] = [d for d in dirs if d not in nested_exclude and not any(fnmatch.fnmatch(d, _p) for _p in nested_glob) and '.bak' not in d]
                        for f in files:
                            if '.bak' in f or f in ('CPU_上傳.bat', 'CPU_備份.bat') or f.lower().endswith('.mp4'):
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
        _BACKUP_STATE['message'] = '✅ 備份完成: ' + filename
    except Exception as e:
        try:
            if os.path.exists(filepath):
                os.remove(filepath)
        except OSError:
            pass
        _BACKUP_STATE['message'] = '❌ 備份失敗: ' + str(e)
    finally:
        _BACKUP_STATE['running'] = False


@app.route('/api/backup/create', methods=['POST'])
def api_backup_create():
    import datetime, re
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

    # 併發保護：同一時間只允許一個備份任務
    if _BACKUP_STATE.get('running'):
        return jsonify({'success': False, 'error': '已有備份任務進行中，請稍候再試（' + str(_BACKUP_STATE.get('message', '')) + '）'}), 409

    _BACKUP_STATE['running'] = True
    _BACKUP_STATE['message'] = '備份執行中…'
    try:
        t = threading.Thread(target=_run_backup_task, args=(mok_dir, backup_dir, user_exclude, filename, filepath), daemon=True)
        t.start()
    except Exception as e:
        _BACKUP_STATE['running'] = False
        return jsonify({'success': False, 'error': str(e)}), 500

    # 非同步：立即回應，避免長時間佔用 HTTP 連線造成 524
    return jsonify({'success': True, 'started': True, 'message': '⏳ 備份已於背景開始執行，完成後會自動出現在下方列表（可自訂名稱: ' + filename + '）', 'filename': filename})


@app.route('/api/backup/status')
def api_backup_status():
    return jsonify({'running': _BACKUP_STATE.get('running', False), 'message': _BACKUP_STATE.get('message', '')})


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

@app.route('/api/backup/contents/<path:filename>')
def api_backup_contents(filename):
    """列出某個備份檔內的頂層項目（還原對話框過濾用；2026-09-28 by 稚）。

    目的：讓前端「選擇性還原」只列出該備份真的有的頂層項目，
          避免使用者選到備份中不存在的項目 -> restore.sh 預檢中止（exit 5）。
    """
    import tarfile
    backup_dir = os.path.join(os.path.expanduser('~'), '.mok', 'backups')
    safe_name = os.path.basename(filename)
    filepath = os.path.join(backup_dir, safe_name)
    if not os.path.isfile(filepath):
        return jsonify({'success': False, 'error': '找不到備份檔案'}), 404
    names = set()
    try:
        with tarfile.open(filepath, 'r:gz') as tar:
            for m in tar:
                nm = m.name
                if nm.startswith('./'):
                    nm = nm[2:]
                top = nm.split('/')[0]
                if top and top not in ('.', '..'):
                    names.add(top)
    except Exception as e:
        return jsonify({'success': False, 'error': '無法讀取備份內容: %s' % e}), 500
    items = sorted(names)
    return jsonify({'success': True, 'items': items, 'has_pgdata': ('pgdata' in names), 'count': len(items)})


@app.route('/api/backup/restore/<path:filename>', methods=['POST'])
def api_backup_restore(filename):
    # 一鍵還原：背景執行 restore.sh（停 pm2 -> 覆蓋檔案 -> 還原 cron -> 重啟所有服務）
    backup_dir = os.path.join(os.path.expanduser('~'), '.mok', 'backups')
    safe_name = os.path.basename(filename)
    filepath = os.path.join(backup_dir, safe_name)
    if not os.path.isfile(filepath):
        return jsonify({'success': False, 'error': '找不到備份檔案'}), 404
    restore_script = os.path.join(os.path.expanduser('~'), '.mok', 'tools', 'scripts', 'restore.sh')
    if not os.path.exists(restore_script):
        return jsonify({'success': False, 'error': '找不到還原腳本 restore.sh'}), 500
    def _clean_names(lst):
        out_names = []
        for x in (lst or []):
            x = str(x).strip().strip('/')
            if x and '/' not in x and x not in ('.', '..') and not x.startswith('-'):
                out_names.append(x)
        return out_names
    data = request.get_json(silent=True) or {}
    only = _clean_names(data.get('only'))
    skip = _clean_names(data.get('skip'))
    cmd = ['setsid', 'bash', restore_script, safe_name]
    mode_txt = '完整還原'
    if only:
        cmd += ['--only', ' '.join(only)]
        mode_txt = '選擇性還原（只還原：' + ' '.join(only) + '）'
    elif skip:
        cmd += ['--skip', ' '.join(skip)]
        mode_txt = '選擇性還原（排除：' + ' '.join(skip) + '）'
    try:
        with open(os.path.join(backup_dir, 'restore_run.out'), 'a') as out:
            subprocess.Popen(cmd,
                             stdout=out, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True, 'started': True,
                    'message': '[還原] 已啟動一鍵還原 -> ' + safe_name +
                               '\n約 6 秒後開始：建立還原前安全備份 -> 停止所有 pm2 -> 覆蓋系統檔案 -> 還原 cron -> 重啟所有服務。'})


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
    """GET /api/tree[?path=<相對 /home/ubuntu 的路徑>]
    2026-09-30：一律惰性展開 —— 每次只回「指定層」的一層項目（不含 children），
    前端展開資料夾時再打一次。省略 path 時列出根層（WATCH_PATH）。"""
    rel = (request.args.get('path') or '').strip().strip('/')
    if not rel:
        return {'tree': get_file_tree_cached()}
    base = os.path.realpath(WATCH_PATH)
    cur = os.path.realpath(os.path.join(base, rel))
    # 安全：不得逃出 WATCH_PATH
    if cur != base and not cur.startswith(base + os.sep):
        return {'error': 'outside root', 'tree': []}
    # 安全：路徑本身或其所屬樹必須落在白名單內（支援 .hermes/skills 這類多層項）
    if cur != base:
        rel_norm = os.path.relpath(cur, base).replace(os.sep, '/')
        if not any(rel_norm == _a or rel_norm.startswith(_a + '/') for _a in ALLOWED_PATHS):
            return {'error': 'not allowed', 'tree': []}
    if not os.path.isdir(cur):
        return {'error': 'not a folder', 'tree': []}
    return {'tree': get_file_tree(cur, 0, one_level=True)}

# ---------- 房間文件樹：侍女 ~/.mok/agent/<名字>；會員 ~/.mok/user/<會員帳號> ----------
@app.route('/api/room_tree')
def api_room_tree():
    """GET /api/room_tree?agent=<名字>[&path=<相對房間路徑>]
    回傳該 agent 房間內一層的項目；path 省略時列出房間根目錄。"""
    agent = (request.args.get('agent') or '').strip()
    rel = (request.args.get('path') or '').replace('\\', '/').strip()
    # 2026-09-30：會員房間移出 agent/（~/.mok/user/<會員>），侍女仍在 ~/.mok/agent/<侍女>
    _root = ENV_DIR
    try:
        import sys as _sys2
        _core2 = os.path.join(os.path.expanduser(f'~.{MOKAGI_home}'), 'core')
        if _core2 not in _sys2.path:
            _sys2.path.insert(0, _core2)
        from output_router import room_root_for as _room_root_for
        _root = _room_root_for(agent)
    except Exception:
        pass
    agent_root = os.path.realpath(_root)
    room = os.path.realpath(os.path.join(agent_root, agent)) if agent else None
    if not agent or not room or room == agent_root or not room.startswith(agent_root + os.sep):
        return {'error': 'unknown agent', 'tree': []}
    if not os.path.isdir(room):
        return {'tree': [], 'room': agent}

    if rel:
        cur = os.path.realpath(os.path.join(room, rel))
        if cur != room and not cur.startswith(room + os.sep):
            return {'error': 'outside room', 'tree': []}
        if not os.path.isdir(cur):
            return {'error': 'not a folder', 'tree': []}
    else:
        cur = room

    names = []
    try:
        names = os.listdir(cur)
    except PermissionError:
        names = []

    def sort_key(item):
        nm = item["name"]
        full = os.path.join(cur, nm)
        is_dir = item["is_dir"]
        mtime = os.path.getmtime(full) if os.path.exists(full) else 0
        return (not is_dir, -mtime)

    items = []
    for it in names:
        fp = os.path.join(cur, it)
        is_dir = os.path.isdir(fp)
        if is_dir and it in SKIP_DIRS:
            continue
        if 'web_viewer' in it:
            continue
        rel_to_room = os.path.relpath(fp, room).replace('\\', '/')
        items.append({'name': it, 'rel': rel_to_room, 'is_dir': is_dir})

    items.sort(key=sort_key)
    return {'tree': items, 'room': agent}


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

        # ===== 2026-10-08 凜：存檔內容防呆（後端治本，繞過前端也擋得住）=====
        # 1) 大小上限 5MB：避免超大內容寫爆磁碟／拖垮房間頁
        if isinstance(content, str) and len(content.encode('utf-8')) > 5 * 1024 * 1024:
            return {"status": "error", "code": "content_too_large",
                    "error": "內容超過 5MB 上限，已拒絕寫入"}, 413
        # 2) JSON 防呆：*.json 必須是合法 JSON；壞資料當場退回、不落地
        if isinstance(content, str) and path.lower().endswith('.json'):
            if content.lstrip('\ufeff').strip():
                try:
                    json.loads(content.lstrip('\ufeff'))
                except (json.JSONDecodeError, ValueError) as _je:
                    _msg = getattr(_je, 'msg', str(_je))
                    _ln = getattr(_je, 'lineno', '?')
                    _col = getattr(_je, 'colno', '?')
                    return {"status": "error", "code": "invalid_json",
                            "error": f"JSON 格式錯誤，已拒絕寫入（第 {_ln} 行、第 {_col} 欄：{_msg}）"}, 400
        # ===== 防呆結束 =====

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

@app.route('/api/eml/list')
def api_eml_list():
    # 列出所有 .eml 郵件檔案（掃描白名單目錄）
    eml_files = []
    SKIP_DIRS = {'node_modules', '.git', '__pycache__', '.cache', '.venv', 'venv', 'site-packages', '.npm', '.cargo', '.local', 'logs', 'jobs', '.git2'}
    def _walk_eml(base, max_depth=7):
        found = []
        if not os.path.isdir(base):
            if os.path.isfile(base) and base.lower().endswith('.eml'):
                try:
                    st = os.stat(base)
                    found.append({'path': os.path.relpath(base, WATCH_PATH), 'name': os.path.basename(base), 'size': st.st_size, 'mtime': st.st_mtime})
                except Exception:
                    pass
            return found
        base_depth = base.rstrip(os.sep).count(os.sep)
        for root, dirs, files in os.walk(base):
            depth = root.rstrip(os.sep).count(os.sep) - base_depth
            if depth >= max_depth:
                dirs[:] = []
            else:
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith('.')]
            for f in files:
                if f.lower().endswith('.eml'):
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, WATCH_PATH)
                    try:
                        st = os.stat(full)
                        found.append({'path': rel, 'name': f, 'size': st.st_size, 'mtime': st.st_mtime})
                    except Exception:
                        pass
        return found
    for prefix in ALLOWED_PATHS:
        eml_files.extend(_walk_eml(os.path.join(WATCH_PATH, prefix)))
    eml_files.sort(key=lambda x: x['mtime'], reverse=True)
    return {'files': eml_files[:500]}

@app.route('/api/ml3/list')
def api_ml3_list():
    # 列出所有 .ml3 媒體檔案（掃描白名單目錄）
    ml3_files = []
    SKIP_DIRS_ML3 = {'node_modules', '.git', '__pycache__', '.cache', '.venv', 'venv', 'site-packages', '.npm', '.cargo', '.local', 'logs', 'jobs', '.git2'}
    def _walk_ml3(base, max_depth=7):
        found = []
        if not os.path.isdir(base):
            if os.path.isfile(base) and base.lower().endswith('.ml3'):
                try:
                    st = os.stat(base)
                    found.append({'path': os.path.relpath(base, WATCH_PATH), 'name': os.path.basename(base), 'size': st.st_size, 'mtime': st.st_mtime})
                except Exception:
                    pass
            return found
        base_depth = base.rstrip(os.sep).count(os.sep)
        for root, dirs, files in os.walk(base):
            depth = root.rstrip(os.sep).count(os.sep) - base_depth
            if depth >= max_depth:
                dirs[:] = []
            else:
                dirs[:] = [d for d in dirs if d not in SKIP_DIRS_ML3 and not d.startswith('.')]
            for f in files:
                if f.lower().endswith('.ml3'):
                    full = os.path.join(root, f)
                    rel = os.path.relpath(full, WATCH_PATH)
                    try:
                        st = os.stat(full)
                        found.append({'path': rel, 'name': f, 'size': st.st_size, 'mtime': st.st_mtime})
                    except Exception:
                        pass
        return found
    for prefix in ALLOWED_PATHS:
        ml3_files.extend(_walk_ml3(os.path.join(WATCH_PATH, prefix)))
    ml3_files.sort(key=lambda x: x['mtime'], reverse=True)
    return {'files': ml3_files[:500]}

@app.route('/api/eml/view/<path:sub_path>')
def api_eml_view(sub_path):
    # 解析 .eml 郵件內容（Subject / From / To / Body）
    normalized = os.path.normpath(sub_path)
    if not any(normalized.startswith(p) for p in ALLOWED_PATHS):
        return {'error': 'Unauthorized access'}, 403
    full_path = os.path.join(WATCH_PATH, normalized)
    if not os.path.exists(full_path) or os.path.isdir(full_path):
        return {'error': 'File not found'}, 404
    try:
        import email
        from email import policy
        with open(full_path, 'rb') as f:
            msg = email.message_from_binary_file(f, policy=policy.default)
        result = {
            'subject': str(msg.get('subject', '') or ''),
            'from': str(msg.get('from', '') or ''),
            'to': str(msg.get('to', '') or ''),
            'cc': str(msg.get('cc', '') or ''),
            'date': str(msg.get('date', '') or ''),
            'body_text': '',
            'body_html': '',
            'attachments': []
        }
        for part in msg.walk():
            if part.is_multipart():
                continue
            filename = part.get_filename()
            if filename:
                result['attachments'].append(filename)
                continue
            ctype = part.get_content_type()
            try:
                payload = part.get_content()
            except Exception:
                raw = part.get_payload(decode=True) or b''
                payload = raw.decode('utf-8', errors='replace')
            if ctype == 'text/plain' and not result['body_text']:
                result['body_text'] = payload
            elif ctype == 'text/html' and not result['body_html']:
                result['body_html'] = payload
        return result
    except Exception as e:
        return {'error': str(e)}, 500


@app.route('/api/env_files')
def get_env_files_api():
    files = get_env_files()
    current = os.path.basename(CURRENT_ENV_PATH) if CURRENT_ENV_PATH else ""
    # 20260929（凜）：側欄 last_active / 工作中燈號改為「只看自己 tenant」，免被其他會員帶動。
    _caller_tenant = resolve_tenant_arg()
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
        # 20260929（凜）：last_active 只算「自己 tenant」的訊息，避免側欄排序被其他會員帶動。
        last_active = 0
        try:
            with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
                if _caller_tenant:
                    cursor = conn.execute(
                        'SELECT timestamp FROM chat_history WHERE agent = ? AND (tenant = ? OR tenant IS NULL) ORDER BY timestamp DESC LIMIT 1',
                        (agent_name, _caller_tenant)
                    )
                else:
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

        # 20260929（凜）：is_running 拆成「自己 tenant 在跑(own)」與「其他人也在跑(others)」，
        #   前端仍以 is_running（=own）維持工作中燈號，但不會再被其他會員帶動。
        _live_tenants = _agent_live_tenants(agent_name)
        _own_running = (_caller_tenant in _live_tenants) if _caller_tenant else False
        _others_running = any((t or "") != (_caller_tenant or "") for t in _live_tenants)
        if (not _live_tenants) and (agent_name in _running_agents):
            _own_running = True  # 無法判定租戶時，維持舊行為
        agents.append({"name": agent_name, "file": f, "icon": icon, "post": post, "desc": desc, "tags": tags, "group": group, "last_active": last_active, "is_running": _own_running, "others_running": _others_running})

    # ----- 按 last_active 降序排序（最新排最前）-----
    agents.sort(key=lambda x: x.get('last_active', 0), reverse=True)

    return {"agents": agents, "current": current}


# ----- wa_auto.py 執行狀態（ws客服 名片小標籤用）-----
@app.route("/api/wa_auto_status")
def api_wa_auto_status():
    """檢查 .mok/skill/whatsappWeb/wa_auto.py 是否正在執行"""
    import subprocess
    running = False
    pid = None
    try:
        r = subprocess.run(
            ["pgrep", "-f", r"wa_auto\.py"],
            capture_output=True, text=True, timeout=5
        )
        if r.returncode == 0:
            running = True
            pids = [p for p in r.stdout.split() if p.strip()]
            pid = pids[0] if pids else None
    except Exception:
        running = False
    return jsonify({"running": running, "pid": pid})






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
                _nuwa_root = os.path.expanduser(f"~/.{MOKAGI_home}/skill/nuwa/角色")
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
    # 生成 soul/user.md（範本：履歷之神/soul/user.md）
    # 「妳負責:」內容取自本 Agent 配置檔 .<name> 的 MOK_AGENT_POST
    # 「自主學習:」時區取自 MOK_ADMIN_TIME_ZONE；時間與主題留 < > 由主人填寫
    _post = ''
    _tz = ''
    _admin_name = '主人'
    try:
        with open(dot_file, 'r', encoding='utf-8') as _cf:
            for _line in _cf:
                _ls = _line.strip()
                if _ls.startswith('MOK_AGENT_POST='):
                    _post = _ls.split('=', 1)[1].strip()
                elif _ls.startswith('MOK_ADMIN_TIME_ZONE='):
                    _tz = _ls.split('=', 1)[1].strip()
                elif _ls.startswith('MOK_ADMIN_NAME='):
                    _admin_name = _ls.split('=', 1)[1].strip()
    except Exception:
        pass
    _user_md = f"""妳負責:
    所有 {_post} 工作

妳房間:
    .mok/agent/{name}/
    可做一切事,但不要亂放文件,一項目一組的放

自主學習:
    每天 {_tz} 時間 < > 妳可以自己搜尋 github 或其他網上 找
    < >
    可安裝在妳房間/jobs/yymmdd-hhmmss_工作目的/

自我意識:
    每次輸出最後,妳都可總結經驗 寫入 .mok/agent/{name}/soul/EXP.md

所有工作:
    所有報告必須是 .html
    每個工作獨立一個資料夾
    {name}/jobs/工作1 , {name}/jobs/工作2 ...


妳房間 {name}/jobs 只可有20個項目,滿了通知我

【核心安全｜硬規則】
    禁止以任何方式重啟、停止 core 進程（含 admin exec / cron / shell / python / 腳本）。
    若修改核心代碼需生效，一律向主人回報「需主人手動重啟」，並停止操作等待確認。
    本規則由工具層強制執行，任何嘗試都會被拒絕。
"""

    _user_md_path = os.path.join(soul_dir, 'user.md')
    if not os.path.exists(_user_md_path):
        with open(_user_md_path, 'w', encoding='utf-8') as _f:
            _f.write(_user_md)

    # ===== Live2D 桌面寵物：寵物樣式 + 原聲（可選） =====
    pet_model = (data.get('pet_model') or '').strip()
    voice_b64 = (data.get('voice_b64') or '').strip()
    _pet_saved = ''
    _voice_saved = ''
    try:
        _appearance_dir = os.path.join(agent_dir, '外觀')
        os.makedirs(_appearance_dir, exist_ok=True)
        if voice_b64:
            try:
                import base64 as _b64
                _raw = _b64.b64decode(voice_b64)
                if len(_raw) > 64:
                    _voice_path = os.path.join(_appearance_dir, '原聲.mp3')
                    with open(_voice_path, 'wb') as _vf:
                        _vf.write(_raw)
                    _voice_saved = '原聲.mp3'
            except Exception as _e:
                print(f"[create_agent] 原聲保存失敗: {_e}")
        if _voice_saved or pet_model:
            with open(dot_file, 'a', encoding='utf-8') as _cf:
                _cf.write('\n# ===== Live2D 桌面寵物 =====\n')
                if _voice_saved:
                    _cf.write('MOK_L2D_VOICE=' + _voice_saved + '\n')
                if pet_model:
                    _cf.write('MOK_L2D_MODEL=' + pet_model + '\n')
            _pet_saved = pet_model
    except Exception as _e:
        print(f"[create_agent] 寵物設定失敗: {_e}")

    return {"status": "ok", "message": f"成功創建 Agent '{name}'，配置文件 {dot_file} 和目錄 {agent_dir}/ 已建立", "agent_md_created": agent_md_created, "pet_model": _pet_saved, "voice": _voice_saved}







# ---------- Live2D 桌面寵物：原聲/樣式更新 + 查詢（Open-LLM-VTuber 用） ----------
@app.route('/api/agent_voice', methods=['POST'])
def agent_voice():
    """上傳/更新 agent 的桌面寵物原聲（存到 <agent>/外觀/原聲.mp3）或寵物樣式，並記到 .<agent> 配置"""
    data = request.get_json(silent=True) or {}
    agent = (data.get('agent') or '').strip()
    if not agent or '/' in agent or '\\' in agent or '..' in agent:
        return jsonify({'success': False, 'error': 'invalid agent'}), 400
    agent_dir = os.path.join(ENV_DIR, agent)
    if not os.path.isdir(agent_dir):
        return jsonify({'success': False, 'error': f"Agent '{agent}' 不存在"}), 404
    dot_file = os.path.join(agent_dir, f'.{agent}')
    try:
        os.makedirs(os.path.join(agent_dir, '外觀'), exist_ok=True)
        # 1) 原聲：voice_b64（base64 音頻）或 voice_data（multipart 上傳）
        _voice = ''
        _b64 = (data.get('voice_b64') or '').strip()
        if _b64:
            import base64 as _b64m
            _raw = _b64m.b64decode(_b64)
            if len(_raw) > 64:
                with open(os.path.join(agent_dir, '外觀', '原聲.mp3'), 'wb') as _vf:
                    _vf.write(_raw)
                _voice = '原聲.mp3'
        elif 'voice_file' in request.files:
            _f = request.files['voice_file']
            if _f and _f.filename:
                with open(os.path.join(agent_dir, '外觀', '原聲.mp3'), 'wb') as _vf:
                    _vf.write(_f.read())
                _voice = '原聲.mp3'
        # 2) 寵物樣式：pet_model（Live2D model 路徑）
        _model = (data.get('pet_model') or '').strip()
        # 3) 寫入 .<agent> 配置
        if _voice or _model:
            _lines = []
            if os.path.exists(dot_file):
                with open(dot_file, 'r', encoding='utf-8') as _cf:
                    _lines = _cf.readlines()
            # 移除舊的 MOK_L2D_* 行
            _lines = [l for l in _lines if not l.strip().startswith(('MOK_L2D_MODEL=', 'MOK_L2D_VOICE='))]
            if not any(l.strip() == '# ===== Live2D 桌面寵物 =====' for l in _lines):
                _lines.append('\n# ===== Live2D 桌面寵物 =====\n')
            if _voice:
                _lines.append('MOK_L2D_VOICE=' + _voice + '\n')
            if _model:
                _lines.append('MOK_L2D_MODEL=' + _model + '\n')
            with open(dot_file, 'w', encoding='utf-8') as _cf:
                _cf.writelines(_lines)
        return jsonify({'success': True, 'agent': agent, 'voice': _voice, 'pet_model': _model})
    except Exception as e:
        print(f"[agent_voice] 失敗: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/agent_pets', methods=['GET'])
def agent_pets():
    """列出所有 agent 的桌面寵物設定（樣式 model + 是否有原聲）"""
    pets = []
    try:
        if os.path.isdir(ENV_DIR):
            for item in sorted(os.listdir(ENV_DIR)):
                _adir = os.path.join(ENV_DIR, item)
                if not os.path.isdir(_adir):
                    continue
                _dot = os.path.join(_adir, f'.{item}')
                _model = ''
                _voice = ''
                if os.path.exists(_dot):
                    with open(_dot, 'r', encoding='utf-8', errors='ignore') as _cf:
                        for _l in _cf:
                            _ls = _l.strip()
                            if _ls.startswith('MOK_L2D_MODEL='):
                                _model = _ls.split('=', 1)[1].strip()
                            elif _ls.startswith('MOK_L2D_VOICE='):
                                _voice = _ls.split('=', 1)[1].strip()
                _has_voice = os.path.isfile(os.path.join(_adir, '外觀', '原聲.mp3'))
                pets.append({'name': item, 'model': _model, 'voice': _voice, 'has_voice': _has_voice})
    except Exception as e:
        print(f"[agent_pets] 失敗: {e}")
    return jsonify({'pets': pets})


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

# ===== /api/mok_config 資安加固（2026-09-26 by 泠）=====
# 1) admin 閘：僅特權 session 可讀
# 2) 白名單輸出：只回非機密 UI 設定，杜絕 MOK_TG_TOKEN / MOK_MODEL_token* / MOK_ALLOWED_USERS 外洩
_MOK_CONFIG_WHITELIST_PREFIXES = (
    'MOK_AGENT_', 'MOK_ADMIN_NAME', 'MOK_ADMIN_TIME_ZONE',
    'MOK_CURRENT_MODEL', 'MOK_MAX_HISTORY_ROUNDS', 'MOK_MEMORY_RECALL_COUNT',
    'MOK_MODEL_NAME', 'MOK_MODEL_url', 'MOK_NUM_THREADS',
    'MOK_temperature', 'MOK_top_p', 'MOK_top_k', 'MOK_num_predict', 'MOK_num_ctx',
    'MOK_repeat_penalty', 'MOK_presence_penalty', 'MOK_frequency_penalty',
    'MOK_max_iterations', 'MOK_max_tack_rounds', 'MOK_dream_EXP', 'MOK_DEMO_', 'MOK_start_msg',
)
_MOK_CONFIG_DENY_SUBSTR = ('token', 'secret', 'key', 'password', 'passwd', 'users', 'chat_id')


def _safe_mok_config():
    _out = {}
    for _k, _v in (MOK_CONFIG or {}).items():
        _ks = str(_k)
        _low = _ks.lower()
        if any(_b in _low for _b in _MOK_CONFIG_DENY_SUBSTR):
            continue
        if any(_ks.startswith(_p) for _p in _MOK_CONFIG_WHITELIST_PREFIXES):
            _out[_ks] = _v
    return _out


@app.route('/api/mok_config')
def get_mok_config():
    if not _is_privileged_session():
        return jsonify({'success': False, 'error': 'forbidden: admin only'}), 403
    return jsonify(_safe_mok_config())

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


# ---------- 做夢：一鍵全體做夢（admin-only，背景執行）2026-10-04 by 衍 ----------
# 面板「🌙 侍女做夢總管」的按鈕呼叫；本體在 core/做夢補丁/dream_core.py
# （CLI：--all --ignore-throttle）。語意：強制立即（略過自流＋份數門檻），
# 但完全沒有新 log 的侍女自動跳過（不空轉）。
_DREAM_ALL_STATE = {'running': False, 'started_at': 0.0, 'finished_at': 0.0,
                    'started_by': '', 'result': None, 'error': '', 'counts': None}
_DREAM_ALL_LOCK = threading.Lock()


def _dream_core_path():
    return os.path.join(os.path.expanduser(f"~/.{MOKAGI_home}"), "core", "做夢補丁", "dream_core.py")


def _run_dream_all_task():
    """背景執行緒：跑 dream_core CLI，解析 JSON 結果回填狀態。"""
    cmd = [sys.executable, _dream_core_path(), "--all", "--ignore-throttle"]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=7200)
        raw = (proc.stdout or "").strip()
        parsed = None
        try:
            parsed = json.loads(raw)
        except Exception:
            i = raw.find("[")
            if i >= 0:
                try:
                    parsed = json.loads(raw[i:])
                except Exception:
                    parsed = None
        counts = {}
        if isinstance(parsed, list):
            for r in parsed:
                k = (r or {}).get("status", "unknown")
                counts[k] = counts.get(k, 0) + 1
        _DREAM_ALL_STATE.update({
            'result': parsed, 'counts': counts,
            'error': '' if proc.returncode == 0 else ((proc.stderr or '')[-500:]),
        })
    except subprocess.TimeoutExpired:
        _DREAM_ALL_STATE.update({'error': '執行逾時（>7200s）'})
    except Exception as e:
        _DREAM_ALL_STATE.update({'error': str(e)})
    finally:
        _DREAM_ALL_STATE['running'] = False
        _DREAM_ALL_STATE['finished_at'] = time.time()


@app.route('/api/dream/run_all', methods=['POST'])
def api_dream_run_all():
    """admin-only（見 _ADMIN_ONLY_PREFIXES）：背景對所有已啟用做夢的侍女跑一次『全體做夢』。"""
    with _DREAM_ALL_LOCK:
        if _DREAM_ALL_STATE.get('running'):
            return jsonify({'success': False, 'running': True,
                            'error': '已有全體做夢在背景執行中，請稍候'}), 409
        _DREAM_ALL_STATE.update({
            'running': True, 'started_at': time.time(), 'finished_at': 0.0,
            'started_by': _session_member_user() or '', 'result': None,
            'error': '', 'counts': None,
        })
    try:
        threading.Thread(target=_run_dream_all_task,
                         name='mok-dream-all', daemon=True).start()
    except Exception as e:
        _DREAM_ALL_STATE['running'] = False
        return jsonify({'success': False, 'error': str(e)}), 500
    return jsonify({'success': True, 'started': True,
                    'message': '🌙 全體做夢已於背景開始（略過節流/門檻，只跳過沒有新 log 的侍女）'})


@app.route('/api/dream/status')
def api_dream_status():
    """admin-only：查詢全體做夢背景任務狀態。"""
    st = dict(_DREAM_ALL_STATE)
    st['success'] = True
    return jsonify(st)


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
    # 2026-09-27 權限收斂（凜）：只剩 admin/root 能查全量；
    # 其他身分（登入會員 / 訪客）一律強制只查自己，未識別者擋掉。
    _viewer = _session_member_user()
    if _viewer not in _PRIVILEGED_TENANTS:
        if not _viewer:
            try:
                from flask import g as _fg
                _sid = getattr(_fg, '_anon_sid', None) or getattr(_fg, '_anon_new_sid', None)
            except Exception:
                _sid = None
            _viewer = ('guest:' + _sid) if _sid else resolve_tenant_arg()
        if not _viewer:
            try:
                return jsonify({'success': False, 'error': 'forbidden'}), 403
            except Exception:
                return ('forbidden', 403)
        if user != _viewer:
            user = _viewer
    
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
    
    with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
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

    # ===== 多租戶隔離(20260921)：一般租戶只看自己的資料，特權(admin)才可讀全部 =====
    tenant = resolve_tenant_arg()
    if tenant is None:
        return {"messages": [], "has_more": False}
    # 20260929（凜）：預設一律收斂到「自己的 tenant」（連 admin 亦同），避免 admin 讀到其他會員
    #   在同一 agent 的對話而出現「對話流污染」。admin 需跨租戶總覽時請明示 ?scope=all。
    _read_all = (request.args.get('scope') == 'all') and _can_read_all(tenant)

    with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
        conn.row_factory = sqlite3.Row
        # 總記錄數（用於判斷是否有更多）
        if _read_all:
            total = conn.execute(
                'SELECT COUNT(*) FROM chat_history WHERE agent = ?', (agent,)
            ).fetchone()[0]
        else:
            total = conn.execute(
                'SELECT COUNT(*) FROM chat_history WHERE agent = ? AND tenant = ?',
                (agent, tenant)
            ).fetchone()[0]

        # 按 id 降序取最新數據（新消息在前）
        if _read_all:
            rows = conn.execute(
                '''SELECT id, conv_id, role, content, think_content, rounds, timestamp
                   FROM chat_history WHERE agent = ?
                   ORDER BY id DESC LIMIT ? OFFSET ?''',
                (agent, limit, offset)
            ).fetchall()
        else:
            rows = conn.execute(
                '''SELECT id, conv_id, role, content, think_content, rounds, timestamp
                   FROM chat_history WHERE agent = ? AND tenant = ?
                   ORDER BY id DESC LIMIT ? OFFSET ?''',
                (agent, tenant, limit, offset)
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
    tenant = resolve_tenant(data)
    if not tenant:
        return {"error": "unauthorized: 請先登入會員（/login）", "code": "UNAUTHORIZED"}, 401
    if not agent or not role:
        return {"error": "Missing required fields"}, 400
    with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
        conn.execute(
            'INSERT INTO chat_history (agent, role, content, think_content, conv_id, timestamp, tenant) VALUES (?, ?, ?, ?, ?, ?, ?)',
            (agent, role, content, think_content, conv_id, timestamp, tenant)
        )
        conn.commit()
    return {"status": "ok"}

@app.route('/api/chat_history', methods=['DELETE'])
def delete_chat_history():
    # 多租戶隔離(20260921)：清空 agent 歷史屬全域破壞性操作，僅特權租戶可執行
    # 2026-09-27（凜）：訪客/會員對話改為雲端保留後，非特權租戶也需能清空「自己」的歷史，
    #   否則未登入訪客按下「清空對話」會拿到 401，重整後雲端歷史又被拉回來（看起來像清不掉）。
    _tenant = resolve_tenant_arg()
    if _tenant is None:
        return {"error": "unauthorized: 僅管理員可清空對話歷史", "code": "UNAUTHORIZED"}, 401
    agent = request.args.get('agent', '')
    if not agent:
        return {"error": "Missing agent parameter"}, 400
    # 20260929（凜）：預設只清「自己 tenant」（連 admin 亦同），避免一鍵清空誤刪其他會員對話；
    #   真要全清需明示 ?scope=all，且仍限特權租戶。
    _global_clear = (request.args.get('scope') == 'all') and _can_read_all(_tenant)
    with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
        if _global_clear:
            conn.execute('DELETE FROM chat_history WHERE agent = ?', (agent,))
        else:
            # 只刪自己 tenant 的列，絕不影響他人
            conn.execute('DELETE FROM chat_history WHERE agent = ? AND tenant = ?', (agent, _tenant))
        conn.commit()
    # 同時清除 mokagi 內存中的歷史（僅特權租戶＝真正的全域清除）
    if _global_clear:
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
    with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
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

    # 多租戶隔離(20260921)：身分一律取自 session／訪客 id，
    # 嚴禁回落 ADMIN_CHAT_ID，否則任何訪客都是在搜 admin 的對話歷史。
    chat_id = resolve_tenant(data)
    if chat_id is None:
        return {"error": "unauthorized: 請先登入會員（/login）", "code": "UNAUTHORIZED"}, 401

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
    out_lines = []

    # 📄 工作報告：掃描 jobs/ 目錄下所有 .html 檔案（含子目錄）
    if os.path.isdir(jobs_dir):
        try:
            reports = []
            for root, dirs, files in os.walk(jobs_dir):
                dirs.sort()
                for fn in sorted(files):
                    if fn.lower().endswith('.html'):
                        full = os.path.join(root, fn)
                        rel = os.path.relpath(full, jobs_dir)
                        st = os.stat(full)
                        reports.append((rel, full, st.st_size, st.st_mtime))
            if reports:
                from urllib.parse import quote
                out_lines.append(f'<div style="padding:8px; border-bottom:1px solid #3e3e42; color:#4ec9b0; font-weight:bold;">📄 工作報告 共 {len(reports)} 份</div>')
                for rel, full, size, mtime in reports:
                    url = '/report/' + quote(agent_name) + '/' + quote(rel)
                    # 🗑 刪除鍵字串（2026-10-07 by indexPage｜Jobs 面板非 admin 亦看得到，故僅對特權 session 渲染；後端端點仍二次把關）
                    # 📋 複製路徑鍵（2026-10-08 by indexPage｜主人要求：👁 預覽 後可一鍵複製相對路徑，如「賺錢王/jobs/2026-10-07/coldcall_漏斗升級指標/report.html」）
                    from html import escape as _htmlesc
                    copy_btn = (
                        f'<button onclick="copyReportPath(this)" data-copypath="{_htmlesc(agent_name + "/jobs/" + rel, quote=True)}" '
                        'title="複製相對路徑（agent/jobs/…）" style="background:#2a2a2e; color:#c9a0ff; border:1px solid #40345a; border-radius:10px; padding:2px 10px; cursor:pointer; font-size:0.75rem;">📋 複製路徑</button>'
                    )
                    # 🗑 刪除鍵字串（2026-10-07 by indexPage｜Jobs 面板非 admin 亦看得到，故僅對特權 session 渲染；後端端點仍二次把關）
                    # indexPage|主人：🗑 刪除 移到最右、與預覽太近|del_btn 加 margin-left:auto 推至列最右|202610080120(香港)
                    del_btn = ''
                    if _is_privileged_session():
                        del_btn = (
                            f'<button onclick="deleteReport(this)" data-agent="{_htmlesc(agent_name, quote=True)}" data-path="{_htmlesc(rel, quote=True)}" '
                            'title="移入回收站，30 天內可還原" style="margin-left:auto; background:#2a2a2e; color:#ff9b9b; border:1px solid #5a3a3a; border-radius:10px; padding:2px 10px; cursor:pointer; font-size:0.75rem;">🗑 刪除</button>'
                        )
                    size_kb = size / 1024.0
                    time_str = time.strftime('%m-%d %H:%M', time.localtime(mtime))
                    out_lines.append(
                        '<div style="padding:6px 8px; border-bottom:1px solid #2a2a2e; display:flex; align-items:center; gap:8px; flex-wrap:wrap; font-family:sans-serif;">'
                        f'<a href="{url}" target="_blank" style="color:#4ec9b0; text-decoration:none; font-weight:bold; font-size:0.85rem;">📄 {rel}</a>'
                        f'<span style="color:#888; font-size:0.7rem;">({size_kb:.1f}KB · {time_str})</span>'
                        f'<button onclick="toggleReportPreview(this)" data-src="{url}" style="background:#2a2a2e; color:#4ec9b0; border:1px solid #3e3e42; border-radius:10px; padding:2px 10px; cursor:pointer; font-size:0.75rem;">👁 預覽</button>'
                        + copy_btn
                        + del_btn
                        + '</div>'
                        + '<div style="display:none; margin:0 8px 8px 8px;">'
                        f'<iframe style="width:100%; height:420px; border:1px solid #3e3e42; border-radius:6px; background:#fff;"></iframe>'
                        '</div>'
                    )
        except Exception as e:
            out_lines.append(f'<div style="color:#ff6b6b; padding:2px 8px;">⚠️ 掃描工作報告失敗: {str(e)}</div>')

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
                socketio.emit('agent_jobs_result', {'content': ''.join(out_lines + lines)}, room=request.sid)
                return
        except Exception as e:
            if out_lines:
                socketio.emit('agent_jobs_result', {'content': ''.join(out_lines) + f'<div style="color:#ff6b6b; padding:2px 8px;">⚠️ 讀取工作失敗: {str(e)}</div>'}, room=request.sid)
                return
            socketio.emit('agent_jobs_result', {'error': f'讀取 jobs 目錄失敗: {str(e)}'}, room=request.sid)
            return

    if out_lines:
        socketio.emit('agent_jobs_result', {'content': ''.join(out_lines) + '<div style="color:#888; padding:8px;">(無工作記錄，僅工作報告)</div>'}, room=request.sid)
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


@socketio.on('delete_agent_report')
def handle_delete_agent_report(data):
    """刪除單一工作報告（2026-10-07 by indexPage｜Jobs 面板「🗑 刪除」鍵專用）。

    admin-only；僅接受該 agent jobs/ 底下的 .html；一律走 trash.sh 進回收站（30 天可還原），不物理刪除。
    """
    if not _is_privileged_session():
        socketio.emit('agent_report_deleted', {'error': 'forbidden: admin only'}, room=request.sid)
        return
    data = data or {}
    agent_name = str(data.get('agent') or '').strip()
    rel_path = str(data.get('path') or '').strip()
    if (not agent_name or '/' in agent_name or '\\' in agent_name or '..' in agent_name
            or not rel_path or rel_path.startswith('/') or '..' in rel_path or '\\' in rel_path):
        socketio.emit('agent_report_deleted', {'error': '參數不合法'}, room=request.sid)
        return
    if os.path.splitext(rel_path)[1].lower() != '.html':
        socketio.emit('agent_report_deleted', {'error': '僅能刪除 .html 工作報告'}, room=request.sid)
        return
    jobs_dir = os.path.join(ENV_DIR, agent_name, 'jobs')
    jobs_real = os.path.realpath(jobs_dir)
    full = os.path.realpath(os.path.join(jobs_dir, rel_path))
    if not full.startswith(jobs_real + os.sep) or not os.path.isfile(full):
        socketio.emit('agent_report_deleted', {'error': '找不到該報告'}, room=request.sid)
        return
    try:
        import subprocess
        r = subprocess.run(['bash', os.path.expanduser('~/.mok/tools/trash.sh'), full],
                           capture_output=True, text=True, timeout=30)
        if r.returncode != 0:
            msg = (r.stderr or r.stdout or '').strip() or ('exit %s' % r.returncode)
            socketio.emit('agent_report_deleted', {'error': '刪除失敗: ' + msg}, room=request.sid)
            return
        socketio.emit('agent_report_deleted', {'success': True, 'agent': agent_name, 'path': rel_path}, room=request.sid)
    except Exception as e:
        socketio.emit('agent_report_deleted', {'error': '刪除失敗: ' + str(e)}, room=request.sid)


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
    # admin-only（2026-09-26 by 泠）：agent 設定檔含環境變數/金鑰，僅特權 session 可讀。
    if not _is_privileged_session():
        socketio.emit('agent_settings_result', {'error': 'forbidden: admin only'}, room=request.sid)
        return
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
    # admin-only（2026-09-26 by 泠）：寫入 agent 設定檔（含金鑰）僅限特權 session。
    if not _is_privileged_session():
        socketio.emit('agent_settings_saved', {'error': 'forbidden: admin only'}, room=request.sid)
        return
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

# ===== /static/ 白名單（2026-10-03 by mokagi說明；主人指示「static 不是雜物櫃」）=====
_STATIC_ALLOWED_EXT = {
    '.js', '.mjs', '.css', '.map',
    '.json', '.html', '.htm',
    '.png', '.jpg', '.jpeg', '.gif', '.svg', '.webp', '.ico', '.bmp', '.avif',
    '.woff', '.woff2', '.ttf', '.otf', '.eot',
    '.mp3', '.wav', '.ogg', '.wasm',
}

@app.before_request
def handle_static():
    if request.path.startswith('/static/'):
        filename = request.path[8:]  # 去掉 '/static/'
        # admin-only：noVNC 桌面靜態頁（/static/novnc/*）僅限 admin（2026-09-23 by 凜）
        if filename.startswith('novnc/') and not _is_privileged_session():
            return jsonify({'success': False, 'error': 'forbidden: admin only'}), 403
        # 白名單管制（2026-10-03）：非白名單副檔名一律 404，static 不再當雜物櫃
        _ext = os.path.splitext(filename)[1].lower()
        if _ext not in _STATIC_ALLOWED_EXT:
            return jsonify({'success': False, 'error': 'forbidden: static file type not allowed'}), 404
        return send_from_directory(static_dir, filename)


@app.route('/webTools/gpu_status_snapshot.json')
def gpu_status_snapshot():
    """GPU 快照（退回用）：僅限 admin。原檔已從公開 /static/ 撤出（2026-10-03 by mokagi說明）。"""
    if not _is_privileged_session():
        return jsonify({'success': False, 'error': 'forbidden: admin only'}), 403
    _p = os.path.expanduser(f"~/.{MOKAGI_home}/html/webTools/gpu_status_snapshot.json")
    try:
        with open(_p, encoding='utf-8') as _f:
            return app.response_class(_f.read(), mimetype='application/json')
    except Exception as _e:
        return jsonify({'success': False, 'error': str(_e)}), 404


# ===== admin-only 管制（2026-09-23 by 凜）：緊急重啟 / 進化 / admin 桌面 / 備份 =====
_ADMIN_ONLY_PREFIXES = ('/api/backup/', '/webTools/admin.html', '/skill/進化/','/webTools/novnc','/api/pm2panel/','/api/dream/')


def _is_privileged_session():
    """是否為特權（admin/root）身分；以登入 session 為唯一依據。"""
    try:
        _u = _session_member_user()
        if _u and str(_u) in _PRIVILEGED_TENANTS:
            return True
    except Exception:
        pass
    return False


@app.before_request
def _admin_only_guard():
    if request.method == 'OPTIONS':
        return None
    p = request.path or ''
    if not (p == '/backup' or p.startswith(_ADMIN_ONLY_PREFIXES)):
        return None
    if _is_privileged_session():
        return None
    return jsonify({'success': False, 'error': 'forbidden: admin only'}), 403


@app.route('/api/whoami')
def api_whoami():
    _u = _session_member_user()
    return jsonify({'logged_in': bool(_u), 'username': _u, 'is_admin': _is_privileged_session()})
        
# ---------- 冷呼控制台 API 代理（轉發到 cold_call 伺服器 8765，解決 iframe 跨埠問題）----------
@app.route('/skill/賺錢王/api/<path:sub_path>', methods=['GET', 'POST', 'OPTIONS'])
def coldcall_api_proxy(sub_path):
    import urllib.request, urllib.error
    target = 'http://127.0.0.1:8765/api/' + sub_path
    if request.query_string:
        target += '?' + request.query_string.decode('utf-8')
    data = request.get_data() if request.method == 'POST' else None
    req = urllib.request.Request(target, data=data, method=request.method)
    ctype = request.headers.get('Content-Type') or 'application/json'
    req.add_header('Content-Type', ctype)
    try:
        with urllib.request.urlopen(req, timeout=180) as resp:
            body = resp.read()
            return Response(body, status=resp.status,
                            content_type=resp.headers.get('Content-Type', 'application/json'))
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code, content_type='application/json')
    except Exception as e:
        return jsonify({'error': 'coldcall proxy failed: %s' % e}), 502

# ---------- PM2 一鍵開關面板 API 代理（轉發到獨立服務 127.0.0.1:8331；解決 iframe 跨埠／混用內容問題）----------
@app.route('/api/pm2panel/<path:sub_path>', methods=['GET', 'POST', 'OPTIONS'])
def pm2panel_api_proxy(sub_path):
    import urllib.request, urllib.error
    if request.method == 'OPTIONS':
        _o = Response('', status=204)
        _o.headers['Access-Control-Allow-Origin'] = '*'
        _o.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        _o.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return _o
    target = 'http://127.0.0.1:8331/api/' + sub_path
    if request.query_string:
        target += '?' + request.query_string.decode('utf-8')
    data = request.get_data() if request.method == 'POST' else None
    req = urllib.request.Request(target, data=data, method=request.method)
    req.add_header('Content-Type', request.headers.get('Content-Type') or 'application/json')
    try:
        with urllib.request.urlopen(req, timeout=90) as resp:
            body = resp.read()
            return Response(body, status=resp.status,
                            content_type=resp.headers.get('Content-Type', 'application/json'))
    except urllib.error.HTTPError as e:
        return Response(e.read(), status=e.code, content_type='application/json')
    except Exception as e:
        return jsonify({'ok': False, 'error': 'pm2panel proxy failed: %s' % e}), 502

# ---------- 公開追蹤端點（客戶 Demo 頁回報開啟，/project/* 為公開路徑）----------
@app.route('/api/track', methods=['POST', 'OPTIONS'])
@app.route('/project/track', methods=['POST', 'OPTIONS'])
def public_track():
    import urllib.request, urllib.error
    target = 'http://127.0.0.1:8765/api/track'
    data = request.get_data()
    if request.method == 'OPTIONS':
        resp = Response('', status=204)
        resp.headers['Access-Control-Allow-Origin'] = '*'
        resp.headers['Access-Control-Allow-Methods'] = 'POST, OPTIONS'
        resp.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return resp
    req = urllib.request.Request(target, data=data, method='POST')
    req.add_header('Content-Type', request.headers.get('Content-Type') or 'application/json')
    # 傳遞真實客戶 IP 與地區（Cloudflare → tunnel → 本機，供 cold_call 控制台記錄）
    real_ip = (request.headers.get('CF-Connecting-IP') or
               request.headers.get('X-Forwarded-For') or
               request.remote_addr or '')
    if real_ip:
        real_ip = real_ip.split(',')[0].strip()
        req.add_header('X-Forwarded-For', real_ip)
        req.add_header('X-Real-IP', real_ip)
        req.add_header('CF-Connecting-IP', real_ip)
    if request.headers.get('CF-IPCountry'):
        req.add_header('CF-IPCountry', request.headers['CF-IPCountry'])
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            out = Response(body, status=resp.status, content_type=resp.headers.get('Content-Type', 'application/json'))
            out.headers['Access-Control-Allow-Origin'] = '*'
            return out
    except Exception as e:
        return jsonify({'error': 'track proxy failed: %s' % e}), 502

# ---------- 會議模式持久化（P0：任務/輸出保存，2026-09-27 by 春）----------
# 全站追蹤 collector 掛載（蹤 2026-10-06；載入失敗不影響本站）
try:
    sys.path.insert(0, '/home/ubuntu/.mok/agent/蹤/jobs/2026-10-06/tracking_system')
    import mok_track_routes
    mok_track_routes.register(app)
except Exception as _track_err:
    print('mok tracker routes not loaded:', _track_err)

_MEETING_DIR = os.path.join(BASE_DIR, '會議模式')
_MEETING_STATE = os.path.join(_MEETING_DIR, 'state.json')


@app.route('/api/meeting/save', methods=['POST', 'OPTIONS'])
def meeting_save():
    if request.method == 'OPTIONS':
        return ('', 204)
    try:
        payload = request.get_json(force=True, silent=True)
        if not isinstance(payload, dict):
            return jsonify({'success': False, 'error': 'invalid payload'}), 400
        os.makedirs(_MEETING_DIR, exist_ok=True)
        tmp = _MEETING_STATE + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False)
        os.replace(tmp, _MEETING_STATE)
        return jsonify({'success': True, 'saved': len(payload.get('tasks') or []), 'ts': payload.get('ts')})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


@app.route('/api/meeting/load', methods=['GET'])
def meeting_load():
    try:
        if os.path.isfile(_MEETING_STATE):
            with open(_MEETING_STATE, encoding='utf-8') as f:
                return jsonify({'success': True, 'state': json.load(f)})
        return jsonify({'success': True, 'state': None})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------- 動態頁面路由（自動匹配 templates 下的 .html）----------
@app.route('/<path:page>')
def dynamic_page(page):
    # 嘗試渲染 HTML 模板（靜態資源已被 before_request 攔截）
    if page.endswith('.html'):
        template = page
    else:
        template = page + '.html'
    # project/ 下的克隆頁面（如 MokCs demo 頁）：直接發送原始檔案，
    # 避免 Jinja2 誤解析頁面 CSS/JS 中的 {#、{{ 等語法導致 404
    if template.startswith('project/'):
        _full = os.path.join(BASE_DIR, template)
        if os.path.isfile(_full):
            return send_file(_full, conditional=True)
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

    user_id = resolve_tenant_arg()
    if not user_id:
        return {"tasks": []}
    
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
    if path.endswith((".js", ".css", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".ico", ".woff2", ".woff", ".webp", ".moc3")):
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
    # 多租戶隔離(20260921)：全域搜尋僅限特權租戶，其餘只能搜自己的 tenant
    _tenant = resolve_tenant_arg()
    if _tenant is None:
        return {"error": "unauthorized: 請先登入會員（/login）", "code": "UNAUTHORIZED"}, 401
    _read_all = _can_read_all(_tenant)
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
        with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT id, agent, role, content, timestamp
                   FROM chat_history
                   WHERE content LIKE ?{_tf}
                   ORDER BY timestamp DESC
                   LIMIT ?""".format(_tf="" if _read_all else " AND tenant = ?"),
                ((f"%{q}%", limit) if _read_all else (f"%{q}%", _tenant, limit))
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
                   WHERE content LIKE ?{_tf}
                   ORDER BY timestamp DESC
                   LIMIT ?""".format(_tf="" if _read_all else " AND tenant = ?"),
                ((f"%{q}%", limit) if _read_all else (f"%{q}%", _tenant, limit))
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
        "title": (data.get("title") or "")[:60],
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

@app.route("/api/bookmark/rename", methods=["POST"])
def bookmark_rename():
    """編輯書籤標題"""
    data = request.get_json(silent=True) or {}
    conv_id = str(data.get("conv_id", "")).strip()
    title = str(data.get("title", "")).strip()[:60]
    if not conv_id:
        return {"success": False, "error": "缺少 conv_id"}, 400
    bookmarks = _load_bookmarks()
    for bm in bookmarks:
        if str(bm.get("conv_id", "")) == conv_id:
            bm["title"] = title
            _save_bookmarks(bookmarks)
            return {"success": True, "message": "已更新書籤標題"}
    return {"success": False, "error": "找不到該書籤"}, 404

@app.route("/api/bookmark/conversation", methods=["GET"])
def bookmark_conversation():
    """根據 conv_id 取得完整對話"""
    # 多租戶隔離(20260921)：只能取自己 tenant 的對話，特權租戶可讀全部
    _tenant = resolve_tenant_arg()
    if _tenant is None:
        return {"error": "unauthorized: 請先登入會員（/login）", "code": "UNAUTHORIZED"}, 401
    _read_all = _can_read_all(_tenant)
    conv_id = request.args.get("conv_id", "").strip()
    if not conv_id:
        return {"error": "缺少 conv_id"}, 400

    messages = []
    # 搜尋 chat_history
    try:
        with closing(sqlite3.connect(DB_PATH, timeout=30)) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT role, content, think_content, timestamp FROM chat_history WHERE CAST(conv_id AS TEXT) = ?" + ("" if _read_all else " AND tenant = ?") + " ORDER BY timestamp ASC",
                ((conv_id,) if _read_all else (conv_id, _tenant))
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
                "SELECT role, content, timestamp FROM conversation_history WHERE CAST(id AS TEXT) = ?" + ("" if _read_all else " AND tenant = ?") + " ORDER BY timestamp ASC",
                ((conv_id,) if _read_all else (conv_id, _tenant))
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


# ===== 未讀標記（per 會員／訪客，跨裝置同步）=====
# indexPage|主人乙案：未讀存後端 per 會員、跨裝置同步、點進去才清除|新增 /api/unread/list、/api/unread/set、/api/unread/clear 三端點與原子 JSON 儲存|202610080215(香港)
UNREAD_FILE = os.path.expanduser(f"~/.{MOKAGI_home}/.memory/unread_marks.json")
UNREAD_TTL = 90 * 24 * 3600
_UNREAD_LOCK = threading.Lock()


def _unread_load():
    try:
        with open(UNREAD_FILE, "r", encoding="utf-8") as f:
            _d = json.load(f)
        return _d if isinstance(_d, dict) else {}
    except Exception:
        return {}


def _unread_prune(store):
    _now = time.time()
    for _t in list(store.keys()):
        _marks = store.get(_t) or {}
        _keep = {a: v for a, v in _marks.items()
                 if isinstance(v, dict) and (_now - float(v.get("t") or 0)) < UNREAD_TTL}
        if _keep:
            store[_t] = _keep
        else:
            store.pop(_t, None)


def _unread_save(store):
    try:
        os.makedirs(os.path.dirname(UNREAD_FILE), exist_ok=True)
        _tmp = UNREAD_FILE + ".tmp"
        with open(_tmp, "w", encoding="utf-8") as f:
            json.dump(store, f, ensure_ascii=False, indent=2)
        os.replace(_tmp, UNREAD_FILE)
        return True
    except Exception as e:
        print(f"[unread] 儲存失敗: {e}")
        return False


def _unread_deny():
    return {"success": False, "error": "unauthorized: 請先登入會員，或重新整理頁面後再試",
            "code": "UNAUTHORIZED"}, 401


@app.route("/api/unread/list", methods=["GET"])
def unread_list():
    _tenant = resolve_tenant_arg()
    if _tenant is None:
        return _unread_deny()
    with _UNREAD_LOCK:
        _marks = _unread_load().get(_tenant) or {}
    return {"success": True, "marks": _marks}


@app.route("/api/unread/set", methods=["POST"])
def unread_set():
    data = request.get_json(silent=True) or {}
    _tenant = resolve_tenant(data)
    if _tenant is None:
        return _unread_deny()
    _agent = str(data.get("agent") or "").strip()
    if not _agent:
        return {"success": False, "error": "缺少 agent"}, 400
    with _UNREAD_LOCK:
        _store = _unread_load()
        _unread_prune(_store)
        _marks = _store.get(_tenant) or {}
        if "note" in data and data.get("note") is None:
            _marks.pop(_agent, None)
            _action = "clear"
        else:
            _marks[_agent] = {"t": time.time(), "note": str(data.get("note") or "").strip()[:200]}
            _action = "set"
        _store[_tenant] = _marks
        _ok = _unread_save(_store)
    if not _ok:
        return {"success": False, "error": "未讀儲存失敗（請檢查磁碟空間或權限）"}, 500
    return {"success": True, "action": _action, "marks": _marks}


@app.route("/api/unread/clear", methods=["POST"])
def unread_clear():
    data = request.get_json(silent=True) or {}
    _tenant = resolve_tenant(data)
    if _tenant is None:
        return _unread_deny()
    _agent = str(data.get("agent") or "").strip()
    if not _agent:
        return {"success": False, "error": "缺少 agent"}, 400
    with _UNREAD_LOCK:
        _store = _unread_load()
        _marks = _store.get(_tenant) or {}
        _hit = _marks.pop(_agent, None) is not None
        if _hit:
            _store[_tenant] = _marks
            _ok = _unread_save(_store)
        else:
            _ok = True
    return {"success": bool(_ok), "cleared": bool(_hit), "marks": _marks}


# ---------- 啟動 ----------
# ========== 短劇王操作台（影片女 jobs/短劇王，2026-09-06 新增） ==========
try:
    import sys as _sys
    _SD = "/home/ubuntu/.mok/agent/影片女/jobs/短劇王"
    if _SD not in _sys.path:
        _sys.path.insert(0, _SD)
    from shortdrama_bp import bp as _shortdrama_bp
    app.register_blueprint(_shortdrama_bp, url_prefix="/shortdrama")
    print("[shortdrama] 操作台已掛載 http://<host>/shortdrama/")
except Exception as _e:
    print("[shortdrama] 掛載失敗（不影響主服務）:", _e)

if __name__ == '__main__':
    # 同步初始配置到 mokagi
    reload_config(CURRENT_ENV_PATH)  # 確保 mokagi 配置與網頁一致
    threading.Thread(target=start_observer, daemon=True).start()
    # ===== 保丁載入（唯一一行核心修改；日後所有修改都在 .mok/frontends/mok_web/ 內） =====
    exec(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), 'mok_web', '保丁.py'), encoding='utf-8').read())
    socketio.run(app, host='127.0.0.1', port=5000, debug=False, allow_unsafe_werkzeug=True)
