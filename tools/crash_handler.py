# ------------------------------------------------------------------------------------ #
# 工具名稱: crash_handler (崩潰處理器)
# 用途: 監控 pm2 日誌中的 Python 錯誤/崩潰，自動提取 traceback 並調用 autofix 嘗試修正。
# 2026-08-08 建立
# ------------------------------------------------------------------------------------ #

PLUGIN_INFO = {
    "command": "/crash",
    "icon": "💥",
    "handler": "handle_crash",
    "description": "崩潰處理器：掃描 pm2 日誌中的錯誤，自動提取 traceback 並調用 autofix 修正。",
    "intent_keywords": [
        ("/崩潰", "/crash check"),
        ("/錯誤檢查", "/crash check"),
        ("/自動修正", "/crash fix"),
        ("/crash", "/crash check"),
    ],
    "tool_schema": {
        "name": "crash_handler",
        "description": (
            "崩潰處理工具：掃描 pm2 日誌中的 Python 錯誤與 traceback，"
            "自動提取錯誤資訊（檔案、行號、錯誤類型、完整 traceback），"
            "並可調用 autofix 嘗試自動修正。\n"
            "支援三個動作：check（掃描日誌找錯誤）、fix（修正指定錯誤）、monitor（持續監控）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["check", "fix", "monitor"],
                    "description": (
                        "操作類型：\n"
                        "- check: 掃描 pm2 日誌，找出最近的 Python 錯誤與 traceback\n"
                        "- fix: 對 check 找到的錯誤嘗試自動修正（需先 check）\n"
                        "- monitor: 持續監控模式（循環 check + fix）"
                    )
                },
                "lines": {
                    "type": "integer",
                    "description": "掃描日誌的行數（僅 check 使用，預設 200）",
                    "default": 200
                },
                "process": {
                    "type": "string",
                    "description": "pm2 進程名稱（預設 mok_agi）。例如 mok_agi, mok_靜"
                }
            },
            "required": ["action"]
        }
    },
    "update": "202608080000_初始版"
}

import logging, json, re, subprocess, os, tempfile
from typing import Optional, Dict
from datetime import datetime

# 嘗試導入 mokagi，若不在 agent 環境則降級
try:
    import mokagi
except ImportError:
    mokagi = None

logger = logging.getLogger(__name__)

# ---- traceback 解析 ----

# 匹配 Python traceback 行：  File "path", line N, in func
TRACEBACK_FILE_RE = re.compile(
    r'^\s*File\s+"([^"]+)",\s*line\s+(\d+),?\s*in\s+(\S+)'
)
# 匹配錯誤類型行：SomeError: message
ERROR_TYPE_RE = re.compile(r'^(\w+(?:\.\w+)*Error|[A-Z]\w*Exception|[A-Z]\w*Error)\s*:?\s*(.*)')
# 匹配更通用的錯誤行
GENERIC_ERROR_RE = re.compile(r'(Error|Exception|Traceback|Failed|失敗|錯誤)', re.IGNORECASE)


def _parse_traceback_lines(log_lines: list) -> list:
    """從日誌行中提取結構化的 traceback 資訊。

    返回 list[dict]，每個 dict 包含：
        - error_type: str       (如 ModuleNotFoundError)
        - error_message: str    (錯誤訊息)
        - frames: list[dict]    (每個 frame: file, line, function)
        - raw_lines: list[str]  (原始日誌行)
        - start_idx: int        (在原始日誌中的起始索引)
    """
    errors = []
    i = 0
    while i < len(log_lines):
        line = log_lines[i]
        # 偵測 traceback 開頭 "Traceback (most recent call last):"
        if 'Traceback (most recent call last)' in line:
            frames = []
            j = i + 1
            error_type = ""
            error_message = ""
            # 收集 File 行
            while j < len(log_lines):
                m = TRACEBACK_FILE_RE.match(log_lines[j])
                if m:
                    frames.append({
                        "file": m.group(1),
                        "line": int(m.group(2)),
                        "function": m.group(3)
                    })
                    j += 1
                    continue
                # 嘗試匹配錯誤類型行
                em = ERROR_TYPE_RE.match(log_lines[j])
                if em:
                    error_type = em.group(1)
                    error_message = em.group(2).strip()
                    j += 1
                    # 有時錯誤訊息會跨行，繼續往後收集非 File 行
                    while j < len(log_lines) and not TRACEBACK_FILE_RE.match(log_lines[j]) and 'Traceback' not in log_lines[j]:
                        extra = log_lines[j].strip()
                        if extra and not extra.startswith('[') and not extra.startswith('During'):
                            if error_message:
                                error_message += "\n" + extra
                            else:
                                error_message = extra
                        j += 1
                    break
                # 非 File 也非錯誤行 → 可能是純錯誤訊息
                em2 = ERROR_TYPE_RE.match(log_lines[j])
                if em2 and not frames:
                    # 沒有 File 行的錯誤（可能只有一行）
                    error_type = em2.group(1)
                    error_message = em2.group(2).strip()
                    j += 1
                    break
                j += 1

            raw = log_lines[i:j]
            errors.append({
                "error_type": error_type,
                "error_message": error_message,
                "frames": frames,
                "raw_lines": raw,
                "start_idx": i
            })
            i = j
        else:
            i += 1
    return errors


def _find_error_file(frames: list) -> Optional[str]:
    """從 frames 中找出最可能屬於專案內部的檔案（非 site-packages）。"""
    project_root = os.path.expanduser("~/.mok")
    for frame in frames:
        f = frame.get("file", "")
        if f.startswith(project_root):
            return f
    # fallback: 返回第一個 frame 的檔案
    if frames:
        return frames[0].get("file", "")
    return None


def _get_pm2_logs(process: str = "mok_agi", lines: int = 200) -> str:
    """讀取 pm2 日誌。"""
    try:
        result = subprocess.run(
            ["pm2", "logs", process, "--lines", str(lines), "--nostream"],
            capture_output=True, text=True, timeout=15
        )
        return result.stdout + "\n" + result.stderr
    except FileNotFoundError:
        # pm2 不在 PATH
        pm2_paths = [
            os.path.expanduser("~/.nvm/versions/node/*/bin/pm2"),
            "/usr/local/bin/pm2",
            "/usr/bin/pm2",
        ]
        for p in pm2_paths:
            import glob
            for matched in glob.glob(p):
                if os.path.exists(matched):
                    result = subprocess.run(
                        [matched, "logs", process, "--lines", str(lines), "--nostream"],
                        capture_output=True, text=True, timeout=15
                    )
                    return result.stdout + "\n" + result.stderr
        return ""
    except Exception as e:
        return f"# PM2_LOG_ERROR: {e}"


def _check_for_errors(process: str = "mok_agi", lines: int = 200) -> dict:
    """掃描 pm2 日誌，回傳結構化錯誤列表。"""
    raw = _get_pm2_logs(process, lines)
    if not raw or not raw.strip():
        return {
            "success": True,
            "process": process,
            "lines_scanned": 0,
            "error_count": 0,
            "errors": [],
            "message": "日誌為空或無法讀取。"
        }

    log_lines = raw.split("\n")
    errors = _parse_traceback_lines(log_lines)

    # 如果有錯誤，補充「首要錯誤檔案」
    for err in errors:
        err["primary_file"] = _find_error_file(err.get("frames", []))

    return {
        "success": True,
        "process": process,
        "lines_scanned": len(log_lines),
        "error_count": len(errors),
        "errors": errors,
        "raw_log_tail": "\n".join(log_lines[-20:]) if len(log_lines) > 20 else raw
    }


async def handle_crash(args, chat_id: str = None, agent_config: Optional[Dict] = None):
    """處理 /crash 命令。

    Args:
        args: str (命令模式) 或 dict (LLM 工具調用)
    """
    if agent_config is None and mokagi:
        agent_config = mokagi._agent_config

    # 標準化 args
    if isinstance(args, dict):
        action = args.get("action", "check")
        lines = args.get("lines", 200)
        process = args.get("process", "mok_agi")
    else:
        parts = (args or "").strip().split()
        action = parts[0].lower() if parts else "check"
        lines = 200
        process = "mok_agi"
        # 支援 /crash check 300 或 /crash check mok_agi 300
        for p in parts[1:]:
            if p.isdigit():
                lines = int(p)
            elif p.startswith("mok"):
                process = p

    if action == "check":
        result = _check_for_errors(process=process, lines=lines)

        if result["error_count"] == 0:
            return json.dumps({
                "success": True,
                "message": f"✅ 掃描了 {result['lines_scanned']} 行日誌（{process}），沒有發現 Python 錯誤。",
                "details": result
            }, ensure_ascii=False)

        # 格式化輸出
        summary_lines = [
            f"💥 **發現 {result['error_count']} 個錯誤**（掃描 {result['lines_scanned']} 行，進程 {process}）",
            ""
        ]
        for idx, err in enumerate(result["errors"], 1):
            summary_lines.append(f"--- 錯誤 #{idx} ---")
            summary_lines.append(f"類型: {err['error_type'] or '未知'}")
            summary_lines.append(f"訊息: {err['error_message'][:200]}")
            summary_lines.append(f"主要檔案: {err.get('primary_file', 'N/A')}")
            if err.get("frames"):
                last_frame = err["frames"][-1]
                summary_lines.append(f"位置: {last_frame['file']}:{last_frame['line']} in {last_frame['function']}")
            summary_lines.append(f"Frames: {len(err['frames'])} 層")
            summary_lines.append("")

        summary_lines.append("💡 使用 `/crash fix` 嘗試自動修正這些錯誤。")

        return json.dumps({
            "success": True,
            "message": "\n".join(summary_lines),
            "details": result
        }, ensure_ascii=False)

    elif action == "fix":
        # 先檢查錯誤
        result = _check_for_errors(process=process, lines=lines)
        if result["error_count"] == 0:
            return json.dumps({
                "success": True,
                "message": "✅ 沒有發現需要修正的錯誤。"
            }, ensure_ascii=False)

        # 對每個錯誤嘗試呼叫 autofix
        fix_results = []
        for err in result["errors"]:
            primary_file = err.get("primary_file", "")
            error_type = err.get("error_type", "")
            error_message = err.get("error_message", "")
            frames = err.get("frames", [])

            # 組合完整錯誤訊息
            full_error = f"{error_type}: {error_message}" if error_type else error_message
            if frames:
                last = frames[-1]
                full_error += f"\n  at {last['file']}:{last['line']} in {last['function']}"

            fix_results.append({
                "error_type": error_type,
                "error_message": error_message,
                "primary_file": primary_file,
                "frames": [f"{fr['file']}:{fr['line']}" for fr in frames],
                "full_error": full_error,
                "fix_note": (
                    "⚠️ autofix 需由 LLM 調用。請使用 autofix 工具傳入以下參數：\n"
                    f"  - code: (從 {primary_file} 讀取)\n"
                    f"  - error: {full_error[:300]}\n"
                    f"  - context: crash_handler 自動檢測"
                )
            })

        summary_lines = [
            f"🔧 **準備修正 {len(fix_results)} 個錯誤**",
            ""
        ]
        for idx, fr in enumerate(fix_results, 1):
            summary_lines.append(f"--- 錯誤 #{idx} ---")
            summary_lines.append(f"類型: {fr['error_type']}")
            summary_lines.append(f"檔案: {fr['primary_file']}")
            summary_lines.append(f"錯誤: {fr['error_message'][:150]}")
            summary_lines.append(f"Frames: {' → '.join(fr['frames'][-3:])}")
            summary_lines.append(f"修正指引: {fr['fix_note'][:200]}")
            summary_lines.append("")

        return json.dumps({
            "success": True,
            "message": "\n".join(summary_lines),
            "fix_targets": fix_results,
            "hint": "請 LLM 使用 autofix 工具逐一修正上述錯誤，然後重新部署。"
        }, ensure_ascii=False)

    elif action == "monitor":
        # 持續監控模式：等同 check，LLM 可定時調用
        result = _check_for_errors(process=process, lines=lines)
        if result["error_count"] == 0:
            return json.dumps({
                "success": True,
                "message": f"🟢 監控 {process}：無錯誤（{result['lines_scanned']} 行）",
                "details": result
            }, ensure_ascii=False)
        else:
            summary = f"🔴 監控 {process}：發現 {result['error_count']} 個錯誤"
            return json.dumps({
                "success": True,
                "message": summary,
                "details": result,
                "hint": "使用 /crash fix 自動修正"
            }, ensure_ascii=False)

    else:
        return json.dumps({
            "success": False,
            "error_type": "unknown_action",
            "error_message": f"未知動作: {action}。支援: check, fix, monitor"
        }, ensure_ascii=False)
