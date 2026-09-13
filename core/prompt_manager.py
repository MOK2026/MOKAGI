"""prompt_manager.py - 系統提示詞管理"""
import logging
import os
import platform
import subprocess
import time
from typing import Dict, Optional

from shared import (
    MOKAGI_home,
    _system_context_cache,
    _system_context_ttl,
    MOK_MODEL_NAME,
)

def get_system_context(agent_name: str, owner: str, owner_time: int = 0) -> str:
    """獲取主機環境信息 + Agent 工作目錄，帶緩存"""
    global _system_context_cache
    now = time.time()
    cached = _system_context_cache.get(agent_name)
    if cached and (now - cached[1]) < _system_context_ttl:
        return cached[0]

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

    # Agent 專屬目錄
    agent_dir = os.path.expanduser(f"~/.{MOKAGI_home}/agent/{agent_name}")

    system_info = f"""【系統環境】- 現在日期: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(owner_time or time.time()))}
- 操作系統: {os_info}
- CPU: {cpu_model}
- 內存總量: {mem_total}
- 根目錄磁盤使用（總量/已用/可用/使用率）: {disk_usage}
- 你的專屬目錄: {agent_dir}
- 你的配置文件: ~/.{MOKAGI_home}/.{agent_name}
- 你的靈魂文件: {agent_dir}/soul/"""

    return system_info
