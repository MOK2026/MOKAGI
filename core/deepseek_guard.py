#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""deepseek_guard — DeepSeek 餘額熔斷守衛（P0）

建立：2026-10-08 稚
目的：餘額過低時（1）推播 Telegram 通知主人、（2）寫下低餘額旗標，
      供工具層派發入口的熔斷閘暫停「非必要」高耗工具。

設計原則：
  - 獨立腳本，不被主迴圈載入 → 由 cron 每次執行最新版，改完即生效、免重啟。
  - 任何異常一律吞掉、fail-open：讀不到餘額或狀態壞掉時，絕不誤擋。
  - 只查餘額、寫旗標、發通知；不改任何既有設定與資料。

CLI：check（cron 用）／status／clear。
"""

import os
import sys
import json
import time
import glob
import urllib.request

HOME_DIR = os.path.expanduser("~")
MOK = os.path.join(HOME_DIR, ".mok")
STATE = os.path.join(MOK, "core", ".ds_balance_state.json")
FLAG = os.path.join(MOK, ".memory", ".ds_balance_low.json")

THRESHOLD = float(os.environ.get("MOK_DS_BALANCE_THRESHOLD") or 10.0)
COOLDOWN = 6 * 3600
FLAG_MAX_AGE = 1800


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


def _load(path):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(path, obj):
    try:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(obj, f, ensure_ascii=False)
    except Exception:
        pass


def _find_api_key():
    """找一個可用的 DeepSeek token：env → 稚設定 → 任一 agent 設定（指向 deepseek 者）。"""
    for env_key in ("MOK_DS_API_KEY", "DEEPSEEK_API_KEY"):
        v = os.environ.get(env_key)
        if v:
            return v
    cands = []
    try:
        cands.append(os.path.join(MOK, "agent", "稚", ".稚"))
        cands.extend(sorted(glob.glob(os.path.join(MOK, "agent", "*", ".*"))))
    except Exception:
        pass
    for path in cands:
        if not os.path.isfile(path):
            continue
        cfg = _read_env(path)
        for k, v in cfg.items():
            if k.startswith("MOK_MODEL_url") and "deepseek.com" in (v or ""):
                idx = k[len("MOK_MODEL_url"):]
                tok = cfg.get("MOK_MODEL_token" + idx, "")
                if tok:
                    return tok
    return ""


def _base_url():
    for env_key in ("MOK_DS_API_BASE", "DEEPSEEK_API_BASE"):
        v = os.environ.get(env_key)
        if v:
            return v
    return "https://api.deepseek.com"


def query_balance(key=None):
    """回傳 (balance, currency)；失敗回 (None, None)。"""
    key = key or _find_api_key()
    if not key:
        return None, None
    try:
        req = urllib.request.Request(
            _base_url().rstrip("/") + "/user/balance",
            headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=20) as r:
            d = json.load(r)
        infos = d.get("balance_infos") or []
        # 【2026-10-08 修正】DeepSeek /user/balance 會同時回多筆幣別（CNY＋USD）且順序不固定，
        # 舊版只取 infos[0] 會不定時挑到帳面 -0.00 的 USD 筆，造成餘額明明充足卻誤觸熔斷
        # （實測 cron log：CNY 26.29 與 USD -0.0 交錯出現）。改為優先取 CNY 主帳。
        normalized = []
        for _it in infos:
            _cur = (_it.get("currency") or "").upper() or "CNY"
            try:
                _bal = float(_it.get("total_balance"))
            except (TypeError, ValueError):
                continue
            normalized.append((_bal, _cur))
        if normalized:
            for _bal, _cur in normalized:
                if _cur == "CNY":
                    return _bal, _cur
            for _bal, _cur in normalized:
                if _bal > 0:
                    return _bal, _cur
            return normalized[0]
    except Exception:
        return None, None
    return None, None


def _tg_notify(text):
    cfg = _read_env(os.path.join(MOK, "agent", "稚", ".稚"))
    tok = cfg.get("MOK_TG_TOKEN")
    chat = cfg.get("ADMIN_CHAT_ID") or cfg.get("MOK_TG_CHAT_ID")
    if not tok or not chat:
        return False
    try:
        body = json.dumps({"chat_id": chat, "text": text,
                           "disable_web_page_preview": True}).encode("utf-8")
        req = urllib.request.Request(
            "https://api.telegram.org/bot" + tok + "/sendMessage",
            data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def is_low_balance(max_age=FLAG_MAX_AGE):
    """給熔斷閘用：讀旗標檔（fail-open：任何異常、或旗標過期，一律回 False＝照常放行）。"""
    try:
        d = _load(FLAG)
        ts = float(d.get("ts") or 0)
        if max_age and (time.time() - ts) > max_age:
            return False
        return bool(d.get("low", True))
    except Exception:
        return False


def tool_blocked(tool_name):
    """低餘額時，該工具是否屬「非必要高耗」而被熔斷（fail-open）。"""
    try:
        if not is_low_balance():
            return False
        blocked = {t.strip() for t in os.environ.get(
            "MOK_DS_BLOCKED_TOOLS", "gui_agent,money,money_video,comic").split(",") if t.strip()}
        return tool_name in blocked
    except Exception:
        return False


def check(notify=True, now=None):
    now = float(now or time.time())
    key = _find_api_key()
    if not key:
        return {"ok": False, "reason": "no_key"}
    bal, cur = query_balance(key)
    if bal is None:
        return {"ok": False, "reason": "no_balance"}
    st = _load(STATE) or {}
    if bal < THRESHOLD:
        _save(FLAG, {"ts": now, "balance": bal, "currency": cur,
                     "threshold": THRESHOLD, "low": True})
        alerted = False
        if notify and (now - float(st.get("last_alert_ts") or 0.0) >= COOLDOWN):
            msg = ("\u26a0\ufe0f DeepSeek \u9918\u984d\u4e0d\u8db3\uff0c\u5df2\u555f\u52d5\u7194\u65b7\n"
                   "\u76ee\u524d\u9918\u984d = %s %.2f\uff08\u544a\u8b66\u9580\u6abb %.2f\uff09\n"
                   "\u5df2\u66ab\u505c\u975e\u5fc5\u8981\u9ad8\u8017\u5de5\u5177\uff08GUI \u81ea\u52d5\u5316\uff0f\u77ed\u5f71\u97f3\uff0f\u6f2b\u5287\uff09\uff0c\u8acb\u76e1\u5feb\u88dc\u503c\u3002"
                   % (cur, bal, THRESHOLD))
            alerted = _tg_notify(msg)
            if alerted:
                st["last_alert_ts"] = now
        st["low"] = True
        st["balance"] = bal
        _save(STATE, st)
        return {"ok": True, "low": True, "balance": bal, "currency": cur, "alerted": alerted}
    if os.path.exists(FLAG):
        try:
            os.remove(FLAG)
        except Exception:
            pass
    st["low"] = False
    st["balance"] = bal
    _save(STATE, st)
    return {"ok": True, "low": False, "balance": bal, "currency": cur}


def _main(argv):
    cmd = (argv[1] if len(argv) > 1 else "check").strip().lower()
    if cmd == "check":
        print(json.dumps(check(notify=True), ensure_ascii=False))
    elif cmd == "status":
        r = check(notify=False)
        r["flag_exists"] = os.path.exists(FLAG)
        r["threshold"] = THRESHOLD
        print(json.dumps(r, ensure_ascii=False))
    elif cmd == "clear":
        try:
            if os.path.exists(FLAG):
                os.remove(FLAG)
        except Exception:
            pass
        print("cleared")
    else:
        print("usage: check|status|clear")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
