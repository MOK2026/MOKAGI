# ------------------------------------------------------------------------------------ #
# 工具名稱: task (任務管理) — 唯一 _job.json 存取層
# 用途: 整個 mokagi 系統中，僅此一個模組直接讀寫 _job.json。
#       提供完整 CRUD：new / list / get / update / delete。
#       既是 LLM 工具（透過 PLUGIN_INFO），也是其他模組的程式庫。
# 更新: 2026-07-03 — 重寫為唯一真相來源
# ------------------------------------------------------------------------------------ #

PLUGIN_INFO = {
    "command": "/task",
    "icon": "📋",
    "handler": "handle_task",
    "description": "任務管理：建立(new)、列出(list)、檢視(get)、編輯(update)、刪除(delete)。",
    "intent_keywords": [
        ("/新任務", "/task new"),
        ("/建立任務", "/task new"),
        ("/任務清單", "/task list"),
        ("/刪除任務", "/task delete"),
        ("/編輯任務", "/task update"),
        ("/檢視任務", "/task get"),
    ],
    "tool_schema": {
        "name": "task",
        "description": (
            "管理持久化任務。支援五個動作：new, list, get, update, delete。\n\n"
            "- **new** (需要 content)：建立新任務，content 為任務目標描述。\n"
            "  範例：`/task new 寫一個網頁爬蟲`\n"
            "- **list** (不需要 content)：列出當前用戶的所有未完成任務。\n"
            "- **get** (需要 content)：檢視指定繼續碼的任務詳情。\n"
            "  範例：`/task get a1b2c3`\n"
            "- **update** (需要 content)：更新指定繼續碼的任務內容。格式：`繼續碼 欄位=值`。\n"
            "  範例：`/task update a1b2c3 goal=寫爬蟲+爬取新聞`\n"
            "- **delete** (需要 content)：刪除指定繼續碼的任務。\n"
            "  範例：`/task delete a1b2c3`"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["new", "list", "get", "update", "delete"],
                    "description": "要執行的動作。"
                },
                "content": {
                    "type": "string",
                    "description": (
                        "動作的參數。\n"
                        "- 對 new：任務目標描述。\n"
                        "- 對 get / delete：繼續碼。\n"
                        "- 對 update：`繼續碼 欄位=值`。\n"
                        "- 對 list：不需要提供。"
                    )
                }
            },
            "required": ["action"]
        }
    },
    "update": "20260703"
}

import hashlib
from hashlib import md5
import json
import logging
import os
import re
import time
from typing import Dict, List, Optional

# ---------- 內部輔助 ----------

def _get_job_file(agent_name: str) -> str:
    """取得 _job.json 路徑（無需外部依賴）"""
    return os.path.expanduser(f"~/.mok/agent/{agent_name}/_job.json")


def _read_jobs(agent_name: str) -> dict:
    job_file = _get_job_file(agent_name)
    if not os.path.exists(job_file):
        return {}
    try:
        with open(job_file, 'r', encoding='utf-8') as f:
            content = f.read().strip()
            if not content:                     # 空檔案 → 修復
                logging.warning(f"[task] _job.json 為空，重新初始化為 {{}}")
                _write_jobs(agent_name, {})     # 寫入有效 JSON
                return {}
            return json.loads(content)
    except json.JSONDecodeError as e:
        logging.warning(f"[task] _job.json 格式無效: {e}，嘗試重設")
        # 可選：備份損毀檔案（此處直接覆蓋）
        _write_jobs(agent_name, {})
        return {}
    except Exception as e:
        logging.warning(f"[task] 讀取 _job.json 失敗: {e}")
        return {}


def _write_jobs(agent_name: str, data: dict):
    """寫入整個 _job.json"""
    job_file = _get_job_file(agent_name)
    os.makedirs(os.path.dirname(job_file), exist_ok=True)
    with open(job_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _build_unique_key(user_id: str, agent_name: str) -> str:
    return f"{user_id}_{agent_name}"


# ---------- 輔助函數 ----------

def _upgrade_legacy_task(user_id: str, task_data: dict) -> dict:
    if "messages" not in task_data and "goal" in task_data:
        task_data["messages"] = []
    if "code" not in task_data:
        task_data["code"] = md5(f"{user_id}_{time.time()}_{task_data.get('goal','')}".encode()).hexdigest()[:12]
    if "agent_name" not in task_data:
        task_data["agent_name"] = "助手"
    if "max_iterations" not in task_data:
        task_data["max_iterations"] = 5
    if "iteration" not in task_data:
        task_data["iteration"] = 0
    if "任務名" not in task_data:
        task_data["任務名"] = task_data.get("goal", "")
    if "goal" not in task_data:
        task_data["goal"] = task_data.get("任務名", "")
    if "整體計劃" not in task_data:
        task_data["整體計劃"] = f"1. {task_data.get('goal', '')}"
    if "現在進度" not in task_data:
        task_data["現在進度"] = {"百分比": "0%"}
    if "上回摘要" not in task_data:
        task_data["上回摘要"] = ""
    if "timestamp" not in task_data:
        task_data["timestamp"] = time.time()
    return task_data


def _extract_plan_from_messages(messages: list, goal: str) -> str:
    for m in messages:
        if m.get("role") == "assistant":
            content = m.get("content", "")
            lines = content.split('\n')
            plan_lines = []
            in_plan = False
            for line in lines:
                if re.match(r'^\d+\.', line.strip()) or re.match(r'^[*-]\s+\d+\.', line.strip()):
                    plan_lines.append(line.strip())
                    in_plan = True
                elif in_plan and line.strip() and not re.match(r'^\d+\.', line.strip()):
                    if len(plan_lines) >= 3:
                        break
            if plan_lines:
                return '\n'.join(plan_lines)
    return f"1. {goal}"


def _extract_summary_from_messages(messages: list) -> str:
    for m in reversed(messages):
        if m.get("role") == "assistant":
            content = m.get("content", "")
            if content:
                return content[:200]
    return ""


# ---------- 公開 CRUD API（其他模組可導入） ----------

def create_task(
    user_id: str,
    messages: list,
    goal: str,
    max_iterations: int,
    iteration: int,
    agent_name: str = "助手",
    continue_code: Optional[str] = None,
    task_name: str = "",
    plan: str = "",
    progress: str = "",
    summary: str = "",
) -> str:
    """建立新任務 → 寫入 _job.json。返回 continue_code。"""
    jobs = _read_jobs(agent_name)
    unique_key = _build_unique_key(user_id, agent_name)
    if unique_key not in jobs:
        jobs[unique_key] = {}

    if continue_code is None:
        for existing_code, existing_task in jobs[unique_key].items():
            if existing_task.get("任務名", "") == goal or existing_task.get("goal", "") == goal:
                continue_code = existing_code
                break
        if continue_code is None:
            continue_code = md5(f"{user_id}_{agent_name}_{goal}_{time.time()}".encode()).hexdigest()[:12]

    task_data = {
        "code": continue_code,
        "任務名": task_name or goal[:30],
        "goal": goal,
        "整體計劃": plan or _extract_plan_from_messages(messages, goal),
        "agent_name": agent_name,
        "max_iterations": max_iterations,
        "iteration": iteration,
        "messages": [],
        "timestamp": time.time(),
        "現在進度": {"百分比": progress or "0%"},
        "上回摘要": summary or _extract_summary_from_messages(messages),
        "對話ID": [],
        "上回對話ID": None,
    }

    jobs[unique_key][continue_code] = task_data
    _write_jobs(agent_name, jobs)
    logging.info(f"[task] _job.json 已建立任務: {continue_code} ({goal[:30]})")
    return continue_code


def get_task(user_id: str, code: str, agent_name: str = "助手") -> Optional[dict]:
    """讀取單一任務"""
    jobs = _read_jobs(agent_name)
    unique_key = _build_unique_key(user_id, agent_name)
    if unique_key in jobs and code in jobs[unique_key]:
        task = jobs[unique_key][code].copy()
        task["messages"] = []
        return task
    return None


def list_tasks(user_id: str, agent_name: str = "助手") -> List[dict]:
    """列出用戶所有任務（摘要）"""
    jobs = _read_jobs(agent_name)
    unique_key = _build_unique_key(user_id, agent_name)
    if unique_key not in jobs:
        return []
    tasks = []
    for code, task in jobs[unique_key].items():
        tasks.append({
            "code": code,
            "任務名": task.get("任務名", task.get("goal", "")),
            "goal": task.get("goal", task.get("任務名", "")),
            "iteration": task.get("iteration", 0),
            "max_iterations": task.get("max_iterations", 5),
            "timestamp": task.get("timestamp", 0),
            "progress": task.get("現在進度", {}).get("百分比", "0%"),
            "summary": task.get("上回摘要", "")[:80]
        })
    return tasks


def update_task(user_id: str, code: str, agent_name: str, updates: dict) -> bool:
    """更新任務的任意欄位。updates 為 dict，合併到現有任務資料中。"""
    jobs = _read_jobs(agent_name)
    unique_key = _build_unique_key(user_id, agent_name)
    if unique_key not in jobs or code not in jobs[unique_key]:
        return False
    jobs[unique_key][code].update(updates)
    _write_jobs(agent_name, jobs)
    logging.info(f"[task] _job.json 已更新任務: {code}")
    return True


def delete_task(user_id: str, code: str, agent_name: str = "助手") -> bool:
    """刪除任務"""
    jobs = _read_jobs(agent_name)
    unique_key = _build_unique_key(user_id, agent_name)
    if unique_key in jobs and code in jobs[unique_key]:
        del jobs[unique_key][code]
        if not jobs[unique_key]:
            del jobs[unique_key]
        _write_jobs(agent_name, jobs)
        logging.info(f"[task] _job.json 已刪除任務: {code}")
        return True
    return False


def update_task_conversation_ids(user_id: str, agent_name: str, code: str, conv_id: int):
    """更新任務的對話 ID 追蹤"""
    jobs = _read_jobs(agent_name)
    unique_key = _build_unique_key(user_id, agent_name)
    if unique_key in jobs and code in jobs[unique_key]:
        task = jobs[unique_key][code]
        if "對話ID" not in task:
            task["對話ID"] = []
        if conv_id not in task["對話ID"]:
            task["對話ID"].append(conv_id)
        task["上回對話ID"] = conv_id
        _write_jobs(agent_name, jobs)


# ---------- LLM 工具接口 ----------

async def handle_task(args, chat_id: str = None, agent_config: Optional[Dict] = None) -> str:
    """任務管理主入口。支援：new, list, get, update, delete。"""

    if agent_config is None:
        agent_config = {}

    # 解析參數
    action = ""
    content = ""

    if isinstance(args, dict):
        action = args.get("action", "").lower()
        content = args.get("content", "")
    elif isinstance(args, str):
        args = args.strip()
        if not args:
            return (
                "📋 **任務管理**\n"
                "用法：\n"
                "  `/task new 任務目標`       – 建立新任務\n"
                "  `/task list`               – 列出所有任務\n"
                "  `/task get 繼續碼`         – 檢視任務詳情\n"
                "  `/task update 繼續碼 欄位=值` – 編輯任務\n"
                "  `/task delete 繼續碼`      – 刪除任務"
            )
        parts = args.split(maxsplit=1)
        action = parts[0].lower()
        content = parts[1] if len(parts) > 1 else ""

    if not action:
        return "❌ 缺少 action 參數。請指定 new / list / get / update / delete。"

    user_id = chat_id or agent_config.get("ADMIN_CHAT_ID", "default_user")
    agent_name = agent_config.get("MOK_AGENT_NAME", "春")

    # ---- 建立任務 ----
    if action == "new":
        if not content:
            return "❌ 請提供任務目標。範例：`/task new 寫一個網頁爬蟲`"
        continue_code = create_task(
            user_id=user_id,
            messages=[],
            goal=content,
            max_iterations=5,
            iteration=0,
            agent_name=agent_name,
        )
        return (
            f"✅ **新任務已建立**\n"
            f"目標：{content}\n"
            f"繼續碼：`{continue_code}`\n"
            f"使用 `/continue {continue_code}` 可恢復執行此任務。"
        )

    # ---- 列出任務 ----
    elif action == "list":
        tasks = list_tasks(user_id, agent_name)
        if not tasks:
            return "📭 目前沒有任何任務。"

        lines = ["📋 **任務清單**\n"]
        for t in sorted(tasks, key=lambda x: x.get("timestamp", 0), reverse=True):
            name = t.get("任務名", t.get("goal", "無名稱"))
            code = t["code"]
            progress = t.get("progress", "0%")
            summary = t.get("summary", "")
            lines.append(f"• `{code}` — {name} ({progress})")
            if summary:
                lines.append(f"  _{summary}_")
        return "\n".join(lines)

    # ---- 檢視任務 ----
    elif action == "get":
        if not content:
            return "❌ 請提供繼續碼。範例：`/task get a1b2c3`"
        task = get_task(user_id, content.strip(), agent_name)
        if not task:
            return f"❌ 找不到任務 `{content.strip()}`。"

        return (
            f"📋 **任務詳情** `{content.strip()}`\n"
            f"🔖 名稱：{task.get('任務名', '無')}\n"
            f"🎯 目標：{task.get('goal', '無')}\n"
            f"📊 進度：{task.get('現在進度', {}).get('百分比', '0%')}\n"
            f"🔄 迭代：{task.get('iteration', 0)}/{task.get('max_iterations', 5)}\n"
            f"📝 計劃：\n{task.get('整體計劃', '無')}\n"
            f"💬 摘要：{task.get('上回摘要', '無')[:200]}"
        )

    # ---- 更新任務 ----
    elif action == "update":
        if not content:
            return "❌ 請提供繼續碼和更新內容。範例：`/task update a1b2c3 goal=新目標`"
        parts = content.split(maxsplit=1)
        code = parts[0].strip()
        rest = parts[1].strip() if len(parts) > 1 else ""

        if not rest:
            return "❌ 請提供要更新的欄位。範例：`/task update a1b2c3 goal=新目標`"

        # 解析 key=value
        updates = {}
        if "=" in rest:
            key, value = rest.split("=", 1)
            key = key.strip()
            value = value.strip()
            if key == "goal":
                updates["goal"] = value
                updates["任務名"] = value[:30]
            elif key == "progress":
                updates["現在進度"] = {"百分比": value}
            elif key == "summary":
                updates["上回摘要"] = value
            elif key == "plan":
                updates["整體計劃"] = value
            elif key == "iteration":
                try:
                    updates["iteration"] = int(value)
                except ValueError:
                    return f"❌ iteration 須為整數，收到：{value}"
            elif key == "max_iterations":
                try:
                    updates["max_iterations"] = int(value)
                except ValueError:
                    return f"❌ max_iterations 須為整數，收到：{value}"
            else:
                return f"❌ 不支援的欄位：{key}。支援：goal, progress, summary, plan, iteration, max_iterations"
        else:
            return "❌ 格式應為 `欄位=值`。範例：`/task update a1b2c3 goal=新目標`"

        ok = update_task(user_id, code, agent_name, updates)
        if not ok:
            return f"❌ 找不到任務 `{code}`。"
        return f"✅ 已更新任務 `{code}`：{rest}"

    # ---- 刪除任務 ----
    elif action == "delete":
        if not content:
            return "❌ 請提供繼續碼。範例：`/task delete a1b2c3`"
        ok = delete_task(user_id, content.strip(), agent_name)
        if not ok:
            return f"❌ 找不到任務 `{content.strip()}`。"
        return f"🗑️ 已刪除任務 `{content.strip()}`。"

    else:
        return f"❌ 未知動作：{action}。支援：new, list, get, update, delete"
