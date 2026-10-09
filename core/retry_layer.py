#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
retry_layer.py — L2 重試層
「四層自我修正架構」第 2 層（設計：jobs/2026-09-29/自我修正機制_設計.md）

職責（唯一）：在「執行失敗」與「叫 LLM 改程式碼（autofix）」之間插入一層
              由錯誤分類決定的退避重試。讓 transient 錯誤有時間自己好，
              而不是立刻被當成 bug 去改 code。

策略（分類由 L1 error_classify 決定）：
    transient  指數退避 + 抖動 0.5 / 1 / 2 / 4 / 8 秒（最多 5 次重試），
               全失敗才交由呼叫方進 autofix
    bug        不重試，直接交由呼叫方進 autofix
    fatal      不重試、不 autofix；寫事件檔後原樣拋出

設計原則：
    1. 不碰 DB、不呼叫 LLM → 永遠不會被它要處理的那把鎖卡住。
    2. 只用標準庫。
    3. 不改既有檔案也能用：呼叫方只 import 本檔，拿 run_with_retry 包住原呼叫。
    4. 事件落地成純檔案（~/.mok/logs/selfheal/events.jsonl），不依賴 DB。

對外 API：
    run_with_retry(coro_factory, ...) -> Any
        成功回傳結果；應交 autofix 時 raise AutofixNeeded；fatal 時 raise FatalStop。
    kind_of(exc) / classify(exc) / total_attempts(kind) / wait_plan(kind)
    write_event(**fields) -> dict           寫一筆 selfheal 事件（不會拋錯）
    summarize(exc_or_cls) -> str            一行摘要，可寫事件檔／通知主人
"""

import asyncio
import json
import os
import time
from typing import Any, Awaitable, Callable, List, Optional

try:  # core/ 在 sys.path（主程式內部呼叫）
    from error_classify import (
        POLICY, TRANSIENT, BUG, FATAL,
        backoff_delay, backoff_plan, classify, describe, should_retry,
    )
except ImportError:  # 套件式匯入（from core.retry_layer import ...）
    from core.error_classify import (
        POLICY, TRANSIENT, BUG, FATAL,
        backoff_delay, backoff_plan, classify, describe, should_retry,
    )

__all__ = [
    "TRANSIENT", "BUG", "FATAL",
    "AutofixNeeded", "FatalStop", "run_with_retry",
    "kind_of", "classify", "should_retry",
    "total_attempts", "wait_plan", "write_event", "summarize", "EVENTS_PATH",
]

SELFHEAL_DIR = os.path.expanduser("~/.mok/logs/selfheal")
EVENTS_PATH = os.path.join(SELFHEAL_DIR, "events.jsonl")


# --------------------------------------------------------------------------
# 策略查詢
# --------------------------------------------------------------------------
def total_attempts(kind: str = TRANSIENT) -> int:
    """總嘗試次數 = 首次 + 退避重試次數（transient = 1 + 5 = 6 次）。"""
    if not POLICY.get(kind, {}).get("retryable"):
        return 1
    return 1 + len(backoff_plan(kind))


def wait_plan(kind: str = TRANSIENT) -> List[float]:
    """完整退避序列（不加抖動）。"""
    return list(backoff_plan(kind)) if POLICY.get(kind, {}).get("retryable") else []


def kind_of(exc: BaseException) -> str:
    try:
        return classify(exc).kind
    except Exception:
        return BUG


def summarize(exc_or_cls: Any) -> str:
    """一行摘要，可直接寫事件檔或通知主人。"""
    try:
        cls = exc_or_cls if hasattr(exc_or_cls, "kind") else classify(exc_or_cls)
        return (
            f"[{cls.kind}] {cls.reason} | 命中: {cls.matched or '-'} | "
            f"{cls.exc_type}: {str(cls.message)[:160]}"
        )
    except Exception as e:  # 保底，永不拋錯
        return f"[unknown] {type(e).__name__}: {e}"


# --------------------------------------------------------------------------
# 事件落地（純檔案，不碰 DB）
# --------------------------------------------------------------------------
def write_event(kind: str = "", event: str = "", label: str = "",
                attempt: int = 0, wait: Optional[float] = None,
                error: Optional[BaseException] = None,
                extra: Optional[dict] = None) -> dict:
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "layer": "L2",
        "kind": kind,
        "event": event,
        "label": label,
        "attempt": attempt,
    }
    if wait is not None:
        try:
            rec["wait"] = round(float(wait), 3)
        except Exception:
            rec["wait"] = wait
    if error is not None:
        rec["error_type"] = type(error).__name__
        rec["error"] = str(error)[:300]
    if extra:
        try:
            rec.update(extra)
        except Exception:
            pass
    try:
        os.makedirs(SELFHEAL_DIR, exist_ok=True)
        with open(EVENTS_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass  # 事件檔寫不進去也不能影響主流程
    return rec


async def _emit(on_event, rec: dict) -> None:
    if not on_event:
        return
    try:
        r = on_event(rec)
        if isinstance(r, Awaitable) or asyncio.iscoroutine(r):
            await r
    except Exception:
        pass


# --------------------------------------------------------------------------
# 兩個「路由」例外
# --------------------------------------------------------------------------
class AutofixNeeded(Exception):
    """L2 判定：應交 autofix 處理（transient 重試用盡，或 bug）。"""

    def __init__(self, exc: BaseException, cls=None, attempts: int = 0, waits: Optional[list] = None):
        super().__init__(f"autofix needed: {type(exc).__name__}: {exc}")
        self.exc = exc
        self.cls = cls if cls is not None else classify(exc)
        self.kind = getattr(self.cls, "kind", BUG)
        self.attempts = attempts
        self.waits = waits or []


class FatalStop(Exception):
    """L2 判定：fatal，不重試、不 autofix。"""

    def __init__(self, exc: BaseException, cls=None, attempts: int = 0, waits: Optional[list] = None):
        super().__init__(f"fatal: {type(exc).__name__}: {exc}")
        self.exc = exc
        self.cls = cls if cls is not None else classify(exc)
        self.kind = FATAL
        self.attempts = attempts
        self.waits = waits or []


# --------------------------------------------------------------------------
# 主體
# --------------------------------------------------------------------------
async def run_with_retry(
    coro_factory: Callable[[], Awaitable[Any]],
    label: str = "",
    on_event: Optional[Callable[[dict], Any]] = None,
    sleeper: Optional[Callable[[float], Awaitable[Any]]] = None,
    max_attempts: Optional[int] = None,
) -> Any:
    """依錯誤分類決定重試策略地執行 coro_factory()。

    coro_factory 必須是「零參數、回傳 awaitable」的可呼叫物件（每次重試重新呼叫），
    因為同一個 coroutine 物件不能被 await 兩次。

    回傳：成功結果。
    拋出：AutofixNeeded（transient 用盡 / bug）、FatalStop（fatal），
          屬性 .exc / .cls / .attempts / .waits 供呼叫方決定下一步。
    """
    if sleeper is None:
        sleeper = asyncio.sleep

    attempt = 0
    waits: List[float] = []

    while True:
        attempt += 1
        try:
            return await coro_factory()
        except Exception as e:
            cls = classify(e)
            kind = cls.kind
            budget = int(max_attempts) if max_attempts else total_attempts(kind)

            if should_retry(cls) and attempt < budget:
                w = backoff_delay(kind, attempt)
                waits.append(round(float(w), 3))
                rec = write_event(kind=kind, event="retry", label=label,
                                  attempt=attempt, wait=w, error=e,
                                  extra={"reason": cls.reason, "matched": cls.matched})
                await _emit(on_event, rec)
                await sleeper(w)
                continue

            if kind == TRANSIENT:
                rec = write_event(kind=kind, event="retry_exhausted", label=label,
                                  attempt=attempt, error=e,
                                  extra={"waits": waits, "reason": cls.reason,
                                         "matched": cls.matched})
                await _emit(on_event, rec)
                raise AutofixNeeded(e, cls, attempt, waits)

            if kind == BUG:
                rec = write_event(kind=kind, event="bug", label=label,
                                  attempt=attempt, error=e,
                                  extra={"reason": cls.reason, "matched": cls.matched})
                await _emit(on_event, rec)
                raise AutofixNeeded(e, cls, attempt, waits)

            rec = write_event(kind=kind, event="fatal", label=label,
                              attempt=attempt, error=e,
                              extra={"reason": cls.reason, "matched": cls.matched})
            await _emit(on_event, rec)
            raise FatalStop(e, cls, attempt, waits)


# --------------------------------------------------------------------------
# 自測
# --------------------------------------------------------------------------
def _selftest() -> int:
    import sqlite3

    ok = True

    def chk(name, cond, extra=""):
        nonlocal ok
        print(("  [OK]   " if cond else "  [FAIL] ") + name + (f"  {extra}" if extra else ""))
        ok = ok and bool(cond)

    print("=== L2 retry_layer 自測 ===")
    chk("transient 總嘗試次數 = 1+5 = 6", total_attempts(TRANSIENT) == 6, f"got {total_attempts(TRANSIENT)}")
    chk("bug 總嘗試次數 = 1", total_attempts(BUG) == 1)
    chk("fatal 總嘗試次數 = 1", total_attempts(FATAL) == 1)
    chk("退避序列 = [0.5,1,2,4,8]", wait_plan(TRANSIENT) == [0.5, 1.0, 2.0, 4.0, 8.0], str(wait_plan(TRANSIENT)))

    calls = {"n": 0}

    async def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    async def main():
        slept = []

        async def fake_sleep(s):
            slept.append(round(float(s), 2))

        r = await run_with_retry(flaky, label="selftest", sleeper=fake_sleep)
        chk("transient 第3次成功", r == "ok" and calls["n"] == 3, f"r={r} n={calls['n']}")
        chk("退避 2 次 (0.5/1，含 ±25% 抖動)",
            len(slept) == 2 and 0.375 <= slept[0] <= 0.625 and 0.75 <= slept[1] <= 1.25,
            str(slept))

        async def always_locked():
            raise sqlite3.OperationalError("database is locked")

        slept2 = []

        async def fake_sleep2(s):
            slept2.append(round(float(s), 2))

        try:
            await run_with_retry(always_locked, label="selftest", sleeper=fake_sleep2)
            chk("transient 用盡應 raise AutofixNeeded", False)
        except AutofixNeeded as an:
            chk("transient 用盡 -> AutofixNeeded", an.kind == TRANSIENT and an.attempts == 6,
                f"kind={an.kind} attempts={an.attempts}")
            _exp = [0.5, 1.0, 2.0, 4.0, 8.0]
            chk("退避 5 次 = 0.5/1/2/4/8（含 ±25% 抖動）",
                len(slept2) == 5 and all(0.75 * b <= x <= 1.25 * b for x, b in zip(slept2, _exp)),
                str(slept2))

        n3 = {"n": 0}

        async def boom():
            n3["n"] += 1
            raise KeyError("no such key: user_name")

        try:
            await run_with_retry(boom, label="selftest", sleeper=fake_sleep2)
            chk("bug 應 raise AutofixNeeded", False)
        except AutofixNeeded as an:
            chk("bug -> 立即 AutofixNeeded（不重試）", an.kind == BUG and n3["n"] == 1, f"n={n3['n']}")

        n4 = {"n": 0}

        async def oom():
            n4["n"] += 1
            raise MemoryError("out of memory")

        try:
            await run_with_retry(oom, label="selftest", sleeper=fake_sleep2)
            chk("fatal 應 raise FatalStop", False)
        except FatalStop as fs:
            chk("fatal -> FatalStop（不重試）", fs.kind == FATAL and n4["n"] == 1, f"n={n4['n']}")

    asyncio.run(main())
    chk("事件檔存在", os.path.exists(EVENTS_PATH), EVENTS_PATH)
    print("=== 自測結果:", "全部通過 ===" if ok else "有失敗 ===")
    return 0 if ok else 1


if __name__ == "__main__":
    import sys

    sys.exit(_selftest())
