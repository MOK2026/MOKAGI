#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""cost_guard.py — MOK 成本速率護欄（預設 shadow：只記帳、只告警、不真停）

設計原則（對齊 MOKAGI 設計守則）：
- 只依賴標準庫，可獨立載入、獨立測試；不 import core 其他模組。
- 預設 shadow 模式：只把每次 LLM 用量的原始欄位 append 進 jsonl，絕不改任何行為、不擋任何回合。
- fail-open：任何例外都吞掉，絕不影響主流程。
- 不寫死 agent：門檻與模式皆可由共用設定檔／環境變數覆寫。
- 多進程安全：只做 O_APPEND 單行寫入（<4KB 原子）。

兩層分工：
  1) 本模組 note_usage()：掛在 log_token_usage() 尾端，只記原始用量（極輕）。
  2) cost_guard_watch.py（cron 每分鐘）：讀 jsonl、算滑動窗金額、越線才告警。

設定檔：~/.mok/.cost_guard（KEY=VALUE，可省略）；環境變數優先。
  MOK_cost_mode          shadow（預設）| on | off
  MOK_cost_min_rmb       1 分鐘窗門檻（預設 10 元 RMB）
  MOK_cost_hour_rmb      1 小時窗門檻（預設 100 元）
  MOK_cost_day_rmb       1 天窗門檻（預設 300 元）
  MOK_cost_price_factor  單價係數（預設 1.0；限時折扣時可調，如 0.25）
  MOK_cost_exempt_agents 逗號分隔；這些 agent 豁免（不告警）
"""
import os
import sys
import json
import time
import datetime

MOK = os.path.join(os.path.expanduser("~"), "." + "mok")
_MEM = os.path.join(MOK, ".memory")
USAGE_LOG = os.path.join(_MEM, "cost_guard_usage.jsonl")
ALERT_LOG = os.path.join(_MEM, "cost_guard_alerts.jsonl")
CONF_FILE = os.path.join(MOK, ".cost_guard")

MODE_SHADOW = "shadow"
MODE_ON = "on"
MODE_OFF = "off"

# 官方單價（人民幣 / 百萬 token）＝**高峰時段**價；空閒時段＝半價。
# 來源：api-docs.deepseek.com/zh-cn/quick_start/pricing（2026-10-08 抄錄）
DEFAULT_PRICES = {
    "deepseek-flash":               {"hit": 0.04, "miss": 2.0,  "out": 8.0},
    "deepseek-v4-flash":            {"hit": 0.04, "miss": 2.0,  "out": 8.0},
    "deepseek-v4-flash-vision-exp": {"hit": 0.04, "miss": 2.0,  "out": 8.0},
    "deepseek-v4-pro":              {"hit": 0.30, "miss": 9.0,  "out": 27.0},
    "deepseek-reasoner":            {"hit": 1.0,  "miss": 4.0,  "out": 16.0},
    "deepseek-chat":                {"hit": 0.5,  "miss": 2.0,  "out": 8.0},
}
OFF_PEAK_FACTOR = 0.5

# 高峰：北京時間 週一~週五 09:00-12:00 與 14:00-18:00（不含國定假日，假日不另判）
PEAK_WINDOWS = ((9, 12), (14, 18))


def _read_conf_file(path=None):
    """讀 KEY=VALUE 設定檔；找不到或壞掉回空 dict（fail-open）。"""
    path = path or CONF_FILE
    out = {}
    try:
        with open(path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    except Exception:
        pass
    return out


def conf(key, default=None):
    """環境變數優先，其次設定檔，最後 default。"""
    v = os.environ.get(key)
    if v is None or str(v).strip() == "":
        v = _read_conf_file().get(key, default)
    return default if (v is None or str(v).strip() == "") else str(v).strip()


def mode():
    m = (conf("MOK_cost_mode", MODE_SHADOW) or MODE_SHADOW).lower()
    return m if m in (MODE_SHADOW, MODE_ON, MODE_OFF) else MODE_SHADOW


def thresholds():
    def _f(k, d):
        try:
            return float(conf(k, d))
        except Exception:
            return float(d)
    return {"min": _f("MOK_cost_min_rmb", 10.0),
            "hour": _f("MOK_cost_hour_rmb", 100.0),
            "day": _f("MOK_cost_day_rmb", 300.0)}


def price_factor():
    try:
        return float(conf("MOK_cost_price_factor", 1.0))
    except Exception:
        return 1.0


def exempt_agents():
    raw = conf("MOK_cost_exempt_agents", "") or ""
    return {a.strip() for a in raw.split(",") if a.strip()}


def beijing_dt(ts=None):
    ts = time.time() if ts is None else float(ts)
    return datetime.datetime.utcfromtimestamp(ts + 8 * 3600)


def is_peak(ts=None):
    """北京時間是否為高峰時段（週一~週五 09-12 / 14-18）。"""
    try:
        b = beijing_dt(ts)
    except Exception:
        return True
    if b.weekday() >= 5:
        return False
    for h0, h1 in PEAK_WINDOWS:
        if h0 <= b.hour < h1:
            return True
    return False


def price_for(model_name):
    m = (model_name or "").strip()
    d = DEFAULT_PRICES.get(m)
    if d is None:
        for key, val in DEFAULT_PRICES.items():
            if m.startswith(key) or key.startswith(m):
                d = val
                break
    return dict(d) if d else {"hit": 0.0, "miss": 0.0, "out": 0.0}


def estimate_rmb(model_name, prompt_tokens, completion_tokens,
                 hit_tokens=None, miss_tokens=None, ts=None):
    """估算單次呼叫成本（人民幣）。資訊不足時保守全算 cache miss。"""
    try:
        p = max(0, int(prompt_tokens or 0))
        c = max(0, int(completion_tokens or 0))
        pr = price_for(model_name)
        f = OFF_PEAK_FACTOR if not is_peak(ts) else 1.0
        if hit_tokens is None and miss_tokens is None:
            hit, miss = 0, p
        else:
            hit = max(0, int(hit_tokens or 0))
            miss = p - hit if miss_tokens is None else max(0, int(miss_tokens or 0))
        cost = (hit * pr["hit"] + miss * pr["miss"] + c * pr["out"]) / 1_000_000.0
        return round(cost * f * price_factor(), 8)
    except Exception:
        return 0.0


def note_usage(agent_name, model_name, prompt_tokens, completion_tokens,
               extra=None, user_id=None, ts=None):
    """掛在 log_token_usage() 尾端：只 append 一行原始用量。永不拋例外。"""
    try:
        if mode() == MODE_OFF:
            return
        extra = extra if isinstance(extra, dict) else {}
        p = int(prompt_tokens or 0)
        c = int(completion_tokens or 0)
        hit = extra.get("cached_tokens")
        miss = extra.get("cache_miss_tokens")
        rec = {
            "ts": float(ts if ts is not None else time.time()),
            "agent": str(agent_name or "unknown"),
            "model": str(model_name or ""),
            "p": p, "c": c,
            "hit": int(hit) if hit is not None else None,
            "miss": int(miss) if miss is not None else None,
            "purpose": str(extra.get("purpose", ""))[:40],
            "user": str(user_id or "")[:64],
        }
        os.makedirs(_MEM, exist_ok=True)
        line = json.dumps(rec, ensure_ascii=False) + "\n"
        fd = os.open(USAGE_LOG, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o644)
        try:
            os.write(fd, line.encode("utf-8"))
        finally:
            os.close(fd)
    except Exception:
        pass


def read_all(limit_bytes=None):
    """讀全部用量事件（供巡檢、離線分析）。壞行略過。"""
    out = []
    try:
        with open(USAGE_LOG, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    out.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        pass
    return out


def window_costs(events, now=None):
    """把事件依 agent 彙總成三窗金額（RMB）。"""
    now = time.time() if now is None else float(now)
    wins = thresholds()
    res = {}
    ex = exempt_agents()
    for ev in events:
        try:
            agent = ev.get("agent") or "unknown"
            if agent in ex:
                continue
            age = now - float(ev.get("ts", 0) or 0)
            if age < 0:
                continue
            cost = estimate_rmb(ev.get("model"), ev.get("p"), ev.get("c"),
                                ev.get("hit"), ev.get("miss"), ev.get("ts"))
            d = res.setdefault(agent, {"min": 0.0, "hour": 0.0, "day": 0.0,
                                       "calls": 0, "cost_total": 0.0})
            d["cost_total"] += cost
            d["calls"] += 1
            if age <= 60:
                d["min"] += cost
            if age <= 3600:
                d["hour"] += cost
            if age <= 86400:
                d["day"] += cost
        except Exception:
            continue
    for d in res.values():
        for k in ("min", "hour", "day", "cost_total"):
            d[k] = round(d[k], 6)
    return res, wins




def read_db(since_ts=None):
    """從 token_usage 表讀權威用量（唯讀、fail-open）。供巡檢回溯與對帳。"""
    import sqlite3
    out = []
    try:
        db = os.path.join(_MEM, "chat_history.db")
        if not os.path.exists(db):
            return out
        conn = sqlite3.connect("file:" + db + "?mode=ro", uri=True, timeout=5)
        try:
            cur = conn.execute(
                "select agent_name, model_name, prompt_tokens, completion_tokens,"
                " timestamp, extra from token_usage where timestamp >= ? order by id",
                (float(since_ts or 0),))
            for agent, model, p, c, ts, extra in cur:
                hit = miss = None
                try:
                    ex = json.loads(extra) if extra else {}
                    hit = ex.get("cached_tokens")
                    miss = ex.get("cache_miss_tokens")
                except Exception:
                    pass
                out.append({"ts": float(ts or 0), "agent": str(agent or "unknown"),
                            "model": str(model or ""), "p": int(p or 0), "c": int(c or 0),
                            "hit": int(hit) if hit is not None else None,
                            "miss": int(miss) if miss is not None else None,
                            "purpose": "", "user": ""})
        finally:
            conn.close()
    except Exception:
        pass
    return out


def _self_test():
    print("mode =", mode(), "| thresholds =", thresholds(), "| factor =", price_factor())
    print("peak now =", is_peak(), "| exempt =", sorted(exempt_agents()))
    demo = estimate_rmb("deepseek-v4-flash", 43986, 1164, 42880, 1106)
    print("demo(43.9k p / 1.16k c, hit 42.8k) =", demo, "RMB")
    ev = read_all()
    res, wins = window_costs(ev)
    print("events =", len(ev), "| agents =", len(res))
    for a, d in sorted(res.items(), key=lambda x: -x[1]["day"])[:15]:
        print("  %-14s day=%8.3f hour=%7.3f min=%6.3f calls=%d" %
              (a, d["day"], d["hour"], d["min"], d["calls"]))


if __name__ == "__main__":
    _self_test()
