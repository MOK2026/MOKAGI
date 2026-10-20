# ------------------------------------------------------------------------------------ #
# cron_tool.py
# 讓 Agent 能夠管理系統 Crontab 定時任務
# 指令：/cron list | add | delete | log
# 高風險操作（add / delete）需經主人二次確認
# 20260706
# ------------------------------------------------------------------------------------ #

PLUGIN_INFO = {
    "command": "/cron",
    "icon": "⏰",
    "handler": "handle_cron",
    "description": "系統 Cron 定時任務管理：查看(list)、新增(add)、刪除(delete)、查看日誌(log)。",
    "intent_keywords": [
        ("/查看排程", "/cron list"),
        ("/新增排程", "/cron add"),
        ("/刪除排程", "/cron delete"),
        ("/排程日誌", "/cron log"),
    ],
    "naturalize_func": "naturalize_cron_result",
    "tool_schema": {
        "name": "cron",
        "description": (
            "管理系統 Crontab 定時任務。支援四個動作：list, add, delete, log。\n\n"
            "- **list**：列出目前所有的 cron 任務。\n"
            "- **add**：新增 cron 任務（格式需為完整 cron 表達式 + 指令）。\n"
            "  範例：`/cron add \"0 8 * * * /home/ubuntu/backup.sh\"`\n"
            "- **delete**：刪除指定 cron 任務（需提供任務編號，從 list 取得）。\n"
            "  範例：`/cron delete 3`（刪除第 3 個任務）\n"
            "- **log**：查看最近的 cron 執行日誌（預設 20 行）。\n"
            "  範例：`/cron log 50`（查看最近 50 行）"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "add", "delete", "log"],
                    "description": "要執行的動作。"
                },
                "args": {
                    "type": "string",
                    "description": "參數。add 需要完整 cron 指令；delete 需要任務編號；log 可選行數。"
                }
            },
            "required": ["action"]
        }
    },
    "update": "20260706"
}

import os
import re
import json
import time
import hashlib
import subprocess
import logging
from typing import Dict, Optional

# ===== 確認流程：統一使用 confirm_manager（admin 與 cron 共用「同一個實例」）=====
import sys as _sys
import importlib.util as _ilu
if "mok_confirm_manager" not in _sys.modules:
    _cm_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "confirm_manager.py")
    _spec = _ilu.spec_from_file_location("mok_confirm_manager", _cm_path)
    _mcm = _ilu.module_from_spec(_spec)
    _sys.modules["mok_confirm_manager"] = _mcm
    _spec.loader.exec_module(_mcm)
_confirm = _sys.modules["mok_confirm_manager"]


def _cron_pending_dir() -> str:
    return _confirm.PENDING_DIR


def generate_token(chat_id: str, action: str, args: str) -> str:
    """生成一次性確認 token（與 admin 共用同一套）。"""
    return _confirm.generate_token(chat_id, "cron", action, args)


def has_pending_cron_token(token: str) -> bool:
    """（相容舊介面）確認碼是否仍在等待確認。"""
    return _confirm.is_pending(token)


def _cron_confirm_warning(warning: str, token: str) -> str:
    """組出 CONFIRM_SPLIT 警告訊息（TTL 提示與 admin 一致）。"""
    hint = _confirm.ttl_hint(_confirm.get_ttl())
    return (
        "CONFIRM_SPLIT:" + warning + "\n"
        "🔐 此確認碼用於授權執行上方操作（僅限您本人確認）。若您未發起此操作，請直接忽略。\n"
        "請在 " + hint + " 內發送確認碼以執行：\n"
        "---CONFIRM_SPLIT---\n"
        "/admin confirm " + token
    )


def is_admin(chat_id: str, agent_config: dict = None) -> bool:
    """owner/admin 判定（2026-10-03 by 凜）：一律走 owner_auth 唯一真相。

    舊版對任何非數字 chat_id 一律放行（網頁全員變 admin）且用字串比對撞名；
    現在以 member.db users.is_admin 為準，數值 chat_id 只認明確的 ADMIN_CHAT_ID。
    """
    if agent_config is None:
        import mokagi
        agent_config = mokagi._agent_config
    try:
        from owner_auth import is_admin_identity
        return bool(is_admin_identity(chat_id, agent_config))
    except Exception:
        return False

# ------------------ 核心 Crontab 操作 ------------------

def _get_current_crontab() -> list:
    """讀取目前使用者的 crontab，回傳行列表"""
    try:
        result = subprocess.run(
            ["crontab", "-l"],
            capture_output=True,
            text=True,
            timeout=5
        )
        if result.returncode != 0:
            # 可能是沒有 crontab
            if "no crontab" in result.stderr.lower():
                return []
            return []
        lines = result.stdout.splitlines()
        # 過濾掉註解和空行（但保留用於顯示）
        return lines
    except Exception as e:
        logging.error(f"讀取 crontab 失敗: {e}")
        return []

def _write_crontab(lines: list) -> tuple:
    """寫入 crontab，回傳 (成功與否, 錯誤訊息)"""
    try:
        # 將行列表組成字串
        content = "\n".join(lines)
        if content and not content.endswith("\n"):
            content += "\n"
        proc = subprocess.run(
            ["crontab", "-"],
            input=content,
            capture_output=True,
            text=True,
            timeout=5
        )
        if proc.returncode != 0:
            return False, proc.stderr
        return True, ""
    except Exception as e:
        return False, str(e)

def _parse_cron_lines(lines: list) -> list:
    """解析 cron 行，回傳帶編號的任務清單（跳過註解與環境變數行）

    2026-10-19 修（agent mokagi說明）：
      1) 支援 @reboot / @daily / @hourly 等特殊排程。舊版把整條指令都當成「排程欄位」，
         結果指令只剩尾巴的 `2>&1`，完全看不懂（真實案例：第 11 條復活腳本）。
      2) schedule 固定為「時間欄位」，command 固定為「要跑的指令」。
      3) 編號 idx 仍然按 crontab 的行號計，這樣 /cron 的編號可以直接對應刪除操作。
    """
    tasks = []
    for idx, line in enumerate(lines, 1):
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith('#'):
            continue
        parts = line_stripped.split()
        # 環境變數行（例：SHELL=/bin/bash）不是任務
        first = parts[0]
        if "=" in first and not first.startswith("/") and first.split("=")[0].isupper():
            continue
        if first.startswith("@"):
            command = " ".join(parts[1:])
            if not command:
                continue
            tasks.append({
                "number": idx,
                "raw": line_stripped,
                "schedule": first,
                "command": command,
                "special": True,
            })
            continue
        if len(parts) >= 6:  # 5個時間 + 至少1個指令
            tasks.append({
                "number": idx,
                "raw": line_stripped,
                "schedule": " ".join(parts[:5]),
                "command": " ".join(parts[5:]),
                "special": False,
            })
    return tasks


# ------------------------------------------------------------------------------------ #
# 🕐 時區顯示（2026-10-19 由 agent「mokagi說明」修）
# 為什麼要改：本機系統時區是 Etc/UTC，cron 的 5 個時間欄位一律以「系統時區」解析，
#   但主人與所有侍女都是用香港時間（HKT = UTC+8）思考。舊版 /cron list 只印原始欄位，
#   於是 `0 4 * * *` 被讀成「凌晨四點」，實際上是香港中午 12:00 —— 這是會出事的誤讀。
# 改法：純顯示層。清單開頭標明「系統時區」，每條任務多一行「HKT 換算 + 人類可讀」。
#   不改任何排程、不改新增／確認流程、不改 crontab 內容。
# 可用環境變數 MOK_CRON_DISPLAY_TZ 覆寫顯示用時區（預設 Asia/Hong_Kong）。
# ------------------------------------------------------------------------------------ #
CRON_DISPLAY_TZ = os.environ.get("MOK_CRON_DISPLAY_TZ", "Asia/Hong_Kong")
_DOW_NAMES = ["日", "一", "二", "三", "四", "五", "六"]
_CRON_SPECIAL = {
    "@reboot": "開機時執行",
    "@yearly": "每年一次",
    "@annually": "每年一次",
    "@monthly": "每月一次",
    "@weekly": "每週一次",
    "@daily": "每日一次",
    "@midnight": "每日 00:00",
    "@hourly": "每小時整點",
}


def _get_system_tz_name() -> str:
    """取得系統時區名稱（＝ cron 解析排程所用的時區）"""
    for path in ("/etc/timezone", "/var/db/zoneinfo"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                name = f.read().strip()
            if name:
                return name
        except Exception:
            continue
    try:
        import datetime
        name = datetime.datetime.now().astimezone().tzname()
        if name:
            return name
    except Exception:
        pass
    return "UTC"


def _tz_offset_hours(tz_name: str, ref_ts: float = None):
    """回傳指定時區相對 UTC 的偏移小時數；無法解析回傳 None"""
    if ref_ts is None:
        ref_ts = time.time()
    try:
        from zoneinfo import ZoneInfo
        import datetime
        dt = datetime.datetime.fromtimestamp(ref_ts, tz=ZoneInfo(tz_name))
        off = dt.utcoffset()
        if off is None:
            return None
        return off.total_seconds() / 3600.0
    except Exception:
        return None


def _tz_short_name(tz_name: str) -> str:
    """Asia/Hong_Kong -> HKT（取不到就退回路徑末段）"""
    try:
        from zoneinfo import ZoneInfo
        import datetime
        ab = datetime.datetime.now(tz=ZoneInfo(tz_name)).tzname()
        if ab:
            return ab
    except Exception:
        pass
    return str(tz_name).split("/")[-1].upper()[:5]


def _fmt_offset(hours) -> str:
    """+8 -> +08:00"""
    if hours is None:
        return "?"
    sign = "+" if hours >= 0 else "-"
    h = abs(hours)
    hh = int(h)
    mm = int(round((h - hh) * 60))
    return "%s%02d:%02d" % (sign, hh, mm)


def _expand_cron_field(field: str, lo: int, hi: int):
    """把單一 cron 欄位展開成整數集合；無法解析回傳 None。支援 * , - /"""
    vals = set()
    field = str(field or "").strip()
    if not field:
        return None
    for part in field.split(","):
        part = part.strip()
        if not part:
            return None
        step = 1
        body = part
        if "/" in part:
            body, s = part.split("/", 1)
            if not s.isdigit() or int(s) <= 0:
                return None
            step = int(s)
        if body == "*":
            start, end = lo, hi
        elif "-" in body:
            a, b = body.split("-", 1)
            if not (a.strip().isdigit() and b.strip().isdigit()):
                return None
            start, end = int(a), int(b)
        elif body.isdigit():
            start = end = int(body)
            if step > 1:
                end = hi
        else:
            return None
        if start < lo or end > hi or start > end:
            return None
        vals.update(range(start, end + 1, step))
    return vals or None


def _fmt_num_set(vals, max_list: int = 6) -> str:
    """{2,3,4} -> '02–04'；{1,5} -> '01、05'"""
    vals = sorted(vals)
    if not vals:
        return ""
    if len(vals) == 1:
        return "%02d" % vals[0]
    if vals == list(range(vals[0], vals[-1] + 1)):
        return "%02d–%02d" % (vals[0], vals[-1])
    if len(vals) <= max_list:
        return "、".join("%02d" % v for v in vals)
    return "%02d、%02d…%02d" % (vals[0], vals[1], vals[-1])


def _cron_dow_desc(field: str) -> str:
    """星期欄位 -> 週一至週五 / 週日、週三 …"""
    if not field or field == "*":
        return ""
    vals = _expand_cron_field(field.replace("7", "0"), 0, 6)
    if not vals or len(vals) >= 7:
        return ""
    if vals == {1, 2, 3, 4, 5, 6}:
        return "週一至週六"
    if vals == {1, 2, 3, 4, 5}:
        return "週一至週五"
    return "、".join("週" + _DOW_NAMES[v] for v in sorted(vals))


def _cron_time_hints(schedule: str, display_tz: str = None) -> dict:
    """把 cron 排程換算成人類可讀時間（純字串，不動排程本身）。

    回傳 {"utc": "UTC 每日 04:00", "local": "HKT 每日 12:00",
          "label": "每日 04:00", "cross_day": bool, "ok": bool}
    """
    out = {"utc": "", "local": "", "label": "", "cross_day": False, "ok": False}
    sched = (schedule or "").strip()
    if not sched:
        return out
    target_tz = display_tz or CRON_DISPLAY_TZ

    if sched.startswith("@"):
        label = _CRON_SPECIAL.get(sched.lower(), sched)
        out.update({
            "label": label,
            "utc": "UTC " + label,
            "local": _tz_short_name(target_tz) + " " + label,
            "ok": True,
        })
        return out

    fields = sched.split()
    if len(fields) != 5:
        return out
    minutes = _expand_cron_field(fields[0], 0, 59)
    hours = _expand_cron_field(fields[1], 0, 23)
    if not minutes or not hours:
        return out

    # ---- 日期／星期描述 ----
    date_parts = []
    if fields[3] != "*":
        date_parts.append(fields[3] + " 月")
    if fields[2] != "*":
        date_parts.append(fields[2] + " 日")
    dow_desc = _cron_dow_desc(fields[4])
    if dow_desc:
        date_parts.append(dow_desc)
    date_prefix = (" ".join(date_parts) + " ") if date_parts else ""
    all_day = len(hours) == 24

    def _time_str(h_set, m_set, marks=None) -> str:
        """marks: {hour: 0/1/-1}，>0 表示翌日、<0 表示前一日"""
        if len(h_set) == 24 and len(m_set) == 60:
            return "每分鐘"
        if len(h_set) == 24:
            return "每小時 %s 分" % _fmt_num_set(m_set)
        hs = sorted(h_set)
        mdesc = _fmt_num_set(m_set)
        if len(hs) == 1 and len(m_set) == 1:
            tag = ""
            if marks:
                d = marks.get(hs[0], 0)
                tag = "(翌日)" if d > 0 else ("(前一日)" if d < 0 else "")
            return "%02d:%02d%s" % (hs[0], list(m_set)[0], tag)
        if marks and len(hs) <= 6:
            hs_txt = "、".join(
                "%02d%s" % (h, "" if marks.get(h, 0) == 0
                            else ("(翌日)" if marks.get(h, 0) > 0 else "(前一日)"))
                for h in hs)
        else:
            hs_txt = _fmt_num_set(hs)
        return "%s 時 %s 分" % (hs_txt, mdesc)

    label = date_prefix + _time_str(hours, minutes)
    out.update({"label": label, "utc": "UTC " + label, "ok": True})

    # ---- 換算到顯示時區 ----
    off_sys = _tz_offset_hours(_get_system_tz_name())
    off_tgt = _tz_offset_hours(target_tz)
    if off_sys is None or off_tgt is None:
        return out
    delta = off_tgt - off_sys
    shift = int(round(delta))
    if abs(delta - shift) > 0.01:
        # 非整點時差（例 +05:30）不強行換算，避免誤導
        return out
    short = _tz_short_name(target_tz)
    if shift == 0 or all_day:
        out["local"] = short + " " + date_prefix + _time_str(hours, minutes)
        return out

    local_marks = {}
    cross = False
    for h in sorted(hours):
        t = h + shift
        d = 0
        while t >= 24:
            t -= 24
            d += 1
        while t < 0:
            t += 24
            d -= 1
        if d:
            cross = True
        if t in local_marks:
            local_marks[t] = max(local_marks[t], d, key=abs)
        else:
            local_marks[t] = d

    shifted = set(local_marks.keys())
    out["local"] = short + " " + date_prefix + _time_str(shifted, minutes, local_marks)
    # 只有「收斂成範圍顯示、看不出個別跨日」時才需要提醒
    out["cross_day"] = cross and len(shifted) > 6
    return out


# ------------------------------------------------------------------------------------ #
# 已知 cron 任務的人類可讀說明
# 當 /cron list 列出的指令命中 CRON_TASK_DESCRIPTIONS 的關鍵字時，會自動附上說明，
# 讓主人一眼看懂「這個排程在幹嘛、怎麼查看/刪除、有什麼風險」。
# 新增排程後，可在這裡補一條說明。
# ------------------------------------------------------------------------------------ #
CRON_TASK_DESCRIPTIONS = {
    "discover_topics.py": {
        "title": "📦 短影音帶貨 · 主題自動發現（每天 02:40）",
        "desc": (
            "自動用種子關鍵字（config.json 的 discovery.seed_keywords）搜尋熱門帶貨話題，"
            "把候選主題 + hashtags 寫回 config.json 的 products 商品池，供 pipeline 產片使用。"
            "搜尋三層備援：Tavily → DuckDuckGo → 模板產生。"
        ),
        "view": (
            "  • 今日候選：cat ~/.mok/work/短影音帶貨/topics/suggestions_*.json\n"
            "  • 商品池　：cat ~/.mok/work/短影音帶貨/config.json\n"
            "  • 執行日誌：tail -50 ~/.mok/work/短影音帶貨/logs/discover.log\n"
            "  • 手動預覽：cd ~/.mok/work/短影音帶貨 && python3 scripts/discover_topics.py --dry-run --limit 10"
        ),
        "delete": "/cron delete 11（刪除後此任務不再自動執行）",
        "install": (
            "無需安裝套件。僅需 Tavily API Key（在 ~/.mok/.稚 設 TAVILY_API_KEY）啟用第一層搜尋；"
            "缺 Key 時自動降級 DuckDuckGo，功能仍可用。"
        ),
        "risk": (
            "⚠️ 高風險：--apply 會「直接覆寫」config.json 的 products 商品池（舊商品被覆蓋）。\n"
            "若種子關鍵字太冷門或搜尋 API 異常，可能寫入不相關/重複主題，進而影響後續產片品質。\n"
            "建議先 --dry-run 預覽再 apply；修改 config.json 前建議先備份。"
        ),
        "code": (
            "scripts/discover_topics.py：讀 seed_keywords → 三層搜尋 → 去重 → 寫 topics/suggestions_*.json；\n"
            "--apply 時額外寫回 config.json 的 products。"
        ),
        "mokagi": (
            "mokagi說明：這是短影音帶貨流水線的「找題材」前置步驟；\n"
            "每天 03:00 的 run_pipeline.sh（pipeline.py）會消費商品池產出影片並發佈。"
            "兩者互補：discover 找題材 → pipeline 出片。"
        ),
    },
}


def _format_cron_description(info: dict) -> str:
    """將任務說明字典格式化成人類可讀文字"""
    title = info.get("title", "任務說明")
    lines = [f"    ┌─ {title}"]

    def _wrap(label: str, text: str) -> None:
        parts = str(text).split(chr(10))
        lines.append(f"    │ {label}{parts[0]}")
        pad = " " * len(label)
        for p in parts[1:]:
            lines.append(f"    │ {pad}{p}")

    if info.get("desc"):
        _wrap("📌 用途：", info["desc"])
    if info.get("view"):
        _wrap("🔍 查看：", info["view"])
    if info.get("delete"):
        _wrap("🗑 刪除：", info["delete"])
    if info.get("install"):
        _wrap("📥 安裝：", info["install"])
    if info.get("risk"):
        _wrap("⚠️ 風險：", info["risk"])
    if info.get("code"):
        _wrap("💻 代碼：", info["code"])
    if info.get("mokagi"):
        _wrap("🤖 ", info["mokagi"])
    lines.append("    └─")
    return chr(10).join(lines) + chr(10)
def _get_cron_description(command: str) -> str:
    """依指令特徵字串查詢人類可讀說明；找不到回傳空字串"""
    for key, info in CRON_TASK_DESCRIPTIONS.items():
        if key in command:
            return _format_cron_description(info)
    return ""


# ------------------ 公開函數（供 Agent 調用） ------------------

async def confirm_cron_command(chat_id: str, token: str, agent_config: dict = None) -> tuple:
    """確認 cron 命令，回傳 (成功與否, 結果訊息)。統一走 confirm_manager。"""
    status, info = _confirm.redeem(chat_id, token, agent_config=agent_config)
    if status != "ok":
        return False, _confirm.status_message(status, info, agent_config=agent_config)

    action = info.get("cmd") or info.get("action") or ""
    if action in ("cron_add", "add"):
        action = "add"
    elif action in ("cron_delete", "delete"):
        action = "delete"
    args = info.get("args", "")

    if action == "add":
        return _execute_cron_add(args)
    elif action == "delete":
        try:
            return _execute_cron_delete(int(str(args).strip()))
        except Exception:
            return False, "❌ 刪除失敗：無效的任務編號。"
    else:
        return False, "❌ 未知動作: " + str(action)


def _execute_cron_add(cron_line: str) -> tuple:
    """實際執行新增 cron 任務"""
    current = _get_current_crontab()
    # 檢查是否已存在完全相同的指令（避免重複）
    for line in current:
        if line.strip() == cron_line.strip():
            return False, f"⚠️ 該任務已存在：\n{cron_line}"
    current.append(cron_line)
    success, err = _write_crontab(current)
    if success:
        return True, f"✅ Cron 任務已新增：\n{cron_line}"
    else:
        return False, f"❌ 新增失敗：{err}"

def _execute_cron_delete(line_number: int) -> tuple:
    """實際執行刪除指定編號的 cron 任務"""
    current = _get_current_crontab()
    if line_number < 1 or line_number > len(current):
        return False, f"❌ 編號 {line_number} 超出範圍（共有 {len(current)} 行）"
    # 檢查該行是否為註解或空白（不應該刪除）
    if not current[line_number-1].strip() or current[line_number-1].strip().startswith('#'):
        return False, f"❌ 編號 {line_number} 是註解或空白行，無法刪除。"
    deleted_line = current[line_number-1]
    del current[line_number-1]
    success, err = _write_crontab(current)
    if success:
        return True, f"✅ 已刪除任務：\n{deleted_line}"
    else:
        return False, f"❌ 刪除失敗：{err}"

# ------------------ 命令處理入口 ------------------

async def handle_cron(args, chat_id: str = None, agent_config: dict = None):
    """處理 /cron 命令"""
    if agent_config is None:
        import mokagi
        agent_config = mokagi._agent_config

    if args is None:
        args = ""
    
    # 支援字典參數（來自 LLM 工具調用）
    if isinstance(args, dict):
        action = args.get("action", "").lower()
        rest = args.get("args", "")
    else:
        args = args.strip()
        if not args:
            return (
                "⏰ **Cron 任務管理**\n"
                "可用指令：\n"
                "  `/cron list`               – 列出所有任務\n"
                "  `/cron add \"排程 指令\"`    – 新增任務（需確認）\n"
                "  `/cron delete 編號`        – 刪除任務（需確認）\n"
                "  `/cron log [行數]`         – 查看執行日誌\n"
                "\n範例：\n"
                "  `/cron add \"0 8 * * * /home/ubuntu/backup.sh\"`"
            )
        parts = args.split(maxsplit=1)
        action = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""

    # ----- list -----
    if action == "list":
        lines = _get_current_crontab()
        if not lines:
            return "📭 目前沒有任何 cron 任務。"
        tasks = _parse_cron_lines(lines)
        if not tasks:
            return "📭 目前沒有任何有效的 cron 任務（僅有註解）。"

        # 🕐 2026-10-19：明示系統時區，並逐條換算顯示時區（純顯示，不改排程）
        sys_tz = _get_system_tz_name()
        sys_off = _fmt_offset(_tz_offset_hours(sys_tz))
        tgt_off = _fmt_offset(_tz_offset_hours(CRON_DISPLAY_TZ))
        result = "📋 **目前的 Cron 任務**（共 %d 條）\n\n" % len(tasks)
        result += "🌐 系統時區：`%s`（UTC%s）— 排程 5 個時間欄位一律以此解析\n" % (sys_tz, sys_off)
        result += "🕐 每條另附 `%s`（UTC%s）換算。**寫排程時要寫系統時區，不是香港時間**：\n" % (CRON_DISPLAY_TZ, tgt_off)
        result += "  例：想要 HKT 每天 04:00，要寫 `0 20 * * *`（前一日 20:00 UTC）。\n\n"
        for t in tasks:
            result += "`%s`. `%s` → `%s`\n" % (t['number'], t['schedule'], t['command'])
            hint = _cron_time_hints(t["schedule"])
            if hint.get("ok") and hint.get("local"):
                flag = "（換算後跨日，星期／日期需一併調整）" if hint.get("cross_day") else ""
                result += "    🕐 %s ｜ %s%s\n" % (hint["local"], hint["utc"], flag)
            # 附上人類可讀說明（若該指令命中 CRON_TASK_DESCRIPTIONS）
            desc = _get_cron_description(t["command"])
            if desc:
                result += desc
        return result

    # ----- add (需確認) -----
    elif action == "add":
        if not rest:
            return "❌ 請提供完整的 cron 指令。範例：`/cron add \"0 8 * * * /path/to/script.sh\"`"
        # 簡單驗證格式（至少 6 個欄位）
        parts = rest.strip().split()
        if len(parts) < 6:
            return "❌ 無效的 cron 格式。請提供完整的 5 個時間欄位 + 指令。"
        # === 🔒 核心自我保護：cron 指令不得重啟/停止 mokagi 服務 ===
        import re as _re
        if _re.search(
            r"(pm2[ ]+(restart|reload|stop|delete|kill|start|resurrect|save|dump)"
            r"|restart.{0,20}mok_|mok_.{0,20}restart"
            r"|systemctl[ ]+(restart|stop).{0,20}(mok|ollama)"
            r"|pkill.{0,20}(mok_|python)"
            r"|kill.{0,20}(mok_|launcher))", rest, _re.I):
            return "❌ 此 cron 指令涉及重啟/停止 mokagi 服務，已被核心自我保護攔截，拒絕新增。"
        # 檢查是否為管理員
        if not is_admin(chat_id, agent_config):
            return "❌ 只有管理員可以新增 cron 任務。"
        # 產生確認碼
        token = generate_token(chat_id, "add", rest)
        _confirm.register(token, "cron", "cron_add", rest, chat_id)
        warning = "⚠️ **新增 Cron 任務**\n`" + rest + "`"
        _desc = _get_cron_description(rest)
        if _desc:
            warning += "\n\n📖 任務說明：\n" + _desc
        return _cron_confirm_warning(warning, token)

    # ----- delete (需確認) -----
    elif action == "delete":
        if not rest or not rest.strip().isdigit():
            return "❌ 請提供要刪除的任務編號（從 `/cron list` 取得）。"
        line_num = int(rest.strip())
        # 檢查該編號是否存在且有效
        lines = _get_current_crontab()
        if line_num < 1 or line_num > len(lines):
            return f"❌ 編號 {line_num} 超出範圍（共有 {len(lines)} 行）。"
        target_line = lines[line_num-1].strip()
        if not target_line or target_line.startswith('#'):
            return f"❌ 編號 {line_num} 是註解或空白行，無法刪除。"
        # 管理員檢查
        if not is_admin(chat_id, agent_config):
            return "❌ 只有管理員可以刪除 cron 任務。"
        # 產生確認碼
        token = generate_token(chat_id, "delete", rest)
        _confirm.register(token, "cron", "cron_delete", rest, chat_id)
        warning = "⚠️ **刪除 Cron 任務**\n`" + target_line + "`"
        _desc = _get_cron_description(target_line)
        if _desc:
            warning += "\n\n📖 任務說明：\n" + _desc
        return _cron_confirm_warning(warning, token)

    # ----- log -----
    elif action == "log":
        lines_count = rest.strip() if rest.strip().isdigit() else "20"
        try:
            # 讀取系統 cron 日誌（Debian/Ubuntu 通常在此）
            cmd = f"grep CRON /var/log/syslog | tail -n {lines_count}"
            result = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=10)
            if result.stdout:
                return f"📋 **最近 {lines_count} 行 Cron 日誌**\n```\n{result.stdout.strip()}\n```"
            else:
                # 若無日誌，可能權限不足或日誌在其他位置
                return "⚠️ 無法讀取 cron 日誌（可能需要 sudo 權限，或檢查 /var/log/syslog）。"
        except Exception as e:
            return f"❌ 讀取日誌失敗：{e}"

    else:
        return f"❌ 未知動作：{action}。支援：list, add, delete, log"

# ------------------ 自然化函數 ------------------

async def naturalize_cron_result(user_text: str, raw_result: str, ollama_api: str, model_name: str, temp_msg=None, context=None, agent_config=None) -> str:
    """自然化結果（可選，簡單回傳原樣）"""
    return raw_result

# ------------------ 與 admin 整合（支援 /admin confirm） ------------------
# 注意：admin.py 中的 confirm_command 需要能處理 cron 的確認。
# 由於 admin.py 的 confirm_command 只認識 ollama_rm, pip_install, shell_exec, autofix_exec，
# 我們需要在 admin.py 中擴充，或讓 cron 使用自己的確認機制。
# 為了簡單，此處我們讓 cron 使用自己的 /cron confirm 機制，但為了與 admin 統一，
# 我們可以修改 admin.py 讓它知道 cron 的 token。
# 不過更方便的做法：讓 cron 的 confirm 也用 /admin confirm，所以需要在 admin.py 的 confirm_command 中加入 cron 的處理。
# 或者，我們讓 cron 直接使用 /cron confirm <token> 獨立的確認路徑。
# 為求簡單且相容現有 admin，我在此提供一個非同步確認函數給 admin 呼叫。

# 在 admin.py 中，可以在 confirm_command 中加入：
# elif cmd_type == "cron_add":
#     from tools.cron_tool import confirm_cron_command
#     return await confirm_cron_command(chat_id, args, agent_config)
# elif cmd_type == "cron_delete":
#     from tools.cron_tool import confirm_cron_command
#     return await confirm_cron_command(chat_id, args, agent_config)

# 但為了完全獨立且不修改 admin.py，此工具將使用獨立的 /cron confirm 指令。
# 不過，為了讓主人方便，我建議還是沿用 /admin confirm 的統一入口。

# 因此，我在這裡提供一個讓 admin.py 能導入的函數。
# 並在工具說明中提醒使用者，需在 admin.py 中新增兩行。