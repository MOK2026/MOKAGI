#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""async_job.py - P1 durable background job (long-op async).

把會阻塞 agent 工具迴圈的長操作（例：MPT 產片約 15 分鐘）改成：
投遞背景 job，立刻拿到 job id，由 cron 巡檢，完成後可用繼續碼觸發新一輪。

狀態檔  : ~/.mok/jobs_async/{name}.json
日誌    : ~/.mok/jobs_async/logs/{name}.log
完成佇列: ~/.mok/jobs_async/done.jsonl

CLI:
  submit {name} [--cwd DIR] [--continue-code CODE] [--agent NAME] -- shell_cmd
  status [{name}]
  reap                 # cron 每分鐘呼叫
  logs {name} [N]
  clean [DAYS]

環境變數:
  MOK_ASYNC_DIR                     預設 ~/.mok/jobs_async
  MOK_TG_BOT_TOKEN / MOK_TG_CHAT_ID 皆有設定時，job 完成發 Telegram（預設不發）
"""
import os
import sys
import json
import time
import glob
import subprocess

HOME = os.path.expanduser("~")
MOK = os.path.join(HOME, ".mok")
BASE = os.environ.get("MOK_ASYNC_DIR", os.path.join(MOK, "jobs_async"))
LOGD = os.path.join(BASE, "logs")
DONE = os.path.join(BASE, "done.jsonl")


def _ensure():
    os.makedirs(LOGD, exist_ok=True)


def _spath(name):
    return os.path.join(BASE, name + ".json")


def _alive(pid):
    try:
        return os.path.exists("/proc/%d" % int(pid))
    except Exception:
        return False


def _save(st):
    _ensure()
    tmp = _spath(st["name"]) + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(st, f, ensure_ascii=False, indent=2)
    os.replace(tmp, _spath(st["name"]))


def submit(name, cmd, cwd=None, continue_code=None, agent=None):
    _ensure()
    logf = os.path.join(LOGD, name + ".log")
    rcf = os.path.join(BASE, name + ".rc")
    try:
        os.remove(rcf)
    except OSError:
        pass
    gt = chr(62)
    wrapped = "( %s ) %s%s 2%s&1; echo $? %s%s" % (cmd, gt, logf, gt, gt, rcf)
    fh = open(logf, "ab")
    proc = subprocess.Popen(["bash", "-lc", wrapped], cwd=cwd or HOME,
                            stdout=fh, stderr=subprocess.STDOUT,
                            stdin=subprocess.DEVNULL, start_new_session=True)
    fh.close()
    st = {"name": name, "cmd": cmd, "cwd": cwd or HOME, "pid": proc.pid,
          "start": time.time(), "end": None, "status": "running", "rc": None,
          "log": logf, "continue_code": continue_code, "agent": agent}
    _save(st)
    print("SUBMITTED %s pid=%d log=%s" % (name, proc.pid, logf))
    return st


def _notify(rec):
    tok = os.environ.get("MOK_TG_BOT_TOKEN")
    chat = os.environ.get("MOK_TG_CHAT_ID")
    if not tok or not chat:
        return
    try:
        import urllib.request
        import urllib.parse
        msg = "[async_job] %s %s rc=%s 耗時%.0fs%s" % (
            rec["name"], rec["status"], rec["rc"], rec["dur"],
            (" | 回覆 /continue %s 續跑" % rec["continue_code"]) if rec.get("continue_code") else "")
        data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
        urllib.request.urlopen("https://api.telegram.org/bot%s/sendMessage" % tok,
                               data=data, timeout=15)
    except Exception as e:
        print("notify_err", e)


def reap():
    _ensure()
    changed = []
    for jf in sorted(glob.glob(os.path.join(BASE, "*.json"))):
        try:
            st = json.load(open(jf, encoding="utf-8"))
        except Exception:
            continue
        if st.get("status") != "running":
            continue
        if _alive(st.get("pid", 0)):
            continue
        rc = None
        try:
            rc = int(open(os.path.join(BASE, st["name"] + ".rc")).read().strip())
        except Exception:
            rc = None
        st["end"] = time.time()
        st["rc"] = rc if rc is not None else 0
        st["status"] = "done" if (rc in (0, None)) else "failed"
        _save(st)
        rec = {"name": st["name"], "status": st["status"], "rc": st["rc"],
               "dur": round((st["end"] or 0) - (st["start"] or 0), 1),
               "continue_code": st.get("continue_code"), "agent": st.get("agent"),
               "at": time.time()}
        with open(DONE, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        changed.append(rec)
        print("DONE %s rc=%s" % (st["name"], st["rc"]))
        _notify(rec)
    if not changed:
        print("reap: 無完成的 job")
    return changed


def status(name=None):
    _ensure()
    rows = []
    paths = [_spath(name)] if name else sorted(glob.glob(os.path.join(BASE, "*.json")))
    for jf in paths:
        try:
            st = json.load(open(jf, encoding="utf-8"))
        except Exception:
            continue
        if st.get("status") == "running" and not _alive(st.get("pid", 0)):
            st["status"] = "stale"
        rows.append(st)
    if not rows:
        print("(無 job)")
        return rows
    for st in rows:
        dur = round((st.get("end") or time.time()) - (st.get("start") or 0), 1)
        print("%-20s %-8s pid=%s rc=%s %.0fs %s" % (
            st.get("name"), st.get("status"), st.get("pid"), st.get("rc"), dur, st.get("cmd", "")))
    return rows


def logs(name, n=40):
    p = os.path.join(LOGD, name + ".log")
    try:
        lines = open(p, encoding="utf-8", errors="replace").read().splitlines()
    except Exception as e:
        print("logs_err", e)
        return
    for ln in lines[-int(n):]:
        print(ln)


def clean(days=7):
    cutoff = time.time() - float(days) * 86400
    n = 0
    for jf in glob.glob(os.path.join(BASE, "*.json")):
        try:
            st = json.load(open(jf, encoding="utf-8"))
        except Exception:
            continue
        if st.get("status") in ("done", "failed") and float(st.get("end") or 0) < cutoff:
            os.remove(jf)
            n += 1
    print("clean: 移除 %d 筆" % n)


def main(argv):
    if not argv:
        print(__doc__)
        return 2
    cmd, rest = argv[0], argv[1:]
    if cmd == "submit":
        if "--" not in rest:
            print("用法: submit name [opts] -- shell_cmd")
            return 2
        i = rest.index("--")
        opts, shell = rest[:i], rest[i + 1:]
        name = opts[0] if opts else ("job_" + str(int(time.time())))
        cwd = cc = agent = None
        j = 1
        while j < len(opts):
            if opts[j] == "--cwd":
                cwd = opts[j + 1]; j += 2
            elif opts[j] == "--continue-code":
                cc = opts[j + 1]; j += 2
            elif opts[j] == "--agent":
                agent = opts[j + 1]; j += 2
            else:
                j += 1
        submit(name, " ".join(shell), cwd=cwd, continue_code=cc, agent=agent)
        return 0
    if cmd == "status":
        status(rest[0] if rest else None); return 0
    if cmd == "reap":
        reap(); return 0
    if cmd == "logs":
        logs(rest[0], rest[1] if len(rest) > 1 else 40); return 0
    if cmd == "clean":
        clean(rest[0] if rest else 7); return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
