#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
config.py - 統一配置管理模塊

功能：
- 定義 MOKAGI_home 常量（系統根目錄名）
- 提供 load_agent_config() 加載指定 Agent 的配置（從 ~/.mok/.agent 及 soul/ 目錄）
- 提供 get_agent_config() 異步緩存版
- 導出當前 Agent 的常用配置變量（模型、API、參數等）

依賴：
- 僅使用標準庫（os, sys, re, json, asyncio）

使用方式（在其他模塊中）：
    from config import MOKAGI_home, _agent_config, MOK_MODEL_NAME, get_agent_config
"""

import os
import sys
import re
import json
import asyncio
import threading
import logging
from typing import Dict, Optional

# ================== 根目錄常量 ==================
MOKAGI_home = "mok"
os.environ["MOKAGI_HOME"] = MOKAGI_home   # 確保所有子進程繼承

# ================== Agent 配置緩存 ==================
_agent_config_cache = {}          # {agent_name: config_dict}
_config_cache_lock = threading.Lock()

def get_config_lock():
    return _config_cache_lock

# ================== 配置加載函數 ==================
def load_agent_config(agent_name: str = None) -> Dict[str, str]:
    """
    加載當前 agent 的配置（通常位於 ~/.mok/.agent 或通過環境變量指定）
    如果未指定 agent_name，則嘗試從環境變量 MOK_AGENT_NAME 或 PM2 程序名推斷。
    返回配置字典，包含 MOK_MODEL_NAME, MOK_MODEL_url, 各項參數等。
    """
    config = {}
    if not agent_name:
        agent_name = os.environ.get("MOK_AGENT_NAME")
        if not agent_name:
            proc_name = os.environ.get("PM2_PROGRAM_NAME") or sys.argv[0]
            match = re.search(rf'{MOKAGI_home}_(.+)$', proc_name)
            agent_name = match.group(1) if match else "default"

    mokagi_name = MOKAGI_home
    config_path = os.path.join(os.path.expanduser("~"), ".mok", "agent", agent_name, f".{agent_name}")
    if not os.path.exists(config_path):
        # 返回默認配置
        return {
            "MOK_MODEL_NAME": "minimax-m3:cloud",
            "MOK_MODEL_url": "http://localhost:11434/api/generate",
            "MOK_num_ctx": "16384",
            "MOK_num_predict": "8192",
            "MOK_temperature": "0.8",
            "MOK_top_p": "0.9",
            "MOK_top_k": "50",
            "MOK_repeat_penalty": "1.5",
            "MOK_presence_penalty": "0.6",
            "MOK_frequency_penalty": "0.5",
            "MOK_MAX_HISTORY_ROUNDS": "6",
            "MOK_MEMORY_RECALL_COUNT": "3",
        }
    with open(config_path, 'r', encoding='utf-8') as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, val = line.split('=', 1)
                config[key.strip()] = val.strip()

    current_model = config.get("MOK_CURRENT_MODEL")
    if current_model:
        current_model = current_model.strip()
        config["MOK_MODEL_NAME"] = current_model
        suffix = None
        # 優先匹配帶後綴的（排除無後綴的 MOK_MODEL_NAME）
        for key, val in config.items():
            if key.startswith("MOK_MODEL_NAME") and key != "MOK_MODEL_NAME" and val == current_model:
                suffix = key.replace("MOK_MODEL_NAME", "")
                break
        # 若沒找到帶後綴的，再嘗試匹配無後綴的
        if suffix is None and config.get("MOK_MODEL_NAME") == current_model:
            suffix = ""
        if suffix is not None:
            api_key = f"MOK_MODEL_url{suffix}"
            token_key = f"MOK_MODEL_token{suffix}"
            if api_key in config:
                config["MOK_MODEL_url"] = config[api_key]
            config["MOK_MODEL_token"] = config.get(token_key, "").strip()
        else:
            config["MOK_MODEL_token"] = ""

    # ===== 讀取靈魂文件 =====
    soul_dir = os.path.join(os.path.expanduser("~"), ".mok", "agent", agent_name, "soul")
    
    # 讀取 soul/user.md
    user_md_path = os.path.join(soul_dir, "user.md")
    if os.path.exists(user_md_path):
        with open(user_md_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, val = line.split('=', 1)
                    config[key.strip()] = val.strip()
    
    # 讀取 soul/soul.md (或 soul.md)
    soul_md_path = os.path.join(soul_dir, "soul.md")
    if os.path.exists(soul_md_path):
        with open(soul_md_path, 'r', encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith('#'):
                    continue
                if '=' in line:
                    key, val = line.split('=', 1)
                    config[key.strip()] = val.strip()
    
    return config

async def get_agent_config(agent_name: str, force_reload: bool = False) -> dict:
    """異步獲取 Agent 配置，帶緩存。若 force_reload=True 則強制重新從磁盤加載。"""
    with get_config_lock():
        if force_reload or agent_name not in _agent_config_cache:
            old_env = os.environ.get("MOK_AGENT_NAME")
            os.environ["MOK_AGENT_NAME"] = agent_name
            try:
                config = load_agent_config(agent_name)
                _agent_config_cache[agent_name] = config
            finally:
                if old_env is not None:
                    os.environ["MOK_AGENT_NAME"] = old_env
                else:
                    os.environ.pop("MOK_AGENT_NAME", None)
        return _agent_config_cache[agent_name]

# ================== 加載當前 Agent 配置（默認） ==================
_agent_config = load_agent_config()

# 導出常用配置變量（方便其他模塊直接引用）
MOK_MODEL_NAME = _agent_config.get("MOK_MODEL_NAME", "minimax-m3:cloud")
MOK_AGENT_NAME = _agent_config.get("MOK_AGENT_NAME", "助手")
OLLAMA_API = _agent_config.get("MOK_MODEL_url", "http://localhost:11434/api/generate")
OLLAMA_OPTIONS = {
    "num_ctx": int(_agent_config.get("MOK_num_ctx", 16384)),
    "num_predict": int(_agent_config.get("MOK_num_predict", 8192)),
    "temperature": float(_agent_config.get("MOK_temperature", 0.8)),
    "top_p": float(_agent_config.get("MOK_top_p", 0.9)),
    "top_k": int(_agent_config.get("MOK_top_k", 50)),
    "repeat_penalty": float(_agent_config.get("MOK_repeat_penalty", 1.5)),
    "presence_penalty": float(_agent_config.get("MOK_presence_penalty", 0.6)),
    "frequency_penalty": float(_agent_config.get("MOK_frequency_penalty", 0.5)),
}
MAX_HISTORY_ROUNDS = int(_agent_config.get("MOK_MAX_HISTORY_ROUNDS", 6))
MEMORY_RECALL_COUNT = int(_agent_config.get("MOK_MEMORY_RECALL_COUNT", 3))

MOK_max_tokens = int(_agent_config.get("MOK_max_tokens", 8192))