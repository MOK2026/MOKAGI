#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mok_worker -- independent agent worker process (Step 1)
=======================================================
Decouple "restart web" from "running tasks": the agent loop
(mokagi.process_message) runs inside THIS process, not inside mok_web,
so restarting mok_web never aborts a running task.
Task events (buffer) are owned by this process, so the frontend can
re-subscribe after a web restart and replay what it missed.

IPC: linux abstract unix socket, JSON lines.

  req  {"cmd":"ping"}
  req  {"cmd":"submit","session_id","user_id","text","agent_name","context_files"}
  req  {"cmd":"subscribe","session_id","after":N}
  push {"session_id":..., "event":{...}}     after subscribe
"""
import os
import sys
import json
import socket
import threading
import asyncio
import time
import traceback

HOME = os.path.expanduser("~")
sys.path.insert(0, os.path.join(HOME, ".mok", "core"))
sys.path.insert(0, os.path.join(HOME, ".mok", "frontends"))

SOCK_NAME = "\0mok_worker_agent"
TASK_TTL = 1800

from mokagi import process_message  # noqa: E402

_tasks = {}
_lock = threading.Lock()


def _sock_send(conn, obj):
    try:
        conn.sendall((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    except Exception:
        pass


def _append_event(session_id, event):
    with _lock:
        t = _tasks.get(session_id)
        if t is None:
            return
        event.setdefault("ts", time.time())
        t["events"].append(event)
        subs = list(t["subs"])
    for conn in subs:
        _sock_send(conn, {"session_id": session_id, "event": event})


def _run_task(session_id, user_id, text, agent_name, context_files):
    print("[worker] task start session=%s agent=%s" % (session_id, agent_name), flush=True)
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def async_stream_cb(event):
        _append_event(session_id, dict(event))

    try:
        loop.run_until_complete(process_message(
            user_id=user_id,
            text=text,
            stream_callback=async_stream_cb,
            agent_name=agent_name,
            context_files=context_files,
        ))
    except Exception as e:
        traceback.print_exc()
        _append_event(session_id, {"type": "error", "content": "worker error: %s" % e})
    finally:
        try:
            loop.close()
        except Exception:
            pass
        with _lock:
            t = _tasks.get(session_id)
            if t is not None:
                t["done"] = True
                _already_done = bool(t["events"]) and t["events"][-1].get("type") == "done"
            else:
                _already_done = False
        # process_message 多數路徑已自行發出 done；避免重複追加造成前端/DB 二次處理
        if not _already_done:
            _append_event(session_id, {"type": "done"})
        print("[worker] task done session=%s" % session_id, flush=True)


def _handle_subscribe(conn, session_id, after):
    with _lock:
        t = _tasks.get(session_id)
        if t is None:
            _sock_send(conn, {"session_id": session_id, "error": "no such session"})
            return
        t["subs"].append(conn)
        backlog = list(t["events"])[after:]
        done = t["done"]
    _sock_send(conn, {"session_id": session_id, "subscribed": True, "count": len(backlog)})
    for ev in backlog:
        _sock_send(conn, {"session_id": session_id, "event": ev})
    if done:
        return
    while True:
        try:
            data = conn.recv(1)
            if not data:
                break
        except Exception:
            break
    with _lock:
        t = _tasks.get(session_id)
        if t is not None and conn in t["subs"]:
            t["subs"].remove(conn)


def _handle_conn(conn):
    try:
        buf = b""
        while b"\n" not in buf:
            chunk = conn.recv(65536)
            if not chunk:
                return
            buf += chunk
        req = json.loads(buf.split(b"\n", 1)[0].decode("utf-8"))
        cmd = req.get("cmd")
        if cmd == "ping":
            _sock_send(conn, {"pong": True, "tasks": list(_tasks.keys())})
        elif cmd == "tasks":
            # 🔧 re-attach 用：回報 worker 掌有的任務快照（含 agent_name / done / 事件數）
            _agent = (req.get("agent") or "").strip()
            with _lock:
                _snap = [
                    {
                        "session_id": _sid,
                        "agent_name": _t.get("agent_name", ""),
                        "done": bool(_t.get("done")),
                        "events": len(_t.get("events", [])),
                        "t0": _t.get("t0"),
                    }
                    for _sid, _t in _tasks.items()
                    if (not _agent) or _t.get("agent_name", "") == _agent
                ]
            _sock_send(conn, {"ok": True, "tasks": _snap})
        elif cmd == "submit":
            sid = req["session_id"]
            with _lock:
                _tasks[sid] = {"events": [], "done": False, "subs": [], "t0": time.time(),
                               "agent_name": req.get("agent_name", "")}
            _sock_send(conn, {"session_id": sid, "accepted": True})
            threading.Thread(
                target=_run_task,
                args=(sid, req.get("user_id"), req.get("text", ""),
                      req.get("agent_name", ""), req.get("context_files")),
                daemon=True,
            ).start()
        elif cmd == "subscribe":
            _handle_subscribe(conn, req["session_id"], int(req.get("after", 0)))
    except Exception:
        traceback.print_exc()
    finally:
        try:
            conn.close()
        except Exception:
            pass


def _reaper():
    while True:
        time.sleep(60)
        now = time.time()
        with _lock:
            for sid in list(_tasks.keys()):
                t = _tasks[sid]
                if t["done"] and (now - t.get("t0", now)) > TASK_TTL:
                    _tasks.pop(sid, None)


def main():
    srv = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    srv.bind(SOCK_NAME)
    srv.listen(64)
    threading.Thread(target=_reaper, daemon=True).start()
    print("[worker] listening (abstract) on mok_worker_agent", flush=True)
    while True:
        conn, _ = srv.accept()
        threading.Thread(target=_handle_conn, args=(conn,), daemon=True).start()


if __name__ == "__main__":
    main()

