# -*- coding: utf-8 -*-
"""dream.py — 「做夢」工具外殼（權限閘 + 心跳掛載）

核心邏輯在 core 的 做夢補丁 dream_core.py；本檔只做註冊與權限。

權限：只有該 agent 設定檔有 MOK_dream_EXP=1 才可用（其餘一律拒絕）。
"""

PLUGIN_INFO = {
    "command": "/dream",
    "icon": "🌙",
    "handler": "handle_dream",
    "description": "做夢：agent 定時讀自己的 logs 與 soul，沉澱經驗寫入 soul/EXP.md。需該 agent 設定檔有 MOK_dream_EXP=1。",
    "tool_schema": {
        "name": "dream",
        "description": "做夢（EXP 反思）：讓 agent 讀自己的 logs 與 soul 沉澱經驗、更新 soul/EXP.md，超過上限自動歸檔到 soul/EXP_archive/。僅對已啟用 MOK_dream_EXP=1 的 agent 生效。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "run", "dry"],
                    "description": "status=查看各 agent 啟用狀態；run=立刻對自己（或指定 agent）做夢；dry=只回報計畫、不呼叫 LLM、不寫檔"
                },
                "agent": {
                    "type": "string",
                    "description": "（可選）run / dry 的目標 agent，預設為呼叫者自己"
                }
            },
            "required": ["action"]
        }
    },
    "heartbeat": {"enabled": True, "handler": "heartbeat_handler", "interval": 60},
    "update": "20260921"
}

import os
import sys
import json
import logging
import threading
import time

HOME = os.path.expanduser("~")
MOK = os.path.join(HOME, ".mok")
CORE_DIR = os.path.join(MOK, "core")
PATCH_DIR = os.path.join(CORE_DIR, "做夢補丁")
AGENT_DIR = os.path.join(MOK, "agent")

logger = logging.getLogger(__name__)


def _core():
    for p in (CORE_DIR, PATCH_DIR):
        if p not in sys.path:
            sys.path.insert(0, p)
    import dream_core
    return dream_core


def _is_admin(agent_config):
    v = str((agent_config or {}).get("MOK_AUTO_APPROVE_ADMIN", "")).strip().lower()
    lv = str((agent_config or {}).get("MOK_AGENT_LV", "")).strip().lower()
    return v in ("1", "true", "yes", "on") or lv in ("vip", "admin", "管理員")


# 做夢執行中旗標：用「開始時間戳」而非 Lock，避免單次做夢卡死（LLM 無回應）
# 時把整個做夢系統永久鎖住。超過 _DREAM_MAX_S 視為卡死，自動放行下一個 agent。
_dream_state = {"started": 0.0}
_dream_guard = threading.Lock()
_DREAM_MAX_S = 1800.0


def _try_begin_dream():
    now = time.time()
    with _dream_guard:
        started = float(_dream_state.get("started") or 0.0)
        if started and (now - started) < _DREAM_MAX_S:
            return False
        if started:
            logger.warning("做夢疑似卡死 %.0f 秒，自動放行下一個 agent", now - started)
        _dream_state["started"] = now
        return True


def _end_dream():
    with _dream_guard:
        _dream_state["started"] = 0.0


def _run_dream(dc, agent_name, agent_config):
    try:
        from app_loop import run_async
        run_async(dc.dream_one(agent_name, agent_config))
    except Exception:
        logger.exception("做夢執行失敗: %s", agent_name)
    finally:
        _end_dream()


async def heartbeat_handler(agent_name, agent_config):
    """心跳：不阻塞心跳迴圈。
    先用同步 claim_slot 佔 gap 時段（保證一輪只放行一個 agent），
    再丟到背景執行緒真正做夢，避免拖住其他工具的心跳。"""
    try:
        dc = _core()
        if not dc.claim_slot(agent_name, agent_config):
            return
        if not _try_begin_dream():
            return
        threading.Thread(target=_run_dream, args=(dc, agent_name, agent_config),
                         name="mok-dream", daemon=True).start()
    except Exception:
        logger.exception("做夢心跳派發失敗: %s", agent_name)


async def handle_dream(args, chat_id=None, agent_config=None):
    dc = _core()
    from config import load_agent_config

    action, target = "status", None
    if isinstance(args, dict):
        action = str(args.get("action") or "status").strip().lower()
        target = args.get("agent")
    elif isinstance(args, str):
        parts = args.strip().split()
        if parts:
            action = parts[0].lower()
        if len(parts) > 1:
            target = parts[1]

    caller = (agent_config or {}).get("MOK_AGENT_NAME") or ""

    if action == "status":
        lines = ["🌙 做夢（EXP 反思）狀態", ""]
        for n in dc.list_agents():
            try:
                cf = load_agent_config(n)
            except Exception:
                continue
            st = dc._read_json(dc._state_path(os.path.join(AGENT_DIR, n)))
            flag = "啟用" if dc.is_enabled(cf) else "未啟用"
            lines.append("- %s：%s｜上次做夢：%s" % (n, flag, st.get("last_run") or "（無）"))
        lines.append("")
        lines.append("啟用方式：在該 agent 設定檔加 MOK_dream_EXP=1")
        return "\n".join(lines)

    if action in ("run", "dry"):
        name = target or caller
        if not name:
            return "❌ 無法判定 agent。"
        if target and target != caller and not _is_admin(agent_config):
            return "⛔ 只有 admin 能對其他 agent 執行做夢。"
        cf = load_agent_config(name)
        if not dc.is_enabled(cf):
            return "⛔ agent「%s」未啟用做夢。請在其設定檔加 MOK_dream_EXP=1。" % name
        from app_loop import run_async
        res = run_async(dc.dream_one(name, cf, dry_run=(action == "dry"), force=True))
        return "🌙 做夢結果（%s）：\n%s" % (name, json.dumps(res, ensure_ascii=False, indent=2))

    return "❌ 未知動作：%s（可用 status / run / dry）" % action

