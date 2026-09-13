#!/usr/bin/env python3
"""Independent MOKAGI repair console. Binds to localhost and uses only stdlib."""
import argparse
import json
import os
import shlex
import subprocess
import threading
import time
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlparse

ROOT = Path.home() / ".mok"
PAGE = ROOT / "html" / "建檔案" / "主機維修台.html"
MAX_FILE = 2 * 1024 * 1024
RECENT_CONSOLE = []
CONSOLE_LOCK = threading.Lock()
AGENT_JOBS = {}
AGENT_LOCK = threading.Lock()

COMMANDS = {
    "pm2 status": ["pm2", "status"],
    "pm2 restart mok_web": ["pm2", "restart", "mok_web", "--update-env"],
    "pm2 restart mok_agi": ["pm2", "restart", "mok_agi", "--update-env"],
    "pm2 logs mok_web --lines 80": ["pm2", "logs", "mok_web", "--lines", "80", "--nostream"],
    "ss -ltnp": ["ss", "-ltnp"],
    "ps aux": ["ps", "aux"],
    "curl health": ["curl", "-fsS", "--max-time", "5", "http://127.0.0.1:5000/api/health"],
    "python check mok_web": ["python3", "-m", "py_compile", str(ROOT / "frontends" / "mok_web.py")],
}

TOOLS = [
    {"type": "function", "function": {"name": "read_file", "description": "讀取 .mok 內的文字檔案。", "parameters": {"type": "object", "properties": {"path": {"type": "string"}}, "required": ["path"]}}},
    {"type": "function", "function": {"name": "write_file", "description": "直接修改 .mok 內已存在的文字檔案。修改前先讀取檔案並只做必要變更。", "parameters": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}, "required": ["path", "content"]}}},
    {"type": "function", "function": {"name": "run_command", "description": "執行白名單內的主機診斷或服務命令。", "parameters": {"type": "object", "properties": {"command": {"type": "string", "enum": sorted(COMMANDS)}}, "required": ["command"]}}},
]

AGENT_SYSTEM = """你是 MOKAGI 主機維修 coding agent。你不是只給建議，必須使用工具調查並直接修復。
工作規則：
1. 每個請求先查看提供的 PM2 狀態、mok_web 日誌與 health；需要時讀相關 .mok 檔案。
2. 找到根因後，直接用 write_file 修改檔案，只改必要內容；修改 Python 後執行 python check mok_web。
3. 修復後用工具再次驗證。服務重啟只在確定修改需要時執行，且只能用白名單命令。
4. 不要假設工具成功；根據工具實際輸出繼續處理。最後簡潔列出檢查、修改、驗證結果。
5. 不要刪除檔案，不要執行白名單以外的命令。"""


def safe_path(value):
    relative = str(value or "").replace("\\", "/").lstrip("/")
    target = (ROOT / relative).resolve()
    if target != ROOT and ROOT not in target.parents:
        raise ValueError("路徑必須位於 .mok 目錄內")
    return target


def json_bytes(payload):
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def remember_console(payload):
    with CONSOLE_LOCK:
        RECENT_CONSOLE.append({"time": time.time(), **payload})
        del RECENT_CONSOLE[:-200:]


def create_agent_job(api_key, question):
    job_id = f"repair-{int(time.time() * 1000)}"
    events = []
    event_queue = __import__('queue').Queue()
    with AGENT_LOCK:
        AGENT_JOBS[job_id] = {"events": events, "queue": event_queue, "done": False}

    def emit(event):
        event["time"] = time.time()
        with AGENT_LOCK:
            job = AGENT_JOBS.get(job_id)
            if job:
                job["events"].append(event)
                job["queue"].put(event)

    def worker():
        try:
            run_agent(api_key, question, emit)
        except Exception as exc:
            emit({"type": "error", "content": "維修代理例外：" + str(exc)})
        finally:
            with AGENT_LOCK:
                if job_id in AGENT_JOBS:
                    AGENT_JOBS[job_id]["done"] = True
                    AGENT_JOBS[job_id]["queue"].put({"type": "job_done", "time": time.time()})

    threading.Thread(target=worker, daemon=True).start()
    return job_id


def execute_command(name):
    argv = COMMANDS.get(name)
    if not argv:
        return {"error": "命令不在白名單內", "allowed": sorted(COMMANDS)}
    try:
        result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=30)
        payload = {"command": name, "returncode": result.returncode, "stdout": result.stdout[-20000:], "stderr": result.stderr[-20000:]}
    except subprocess.TimeoutExpired as exc:
        payload = {"command": name, "returncode": 124, "stdout": str(exc.stdout or "")[-20000:], "stderr": "命令超過 30 秒"}
    except OSError as exc:
        payload = {"command": name, "returncode": 127, "stdout": "", "stderr": str(exc)}
    remember_console(payload)
    return payload


def run_agent(api_key, question, emit):
    initial = {name: execute_command(name) for name in ("pm2 status", "pm2 logs mok_web --lines 80", "curl health")}
    with CONSOLE_LOCK:
        initial["browser_console"] = list(RECENT_CONSOLE[-50:])
    messages = [
        {"role": "system", "content": AGENT_SYSTEM},
        {"role": "user", "content": question + "\n\n這是剛剛自動取得的主機資料：\n" + json.dumps(initial, ensure_ascii=False)},
    ]
    emit({"type": "step", "content": "已自動讀取 PM2 狀態、mok_web 日誌與 health，開始分析。"})
    for _ in range(12):
        payload = json.dumps({"model": "deepseek-chat", "messages": messages, "tools": TOOLS, "tool_choice": "auto", "temperature": 0.1}, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request("https://api.deepseek.com/chat/completions", data=payload, headers={"Content-Type": "application/json", "Authorization": "Bearer " + api_key})
        try:
            with urllib.request.urlopen(request, timeout=90) as response:
                result = json.loads(response.read().decode("utf-8"))
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            emit({"type": "error", "content": "DeepSeek API 錯誤：" + str(exc)})
            return
        choice = result.get("choices", [{}])[0].get("message", {})
        messages.append(choice)
        calls = choice.get("tool_calls") or []
        if not calls:
            emit({"type": "final", "content": choice.get("content", "DeepSeek 沒有回覆")})
            return
        for call in calls:
            name = call.get("function", {}).get("name", "")
            try:
                args = json.loads(call.get("function", {}).get("arguments", "{}"))
                if name == "read_file":
                    target = safe_path(args.get("path"))
                    if not target.is_file():
                        output = {"error": "找不到檔案"}
                    elif target.stat().st_size > MAX_FILE:
                        output = {"error": "檔案超過 2MB"}
                    else:
                        output = {"path": args.get("path"), "content": target.read_text(encoding="utf-8", errors="replace")}
                elif name == "write_file":
                    target = safe_path(args.get("path"))
                    content = str(args.get("content", ""))
                    if not target.is_file():
                        output = {"error": "只能修改已存在的檔案"}
                    elif len(content.encode("utf-8")) > MAX_FILE:
                        output = {"error": "檔案超過 2MB"}
                    else:
                        target.write_text(content, encoding="utf-8")
                        output = {"ok": True, "path": args.get("path")}
                elif name == "run_command":
                    output = execute_command(args.get("command", ""))
                else:
                    output = {"error": "未知工具"}
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                output = {"error": str(exc)}
            emit({"type": "tool", "content": name + " → " + json.dumps(output, ensure_ascii=False)[:12000]})
            messages.append({"role": "tool", "tool_call_id": call.get("id", ""), "content": json.dumps(output, ensure_ascii=False)})
    emit({"type": "error", "content": "已達到本次維修最多 12 個步驟，請查看目前工具輸出。"})


class Handler(BaseHTTPRequestHandler):
    server_version = "MOKAGI-Repair/1.0"

    def log_message(self, fmt, *args):
        return

    def send_json(self, payload, status=200):
        body = json_bytes(payload)
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/":
            try:
                body = PAGE.read_bytes()
            except OSError as exc:
                self.send_json({"error": str(exc)}, 500)
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(body)
            return
        if parsed.path == "/api/health":
            self.send_json({"status": "ok", "service": "repair_server", "root": str(ROOT)})
            return
        if parsed.path == "/api/tree":
            self.send_json({"root": str(ROOT), "tree": self.tree(ROOT)})
            return
        if parsed.path == "/api/file":
            try:
                path = parse_qs(parsed.query).get("path", [""])[0]
                target = safe_path(path)
                if not target.is_file():
                    self.send_json({"error": "找不到檔案"}, 404)
                    return
                if target.stat().st_size > MAX_FILE:
                    self.send_json({"error": "檔案超過 2MB"}, 413)
                    return
                self.send_json({"path": path, "content": target.read_text(encoding="utf-8", errors="replace")})
            except (ValueError, OSError) as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if parsed.path == "/api/console":
            with CONSOLE_LOCK:
                self.send_json({"events": list(RECENT_CONSOLE)})
            return
        if parsed.path == "/api/agent/events":
            job_id = parse_qs(parsed.query).get("job_id", [""])[0]
            with AGENT_LOCK:
                job = AGENT_JOBS.get(job_id)
            if not job:
                self.send_json({"error": "job not found"}, 404)
                return
            def stream():
                yield b": connected\n\n"
                while True:
                    try:
                        event = job["queue"].get(timeout=15)
                        yield b"data: " + json_bytes(event) + b"\n\n"
                        if event.get("type") == "job_done":
                            break
                    except __import__('queue').Empty:
                        yield b": heartbeat\n\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/event-stream; charset=utf-8")
            self.send_header("Cache-Control", "no-cache")
            self.send_header("Connection", "keep-alive")
            self.send_header("Access-Control-Allow-Origin", "*")
            self.end_headers()
            try:
                for chunk in stream():
                    self.wfile.write(chunk)
                    self.wfile.flush()
            except (BrokenPipeError, ConnectionResetError):
                pass
            return
        self.send_json({"error": "not found"}, 404)

    def tree(self, path, depth=0):
        if depth > 8:
            return []
        result = []
        try:
            entries = sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
            for entry in entries:
                if entry.name in {".git", "__pycache__"}:
                    continue
                item = {"name": entry.name, "path": str(entry.relative_to(ROOT)).replace(os.sep, "/"), "is_dir": entry.is_dir()}
                if item["is_dir"]:
                    item["children"] = self.tree(entry, depth + 1)
                result.append(item)
        except OSError:
            pass
        return result

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length > 3 * 1024 * 1024:
            self.send_json({"error": "request too large"}, 413)
            return
        try:
            data = json.loads(self.rfile.read(length) or b"{}")
        except (ValueError, json.JSONDecodeError):
            self.send_json({"error": "invalid json"}, 400)
            return
        if self.path == "/api/file":
            try:
                target = safe_path(data.get("path"))
                content = str(data.get("content", ""))
                if not target.is_file():
                    self.send_json({"error": "只能修改已存在的檔案"}, 404)
                    return
                if len(content.encode("utf-8")) > MAX_FILE:
                    self.send_json({"error": "檔案超過 2MB"}, 413)
                    return
                target.write_text(content, encoding="utf-8")
                self.send_json({"ok": True, "path": data.get("path")})
            except (ValueError, OSError) as exc:
                self.send_json({"error": str(exc)}, 400)
            return
        if self.path == "/api/exec":
            name = str(data.get("command", ""))
            argv = COMMANDS.get(name)
            if not argv:
                self.send_json({"error": "命令不在白名單內", "allowed": sorted(COMMANDS)}, 403)
                return
            try:
                result = subprocess.run(argv, cwd=ROOT, capture_output=True, text=True, timeout=30)
                payload = {"command": name, "returncode": result.returncode, "stdout": result.stdout[-20000:], "stderr": result.stderr[-20000:]}
            except subprocess.TimeoutExpired as exc:
                payload = {"command": name, "returncode": 124, "stdout": str(exc.stdout or "")[-20000:], "stderr": "命令超過 30 秒"}
            except OSError as exc:
                payload = {"command": name, "returncode": 127, "stdout": "", "stderr": str(exc)}
            remember_console(payload)
            self.send_json(payload)
            return
        if self.path == "/api/console":
            remember_console(data)
            self.send_json({"ok": True})
            return
        if self.path == "/api/agent":
            api_key = str(data.get("api_key", "")).strip()
            question = str(data.get("question", "")).strip()
            if not api_key or not question:
                self.send_json({"error": "需要 api_key 與 question"}, 400)
                return
            if len(question) > 12000:
                self.send_json({"error": "問題太長"}, 413)
                return
            self.send_json({"ok": True, "job_id": create_agent_job(api_key, question)})
            return
        self.send_json({"error": "not found"}, 404)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=5001)
    args = parser.parse_args()
    if args.host not in {"127.0.0.1", "localhost"}:
        raise SystemExit("repair_server must bind to localhost")
    ThreadingHTTPServer.allow_reuse_address = True
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"repair_server listening on {args.host}:{args.port}", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
