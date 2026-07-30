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
PROJECT_DIR = Path("/home/ubuntu/.mok")


AGENT_ROOT = PROJECT_DIR / "agent"   # Agent 配置根目錄
EXCLUDE_FILES = {".env"}
processes = []          # 存儲子進程對象，每個進程有 type 屬性
stop_event = threading.Event()

def log_with_prefix(prefix, line):
    line = line.rstrip('\n')
    print(f"{prefix} {line}", flush=True)

def stream_reader(pipe, prefix, output_queue):
    for line in pipe:
        if not line:
            break
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





def start_bot(agent_name, config_path):
    env = os.environ.copy()
    env.update(get_env_from_config(config_path))
    
    # 檢查是否提供了有效的 MOK_TG_TOKEN，沒有則跳過啟動
    if not env.get("MOK_TG_TOKEN"):
        log_with_prefix(f"[Bot:{agent_name}]", "配置文件缺少 MOK_TG_TOKEN，跳過啟動")
        return None

    env["MOK_AGENT_NAME"] = agent_name
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

    web_script = PROJECT_DIR / "frontends" / "mok_web.py"
    if not web_script.exists():
        log_with_prefix("[Web]", f"錯誤: {web_script} 不存在")
        return None

    proc = subprocess.Popen(
        [sys.executable, str(web_script)],   # 移除 "--port", str(port)
        #[sys.executable, str(web_script), "--port", str(port)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        env=env, cwd=str(PROJECT_DIR), text=True, bufsize=1
    )
    proc.type = 'web'          # 標記為 Web 進程
    return proc

def signal_handler(sig, frame):
    print("\n收到退出信號，關閉所有子進程...", flush=True)
    stop_event.set()
    for p in processes:
        if p and p.poll() is None:
            try:
                p.terminate()
            except:
                pass
    time.sleep(1)
    for p in processes:
        if p and p.poll() is None:
            try:
                p.kill()
            except:
                pass
    sys.exit(0)

def main():
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)
    print("MOK AGI 統一啟動器 (source 方式加載配置)", flush=True)
    print(f"項目目錄: {PROJECT_DIR}", flush=True)

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

    # 啟動 Web 界面
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
        # 檢查是否有進程退出
        exited = []
        for p in processes:
            if p.poll() is not None:
                exited.append(p)

        if exited:
            for p in exited:
                if p.type == 'web':
                    # Web 進程退出，視為嚴重錯誤，重啟全部
                    print("Web 服務意外退出，5秒後自動重啟所有服務...", flush=True)
                    time.sleep(5)
                    signal_handler(None, None)
                    return  # signal_handler 會退出，這裡不再繼續
                else:  # bot 進程退出
                    # 機器人退出，從列表中移除，不觸發全局重啟
                    print(f"機器人進程（PID {p.pid}）已退出，將不再重啟。", flush=True)
                    processes.remove(p)
            # 如果只剩下 Web 進程，也無需額外動作
        time.sleep(2)

if __name__ == "__main__":
    main()