# ------------------------------------------------------------------------------------ #
# job.py - 任務管理工具（基於 job.md）
# 設計：每個任務獨立目錄 + job.md，狀態機驅動，提供 /job 指令與心跳執行。
# 合併 job_manager.py 所有功能，成為唯一任務管理入口。
# 2026-07-17 合併版
# ------------------------------------------------------------------------------------ #

PLUGIN_INFO = {
    "command": "/job",
    "icon": "📋",
    "handler": "handle_job",
    "description": """        
        (/新工作, /job new),
        (/所有工作, /job list),
        (/檢視工作, /job get),
        (/更新工作, /job update),
        (/刪除工作, /job delete),
        (/執行工作, /job run),
        (/繼續工作, /continue <job_name> 或 /continue <job_name> 補充內容)
    """,
    "heartbeat": {
        "enabled": True,
        "handler": "heartbeat_handler",
        "interval": 60
    },
    "intent_keywords": [
        ("/新工作", "/job new"),
        ("/所有工作", "/job list"),
        ("/檢視工作", "/job get"),
        ("/更新工作", "/job update"),
        ("/刪除工作", "/job delete"),
        ("/執行工作", "/job run"),
    ],
    "tool_schema": {
        "name": "job",
        "description": (
            "任務管理：建立、列表、檢視、更新、刪除、手動執行。\n"
            "當用戶提出多步驟、定期或耗時任務時，主動使用 action=new 建立任務。\n"
            "建立後告知任務名稱，引導使用 /continue <任務名> 繼續。\n"
            "更新：/job update <job_name> <選項> [值]\n"
            "  0=進行中  1=完成  2=封鎖  3=等待確認  4 <進度%>"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {"type": "string", "enum": ["new", "list", "get", "update", "delete", "run"]},
                "content": {"type": "string", "description": "動作所需參數"}
            },
            "required": ["action"]
        }
    },
    "update": "20260717"
}

import os
import re
import time
import json
import hashlib
import logging
import shutil
import asyncio
import httpx
from pathlib import Path
from typing import Dict, Optional, List, Tuple

logger = logging.getLogger(__name__)


# ========== 修正 PM2 環境下的 HOME 路徑問題 ==========
# 強制使用正確的專案根目錄（不受 HOME 環境變量影響）
_USER_HOME = os.path.expanduser("~")
# 若 HOME 是 /root，但實際專案在 /home/ubuntu，則強制修正
if _USER_HOME == "/root" and os.path.exists("/home/ubuntu/.mok"):
    _USER_HOME = "/home/ubuntu"
PROJECT_DIR = Path(_USER_HOME) / ".mok"
# ========== 結束 ==========

# ========== TG 通知輔助函數 ==========
async def send_tg_notification(agent_name: str, agent_config: dict, message: str):
    """透過 Telegram 發送通知給主人（如果已配置 Token 與 Chat ID）"""
    token = agent_config.get("MOK_TG_TOKEN")
    chat_id = agent_config.get("ADMIN_CHAT_ID")
    if not token or not chat_id:
        logger.debug(f"跳過 TG 通知（缺少 Token 或 Chat ID）")
        return
    try:
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        payload = {"chat_id": chat_id, "text": message, "parse_mode": "Markdown"}
        async with httpx.AsyncClient(timeout=10.0) as client:
            await client.post(url, json=payload)
        logger.info(f"✅ 已發送 TG 通知給 {chat_id}")
    except Exception as e:
        logger.warning(f"發送 TG 通知失敗: {e}")




# ========== 路徑函數（統一使用 Path） ==========
def get_jobs_root(agent_name: str = "稚") -> Path:
    return PROJECT_DIR / "agent" / agent_name / "jobs"

def get_job_path(agent_name: str, job_name: str) -> Path:
    return get_jobs_root(agent_name) / job_name

def get_job_md_path(agent_name: str, job_name: str) -> Path:
    return get_job_path(agent_name, job_name) / "job.md"

def get_detailed_log_path(agent_name: str, job_name: str) -> Path:
    return get_job_path(agent_name, job_name) / "detailed.log"

# ========== Markdown 解析與渲染 ==========
def parse_job_md(filepath: Path) -> Dict[str, str]:
    """從 job.md 解析出各個區塊（## 標題）的內容"""
    if not filepath.exists():
        return {}
    with open(filepath, 'r', encoding='utf-8') as f:
        content = f.read()
    
    pattern = r'^##\s*(.+?)\s*\n(.*?)(?=\n##\s|\Z)'
    matches = re.findall(pattern, content, re.DOTALL | re.MULTILINE)
    
    data = {}
    for title, body in matches:
        data[title.strip()] = body.strip()
    
    title_match = re.search(r'^#\s*(.+?)$', content, re.MULTILINE)
    if title_match:
        data['_title'] = title_match.group(1).strip()
    return data

def render_job_md(data: Dict[str, str], title: str) -> str:
    """將 dict 渲染為 job.md 格式"""
    lines = [f"# {title}", ""]
    sections = ["狀態", "進度", "主人原話", "上次執行摘要", "下一次目標", "主人補充"]
    for sec in sections:
        if sec in data and data[sec]:
            lines.append(f"## {sec}")
            lines.append(data[sec])
            lines.append("")
    return "\n".join(lines).strip()

# ========== CRUD 操作 ==========
def create_job(agent_name: str, job_name: str, user_text: str) -> bool:
    """建立新任務（初始化 job.md）"""
    job_dir = get_job_path(agent_name, job_name)
    if job_dir.exists():
        return False
    
    job_dir.mkdir(parents=True, exist_ok=True)
    
    data = {
        "狀態": "waiting_confirm",
        "進度": "0%",
        "主人原話": user_text,
        "上次執行摘要": "任務剛建立，等待第一次執行。",
        "下一次目標": "請 AI 分析並制定具體步驟。",
        "主人補充": ""
    }
    md_path = get_job_md_path(agent_name, job_name)
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(render_job_md(data, job_name))
    
    log_path = get_detailed_log_path(agent_name, job_name)
    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump([], f, ensure_ascii=False, indent=2)
    
    return True

def read_job_summary(agent_name: str, job_name: str) -> Dict[str, str]:
    """讀取 job.md 摘要（省 Token）"""
    md_path = get_job_md_path(agent_name, job_name)
    if not md_path.exists():
        return {}
    data = parse_job_md(md_path)
    return {
        "狀態": data.get("狀態", "unknown"),
        "進度": data.get("進度", "0%"),
        "主人原話": data.get("主人原話", ""),
        "上次執行摘要": data.get("上次執行摘要", ""),
        "下一次目標": data.get("下一次目標", ""),
        "主人補充": data.get("主人補充", "")
    }

def update_job_summary(agent_name: str, job_name: str, new_summary: str, new_next_goal: str = ""):
    """更新「上次執行摘要」和「下一次目標」，進度自動 +1%"""
    md_path = get_job_md_path(agent_name, job_name)
    if not md_path.exists():
        return False
    
    data = parse_job_md(md_path)
    data["上次執行摘要"] = new_summary
    if new_next_goal:
        data["下一次目標"] = new_next_goal
    
    progress = data.get("進度", "0%")
    try:
        p = int(progress.strip('%'))
        if p < 98:
            p += 1
            data["進度"] = f"{p}%"
    except:
        pass
    
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(render_job_md(data, data.get('_title', job_name)))
    return True

def add_user_supplement(agent_name: str, job_name: str, supplement: str) -> bool:
    """追加主人補充內容，狀態改為 in_progress"""
    md_path = get_job_md_path(agent_name, job_name)
    if not md_path.exists():
        return False
    
    data = parse_job_md(md_path)
    timestamp = time.strftime("%Y-%m-%d %H:%M")
    new_entry = f"> {timestamp}: {supplement}"
    
    old = data.get("主人補充", "")
    data["主人補充"] = old + "\n" + new_entry if old else new_entry
    data["狀態"] = "in_progress"
    
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(render_job_md(data, data.get('_title', job_name)))
    return True

def update_status(agent_name: str, job_name: str, status: str):
    """直接更新狀態（done / blocked / in_progress / awaiting_confirm）"""
    md_path = get_job_md_path(agent_name, job_name)
    if not md_path.exists():
        return False
    data = parse_job_md(md_path)
    data["狀態"] = status
    with open(md_path, 'w', encoding='utf-8') as f:
        f.write(render_job_md(data, data.get('_title', job_name)))
    return True

def log_detailed(agent_name: str, job_name: str, entry: dict):
    """記錄完整對話到 detailed.log（LLM 不讀）"""
    log_path = get_detailed_log_path(agent_name, job_name)
    if not log_path.parent.exists():
        return
    logs = []
    if log_path.exists():
        with open(log_path, 'r', encoding='utf-8') as f:
            try:
                logs = json.load(f)
            except:
                logs = []
    entry["timestamp"] = time.time()
    logs.append(entry)
    with open(log_path, 'w', encoding='utf-8') as f:
        json.dump(logs, f, ensure_ascii=False, indent=2)

def list_jobs(agent_name: str) -> List[Tuple[str, str, str]]:
    """列出所有任務（名稱, 狀態, 進度）"""
    root = get_jobs_root(agent_name)
    if not root.exists():
        return []
    jobs = []
    for d in root.iterdir():
        if d.is_dir():
            md_path = d / "job.md"
            if md_path.exists():
                data = parse_job_md(md_path)
                jobs.append((d.name, data.get("狀態", "unknown"), data.get("進度", "0%")))
    return jobs

# ========== LLM 整合輔助 ==========
def get_llm_context(agent_name: str, job_name: str) -> str:
    """生成供 LLM 在 /continue 時讀取的上下文（僅摘要）"""
    summary = read_job_summary(agent_name, job_name)
    if not summary:
        return ""
    return f"""📋 **任務摘要：{job_name}**

- 狀態：{summary.get('狀態', 'unknown')}
- 進度：{summary.get('進度', '0%')}

📌 主人原話：
{summary.get('主人原話', '')}

📝 上次執行摘要：
{summary.get('上次執行摘要', '')}

🎯 下一次目標：
{summary.get('下一次目標', '')}

💬 主人補充：
{summary.get('主人補充', '無')}

請根據上述摘要繼續執行任務。完成後，請輸出「任務完成」並提供最終結果。
"""

# ========== /continue 執行入口（供 mokagi.py 鉤子呼叫）==========
async def run_task(user_id: str, agent_name: str, continue_code: str, full_text: str, stream_callback=None) -> str:
    """
    由 mokagi.py 的鉤子呼叫。
    處理 /continue 命令：補充內容或執行任務。
    """

    # ===== 🛡️ 新增：檢查任務是否屬於當前 Agent =====
    md_path = get_job_md_path(agent_name, continue_code)
    if not md_path.exists():
        # 嘗試在當前 Agent 的目錄中查找，若不存在則拒絕
        return f"❌ 任務「{continue_code}」不屬於當前 Agent「{agent_name}」，請切換到正確的 Agent 再執行。"
    # ===== 結束 =====

    md_path = get_job_md_path(agent_name, continue_code)
    if not md_path.exists():
        return f"❌ 找不到任務「{continue_code}」，請確認任務名稱是否正確。"

    # 檢查是否為真正的補充內容（只有當 /continue 後面有「非 job_name 的內容」才算）
    parts = full_text.strip().split(maxsplit=1)
    if len(parts) > 1:
        supplement = parts[1].strip()
        # 如果補充內容等於 continue_code（心跳呼叫時傳入的 job_name），視為無補充
        if supplement != continue_code:
            add_user_supplement(agent_name, continue_code, supplement)
            return f"✅ 已補充內容，任務「{continue_code}」狀態轉為 in_progress，等待心跳執行。"
        # 否則當作無補充，繼續執行任務

    context = get_llm_context(agent_name, continue_code)
    if not context:
        return f"❌ 無法讀取任務「{continue_code}」的摘要，請檢查 job.md 是否損壞。"

    import mokagi
    collected_reply = ""
    done_event = asyncio.Event()

    async def forward_and_collect(event):
        nonlocal collected_reply
        if stream_callback:
            await stream_callback(event)
        if event.get("type") == "reply":
            collected_reply += event.get("content", "")
        if event.get("type") == "done":
            done_event.set()

    await mokagi.process_message(
        user_id=user_id,
        text=f"【任務執行】{continue_code}（僅限 Agent {agent_name}）",
        stream_callback=forward_and_collect,
        agent_name=agent_name,
        initial_prompt=f"{context}\n\n請根據上述任務摘要繼續執行。完成時請輸出「任務完成」。"
    )

    await done_event.wait()
    result = collected_reply
    
    # ===== 🛡️ 修復：即使回覆為空（例如模型只輸出 thinking 被截斷），也記錄失敗 =====
    if not result:
        md_path = get_job_md_path(agent_name, continue_code)
        if md_path.exists():
            data = parse_job_md(md_path)
            retry = int(data.get("_retry_count", 0)) + 1
            data["_retry_count"] = str(retry)
            if retry >= 3:
                data["狀態"] = "blocked"
                logger.warning(f"任務 {continue_code} 連續失敗 {retry} 次，自動標記為 blocked")
            else:
                data["上次執行摘要"] = f"⚠️ LLM 僅輸出思考過程（未輸出最終回覆），可能因模型截斷或工具調用錯誤（第 {retry} 次）"
                data["下一次目標"] = data.get("下一次目標", "請簡化任務描述或切換模型後重試。")
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(render_job_md(data, data.get('_title', continue_code)))
        return f"⚠️ LLM 回覆為空（僅輸出 thinking），任務已記錄失敗並可能被封鎖。"

    summary = result[:200] + ("..." if len(result) > 200 else "")
    # 支援中英文標點分割
    import re
    sentences = re.split(r'[。？！.?!]', result)
    sentences = [s.strip() for s in sentences if s.strip()]
    next_goal = sentences[-1] if sentences else "請根據最新進度繼續執行。"
    update_job_summary(agent_name, continue_code, summary, next_goal)
    if "任務完成" in result:
        update_status(agent_name, continue_code, "awaiting_confirm")
        return f"🎉 **任務已完成，等待主人確認！**\n📋 `{continue_code}`\n💡 請使用 `/job update {continue_code} status=done` 確認完成"
    else:
        return f"📌 **任務持續執行中**（進度已自動 +1%）"

    summary = result[:200] + ("..." if len(result) > 200 else "")
    # 支援中英文標點分割
    import re
    sentences = re.split(r'[。？！.?!]', result)
    sentences = [s.strip() for s in sentences if s.strip()]
    next_goal = sentences[-1] if sentences else "請根據最新進度繼續執行。"
    update_job_summary(agent_name, continue_code, summary, next_goal)
    if "任務完成" in result:
        update_status(agent_name, continue_code, "awaiting_confirm")
        return f"🎉 **任務已完成，等待主人確認！**\n📋 `{continue_code}`\n💡 請使用 `/job update {continue_code} status=done` 確認完成"
    else:
        return f"📌 **任務持續執行中**（進度已自動 +1%）"

# ========== 心跳處理器 ==========
async def heartbeat_handler(agent_name: str, agent_config: dict):
    """掃描並執行待處理任務（狀態機）"""
    root = get_jobs_root(agent_name)
    if not root.exists():
        return

    auto_mode = agent_config.get("MOK_AGENT_auto", 0) == 1
    user_id = agent_config.get("ADMIN_CHAT_ID", "heart_user")
    max_retry = int(agent_config.get("MOK_MAX_RETRY", 3))

    for job_name in os.listdir(root):
        job_path = root / job_name
        if not job_path.is_dir():
            continue

        data = parse_job_md(job_path / "job.md")
        if not data:
            continue

        status = data.get("狀態", "waiting_confirm")
        if status in ("done", "blocked"):
            continue

        # ===== 等待主人確認：每小時 TG 提醒一次，不執行 =====
        if status == "awaiting_confirm":
            now = time.time()
            last_reminder = float(data.get("_last_reminder_ts", 0))
            if now - last_reminder >= 3600:
                logger.info(f"⏰ 發送 awaiting_confirm 提醒: {job_name}")
                await send_tg_notification(
                    agent_name,
                    agent_config,
                    f"⏰ *等待主人確認*\n📋 `{job_name}`\n📊 狀態：awaiting_confirm\n⚠️ 此任務已完成，等待主人確認。\n💡 請使用 `/job update {job_name} status=done` 確認完成"
                )
                data["_last_reminder_ts"] = str(now)
                with open(job_path / "job.md", 'w', encoding='utf-8') as f:
                    f.write(render_job_md(data, data.get('_title', job_name)))
            continue

        if status == "waiting_confirm" and not auto_mode:
            continue

        logger.info(f"❤️ 心跳觸發任務: {job_name} (狀態: {status})")
        if status == "waiting_confirm" and auto_mode:
            update_status(agent_name, job_name, "in_progress")

        # ===== 執行前發送 TG 通知給主人 =====
        await send_tg_notification(
            agent_name,
            agent_config,
            f"📋 *即將執行任務*\n📌 `{job_name}`\n📊 當前狀態：{status}\n🎯 目標：{data.get('下一次目標', '無')}"
        )

        # 直接呼叫 run_task（不流式）
        try:
            result = await run_task(user_id, agent_name, job_name, f"/continue {job_name}", stream_callback=None)
            logger.info(f"任務 {job_name} 執行結果: {result[:200] if result else '無回覆'}")
            # ===== 發送成功通知 =====
            await send_tg_notification(
                agent_name,
                agent_config,
                f"✅ *任務執行完成*\n📋 `{job_name}`\n📊 摘要：{result[:150] if result else '無回覆'}"
            )
        except Exception as e:
            logger.error(f"執行任務 {job_name} 失敗: {e}")
            # ===== 發送失敗通知 =====
            await send_tg_notification(
                agent_name,
                agent_config,
                f"❌ *任務執行失敗*\n📋 `{job_name}`\n⚠️ 錯誤：{str(e)}"
            )
            # 簡易重試計數（寫入 _retry_count 欄位）
            retry = int(data.get("_retry_count", 0)) + 1
            data["_retry_count"] = str(retry)
            if retry >= max_retry:
                data["狀態"] = "blocked"
                logger.warning(f"任務 {job_name} 重試達上限，標記為 blocked")
            with open(job_path / "job.md", 'w', encoding='utf-8') as f:
                f.write(render_job_md(data, data.get('_title', job_name)))
            continue

        # 重新讀取進度，判斷是否完成
        new_data = parse_job_md(job_path / "job.md")
        if new_data and new_data.get("進度") != data.get("進度"):
            logger.info(f"任務 {job_name} 進度更新為 {new_data.get('進度')}")
            if new_data.get("進度") == "100%":
                update_status(agent_name, job_name, "awaiting_confirm")
                # ===== 進度 100% 特別通知 =====
                await send_tg_notification(
                    agent_name,
                    agent_config,
                    f"🎉 *任務已 100% 完成！*\n📋 `{job_name}`\n⏳ 狀態已轉為 awaiting_confirm，請主人確認。\n💡 使用 `/job update {job_name} status=done` 確認"
                )

# ========== /job 指令處理 ==========
async def handle_job(args, chat_id: str = None, agent_config: dict = None):
    """處理 /job 命令"""
    if agent_config is None:
        agent_config = {}
    agent_name = agent_config.get("MOK_AGENT_NAME", "春")
    user_id = chat_id or agent_config.get("ADMIN_CHAT_ID", "default_user")

    action = ""
    content = ""
    if isinstance(args, dict):
        action = args.get("action", "").lower()
        content = args.get("content", "")
    elif isinstance(args, str):
        parts = args.strip().split(maxsplit=1)
        action = parts[0].lower() if parts else ""
        content = parts[1] if len(parts) > 1 else ""

    if action == "new":
        if not content:
            return "❌ 請提供工作描述（可多行）"
        # 生成任務名稱
        name_hash = hashlib.md5(content.encode()).hexdigest()[:6]
        job_name = f"job_{int(time.time())}_{name_hash}"
        success = create_job(agent_name, job_name, content)
        if not success:
            return "❌ 建立任務失敗，可能名稱已存在。"
        return (
            f"✅ 任務已建立！\n"
            f"名稱：`{job_name}`\n"
            f"可使用 `/continue {job_name}` 繼續執行，或等待心跳自動觸發。"
        )

    elif action == "list":
        jobs = list_jobs(agent_name)
        if not jobs:
            return "📭 目前沒有任務"
        lines = ["📋 任務清單："]
        for name, status, progress in jobs:
            lines.append(f"- `{name}` ({status}) 進度: {progress}")
        return "\n".join(lines)

    elif action == "get":
        if not content:
            return "❌ 請提供 job_name"
        data = read_job_summary(agent_name, content.strip())
        if not data:
            return f"❌ 找不到任務 `{content}`"
        return (
            f"📋 任務 `{content}`\n"
            f"狀態：{data.get('狀態')}\n"
            f"進度：{data.get('進度')}\n"
            f"主人原話：{data.get('主人原話')}\n"
            f"上次執行摘要：{data.get('上次執行摘要')}\n"
            f"下一次目標：{data.get('下一次目標')}\n"
            f"主人補充：{data.get('主人補充')}"
        )

    elif action == "update":
        SHORTCUTS = {"0": "in_progress", "1": "done", "2": "blocked", "3": "awaiting_confirm"}
        HELP = "0=進行中  1=完成  2=封鎖  3=等待確認  4 <進度%>"
        if not content:
            return f"❌ 請提供 job_name 與選項\n{HELP}"
        parts = content.split(maxsplit=1)
        if len(parts) < 2:
            return f"❌ 請提供選項\n{HELP}"
        job_name = parts[0]
        rest = parts[1].strip()

        # ── 數字快捷 0~3 ──
        if rest in SHORTCUTS:
            update_status(agent_name, job_name, SHORTCUTS[rest])
            return f"✅ 已更新 `{job_name}` 的狀態為 {SHORTCUTS[rest]}"

        # ── 4 <進度%> ──
        if rest.startswith("4"):
            val = rest[1:].strip().lstrip('= ')
            if not val:
                return f"❌ 請提供進度數值，例如 `job_xxx 4 50`\n{HELP}"
            if not val.endswith('%'):
                val += '%'
            data = read_job_summary(agent_name, job_name)
            if not data:
                return f"❌ 找不到任務 `{job_name}`"
            md_path = get_job_md_path(agent_name, job_name)
            full_data = parse_job_md(md_path)
            full_data["進度"] = val
            with open(md_path, 'w', encoding='utf-8') as f:
                f.write(render_job_md(full_data, full_data.get('_title', job_name)))
            return f"✅ 已更新 `{job_name}` 的進度為 {val}"

        # ── 向後相容 field=value ──
        if '=' in rest:
            key, value = rest.split('=', 1)
            key = key.strip()
            value = value.strip()
            if key == "status":
                if value not in ("in_progress", "done", "blocked", "awaiting_confirm"):
                    return f"❌ status 只能為 in_progress / done / blocked / awaiting_confirm\n{HELP}"
                update_status(agent_name, job_name, value)
                return f"✅ 已更新 `{job_name}` 的狀態為 {value}"
            elif key == "progress":
                if not value.endswith('%'):
                    value += '%'
                data = read_job_summary(agent_name, job_name)
                if not data:
                    return f"❌ 找不到任務 `{job_name}`"
                md_path = get_job_md_path(agent_name, job_name)
                full_data = parse_job_md(md_path)
                full_data["進度"] = value
                with open(md_path, 'w', encoding='utf-8') as f:
                    f.write(render_job_md(full_data, full_data.get('_title', job_name)))
                return f"✅ 已更新 `{job_name}` 的進度為 {value}"
            else:
                return f"❌ 不支援的欄位：{key}\n{HELP}"

        return f"❌ 無效格式\n{HELP}"

    elif action == "delete":
        if not content:
            return "❌ 請提供 job_name"
        job_name = content.strip()
        job_path = get_job_path(agent_name, job_name)
        if not job_path.exists():
            return f"❌ 找不到任務 `{job_name}`"
        shutil.rmtree(job_path)
        return f"🗑️ 已刪除任務 `{job_name}`"

    elif action == "run":
        if not content:
            return "❌ 請提供 job_name"
        job_name = content.strip()
        data = read_job_summary(agent_name, job_name)
        if not data:
            return f"❌ 找不到任務 `{job_name}`"
        # 手動執行（直接呼叫 run_task）
        try:
            result = await run_task(user_id, agent_name, job_name, f"/continue {job_name}", stream_callback=None)
            return f"✅ 手動執行完成：\n{result}"
        except Exception as e:
            return f"❌ 執行失敗：{e}"

    else:
        return (
            "📋 任務管理\n"
            "可用指令：\n"
            "  /job new <描述>         建立新任務\n"
            "  /job list               列出所有任務\n"
            "  /job get <job_name>     檢視任務詳情\n"
            "  /job update <job_name> 0|1|2|3|4 <值>  更新任務\n"
            "    0=進行中  1=完成  2=封鎖  3=等待確認  4 <進度%>\n"
            "  /job delete <job_name>  刪除任務\n"
            "  /job run <job_name>     手動立即執行任務"
        )