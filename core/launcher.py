#!/usr/bin/env python3


"""
202606072303
launcher.py - 統一啟動 pm2 24小時運行的多個 agent 和網頁界面，支持 source 配置文件加載環境變量
- 從 ~/.mok/ 目錄下讀取以 . 開頭的配置文件
"""


import os
import sys
import subprocess
import threading
import queue
import time
import signal
from pathlib import Path

#PROJECT_DIR = Path.home() / ".mok"
PROJECT_DIR = Path("/home/ubuntu/.mok"); import logging, logging.handlers as _lgh; _sse_log = logging.getLogger("mok.sse"); _sse_log.setLevel(logging.INFO); _sse_log.propagate = False; _sse_h = _lgh.RotatingFileHandler(str(PROJECT_DIR / "logs" / "sse.log"), maxBytes=536870912, backupCount=5, encoding="utf-8"); _sse_h.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s")); _sse_log.addHandler(_sse_h)  # [E 2026-09-26 衍] SSE 事件獨立日誌檔（512MB 自帶輪替，保留 5 份）


AGENT_ROOT = PROJECT_DIR / "agent"   # Agent 配置根目錄
EXCLUDE_FILES = {".env"}
processes = []          # 存儲子進程對象，每個進程有 type 屬性
stop_event = threading.Event()


def _env_flag_true(name: str, default: str = "0") -> bool:
    val = os.environ.get(name, default)
    if val is None:
        return False
    return str(val).strip().lower() in {"1", "true", "yes", "on"}

def log_with_prefix(prefix, line):
    line = line.rstrip('\n')
    print(f"{prefix} {line}", flush=True)

# [E 2026-09-29 架] item3：每個 agent 獨立 log 目錄
AGENT_LOG_DIR = PROJECT_DIR / "logs" / "agents"
from agent_log_writer import write_record as _alw  # [P0-3 2026-10-04 稚] agent log 批次 flush + 單檔輪替
try:
    AGENT_LOG_DIR.mkdir(parents=True, exist_ok=True)
except Exception:
    pass
_agent_log_fhs = {}
_agent_log_lock = threading.Lock()

def write_agent_log(prefix, line):
    # [E 2026-09-29 架] item3：每個 agent 寫自己的 log 檔 + 正確時間戳（%H:%M:%S）
    try:
        name = prefix.strip().strip("[]").replace("[", "_").replace("]", "_")
        name = name.replace("Bot:", "").replace(":", "_").replace("/", "_").replace(" ", "_").strip("_")
        while "__" in name:
            name = name.replace("__", "_")
        fn = (name or "misc") + ".log"
        ts = time.strftime("%Y-%m-%d %H:%M:%S")
        with _agent_log_lock:
            fh = _agent_log_fhs.get(fn)
            if fh is None:
                fh = open(AGENT_LOG_DIR / fn, "a", encoding="utf-8")
                _agent_log_fhs[fn] = fh
            _alw(fn, fh, f"{ts} {prefix} {line.rstrip()}\n")
            pass  # [P0-3] 批次 flush 由 agent_log_writer 負責
    except Exception:
        pass


def stream_reader(pipe, prefix, output_queue):
    for line in pipe:
        if not line:
            break
        write_agent_log(prefix, line)
        if "[SSE" in line[:24]:
            _sse_log.info("%s %s", prefix, line.rstrip("\n"))  # [E 2026-09-26 衍] SSE 事件改走獨立 log
        else:
            output_queue.put((prefix, line))

def get_env_from_config(config_path):
    """直接讀取配置文件，解析 KEY=VALUE 行，忽略註釋和空行"""
    env = {}
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, val = line.split('=', 1)
                    key = key.strip()
                    val = val.strip()
                    # 去除可能的引號
                    if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                        val = val[1:-1]
                    env[key] = val
    except Exception as e:
        log_with_prefix("[Launcher]", f"讀取配置失敗 {config_path}: {e}")
    return env





_THREAD_ENV_KEYS = (
    "OMP_NUM_THREADS", "MKL_NUM_THREADS", "OPENBLAS_NUM_THREADS",
    "NUMEXPR_NUM_THREADS", "VECLIB_MAXIMUM_THREADS",
)


def _true_cpu_count():
    """真實可用核心數：優先 sched_getaffinity（受 affinity / cgroup 限制時最準），其次 os.cpu_count()。"""
    try:
        n = len(os.sched_getaffinity(0))
        if n >= 1:
            return n
    except (AttributeError, OSError):
        pass
    return os.cpu_count() or 1


def apply_thread_limits(env):
    """A方案：把 MOK_NUM_THREADS 套用到所有 native 執行緒環境變數，避免多個侍女進程
    各自依偵測核心數開一堆執行緒，造成 CPU 超額訂閱與發熱。

    [2026-10-08 汐｜A 治本]
    - 未設 MOK_NUM_THREADS（或值無效）→ 採真核心數，不再硬性 1（修掉 nproc/shell 誤報 1 核）。
    - 有明確設定值（>=1）→ 尊重設定，保留管理員限流能力。
    - 結果同時寫回 launcher 本體 os.environ，使 exec/shell/cron 與子進程一致。
    如需刻意限流，於配置檔設 MOK_NUM_THREADS=<n> 即可。"""
    raw = str(env.get("MOK_NUM_THREADS", "")).strip()
    try:
        n = int(raw)
        if n < 1:
            n = 1
    except (TypeError, ValueError):
        n = _true_cpu_count()   # [汐 2026-10-08 A治本] 未設定 → 用真核心數，不再硬性 1
    for _k in _THREAD_ENV_KEYS:
        env[_k] = str(n)
    env["TOKENIZERS_PARALLELISM"] = "false"
    # [汐 2026-10-08 A治本] 同步回本體環境：讓 launcher 自身與其後續 fork（exec/shell/cron）看到一致真值
    try:
        for _k in _THREAD_ENV_KEYS:
            os.environ[_k] = str(n)
        os.environ["TOKENIZERS_PARALLELISM"] = "false"
    except Exception:
        pass
    return n


def start_bot(agent_name, config_path):
    env = os.environ.copy()
    env.update(get_env_from_config(config_path))
    
    # 檢查是否提供了有效的 MOK_TG_TOKEN，沒有則跳過啟動
    if not env.get("MOK_TG_TOKEN"):
        log_with_prefix(f"[Bot:{agent_name}]", "配置文件缺少 MOK_TG_TOKEN，跳過啟動")
        return None

    env["MOK_AGENT_NAME"] = agent_name
    apply_thread_limits(env)
    env["MOKAGI_HOME"] = "mok"
    env["PYTHONPATH"] = f"{str(PROJECT_DIR / 'core')}:{str(PROJECT_DIR)}:{str(AGENT_ROOT)}"

    bot_script = PROJECT_DIR / "frontends" / "mok_tg.py"
    if not bot_script.exists():
        log_with_prefix(f"[Bot:{agent_name}]", f"錯誤: {bot_script} 不存在")
        return None

    proc = subprocess.Popen(
        [sys.executable, str(bot_script)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, cwd=str(PROJECT_DIR), text=True, bufsize=1
    )
    proc.type = 'bot'          # 標記為機器人進程

    


    return proc

def start_web(port=5000):
    env = os.environ.copy()
    default_cfg = PROJECT_DIR / ".default"
    if default_cfg.exists():
        env.update(get_env_from_config(default_cfg))
    env["MOKAGI_HOME"] = "mok"
    env["PYTHONPATH"] = f"{str(PROJECT_DIR / 'core')}:{str(PROJECT_DIR)}:{str(AGENT_ROOT)}"

    apply_thread_limits(env)

    # [E 2026-09-29 架] 舊的（detached）Web 若尚在收尾、仍佔用埠，先等它退出，避免新舊搶埠
    try:
        import socket
        _deadline = time.time() + 15
        while time.time() < _deadline:
            _s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            try:
                _s.settimeout(0.3)
                _busy = _s.connect_ex(("127.0.0.1", port)) == 0
            finally:
                _s.close()
            if not _busy:
                break
            print(f"[launcher] 埠 {port} 仍被舊 Web 佔用，等待其收尾...", flush=True)
            time.sleep(0.5)
    except Exception as _e:
        print(f"[launcher] 檢查埠 {port} 失敗（略過）：{_e}", flush=True)

    web_script = PROJECT_DIR / "frontends" / "mok_web.py"
    if not web_script.exists():
        log_with_prefix("[Web]", f"錯誤: {web_script} 不存在")
        return None

    # 讓 Web 進程脫離 launcher 的 process group，避免父進程收到 SIGTERM / signal_handler
    # 後連帶終止實際的 Flask 服務，造成前端 SSE/HTTP 斷線、連續重啟與 502/524。
    proc = subprocess.Popen(
        [sys.executable, str(web_script)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, cwd=str(PROJECT_DIR), text=True, bufsize=1,
        start_new_session=True
    )
    proc.type = 'web'
    return proc

GRACE_SECONDS = int(os.environ.get("MOK_SHUTDOWN_GRACE", "10"))


def broadcast_restart_notice():
    """(item1) 優雅重啟：請 Web 層向所有 SSE 前端廣播『服務即將重啟』，
    讓前端顯示提示，並在連線中斷後自動續流把答案接回來。"""
    try:
        import urllib.request
        req = urllib.request.Request(
            "http://127.0.0.1:5000/api/restart-notice",
            data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
        urllib.request.urlopen(req, timeout=3).read()
        print("[launcher] 已廣播重啟通知給前端", flush=True)
    except Exception as e:
        print(f"[launcher] 廣播重啟通知失敗（略過）：{e}", flush=True)


# ===== [軟重啟 2026-10-03 by 稚] 前端「軟重啟」的後端控制點 =====
# 機制：Web 子進程（軟重啟補丁）把請求寫成旗標檔；launcher 主迴圈偵測到就
#       「只重拉 Web 子進程」＝重新載入所有補丁 + 復原核心，完全不碰 pm2，
#       故不會觸發 pm2 層的 MOK-PLAN-C-GUARD 守衛；頁面照樣立即更新。
SOFT_RESTART_FLAG = PROJECT_DIR / "run" / "soft_restart.flag"
try:
    SOFT_RESTART_FLAG.parent.mkdir(parents=True, exist_ok=True)
except Exception:
    pass


def _consume_soft_restart_flag():
    """【已封鎖 2026-10-04】軟重啟旗標機制停用：偵測到就丟棄，一律回 False。"""
    try:
        if SOFT_RESTART_FLAG.exists():
            SOFT_RESTART_FLAG.unlink()
            print("[launcher] 軟重啟旗標已封鎖，忽略並刪除。", flush=True)
    except Exception as e:
        print(f"[launcher] 讀取軟重啟旗標失敗：{e}", flush=True)
    return False


# ===== [主人重啟 2026-10-03 by 稚] 前端「主人重啟鈕」的後端控制點 =====
# 機制：Web 子進程（主人重啟補丁）把請求寫成旗標檔；launcher 主迴圈偵測到就
#       「完整重啟」：優雅關閉所有子進程（Web + 全部 Bot）→ execv 重啟 launcher 本體。
#       = 重載 launcher／mok_web／所有補丁／所有 Agent 程式碼。
#       完全不呼叫 pm2 指令，故不會觸發 MOK-PLAN-C-GUARD 守衛。
MASTER_RESTART_FLAG = PROJECT_DIR / "run" / "master_restart.flag"
try:
    MASTER_RESTART_FLAG.parent.mkdir(parents=True, exist_ok=True)
except Exception:
    pass


def _consume_master_restart_flag():
    """有主人重啟旗標就消費掉並回 True。"""
    try:
        if MASTER_RESTART_FLAG.exists():
            # 忽略過期旗標（>120s），避免殘留旗標在下次啟動時誤觸發
            try:
                if time.time() - MASTER_RESTART_FLAG.stat().st_mtime > 120:
                    MASTER_RESTART_FLAG.unlink()
                    return False
            except Exception:
                pass
            # [封後門 2026-10-04] 只認主人重啟鈕（admin session）寫入的內容；
            # 空檔 / 裸 touch 一律視為未授權，直接丟棄。
            try:
                _mrf = MASTER_RESTART_FLAG.read_text(encoding="utf-8", errors="replace").strip()
            except Exception:
                _mrf = ""
            MASTER_RESTART_FLAG.unlink()
            if _mrf != "master":
                print("[launcher] 主人重啟旗標內容不符（疑似未授權），已丟棄。", flush=True)
                return False
            return True
    except Exception as e:
        print(f"[launcher] 讀取主人重啟旗標失敗：{e}", flush=True)
    return False


def master_restart(output_queue=None):
    """完整重啟：優雅關掉所有子進程（Web + Bot），再以 execv 重啟 launcher 本體。
    不呼叫 pm2 指令 → 不觸發方案C 閘門；重啟後所有程式碼與補丁都會重新載入。"""
    print("[launcher] ← 收到主人重啟請求：關閉全部子進程 + 重啟本體", flush=True)
    broadcast_restart_notice()
    for _p in list(processes):
        if _p and _p.poll() is None:
            try:
                _p.terminate()
            except Exception:
                pass
    _deadline = time.time() + GRACE_SECONDS
    while time.time() < _deadline:
        if all((_p is None) or (_p.poll() is not None) for _p in processes):
            break
        time.sleep(0.2)
    for _p in list(processes):
        if _p and _p.poll() is None:
            try:
                _p.send_signal(9)
            except Exception:
                pass
    print("[launcher] ✅ 子進程已收尾，重啟 launcher 本體…", flush=True)
    try:
        sys.stdout.flush()
        sys.stderr.flush()
    except Exception:
        pass
    try:
        os.execv(sys.executable, [sys.executable, os.path.abspath(__file__)])
    except Exception as _e:
        print(f"[launcher] ⚠️ 本體重啟失敗（{_e}）：改為退出，交由 pm2 自動重拉", flush=True)
        os._exit(1)


def _current_web_proc():
    for _p in processes:
        if getattr(_p, 'type', None) == 'web' and _p.poll() is None:
            return _p
    return None


def soft_restart_web(output_queue=None):
    """只重拉 Web 子進程：優雅關舊的 → 等埠釋放 → 拉起新的（開機自動重載所有補丁）。"""
    print("[launcher] ← 收到軟重啟請求：只重拉 Web（重載所有補丁），不碰 pm2", flush=True)
    broadcast_restart_notice()
    old = _current_web_proc()
    if old is not None:
        try:
            old.terminate()
        except Exception:
            pass
        _deadline = time.time() + GRACE_SECONDS
        while time.time() < _deadline and old.poll() is None:
            time.sleep(0.2)
        if old.poll() is None:
            try:
                old.kill()
            except Exception:
                pass
        try:
            processes.remove(old)
        except ValueError:
            pass
    newp = start_web(5000)
    if newp:
        processes.append(newp)
        if output_queue is not None:
            threading.Thread(target=stream_reader, args=(newp.stdout, "[Web]", output_queue), daemon=True).start()
            if newp.stderr:
                threading.Thread(target=stream_reader, args=(newp.stderr, "[Web][ERR]", output_queue), daemon=True).start()
        print(f"[launcher] ✅ Web 已軟重啟（新 PID {newp.pid}），補丁已重新載入", flush=True)
    else:
        print("[launcher] ⚠️ 軟重啟失敗：Web 未成功啟動", flush=True)
    return newp


def signal_handler(sig, frame):
    print(f"\n收到退出信號 {sig}，進入優雅關閉（廣播 → 寬限 {GRACE_SECONDS}s → 收尾）...", flush=True)
    # 1) 先廣播，讓前端知道是「重啟」而非斷線（前端會自動續流把答案接回來）
    broadcast_restart_notice()
    # 2) 對所有子進程（含 Web）送 SIGTERM，給寬限期讓進行中的工作收尾（不再 1 秒硬砍）
    for p in processes:
        if p and p.poll() is None:
            try:
                p.terminate()
            except Exception:
                pass
    deadline = time.time() + GRACE_SECONDS
    while time.time() < deadline:
        if all((p is None) or (p.poll() is not None) for p in processes):
            break
        time.sleep(0.2)
    # 3) 只有逾時仍未退出的才強制收掉
    for p in processes:
        if p and p.poll() is None:
            try:
                p.kill()
            except Exception:
                pass
    stop_event.set()
    sys.exit(0)

def _ensure_pm2_guard():
    """[方案C] 開機自檢：pm2 CLI 二進位層守衛補丁若不在（例如系統還原後），自動重注入。"""
    cli = "/usr/lib/node_modules/pm2/bin/pm2"
    mark = "MOK-PLAN-C-GUARD"
    guard_dir = "/home/ubuntu/.mok/guard"
    patcher = os.path.join(guard_dir, "patch_pm2_cli.py")
    tmp = os.path.join(guard_dir, "pm2.patched.tmp")
    try:
        with open(cli, "r", encoding="utf-8", errors="surrogateescape") as f:
            cur = f.read()
    except Exception as e:
        print(f"[Guard] 讀取 pm2 CLI 失敗：{e}", flush=True)
        return False
    if mark in cur:
        print("[Guard] pm2 CLI 守衛補丁存在，通過。", flush=True)
        return True
    print("[Guard] pm2 CLI 守衛補丁【不存在】→ 嘗試自動重注入……", flush=True)
    if not os.path.exists(patcher):
        print(f"[Guard] 找不到注入腳本 {patcher}，請主人手動處理。", flush=True)
        return False
    try:
        subprocess.run([sys.executable, patcher], check=True, timeout=60)
        subprocess.run(["sudo", "-n", "cp", tmp, cli], check=True, timeout=30)
        subprocess.run(["sudo", "-n", "chmod", "755", cli], check=True, timeout=30)
        with open(cli, "r", encoding="utf-8", errors="surrogateescape") as f:
            ok = mark in f.read()
        print("[Guard] 重注入%s。" % ("成功" if ok else "後驗證失敗"), flush=True)
        return ok
    except Exception as e:
        print(f"[Guard] 自動重注入失敗：{e}（請主人手動處理）", flush=True)
        return False


def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    print("MOK AGI 統一啟動器 (source 方式加載配置)", flush=True)
    print(f"項目目錄: {PROJECT_DIR}", flush=True)
    _ensure_pm2_guard()

    web_only_mode = _env_flag_true("MOK_WEB_ONLY", "0")
    if web_only_mode:
        print("MOK_WEB_ONLY=1：啟用 Web Only 模式（跳過所有 Bot 啟動）", flush=True)

    config_files = []
    if AGENT_ROOT.exists():
        for agent_dir in AGENT_ROOT.iterdir():
            if agent_dir.is_dir() and "BACKUP" not in agent_dir.name:   # 增加过滤
                agent_name = agent_dir.name
                cfg_path = agent_dir / f".{agent_name}"
                if cfg_path.exists():
                    config_files.append((agent_name, cfg_path))
                    print(f"發現配置: {agent_name} -> {cfg_path}", flush=True)
    else:
        print(f"警告: Agent 目錄 {AGENT_ROOT} 不存在", flush=True)

    output_queue = queue.Queue()

    # 啟動所有機器人
    if not web_only_mode:
        for agent_name, cfg_path in config_files:
            print(f"正在啟動機器人: {agent_name}", flush=True)
            proc = start_bot(agent_name, cfg_path)
            if proc:
                processes.append(proc)
                threading.Thread(target=stream_reader, args=(proc.stdout, f"[Bot:{agent_name}]", output_queue), daemon=True).start()
                if proc.stderr:
                    threading.Thread(target=stream_reader, args=(proc.stderr, f"[Bot:{agent_name}][ERR]", output_queue), daemon=True).start()
            else:
                print(f"啟動機器人 {agent_name} 失敗（已跳過）", flush=True)
    else:
        print("已跳過 Bot 啟動。", flush=True)

    # Web 與所有 Agent 由同一個 launcher 管理；PM2 只管理 launcher 本身。
    print("正在啟動網頁界面...", flush=True)
    web_proc = start_web(5000)
    if web_proc:
        processes.append(web_proc)
        threading.Thread(target=stream_reader, args=(web_proc.stdout, "[Web]", output_queue), daemon=True).start()
        if web_proc.stderr:
            threading.Thread(target=stream_reader, args=(web_proc.stderr, "[Web][ERR]", output_queue), daemon=True).start()
    else:
        print("啟動網頁界面失敗", flush=True)

    print(f"已啟動 {len(processes)} 個服務", flush=True)

    def handle_output():
        while not stop_event.is_set():
            try:
                prefix, line = output_queue.get(timeout=0.5)
                log_with_prefix(prefix, line)
            except queue.Empty:
                continue
    threading.Thread(target=handle_output, daemon=True).start()

    # 主監控循環
    while not stop_event.is_set():
        # [主人重啟 2026-10-03 by 稚] 前端請求 → 完整重啟（全部子進程 + 本體），不呼叫 pm2 指令
        if _consume_master_restart_flag():
            try:
                master_restart(output_queue)
            except Exception as _e:
                print(f"[launcher] 主人重啟異常：{_e}", flush=True)
            continue
        # [軟重啟 2026-10-03 by 稚] 前端請求 → 只重拉 Web（重載所有補丁），不碰 pm2
        if _consume_soft_restart_flag():
            try:
                soft_restart_web(output_queue)
            except Exception as _e:
                print(f"[launcher] 軟重啟異常：{_e}", flush=True)
            continue
        # 檢查是否有進程退出
        exited = []
        for p in processes:
            if p.poll() is not None:
                exited.append(p)

        if exited:
            for p in exited:
                if p.type == 'web':
                    # 只重啟 Web，不中斷其他 Agent；避免單一 Web 問題拖垮全部服務。
                    exit_code = p.returncode
                    if exit_code is not None and exit_code < 0:
                        exit_reason = f"signal {-exit_code}"
                    else:
                        exit_reason = f"exit code {exit_code}"
                    print(f"Web 服務意外退出（PID {p.pid}，{exit_reason}），5秒後只重啟 Web...", flush=True)
                    processes.remove(p)
                    time.sleep(5)
                    replacement = start_web(5000)
                    if replacement:
                        processes.append(replacement)
                        threading.Thread(target=stream_reader, args=(replacement.stdout, "[Web]", output_queue), daemon=True).start()
                        if replacement.stderr:
                            threading.Thread(target=stream_reader, args=(replacement.stderr, "[Web][ERR]", output_queue), daemon=True).start()
                    continue
                else:  # bot 進程退出
                    # 機器人退出，從列表中移除，不觸發全局重啟
                    print(f"機器人進程（PID {p.pid}）已退出，將不再重啟。", flush=True)
                    processes.remove(p)
            # 如果只剩下 Web 進程，也無需額外動作
        time.sleep(2)

if __name__ == "__main__":
    main()