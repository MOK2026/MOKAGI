# -*- coding: utf-8 -*-
"""loop_guard.py — 工具迴圈保護（P0/P1）

提供給 mokagi.py 的工具迴圈使用，目的：避免「超長工具迴圈」因逾時/斷線而沒有收尾。

可由 agent 設定（~/.mok/.<agent>、~/.mok/.mokagi）開關：
  MOK_loop_deadline        秒；預設 0。>0 時，單一任務迴圈總時長超過此值即自動收尾；<=0 關閉。
  MOK_loop_checkpoint      1/0；預設 1。是否每一輪都寫入 pending checkpoint。
  MOK_tool_fail_threshold  整數；預設 50。同一工具連續失敗達此次數即熔斷停手。
  MOK_tool_timeout         秒；預設 0。>0 時單一工具呼叫最長等待（保留給後續異步化）。

本模組只依賴標準庫，可獨立載入與測試。
"""
import json
import time

DEFAULT_DEADLINE = 0
DEFAULT_FAIL_THRESHOLD = 50
DEFAULT_TOOL_TIMEOUT = 0.0


def get_loop_deadline(cfg):
    try:
        return float((cfg or {}).get("MOK_loop_deadline", DEFAULT_DEADLINE))
    except Exception:
        return DEFAULT_DEADLINE


def get_tool_fail_threshold(cfg):
    try:
        v = int((cfg or {}).get("MOK_tool_fail_threshold", DEFAULT_FAIL_THRESHOLD))
        return v if v > 0 else DEFAULT_FAIL_THRESHOLD
    except Exception:
        return DEFAULT_FAIL_THRESHOLD


def get_tool_timeout(cfg):
    try:
        v = float((cfg or {}).get("MOK_tool_timeout", DEFAULT_TOOL_TIMEOUT))
        return v if v > 0 else 0.0
    except Exception:
        return 0.0


def checkpoint_enabled(cfg):
    try:
        return str((cfg or {}).get("MOK_loop_checkpoint", "1")) != "0"
    except Exception:
        return True


_FAIL_KWS = (
    "命令執行超時", "工具執行超時", "執行超時", "超時",
    "timed out", "timeout",
    "traceback (most recent call last)",
)
_FAIL_MARKS = (
    '"success": false', '"success":false', "'success': false", "'success':false",
    '"ok": false', '"status": "error"',
)


def is_failure(result):
    """粗略判斷工具結果是否屬於失敗（逾時 / 例外 / success=false）。"""
    try:
        if isinstance(result, str):
            s = result
        else:
            try:
                s = json.dumps(result, ensure_ascii=False)
            except Exception:
                s = str(result)
    except Exception:
        s = str(result)
    low = (s or "").lower()
    if not low:
        return False
    for kw in _FAIL_KWS:
        if kw.lower() in low:
            return True
    for mk in _FAIL_MARKS:
        if mk in low:
            return True
    return False


def build_pause_report(reason, iteration, max_iterations, code):
    return (
        "⏳ 已自動收尾並保存進度（%s）。\n"
        "· 進度：第 %d/%d 輪\n"
        "· 繼續碼：`%s`\n"
        "直接回覆「繼續」即可從上次進度接續；或叫我針對卡住的步驟換個做法。"
        % (reason, int(iteration) + 1, int(max_iterations), code)
    )
