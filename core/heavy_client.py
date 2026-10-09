# -*- coding: utf-8 -*-
"""heavy_client.py — 重工常駐服務的客戶端（P1-6 2026-10-04 by 稚）

給 mok_web / tools 用：把重工 shell 丟給 127.0.0.1:MOK_HEAVY_PORT 的常駐服務執行，
讓呼叫端（web 請求執行緒）只做 HTTP I/O，不佔 GIL。
服務沒開時會**自動拉起**（detached），確保 self-healing。

對外：
    is_heavy(cmd) -> bool        判斷這條命令算不算重工
    run(cmd, timeout=300, cwd=None) -> dict|None
        成功回 {"rc","stdout","stderr","timeout","elapsed"}；服務不可用回 None（呼叫端自理）。

停用：MOK_HEAVY_OFFLOAD=0（呼叫端自行判斷要不要用）
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
import urllib.request
import urllib.error

_HOST = os.environ.get("MOK_HEAVY_HOST", "127.0.0.1")
_PORT = int(os.environ.get("MOK_HEAVY_PORT", "5011") or 5011)
_BASE = "http://%s:%d" % (_HOST, _PORT)
_SVC = os.path.join(os.path.expanduser("~"), ".mok", "tools", "heavy_service.py")
_LOG = os.path.join(os.path.expanduser("~"), ".mok", "logs", "heavy_service.log")

_LK = threading.Lock()
_LAST_ENSURE = 0.0


def enabled():
    return os.environ.get("MOK_HEAVY_OFFLOAD", "1").strip().lower() not in ("0", "false", "no", "off")


_HEAVY_PAT = [
    r"(^|[\s;&|(])(grep|rg|egrep|fgrep|ack|ag)\b",
    r"(^|[\s;&|(])find\b",
    r"\bffmpeg\b", r"\bffprobe\b",
    r"\bdu\b", r"\bncdu\b", r"\btar\b", r"\bzip\b", r"\bunzip\b", r"\bgzip\b",
    r"\bgraphify\b", r"\bmoney_video\b", r"\bcomic\b",
    r"\.log\b",
]
_HEAVY_RE = re.compile("|".join(_HEAVY_PAT))
_BIG_FILE_RE = re.compile(r"(?<![\w./-])(/[^\s;&|()<>'\"]+)")


def is_heavy(cmd):
    """重工判定：指令命中重工樣式，或引用了 >5MB 的檔案。"""
    if not cmd:
        return False
    if os.environ.get("MOK_HEAVY_ALL", "0").strip().lower() in ("1", "true", "yes", "on"):
        return True
    if _HEAVY_RE.search(cmd):
        return True
    try:
        for m in _BIG_FILE_RE.finditer(cmd):
            p = m.group(1)
            if os.path.isfile(p) and os.path.getsize(p) > 5 * 1024 * 1024:
                return True
    except Exception:
        pass
    return False


def _health(timeout=0.6):
    try:
        with urllib.request.urlopen(_BASE + "/health", timeout=timeout) as r:
            return json.loads(r.read().decode("utf-8"))
    except Exception:
        return None


def _spawn():
    try:
        os.makedirs(os.path.dirname(_LOG), exist_ok=True)
        lf = open(_LOG, "a", encoding="utf-8")
        subprocess.Popen(
            [sys.executable, _SVC, "--port", str(_PORT)],
            stdout=lf, stderr=lf, stdin=subprocess.DEVNULL,
            start_new_session=True,
        )
        return True
    except Exception:
        return False


def ensure_service(wait_s=5.0):
    """確認服務在線；不在就拉起並等待。回傳 health dict 或 None。"""
    global _LAST_ENSURE
    h = _health()
    if h:
        return h
    with _LK:
        # 二次檢查（避免多執行緒同時拉）
        h = _health()
        if h:
            return h
        now = time.time()
        if now - _LAST_ENSURE < 2.0:
            return None
        _LAST_ENSURE = now
        if not _spawn():
            return None
    deadline = time.time() + wait_s
    while time.time() < deadline:
        h = _health()
        if h:
            return h
        time.sleep(0.25)
    return None


def run(cmd, timeout=300, cwd=None):
    """把命令丟給常駐服務；成功回結果 dict，服務不可用回 None。"""
    if ensure_service() is None:
        return None
    payload = json.dumps({"cmd": cmd, "timeout": int(timeout), "cwd": cwd or ""}).encode("utf-8")
    req = urllib.request.Request(_BASE + "/run", data=payload,
                                 headers={"Content-Type": "application/json"}, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=int(timeout) + 30) as r:
            obj = json.loads(r.read().decode("utf-8") or "{}")
        if obj.get("ok"):
            return obj
        return None
    except Exception:
        return None
