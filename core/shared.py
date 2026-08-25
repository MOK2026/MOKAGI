"""shared.py - mokagi sharing globals"""
import asyncio
import json
import os
import sys
import threading
MOKAGI_home = "mok"
os.environ.setdefault("MOKAGI_HOME", MOKAGI_home)
TASK_COMPLETE_MARKER = "TASK_COMPLETE: true"
TASK_COMPLETE_ALT = "任務完成"
_model_timeout = 300.0
TOOLS_DIR = os.path.expanduser("~/.mok/tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)
_agent_config_cache = {}
_config_cache_lock = threading.Lock()
_system_context_cache = {}
_system_context_ttl = 60
HISTORY_DB_PATH = os.path.expanduser("~/.mok/.memory/conversation_history.db")
EXPERIENCE_DB_PATH = os.path.expanduser("~/.mok/.memory/conversation_history.db")
MOK_MODEL_NAME = None
