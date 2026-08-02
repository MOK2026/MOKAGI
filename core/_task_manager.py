"""task_manager.py - 任務管理薄包裝層
所有 _job.json 讀寫已移至 tools/task.py（唯一真相來源）。
本模組僅重新導出，保持向後相容。
"""
import sys
import os

# 確保 tools/ 在 sys.path 中
TOOLS_DIR = os.path.join(os.path.dirname(os.path.dirname(__file__)), "tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

from task import (  # noqa: E402, F401
    # CRUD API
    create_task as save_pending_task,
    get_task as load_pending_task,
    list_tasks as list_pending_tasks,
    update_task as update_pending_task,
    delete_task as delete_pending_task,
    update_task_conversation_ids,
    # 輔助函數
    _upgrade_legacy_task,
    _extract_plan_from_messages,
    _extract_summary_from_messages,
    # 底層存取（供特殊情況使用）
    _read_jobs,
    _write_jobs,
    _get_job_file,
    _build_unique_key,
)

KEEP_RECENT_ROUNDS = 1
