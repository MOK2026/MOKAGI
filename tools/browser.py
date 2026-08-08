# ------------------------------------------------------------------------------------ #
# browser.py - 真實瀏覽器自動化工具
# 使用 Playwright + Chromium，透過 Xvfb 在 headless 主機上實現 headful 模式
# 模擬真人用戶的滑鼠和鍵盤操作，適合註冊/登入無 API 的網站
# 2026-07-21 衍 創建（2026-07-22 修正 Alpine/Docker 沙箱相容性）
# ------------------------------------------------------------------------------------ #

PLUGIN_INFO = {
    "command": "/browser",
    "icon": "🖥️",
    "handler": "handle_browser",
    "description": "主機真實瀏覽器操作：安裝 Chromium + Playwright、啟動瀏覽器、導航、點擊、輸入、截圖、滾動、按鍵、執行 JS、取得內容。模擬真人滑鼠鍵盤操作。",
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
        "description": "在主機操作真實 Chromium 瀏覽器，模擬真人滑鼠鍵盤操作。支援安裝瀏覽器環境、啟動、導航到 URL、點擊元素、輸入文字、截圖儲存、頁面滾動、鍵盤按鍵、等待元素/時間、執行 JavaScript、取得頁面文字內容、關閉瀏覽器等完整操作。適合需要模擬真人操作網站的場景（如註冊帳號、登入無 API 的網站、手動瀏覽）。瀏覽器以 headful 模式運行（透過 Xvfb 虛擬顯示器），支援持久化設定檔保存登入狀態。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["install", "launch", "goto", "click", "type", "screenshot", "scroll", "wait", "press", "close", "status", "execute", "content"],
                    "description": "操作類型：install(安裝 Chromium+Playwright 環境), launch(啟動瀏覽器，可選 'persistent' 使用持久化設定檔), goto(導航到指定 URL), click(點擊元素，支援 CSS 選擇器或 'text=文字'), type(在輸入框填入文字，格式: '選擇器 文字'), screenshot(截圖存檔), scroll(滾動頁面，格式: '方向 像素'), wait(等待毫秒或等待選擇器出現), press(按下鍵盤按鍵), close(關閉瀏覽器), status(查看瀏覽器運行狀態), execute(執行 JavaScript 代碼), content(取得頁面文字內容，可選 'full' 取得完整內容)"
                },
                "args": {
                    "type": "string",
                    "description": "操作參數，依據 action 不同：launch: 可選 'persistent' 啟用持久化設定檔；goto: 完整 URL；click: CSS 選擇器或 'text=按鈕文字'；type: '選擇器 要輸入的文字'（中間空格分隔）；screenshot: 可選檔名；scroll: '方向 像素'（方向: down/up/top/bottom）；wait: 毫秒數 或 'selector 選擇器'；press: 按鍵名稱（Enter/Tab/Escape/Control+A 等）；execute: JavaScript 代碼；content: 可選 'full'；install/close/status: 無需參數"
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
_playwright = None       # Playwright 實例
_browser = None          # Chromium 瀏覽器實例
_page = None             # 當前頁面
_context = None          # 瀏覽器上下文
_xvfb_proc = None        # Xvfb 進程
_display_num = None      # 虛擬顯示器編號
_persistent_dir = "/home/ubuntu/.mok/browser_profile"  # 持久化設定檔目錄

import asyncio
import os
import json
import shutil
import time
import glob
import random
import subprocess
from typing import Optional, Dict, Union

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


def _check_xvfb() -> bool:
    """檢查 Xvfb 是否可用"""
    return shutil.which("Xvfb") is not None


def _start_xvfb() -> int:
    """啟動 Xvfb 虛擬顯示器，返回 display number"""
    global _xvfb_proc, _display_num

    if _xvfb_proc is not None:
        return _display_num

    _display_num = random.randint(10, 99)

    # 確保沒有殘留的鎖檔
    lockfile = f"/tmp/.X{_display_num}-lock"
    if os.path.exists(lockfile):
        try:
            os.remove(lockfile)
        except:
            _display_num = random.randint(10, 99)

    try:
        _xvfb_proc = subprocess.Popen(
            ["Xvfb", f":{_display_num}", "-screen", "0", "1920x1080x24", "-ac",
             "+extension", "RANDR"],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        # 等 Xvfb 就緒
        time.sleep(0.5)
        os.environ["DISPLAY"] = f":{_display_num}"
        return _display_num
    except FileNotFoundError:
        raise RuntimeError("❌ Xvfb 未安裝，請先執行 /browser install 或手動安裝：apk add xvfb" if _is_alpine() else "❌ Xvfb 未安裝，請先執行 /browser install 或手動安裝：sudo apt-get install -y xvfb")
    except Exception as e:
        _xvfb_proc = None
        raise RuntimeError(f"❌ 無法啟動 Xvfb: {e}")


def _stop_xvfb():
    """停止 Xvfb"""
    global _xvfb_proc, _display_num
    if _xvfb_proc:
        try:
            _xvfb_proc.terminate()
            _xvfb_proc.wait(timeout=5)
        except:
            try:
                _xvfb_proc.kill()
            except:
                pass
        _xvfb_proc = None
        _display_num = None

# ------------------------------------------------------------------------------------ #
# 自然化輸出
# ------------------------------------------------------------------------------------ #

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
                    "  launch     - 啟動瀏覽器（可加 'persistent' 使用持久化設定檔）\n"
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
            return await _handle_close()
        elif action == "status":
            return await _handle_status()
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
    """啟動 Chromium 瀏覽器"""
    global _playwright, _browser, _page, _context

    if _page is not None:
        try:
            # 檢查頁面是否還活著
            await _page.title()
            return json.dumps({
                "success": True,
                "action": "launch",
                "message": "瀏覽器已在運行中，無需重複啟動",
                "display": _display_num,
                "persistent": os.path.isdir(_persistent_dir)
            }, ensure_ascii=False)
        except:
            # 頁面已失效，清理後重新啟動
            await _handle_close()

    if not _check_playwright():
        return json.dumps({
            "success": False,
            "error": "⚠️ Playwright 尚未安裝，請先執行 /browser install",
            "action": "launch"
        }, ensure_ascii=False)

    from playwright.async_api import async_playwright

    # 啟動 Xvfb
    try:
        display = _start_xvfb()
    except RuntimeError as e:
        return json.dumps({"success": False, "error": str(e), "action": "launch"}, ensure_ascii=False)

    # ⚠️ 超時保護：async_playwright().start() 可能因為下載 Chromium 而長時間懸掛
    try:
        _playwright = await asyncio.wait_for(async_playwright().start(), timeout=30)
    except asyncio.TimeoutError:
        return json.dumps({
            "success": False,
            "error": "⚠️ Playwright 啟動超時（30秒）。可能正在下載 Chromium，請先手動執行 /browser install。",
            "action": "launch"
        }, ensure_ascii=False)

    use_persistent = args.strip().lower() == "persistent"
    chromium_path = _find_chromium_path()

    try:
        if use_persistent:
            os.makedirs(_persistent_dir, exist_ok=True)
            _context = await asyncio.wait_for(
                _playwright.chromium.launch_persistent_context(
                    _persistent_dir,
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
                    ],
                    executable_path=chromium_path,
                ),
                timeout=60
            )
            _browser = None
            _page = _context.pages[0] if _context.pages else await _context.new_page()
        else:
            launch_opts = {
                "headless": False,
                "args": [
                    "--no-sandbox",
                    "--disable-setuid-sandbox",
                    "--disable-blink-features=AutomationControlled",
                ],
            }
            if chromium_path:
                launch_opts["executable_path"] = chromium_path

            _browser = await asyncio.wait_for(
                _playwright.chromium.launch(**launch_opts),
                timeout=60
            )
            _context = await _browser.new_context(
                viewport={"width": 1280, "height": 800},
                user_agent=(
                    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/126.0.0.0 Safari/537.36"
                ),
            )
            _page = await _context.new_page()
    except asyncio.TimeoutError:
        return json.dumps({
            "success": False,
            "error": "⚠️ 啟動 Chromium 超時（60秒）。請檢查系統資源或手動重試。",
            "action": "launch"
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "success": False,
            "error": f"⚠️ 啟動 Chromium 失敗：{str(e)}",
            "action": "launch"
        }, ensure_ascii=False)

    # 注入反偵測腳本
    await _page.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => false });
        Object.defineProperty(navigator, 'plugins', { get: () => [1, 2, 3, 4, 5] });
        Object.defineProperty(navigator, 'languages', { get: () => ['zh-TW', 'zh', 'en'] });
        window.chrome = { runtime: {} };
    """)

    return json.dumps({
        "success": True,
        "action": "launch",
        "message": "瀏覽器已啟動",
        "display": _display_num,
        "persistent": use_persistent
    }, ensure_ascii=False)


async def _ensure_page():
    """確保瀏覽器和頁面已就緒，否則拋出錯誤"""
    global _page
    if _page is None:
        raise RuntimeError("瀏覽器尚未啟動，請先執行 /browser launch")
    # 檢查頁面是否還活著
    try:
        await _page.evaluate("1")
    except:
        _page = None
        raise RuntimeError("瀏覽器頁面已失效，請重新執行 /browser launch")
    return _page


async def _handle_goto(args: str) -> str:
    """導航到指定網址"""
    page = await _ensure_page()
    url = args.strip()
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
        "title": title
    }, ensure_ascii=False)


async def _handle_click(args: str) -> str:
    """點擊頁面元素"""
    page = await _ensure_page()
    selector = args.strip()
    if not selector:
        return json.dumps({"success": False, "error": "請提供點擊目標（CSS 選擇器或 text=文字）"}, ensure_ascii=False)

    # 如果沒前綴，預設用 text= 匹配
    if not any(selector.startswith(p) for p in ["text=", "css=", "xpath=", "#", ".", "[", "button", "a", "input", "div", "span", "li", "p", "h1", "h2", "h3", "h4"]):
        selector = f"text={selector}"

    await page.click(selector, timeout=10000)
    title = await page.title()
    return json.dumps({
        "success": True,
        "action": "click",
        "target": selector,
        "title": title
    }, ensure_ascii=False)


async def _handle_type(args: str) -> str:
    """在輸入框中輸入文字"""
    page = await _ensure_page()
    parts = args.strip().split(maxsplit=1)
    if len(parts) < 2:
        return json.dumps({"success": False, "error": "格式：/browser type <選擇器> <文字內容>\n例如：/browser type '#email' user@example.com"}, ensure_ascii=False)

    selector, text = parts[0], parts[1]
    await page.fill(selector, text)
    return json.dumps({
        "success": True,
        "action": "type",
        "selector": selector,
        "text_length": len(text)
    }, ensure_ascii=False)


async def _handle_screenshot(args: str) -> str:
    """截圖並儲存"""
    page = await _ensure_page()
    filename = args.strip() if args.strip() else f"browser_screenshot_{int(time.time())}.png"
    if not filename.startswith("/"):
        filename = f"/tmp/{filename}"

    await page.screenshot(path=filename, full_page=False)
    return json.dumps({
        "success": True,
        "action": "screenshot",
        "path": filename
    }, ensure_ascii=False)


async def _handle_scroll(args: str) -> str:
    """滾動頁面"""
    page = await _ensure_page()
    parts = args.strip().split()
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
        "pixels": pixels
    }, ensure_ascii=False)


async def _handle_wait(args: str) -> str:
    """等待（毫秒或等待選擇器出現）"""
    page = await _ensure_page()
    arg = args.strip()

    if not arg:
        await asyncio.sleep(1)
        return json.dumps({"success": True, "action": "wait", "waited": "1s"}, ensure_ascii=False)

    # selector <選擇器> 格式
    if arg.startswith("selector "):
        sel = arg[9:].strip()
        if sel:
            await page.wait_for_selector(sel, timeout=15000)
            return json.dumps({"success": True, "action": "wait", "waited": f"selector: {sel}"}, ensure_ascii=False)

    # 嘗試解析為毫秒
    try:
        ms = int(arg)
        await asyncio.sleep(ms / 1000.0)
        return json.dumps({"success": True, "action": "wait", "waited": f"{ms}ms"}, ensure_ascii=False)
    except ValueError:
        # 當作選擇器
        await page.wait_for_selector(arg, timeout=15000)
        return json.dumps({"success": True, "action": "wait", "waited": f"selector: {arg}"}, ensure_ascii=False)


async def _handle_press(args: str) -> str:
    """按下鍵盤按鍵"""
    page = await _ensure_page()
    key = args.strip()
    if not key:
        return json.dumps({"success": False, "error": "請提供按鍵名稱，例如：Enter, Tab, Escape, ArrowDown, Control+A"}, ensure_ascii=False)

    await page.keyboard.press(key)
    return json.dumps({"success": True, "action": "press", "key": key}, ensure_ascii=False)


async def _handle_close() -> str:
    """關閉瀏覽器並清理資源"""
    global _playwright, _browser, _page, _context

    errors = []
    for obj, name in [(_page, "page"), (_context, "context"), (_browser, "browser"), (_playwright, "playwright")]:
        if obj:
            try:
                if hasattr(obj, 'close'):
                    await obj.close()
                elif hasattr(obj, 'stop'):
                    await obj.stop()
            except Exception as e:
                errors.append(f"{name}: {e}")

    _page = None
    _context = None
    _browser = None
    _playwright = None
    _stop_xvfb()

    return json.dumps({
        "success": True,
        "action": "close",
        "message": "瀏覽器已關閉" + (f"（清理時有 {len(errors)} 個非致命錯誤）" if errors else "")
    }, ensure_ascii=False)


async def _handle_status() -> str:
    """查詢瀏覽器運行狀態"""
    global _page
    if _page is None:
        return json.dumps({"success": True, "action": "status", "running": False}, ensure_ascii=False)

    try:
        url = _page.url
        title = await _page.title()
        return json.dumps({
            "success": True,
            "action": "status",
            "running": True,
            "url": url,
            "title": title,
            "display": _display_num,
            "persistent": os.path.isdir(_persistent_dir)
        }, ensure_ascii=False)
    except Exception as e:
        return json.dumps({
            "success": True,
            "action": "status",
            "running": True,
            "url": "（無法取得）",
            "title": "（無法取得）",
            "note": str(e)
        }, ensure_ascii=False)


async def _handle_execute(args: str) -> str:
    """在頁面中執行 JavaScript"""
    page = await _ensure_page()
    js_code = args.strip()
    if not js_code:
        return json.dumps({"success": False, "error": "請提供 JavaScript 代碼"}, ensure_ascii=False)

    result = await page.evaluate(js_code)
    return json.dumps({
        "success": True,
        "action": "execute",
        "result": str(result)[:2000]
    }, ensure_ascii=False)


async def _handle_content(args: str) -> str:
    """取得頁面文字內容"""
    page = await _ensure_page()
    full = args.strip().lower() == "full"

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
        "content": content[:5000]
    }, ensure_ascii=False)
