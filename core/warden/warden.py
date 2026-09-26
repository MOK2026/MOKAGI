#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
warden.py  ── MOKAGI 緊急守衛（全新、獨立，不 import 任何 mokagi 代碼）

定位：主人要的「緊急鍵」。與現有 `緊急重啟`(index.html stopBtn) 及
      `pm2 restart mok_agi` 完全無關，不共用、不修改。

功能：
  GET  /                    簡易操作面板 (warden.html)
  GET  /api/status          各服務 PID / 狀態
  POST /api/panic           緊急停止：停掉所有 mokagi 相關進程並上鎖
  POST /api/resume          解除鎖定並把 pm2 服務拉回（pm2 start，非 restart）
  POST /api/restart?name=X  單一服務安全重啟（pm2 stop + pm2 start）
  GET  /api/log?n=200       最近日誌

安全設計（避免重蹈「agent 殺死自己 web 進程」的 bug）：
  1. warden 自身、其上層祖先、其子進程 一律排除，永不擊殺。
  2. 先 SIGTERM、逾時再 SIGKILL。
  3. 只監聽 127.0.0.1（不外曝）。
  4. panic 會寫鎖檔，watchdog 看到鎖檔就不會自動復活。
"""
import json
import os
import signal
import subprocess
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

BASE = Path(__file__).resolve().parent
CONF_PATH = BASE / "warden.json"
LOCK_PATH = BASE / "LOCK"
LOG_PATH = BASE / "warden.log"

DEFAULTS = {
    "host": "127.0.0.1",
    "port": 5599,
    "token": "",
    "services": [
        {"name": "mok_agi", "type": "pm2", "pm2_name": "mok_agi", "critical": True},
        {"name": "mok_web", "type": "match",
         "pattern": "/home/ubuntu/.mok/frontends/mok_web.py", "critical": True},
        {"name": "mok_bots", "type": "match",
         "pattern": "/home/ubuntu/.mok/frontends/mok_tg.py", "critical": True},
    ],
    "panic": {
        "pm2_stop": ["mok_agi"],
        "kill_patterns": [
            "/home/ubuntu/.mok/frontends/mok_web.py",
            "/home/ubuntu/.mok/frontends/mok_tg.py",
            "/home/ubuntu/.mok/core/launcher.py",
        ],
        "grace_seconds": 3,
    },
    "watchdog": {"enabled": False, "interval": 15, "auto_restart": []},
}


def log(msg, level="INFO"):
    line = "%s [%s] %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), level, msg)
    try:
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass
    print(line, flush=True)


def load_conf():
    conf = json.loads(json.dumps(DEFAULTS))
    try:
        if CONF_PATH.exists():
            user = json.loads(CONF_PATH.read_text(encoding="utf-8"))
            for k, v in user.items():
                if isinstance(v, dict) and isinstance(conf.get(k), dict):
                    conf[k].update(v)
                else:
                    conf[k] = v
    except Exception as e:
        log("讀取 warden.json 失敗，用預設值：%s" % e, "WARN")
    return conf


# ================= 進程工具（純 stdlib） =================
def read_procs():
    """回傳 {pid: (ppid, cmdline)} 快照。"""
    out = {}
    for name in os.listdir("/proc"):
        if not name.isdigit():
            continue
        pid = int(name)
        try:
            with open("/proc/%d/stat" % pid, "rb") as f:
                data = f.read().decode("utf-8", "replace")
            ppid = int(data.rsplit(")", 1)[1].split()[1])
            with open("/proc/%d/cmdline" % pid, "rb") as f:
                cmd = f.read().decode("utf-8", "replace").replace("\x00", " ").strip()
            out[pid] = (ppid, cmd)
        except Exception:
            continue
    return out


def ancestors_of(pid, procs):
    seen, cur = set(), pid
    for _ in range(40):
        if cur not in procs:
            break
        ppid = procs[cur][0]
        if ppid <= 1 or ppid in seen:
            break
        seen.add(ppid)
        cur = ppid
    return seen


def protected_pids(procs):
    """永不擊殺：warden 自己 + 祖先鏈 + 自己的直接子進程。"""
    me = os.getpid()
    prot = {me}
    prot |= ancestors_of(me, procs)
    for p, (ppid, _cmd) in procs.items():
        if ppid == me:
            prot.add(p)
    return prot


def match_pids(pattern, procs):
    return [p for p, (_pp, cmd) in procs.items() if pattern in cmd]


def pm2_pids(names):
    res = {}
    try:
        raw = subprocess.run(["pm2", "jlist"], capture_output=True, text=True, timeout=15).stdout
        for app in json.loads(raw or "[]"):
            if app.get("name") in names:
                res[app["name"]] = app.get("pid") or 0
    except Exception as e:
        log("pm2 jlist 失敗：%s" % e, "WARN")
    return res


def kill_pids(pids, grace, procs):
    """先 SIGTERM 後 SIGKILL，跳過受保護 PID。"""
    prot = protected_pids(procs)
    targets = [p for p in pids if p not in prot and p > 1]
    result = {}
    for p in targets:
        try:
            os.kill(p, signal.SIGTERM)
            result[p] = "SIGTERM"
        except ProcessLookupError:
            result[p] = "gone"
        except Exception as e:
            result[p] = "err:%s" % e
    deadline = time.time() + grace
    while time.time() < deadline:
        if not [p for p in targets if os.path.exists("/proc/%d" % p)]:
            break
        time.sleep(0.3)
    for p in targets:
        if os.path.exists("/proc/%d" % p):
            try:
                os.kill(p, signal.SIGKILL)
                result[p] = str(result.get(p, "")) + "+SIGKILL"
            except Exception:
                pass
    return result


def pm2_cmd(args, timeout=60):
    try:
        r = subprocess.run(["pm2"] + args, capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 1, str(e)


# ================= 動作 =================
_conf = load_conf()


def do_status():
    procs = read_procs()
    svcs = []
    for s in _conf["services"]:
        pids = []
        if s.get("type") == "pm2":
            pid = pm2_pids([s.get("pm2_name")]).get(s.get("pm2_name"), 0)
            if pid:
                pids = [pid]
        else:
            pids = match_pids(s.get("pattern", "\x00"), procs)
        svcs.append({
            "name": s["name"],
            "critical": bool(s.get("critical")),
            "pids": pids,
            "alive": bool(pids),
            "cmds": [procs[p][1][:120] for p in pids if p in procs][:6],
        })
    return {
        "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        "locked": LOCK_PATH.exists(),
        "warden_pid": os.getpid(),
        "services": svcs,
    }


def do_panic(reason="manual"):
    procs = read_procs()
    steps = []
    log("PANIC 觸發（%s）" % (reason or "manual"))
    try:
        LOCK_PATH.write_text("%s panic\n" % time.time(), encoding="utf-8")
        steps.append("已上鎖 LOCK")
    except Exception as e:
        steps.append("上鎖失敗：%s" % e)
    for name in _conf["panic"]["pm2_stop"]:
        rc, out = pm2_cmd(["stop", name])
        steps.append("pm2 stop %s rc=%s" % (name, rc))
        log("pm2 stop %s -> rc=%s %s" % (name, rc, out.strip()[:200]))
    procs = read_procs()
    killed = {}
    for pat in _conf["panic"]["kill_patterns"]:
        pids = match_pids(pat, procs)
        if pids:
            killed.update(kill_pids(pids, _conf["panic"]["grace_seconds"], procs))
    steps.append("擊殺殘留 %d 個進程" % len(killed))
    time.sleep(0.5)
    procs2 = read_procs()
    leftover = {}
    for pat in _conf["panic"]["kill_patterns"]:
        for p in match_pids(pat, procs2):
            leftover[p] = procs2[p][1][:100]
    log("PANIC 完成，殘留=%s" % leftover)
    return {"ok": True, "reason": reason, "steps": steps,
            "killed": killed, "leftover": leftover}


def do_resume():
    steps = []
    try:
        if LOCK_PATH.exists():
            LOCK_PATH.unlink()
            steps.append("已解鎖 LOCK")
    except Exception as e:
        steps.append("解鎖失敗：%s" % e)
    for name in _conf["panic"]["pm2_stop"]:
        rc, out = pm2_cmd(["start", name])
        steps.append("pm2 start %s rc=%s" % (name, rc))
        log("pm2 start %s -> rc=%s %s" % (name, rc, out.strip()[:200]))
    return {"ok": True, "steps": steps}


def do_restart(name):
    svc = next((s for s in _conf["services"] if s["name"] == name), None)
    if not svc:
        return {"ok": False, "error": "未知服務 %s" % name}
    steps = []
    if svc.get("type") == "pm2":
        app = svc["pm2_name"]
        rc1, _o1 = pm2_cmd(["stop", app])
        steps.append("pm2 stop %s rc=%s" % (app, rc1))
        time.sleep(1)
        rc2, _o2 = pm2_cmd(["start", app])
        steps.append("pm2 start %s rc=%s" % (app, rc2))
        log("safe-restart %s stop=%s start=%s" % (app, rc1, rc2))
    else:
        procs = read_procs()
        pids = match_pids(svc.get("pattern", "\x00"), procs)
        steps.append("擊殺 %d 個進程（由 launcher/pm2 自行拉回）" % len(pids))
        kill_pids(pids, 3, procs)
    return {"ok": True, "service": name, "steps": steps}


def watchdog_loop():
    wd = _conf.get("watchdog", {})
    if not wd.get("enabled"):
        log("watchdog 未啟用（預設關）。")
        return
    interval = int(wd.get("interval", 15))
    log("watchdog 啟動 interval=%ss auto_restart=%s" % (interval, wd.get("auto_restart")))
    while True:
        time.sleep(interval)
        if LOCK_PATH.exists():
            continue
        procs = read_procs()
        for s in _conf["services"]:
            if not s.get("critical"):
                continue
            if s.get("type") == "match":
                alive = bool(match_pids(s.get("pattern", "\x00"), procs))
            else:
                alive = bool(pm2_pids([s.get("pm2_name")]).get(s.get("pm2_name"), 0))
            if not alive and s["name"] in (wd.get("auto_restart") or []):
                log("watchdog: %s 掛了 → 嘗試復原" % s["name"], "WARN")
                try:
                    do_restart(s["name"])
                except Exception as e:
                    log("watchdog 復原失敗：%s" % e, "ERROR")


# ================= HTTP =================
class Handler(BaseHTTPRequestHandler):
    server_version = "mok-warden/1.0"

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False, indent=2).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _html(self, text, code=200):
        body = text.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _auth_ok(self, qs):
        tok = _conf.get("token") or ""
        if not tok:
            return True
        return (qs.get("token", [""])[0] == tok) or (self.headers.get("X-Warden-Token") == tok)

    def do_GET(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if not self._auth_ok(qs):
            return self._json({"ok": False, "error": "unauthorized"}, 401)
        if u.path in ("/", "/index.html"):
            page = BASE / "warden.html"
            return self._html(page.read_text(encoding="utf-8") if page.exists() else "<h1>warden</h1>")
        if u.path == "/api/status":
            return self._json(do_status())
        if u.path == "/api/log":
            n = int(qs.get("n", ["200"])[0])
            lines = []
            if LOG_PATH.exists():
                lines = LOG_PATH.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]
            return self._json({"ok": True, "lines": lines})
        return self._json({"ok": False, "error": "not found"}, 404)

    def do_POST(self):
        u = urlparse(self.path)
        qs = parse_qs(u.query)
        if not self._auth_ok(qs):
            return self._json({"ok": False, "error": "unauthorized"}, 401)
        try:
            if u.path == "/api/panic":
                return self._json(do_panic(qs.get("reason", ["manual"])[0]))
            if u.path == "/api/resume":
                return self._json(do_resume())
            if u.path == "/api/restart":
                return self._json(do_restart(qs.get("name", [""])[0]))
        except Exception as e:
            log("處理 %s 失敗：%s" % (u.path, e), "ERROR")
            return self._json({"ok": False, "error": str(e)}, 500)
        return self._json({"ok": False, "error": "not found"}, 404)

    def log_message(self, *a):
        pass


def request_loop():
    """目錄旗標式安全重啟：#2 方案 A。
    agent 只要 `touch ~/.mok/core/warden/requests/<服務名>.req`，
    warden 就會代它安全重啟，agent 永遠不需要自己動刀。"""
    req_dir = BASE / "requests"
    try:
        req_dir.mkdir(exist_ok=True)
    except Exception:
        pass
    log("request_loop 啟動（監看 %s）" % req_dir)
    while True:
        time.sleep(3)
        try:
            for f in sorted(req_dir.glob("*.req")):
                name = f.stem
                log("收到安全重啟請求：%s" % name)
                try:
                    do_restart(name)
                except Exception as e:
                    log("請求處理失敗：%s" % e, "ERROR")
                try:
                    f.unlink()
                except Exception:
                    pass
        except Exception as e:
            log("request_loop 異常：%s" % e, "ERROR")


def main():
    log("warden 啟動 pid=%s listen=%s:%s" % (os.getpid(), _conf["host"], _conf["port"]))
    threading.Thread(target=watchdog_loop, daemon=True).start()
    threading.Thread(target=request_loop, daemon=True).start()
    srv = ThreadingHTTPServer((_conf["host"], int(_conf["port"])), Handler)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        log("warden 收到中斷，結束。")


if __name__ == "__main__":
    main()

