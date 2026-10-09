#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cost_guard_watch.py — 成本速率護欄巡檢（cron 每分鐘）

職責（shadow 優先）：
  1) 讀 cost_guard 記帳檔（.memory 內 jsonl），算每個 agent 的 1 分鐘 / 1 小時 / 1 天金額。
  2) 任一窗越線 → 告警（Telegram ＋ alerts.jsonl ＋ log）。
  3) shadow 模式（預設）：只告警，**絕不**擋任何回合、不寫冷卻旗標。
     on 模式：除告警外，另寫冷卻旗標供准入層讀（未來接 global_gate；本版仍不主動擋）。

CLI：watch（cron 用，預設）／report（印現況分布）／status。
任何例外一律吞掉、fail-open。
"""
import os
import sys
import json
import time

HERE = os.path.dirname(os.path.abspath(__file__))
MOK = os.path.dirname(os.path.dirname(HERE))
CORE = os.path.join(MOK, "core")
if CORE not in sys.path:
    sys.path.insert(0, CORE)

import cost_guard as cg

LOG = os.path.join(MOK, "logs", "cost_guard_watch.log")
STATE = os.path.join(MOK, ".memory", "cost_guard_watch_state.json")
FLAGDIR = os.path.join("/tmp", "mok_cost_guard")
ALERT_COOLDOWN = 600          # 同一 agent 同一窗，10 分鐘內不重複告警
SUMMARY_INTERVAL = 1800       # 每 30 分鐘寫一次分布摘要


def log(m):
    line = "%s %s" % (time.strftime("%Y-%m-%d %H:%M:%S"), m)
    print(line, flush=True)
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write(line + "\n")
    except Exception:
        pass


def load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return default


def save(path, obj):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
    except Exception:
        pass


def _read_env(path):
    cfg = {}
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, _, v = line.partition("=")
                    cfg[k.strip()] = v.strip()
    except Exception:
        pass
    return cfg


def tg_notify(text):
    cfg = _read_env(os.path.join(MOK, "agent", "稚", ".\u7a1a"))
    tok = cfg.get("MOK_TG_TOKEN")
    chat = cfg.get("ADMIN_CHAT_ID") or cfg.get("MOK_TG_CHAT_ID")
    if not tok or not chat:
        return False
    try:
        import urllib.request
        body = json.dumps({"chat_id": chat, "text": text,
                           "disable_web_page_preview": True}).encode("utf-8")
        req = urllib.request.Request(
            "https://api.telegram.org/bot" + tok + "/sendMessage",
            data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def write_alert(rec):
    try:
        os.makedirs(os.path.dirname(cg.ALERT_LOG), exist_ok=True)
        with open(cg.ALERT_LOG, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception:
        pass



def load_events(now):
    """事件來源：db（預設，權威；含 cache 命中欄位）｜jsonl｜both。"""
    src = (cg.conf("MOK_cost_source", "db") or "db").lower()
    ev = []
    if src in ("db", "both"):
        ev += cg.read_db(now - 86400)
    if src in ("jsonl", "both"):
        ev += cg.read_all()
    return ev


def evaluate(events, now, wins):
    """回傳 (hits, res)：hits＝越線清單。"""
    res, _ = cg.window_costs(events, now)
    hits = []
    for agent, d in res.items():
        for win in ("min", "hour", "day"):
            limit = wins.get(win, 0) or 0
            if limit > 0 and d.get(win, 0) >= limit:
                hits.append({"agent": agent, "window": win, "spent": d[win],
                             "limit": limit, "calls": d.get("calls", 0),
                             "day": d.get("day", 0)})
    return hits, res


def watch(now=None):
    now = float(now or time.time())
    m = cg.mode()
    if m == cg.MODE_OFF:
        print("mode=off, skip")
        return
    st = load(STATE, {})
    last_alert = st.get("last_alert", {}) or {}
    events = load_events(now)
    wins = cg.thresholds()
    hits, res = evaluate(events, now, wins)
    triggered = 0
    for h in hits:
        a, w = h["agent"], h["window"]
        key = a + "|" + w
        if now - float(last_alert.get(key, 0) or 0) < ALERT_COOLDOWN:
            continue
        last_alert[key] = now
        triggered += 1
        label = {"min": "1 分鐘", "hour": "1 小時", "day": "1 天"}.get(w, w)
        head = "\u26a0\ufe0f 成本速率護欄\uff08shadow \u53ea\u8a18\u5e33\u3001\u4e0d\u771f\u505c\uff09" \
            if m == cg.MODE_SHADOW else "\u26a0\ufe0f 成本速率護欄\uff08on\uff09"
        msg = ("%s\n\u4f8d\u5973\uff1a%s\n\u7a97\u53e3\uff1a%s \u82b1\u8cbb %.2f \u5143\uff08\u9580\u6abb %.2f\uff09\n"
               "\u7d2f\u8a08\uff1a1h %.2f \u5143\uff0f1d %.2f \u5143\uff08%d \u6b21\u547c\u53eb\uff09\n"
               "\u6a21\u5f0f\uff1a%s\uff08%s\uff09"
               % (head, a, label, h["spent"], h["limit"], res.get(a, {}).get("hour", 0),
                  res.get(a, {}).get("day", 0), h["calls"], m,
                  "\u672a\u66ab\u505c\uff0c\u53ea\u662f\u544a\u8b66" if m == cg.MODE_SHADOW else "\u5df2\u5beb\u51b7\u537b\u65d7\u6a19"))
        rec = {"ts": now, "mode": m, "agent": a, "window": w,
               "spent": round(h["spent"], 4), "limit": h["limit"],
               "hour": res.get(a, {}).get("hour", 0), "day": res.get(a, {}).get("day", 0),
               "calls": h["calls"]}
        write_alert(rec)
        ok = tg_notify(msg)
        log("ALERT %s agent=%s win=%s spent=%.3f limit=%.2f tg=%s" %
            (m, a, w, h["spent"], h["limit"], ok))
        if m == cg.MODE_ON:
            try:
                os.makedirs(FLAGDIR, exist_ok=True)
                save(os.path.join(FLAGDIR, a + ".json"),
                     {"ts": now, "window": w, "spent": h["spent"], "limit": h["limit"]})
            except Exception:
                pass
    st["last_alert"] = last_alert
    st["last_run"] = now
    if now - float(st.get("last_summary", 0) or 0) >= SUMMARY_INTERVAL:
        st["last_summary"] = now
        parts = []
        for a, d in sorted(res.items(), key=lambda x: -x[1]["day"])[:12]:
            parts.append("%s d=%.2f h=%.2f m=%.2f" % (a, d["day"], d["hour"], d["min"]))
        log("SUMMARY mode=%s agents=%d | %s" % (m, len(res), " ; ".join(parts)))
    save(STATE, st)
    print(json.dumps({"mode": m, "agents": len(res), "hits": len(hits),
                      "alerted": triggered}, ensure_ascii=False))


def report():
    events = load_events(time.time())
    res, wins = cg.window_costs(events)
    print("mode =", cg.mode(), "| thresholds =", wins, "| events =", len(events))
    print("%-16s %10s %10s %10s %8s" % ("agent", "day(RMB)", "hour(RMB)", "min(RMB)", "calls"))
    for a, d in sorted(res.items(), key=lambda x: -x[1]["day"]):
        print("%-16s %10.3f %10.3f %10.3f %8d" % (a, d["day"], d["hour"], d["min"], d["calls"]))


def _main(argv):
    cmd = (argv[1] if len(argv) > 1 else "watch").strip().lower()
    try:
        if cmd == "watch":
            watch()
        elif cmd == "report":
            report()
        elif cmd == "status":
            st = load(STATE, {})
            print(json.dumps({"mode": cg.mode(), "thresholds": cg.thresholds(),
                              "state": st}, ensure_ascii=False))
        else:
            print("usage: watch|report|status")
    except Exception as e:
        log("ERR %s" % e)
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
