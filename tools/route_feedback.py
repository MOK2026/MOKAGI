#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""route_feedback.py — P4 觀測驅動自我優化（繞路學習迴路）

find.py 早就會讀 ~/.mok/skill/_route_feedback.json 做 _learned_boost 加權，
卻沒有任何程式寫這個檔。這裡把它接起來。

觀測（find / skill view 呼叫進來）→ 判定繞路 → 寫回饋加權 → report 產生教材與缺口清單。
CLI: report | analyze | bump <name> [source] | use <name> | hook <q> [names] | log | path
"""
import json, os, sys, time
from datetime import datetime

SKILL_DIR = os.path.expanduser("~/.mok/skill")
LOG_PATH = os.path.join(SKILL_DIR, "_route_log.jsonl")
FEEDBACK = os.path.join(SKILL_DIR, "_route_feedback.json")
REPORT_PATH = os.path.join(SKILL_DIR, "_route_report.md")
WINDOW = 1800
DETOUR_MIN = 2
READ_TAIL = 800
LOG_MAX = 5000
MAX_CASES = 15
NL = chr(10)


def _at(ts=None):
    return datetime.fromtimestamp(ts or time.time()).strftime("%Y-%m-%d %H:%M:%S")


def _clip(s, n=90):
    s = " ".join(str(s or "").split())
    return (s[:n] + "…") if len(s) > n else s


def _agent():
    return (os.environ.get("MOK_AGENT_NAME") or os.environ.get("AGENT_NAME")
            or os.environ.get("MOK_AGENT") or "unknown")


def _lock(fp):
    try:
        import fcntl
        fcntl.flock(fp.fileno(), fcntl.LOCK_EX)
    except Exception:
        pass


def _read_tail(n=READ_TAIL):
    out = []
    try:
        with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
            _lock(f)
            lines = f.readlines()[-n:]
    except Exception:
        return out
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
            if isinstance(r, dict):
                out.append(r)
        except Exception:
            continue
    return out


def _append(rec):
    try:
        os.makedirs(SKILL_DIR, exist_ok=True)
        rec = dict(rec)
        rec.setdefault("ts", time.time())
        rec.setdefault("agent", _agent())
        rec["at"] = _at(rec["ts"])
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            _lock(f)
            f.write(json.dumps(rec, ensure_ascii=False) + NL)
        _trim()
    except Exception:
        pass


def _trim():
    try:
        if os.path.getsize(LOG_PATH) < 400000:
            return
        with open(LOG_PATH, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()
        if len(lines) > LOG_MAX:
            with open(LOG_PATH, "w", encoding="utf-8") as f:
                f.writelines(lines[-LOG_MAX:])
    except Exception:
        pass


def _load_fb():
    try:
        with open(FEEDBACK, "r", encoding="utf-8") as f:
            d = json.load(f)
        if not isinstance(d, dict):
            d = {}
    except Exception:
        d = {}
    if not isinstance(d.get("learned"), dict):
        d["learned"] = {}
    d.setdefault("version", 1)
    return d


def _save_fb(d):
    try:
        os.makedirs(SKILL_DIR, exist_ok=True)
        d["updated_at"] = _at()
        tmp = FEEDBACK + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            _lock(f)
            json.dump(d, f, ensure_ascii=False, indent=1)
        os.replace(tmp, FEEDBACK)
    except Exception:
        pass


def _bump(name, source="detour", n=1):
    d = _load_fb()
    e = d["learned"].get(name)
    if not isinstance(e, dict):
        e = {"count": 0}
    e["count"] = float(e.get("count") or 0) + n
    src = e.get("sources") if isinstance(e.get("sources"), dict) else {}
    src[source] = int(src.get(source) or 0) + n
    e["sources"] = src
    e["updated_at"] = _at()
    d["learned"][name] = e
    _save_fb(d)
    return e["count"]


def bump(name, source="seed", n=1):
    try:
        return _bump(str(name), source=source, n=n)
    except Exception:
        return 0


def hook(query, names=None, agent=None):
    """find() 每次呼叫都進來：有候選記 search、零候選記 miss。永不拋錯。"""
    try:
        names = [str(x) for x in (names or []) if x][:5]
        _append({"kind": "search" if names else "miss", "tool": "find",
                 "query": _clip(query, 200), "results": names,
                 "agent": agent or _agent()})
    except Exception:
        pass


def record_use(name, query="", agent=None):
    """skill view <name> 成功時呼叫：這是「終於找到」的訊號。"""
    try:
        if not name:
            return {"detour": False}
        agent = agent or _agent()
        now = time.time()
        tail = _read_tail()
        last_use = 0.0
        for r in tail:
            if r.get("agent") == agent and r.get("kind") == "use":
                last_use = max(last_use, float(r.get("ts") or 0))
        floor = max(now - WINDOW, last_use)
        steps = [r for r in tail
                 if r.get("agent") == agent and r.get("kind") in ("search", "miss")
                 and float(r.get("ts") or 0) >= floor]
        _append({"kind": "use", "name": str(name), "query": _clip(query, 200), "agent": agent})
        if len(steps) >= DETOUR_MIN:
            _bump(str(name), source="detour", n=1)
            _append({"kind": "detour", "agent": agent, "name": str(name),
                     "searched": len(steps),
                     "steps": ["%s「%s」" % (s.get("kind"), _clip(s.get("query"), 40)) for s in steps[-6:]]})
            return {"detour": True, "searched": len(steps), "name": name}
        return {"detour": False, "searched": len(steps), "name": name}
    except Exception:
        return {"detour": False}


def _no_trigger_skills(limit=25):
    out = []
    try:
        t = os.path.expanduser("~/.mok/tools")
        if t not in sys.path:
            sys.path.insert(0, t)
        import skill_index
        idx = skill_index.load_index(max_age=3600)
        for s in idx.get("skills", []):
            if s.get("stub"):
                continue
            if not s.get("triggers"):
                out.append((s.get("name") or "?",
                            _clip(s.get("description") or s.get("title") or "", 60)))
    except Exception:
        pass
    return out[:limit]


def analyze(days=7):
    since = time.time() - float(days) * 86400
    evs = [r for r in _read_tail(LOG_MAX) if float(r.get("ts") or 0) >= since]
    miss_cnt, det_cnt = {}, {}
    for r in evs:
        if r.get("kind") == "miss":
            k = _clip(r.get("query"), 60)
            miss_cnt[k] = miss_cnt.get(k, 0) + 1
        elif r.get("kind") == "detour":
            k = r.get("name") or "?"
            det_cnt[k] = det_cnt.get(k, 0) + 1
    each = lambda k: len([r for r in evs if r.get("kind") == k])
    return {"window_days": days, "search": each("search"), "miss": each("miss"),
            "use": each("use"), "detour": each("detour"),
            "top_miss": sorted(miss_cnt.items(), key=lambda x: -x[1])[:15],
            "top_detour": sorted(det_cnt.items(), key=lambda x: -x[1])[:10],
            "recent_detours": [r for r in evs if r.get("kind") == "detour"][-MAX_CASES:]}


def report(days=7, write=True):
    a = analyze(days)
    fb = _load_fb()
    L = ["# 技能/工具 繞路觀測報告（P4）", "",
         "- 產生時間：%s" % _at(),
         "- 統計窗：近 %s 天" % days,
         "- find 有候選：%d　find 查空：%d　真正用上：%d　繞路：%d"
         % (a["search"], a["miss"], a["use"], a["detour"]), "",
         "## 一、繞路案例（教材：同一需求檢索 >=%d 次才用上）" % DETOUR_MIN]
    if not a["recent_detours"]:
        L.append("（暫無 —— 代表 agent 一次就找到路）")
    for c in a["recent_detours"]:
        L.append("- [%s] %s → 最終使用 `%s`" % (c.get("agent"), " → ".join(c.get("steps") or []), c.get("name")))
    L += ["", "## 二、find 查空的高頻需求（→ 該補的觸發詞）"]
    if not a["top_miss"]:
        L.append("（暫無 miss 紀錄）")
    for q, n in a["top_miss"]:
        L.append("- x%d　`%s`" % (n, q))
    L += ["", "## 三、已學到的配對（find 下次會 boost）"]
    learned = sorted((fb.get("learned") or {}).items(),
                     key=lambda x: -float((x[1] or {}).get("count") or 0))
    if not learned:
        L.append("（尚無）")
    for n, e in learned[:20]:
        L.append("- `%s` ★%s（%s）" % (n, e.get("count"),
                                       ", ".join((e.get("sources") or {}).keys()) or "-"))
    L += ["", "## 四、完全沒有 triggers 的技能（最該補 frontmatter 的一群）"]
    nt = _no_trigger_skills()
    if not nt:
        L.append("（全部技能都有 triggers）")
    for n, d in nt:
        L.append("- `%s` — %s" % (n, d))
    L += ["", "> 補完觸發詞後重建索引：python3 ~/.mok/tools/skill_index.py build"]
    text = NL.join(L)
    if write:
        try:
            with open(REPORT_PATH, "w", encoding="utf-8") as f:
                _lock(f)
                f.write(text + NL)
        except Exception:
            pass
    return text


def main(argv):
    cmd = (argv[1] if len(argv) > 1 else "report").strip()
    days = int(argv[2]) if len(argv) > 2 and argv[2].isdigit() else 7
    if cmd == "report":
        print(report(days))
    elif cmd == "analyze":
        print(json.dumps(analyze(days), ensure_ascii=False, indent=1))
    elif cmd == "bump":
        print(bump(argv[2], argv[3] if len(argv) > 3 else "seed"))
    elif cmd == "use":
        print(json.dumps(record_use(argv[2] if len(argv) > 2 else "",
                                    argv[3] if len(argv) > 3 else ""), ensure_ascii=False))
    elif cmd == "hook":
        hook(argv[2] if len(argv) > 2 else "",
             (argv[3].split(",") if len(argv) > 3 and argv[3] else []))
        print("logged")
    elif cmd == "log":
        n = int(argv[2]) if len(argv) > 2 and argv[2].isdigit() else 20
        for r in _read_tail(n)[-n:]:
            print(json.dumps(r, ensure_ascii=False))
    elif cmd == "path":
        print(LOG_PATH)
        print(FEEDBACK)
        print(REPORT_PATH)
    else:
        print(__doc__)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
