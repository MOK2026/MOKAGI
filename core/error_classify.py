#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
error_classify.py — L1 錯誤分類層
「四層自我修正架構」第 1 層（設計文件：jobs/2026-09-29/自我修正機制_設計.md）

職責（唯一）：把一個 exception 判成三類之一，並給出重試建議。
    transient  暫時性資源競爭／遠端抖動 → 指數退避重試，不要改程式碼
    bug        程式碼或資料形狀錯誤     → 直接進 autofix（LLM 改 code）
    fatal      不可恢復（OOM／中止）    → 不重試，寫事件檔並通知主人

設計原則：
    1. 純函數、無副作用、不碰 DB、不呼叫 LLM
       → 所以它永遠不會被它要處理的那把鎖卡住。
    2. 判斷順序：fatal（型別+關鍵字）→ transient（型別+關鍵字）→ bug（型別+關鍵字）→ 預設 bug。
       先判 fatal、再判 transient，避免「unable to open database file: disk i/o error」被判成 transient。
    3. 支援例外鏈（__cause__ / __context__）：鏈上任一層命中即採用該層的分類。
    4. 不 import 重型模組（不用 aiohttp / requests），只用標準庫。

對外 API：
    classify(exc)              -> Classified
    classify_text(text)        -> Classified     （給日誌掃描用，只有字串）
    should_retry(exc_or_cls)   -> bool
    backoff_delay(kind, n)     -> float          （第 n 次重試前的等待秒數，n 從 1 起算）
    backoff_plan(kind)         -> list[float]    （完整退避序列）
    describe(exc)              -> str            （一行人類可讀摘要）

自測：python3 error_classify.py
"""

from __future__ import annotations

import asyncio
import json
import random
import socket
import sqlite3
from dataclasses import dataclass, field, asdict
from typing import Any, Dict, List, Optional, Tuple

__all__ = [
    "TRANSIENT", "BUG", "FATAL",
    "Classified", "classify", "classify_text", "should_retry",
    "backoff_delay", "backoff_plan", "describe", "POLICY",
]

TRANSIENT = "transient"
BUG = "bug"
FATAL = "fatal"

MAX_CHAIN = 6  # 例外鏈最多看幾層

# --------------------------------------------------------------------------
# 關鍵字表（全部小寫比對；只比對錯誤訊息，不比對檔名）
# --------------------------------------------------------------------------
TRANSIENT_KEYWORDS: Tuple[str, ...] = (
    # SQLite 競爭
    "database is locked",
    "database table is locked",
    "database schema is locked",
    "database is busy",
    "sqlite_busy",
    "sqlite_locked",
    # 逾時
    "timeout",
    "timed out",
    "read timed out",
    "connect timeout",
    "deadline exceeded",
    # HTTP 限流／暫時性狀態碼
    "rate limit",
    "ratelimit",
    "too many requests",
    "429",
    "502",
    "503",
    "504",
    "temporarily unavailable",
    "service unavailable",
    "server disconnected",
    "server error",
    "overloaded",
    "try again",
    "please retry",
    "retry later",
    # 網路／連線
    "connection reset",
    "connection aborted",
    "connection refused",
    "connection closed",
    "broken pipe",
    "eof occurred",
    "network is unreachable",
    "temporary failure in name resolution",
    "name or service not known",
    "no route to host",
    "handshake",
    "bad gateway",
    "gateway time-out",
    "connection error",
    "connection failed",
    "network connection",
    "network error",
    "request failed",
    "request exception",
    "failed to fetch",
    "socket hang up",
    "econnreset",
    "econnrefused",
    "etimedout",
    "getaddrinfo",
    "i/o timeout",
    "empty response",
    "upstream request",
    "all providers failed",
    # 前端平台抖動
    "conflict: terminated by other getupdates",
    "message is not modified",
    "flood control",
    "retry_after",
)

FATAL_KEYWORDS: Tuple[str, ...] = (
    # 資源枯竭／不可恢復
    "out of memory",
    "cannot allocate memory",
    "no space left on device",
    "disk full",
    "database or disk is full",
    "read-only database",
    "database disk image is malformed",
    "file is not a database",
    "unable to open database file",
    "permission denied",
    "operation not permitted",
    "cannot open shared object",
    "no such file or directory: '/",  # 設定／路徑壞掉
    "quota exceeded",
    "billing",
    # 帳務／額度耗盡（DeepSeek 402 Insufficient Balance）：重試無用，需主人補值 → fatal
    "insufficient balance",
    "insufficient_quota",
    "insufficient quota",
    "insufficient funds",
    "insufficient account balance",
    "account balance is insufficient",
    "402 payment required",
    "余额不足",
    "餘額不足",
    # 設定檔／環境缺失：重試與 autofix 都救不了，需要主人處理
    "配置文件",
    "config file",
    "configuration file",
    "缺少 tg_token",
    "缺少 mok_tg_token",
    "missing api key",
    "missing environment variable",
    # 純文字（日誌）情境下，型別名本身就是硬證據
    "memoryerror",
    "keyboardinterrupt",
    "systemexit",
    "outofmemoryerror",
)

BUG_KEYWORDS: Tuple[str, ...] = (
    "invalid syntax",
    "syntaxerror",
    "nameerror",
    "is not defined",
    "attributeerror",
    "has no attribute",
    "object is not subscriptable",
    "object is not callable",
    "typeerror",
    "unexpected keyword argument",
    "positional argument",
    "cannot unpack",
    "unsupported operand",
    "valueerror",
    "invalid literal for int()",
    "keyerror",
    "indexerror",
    "list index out of range",
    "modulenotfounderror",
    "no module named",
    "importerror",
    "notimplementederror",
    "zerodivisionerror",
    "division by zero",
    "assertionerror",
    "indentationerror",
    "unboundlocalerror",
    "recursionerror",
    "jsondecodeerror",
    "expecting value",
    "unique constraint failed",
    "integrityerror",
    "schema",
)

# --------------------------------------------------------------------------
# 例外型別表
# --------------------------------------------------------------------------
TRANSIENT_EXC_TYPES: Tuple[type, ...] = (
    TimeoutError,
    ConnectionError,
    ConnectionResetError,
    ConnectionAbortedError,
    ConnectionRefusedError,
    BrokenPipeError,
    BlockingIOError,
    InterruptedError,
    socket.timeout,
    socket.gaierror,
)

FATAL_EXC_TYPES: Tuple[type, ...] = (
    MemoryError,
    SystemExit,
    KeyboardInterrupt,
    GeneratorExit,
)

BUG_EXC_TYPES: Tuple[type, ...] = (
    SyntaxError,
    IndentationError,
    NameError,
    UnboundLocalError,
    AttributeError,
    TypeError,
    KeyError,
    IndexError,
    ImportError,
    ModuleNotFoundError,
    NotImplementedError,
    ZeroDivisionError,
    AssertionError,
    RecursionError,
)

# --------------------------------------------------------------------------
# 重試政策（L2 會直接讀這張表，不要在別處寫死次數）
# --------------------------------------------------------------------------
POLICY: Dict[str, Dict[str, Any]] = {
    TRANSIENT: {"retryable": True, "max_attempts": 5, "base": 0.5, "factor": 2.0, "cap": 8.0, "jitter": 0.25},
    BUG: {"retryable": False, "max_attempts": 0, "base": 0.0, "factor": 1.0, "cap": 0.0, "jitter": 0.0},
    FATAL: {"retryable": False, "max_attempts": 0, "base": 0.0, "factor": 1.0, "cap": 0.0, "jitter": 0.0},
}


@dataclass
class Classified:
    """分類結果。"""

    kind: str
    reason: str
    matched: str = ""
    exc_type: str = ""
    message: str = ""
    retryable: bool = False
    max_attempts: int = 0
    backoff: List[float] = field(default_factory=list)
    chain: List[str] = field(default_factory=list)

    @property
    def is_transient(self) -> bool:
        return self.kind == TRANSIENT

    @property
    def is_bug(self) -> bool:
        return self.kind == BUG

    @property
    def is_fatal(self) -> bool:
        return self.kind == FATAL

    def as_dict(self) -> Dict[str, Any]:
        d = asdict(self)
        d["is_transient"] = self.is_transient
        return d

    def __str__(self) -> str:  # pragma: no cover - 只為人類閱讀
        return f"[{self.kind}] {self.exc_type}: {self.message[:120]} (原因: {self.reason})"


# --------------------------------------------------------------------------
# 內部工具
# --------------------------------------------------------------------------
def _exception_chain(exc: BaseException) -> List[BaseException]:
    """沿 __cause__ / __context__ 收集例外鏈（去重、限長）。"""

    chain: List[BaseException] = []
    seen = set()
    cur: Optional[BaseException] = exc
    while cur is not None and len(chain) < MAX_CHAIN:
        if id(cur) in seen:
            break
        seen.add(id(cur))
        chain.append(cur)
        cur = cur.__cause__ or cur.__context__
    return chain


def _type_chain(exc: BaseException) -> List[str]:
    return [f"{type(e).__name__}" for e in _exception_chain(exc)]


def _message_of(exc: BaseException) -> str:
    try:
        text = str(exc)
    except Exception:
        text = ""
    if not text:
        text = type(exc).__name__
    return text


def _hit(
    chain: List[BaseException],
    types: Tuple[type, ...],
    keywords: Tuple[str, ...],
) -> Optional[Tuple[str, str, str, str]]:
    """
    在整條例外鏈上找命中。
    回傳 (kind_source, matched, reason, message) 或 None。
    型別命中優先於關鍵字命中（型別是硬證據）。
    """
    for e in chain:
        if isinstance(e, types):
            return ("type", type(e).__name__, f"型別命中 {type(e).__name__}", _message_of(e))
    for e in chain:
        msg = _message_of(e)
        low = msg.lower()
        for kw in keywords:
            if kw in low:
                return ("keyword", kw, f"關鍵字命中 '{kw}'", msg)
    return None


def _sqlite_hit(chain: List[BaseException]) -> Optional[Tuple[str, str, str, str]]:
    """sqlite3 例外單獨判斷（比對型別與 sqlite 錯誤字串）。"""

    for e in chain:
        if not isinstance(e, sqlite3.Error):
            continue
        name = type(e).__name__
        msg = _message_of(e)
        low = msg.lower()
        if isinstance(e, sqlite3.IntegrityError):
            return ("type", name, "IntegrityError（資料違反約束）", msg)
        if isinstance(e, sqlite3.ProgrammingError):
            return ("type", name, "ProgrammingError（SQL／參數寫錯）", msg)
        if isinstance(e, sqlite3.OperationalError):
            if any(kw in low for kw in TRANSIENT_KEYWORDS):
                kw = next(kw for kw in TRANSIENT_KEYWORDS if kw in low)
                return ("keyword", f"sqlite3.OperationalError/{kw}", f"SQLite 競爭：'{kw}'", msg)
            if any(kw in low for kw in FATAL_KEYWORDS):
                kw = next(kw for kw in FATAL_KEYWORDS if kw in low)
                return ("keyword", f"sqlite3.OperationalError/{kw}", f"SQLite 不可恢復：'{kw}'", msg)
            return ("type", name, "OperationalError 但無法判定，保守視為 bug", msg)
        if isinstance(e, sqlite3.DatabaseError):
            return ("type", name, "DatabaseError（結構／檔案層級錯誤）", msg)
    return None


# --------------------------------------------------------------------------
# 對外 API
# --------------------------------------------------------------------------
def classify(exc: BaseException) -> Classified:
    """
    分類一個 exception。

    判斷順序：fatal → transient → bug → 預設 bug。
    fatal 先判，因為「no space left on device」這類訊息裡常同時含有
    「disk i/o error」等 transient 關鍵字，若先判 transient 會誤重試。
    """

    if not isinstance(exc, BaseException):
        raise TypeError("classify() 需要 BaseException（或其子類）")

    chain = _exception_chain(exc)
    types_chain = _type_chain(exc)
    base_msg = _message_of(exc)

    def build(kind: str, source: str, matched: str, reason: str, message: str) -> Classified:
        pol = POLICY[kind]
        return Classified(
            kind=kind,
            reason=reason,
            matched=matched,
            exc_type=type(exc).__name__,
            message=base_msg,
            retryable=bool(pol["retryable"]),
            max_attempts=int(pol["max_attempts"]),
            backoff=backoff_plan(kind),
            chain=types_chain,
        )

    sq = _sqlite_hit(chain)
    if sq is not None:
        source, matched, reason, message = sq
        low = message.lower()
        if matched.endswith("IntegrityError") or "integrityerror" in matched.lower():
            return build(BUG, source, matched, reason, message)
        if any(kw in low for kw in FATAL_KEYWORDS):
            return build(FATAL, source, matched, reason, message)
        if any(kw in low for kw in TRANSIENT_KEYWORDS):
            return build(TRANSIENT, source, matched, reason, message)
        return build(BUG, source, matched, reason, message)

    hit = _hit(chain, FATAL_EXC_TYPES, FATAL_KEYWORDS)
    if hit:
        _, matched, reason, message = hit
        return build(FATAL, "fatal", matched, reason, message)

    hit = _hit(chain, TRANSIENT_EXC_TYPES, TRANSIENT_KEYWORDS)
    if hit:
        _, matched, reason, message = hit
        return build(TRANSIENT, "transient", matched, reason, message)

    hit = _hit(chain, BUG_EXC_TYPES, BUG_KEYWORDS)
    if hit:
        _, matched, reason, message = hit
        return build(BUG, "bug", matched, reason, message)

    return build(BUG, "default", "", "無命中規則，保守視為 bug（進 autofix）", base_msg)


def classify_text(text: str) -> Classified:
    """只有字串（例如日誌一行）時的入口。"""

    return classify(Exception(text or ""))


def should_retry(exc_or_classified: Any) -> bool:
    """可否用「退避重試」解決。"""

    if isinstance(exc_or_classified, Classified):
        return exc_or_classified.retryable
    return classify(exc_or_classified).retryable


def backoff_delay(kind: str, attempt: int, jitter: bool = True) -> float:
    """
    第 attempt 次重試前應等待的秒數（attempt 從 1 起算）。

    transient: 0.5, 1, 2, 4, 8（上限 cap=8）
    bug/fatal: 0.0（不重試）
    """

    pol = POLICY.get(kind) or POLICY[BUG]
    if not pol["retryable"]:
        return 0.0
    attempt = max(1, int(attempt))
    delay = pol["base"] * (pol["factor"] ** (attempt - 1))
    delay = min(delay, pol["cap"])
    if jitter and pol["jitter"]:
        delay = delay * (1.0 + random.uniform(-pol["jitter"], pol["jitter"]))
    return round(max(0.0, delay), 3)


def backoff_plan(kind: str, jitter: bool = False) -> List[float]:
    """完整退避序列（給 L2 直接照表睡）。"""

    pol = POLICY.get(kind) or POLICY[BUG]
    if not pol["retryable"]:
        return []
    n = int(pol["max_attempts"])
    return [backoff_delay(kind, i, jitter=jitter) for i in range(1, n + 1)]


def describe(exc: BaseException) -> str:
    """一行摘要，可貼進事件檔或通知訊息。"""

    c = classify(exc)
    return f"{c.kind} / {c.exc_type} / {c.reason} / {c.message[:160]}"


# --------------------------------------------------------------------------
# 自測
# --------------------------------------------------------------------------
def _selftest() -> int:
    import asyncio as _asyncio

    now = 0.0

    def nested() -> BaseException:
        try:
            try:
                raise sqlite3.OperationalError("database is locked")
            except sqlite3.OperationalError as inner:
                raise RuntimeError("add_to_history 失敗") from inner
        except RuntimeError as outer:
            return outer

    cases: List[Tuple[str, BaseException, str]] = [
        ("sqlite 寫鎖", sqlite3.OperationalError("database is locked"), TRANSIENT),
        ("sqlite table 鎖", sqlite3.OperationalError("database table is locked"), TRANSIENT),
        ("httpx 逾時字串", Exception("Read timed out."), TRANSIENT),
        ("asyncio 逾時", _asyncio.TimeoutError(), TRANSIENT),
        ("連線重置", ConnectionResetError("Connection reset by peer"), TRANSIENT),
        ("HTTP 503", Exception("Server error '503 Service Unavailable'"), TRANSIENT),
        ("HTTP 429", Exception("Too Many Requests 429 rate limit"), TRANSIENT),
        ("TG 衝突", Exception("Conflict: terminated by other getUpdates request"), TRANSIENT),
        ("例外鏈包裝", nested(), TRANSIENT),
        ("NoneType 屬性", AttributeError("'NoneType' object has no attribute 'strip'"), BUG),
        ("KeyError", KeyError("agent_config"), BUG),
        ("TypeError", TypeError("bad() got an unexpected keyword argument 'x'"), BUG),
        ("JSON 解析", Exception("jsondecodeerror: Expecting value: line 1 column 1"), BUG),
        ("UNIQUE 約束", sqlite3.IntegrityError("UNIQUE constraint failed: mok_memory.id"), BUG),
        ("LLM 連線失敗", Exception("FailoverError: LLM request failed: network connection error."), TRANSIENT),
        ("連線錯誤", Exception("rawError=Connection error."), TRANSIENT),
        ("設定檔缺失", RuntimeError("配置文件 /home/ubuntu/.mok/.tg.py 不存在"), FATAL),
        ("缺少 TOKEN", RuntimeError("配置檔案中缺少 TG_TOKEN"), FATAL),
        ("SyntaxError", SyntaxError("invalid syntax"), BUG),
        ("未知錯誤", Exception("something very strange happened"), BUG),
        ("OOM", MemoryError("out of memory"), FATAL),
        ("磁碟爆", Exception("OSError: [Errno 28] No space left on device"), FATAL),
        ("DB 檔壞", sqlite3.DatabaseError("database disk image is malformed"), FATAL),
        ("權限", PermissionError("[Errno 13] Permission denied: '/home/ubuntu/.mok/x.db'"), FATAL),
        ("KeyboardInterrupt", KeyboardInterrupt(), FATAL),
        ("KB 純文字", Exception("KeyboardInterrupt:"), FATAL),
        ("OOM 純文字", Exception("MemoryError: out of memory"), FATAL),
    ]

    print("=== L1 錯誤分類層 自測 ===")
    fails = 0
    for name, exc, expect in cases:
        got = classify(exc)
        ok = got.kind == expect
        fails += 0 if ok else 1
        print(f"{'PASS' if ok else 'FAIL'}  {name:<14} 期望={expect:<9} 實得={got.kind:<9} [{got.matched or got.reason}]")

    print("\n=== 退避序列（transient）===")
    plan = backoff_plan(TRANSIENT)
    print(f"max_attempts={POLICY[TRANSIENT]['max_attempts']}  backoff={plan}")
    if plan != [0.5, 1.0, 2.0, 4.0, 8.0]:
        print("FAIL  退避序列不是 0.5,1,2,4,8")
        fails += 1
    if backoff_plan(BUG) or backoff_plan(FATAL):
        print("FAIL  bug / fatal 不該有退避序列")
        fails += 1

    print("\n=== 真實情境字串（來自本次事故）===")
    real = "處理消息時發生嚴重錯誤，自動修復未能解決：自動修復循環失敗（1次嘗試），最後錯誤: OperationalError: database is locked"
    c = classify_text(real)
    print(f"kind={c.kind} retryable={c.retryable} matched={c.matched}")
    if c.kind != TRANSIENT:
        print("FAIL  事故字串應判為 transient")
        fails += 1

    print(f"\n結果：{'全部通過 ✅' if fails == 0 else f'{fails} 個失敗 ❌'}")
    return 0 if fails == 0 else 1


if __name__ == "__main__":
    raise SystemExit(_selftest())
