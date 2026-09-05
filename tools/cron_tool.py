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

# 全域變數：存放待確認的命令
_pending_cron_confirmations = {}


def _cron_pending_dir() -> str:
    try:
        import mokagi
        base = os.path.expanduser(f"~/.{mokagi.MOKAGI_home}")
    except Exception:
        base = os.path.expanduser("~/.mok")
    return os.path.join(base, ".pending_cron_confirm")


def _cron_token_path(token: str) -> str:
    return os.path.join(_cron_pending_dir(), f"{token}.json")


def _load_cron_pending() -> None:
    d = _cron_pending_dir()
    try:
        if not os.path.isdir(d):
            return
        now = time.time()
        for fn in os.listdir(d):
            if not fn.endswith(".json"):
                continue
            p = os.path.join(d, fn)
            try:
                with open(p, "r", encoding="utf-8") as f:
                    item = json.load(f)
                if isinstance(item, dict) and now - item.get("timestamp", 0) <= 300:
                    _pending_cron_confirmations.setdefault(fn[:-5], item)
                else:
                    try:
                        os.remove(p)
                    except Exception:
                        pass
            except Exception:
                pass
    except Exception:
        pass


def _save_cron_pending() -> None:
    try:
        d = _cron_pending_dir()
        os.makedirs(d, exist_ok=True)
        for token, item in _pending_cron_confirmations.items():
            p = _cron_token_path(token)
            tmp = p + ".tmp"
            try:
                with open(tmp, "w", encoding="utf-8") as f:
                    json.dump(item, f, ensure_ascii=False)
                os.replace(tmp, p)
            except Exception:
                pass
    except Exception:
        pass


def _remove_cron_pending_file(token: str) -> None:
    try:
        p = _cron_token_path(token)
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        pass


def has_pending_cron_token(token: str) -> bool:
    _load_cron_pending()
    return token in _pending_cron_confirmations


_load_cron_pending()

def generate_token(chat_id: str, action: str, args: str) -> str:
    """生成一次性確認 token"""
    raw = f"{chat_id}_{action}_{args}_{time.time()}_{os.urandom(4).hex()}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]

def is_admin(chat_id: str, agent_config: dict = None) -> bool:
    """簡易管理員檢查（網頁版放行）"""
    if agent_config is None:
        import mokagi
        agent_config = mokagi._agent_config
    admin_chat_id = agent_config.get("ADMIN_CHAT_ID", "")
    if chat_id and not chat_id.isdigit():
        return True  # 網頁版
    return str(chat_id) == admin_chat_id

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
    """解析 cron 行，回傳帶編號的任務清單（跳過註解）"""
    tasks = []
    for idx, line in enumerate(lines, 1):
        line_stripped = line.strip()
        if not line_stripped or line_stripped.startswith('#'):
            continue
        # 簡單判斷：是否為有效的 cron 行（至少有 5 個時間欄位）
        parts = line_stripped.split()
        if len(parts) >= 6:  # 5個時間 + 至少1個指令
            tasks.append({
                "number": idx,
                "raw": line_stripped,
                "schedule": " ".join(parts[:5]),
                "command": " ".join(parts[5:])
            })
    return tasks


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
    """確認 cron 命令，回傳 (成功與否, 結果訊息)"""
    _load_cron_pending()
    if token not in _pending_cron_confirmations:
        return False, "❌ 確認碼無效或已過期。"
    info = _pending_cron_confirmations[token]
    if str(info["chat_id"]) != str(chat_id):
        return False, "❌ 確認碼與用戶不匹配。"
    if time.time() - info["timestamp"] > 300:
        del _pending_cron_confirmations[token]
        _remove_cron_pending_file(token)
        return False, "❌ 確認碼已超時（5分鐘）。"

    action = info["action"]
    args = info["args"]
    del _pending_cron_confirmations[token]
    _remove_cron_pending_file(token)

    if action == "add":
        return _execute_cron_add(args)
    elif action == "delete":
        return _execute_cron_delete(args)
    else:
        return False, f"❌ 未知動作: {action}"

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
        result = "📋 **目前的 Cron 任務**\n\n"
        for t in tasks:
            result += f"`{t['number']}`. `{t['schedule']}` → `{t['command']}`\n"
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
        # 檢查是否為管理員
        if not is_admin(chat_id, agent_config):
            return "❌ 只有管理員可以新增 cron 任務。"
        # 產生確認碼
        token = generate_token(chat_id, "add", rest)
        _pending_cron_confirmations[token] = {
            "action": "add",
            "args": rest,
            "chat_id": chat_id,
            "timestamp": time.time()
        }
        _save_cron_pending()
        warning = f"⚠️ **新增 Cron 任務**\n`{rest}`"
        _desc = _get_cron_description(rest)
        if _desc:
            warning += f"\n\n📖 任務說明：\n{_desc}"
        return f"CONFIRM_SPLIT:{warning}\n🔐 此確認碼用於授權執行上方操作（僅限您本人確認）。若您未發起此操作，請直接忽略。\n請在 5 分鐘內發送確認碼以執行：\n---CONFIRM_SPLIT---\n/admin confirm {token}"

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
        _pending_cron_confirmations[token] = {
            "action": "delete",
            "args": rest,
            "chat_id": chat_id,
            "timestamp": time.time()
        }
        _save_cron_pending()
        warning = f"⚠️ **刪除 Cron 任務**\n`{target_line}`"
        _desc = _get_cron_description(target_line)
        if _desc:
            warning += f"\n\n📖 任務說明：\n{_desc}"
        return f"CONFIRM_SPLIT:{warning}\n🔐 此確認碼用於授權執行上方操作（僅限您本人確認）。若您未發起此操作，請直接忽略。\n請在 5 分鐘內發送確認碼以執行：\n---CONFIRM_SPLIT---\n/admin confirm {token}"

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