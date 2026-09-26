#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""loop_watchdog.py — P2 迴圈 / 長操作 watchdog（建議 cron 每 5 分鐘）

職責（刻意只做兩件事，不做收尾）：
  1. 觀測 pending 任務（~/.mok/agent/*/_job.json）中「近期且未完成」且超過門檻時間者 → 通知。
     註：只告警「最近 MAX_AGE 內」的任務，避免把幾週前的殘留紀錄當成卡住。
  2. 觀測符合「長操作樣式」的程序，執行時間超過門檻者 → 通知（--kill ＋ LW_KILL=1 時才終止）。

輸出：~/.mok/logs/loop_watchdog.log（+ 告警 jsonl）；可選 Telegram（設 MOK_TG_BOT_TOKEN / MOK_TG_CHAT_ID）。

環境變數：
  LW_STUCK_MIN  預設 30（分鐘）   任務卡住門檻
  LW_MAX_AGE    預設 720（分鐘）  只告警此時間窗內的任務（更舊者視為殘留，略過）
  LW_PROC_MIN   預設 30（分鐘）   長操作程序門檻
  LW_KILL       預設 0；設 1 且執行時帶 --kill 才會終止超時程序
"""
import os
import sys
import json
import time
import glob
import subprocess

HOME = os.path.expanduser("~")
MOK = os.path.join(HOME, ".mok")
LOG = os.path.join(MOK, "logs", "loop_watchdog.log")
ALERT = os.path.join(MOK, "logs", "loop_watchdog_alerts.jsonl")
STATE = os.path.join(MOK, "logs", "loop_watchdog_state.json")

STUCK_MIN = float(os.environ.get("LW_STUCK_MIN", "30"))
MAX_AGE_MIN = float(os.environ.get("LW_MAX_AGE", "720"))
PROC_MIN = float(os.environ.get("LW_PROC_MIN", "30"))
KILL = os.environ.get("LW_KILL", "0") == "1"
DO_KILL = "--kill" in sys.argv

PATTERNS = ("money_video", "run_pipeline", "clip_upload", "daily.py",
            "transcribe_all", "text_to_video")


def log(m):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), m)
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def notify(msg):
    try:
        with open(ALERT, "a", encoding="utf-8") as f:
            f.write(json.dumps({"ts": time.time(), "msg": msg}, ensure_ascii=False) + "\n")
    except Exception:
        pass
    tok = os.environ.get("MOK_TG_BOT_TOKEN")
    chat = os.environ.get("MOK_TG_CHAT_ID")
    if tok and chat:
        try:
            import urllib.request
            import urllib.parse
            data = urllib.parse.urlencode({"chat_id": chat, "text": msg}).encode()
            urllib.request.urlopen("https://api.telegram.org/bot%s/sendMessage" % tok, data=data, timeout=10)
        except Exception as e:
            log("notify_tg_err %s" % e)


def load_state():
    try:
        return json.load(open(STATE, encoding="utf-8"))
    except Exception:
        return {}


def save_state(st):
    try:
        os.makedirs(os.path.dirname(STATE), exist_ok=True)
        json.dump(st, open(STATE, "w", encoding="utf-8"), ensure_ascii=False)
    except Exception:
        pass


def scan_tasks(now):
    hits = []
    for jf in glob.glob(os.path.join(MOK, "agent", "*", "_job.json")):
        try:
            data = json.load(open(jf, encoding="utf-8"))
        except Exception:
            continue
        agent = os.path.basename(os.path.dirname(jf))
        tasks = []
        if isinstance(data, dict):
            for k, v in data.items():
                if isinstance(v, dict) and "goal" in v:
                    tasks.append((k, v))
                elif isinstance(v, dict):
                    for code, t in v.items():
                        if isinstance(t, dict):
                            tasks.append((code, t))
        for code, t in tasks:
            status = str(t.get("status", "running"))
            if status in ("done", "completed", "success", "paused"):
                continue
            ts = float(t.get("timestamp", 0) or 0)
            if not ts:
                continue
            age_min = (now - ts) / 60.0
            if STUCK_MIN < age_min < MAX_AGE_MIN:
                hits.append((agent, code, status, age_min))
    return hits


def scan_procs(now):
    hits = []
    try:
        out = subprocess.check_output(["ps", "-eo", "pid,etimes,args"], text=True, errors="ignore")
    except Exception as e:
        log("ps_err %s" % e)
        return hits
    for line in out.splitlines()[1:]:
        parts = line.strip().split(None, 2)
        if len(parts) < 3:
            continue
        pid_s, etimes, args = parts
        if "loop_watchdog" in args or "ps -eo" in args:
            continue
        if not any(p in args for p in PATTERNS):
            continue
        try:
            secs = int(etimes)
        except Exception:
            continue
        if secs > PROC_MIN * 60:
            hits.append((int(pid_s), secs / 60.0, args[:140]))
    return hits


def main():
    now = time.time()
    st = load_state()
    alerts = []
    for agent, code, status, mins in scan_tasks(now):
        key = "task:%s:%s" % (agent, code)
        if now - float(st.get(key, 0) or 0) > 1800:
            alerts.append("🐢 [watchdog] %s 任務疑似卡住：%s（status=%s，已 %.0f 分鐘）"
                          % (agent, code, status, mins))
            st[key] = now
    for pid, mins, args in scan_procs(now):
        key = "proc:%d" % pid
        if now - float(st.get(key, 0) or 0) > 1800:
            alerts.append("🐢 [watchdog] 長操作程序疑似卡住：pid=%d 已 %.0f 分鐘：%s" % (pid, mins, args))
            st[key] = now
            if KILL and DO_KILL:
                try:
                    os.kill(pid, 15)
                    alerts.append("   ↳ 已送 SIGTERM 給 pid=%d（LW_KILL=1）" % pid)
                except Exception as e:
                    log("kill_err %s" % e)
    for k in list(st.keys()):
        if now - float(st.get(k, 0) or 0) > 7 * 86400:
            del st[k]
    if alerts:
        for a in alerts:
            log(a)
            notify(a)
    else:
        log("ok: 無卡住任務/程序")
    save_state(st)
    return 0


if __name__ == "__main__":
    sys.exit(main())
