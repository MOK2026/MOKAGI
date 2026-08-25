# -*- coding: utf-8 -*-
"""
money.py - MoneyPrinterTurbo 短影音生成工具

用法:
    /money 主題                     → 生成 9:16 直式短影音
    /money subject=主題 aspect=16:9 paragraphs=3
    LLM tool call: money_video(subject=..., aspect=..., paragraphs=..., language=...)

底層: /home/ubuntu/.mok/mpt/MoneyPrinterTurbo/cli.py (uv venv)
"""
import os
import re
import asyncio
import json
import logging
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

PLUGIN_INFO = {
    "command": "/money",
    "icon": "🎬",
    "handler": "handle_money",
    "description": "AI 短影音生成：輸入主題，自動寫腳本、配音、配素材、加字幕合成影片（MoneyPrinterTurbo）。",
    "intent_keywords": [
        ("做影片", "/money"),
        ("生成影片", "/money"),
        ("短影音", "/money"),
        ("AI影片", "/money"),
    ],
    "tool_schema": {
        "name": "money_video",
        "description": (
            "生成 AI 短影音。給定一個主題或關鍵詞，自動生成腳本、配音、素材、字幕並合成短影音。"
            "可用 stop_at 控制只生成到中間步驟（script=只寫腳本，audio=含配音，materials=含素材，video=完整影片）。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "影片主題或關鍵詞，例如：深海裡的微光"},
                "language": {"type": "string", "description": "語言代碼：zh-CN（簡中）/ zh-TW（繁中）/ en-US（英文）", "default": "zh-CN"},
                "aspect": {"type": "string", "description": "畫面比例：9:16（直式抖音）/ 16:9（橫式）", "default": "9:16"},
                "paragraphs": {"type": "integer", "description": "段落數，每段約10秒旁白，1-5", "default": 2},
                "stop_at": {"type": "string", "description": "生成到哪一步：script/audio/subtitle/materials/video", "default": "video"},
            },
            "required": ["subject"],
        },
    },
    "update": "202608220715",
    "naturalize_func": "naturalize_money_result",
}

MPT_DIR = "/home/ubuntu/.mok/mpt/MoneyPrinterTurbo"
PYTHON = os.path.join(MPT_DIR, ".venv", "bin", "python")
CLI = os.path.join(MPT_DIR, "cli.py")
STORAGE = os.path.join(MPT_DIR, "storage", "tasks")
LOCAL_VIDEOS = os.path.join(MPT_DIR, "storage", "local_videos")


def _parse_args(args) -> Dict:
    """統一解析 str 或 dict 參數。"""
    if isinstance(args, dict):
        return args
    s = str(args).strip()
    if not s:
        return {}
    # 支援 key=value 或純文字（當作 subject）
    kv = {}
    rest = []
    for token in re.split(r"\s+", s):
        if "=" in token:
            k, _, v = token.partition("=")
            kv[k.strip()] = v.strip()
        else:
            rest.append(token)
    if "subject" not in kv and rest:
        kv["subject"] = " ".join(rest)
    return kv


async def handle_money(args, user_id: str = None, agent_config: Optional[Dict] = None) -> str:
    try:
        p = _parse_args(args)
        subject = (p.get("subject") or "").strip()
        if not subject:
            return json.dumps({"success": False, "error": "請提供影片主題，例如：/money 深海裡的微光"}, ensure_ascii=False)

        language = p.get("language", "zh-CN")
        aspect = p.get("aspect", "9:16")
        paragraphs = int(p.get("paragraphs", 2))
        stop_at = p.get("stop_at", "video")

        if aspect not in ("9:16", "16:9", "1:1"):
            aspect = "9:16"
        if paragraphs < 1 or paragraphs > 6:
            paragraphs = 2
        if stop_at not in ("script", "audio", "subtitle", "materials", "video"):
            stop_at = "video"

        cmd = [
            PYTHON, CLI,
            "--video-subject", subject,
            "--video-language", language,
            "--video-aspect", aspect,
            "--paragraph-number", str(paragraphs),
            "--stop-at", stop_at,
        ]

        logger.info(f"[money] running: {' '.join(cmd)}")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=MPT_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=900)
        except asyncio.TimeoutError:
            proc.kill()
            return json.dumps({"success": False, "error": "生成逾時（>15分鐘）"}, ensure_ascii=False)

        text = out.decode("utf-8", errors="ignore")
        result = _summarize(subject, stop_at, text)
        # 完整影片生成後自動清理已下載素材，避免堆積佔用磁碟（stop_at=materials 時保留素材供使用）
        if stop_at == "video":
            try:
                removed = _cleanup_local_videos()
                if removed:
                    logger.info(f"[money] 生成完成，已自動清理 {removed} 個素材檔案")
            except Exception:
                logger.exception("[money] 素材清理失敗（不影響結果）")
        return result
    except Exception as e:
        logger.exception("[money] error")
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


def _cleanup_local_videos() -> int:
    """清理 local_videos 素材下載目錄，返回刪除的檔案數。"""
    if not os.path.isdir(LOCAL_VIDEOS):
        return 0
    removed = 0
    for f in os.listdir(LOCAL_VIDEOS):
        fp = os.path.join(LOCAL_VIDEOS, f)
        try:
            if os.path.isfile(fp):
                os.remove(fp)
                removed += 1
        except OSError:
            logger.warning(f"[money] 無法刪除素材: {fp}")
    return removed


def _summarize(subject: str, stop_at: str, log_text: str) -> str:
    """從 CLI 日誌中提取結果摘要。"""
    # 找最新 task_id 對應的輸出目錄
    newest_task = None
    newest_mtime = 0
    if os.path.isdir(STORAGE):
        for name in os.listdir(STORAGE):
            d = os.path.join(STORAGE, name)
            if os.path.isdir(d):
                m = os.path.getmtime(d)
                if m > newest_mtime:
                    newest_mtime = m
                    newest_task = name

    finals = []
    if newest_task:
        tdir = os.path.join(STORAGE, newest_task)
        for f in sorted(os.listdir(tdir)):
            if f.startswith("final-") and f.endswith(".mp4"):
                finals.append(os.path.join(tdir, f))

    error_lines = [ln for ln in log_text.splitlines() if "ERROR" in ln or "failed" in ln.lower()]
    errors = error_lines[-3:] if error_lines else []

    result = {
        "success": True,
        "subject": subject,
        "stage": stop_at,
        "output_dir": os.path.join(STORAGE, newest_task) if newest_task else None,
        "videos": finals,
    }
    if errors:
        result["warnings"] = errors
    return json.dumps(result, ensure_ascii=False)


def naturalize_money_result(result_str: str) -> str:
    """把 JSON 結果轉成人話。"""
    try:
        d = json.loads(result_str) if isinstance(result_str, str) else result_str
    except Exception:
        return result_str
    if not d.get("success"):
        return f"❌ 生成失敗：{d.get('error', '未知錯誤')}"
    subject = d.get("subject", "")
    stage = d.get("stage", "video")
    videos = d.get("videos") or []
    stage_name = {
        "script": "腳本", "audio": "配音", "subtitle": "字幕",
        "materials": "素材", "video": "完整影片",
    }.get(stage, stage)
    if videos:
        return f"🎬 「{subject}」{stage_name}生成完成！共 {len(videos)} 支：\n" + "\n".join(videos)
    if d.get("output_dir"):
        return f"🎬 「{subject}」{stage_name}已生成，輸出目錄：{d['output_dir']}"
    return f"🎬 「{subject}」{stage_name}已生成。"
