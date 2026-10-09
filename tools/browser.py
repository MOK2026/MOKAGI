# ------------------------------------------------------------------------------------ #
# browser.py - 真實瀏覽器自動化工具
# 使用 Playwright + Chromium，透過 Xvfb 在 headless 主機上實現 headful 模式
# 模擬真人用戶的滑鼠和鍵盤操作，適合註冊/登入無 API 的網站
# 2026-07-21 衍 創建（2026-07-22 修正 Alpine/Docker 沙箱相容性）
# 2026-09-27 卓 進程鎖改為 per-profile + JSON 持有者資訊（pid/心跳），錯誤訊息顯示真正持有者
# 2026-09-27 卓 工具描述補上：侍女預設用自己的專屬 profile（可並行）；同 profile 並行時第二個約等 20 秒失敗
# 2026-09-28 衍 Xvfb 逐實例獨立：DISPLAY 改用 launch env 注入（不再改全域 os.environ）；編號改確定性掃描空閒；啟動前回收 PPID=1 孤兒
# 2026-09-28 衍 修正孤兒回收門檻 dn<10 → 只跳過 :0/:1（原 :2~:9 孤兒永遠清不掉）；實測已回收殘留 :2
# ------------------------------------------------------------------------------------ #

PLUGIN_INFO = {
    "command": "/browser",
    "icon": "🖥️",
    "handler": "handle_browser",
    "description": "主機真實瀏覽器操作：安裝 Chromium + Playwright、啟動瀏覽器（支援 profile=xxx 獨立設定檔，隔離登入狀態）、導航、點擊、輸入、截圖、滾動、按鍵、執行 JS、取得內容。模擬真人滑鼠鍵盤操作。",
    "intent_keywords": [
        ("打開瀏覽器", "/browser launch"),
        ("啟動瀏覽器", "/browser launch"),
        ("瀏覽器", "/browser"),
        ("截圖", "/browser screenshot"),
        ("點擊", "/browser click"),
        ("輸入文字", "/browser type"),
        ("關閉瀏覽器", "/browser close"),
    ],
    "naturalize_func": "naturalize_browser_result",
    "tool_schema": {
        "name": "browser",
        "description": "在主機操作真實 Chromium 瀏覽器，模擬真人滑鼠鍵盤操作。支援安裝瀏覽器環境、啟動、導航到 URL、點擊元素、輸入文字、截圖儲存、頁面滾動、鍵盤按鍵、等待元素/時間、執行 JavaScript、取得頁面文字內容、關閉瀏覽器等完整操作。適合需要模擬真人操作網站的場景（如註冊帳號、登入無 API 的網站、手動瀏覽）。瀏覽器以 headful 模式運行（透過 Xvfb 虛擬顯示器），支援持久化設定檔保存登入狀態。你（侍女）預設使用自己的專屬 Chromium profile（=侍女名），與其他侍女互不干擾、可並行；正常情況各侍女自己的 profile 不會撞，但同一侍女若同時開多個並行任務搶自己的 profile，第二個只等 20 秒就會失敗。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["install", "launch", "goto", "click", "type", "screenshot", "scroll", "wait", "press", "close", "status", "execute", "content"],
                    "description": "操作類型：install(安裝 Chromium+Playwright 環境), launch(啟動瀏覽器，預設使用持久化設定檔，可加 'fresh' 用全新設定檔，或 'profile=xxx' 用獨立設定檔隔離登入狀態), goto(導航到指定 URL), click(點擊元素，支援 CSS 選擇器或 'text=文字'), type(在輸入框填入文字，格式: '選擇器 文字'), screenshot(截圖存檔), scroll(滾動頁面，格式: '方向 像素'), wait(等待毫秒或等待選擇器出現), press(按下鍵盤按鍵), close(關閉瀏覽器), status(查看瀏覽器運行狀態), execute(執行 JavaScript 代碼), content(取得頁面文字內容，可選 'full' 取得完整內容)"
                },
                "args": {
                    "type": "string",
                    "description": "操作參數，依據 action 不同：launch: 預設使用持久化設定檔，可加 'fresh' 用全新設定檔、'profile=xxx' 指定獨立設定檔（如 profile=wa 給 wa_auto 專用，徹底隔離登入狀態）；goto: 目標網址（可省略 https:// 前綴）；click: CSS 選擇器或 'text=按鈕文字'；type: '選擇器 要輸入的文字'（中間空格分隔）；screenshot: 可選檔名（預設存 /tmp/，省略自動命名）；scroll: '方向 像素'（方向: down/up/top/bottom）；wait: 毫秒數 或 'selector 選擇器'；press: 按鍵名稱（Enter/Tab/Escape/Control+A 等）；execute: JavaScript 代碼；content: 可選 'full'；install/close/status: 無需參數"
                }
            },
            "required": ["action"]
        }
    }
}

# ------------------------------------------------------------------------------------ #
# 模組級狀態 —— 跨調用持久化瀏覽器實例
# 因為 mokagi 以模組方式加載工具，模組級變數在進程生命週期內持久存在
# ------------------------------------------------------------------------------------ #
_playwright = None       # Playwright 實例（鏡像：最近活躍實例，向後相容）
_browser = None          # Chromium 瀏覽器實例（鏡像）
_page = None             # 當前頁面（鏡像）
_context = None          # 瀏覽器上下文（鏡像）
_xvfb_proc = None        # Xvfb 進程（鏡像）
_display_num = None      # 虛擬顯示器編號（鏡像）
_persistent_dir = "/home/ubuntu/.mok/browser_profile2"  # 預設持久化設定檔目錄（相容舊版）
_profiles_root = "/home/ubuntu/.mok/browser_profiles"   # 各 profile 獨立設定檔根目錄
_active_profile = "default"                              # 最近活躍 profile 名稱
_default_profile = "default"                             # 由呼叫端侍女推導出的預設 profile（未指定 profile= 時使用）
_default_profile_agent = None                            # _default_profile 目前對應的侍女名稱

# ==================== 多實例並行支援（profile 各自獨立瀏覽器，互不關閉） ====================
# 每個 profile 擁有獨立的 Playwright/瀏覽器/頁面/Xvfb 顯示器，可同時並行運行。
# 例：ws客服用 profile=wa、b侍女用 profile=b，兩者同時存在、互不干擾。
_instances = {}          # profile -> {playwright, browser, context, page, xvfb_proc, display_num}
_last_profile = "default"  # 未指定 profile 時的操作目標（最近一次使用的 profile）


def _agent_profile_name(agent_config) -> str:
    """由呼叫端侍女推導預設 profile 名稱（統一交由 mok_profile 模組，單一真相來源）。
    沒有可用名稱時回退為 "default"（沿用舊版 browser_profile2）。"""
    return mok_profile.agent_profile_name(agent_config)


def _seed_profile_if_needed(profile_name: str, profile_dir: str) -> str:
    """若侍女專屬 profile 尚未建立，則從 browser_profile2 複製一份種子。
    複製邏輯統一交由 mok_profile 模組（單一真相來源）。"""
    if profile_name == "default":
        return ""
    try:
        if mok_profile.seed_profile(profile_dir):
            return f"🌱 已從 {_persistent_dir} 複製種子，建立侍女專屬 profile：{profile_dir}"
    except Exception as e:
        return f"⚠️ 複製種子 profile 失敗（改用空白 profile 啟動）：{e}"
    return ""


import asyncio
import os
import json
import shutil
import time
import glob
import random
import subprocess
import fcntl
import re
import signal
from typing import Optional, Dict, Union
import mok_profile as mok_profile  # 全系統統一的 profile 推導（單一真相來源）

# MOKAGI 沙箱：Chromium 存放在共享的 .mok/playwright-browsers
os.environ.setdefault("PLAYWRIGHT_BROWSERS_PATH", "/home/ubuntu/.mok/playwright-browsers")

# ------------------------------------------------------------------------------------ #
# 內部輔助函數
# ------------------------------------------------------------------------------------ #

def _is_alpine() -> bool:
    """偵測是否在 Alpine Linux 容器中"""
    try:
        with open("/etc/os-release", "r") as f:
            return "alpine" in f.read().lower()
    except:
        return os.path.exists("/etc/alpine-release")


def _get_pkg_install_cmd() -> list:
    """取得系統套件安裝指令（Alpine → apk, Ubuntu → apt-get）"""
    if _is_alpine():
        return ["apk", "add"]
    else:
        return ["apt-get", "install", "-y"]


async def _ensure_python() -> str:
    """確保 Python3 可用；若無則透過系統套件管理器安裝。返回 python3 路徑或 \"python3\""""
    if shutil.which("python3"):
        return "python3"
    if shutil.which("python"):
        return "python"

    # 需要安裝 Python
    install_cmd = _get_pkg_install_cmd()
    if _is_alpine():
        install_cmd += ["python3", "py3-pip"]
    else:
        install_cmd += ["python3", "python3-pip"]

    proc = await asyncio.create_subprocess_exec(
        *install_cmd,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        raise RuntimeError(f"無法安裝 Python3：{stderr.decode()[:500]}")
    return "python3"


def _check_playwright() -> bool:
    """檢查 playwright 是否已 pip 安裝"""
    try:
        import playwright
        return True
    except ImportError:
        return False


def _find_chromium_path() -> Optional[str]:
    """尋找 Playwright 安裝的 chromium 執行檔路徑"""
    patterns = [
        os.path.expanduser("~/.cache/ms-playwright/chromium-*/chrome-linux/chrome"),
        os.path.expanduser("~/.cache/ms-playwright/chromium_headless_shell-*/chrome-linux/headless_shell"),
        os.path.expanduser("~/.mok/playwright-browsers/chromium-*/chrome-linux/chrome"),
        os.path.expanduser("~/.mok/playwright-browsers/chromium_headless_shell-*/chrome-linux/headless_shell"),
    ]
    for pattern in patterns:
        matches = sorted(glob.glob(pattern), reverse=True)
        for m in matches:
            if os.path.isfile(m) and os.access(m, os.X_OK):
                return m

    # 系統 chromium
    for p in ["/usr/bin/chromium-browser", "/usr/bin/chromium", "/usr/bin/google-chrome-stable", "/usr/bin/google-chrome", "/snap/bin/chromium"]:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def _browser_env(display_num: int) -> dict:
    """為單一瀏覽器進程準備獨立環境：DISPLAY 指向它自己的 Xvfb。

    重點：不再改動全域 os.environ["DISPLAY"]——同一主進程多實例並行時，
    全域變數會被後啟動的實例覆蓋，導致瀏覽器掛到錯的顯示器搶滑鼠/焦點。
    """
    return {**os.environ, "DISPLAY": f":{display_num}"}


def _reap_orphan_xvfb() -> None:
    """回收孤兒 Xvfb：父行程已死（PPID=1）、且未被 _instances 追蹤者。

    桌面 :0/:1 屬系統服務，永久保留（只跳過 :0/:1；:2 以上孤兒可回收）。
    """
    tracked = {i.get("display_num") for i in _instances.values()}
    try:
        out = subprocess.run(["ps", "-eo", "pid=,ppid=,args="],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return
    for line in out.splitlines():
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        pid, ppid, cmd = parts
        if not cmd.startswith("Xvfb :"):
            continue
        try:
            dn = int(cmd.split()[1].lstrip(":"))
        except Exception:
            continue
        if dn < 2 or dn in tracked or ppid != "1":
            continue
        try:
            os.kill(int(pid), signal.SIGTERM)
        except Exception:
            pass


def _live_xvfb_displays() -> set:
    """回傳目前有活著 Xvfb 進程在服務的 display 編號（判斷空閒的權威來源）。"""
    live = set()
    try:
        out = subprocess.run(["ps", "-eo", "args="],
                             capture_output=True, text=True, timeout=10).stdout
    except Exception:
        return live
    for line in out.splitlines():
        line = line.strip()
        if not line.startswith("Xvfb :"):
            continue
        try:
            live.add(int(line.split()[1].lstrip(":")))
        except Exception:
            pass
    return live


def _check_xvfb() -> bool:
    """檢查 Xvfb 是否可用"""
    return shutil.which("Xvfb") is not None

def _start_xvfb(profile: str = "default") -> int:
    """啟動該 profile 專屬的 Xvfb 虛擬顯示器，返回 display number（多 profile 各自獨立）"""
    global _xvfb_proc, _display_num
    inst = _instances.get(profile)
    if inst and inst.get("display_num") is not None:
        return inst["display_num"]

    # 先回收上一輪行程重啟後遺留的孤兒 Xvfb，避免編號被幽靈佔住
    _reap_orphan_xvfb()

    # 第一個實例優先使用既有桌面顯示器 :1（即 noVNC 桌面面板所見），讓 Chrome 直接出現在桌面上
    if os.path.exists("/tmp/.X11-unix/X1") and not any(
        i.get("display_num") == 1 for i in _instances.values()
    ):
        display_num = 1
        xvfb_proc = None
        os.environ["DISPLAY"] = ":1"
    else:
        # 確定性分配：依序挑「真正空閒」的編號（2-99），不與其他實例重複，
        # 且 /tmp/.X11-unix/X{n} 與 /tmp/.X{n}-lock 皆不存在（避免撞殘留/他人）
        used = {i.get("display_num") for i in _instances.values()}
        live = _live_xvfb_displays()
        display_num = None
        for n in range(2, 100):
            if n in used or n in live:
                continue
            display_num = n
            # 此號無活著的 Xvfb，清掉殘留鎖檔/socket（Xvfb 遇殘留鎖可能拒啟，
            # 而 SIGTERM 常留下 /tmp/.X{n}-lock），讓新顯示器能順利啟動
            for _stale in (f"/tmp/.X{n}-lock", f"/tmp/.X11-unix/X{n}"):
                if os.path.exists(_stale):
                    try:
                        os.unlink(_stale)
                    except Exception:
                        pass
            break
        if display_num is None:
            raise RuntimeError("找不到可用的 Xvfb 顯示器編號（2-99 皆被佔用）")
        xvfb_proc = subprocess.Popen(
            ["Xvfb", f":{display_num}", "-screen", "0", "1920x1080x24", "-ac",
             "+extension", "RANDR"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # 不再改全域 DISPLAY；改由 _browser_env(display) 逐實例注入

    if profile not in _instances:
        _instances[profile] = {}
    _instances[profile]["xvfb_proc"] = xvfb_proc
    _instances[profile]["display_num"] = display_num
    if _active_profile == profile:
        _xvfb_proc = xvfb_proc
        _display_num = display_num
    return display_num


def _stop_xvfb(profile: str = "default"):
    """停止指定 profile 的 Xvfb（:1 為桌面服務所有，browser 不負責停止）"""
    global _xvfb_proc, _display_num
    inst = _instances.get(profile)
    if not inst:
        return
    xvfb_proc = inst.get("xvfb_proc")
    display_num = inst.get("display_num")
    if display_num == 1:
        inst["xvfb_proc"] = None
        inst["display_num"] = None
    elif xvfb_proc:
        try:
            xvfb_proc.terminate()
            xvfb_proc.wait(timeout=5)
        except:
            try:
                xvfb_proc.kill()
            except:
                pass
        inst["xvfb_proc"] = None
        inst["display_num"] = None
    if _active_profile == profile:
        _xvfb_proc = None
        _display_num = None


def _extract_profile(args):
    """從參數中提取 profile=xxx，返回 (clean_args, profile_or_None)。"""
    if isinstance(args, str) and "profile=" in args:
        prof = _default_profile
        rest = []
        for t in args.split():
            if t.startswith("profile="):
                prof = t.split("=", 1)[1].strip() or _default_profile
            else:
                rest.append(t)
        return " ".join(rest), prof
    return args, None


def _set_active(profile: str):
    """將指定 profile 設為活躍實例，並同步鏡像全域變數（向後相容）。"""
    global _active_profile, _last_profile, _page, _context, _browser, _playwright, _display_num, _xvfb_proc
    _active_profile = profile
    _last_profile = profile
    inst = _instances.get(profile, {})
    _page = inst.get("page")
    _context = inst.get("context")
    _browser = inst.get("browser")
    _playwright = inst.get("playwright")
    _display_num = inst.get("display_num")
    _xvfb_proc = inst.get("xvfb_proc")


async def _close_instance(profile: str):
    """關閉指定 profile 的瀏覽器實例（不影響其他 profile）。"""
    global _active_profile, _last_profile, _page, _context, _browser, _playwright
    inst = _instances.pop(profile, None)
    if not inst:
        return []
    errors = []
    for obj, name in [("page", "page"), ("context", "context"), ("browser", "browser"), ("playwright", "playwright")]:
        o = inst.get(obj)
        if o:
            try:
                if hasattr(o, "close"):
                    await o.close()
                elif hasattr(o, "stop"):
                    await o.stop()
            except Exception as e:
                errors.append(f"{name}: {e}")
    _stop_xvfb(profile)
    if _active_profile == profile:
        _active_profile = _default_profile
        _last_profile = _default_profile
        _page = None
        _context = None
        _browser = None
        _playwright = None
    return errors


def naturalize_browser_result(user_text: str = "", raw_result: str = "", ollama_api: str = "", model_name: str = "", temp_msg=None, context=None) -> str:
    """將瀏覽器操作的 JSON 結果轉為自然語言"""
    try:
        data = json.loads(raw_result) if raw_result else {}
    except (json.JSONDecodeError, TypeError):
        return raw_result if raw_result else "（無回應）"

    if not isinstance(data, dict):
        return raw_result if raw_result else "（無回應）"

    if not data.get("success", False):
        return f"❌ 瀏覽器操作失敗：{data.get('error', '未知錯誤')}"

    action = data.get("action", "")

    if action == "install":
        return f"✅ 瀏覽器環境安裝完成！\n{data.get('message', '')}"

    elif action == "launch":
        return f"✅ 瀏覽器已啟動！（顯示器 :{data.get('display', '?')}）\n模式：{'持久化' if data.get('persistent') else '一般'}"

    elif action == "goto":
        return f"✅ 已導航至：{data.get('url', '')}\n📄 頁面標題：{data.get('title', '無標題')}"

    elif action == "click":
        return f"✅ 已點擊「{data.get('target', '')}」\n📄 當前頁面：{data.get('title', '')}"

    elif action == "type":
        return f"✅ 已在「{data.get('selector', '')}」中輸入 {data.get('text_length', 0)} 個字元"

    elif action == "screenshot":
        return f"📸 截圖已儲存：{data.get('path', '')}"

    elif action == "scroll":
        return f"✅ 已向{data.get('direction', '')}滾動 {data.get('pixels', 0)}px"

    elif action == "wait":
        return f"✅ 等待完成：{data.get('waited', '')}"

    elif action == "press":
        return f"✅ 已按下「{data.get('key', '')}」"

    elif action == "close":
        return f"✅ 瀏覽器已關閉"

    elif action == "status":
        if data.get("running"):
            return f"🖥️ 瀏覽器運行中\n📍 當前網址：{data.get('url', '')}\n📄 頁面標題：{data.get('title', '')}"
        else:
            return "🖥️ 瀏覽器目前未啟動"

    elif action == "execute":
        r = data.get("result", "")
        return f"✅ JavaScript 執行完成\n📋 結果：{r[:300]}{'...(截斷)' if len(r) > 300 else ''}"

    elif action == "content":
        c = data.get("content", "")
        return f"📄 頁面文字內容：\n{c[:800]}{'...(截斷)' if len(c) > 800 else ''}"

    return f"✅ 操作完成：{data.get('message', '')}"


# ------------------------------------------------------------------------------------ #
# ------------------------------------------------------------------------------------ #
# 瀏覽器進程鎖（per-profile）：
#   - 鎖的粒度＝profile：不同侍女（各自專屬 profile）可並行，不再全機排隊
#   - 同一 profile 仍序列化，避免兩個呼叫同時操作同一個 Chromium
#   - 鎖檔以 JSON 記錄真正的持有者（agent/pid/chat/profile/ts=心跳時間）
#   - 成功上鎖即覆寫鎖檔 → 自動清掉殘留的舊持有者；錯誤訊息顯示真正的持有者
# ------------------------------------------------------------------------------------ #

BROWSER_LOCK_WAIT = 10       # 等待秒數
BROWSER_LOCK_MAX_TRIES = 3   # 最多嘗試次數
BROWSER_LOCK_STALE = 180     # 心跳超過此秒數視為疑似逾時（僅用於訊息標註）


def _browser_lock_file(profile) -> str:
    """每個 profile 一條鎖檔：不同侍女（不同 profile）可並行。"""
    name = re.sub(r"[^\w\u4e00-\u9fff.-]", "_", str(profile or "default")).strip("._") or "default"
    return os.path.join("/tmp", f"mok_browser_{name}.lock")


def _pid_alive(pid) -> bool:
    """檢查 pid 是否仍存活（用於辨識已結束的殭屍鎖）。"""
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return False
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _read_lock_info(lock_file: str) -> dict:
    """讀取鎖檔中的持有者資訊；JSON 為主，舊格式（裸字串）向後相容。"""
    try:
        with open(lock_file, "r", encoding="utf-8", errors="ignore") as f:
            content = f.read().strip()
        if not content:
            return {}
        try:
            info = json.loads(content)
            return info if isinstance(info, dict) else {"agent": content}
        except Exception:
            return {"agent": content}
    except Exception:
        return {}


def _try_lock_browser(lock_file: str, holder_info: dict) -> Optional[int]:
    """嘗試非阻塞獲取指定 profile 的鎖，成功返回 fd，失敗返回 None。

    只有在真正拿到 flock 之後才寫入持有者資訊，因此不會有「沒搶到鎖卻蓋掉持有者名字」的情況；
    寫入前先 ftruncate，等同自動清掉殘留的舊持有者內容。
    """
    try:
        fd = os.open(lock_file, os.O_CREAT | os.O_RDWR, 0o644)
    except Exception:
        return None
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            os.close(fd)
            return None
        # 寫入「真正的」持有者（含心跳時間戳），方便其他侍女辨識
        try:
            payload = dict(holder_info)
            payload["ts"] = time.time()
            os.ftruncate(fd, 0)
            os.lseek(fd, 0, os.SEEK_SET)
            os.write(fd, json.dumps(payload, ensure_ascii=False).encode("utf-8"))
        except Exception:
            pass
        return fd
    except Exception:
        try:
            os.close(fd)
        except Exception:
            pass
        return None


def _get_lock_holder(lock_file: str) -> str:
    """讀取並格式化真正的鎖持有者資訊（含存活檢查與持有時間）。"""
    info = _read_lock_info(lock_file)
    if not info:
        return "未知進程"
    agent = info.get("agent") or "?"
    pid = info.get("pid")
    prof = info.get("profile")
    chat = info.get("chat")
    ts = info.get("ts")
    parts = [f"agent={agent}"]
    if pid:
        parts.append(f"pid={pid}" + ("" if _pid_alive(pid) else "(已結束)"))
    if prof:
        parts.append(f"profile={prof}")
    if chat:
        parts.append(f"chat={chat}")
    if ts:
        try:
            held = int(time.time() - float(ts))
            parts.append(f"已持有{held}秒" + ("(疑似逾時)" if held > BROWSER_LOCK_STALE else ""))
        except Exception:
            pass
    return " ".join(parts)


def _release_browser_lock(fd: int, lock_file: str = None) -> None:
    """釋放瀏覽器鎖並關閉 fd。"""
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    except Exception:
        pass
    try:
        os.close(fd)
    except Exception:
        pass


async def _acquire_browser_lock(lock_file: str, holder_info: dict) -> Optional[int]:
    """
    獲取指定 profile 的瀏覽器鎖：先試一次，被佔用則等 10 秒再試，最多試 3 次。
    成功返回鎖 fd；3 次都失敗返回 None。
    """
    for attempt in range(BROWSER_LOCK_MAX_TRIES):
        fd = _try_lock_browser(lock_file, holder_info)
        if fd is not None:
            return fd
        if attempt < BROWSER_LOCK_MAX_TRIES - 1:
            await asyncio.sleep(BROWSER_LOCK_WAIT)
    return None


# ------------------------------------------------------------------------------------ #
# 核心 handler
# ------------------------------------------------------------------------------------ #

async def handle_browser(args: Union[str, dict], chat_id: str = None, agent_config: Optional[Dict] = None) -> str:
    """
    處理 /browser 命令或 LLM 工具調用。
    args 可以是字串（"/browser <action> [args]"）或字典（{"action": "...", "args": "..."}）
    """
    # --- 解析參數 ---
    if isinstance(args, str):
        args = args.strip()
        if not args:
            return json.dumps({
                "success": False,
                "error": (
                    "請指定操作類型。支援以下動作：\n"
                    "  install    - 安裝 Chromium + Playwright 環境\n"
                    "  launch     - 啟動瀏覽器（預設使用持久化設定檔；可加 'fresh' 用全新設定檔，或 'profile=xxx' 用獨立設定檔隔離登入狀態）\n"
                    "  goto       - 導航到 URL\n"
                    "  click      - 點擊元素\n"
                    "  type       - 在輸入框輸入文字\n"
                    "  screenshot - 截圖\n"
                    "  scroll     - 滾動頁面\n"
                    "  wait       - 等待（毫秒或選擇器）\n"
                    "  press      - 按下鍵盤按鍵\n"
                    "  execute    - 執行 JavaScript\n"
                    "  content    - 取得頁面文字\n"
                    "  status     - 查看瀏覽器狀態\n"
                    "  close      - 關閉瀏覽器\n"
                    "用法：/browser <action> [參數]"
                )
            }, ensure_ascii=False)

        parts = args.split(maxsplit=1)
        action = parts[0].lower()
        action_args = parts[1] if len(parts) > 1 else ""
    elif isinstance(args, dict):
        action = args.get("action", "").lower()
        action_args = args.get("args", "")
    else:
        return json.dumps({"success": False, "error": "無效的參數格式"}, ensure_ascii=False)

    # --- 進程鎖（per-profile）：不同 profile 可並行；同一 profile 才需排隊 ---
    agent_name = "?"
    if agent_config and isinstance(agent_config, dict):
        agent_name = agent_config.get("MOK_AGENT_NAME") or agent_config.get("name") or "?"

    # --- 依呼叫端侍女決定「預設 profile」；切換侍女時同步重置 _last_profile ---
    global _default_profile, _default_profile_agent, _last_profile
    _default_profile = _agent_profile_name(agent_config)
    if _default_profile_agent != agent_name:
        _default_profile_agent = agent_name
        _last_profile = _default_profile

    # 鎖的粒度＝本次實際操作的 profile（顯式 profile= 優先，否則用侍女預設）
    try:
        _, _lock_profile = _extract_profile(action_args or "")
    except Exception:
        _lock_profile = None
    _lock_profile = _lock_profile or _default_profile
    lock_file = _browser_lock_file(_lock_profile)

    holder_info = {
        "agent": agent_name,
        "pid": os.getpid(),
        "chat": str(chat_id) if chat_id else "?",
        "profile": _lock_profile,
    }

    lock_fd = await _acquire_browser_lock(lock_file, holder_info)
    if lock_fd is None:
        return json.dumps({
            "success": False,
            "error": f"瀏覽器（profile={_lock_profile}）正在使用中：{_get_lock_holder(lock_file)}，請等對方用完再試。",
            "action": action,
            "locked": True
        }, ensure_ascii=False)

    try:
        # --- 路由到各處理器 ---
        try:
            if action == "install":
                return await _handle_install()
            elif action == "launch":
                return await _handle_launch(action_args)
            elif action == "goto":
                return await _handle_goto(action_args)
            elif action == "click":
                return await _handle_click(action_args)
            elif action == "type":
                return await _handle_type(action_args)
            elif action == "screenshot":
                return await _handle_screenshot(action_args)
            elif action == "scroll":
                return await _handle_scroll(action_args)
            elif action == "wait":
                return await _handle_wait(action_args)
            elif action == "press":
                return await _handle_press(action_args)
            elif action == "close":
                return await _handle_close(action_args)
            elif action == "status":
                return await _handle_status(action_args)
            elif action == "execute":
                return await _handle_execute(action_args)
            elif action == "content":
                return await _handle_content(action_args)
            else:
                return json.dumps({
                    "success": False,
                    "error": f"未知操作「{action}」。支援：install, launch, goto, click, type, screenshot, scroll, wait, press, close, status, execute, content"
                }, ensure_ascii=False)
        except Exception as e:
            import traceback
            tb = traceback.format_exc()
            return json.dumps({
                "success": False,
                "error": f"{type(e).__name__}: {e}",
                "action": action,
                "traceback": tb[-500:]
            }, ensure_ascii=False)
    finally:
        _release_browser_lock(lock_fd)


# ------------------------------------------------------------------------------------ #
# 各操作處理器
# ------------------------------------------------------------------------------------ #

async def _handle_install() -> str:
    """安裝 Playwright、Chromium、Xvfb 及系統依賴"""
    results = []

    # 0. 確保 Python 環境可用（Alpine 容器可能無 Python）
    try:
        python_bin = await _ensure_python()
        results.append(f"✅ Python 環境就緒 ({python_bin})")
    except RuntimeError as e:
        return json.dumps({
            "success": False,
            "error": f"無法準備 Python 環境：{e}",
            "action": "install"
        }, ensure_ascii=False)

    pip_cmd = [python_bin, "-m", "pip"]

    # 1. 安裝 playwright Python 庫
    if not _check_playwright():
        results.append("📦 正在安裝 playwright Python 庫...")
        proc = await asyncio.create_subprocess_exec(
            *pip_cmd, "install", "playwright",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            return json.dumps({
                "success": False,
                "error": f"playwright 安裝失敗：{stderr.decode()[:500]}",
                "action": "install"
            }, ensure_ascii=False)
        results.append("✅ playwright 已安裝")
    else:
        results.append("✅ playwright 已存在")

    # 2. 安裝 Chromium 瀏覽器
    results.append("📦 正在安裝 Chromium 瀏覽器（可能需要 2-5 分鐘，請耐心等候）...")
    proc = await asyncio.create_subprocess_exec(
        python_bin, "-m", "playwright", "install", "chromium",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode != 0:
        # 嘗試 npx 方式（先確保 Node.js 可用）
        if shutil.which("npx"):
            results.append("⚠️ python -m playwright 失敗，嘗試 npx 方式...")
            proc2 = await asyncio.create_subprocess_exec(
                "npx", "playwright", "install", "chromium",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout2, stderr2 = await proc2.communicate()
            if proc2.returncode != 0:
                return json.dumps({
                    "success": False,
                    "error": f"Chromium 安裝失敗。\npython stderr: {stderr.decode()[:300]}\nnpx stderr: {stderr2.decode()[:300]}",
                    "action": "install"
                }, ensure_ascii=False)
        else:
            return json.dumps({
                "success": False,
                "error": f"Chromium 安裝失敗（python 方式）。\nstderr: {stderr.decode()[:500]}\n\n💡 Alpine 容器請先執行：apk add nodejs npm",
                "action": "install"
            }, ensure_ascii=False)
    results.append("✅ Chromium 已安裝")

    # 3. 檢查/安裝 Xvfb
    if not _check_xvfb():
        results.append("📦 正在安裝 Xvfb 虛擬顯示器...")
        proc = await asyncio.create_subprocess_exec(
            *_get_pkg_install_cmd(), "xvfb",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
        if proc.returncode != 0:
            pkg_hint = "apk add xvfb" if _is_alpine() else "sudo apt-get install -y xvfb"
            results.append(f"⚠️ Xvfb 自動安裝失敗，請手動執行：{pkg_hint}\n詳情：{stderr.decode()[:200]}")
        else:
            results.append("✅ Xvfb 已安裝")
    else:
        results.append("✅ Xvfb 已存在")

    # 4. 安裝系統依賴庫（Playwright 所需）
    results.append("📦 正在安裝 Chromium 系統依賴庫...")
    proc = await asyncio.create_subprocess_exec(
        python_bin, "-m", "playwright", "install-deps", "chromium",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, stderr = await proc.communicate()
    if proc.returncode == 0:
        results.append("✅ 系統依賴已安裝")
    else:
        pkg_hint = "sudo python3 -m playwright install-deps chromium" if not _is_alpine() else "apk add <缺依賴>（playwright install-deps 不支援 Alpine，請手動安裝缺失的 .so 庫）"
        results.append(f"⚠️ 系統依賴安裝可能不完整（非致命），可手動執行：{pkg_hint}")

    # 5. 驗證
    chromium_path = _find_chromium_path()
    if chromium_path:
        results.append(f"✅ 驗證成功：Chromium @ {chromium_path}")
    else:
        results.append("⚠️ 找不到 Chromium 執行檔，但安裝程序已完成，請嘗試啟動")

    return json.dumps({
        "success": True,
        "action": "install",
        "message": "\n".join(results)
    }, ensure_ascii=False)


async def _handle_launch(args: str) -> str:
    """啟動 Chromium 瀏覽器（支援 profile=xxx 指定獨立設定檔；多 profile 可並行共存，互不關閉）"""
    # --- 解析 profile 參數：/browser launch profile=wa ---
    # 未指定 profile= 時，採用呼叫端侍女的專屬 profile（不存在會自動種子複製）
    profile_name = _default_profile
    clean_args = (args or "").strip()
    if "profile=" in clean_args:
        for token in clean_args.split():
            if token.startswith("profile="):
                profile_name = token.split("=", 1)[1].strip() or _default_profile
        # 從參數中移除 profile=xxx，避免影響 use_persistent 判斷
        clean_args = " ".join(t for t in clean_args.split() if not t.startswith("profile="))
    # 統一推導：交由 mok_profile 模組（單一真相來源）；種子複製由下方 _seed_profile_if_needed 處理
    profile_dir = mok_profile.resolve_profile_dir(explicit=profile_name, seed=False)

    # 目標 profile 已存在且頁面活著 → 直接切換為活躍實例；絕不關閉其他 profile
    inst = _instances.get(profile_name)
    if inst and inst.get("page") is not None:
        try:
            await inst["page"].title()
            _set_active(profile_name)
            return json.dumps({
                "success": True,
                "action": "launch",
                "message": f"瀏覽器已在運行中（profile: {profile_name}），無需重複啟動",
                "display": inst.get("display_num"),
                "persistent": os.path.isdir(profile_dir),
                "profile": profile_name
            }, ensure_ascii=False)
        except:
            # 頁面已失效 → 只關閉該 profile 實例，重新啟動
            await _close_instance(profile_name)

    if not _check_playwright():
        return json.dumps({
            "success": False,
            "error": "⚠️ Playwright 尚未安裝，請先執行 /browser install",
            "action": "launch"
        }, ensure_ascii=False)

    from playwright.async_api import async_playwright

    # 啟動該 profile 專屬 Xvfb 顯示器（多實例各自獨立，互不搶佔）
    try:
        display = _start_xvfb(profile_name)
    except RuntimeError as e:
        return json.dumps({
            "success": False,
            "error": f"⚠️ Xvfb 啟動失敗：{e}",
            "action": "launch"
        }, ensure_ascii=False)

    use_persistent = "fresh" not in clean_args
    chromium_path = _find_chromium_path()
    pw = await async_playwright().start()

    seed_msg = ""
    try:
        if use_persistent:
            seed_msg = _seed_profile_if_needed(profile_name, profile_dir)
            os.makedirs(profile_dir, exist_ok=True)
            context = await asyncio.wait_for(
                pw.chromium.launch_persistent_context(
                    profile_dir,
                    headless=False,
                    viewport={"width": 1280, "height": 800},
                    user_agent=(
                        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                        "AppleWebKit/537.36 (KHTML, like Gecko) "
                        "Chrome/126.0.0.0 Safari/537.36"
                    ),
                    args=[
                        "--no-sandbox",
                        "--disable-setuid-sandbox",
                        "--disable-blink-features=AutomationControlled",
                        "--enable-unsafe-swiftshader",
                        "--ignore-gpu-blocklist",
                        "--enable-webgl",
                        "--use-gl=angle",
                        "--use-angle=swiftshader",
                        "--disable-dev-shm-usage",
                    ],
                    executable_path=chromium_path,
                    env=_browser_env(display),
                ),
                timeout=60
            )
            browser = None
            page = context.pages[0] if context.pages else await context.new_page()
        else:
            launch_opts = {
                "headless": False,
                "env": _browser_env(display),
                "args": [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ],
            }
            if chromium_path:
                launch_opts["executable_path"] = chromium_path

            browser = await asyncio.wait_for(
                pw.chromium.launch(**launch_opts),
                timeout=60
            )
            context = await browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
            )
            page = await context.new_page()
    except asyncio.TimeoutError:
        return json.dumps({
            "success": False,
            "error": "⚠️ 啟動 Chromium 超時（60秒）。請檢查系統資源或手動重試。",
            "action": "launch"
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"⚠️ 啟動 Chromium 失敗：{e}",
            "action": "launch"
        }, ensure_ascii=False)

    # 註冊到實例表（覆蓋舊實例殘留），並設為活躍
    _instances[profile_name] = {
        "playwright": pw,
        "browser": browser,
        "context": context,
        "page": page,
        "xvfb_proc": _instances.get(profile_name, {}).get("xvfb_proc"),
        "display_num": _instances.get(profile_name, {}).get("display_num"),
    }
    _set_active(profile_name)

    return json.dumps({
        "success": True,
        "action": "launch",
        "message": (f"瀏覽器已啟動（profile: {profile_name}）" + (f"\n{seed_msg}" if seed_msg else "")),
        "display": display,
        "persistent": use_persistent,
        "profile": profile_name,
        "active_profiles": list(_instances.keys())
    }, ensure_ascii=False)


async def _ensure_page(profile: str = None):
    """確保指定 profile 的瀏覽器和頁面已就緒，否則拋出錯誤"""
    target = profile or _last_profile or _default_profile or _active_profile
    inst = _instances.get(target)
    if not inst or inst.get("page") is None:
        raise RuntimeError(f"瀏覽器尚未啟動（profile: {target}），請先執行 /browser launch profile={target}")
    page = inst["page"]
    # 檢查頁面是否還活著
    try:
        await page.evaluate("1")
    except:
        raise RuntimeError(f"瀏覽器頁面已失效（profile: {target}），請重新執行 /browser launch profile={target}")
    _set_active(target)
    return page

async def _handle_goto(args: str) -> str:
    """導航到指定網址（支援 profile=xxx）"""
    clean_args, prof = _extract_profile(args)
    page = await _ensure_page(prof)
    url = clean_args.strip()
    if not url:
        return json.dumps({"success": False, "error": "請提供網址，例如：/browser goto https://example.com"}, ensure_ascii=False)
    if not url.startswith("http"):
        url = "https://" + url

    await page.goto(url, wait_until="domcontentloaded", timeout=30000)
    title = await page.title()
    return json.dumps({
        "success": True,
        "action": "goto",
        "url": url,
        "title": title,
        "profile": _active_profile
    }, ensure_ascii=False)


async def _handle_click(args: str) -> str:
    """點擊頁面元素（支援 profile=xxx）"""
    clean_args, prof = _extract_profile(args)
    page = await _ensure_page(prof)
    selector = clean_args.strip()
    if not selector:
        return json.dumps({"success": False, "error": "請提供點擊目標（CSS 選擇器或 text=文字）"}, ensure_ascii=False)

    # 支援座標點擊：格式 "x,y" 或 "x=100,y=200"
    import re as _re
    _m = _re.match(r"^\s*(\d+)\s*[,xX]\s*(\d+)\s*$", selector)
    if _m:
        cx, cy = int(_m.group(1)), int(_m.group(2))
        await page.mouse.move(cx, cy, steps=5)
        await page.mouse.click(cx, cy)
        title = await page.title()
        return json.dumps({
            "success": True,
            "action": "click",
            "target": f"座標({cx},{cy})",
            "title": title,
            "profile": _active_profile
        }, ensure_ascii=False)

    # 如果沒前綴，預設用 text= 匹配
    if not any(selector.startswith(p) for p in ["text=", "css=", "xpath=", "#", ".", "[", "button", "a", "input", "div", "span", "li", "p", "h1", "h2", "h3", "h4"]):
        selector = f"text={selector}"

    await page.click(selector, timeout=10000)
    title = await page.title()
    return json.dumps({
        "success": True,
        "action": "click",
        "target": selector,
        "title": title,
        "profile": _active_profile
    }, ensure_ascii=False)

async def _handle_type(args: str) -> str:
    """在輸入框中輸入文字（支援 profile=xxx）"""
    clean_args, prof = _extract_profile(args)
    page = await _ensure_page(prof)
    parts = clean_args.strip().split(maxsplit=1)
    if len(parts) < 2:
        return json.dumps({"success": False, "error": "格式：/browser type <選擇器> <文字內容>\n例如：/browser type #email user@example.com"}, ensure_ascii=False)

    selector, text = parts[0], parts[1]
    await page.fill(selector, text)
    return json.dumps({
        "success": True,
        "action": "type",
        "selector": selector,
        "text_length": len(text),
        "profile": _active_profile
    }, ensure_ascii=False)


async def _handle_screenshot(args: str) -> str:
    """截圖並儲存（支援 profile=xxx）"""
    clean_args, prof = _extract_profile(args)
    page = await _ensure_page(prof)
    filename = clean_args.strip() if clean_args.strip() else f"browser_screenshot_{int(time.time())}.png"
    if not filename.startswith("/"):
        filename = f"/tmp/{filename}"

    await page.screenshot(path=filename, full_page=False)
    return json.dumps({
        "success": True,
        "action": "screenshot",
        "path": filename,
        "profile": _active_profile
    }, ensure_ascii=False)

async def _handle_scroll(args: str) -> str:
    """滾動頁面（支援 profile=xxx）"""
    clean_args, prof = _extract_profile(args)
    page = await _ensure_page(prof)
    parts = clean_args.strip().split()
    direction = parts[0].lower() if parts else "down"
    pixels = int(parts[1]) if len(parts) > 1 else 300

    scroll_js = {
        "down":    f"window.scrollBy(0, {pixels})",
        "up":      f"window.scrollBy(0, -{pixels})",
        "bottom":  "window.scrollTo(0, document.body.scrollHeight)",
        "top":     "window.scrollTo(0, 0)",
    }
    js = scroll_js.get(direction, f"window.scrollBy(0, {pixels})")
    await page.evaluate(js)

    return json.dumps({
        "success": True,
        "action": "scroll",
        "direction": direction,
        "pixels": pixels,
        "profile": _active_profile
    }, ensure_ascii=False)

async def _handle_wait(args: str) -> str:
    """等待（支援 profile=xxx）"""
    clean_args, prof = _extract_profile(args)
    page = await _ensure_page(prof)
    arg = clean_args.strip()

    if not arg:
        await asyncio.sleep(1)
        return json.dumps({"success": True, "action": "wait", "waited": "1s"}, ensure_ascii=False)

    # selector <選擇器> 格式
    if arg.startswith("selector "):
        sel = arg[9:].strip()
        if sel:
            await page.wait_for_selector(sel, timeout=15000)
            return json.dumps({"success": True, "action": "wait", "waited": f"selector: {sel}", "profile": _active_profile}, ensure_ascii=False)

    # 嘗試解析為毫秒
    try:
        ms = int(arg)
        await asyncio.sleep(ms / 1000.0)
        return json.dumps({"success": True, "action": "wait", "waited": f"{ms}ms", "profile": _active_profile}, ensure_ascii=False)
    except ValueError:
        # 當作選擇器
        await page.wait_for_selector(arg, timeout=15000)
        return json.dumps({"success": True, "action": "wait", "waited": f"selector: {arg}", "profile": _active_profile}, ensure_ascii=False)

async def _handle_press(args: str) -> str:
    """按下鍵盤按鍵（支援 profile=xxx）"""
    clean_args, prof = _extract_profile(args)
    page = await _ensure_page(prof)
    key = clean_args.strip()
    if not key:
        return json.dumps({"success": False, "error": "請提供按鍵名稱，例如：Enter, Tab, Escape, ArrowDown, Control+A"}, ensure_ascii=False)

    await page.keyboard.press(key)
    return json.dumps({"success": True, "action": "press", "key": key, "profile": _active_profile}, ensure_ascii=False)


async def _handle_close(args: str = "") -> str:
    """關閉瀏覽器。close profile=xxx 只關閉指定 profile；close 關閉全部實例"""
    clean_args, prof = _extract_profile(args)
    if prof:
        # 只關閉指定 profile，其他 profile 不受影響
        if prof in _instances:
            errors = await _close_instance(prof)
            return json.dumps({
                "success": True,
                "action": "close",
                "message": f"瀏覽器已關閉（profile: {prof}）",
                "profile": prof,
                "remaining": list(_instances.keys())
            }, ensure_ascii=False)
        return json.dumps({
            "success": True,
            "action": "close",
            "message": f"profile「{prof}」未在運行",
            "profile": prof
        }, ensure_ascii=False)

    # 關閉全部實例
    closed = list(_instances.keys())
    total_errors = []
    for p in list(_instances.keys()):
        errs = await _close_instance(p)
        if errs:
            total_errors.extend(errs)
    return json.dumps({
        "success": True,
        "action": "close",
        "message": f"已關閉 {len(closed)} 個瀏覽器實例" + (f"（清理時有 {len(total_errors)} 個非致命錯誤）" if total_errors else ""),
        "closed": closed
    }, ensure_ascii=False)


async def _handle_status(args: str = "") -> str:
    """查詢瀏覽器運行狀態（列出所有 profile 實例）"""
    clean_args, prof = _extract_profile(args)
    if not _instances:
        return json.dumps({"success": True, "action": "status", "running": False, "instances": []}, ensure_ascii=False)

    instances = []
    target = prof or _last_profile or _active_profile
    for p, inst in _instances.items():
        page = inst.get("page")
        info = {
            "profile": p,
            "display": inst.get("display_num"),
            "active": (p == _active_profile)
        }
        if page is not None:
            try:
                info["url"] = page.url
                info["title"] = await page.title()
            except Exception as e:
                info["url"] = "（無法取得）"
                info["title"] = "（無法取得）"
                info["note"] = str(e)
        instances.append(info)

    return json.dumps({
        "success": True,
        "action": "status",
        "running": True,
        "active_profile": _active_profile,
        "instances": instances
    }, ensure_ascii=False)

async def _handle_execute(args: str) -> str:
    """在頁面中執行 JavaScript（支援 profile=xxx）"""
    clean_args, prof = _extract_profile(args)
    page = await _ensure_page(prof)
    js_code = clean_args.strip()
    if not js_code:
        return json.dumps({"success": False, "error": "請提供 JavaScript 代碼"}, ensure_ascii=False)

    result = await page.evaluate(js_code)
    return json.dumps({
        "success": True,
        "action": "execute",
        "result": str(result)[:2000],
        "profile": _active_profile
    }, ensure_ascii=False)


async def _handle_content(args: str) -> str:
    """取得頁面文字內容（支援 profile=xxx）"""
    clean_args, prof = _extract_profile(args)
    page = await _ensure_page(prof)
    full = clean_args.strip().lower() == "full"

    try:
        if full:
            content = await page.evaluate("document.body?.innerText || ''")
        else:
            content = await page.text_content("body")
        content = content.strip() if content else "(空白頁面)"
    except Exception as e:
        content = f"(取得失敗：{e})"

    return json.dumps({
        "success": True,
        "action": "content",
        "content": content[:5000],
        "profile": _active_profile
    }, ensure_ascii=False)
