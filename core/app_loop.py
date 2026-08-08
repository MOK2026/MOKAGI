# -*- coding: utf-8 -*-
"""
app_loop.py — 全局持久 Event Loop（方案 B 根治核心）

設計目標：全進程只存在「一個」持久 APP_LOOP（在專屬 daemon 執行緒上 run_forever，
永不 close）。所有 async 資源（AsyncOpenAI client 等）只綁定到它，
徹底消除 new_event_loop() / asyncio.run() / loop.close() 的臨時模式，
從根上杜絕 "Event loop is closed"。

用法：
    from app_loop import init_app_loop, run_async, run_async_await

    # 同步上下文（其他執行緒）需要跑 async 時：
    result = run_async(some_coroutine())

    # async 上下文內需要確保跑到全局 loop 時：
    result = await run_async_await(some_coroutine())

    # 進程啟動時可主動初始化（亦可惰性初始化）：
    init_app_loop()
"""
import asyncio
import threading

APP_LOOP = None
_APP_LOOP_THREAD = None
_lock = threading.Lock()


def init_app_loop():
    """建立並啟動全局持久 event loop（執行緒安全，重複呼叫無害）"""
    global APP_LOOP, _APP_LOOP_THREAD
    with _lock:
        if APP_LOOP is not None and not APP_LOOP.is_closed():
            return APP_LOOP
        loop = asyncio.new_event_loop()
        t = threading.Thread(
            target=_run_loop_forever,
            args=(loop,),
            name="MOK-APP-LOOP",
            daemon=True,
        )
        t.start()
        APP_LOOP = loop
        _APP_LOOP_THREAD = t
        return APP_LOOP


def _run_loop_forever(loop):
    asyncio.set_event_loop(loop)
    loop.run_forever()


def get_app_loop():
    """取得全局 APP_LOOP（惰性初始化）"""
    if APP_LOOP is None or APP_LOOP.is_closed():
        init_app_loop()
    return APP_LOOP


def is_loop_thread():
    """目前執行緒是否就是 APP_LOOP 執行緒"""
    return _APP_LOOP_THREAD is not None and threading.current_thread() is _APP_LOOP_THREAD


def run_async(coro, timeout=None):
    """
    統一入口（同步阻塞）：在全局 APP_LOOP 上執行 coroutine 並等待結果。
    - 若在 APP_LOOP 執行緒內被呼叫 → 拋出 RuntimeError（避免 deadlock），
      此上下文請直接 await coroutine，或改用 run_async_await()。
    - 其他執行緒 → asyncio.run_coroutine_threadsafe(coro, APP_LOOP).result(timeout)
    """
    loop = get_app_loop()
    if is_loop_thread():
        raise RuntimeError(
            "run_async() 被呼叫於 APP_LOOP 執行緒內（會 deadlock）。"
            "請直接 await coroutine，或改用 app_loop.run_async_await(coro)。"
        )
    future = asyncio.run_coroutine_threadsafe(coro, loop)
    return future.result(timeout=timeout)


async def run_async_await(coro):
    """
    統一入口（async 版本）：確保 coroutine 在全局 APP_LOOP 上執行。
    - 已在 APP_LOOP 執行緒內 → 直接 await（零切換開銷）
    - 其他執行緒 → 切換到 APP_LOOP 執行並等待結果
    """
    if is_loop_thread():
        return await coro
    loop = get_app_loop()
    return await asyncio.wrap_future(asyncio.run_coroutine_threadsafe(coro, loop))
