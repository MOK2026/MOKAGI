# -*- coding: utf-8 -*-
"""context_guard.py — 上下文護欄（P0-1 工具輸出截斷 / P0-2 單回合 token 預算）

設計原則（對齊 MOKAGI 設計守則）：
- 只依賴標準庫（mok_token 可選），可獨立載入與測試。
- 由 agent_config 控制；預設「乾跑（dry）」——只記錄、不改任何內容、不中斷任務。
- 不寫死任何 agent；所有門檻皆可由設定覆寫。
- 記錄檔 append-only，落於 ~/.mok/.memory/context_guard.jsonl（多 agent 共用）。

可設定（~/.mok/.<agent> 或 ~/.mok/.mokagi）：
  MOK_ctx_mode         dry（預設）| on | off
  MOK_ctx_tool_limit   工具輸出字元上限，預設 8000
  MOK_ctx_tool_head    截斷時保留頭部字元，預設 3000
  MOK_ctx_tool_tail    截斷時保留尾部字元，預設 2000
  MOK_ctx_turn_budget  單回合 token 預算，預設 1500000（<=0 關閉）
  MOK_ctx_exempt_tools 逗號分隔；額外豁免（永不截斷）的工具名
  MOK_ctx_dump_dir     完整原文落地目錄，預設 ~/.mok/.ctx_dumps

事件記錄（每行一筆 JSON）：
  ts, event, mode, action, agent, user, tool, raw_chars, est_tokens, limit, iteration, dump
"""
import os
import json
import time

MODE_DRY = "dry"
MODE_ON = "on"
MODE_OFF = "off"

DEFAULT_TOOL_LIMIT = 8000
DEFAULT_TOOL_HEAD = 3000
DEFAULT_TOOL_TAIL = 2000
DEFAULT_TURN_BUDGET = 1500000

# 預設豁免（已知小輸出工具；即使超標亦不需截斷處理，避免無謂掃描）
DEFAULT_EXEMPT = (
    "tts", "admin_mode", "admin_set_model", "admin_ollama_rm",
    "linkedin", "instagram", "facebook", "cron", "memory", "task", "job",
)

LOG_PATH = os.path.expanduser("~/.mok/.memory/context_guard.jsonl")

_TOKEN_FN = None


def _rough_tokens(text):
    """無 tiktoken 時的粗略估算：ASCII 約 4 字元/token，非 ASCII（中日韓）約 1.5 字元/token。"""
    if not text:
        return 0
    try:
        non_ascii = len(text) - len(text.encode("ascii", "ignore"))
    except Exception:
        non_ascii = 0
    ascii_n = len(text) - non_ascii
    return int(ascii_n / 4.0 + non_ascii / 1.5)


def _cfg_get(cfg, key, default=None):
    try:
        v = (cfg or {}).get(key)
        return default if v is None else v
    except Exception:
        return default


def mode(cfg):
    m = str(_cfg_get(cfg, "MOK_ctx_mode", MODE_DRY)).strip().lower()
    return m if m in (MODE_DRY, MODE_ON, MODE_OFF) else MODE_DRY


def is_enabled(cfg):
    return mode(cfg) != MODE_OFF


def is_dryrun(cfg):
    return mode(cfg) != MODE_ON


def _int(cfg, key, default):
    try:
        v = int(float(str(_cfg_get(cfg, key, default)).strip()))
        return v if v > 0 else default
    except Exception:
        return default


def tool_limit(cfg):
    return _int(cfg, "MOK_ctx_tool_limit", DEFAULT_TOOL_LIMIT)


def tool_head(cfg):
    return _int(cfg, "MOK_ctx_tool_head", DEFAULT_TOOL_HEAD)


def tool_tail(cfg):
    return _int(cfg, "MOK_ctx_tool_tail", DEFAULT_TOOL_TAIL)


def turn_budget(cfg):
    try:
        v = int(float(str(_cfg_get(cfg, "MOK_ctx_turn_budget", DEFAULT_TURN_BUDGET)).strip()))
        return v if v > 0 else 0
    except Exception:
        return DEFAULT_TURN_BUDGET


def exempt_tools(cfg):
    raw = str(_cfg_get(cfg, "MOK_ctx_exempt_tools", "") or "")
    extra = [t.strip() for t in raw.split(",") if t.strip()]
    return set(DEFAULT_EXEMPT) | set(extra)


def dump_dir(cfg):
    return str(_cfg_get(cfg, "MOK_ctx_dump_dir", "") or os.path.expanduser("~/.mok/.ctx_dumps"))


def _record(event):
    try:
        os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
        with open(LOG_PATH, "a", encoding="utf-8") as f:
            f.write(json.dumps(event, ensure_ascii=False) + "\n")
    except Exception:
        pass


def _token_counter():
    global _TOKEN_FN
    if _TOKEN_FN is not None:
        return _TOKEN_FN
    fn = None
    try:
        import importlib.util as _ilu
        p = os.path.join(os.path.expanduser("~"), ".mok", "core", "mok_token.py")
        if os.path.exists(p):
            spec = _ilu.spec_from_file_location("_cg_mok_token", p)
            mod = _ilu.module_from_spec(spec)
            spec.loader.exec_module(mod)
            if hasattr(mod, "count_tokens"):
                fn = mod.count_tokens
    except Exception:
        fn = None
    if fn is None:
        fn = _rough_tokens
    _TOKEN_FN = fn
    return fn


def estimate_tokens(text):
    if not text:
        return 0
    try:
        return int(_token_counter()(text))
    except Exception:
        return len(text) // 4


def estimate_messages_tokens(messages):
    total = 0
    try:
        for m in (messages or []):
            if not isinstance(m, dict):
                continue
            c = m.get("content")
            if isinstance(c, str) and c:
                total += estimate_tokens(c)
            for tc in (m.get("tool_calls") or []):
                try:
                    total += estimate_tokens(
                        json.dumps(tc.get("function", {}).get("arguments", ""), ensure_ascii=False))
                except Exception:
                    pass
    except Exception:
        pass
    return total


def process_tool_output(text, tool_name, cfg, user_id=None, agent_name=None, iteration=None):
    """工具輸出進上下文前的護欄。回傳 (text, info)；info=None 表未觸發。

    - off：原樣回傳
    - dry：原樣回傳，僅記錄 would_truncate
    - on ：超過上限且非豁免 → 留頭尾 + 顯眼標記（並落地全文），回傳截斷後文字
    """
    try:
        if not is_enabled(cfg) or not isinstance(text, str):
            return text, None
        if str(tool_name) in exempt_tools(cfg):
            return text, None
        limit = tool_limit(cfg)
        n = len(text)
        if n <= limit:
            return text, None
        info = {
            "event": "tool_output_over_limit",
            "mode": mode(cfg),
            "agent": agent_name,
            "user": str(user_id) if user_id is not None else None,
            "tool": str(tool_name),
            "raw_chars": n,
            "est_tokens": estimate_tokens(text),
            "limit": limit,
            "iteration": iteration,
        }
        if is_dryrun(cfg):
            info["action"] = "would_truncate"
            _record(dict({"ts": time.time()}, **info))
            return text, info
        head = tool_head(cfg)
        tail = tool_tail(cfg)
        dump_path = None
        try:
            d = dump_dir(cfg)
            os.makedirs(d, exist_ok=True)
            safe_tool = "".join(ch for ch in str(tool_name) if ch.isalnum() or ch in "_-") or "tool"
            fn = "%s_%s_%s.txt" % (time.strftime("%Y%m%d-%H%M%S"), safe_tool, str(int(time.time() * 1000))[-6:])
            dump_path = os.path.join(d, fn)
            with open(dump_path, "w", encoding="utf-8") as f:
                f.write(text)
        except Exception:
            dump_path = None
        omitted = max(n - head - tail, 0)
        marker = ("\n\n…[⚠️ 已截斷 %d 字元，全文見 %s；需要細節請用 read_file / code_index get_chunk 分段讀取，勿一次塞入整份。]…\n\n"
                  % (omitted, dump_path or "(落地失敗)"))
        new_text = text[:head] + marker + text[-tail:]
        info["action"] = "truncated"
        info["dump"] = dump_path
        _record(dict({"ts": time.time()}, **info))
        return new_text, info
    except Exception:
        return text, None


def check_turn_budget(messages, cfg, user_id=None, agent_name=None, iteration=None):
    """每輪呼叫 LLM 前檢查單回合 token 預算。回傳 dict 或 None。

    action：ok / would_pause（乾跑，呼叫端不得中斷）/ pause（on，呼叫端應收尾）
    """
    try:
        if not is_enabled(cfg):
            return None
        budget = turn_budget(cfg)
        if budget <= 0:
            return None
        est = estimate_messages_tokens(messages)
        res = {"over": est > budget, "est_tokens": est, "budget": budget, "mode": mode(cfg)}
        if not res["over"]:
            res["action"] = "ok"
            return res
        ev = {
            "event": "turn_budget_exceeded",
            "mode": mode(cfg),
            "agent": agent_name,
            "user": str(user_id) if user_id is not None else None,
            "est_tokens": est,
            "limit": budget,
            "iteration": iteration,
        }
        if is_dryrun(cfg):
            res["action"] = "would_pause"
            _record(dict({"ts": time.time()}, **ev, action="would_pause"))
        else:
            res["action"] = "pause"
            _record(dict({"ts": time.time()}, **ev, action="pause"))
        return res
    except Exception:
        return None
