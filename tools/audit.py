# -*- coding: utf-8 -*-
# tools/audit.py — 動作審計查詢工具（讀 core/audit_layer.py）
# 提供 /audit recent | stats | policy
import os
import sys
from typing import Optional, Dict


def _import_audit():
    sys.path.insert(0, os.path.expanduser("~/.mok/core"))
    import audit_layer
    return audit_layer


PLUGIN_INFO = {
    "command": "/audit",
    "icon": "AUDIT",
    "handler": "handle_audit",
    "description": "動作審計：查詢工具呼叫紀錄、來源（person/routine/handoff）與政策攔截統計。",
    "intent_keywords": [
        ("/審計", "/audit recent"),
        ("/動作紀錄", "/audit recent"),
        ("/誰動了什麼", "/audit recent"),
        ("/審計統計", "/audit stats"),
    ],
    "tool_schema": {
        "name": "audit",
        "description": "查詢 mokagi 動作審計紀錄。action: recent/stats/policy。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["recent", "stats", "policy"], "description": "recent=最近紀錄, stats=統計, policy=顯示政策檔"},
                "limit": {"type": "integer", "description": "recent 回傳筆數（預設20）"},
                "initiator": {"type": "string", "description": "過濾來源 person/routine/handoff"},
                "tool": {"type": "string", "description": "過濾工具名"},
                "agent": {"type": "string", "description": "過濾 agent 名"},
                "days": {"type": "integer", "description": "stats 統計天數（預設1）"},
            },
            "required": ["action"],
        },
    },
}


def _parse(args):
    if isinstance(args, dict):
        return args
    d = {}
    if isinstance(args, str):
        parts = args.split()
        if parts:
            d["action"] = parts[0]
            for p in parts[1:]:
                if "=" in p:
                    k, v = p.split("=", 1)
                    d[k.strip()] = v.strip()
    return d


async def handle_audit(args, chat_id: str = None, agent_config: Optional[Dict] = None) -> str:
    a = _import_audit()
    d = _parse(args)
    action = (d.get("action") or "recent").lower()

    if action == "stats":
        days = int(d.get("days", 1) or 1)
        s = a.stats(days)
        lines = ["審計統計（最近 %d 天）" % days,
                 "總計：%d 筆" % s["total"],
                 "被政策攔截：%d 筆" % s["denied"],
                 "執行失敗：%d 筆" % s["failed"],
                 "來源分佈："]
        for k, v in s["by_initiator"]:
            lines.append("  - %s：%d" % (k, v))
        return chr(10).join(lines)

    if action == "policy":
        path = a.POLICY_FILE
        lines = ["政策檔：%s" % path,
                 "enforce=%s failclosed=%s" % (a.AUDIT_ENFORCE, a.POLICY_FAILCLOSED), ""]
        try:
            with open(path, "r", encoding="utf-8") as f:
                lines.append(f.read())
        except Exception as e:
            lines.append("(讀取失敗：%s)" % e)
        return chr(10).join(lines)

    limit = int(d.get("limit", 20) or 20)
    kw = {}
    for k in ("initiator", "tool", "agent"):
        if d.get(k):
            kw[k] = d[k]
    return "最近動作紀錄（newest first）" + chr(10) + a.format_recent(limit=limit, **kw)
