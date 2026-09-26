#!/usr/bin/env python3
# ------------------------------------------------------------------------------------ #
# backup.py - 備份中心工具
# 將備份邏輯包裝為 /backup 指令：執行/查看/清理備份、系統還原點(points)、一鍵還原(restore)
# 2026-08-22 建立；2026-09-17 加入系統還原點
# ------------------------------------------------------------------------------------ #
import os
import subprocess
from datetime import datetime, timedelta, timezone

MOK = os.path.expanduser("~/.mok")
BK = os.path.join(MOK, "backups")
LOG = os.path.join(BK, "backup_cron.log")
SCRIPT = os.path.join(MOK, "tools", "scripts", "backup.sh")
RESTORE = os.path.join(MOK, "tools", "scripts", "restore.sh")


def _admin_tz():
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
    "description": "備份中心：執行備份(run)、備份列表(list)、系統還原點(points)、一鍵還原(restore)、狀態(status)、清理舊備份(cleanup)。",
    "intent_keywords": [
        ("/備份", "/backup run"),
        ("/備份狀態", "/backup status"),
        ("/備份列表", "/backup list"),
        ("/清理備份", "/backup cleanup"),
        ("/還原點", "/backup points"),
        ("/一鍵還原", "/backup restore latest"),
        ("/系統還原", "/backup restore latest"),
    ],
    "tool_schema": {
        "name": "backup",
        "description": "MOK 系統備份與還原：run=立即備份（含所有 cron 與執行中 pm2）；list=備份列表；points=系統還原點列表；restore=一鍵還原到指定備份並重啟所有服務；status=狀態；cleanup=清理舊備份（保留最近7份）。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["run", "list", "points", "restore", "status", "cleanup"],
                    "description": "run=立即備份；list=備份列表；points=還原點列表；restore=一鍵還原（可搭配 target）；status=狀態；cleanup=清理舊備份"
                },
                "target": {
                    "type": "string",
                    "description": "restore 用：還原點編號 / 檔名 / latest（預設最新）。可在最後加 fast 跳過還原前安全備份"
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


def _load_meta(fn):
    import json
    p = os.path.join(BK, fn + ".meta.json")
    if not os.path.exists(p):
        return None
    try:
        with open(p, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _run_backup():
    if not os.path.exists(SCRIPT):
        return f"❌ 備份腳本不存在: {SCRIPT}"
    try:
        proc = subprocess.run(["bash", SCRIPT], capture_output=True, text=True, timeout=1800)
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


def _points():
    files = _list_files()
    if not files:
        return "🕐 尚無系統還原點（請先執行 /backup run 建立）"
    lines = [f"🕐 系統還原點（共 {len(files)} 個，最新在前）:"]
    for i, f in enumerate(files, 1):
        fp = os.path.join(BK, f)
        mtime = datetime.fromtimestamp(os.path.getmtime(fp), _admin_tz()).strftime("%Y-%m-%d %H:%M")
        m = _load_meta(f)
        extra = ""
        if m:
            extra = f" ⟵ cron {m.get('cron_count', '?')} 項 · pm2 {m.get('pm2_count', '?')} 個"
        lines.append(f"  {i}. {f}  ({_fmt_size(os.path.getsize(fp))})  {mtime}{extra}")
    lines.append("")
    lines.append("↩️ 一鍵還原：/backup restore <編號|檔名|latest>")
    return "\n".join(lines)


def _restore(target):
    if not os.path.exists(RESTORE):
        return f"❌ 還原腳本不存在: {RESTORE}"
    files = _list_files()
    if not files:
        return "❌ 目前沒有任何備份，無法還原"
    t = (target or "").strip()
    fast = False
    if t.endswith(" fast") or t.endswith(" --no-safety"):
        fast = True
        t = t.rsplit(" ", 1)[0].strip()
    if t in ("", "latest", "最新"):
        arch = files[0]
    elif t.isdigit() and 1 <= int(t) <= len(files):
        arch = files[int(t) - 1]
    else:
        arch = os.path.basename(t)
        if not os.path.exists(os.path.join(BK, arch)):
            return f"❌ 找不到備份: {t}\n用 /backup points 查看可用還原點"
    cmd = ["setsid", "bash", RESTORE, arch]
    if fast:
        cmd.append("--no-safety")
    try:
        with open(os.path.join(BK, "restore_run.out"), "a") as out:
            subprocess.Popen(cmd, stdout=out, stderr=subprocess.STDOUT,
                             stdin=subprocess.DEVNULL, start_new_session=True)
    except Exception as e:
        return f"❌ 無法啟動還原: {e}"
    tip = "（快速模式：略過還原前安全備份）" if fast else "（會先建立還原前安全備份，可再回退）"
    return (f"🔄 已啟動一鍵還原 → {arch} {tip}\n"
            f"⏳ 約 6 秒後將停止所有 pm2、覆蓋系統檔案、還原 cron 並重啟所有服務。\n"
            f"本體 mok_agi 會在還原完成後自動重啟。\n"
            f"進度可看 /backup status 或 backups/restore.log")


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
    lr = os.path.join(BK, "last_restore.txt")
    if os.path.exists(lr):
        try:
            out.append(f"↩️ 上次還原: {open(lr, encoding='utf-8').read().strip()}")
        except Exception:
            pass
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
            mp = os.path.join(BK, f + ".meta.json")
            if os.path.exists(mp):
                os.remove(mp)
        except OSError as e:
            return f"❌ 刪除失敗 {f}: {e}"
    return f"🗑 已清理 {len(remove)} 份舊備份，保留最近 {keep} 份"


async def handle_backup(args, mode="command", user_id=None, agent_name=None, **kwargs):
    if isinstance(args, dict):
        action = (args.get("action") or "status").lower()
        extra = (args.get("target") or "").strip()
    else:
        parts = (args or "").split(maxsplit=1)
        action = (parts[0] or "status").lower()
        extra = parts[1] if len(parts) > 1 else ""

    if action in ("run", "now", "backup"):
        return _run_backup()
    if action in ("list", "ls"):
        return _list_backups()
    if action in ("points", "point", "restore-point", "還原點"):
        return _points()
    if action in ("restore", "revert", "rollback", "還原"):
        return _restore(extra)
    if action in ("cleanup", "clean", "rm"):
        return _cleanup(extra)
    return _status()
