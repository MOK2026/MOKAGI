# -*- coding: utf-8 -*-
"""併發閘補丁 (2026-09-29 架)
限制「同時執行的對話數」，預設 5，避免多開侍女把 4 核 CPU 榨乾。
停用：把本目錄改名（前面加 _）後重啟 mok_web。env: MOK_MAX_CONCURRENT
"""
import asyncio
import functools
import os
import sys
import threading
import time

MAX = max(1, int(os.environ.get("MOK_MAX_CONCURRENT", "5") or 5))
_SLOT = threading.BoundedSemaphore(MAX)
_LK = threading.Lock()
_ST = {"started": 0, "waited": 0, "wait_total": 0.0, "max_wait": 0.0}


def _wrap(mod):
    orig = getattr(mod, "process_message", None)
    if orig is None or getattr(orig, "_mok_conc_wrapped", False):
        return False

    @functools.wraps(orig)
    async def wrapped(*a, **kw):
        t0 = time.time()
        await asyncio.to_thread(_SLOT.acquire)
        w = time.time() - t0
        with _LK:
            _ST["started"] += 1
            if w > 0.01:
                _ST["waited"] += 1
                _ST["wait_total"] += w
                _ST["max_wait"] = max(_ST["max_wait"], w)
        if w >= 0.5:
            print("[併發閘] 排隊 %.1fs 才開始（上限 %d）" % (w, MAX), flush=True)
        try:
            return await orig(*a, **kw)
        finally:
            _SLOT.release()

    wrapped._mok_conc_wrapped = True
    setattr(mod, "process_message", wrapped)
    return True


_ok = []
try:
    import mokagi
    if _wrap(mokagi):
        _ok.append("mokagi")
except Exception as e:
    print("[併發閘] import mokagi 失敗: %s" % e, flush=True)
_main = sys.modules.get("__main__")
if _main is not None and hasattr(_main, "process_message") and _wrap(_main):
    _ok.append("__main__")
print("[併發閘] installed max=%d in %s" % (MAX, _ok), flush=True)
