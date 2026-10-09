#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""heavy_service.py — MOK 重工常駐服務（P1-6 2026-10-04 by 稚）

目的：把「重工指令」（遞迴搜尋、讀巨型日誌、影音轉檔、目錄佔用統計…）從
     mok_web 的請求執行緒搬到**獨立常駐進程**執行，讓 web 只做 I/O 等待，
     不再因 GIL 互卡。

介面（僅綁 127.0.0.1，只信任本機）：
    GET  /health  -> {"ok":true,"pid":..,"uptime":..,"running":..}
    POST /run     {"cmd":str,"timeout":int,"cwd":str}
                  -> {"rc":int,"stdout":str,"stderr":str,"timeout":bool,"elapsed":float}

併發：以 MOK_HEAVY_CONCURRENCY（預設 2）限制同時在跑的重工數；超過排隊。

啟停：一般由 core/heavy_client.py 需要時自動拉起；
     亦可手動執行本檔加 --port 5011。
"""
import argparse
import json
import os
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

_START = time.time()
_SEM = threading.BoundedSemaphore(int(os.environ.get("MOK_HEAVY_CONCURRENCY", "2") or 2))
_RUNNING = {"n": 0}
_LK = threading.Lock()
_MAX_TIMEOUT = int(os.environ.get("MOK_HEAVY_MAX_TIMEOUT", "600") or 600)


def _run(cmd, timeout, cwd):
    t0 = time.time()
    with _LK:
        _RUNNING["n"] += 1
    try:
        _SEM.acquire()
        try:
            r = subprocess.run(
                ["bash", "-l", "-c", cmd],
                capture_output=True, text=True, timeout=timeout, cwd=cwd, encoding="utf-8", errors="replace",
                env=os.environ.copy(),
            )
            return {
                "rc": r.returncode,
                "stdout": r.stdout or "",
                "stderr": r.stderr or "",
                "timeout": False,
                "elapsed": round(time.time() - t0, 3),
            }
        finally:
            _SEM.release()
    except subprocess.TimeoutExpired as e:
        out = e.stdout or ""
        err = e.stderr or ""
        if isinstance(out, bytes):
            out = out.decode("utf-8", "replace")
        if isinstance(err, bytes):
            err = err.decode("utf-8", "replace")
        return {"rc": 124, "stdout": out, "stderr": err, "timeout": True,
                "elapsed": round(time.time() - t0, 3)}
    except Exception as e:
        return {"rc": -1, "stdout": "", "stderr": "heavy_service error: %s" % e,
                "timeout": False, "elapsed": round(time.time() - t0, 3)}
    finally:
        with _LK:
            _RUNNING["n"] -= 1


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    server_version = "mok-heavy/1.0"

    def log_message(self, *a):
        return

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        try:
            self.wfile.write(body)
        except Exception:
            pass

    def do_GET(self):
        if self.path.split("?")[0] == "/health":
            with _LK:
                n = _RUNNING["n"]
            return self._json({"ok": True, "pid": os.getpid(),
                               "uptime": round(time.time() - _START, 1), "running": n})
        return self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        if self.path.split("?")[0] != "/run":
            return self._json({"ok": False, "error": "not found"}, 404)
        try:
            ln = int(self.headers.get("Content-Length", "0") or 0)
            raw = self.rfile.read(ln) if ln > 0 else b"{}"
            data = json.loads(raw.decode("utf-8") or "{}")
        except Exception as e:
            return self._json({"ok": False, "error": "bad request: %s" % e}, 400)

        cmd = (data.get("cmd") or "").strip()
        if not cmd:
            return self._json({"ok": False, "error": "empty cmd"}, 400)
        try:
            timeout = int(data.get("timeout") or 300)
        except Exception:
            timeout = 300
        timeout = max(1, min(timeout, _MAX_TIMEOUT))
        cwd = data.get("cwd") or None
        if cwd and not os.path.isdir(cwd):
            cwd = None
        res = _run(cmd, timeout, cwd)
        res["ok"] = True
        return self._json(res)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("MOK_HEAVY_HOST", "127.0.0.1"))
    ap.add_argument("--port", type=int, default=int(os.environ.get("MOK_HEAVY_PORT", "5011") or 5011))
    args = ap.parse_args()
    srv = ThreadingHTTPServer((args.host, args.port), _Handler)
    srv.daemon_threads = True
    print("[heavy_service] listening on %s:%d pid=%d" % (args.host, args.port, os.getpid()), flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()


if __name__ == "__main__":
    main()
