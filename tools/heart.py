# ------------------------------------------------------------------------------------ #
# heart.py - 純心跳排程引擎（不綁定任何業務邏輯）
# 設計：動態掃描所有工具模組的 PLUGIN_INFO["heartbeat"]，自動執行符合契約的處理器。
# 2026-07-13 重構版
# ------------------------------------------------------------------------------------ #

PLUGIN_INFO = {
    "command": "/heart",
    "icon": "❤️",
    "handler": "handle_heart",
    "description": "心跳引擎：掃描所有啟用心跳的工具並自動執行。",
    "tool_schema": {
        "name": "heart",
        "description": "查詢心跳狀態與手動觸發掃描",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "scan"],
                    "description": "status: 顯示註冊的心跳工具; scan: 手動觸發一次掃描"
                }
            }
        }
    },
    "update": "20260713"
}

import os
import time
import threading
import logging
import asyncio
from typing import List, Dict, Any

# 導入工具管理
import tool_handler
from config import load_agent_config

logger = logging.getLogger(__name__)

# ---------- 全域設定 ----------
HEART_ENABLED = True   # 可從環境變數 MOK_HEART_ENABLED 讀取
_HEART_INTERVAL = 60   # 全域掃描間隔（秒）
_heart_stop_event = threading.Event()
_heart_thread = None

# ---------- 輔助函數 ----------
def get_agent_dirs() -> List[str]:
    """回傳所有 Agent 目錄路徑"""
    base = os.path.expanduser("~/.mok/agent")
    if not os.path.exists(base):
        return []
    return [os.path.join(base, name) for name in os.listdir(base)
            if os.path.isdir(os.path.join(base, name))]

# ---------- 持久化事件循環 ----------
_heart_loop = None

def get_heart_loop():
    """取得心跳線程的持久事件循環（自動修復已關閉的 loop）"""
    global _heart_loop
    if _heart_loop is None or _heart_loop.is_closed():
        _heart_loop = asyncio.new_event_loop()
    return _heart_loop

# ---------- 核心掃描（執行緒安全） ----------
def scan_and_execute():
    """同步包裝，在執行緒中執行非同步掃描"""
    loop = get_heart_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_async_scan())
    except RuntimeError as e:
        if "closed" in str(e).lower():
            logger.warning("心跳事件循環已關閉，自動重建")
            global _heart_loop
            _heart_loop = asyncio.new_event_loop()
            loop = _heart_loop
            asyncio.set_event_loop(loop)
            try:
                loop.run_until_complete(_async_scan())
            except Exception as e2:
                logger.exception("心跳掃描非同步異常（重建後重試失敗）")
        else:
            logger.exception("心跳掃描非同步異常")
    except Exception as e:
        logger.exception("心跳掃描非同步異常")

async def _async_scan():
    """非同步掃描：遍歷所有 Agents 與已註冊的心跳工具"""
    tools = tool_handler.get_tools()
    
    # 收集所有啟用心跳的工具及其處理器
    heartbeat_handlers = []   # 每個元素: (handler_func, interval)
    for mod_name, mod in tools.items():
        if not hasattr(mod, "PLUGIN_INFO"):
            continue
        hb = mod.PLUGIN_INFO.get("heartbeat")
        if hb and hb.get("enabled"):
            handler_name = hb.get("handler")
            if handler_name and hasattr(mod, handler_name):
                handler = getattr(mod, handler_name)
                interval = hb.get("interval", _HEART_INTERVAL)
                heartbeat_handlers.append((handler, interval))
    
    if not heartbeat_handlers:
        logger.debug("目前沒有任何工具啟用心跳")
        return

    # 對每個 Agent 執行所有心跳處理器
    for agent_dir in get_agent_dirs():
        agent_name = os.path.basename(agent_dir)
        try:
            agent_config = load_agent_config(agent_name)
        except Exception as e:
            logger.warning(f"載入 Agent {agent_name} 配置失敗: {e}")
            continue

        for handler, _ in heartbeat_handlers:
            try:
                await handler(agent_name, agent_config)
            except Exception as e:
                logger.exception(
                    f"執行心跳處理器 {handler.__name__} 於 Agent {agent_name} 失敗"
                )
                # 繼續下一個處理器，不中斷整個掃描
                continue

# ---------- 後臺執行緒 ----------
def heart_loop():
    logger.info("❤️ 心跳引擎啟動，間隔 %d 秒", _HEART_INTERVAL)
    while not _heart_stop_event.is_set():
        try:
            scan_and_execute()
        except Exception as e:
            logger.exception(f"心跳掃描發生異常，將繼續執行: {e}")
        _heart_stop_event.wait(_HEART_INTERVAL)
    logger.info("❤️ 心跳引擎正常停止")

def start_heart_thread():
    global _heart_thread
    if not HEART_ENABLED:
        logger.info("心跳已停用（HEART_ENABLED=False）")
        return
    
    # 如果已有線程且正在運行，則不重複建立
    if _heart_thread is not None and _heart_thread.is_alive():
        logger.debug("心跳線程已在運行，跳過")
        return
    
    # 如果有舊線程但已死亡，或從未建立，則新建
    if _heart_thread is not None and not _heart_thread.is_alive():
        logger.info("心跳線程已死亡，重新啟動...")
    
    # 強制清除事件，確保線程能正常啟動
    _heart_stop_event.clear()
    _heart_thread = threading.Thread(target=heart_loop, daemon=True)
    _heart_thread.start()
    logger.info("❤️ 心跳線程已啟動")

def stop_heart_thread():
    global _heart_loop
    _heart_stop_event.set()
    if _heart_thread:
        _heart_thread.join(timeout=10)
        logger.info("心跳線程已停止")
    if _heart_loop and not _heart_loop.is_closed():
        try:
            _heart_loop.close()
        except Exception:
            pass
        _heart_loop = None

# ---------- /heart 管理指令 ----------
async def handle_heart(args, chat_id: str = None, agent_config: dict = None):
    """處理 /heart 命令（僅提供狀態查詢與手動掃描）"""
    action = ""
    if isinstance(args, dict):
        action = args.get("action", "")
    elif isinstance(args, str):
        parts = args.strip().split()
        action = parts[0].lower() if parts else ""

    if action == "status":
        tools = tool_handler.get_tools()
        lines = ["❤️ **已註冊的心跳工具**"]
        for name, mod in tools.items():
            if hasattr(mod, "PLUGIN_INFO"):
                hb = mod.PLUGIN_INFO.get("heartbeat")
                if hb and hb.get("enabled"):
                    lines.append(f"- {name}: {hb.get('handler', 'N/A')} (間隔 {hb.get('interval', _HEART_INTERVAL)}s)")
        if len(lines) == 1:
            lines.append("（無）")
        return "\n".join(lines)

    elif action == "scan":
        # 立即執行一次掃描（非同步）
        await _async_scan()
        return "✅ 手動掃描完成"

    else:
        return (
            "❤️ 心跳引擎\n"
            "可用指令：\n"
            "  /heart status   查看已啟用心跳的工具\n"
            "  /heart scan     手動觸發一次掃描\n"
            "（自動掃描每 60 秒執行一次）"
        )

# ---------- 啟動（模組載入時自動啟動） ----------
start_heart_thread()
import atexit
atexit.register(stop_heart_thread)