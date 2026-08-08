"""
sandbox_bridge.py - 沙箱橋接工具
讓容器內的 agent 可以存取宿主機資源（pm2 logs, Chrome CDP, 桌面狀態）

20260807 - 解決 agent 在 Docker 容器內看不到 pm2 / Chrome 的問題
"""

import os
import json
import subprocess
from pathlib import Path

# ─── 配置 ─────────────────────────────────────────
PM2_LOG_DIRS = [
    "/home/ubuntu/.pm2_logs",       # 宿主 pm2 logs 掛載點
    "/home/ubuntu/.pm2/logs",       # 備用路徑
    os.path.expanduser("~/.pm2/logs"),
]

# 宿主 Chrome CDP 端口（容器可透過 host 網路存取）
# 如果是 Docker，通常宿主在 172.17.0.1 或 host.docker.internal
CDP_HOSTS = [
    "host.docker.internal",
    "172.17.0.1",
    "127.0.0.1",
]
CDP_PORT = os.environ.get("DESKTOP_CDP_PORT", "9222")


def _find_pm2_log_dir():
    """找到可用的 pm2 logs 目錄"""
    for d in PM2_LOG_DIRS:
        if os.path.isdir(d) and os.listdir(d):
            return d
    return None


def _find_cdp_url():
    """找到可用的 Chrome CDP URL"""
    import urllib.request
    for host in CDP_HOSTS:
        url = f"http://{host}:{CDP_PORT}/json/version"
        try:
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req, timeout=3) as resp:
                data = json.loads(resp.read())
                if "Browser" in data:
                    return f"http://{host}:{CDP_PORT}", data
        except Exception:
            continue
    return None, None


def run(args_str):
    """
    沙箱橋接工具入口
    
    動作：
    - pm2_logs [行數]   : 查看 pm2 最近的日誌
    - pm2_status        : 模擬 pm2 status（讀取 log 判斷）
    - cdp_status        : 檢查 Chrome CDP 是否可用
    - desktop_status    : 檢查桌面服務狀態（Xvfb, x11vnc, websockify）
    - host_exec <cmd>   : 透過 SSH/socat 在宿主機執行指令（需設定）
    - diag              : 完整診斷報告
    """
    parts = args_str.strip().split(maxsplit=1)
    action = parts[0].lower() if parts else "diag"
    arg = parts[1] if len(parts) > 1 else ""
    
    if action == "pm2_logs":
        return _read_pm2_logs(arg)
    elif action == "pm2_status":
        return _pm2_status()
    elif action == "cdp_status":
        return _cdp_status()
    elif action == "desktop_status":
        return _desktop_status()
    elif action == "host_exec":
        return _host_exec(arg)
    elif action == "diag":
        return _full_diag()
    else:
        return json.dumps({
            "success": False,
            "error": f"未知動作: {action}",
            "available": ["pm2_logs", "pm2_status", "cdp_status", "desktop_status", "host_exec", "diag"]
        }, ensure_ascii=False)


def _read_pm2_logs(arg):
    """讀取 pm2 日誌"""
    log_dir = _find_pm2_log_dir()
    if not log_dir:
        return json.dumps({
            "success": False,
            "error": "找不到 pm2 logs 目錄。請確認容器已掛載 ~/.pm2/logs → /home/ubuntu/.pm2_logs",
            "checked_paths": PM2_LOG_DIRS
        }, ensure_ascii=False)
    
    lines = 30
    if arg.strip():
        try:
            lines = int(arg.strip())
        except ValueError:
            pass
    
    result = {"success": True, "log_dir": log_dir, "files": {}}
    
    try:
        log_files = sorted(Path(log_dir).glob("*.log"))
        for lf in log_files:
            try:
                with open(lf, 'r') as f:
                    all_lines = f.readlines()
                    result["files"][lf.name] = {
                        "total_lines": len(all_lines),
                        "last_lines": [l.rstrip() for l in all_lines[-lines:]]
                    }
            except Exception as e:
                result["files"][lf.name] = {"error": str(e)}
    except Exception as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)
    
    return json.dumps(result, ensure_ascii=False, indent=2)


def _pm2_status():
    """模擬 pm2 status"""
    log_dir = _find_pm2_log_dir()
    if not log_dir:
        return json.dumps({
            "success": False,
            "error": "找不到 pm2 logs，無法判斷服務狀態",
            "hint": "pm2 在宿主機運行，容器內無法直接執行 pm2 指令。請用 sandbox_bridge pm2_logs 查看日誌"
        }, ensure_ascii=False)
    
    # 檢查最近的日誌來判斷服務狀態
    result = {"success": True, "note": "以下狀態由日誌推斷，非即時 pm2 status", "services": {}}
    
    try:
        for lf in sorted(Path(log_dir).glob("*.log")):
            stat = lf.stat()
            result["services"][lf.name.replace(".log", "").replace("_error", "").replace("_out", "")] = {
                "last_modified": subprocess.getoutput(f"stat -c '%y' '{lf}' 2>/dev/null") or str(stat.st_mtime),
                "size_bytes": stat.st_size,
                "has_content": stat.st_size > 0
            }
    except Exception as e:
        result["error"] = str(e)
    
    return json.dumps(result, ensure_ascii=False, indent=2)


def _cdp_status():
    """檢查 Chrome CDP 狀態"""
    cdp_url, info = _find_cdp_url()
    if cdp_url:
        return json.dumps({
            "success": True,
            "cdp_url": cdp_url,
            "browser": info.get("Browser", "unknown"),
            "version": info.get("webSocketDebuggerUrl", "")[:50] + "..."
        }, ensure_ascii=False, indent=2)
    else:
        return json.dumps({
            "success": False,
            "error": "無法連線 Chrome CDP",
            "checked": [f"http://{h}:{CDP_PORT}" for h in CDP_HOSTS],
            "hint": "請在宿主機執行: bash ~/.mok/start_chromium_cdp.sh"
        }, ensure_ascii=False, indent=2)


def _desktop_status():
    """檢查桌面服務 (Xvfb, x11vnc, websockify)"""
    checks = {}
    
    # 檢查 Xvfb
    try:
        r = subprocess.run(["pgrep", "-f", "Xvfb.*:1"], capture_output=True, text=True, timeout=3)
        checks["Xvfb_:_1"] = r.returncode == 0
    except Exception:
        checks["Xvfb_:_1"] = "unknown"
    
    # 檢查 x11vnc
    try:
        r = subprocess.run(["pgrep", "-f", "x11vnc.*5900"], capture_output=True, text=True, timeout=3)
        checks["x11vnc_:_5900"] = r.returncode == 0
    except Exception:
        checks["x11vnc_:_5900"] = "unknown"
    
    # 檢查 websockify
    try:
        r = subprocess.run(["pgrep", "-f", "websockify.*6080"], capture_output=True, text=True, timeout=3)
        checks["websockify_:_6080"] = r.returncode == 0
    except Exception:
        checks["websockify_:_6080"] = "unknown"
    
    # 檢查 fluxbox
    try:
        r = subprocess.run(["pgrep", "-f", "fluxbox"], capture_output=True, text=True, timeout=3)
        checks["fluxbox"] = r.returncode == 0
    except Exception:
        checks["fluxbox"] = "unknown"
    
    all_ok = all(v == True for v in checks.values())
    
    return json.dumps({
        "success": all_ok,
        "checks": checks,
        "note": "桌面服務（Xvfb/fluxbox/x11vnc/websockify）應該在宿主機運行，容器內無法管理它們。"
    }, ensure_ascii=False, indent=2)


def _host_exec(cmd):
    """嘗試在宿主機執行指令（有限支援）"""
    # 方法1: 透過 SSH (如果設定了)
    ssh_host = os.environ.get("MOK_HOST_SSH", "")
    if ssh_host and cmd:
        try:
            r = subprocess.run(
                ["ssh", "-o", "StrictHostKeyChecking=no", "-o", "ConnectTimeout=5", ssh_host, cmd],
                capture_output=True, text=True, timeout=15
            )
            return json.dumps({
                "success": r.returncode == 0,
                "stdout": r.stdout[:2000],
                "stderr": r.stderr[:1000],
                "returncode": r.returncode
            }, ensure_ascii=False)
        except Exception as e:
            return json.dumps({"success": False, "error": f"SSH failed: {e}"}, ensure_ascii=False)
    
    # 方法2: 檢查是否有 nsenter 權限（同 pid namespace）
    try:
        r = subprocess.run(["nsenter", "-t", "1", "-m", "-u", "-i", "-n", "--", "sh", "-c", cmd],
                          capture_output=True, text=True, timeout=15)
        return json.dumps({
            "success": r.returncode == 0,
            "stdout": r.stdout[:2000],
            "stderr": r.stderr[:1000],
            "method": "nsenter"
        }, ensure_ascii=False)
    except Exception:
        pass
    
    return json.dumps({
        "success": False,
        "error": "無法在宿主機執行指令。請設定 MOK_HOST_SSH 環境變數，或給容器 privileged 權限。",
        "hint": "安全的做法是在宿主機直接執行指令，而非透過容器。"
    }, ensure_ascii=False)


def _full_diag():
    """完整診斷"""
    diag = {
        "environment": {
            "in_container": os.path.exists("/.dockerenv"),
            "hostname": subprocess.getoutput("hostname"),
            "os": subprocess.getoutput("cat /etc/os-release 2>/dev/null | head -3"),
        },
        "pm2_logs": json.loads(_read_pm2_logs("5")),
        "cdp": json.loads(_cdp_status()),
        "desktop": json.loads(_desktop_status()),
        "mounts": {
            "mok_mounted": os.path.isdir("/home/ubuntu/.mok"),
            "pm2_mounted": _find_pm2_log_dir() is not None,
        }
    }
    return json.dumps(diag, ensure_ascii=False, indent=2)


# ─── 註冊工具 ─────────────────────────────────────
TOOL_SCHEMA = {
    "name": "sandbox_bridge",
    "description": """沙箱橋接工具：讓容器內的 agent 存取宿主機資源。

【功能】
- pm2_logs [行數]: 讀取 pm2 日誌（預設30行）
- pm2_status: 模擬 pm2 status（從日誌推斷）
- cdp_status: 檢查宿主 Chrome CDP 是否可用
- desktop_status: 檢查桌面服務狀態
- host_exec <cmd>: 嘗試在宿主機執行指令
- diag: 完整診斷報告

【注意】此工具解決 agent 在 Docker 容器內看不到宿主 pm2/Chrome 的問題。""",
    "parameters": {
        "type": "object",
        "properties": {
            "args": {
                "type": "string",
                "description": "動作及參數。格式: '動作 [參數]'。例如 'pm2_logs 50'"
            }
        },
        "required": ["args"]
    }
}


if __name__ == "__main__":
    import sys
    print(run(" ".join(sys.argv[1:]) if sys.argv[1:] else "diag"))
