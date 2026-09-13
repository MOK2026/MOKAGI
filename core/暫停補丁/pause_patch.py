# -*- coding: utf-8 -*-
"""
暫停補丁 (pause_patch)
------------------------------------------------------------
讓 MOKAGI 網頁介面能像 ChatGPT 一樣「暫停 / 繼續」生成，
且【不直接修改 mokagi.py】。

原理
----
mok_web.py 的 SSE 背景工作執行緒，會在每個串流事件呼叫
async_stream_cb(event)。本模組提供 wait_if_paused(agent)，
只要在該回調最前面 await 它；若該 agent 處於暫停狀態，回調就會阻塞，
上游（mokagi 的 LLM 串流迴圈）因 backpressure 一併停住，
直到使用者按下「繼續」為止 —— 不會重新發送、不重複計費。

Flask 路由
----------
  POST /api/chat/pause          body: {"agent": "...", "paused": true/false}
  GET  /api/chat/pause/status?agent=...

安全
----
  - 最長暫停 _MAX_PAUSE_SEC（預設 300 秒）後自動解除，避免請求永久卡死。
  - 全程 try/except 保護，載入或呼叫失敗都不影響原本功能。
"""
import time
import threading

_LOCK = threading.Lock()
_PAUSED = {}          # {agent: bool}
_PAUSED_AT = {}       # {agent: float}
_MAX_PAUSE_SEC = 300


def _agent(a):
    return a or ""


def is_paused(agent):
    a = _agent(agent)
    with _LOCK:
        if not _PAUSED.get(a):
            return False
        if time.time() - _PAUSED_AT.get(a, 0.0) > _MAX_PAUSE_SEC:
            _PAUSED[a] = False
            _PAUSED_AT.pop(a, None)
            return False
        return True


def set_paused(agent, paused):
    a = _agent(agent)
    with _LOCK:
        _PAUSED[a] = bool(paused)
        if paused:
            _PAUSED_AT[a] = time.time()
        else:
            _PAUSED_AT.pop(a, None)
    return bool(paused)


def status(agent=None):
    with _LOCK:
        if agent is None:
            return {k: bool(v) for k, v in _PAUSED.items()}
        return bool(_PAUSED.get(_agent(agent), False))


async def wait_if_paused(agent, poll=0.15):
    """在 async 串流回調中呼叫；暫停期間阻塞，直到解除或超過上限。"""
    import asyncio
    while is_paused(agent):
        await asyncio.sleep(poll)
    return True


def register_routes(app):
    """把暫停/繼續控制端點註冊到 Flask app。"""
    from flask import request, jsonify

    @app.route("/api/chat/pause", methods=["POST"])
    def _mok_pause_ctl():
        data = request.get_json(force=True, silent=True) or {}
        agent = data.get("agent") or ""
        paused = bool(data.get("paused"))
        set_paused(agent, paused)
        print("[pause_patch] agent=%s paused=%s" % (agent, paused))
        return jsonify({"ok": True, "agent": agent, "paused": paused})

    @app.route("/api/chat/pause/status", methods=["GET"])
    def _mok_pause_status():
        agent = request.args.get("agent")
        return jsonify({"ok": True, "paused": status(agent) if agent else status()})

    print("[pause_patch] routes registered: /api/chat/pause")
    return app
