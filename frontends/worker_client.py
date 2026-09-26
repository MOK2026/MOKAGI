#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
worker_client -- mok_web side IPC client to mok_worker (Step 2)
===============================================================
So mok_web only has to FORWARD SSE, while the agent loop actually runs
inside the long-lived mok_worker process. Restarting mok_web then never
aborts a running task; the frontend can re-subscribe and replay.

Transport: linux abstract unix socket, JSON lines.
"""
import socket
import json

SOCK_NAME = "\0mok_worker_agent"


def _connect(timeout=10):
    s = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    s.settimeout(timeout)
    s.connect(SOCK_NAME)
    return s


def _send_request(req, timeout=10):
    """One-shot request: send one line, read the first reply line."""
    try:
        s = _connect(timeout)
    except Exception:
        return {"ok": False, "error": "worker_unavailable"}
    try:
        s.sendall((json.dumps(req, ensure_ascii=False) + "\n").encode("utf-8"))
        buf = b""
        while b"\n" not in buf:
            chunk = s.recv(65536)
            if not chunk:
                break
            buf += chunk
        if not buf:
            return {"ok": False, "error": "empty_response"}
        try:
            return json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
        except Exception:
            return {"ok": False, "error": "bad_json"}
    finally:
        try:
            s.close()
        except Exception:
            pass


def ping():
    return _send_request({"cmd": "ping"})


def is_up():
    try:
        return bool(ping().get("pong"))
    except Exception:
        return False


def tasks(agent=None):
    """查詢 worker 目前掌有的任務（可依 agent_name 過濾）。

    回傳 {"ok": True, "tasks": [{"session_id","agent_name","done","events","t0"}, ...]}
    供 mok_web 在重啟後 re-attach 未知 session 用。"""
    return _send_request({"cmd": "tasks", "agent": agent or ""})


def submit(session_id, user_id, text, agent_name, context_files=None):
    return _send_request({
        "cmd": "submit",
        "session_id": session_id,
        "user_id": user_id,
        "text": text,
        "agent_name": agent_name,
        "context_files": context_files,
    })


def subscribe(session_id, after=0):
    """Generator yielding event dicts; blocks until the worker closes the stream."""
    s = _connect(timeout=None)
    s.settimeout(None)
    s.sendall((json.dumps(
        {"cmd": "subscribe", "session_id": session_id, "after": after}
    ) + "\n").encode("utf-8"))
    f = s.makefile("rb")
    try:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line.decode("utf-8"))
            except Exception:
                continue
            ev = msg.get("event")
            if ev is not None:
                yield ev
            if ev and ev.get("type") in ("done", "error"):
                return
    finally:
        try:
            s.close()
        except Exception:
            pass

