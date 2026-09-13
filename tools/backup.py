#!/usr/bin/env python3
# ------------------------------------------------------------------------------------ #
# backup.py - 備份中心工具
# 將備份邏輯包裝為 /backup 指令，Agent 可隨時執行/查看 MOK 系統備份
# 2026-08-22
# ------------------------------------------------------------------------------------ #
import os
import subprocess
from datetime import datetime, timedelta, timezone

MOK = os.path.expanduser("~/.mok")
BK = os.path.join(MOK, "backups")
LOG = os.path.join(BK, "backup_cron.log")
SCRIPT = os.path.join(MOK, "tools", "scripts", "backup.sh")


def _admin_tz():
    """統一使用 MOK_ADMIN_TIME_ZONE (+8) 顯示時間"""
    off = 8
    try:
        with open(os.path.join(MOK, "env.env"), encoding="utf-8", errors="replace") as f:
            for ln in f:
                ln = ln.strip()
                if ln.startswith("MOK_ADMIN_TIME_ZONE="):
                    v = ln.split("=", 1)[1].strip()
                    if v:
                        off = int(v)
                    break
    except Exception:
        pass
    return timezone(timedelta(hours=off))

PLUGIN_INFO = {
    "command": "/backup",
    "icon": "📦",
    "handler": "handle_backup",
    "description": "備份中心：執行備份(run)、列出備份(list)、查看狀態(status)、清理舊備份(cleanup)。",
    "intent_keywords": [
        ("/備份", "/backup run"),
        ("/備份狀態", "/backup status"),
        ("/備份列表", "/backup list"),
        ("/清理備份", "/backup cleanup"),
    ],
    "tool_schema": {
        "name": "backup",
        "description": "MOK 系統備份管理：立即備份(run)、查看備份列表(list)、查看狀態(status)、清理舊備份(cleanup，預設保留最近7份)。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["run", "list", "status", "cleanup"],
                    "description": "run=立即執行備份；list=列出備份檔案；status=查看備份狀態；cleanup=清理舊備份（保留最近7份）"
                }
            },
            "required": ["action"]
        }
    }
}


def _fmt_size(n):
    for unit in ["B", "KB", "MB", "GB"]:
        if n < 1024:
            return f"{n:.1f}{unit}"
        n /= 1024
    return f"{n:.1f}TB"


def _list_files():
    if not os.path.isdir(BK):
        return []
    return sorted(
        [f for f in os.listdir(BK) if f.startswith("mok_backup_") and f.endswith(".tar.gz")],
        reverse=True,
    )


def _tail_log(n=6):
    if not os.path.exists(LOG):
        return "(無日誌)"
    with open(LOG, "r", encoding="utf-8", errors="replace") as f:
        lines = f.readlines()
    return "".join(lines[-n:])


def _run_backup():
    if not os.path.exists(SCRIPT):
        return f"❌ 備份腳本不存在: {SCRIPT}"
    try:
        proc = subprocess.run(
            ["bash", SCRIPT], capture_output=True, text=True, timeout=1800
        )
    except subprocess.TimeoutExpired:
        return "⚠️ 備份超過 30 分鐘未完成，已中止（可稍後再試 /backup run）"
    tail = _tail_log(6)
    if proc.returncode == 0:
        return f"✅ 備份完成（rc=0）\n{tail}"
    return f"⚠️ 備份完成但含檔案變動警告（rc={proc.returncode}，屬正常）\n{tail}"


def _list_backups():
    files = _list_files()
    if not files:
        return "📦 目前沒有任何備份檔案"
    lines = [f"📦 備份列表（共 {len(files)} 份，最新在前）:"]
    total = 0
    for i, f in enumerate(files, 1):
        fp = os.path.join(BK, f)
        size = os.path.getsize(fp)
        total += size
        mtime = datetime.fromtimestamp(os.path.getmtime(fp), _admin_tz()).strftime("%Y-%m-%d %H:%M")
        lines.append(f"  {i}. {f}  ({_fmt_size(size)})  {mtime}")
    lines.append(f"合計: {_fmt_size(total)}")
    return "\n".join(lines)


def _status():
    out = []
    if os.path.exists(LOG):
        with open(LOG, "r", encoding="utf-8", errors="replace") as f:
            lines = [l.strip() for l in f.readlines() if l.strip()]
        last = next((l for l in reversed(lines) if l.startswith("=====") and "start" in l), "N/A")
        result = next((l for l in reversed(lines) if l.startswith("done") or l.startswith("fail") or l.startswith("count:")), "N/A")
        out.append(f"🗂 最近執行: {last}")
        out.append(f"  結果: {result}")
    files = _list_files()
    out.append(f"📦 備份份數: {len(files)}")
    if files:
        newest = files[0]
        fp = os.path.join(BK, newest)
        out.append(f"🕐 最新備份: {newest} ({_fmt_size(os.path.getsize(fp))})")
    if os.path.exists(LOG):
        out.append(f"📄 日誌位置: {LOG}")
    return "\n".join(out)


def _cleanup(keep=7):
    try:
        keep = int(keep)
    except (TypeError, ValueError):
        keep = 7
    if keep < 1:
        keep = 1
    files = _list_files()
    if len(files) <= keep:
        return f"🗑 無需清理（目前 {len(files)} 份，保留上限 {keep} 份）"
    remove = files[keep:]
    for f in remove:
        try:
            os.remove(os.path.join(BK, f))
        except OSError as e:
            return f"❌ 刪除失敗 {f}: {e}"
    return f"🗑 已清理 {len(remove)} 份舊備份，保留最近 {keep} 份"


async def handle_backup(args, mode="command", user_id=None, agent_name=None, **kwargs):
    if isinstance(args, dict):
        action = (args.get("action") or "status").lower()
    else:
        parts = (args or "").split(maxsplit=1)
        action = (parts[0] or "status").lower()
        extra = parts[1] if len(parts) > 1 else ""

    if action in ("run", "now", "backup"):
        return _run_backup()
    if action in ("list", "ls"):
        return _list_backups()
    if action in ("cleanup", "clean", "rm"):
        return _cleanup(extra)
    return _status()
