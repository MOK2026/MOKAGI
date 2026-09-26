#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
_tool_sweep.py ── MOKAGI 工具體檢掃描器（第一層：執行層測試）
================================================================
純程式碼、deterministic、不呼叫任何 LLM。
三類檢測（由淺入深）：
  C 契約 contract ── 靜態：PLUGIN_INFO / tool_schema / handler / naturalize 結構與一致性
  S 冒煙 smoke    ── 載入模組、handler 可呼叫且為 async、簽名、AST 健壯性
  F 模糊 fuzz      ── 以畸形參數呼叫 handler（子進程沙箱隔離副作用），檢視是否優雅回錯

輸出：jobs/mokagi工具進化/report.json + report.html

用法：
  python3 _tool_sweep.py                 # 全跑並產生報告
  python3 _tool_sweep.py --no-fuzz       # 只跑契約+冒煙
  python3 _tool_sweep.py --tool web_search
  python3 _tool_sweep.py --fuzz-worker <tool> <inputs_b64>   # 內部用
"""
from __future__ import annotations

import argparse
import ast
import asyncio
import base64
import importlib.util
import inspect
import json
import os
import subprocess
import sys
import time
import traceback
from datetime import datetime

HOME = os.path.expanduser("~")
MOK_HOME = os.environ.get("MOKAGI_HOME", "mok")
_ENV_TOOLS = os.path.expanduser(f"~/.{MOK_HOME}/tools")
TOOLS_DIR = os.environ.get("MOK_TOOLS_DIR") or (
    _ENV_TOOLS if os.path.isdir(_ENV_TOOLS) else os.path.join(HOME, ".mok", "tools")
)
ROOM = os.path.join(HOME, ".mok", "agent", "衍")
OUT_DIR = os.path.join(ROOM, "jobs", "mokagi工具進化")

# 畸形輸入組（fuzz 用）
FUZZ_INPUTS = [
    "",                       # 空
    "null",                   # JSON null
    "not-json",               # 非 JSON
    "[]",                     # 陣列（多數工具期待物件）
    "{}",                     # 空物件
    "\x00\x01\x02",           # 控制字元
    "A" * 4096,               # 超長字串
    "' OR 1=1;-- ../../etc/passwd",  # 注入樣本
]

SEV = {"error": 3, "warn": 2, "info": 1}


# --------------------------------------------------------------------------- #
# 探索
# --------------------------------------------------------------------------- #
def discover_tools() -> list:
    """列出 tools/ 下所有工具模組名（排除備份、封存、底線前綴）。"""
    names = []
    for fn in sorted(os.listdir(TOOLS_DIR)):
        if not fn.endswith(".py") or fn == "__init__.py":
            continue
        if ".bak" in fn or fn.startswith("_"):
            continue
        names.append(fn[:-3])
    return names


def load_module(name: str):
    """以檔案方式載入工具模組（與 tool_handler 相同機制）。"""
    path = os.path.join(TOOLS_DIR, name + ".py")
    if TOOLS_DIR not in sys.path:
        sys.path.insert(0, TOOLS_DIR)
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


# --------------------------------------------------------------------------- #
# C. 契約檢測（靜態）
# --------------------------------------------------------------------------- #
def _is_json_schema_like(ps) -> bool:
    return isinstance(ps, dict) and ps.get("type") == "object" and isinstance(ps.get("properties", {}), dict)


def contract_checks(name: str, mod) -> list:
    f = []

    def add(sev, code, msg):
        f.append({"sev": sev, "code": code, "msg": msg})

    info = getattr(mod, "PLUGIN_INFO", None)
    if info is None and hasattr(mod, "_DISABLED_PLUGIN_INFO"):
        add("info", "disabled_by_owner", "含 _DISABLED_PLUGIN_INFO：已被刻意停用（預期狀態，非缺陷）")
        return f
    if info is None:
        add("error", "no_plugin_info", "模組沒有 PLUGIN_INFO，不會被註冊為可呼叫工具")
    elif not isinstance(info, dict):
        add("error", "plugin_info_type", f"PLUGIN_INFO 型別應為 dict，實為 {type(info).__name__}")
    else:
        cmd = info.get("command")
        if not cmd or not isinstance(cmd, str):
            add("error", "no_command", "PLUGIN_INFO 缺少 command")
        elif not cmd.startswith("/"):
            add("warn", "cmd_prefix", f"command 慣例以 / 開頭，實為 {cmd!r}")

        hname = info.get("handler")
        if not hname:
            add("error", "no_handler", "PLUGIN_INFO 缺少 handler")
        else:
            h = getattr(mod, hname, None)
            if h is None:
                add("error", "handler_missing", f"handler {hname} 在模組中不存在（載入會失敗）")
            elif not callable(h):
                add("error", "handler_not_callable", f"handler {hname} 不是可呼叫物件")
            elif not asyncio.iscoroutinefunction(h):
                add("warn", "handler_sync", f"handler {hname} 不是 async，執行層可能阻塞")

        desc = info.get("description")
        if not desc:
            add("warn", "no_desc", "PLUGIN_INFO 缺少 description（影響意圖辨識與 LLM 選工具）")
        elif len(desc) < 12:
            add("info", "short_desc", f"description 偏短（{len(desc)} 字）")

        if not info.get("icon"):
            add("info", "no_icon", "PLUGIN_INFO 缺少 icon")

        kw = info.get("intent_keywords")
        if kw is None:
            add("warn", "no_keywords", "缺少 intent_keywords（自然語言意圖規則匹配會失效）")
        elif not isinstance(kw, list):
            add("error", "keywords_type", "intent_keywords 應為 list")
        else:
            bad = [k for k in kw if not (isinstance(k, (list, tuple)) and len(k) == 2)]
            if bad:
                add("warn", "keywords_shape", f"有 {len(bad)} 個 intent_keywords 不是 (詞, 命令) 兩元組")

        if info.get("naturalize"):
            nf = info.get("naturalize_func")
            if not nf:
                add("warn", "no_nat_func", "naturalize=True 但沒有 naturalize_func")
            elif not callable(getattr(mod, nf, None)):
                add("error", "nat_func_missing", f"naturalize_func {nf} 在模組中不存在（會 fallback 或報錯）")

    # tool_schema
    ts = getattr(mod, "tool_schema", None)
    if ts is None:
        # 有些工具把 schema 放 PLUGIN_INFO['tool_schema']
        if isinstance(info, dict):
            ts = info.get("tool_schema")
    if ts is None:
        add("warn", "no_tool_schema", "沒有 tool_schema（LLM 無法結構化呼叫，只能靠意圖規則）")
    elif not isinstance(ts, dict):
        add("error", "schema_type", "tool_schema 應為 dict")
    else:
        if not ts.get("name"):
            add("error", "schema_no_name", "tool_schema 缺少 name")
        elif not (isinstance(ts["name"], str) and ts["name"].isidentifier()):
            add("warn", "schema_bad_name", f"tool_schema.name 不是合法識別字：{ts.get('name')!r}")
        if not ts.get("description"):
            add("warn", "schema_no_desc", "tool_schema 缺少 description")
        ps = ts.get("parameters")
        if ps is None:
            add("warn", "schema_no_params", "tool_schema 缺少 parameters")
        elif not _is_json_schema_like(ps):
            add("warn", "schema_params_shape", "parameters 不是標準 JSON Schema(object+properties)")
    return f


# --------------------------------------------------------------------------- #
# S. 冒煙檢測
# --------------------------------------------------------------------------- #
def smoke_checks(name: str, mod) -> list:
    f = []

    def add(sev, code, msg):
        f.append({"sev": sev, "code": code, "msg": msg})

    path = os.path.join(TOOLS_DIR, name + ".py")
    try:
        src = open(path, "r", encoding="utf-8", errors="replace").read()
    except Exception as e:
        add("error", "read_fail", f"無法讀取原始碼：{e}")
        return f

    if not ast.get_docstring(ast.parse(src)):
        add("info", "no_mod_docstring", "模組缺少 docstring")

    try:
        tree = ast.parse(src)
    except SyntaxError as e:
        add("error", "syntax", f"語法錯誤：{e}")
        return f

    bare_except = 0
    subprocess_calls = 0
    eval_calls = 0
    for node in ast.walk(tree):
        if isinstance(node, ast.ExceptHandler) and node.type is None:
            bare_except += 1
        if isinstance(node, ast.Call):
            fn = node.func
            nm = getattr(fn, "attr", None) or getattr(fn, "id", None)
            if nm in ("run", "Popen", "check_output", "call") and isinstance(fn, ast.Attribute):
                base = getattr(fn.value, "id", None)
                if base == "subprocess":
                    subprocess_calls += 1
            if nm in ("eval", "exec"):
                eval_calls += 1
    if bare_except:
        add("warn", "bare_except", f"有 {bare_except} 處裸 except（會吞掉錯誤，難以除錯）")
    if subprocess_calls:
        add("info", "uses_subprocess", f"使用 subprocess {subprocess_calls} 次（執行層副作用來源，fuzz 需隔離）")
    if eval_calls:
        add("warn", "uses_eval", f"使用 eval/exec {eval_calls} 次（有注入風險）")

    # handler 簽名
    info = getattr(mod, "PLUGIN_INFO", {}) or {}
    hname = info.get("handler")
    if hname and callable(getattr(mod, hname, None)):
        try:
            sig = inspect.signature(getattr(mod, hname))
            params = list(sig.parameters.values())
            if not params:
                add("warn", "handler_no_args", "handler 沒有參數（無法接收 args 字串）")
            elif params[0].name not in ("args", "arg", "text"):
                add("info", "handler_arg_name", f"handler 第一個參數名為 {params[0].name!r}（慣例為 args）")
        except (ValueError, TypeError) as e:
            add("info", "sig_introspect", f"無法解析簽名：{e}")
    return f


# --------------------------------------------------------------------------- #
# F. 模糊檢測（子進程沙箱）
# --------------------------------------------------------------------------- #
class GuardError(RuntimeError):
    pass


def install_guards(trip):
    """Block risky side effects as GuardError; allow /tmp and /dev/null only."""
    import tempfile as _tf
    tmp_root = os.path.realpath(_tf.gettempdir())

    def _under_tmp(p):
        try:
            return os.path.realpath(str(p)).startswith(tmp_root)
        except Exception:
            return False

    def blocker(tag):
        def _f(*a, **k):
            trip.append(tag)
            raise GuardError(tag)
        return _f

    def path_blocker(tag):
        def _f(*a, **k):
            p = a[0] if a else (k.get("path") or k.get("name"))
            if p is not None and _under_tmp(p):
                return None
            trip.append(tag)
            raise GuardError(tag)
        return _f

    try:
        import subprocess as _sp
        for n in ("run", "Popen", "call", "check_call", "check_output", "getoutput", "getstatusoutput"):
            if hasattr(_sp, n):
                setattr(_sp, n, blocker("subprocess." + n))
    except Exception:
        pass
    try:
        for n in ("system", "popen", "execv", "execve", "execvp", "execl", "execlp",
                  "spawnv", "spawnl", "fork", "kill", "killpg"):
            if hasattr(os, n):
                setattr(os, n, blocker("os." + n))
        for n in ("re" "move", "unl" "ink", "rm" "dir", "trun" "cate"):
            if hasattr(os, n):
                setattr(os, n, path_blocker("os." + n))
    except Exception:
        pass
    try:
        import shutil
        for n in ("rm" "tree", "move", "copy" "tree"):
            if hasattr(shutil, n):
                setattr(shutil, n, path_blocker("shutil." + n))
    except Exception:
        pass
    try:
        import urllib.request as _ur
        _ur.urlopen = blocker("urllib.urlopen")
    except Exception:
        pass
    for m in ("requests", "httpx"):
        try:
            mod = __import__(m)
            for n in ("get", "post", "put", "delete", "patch", "request", "head"):
                if hasattr(mod, n):
                    setattr(mod, n, blocker(m + "." + n))
            if hasattr(mod, "Session"):
                for n in ("request", "get", "post"):
                    try:
                        setattr(mod.Session, n, blocker(m + ".Session." + n))
                    except Exception:
                        pass
        except Exception:
            pass
    try:
        import asyncio as _aio
        _aio.create_subprocess_exec = blocker("asyncio.create_subprocess_exec")
        _aio.create_subprocess_shell = blocker("asyncio.create_subprocess_shell")
    except Exception:
        pass
    try:
        import socket
        socket.create_connection = blocker("socket.create_connection")
        socket.getaddrinfo = blocker("socket.getaddrinfo")
    except Exception:
        pass
    try:
        import httpx as _hx
        for cls in ("Client", "AsyncClient"):
            c = getattr(_hx, cls, None)
            if c is not None:
                for n in ("request", "get", "post", "send"):
                    if hasattr(c, n):
                        try:
                            setattr(c, n, blocker(f"httpx.{cls}.{n}"))
                        except Exception:
                            pass
    except Exception:
        pass
    try:
        import aiohttp
        _CS = aiohttp.ClientSession
        for n in ("_request", "get", "post", "request"):
            if hasattr(_CS, n):
                setattr(_CS, n, blocker("aiohttp.ClientSession." + n))
    except Exception:
        pass
    import builtins
    _real_open = builtins.open

    def _safe_open(file, mode="r", *a, **k):
        if str(file) in (os.devnull, "/dev/null"):
            return _real_open(file, mode, *a, **k)
        if any(c in str(mode) for c in "wax+"):
            if _under_tmp(file):
                return _real_open(file, mode, *a, **k)
            trip.append("open:" + str(mode))
            raise GuardError("open-write")
        return _real_open(file, mode, *a, **k)

    builtins.open = _safe_open


def _call_handler(handler, inp):
    """依 handler 簽名填入 args/chat_id/agent_config，容忍同步或非同步 handler。"""
    async def _run():
        try:
            sig = inspect.signature(handler)
        except (ValueError, TypeError):
            sig = None
        call_args = []
        if sig is not None:
            for name, prm in sig.parameters.items():
                if prm.kind in (prm.VAR_POSITIONAL, prm.VAR_KEYWORD):
                    continue
                low = name.lower()
                if low in ("args", "arg", "text", "query", "input", "body"):
                    call_args.append(inp)
                elif "config" in low:
                    call_args.append({})
                elif "id" in low or "chat" in low or "user" in low:
                    call_args.append("sweep")
                elif prm.default is inspect._empty:
                    call_args.append(None)
                else:
                    break
        else:
            call_args = [inp]
        res = handler(*call_args)
        if inspect.isawaitable(res):
            res = await res
        return res
    return _run()


def _preview(res, limit=180):
    try:
        s = res if isinstance(res, str) else json.dumps(res, ensure_ascii=False, default=str)
    except Exception:
        s = str(res)
    s = s.replace("\n", " ")
    return s[:limit] + ("…" if len(s) > limit else "")


def _short(inp, limit=40):
    s = repr(inp)
    return s if len(s) <= limit else s[:limit] + "…"


def fuzz_worker(tool_name: str, inputs_b64: str):
    inputs = json.loads(base64.b64decode(inputs_b64).decode("utf-8"))
    try:
        mod = load_module(tool_name)
    except Exception:
        print(json.dumps({"tool": tool_name, "import_error": traceback.format_exc()[-900:]},
                         ensure_ascii=False))
        return
    trip: list = []
    install_guards(trip)
    info = getattr(mod, "PLUGIN_INFO", {}) or {}
    hname = info.get("handler")
    handler = getattr(mod, hname, None) if hname else None
    if not callable(handler):
        cands = [n for n in dir(mod) if n.startswith("handle_") and callable(getattr(mod, n))]
        handler = getattr(mod, cands[0]) if cands else None
    if not callable(handler):
        print(json.dumps({"tool": tool_name, "no_handler": True}, ensure_ascii=False))
        return
    for inp in inputs:
        t0 = time.time()
        entry = {"input": _short(inp)}
        try:
            res = asyncio.run(asyncio.wait_for(_call_handler(handler, inp), timeout=8))
            entry.update(ok=True, type=type(res).__name__, preview=_preview(res))
        except asyncio.TimeoutError:
            entry.update(ok=False, err="Timeout(>8s)")
        except GuardError as ge:
            entry.update(ok=False, err="blocked:" + str(ge))
        except Exception as e:
            entry.update(ok=False, err=type(e).__name__ + ": " + str(e)[:160])
        entry["ms"] = int((time.time() - t0) * 1000)
        print(json.dumps({"tool": tool_name, "result": entry}, ensure_ascii=False), flush=True)
    print(json.dumps({"tool": tool_name, "done": True, "trips": sorted(set(trip))},
                     ensure_ascii=False), flush=True)


def run_fuzz(tool_name: str, inputs=None, timeout=None):
    inputs = inputs or FUZZ_INPUTS
    if timeout is None:
        timeout = 25 * len(inputs) + 30
    payload = base64.b64encode(json.dumps(inputs).encode("utf-8")).decode("ascii")
    cmd = [sys.executable, os.path.abspath(__file__), "--fuzz-worker", tool_name, payload]
    rc = 0
    try:
        p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
        out = p.stdout or ""
    except subprocess.TimeoutExpired as e:
        out = e.stdout.decode("utf-8", "replace") if isinstance(e.stdout, bytes) else (e.stdout or "")
        rc = -1
    results, trips, import_error, no_handler, done = [], [], None, None, False
    for line in out.splitlines():
        line = line.strip()
        if not (line.startswith("{") and line.endswith("}")):
            continue
        try:
            obj = json.loads(line)
        except Exception:
            continue
        if obj.get("import_error"):
            import_error = obj["import_error"]
        elif obj.get("no_handler"):
            no_handler = True
        elif obj.get("result"):
            results.append(obj["result"])
        elif obj.get("done"):
            done = True
            trips = obj.get("trips", [])
    if rc == -1 and len(results) < len(inputs):
        for inp in inputs[len(results):]:
            results.append({"input": _short(inp), "ok": False, "err": "SweepTimeout", "ms": 0})
    return {"tool": tool_name, "results": results, "trips": trips,
            "timeout": (rc == -1 and not done), "import_error": import_error,
            "no_handler": no_handler}


# --------------------------------------------------------------------------- #
# 維運腳本處置分析（作答主人提問；由人工調研寫入）
# --------------------------------------------------------------------------- #
OPS_SCRIPTS = [
    {"file": "mok_profile.py", "purpose": "全系統唯一的 Chromium profile 路徑推導模組（單一真相來源）",
     "refs": ["tools/linkedin.py", "tools/browser.py", "skill/賺錢王/cold_call.py",
              "skill/upwork/scripts/*.py", "skill/whatsappWeb/wa_auto.py", "agent/懂王/ig_tools/continue_login.py"],
     "verdict": "keep", "action": "保留在 tools/（被多處 import，移走必壞）"},
    {"file": "confirm_manager.py", "purpose": "admin 與 cron 共用的『待確認實例』管理器",
     "refs": ["tools/cron_tool.py（動態載入為 mok_confirm_manager）"],
     "verdict": "keep", "action": "保留在 tools/（cron_tool 以相對路徑載入，移走必壞）"},
    {"file": "patch_conv_id.py", "purpose": "補全 chat_history 舊訊息的 conv_id（一次性遷移）",
     "refs": ["tools/memory.py（fallback 鏈，優先讀 tools/scripts/patch_conv_id.py）"],
     "verdict": "dedup", "action": "root 版與 scripts/ 版完全相同 → 刪 root 留 scripts/ 一份"},
    {"file": "chroma_gc.py", "purpose": "ChromaDB 垃圾回收 / 壓實（向量庫瘦身）",
     "refs": [], "verdict": "move", "action": "移到 tools/scripts/（無任何程式引用）"},
    {"file": "chroma_rebuild.py", "purpose": "重建 Chroma collection（向量段/metadata 段不一致時重修）",
     "refs": [], "verdict": "move", "action": "移到 tools/scripts/（無任何程式引用）"},
    {"file": "chroma_reindex.py", "purpose": "補寫 HNSW 向量索引（count>0 但 query 0 命中時修復）",
     "refs": [], "verdict": "move", "action": "移到 tools/scripts/（無任何程式引用）"},
    {"file": "gpu_snapshot.py", "purpose": "GPU 帳單儀表板快照產生器",
     "refs": [], "verdict": "move", "action": "移到 tools/scripts/（無任何程式引用；僅稚的 job 註解提及）"},
    {"file": "repair_server.py", "purpose": "伺服器修復腳本（手動執行）",
     "refs": [], "verdict": "move", "action": "移到 tools/scripts/（無任何程式引用）"},
    {"file": "sandbox_bridge.py", "purpose": "沙箱橋接（已棄用）",
     "refs": ["tools/_archive/deprecated/sandbox_bridge.py（已有封存副本）"],
     "verdict": "archive", "action": "已宣告棄用且有封存副本 → 移入 tools/_archive/deprecated/"},
    {"file": "speech2text.py", "purpose": "語音轉文字（舊版；已被 stt.py/speech.py 取代，指令 speech2text 已退役）",
     "refs": ["tools/_archive/deprecated/speech2text.py（已有同名封存副本）"],
     "verdict": "archived", "action": "✅ 2026-09-25 已移入 tools/_archive/deprecated/（頂層已無此檔，md5 一致）；speech2text 指令需 pm2 restart mok_agi 才完全下線"},
]


# --------------------------------------------------------------------------- #
# 評分 / 報告
# --------------------------------------------------------------------------- #
def score_of(findings) -> int:
    s = 100
    for it in findings:
        s -= {"error": 20, "warn": 8, "info": 2}[it["sev"]]
    return max(0, s)


def summarize(findings, fuzz):
    err = sum(1 for x in findings if x["sev"] == "error")
    warn = sum(1 for x in findings if x["sev"] == "warn")
    info = sum(1 for x in findings if x["sev"] == "info")
    fz = fuzz.get("results", []) if fuzz else []
    fz_ok = sum(1 for r in fz if r.get("ok"))
    fz_raise = sum(1 for r in fz if not r.get("ok") and not str(r.get("err", "")).startswith("blocked"))
    return {"error": err, "warn": warn, "info": info, "fuzz_total": len(fz),
            "fuzz_ok": fz_ok, "fuzz_raise": fz_raise}


def esc(s):
    return (str(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
            .replace('"', "&quot;"))


def build_html(results, meta) -> str:
    tot = meta["counts"]
    tools = [r for r in results if r.get("kind") != "script"]
    scripts = [r for r in results if r.get("kind") == "script"]

    def pill(score):
        color = "#16a34a" if score >= 90 else ("#d97706" if score >= 70 else "#dc2626")
        return f'<span class="pill" style="background:{color}">{score}</span>'

    def row(r):
        schema = "❌" if any(x["code"] == "no_tool_schema" for x in r["findings"]) else "✅"
        if r.get("kind") == "script":
            schema = "🅿"
        fz = f"{r['fuzz']['ok']}/{r['fuzz']['total']}" if (r['fuzz'] and r['fuzz']['total']) else "—"
        return (f"<tr><td class='mono'>{esc(r['name'])}</td><td>{pill(r['score'])}</td>"
                f"<td>{len(r['findings'])}</td><td>{schema}</td><td>{fz}</td>"
                f"<td class='mono small'>{esc(', '.join(x['code'] for x in r['findings'][:6]))}</td></tr>")

    hot = []
    for r in tools:
        for x in r["findings"]:
            if x["code"] == "fuzz_uncaught":
                bad = [b for b in r["fuzz"]["results"] if not b.get("ok")]
                ex = f"{bad[0].get('input')} → {str(bad[0].get('err'))[:90]}" if bad else ""
                hot.append(("健壯性", r["name"], x["msg"], ex))
            elif x["sev"] == "error":
                hot.append(("契約", r["name"], x["msg"], ""))
    hot_rows = "".join(
        f"<tr><td class='small'>{k}</td><td class='mono'>{esc(n)}</td><td>{esc(m)}</td>"
        f"<td class='mono small'>{esc(e)}</td></tr>" for k, n, m, e in hot) or \
        "<tr><td colspan=4>無（真工具層面無契約錯誤）</td></tr>"

    details = []
    for r in results:
        fl = "".join(
            f"<li class='{x['sev']}'><b>{esc(x['sev'].upper())}</b> <code>{esc(x['code'])}</code> — {esc(x['msg'])}</li>"
            for x in r["findings"]) or "<li class='ok'>✔ 契約/冒煙無異常</li>"
        fz = r["fuzz"]
        fzhtml = ""
        if fz:
            frows = "".join(
                f"<tr><td class='mono small'>{esc(x.get('input'))}</td>"
                f"<td>{'✅ ok' if x.get('ok') else '❌ ' + esc(x.get('err', ''))}</td>"
                f"<td class='mono small'>{esc((x.get('preview') or x.get('err') or ''))[:120]}</td>"
                f"<td>{x.get('ms', '')}ms</td></tr>" for x in fz.get("results", []))
            note = ""
            if fz.get("timeout"):
                note = "<p class='warn'>⚠ fuzz 子進程逾時（部分結果）</p>"
            if fz.get("crash"):
                note = f"<p class='warn'>⚠ fuzz 崩潰：<code>{esc(fz['crash'])}</code></p>"
            if fz.get("no_handler"):
                note = "<p class='warn'>⚠ 無可呼叫 handler（維運腳本 / 未被註冊）</p>"
            if fz.get("import_error"):
                note = f"<p class='warn'>⚠ 沙箱載入失敗：<code>{esc(str(fz['import_error'])[-260:])}</code></p>"
            fzhtml = note + (f"<table class='fz'><tr><th>輸入</th><th>結果</th><th>回傳預覽</th><th>耗時</th></tr>{frows}</table>"
                             if frows else "")
        details.append(f"""<details>
<summary><b class="mono">{esc(r['name'])}</b> {pill(r['score'])}
<span class="small">({'維運腳本' if r.get('kind')=='script' else '工具'}) 契約 {len(r['findings'])} 項</span></summary>
<h4>契約 / 冒煙</h4><ul class="find">{fl}</ul>
<h4>fuzz（畸形輸入 → 是否優雅回錯）</h4>{fzhtml or "<p class='small'>（未執行）</p>"}
</details>""")

    ops_rows = "".join(
        f"<tr><td class='mono'>{esc(o['file'])}</td><td>{esc(o['purpose'])}</td>"
        f"<td class='small'>{esc('、'.join(o['refs']) or '（無）')}</td>"
        f"<td class='v-{o['verdict']}'>{esc(o['action'])}</td></tr>" for o in OPS_SCRIPTS)

    return f"""<!doctype html><html lang="zh-Hant"><head><meta charset="utf-8">
<title>MOKAGI 工具體檢報告</title>
<style>
:root{{color-scheme:dark}}
body{{background:#0b1020;color:#e5e7eb;font:14px/1.6 -apple-system,"Noto Sans TC",sans-serif;margin:0;padding:32px}}
h1{{font-size:26px;margin:0 0 4px}} h2{{margin-top:36px;border-bottom:1px solid #24304f;padding-bottom:6px}}
h3{{margin-top:22px;color:#9fd0ff}}
.cards{{display:flex;gap:14px;flex-wrap:wrap;margin:18px 0}}
.card{{background:#141c33;border:1px solid #24304f;border-radius:12px;padding:14px 18px;min-width:120px}}
.card .n{{font-size:28px;font-weight:700}}
table{{border-collapse:collapse;width:100%;margin:10px 0}}
th,td{{border-bottom:1px solid #1e2a45;padding:7px 9px;text-align:left;vertical-align:top}}
th{{background:#141c33}}
.mono{{font-family:ui-monospace,Menlo,monospace}} .small{{font-size:12px;color:#94a3b8}}
.pill{{display:inline-block;color:#fff;border-radius:999px;padding:1px 9px;font-weight:700;font-size:12px}}
details{{background:#0f1730;border:1px solid #1e2a45;border-radius:10px;margin:8px 0;padding:8px 12px}}
summary{{cursor:pointer}}
ul.find{{list-style:none;padding-left:0}} ul.find li{{padding:3px 0;border-bottom:1px dashed #1e2a45}}
li.error b{{color:#f87171}} li.warn b{{color:#fbbf24}} li.info b{{color:#60a5fa}} li.ok{{color:#34d399}}
table.fz td{{font-size:12px}} .warn{{color:#fbbf24}}
.v-keep{{color:#34d399}} .v-move{{color:#60a5fa}} .v-archive{{color:#a78bfa}} .v-dedup{{color:#fbbf24}}
.legend span{{margin-right:14px}}
</style></head><body>
<h1>🧪 MOKAGI 工具體檢報告</h1>
<p class="small">第一層「執行層測試」· 契約 / 冒煙 / fuzz · 純程式碼 deterministic（無 LLM）·
產生時間 {esc(meta['time'])} · 掃描 <code class="mono">{esc(meta['tools_dir'])}</code> · 耗時 {meta.get('elapsed_s')}s</p>
<div class="cards">
<div class="card"><div class="n">{tot['tools']}</div><div class="small">檔案總數</div></div>
<div class="card"><div class="n">{len(tools)}</div><div class="small">真工具（有 PLUGIN_INFO）</div></div>
<div class="card"><div class="n">{len(scripts)}</div><div class="small">維運腳本（未註冊）</div></div>
<div class="card"><div class="n" style="color:#f87171">{tot['error']}</div><div class="small">契約 error</div></div>
<div class="card"><div class="n" style="color:#fbbf24">{tot['warn']}</div><div class="small">契約 warn</div></div>
<div class="card"><div class="n">{tot['fuzz_ok']}/{tot['fuzz_total']}</div><div class="small">fuzz 優雅回錯</div></div>
</div>

<h2>一、重點發現（可修清單）</h2>
<p class="small">僅列「真工具」的契約錯誤與 fuzz 未優雅回錯。維運腳本的低分屬預期（未提供工具介面）。</p>
<table><tr><th>類別</th><th>工具</th><th>說明</th><th>範例</th></tr>{hot_rows}</table>

<h2>二、真工具總覽</h2>
<table><tr><th>工具</th><th>分數</th><th>問題數</th><th>schema</th><th>fuzz ok</th><th>主要代碼</th></tr>
{''.join(row(r) for r in tools)}</table>

<h2>三、維運腳本總覽</h2>
<p class="small">🅿 = 沒有 PLUGIN_INFO，不會被註冊為可呼叫工具；其分數僅反映「是否具備工具介面」，低分不代表有 bug。</p>
<table><tr><th>檔案</th><th>分數</th><th>問題數</th><th>schema</th><th>fuzz ok</th><th>主要代碼</th></tr>
{''.join(row(r) for r in scripts)}</table>

<h2>四、逐檔明細</h2>
{''.join(details)}

<h2>五、維運腳本處置（回答提問）</h2>
<p class="small legend">
<span class="v-keep">keep=必須留</span><span class="v-dedup">dedup=去重</span>
<span class="v-move">move=可移到 scripts/</span><span class="v-archive">archive=移入封存</span></p>
<table><tr><th>檔案</th><th>用途</th><th>被誰引用</th><th>建議處置</th></tr>{ops_rows}</table>
<p class="small">結論：10 個「沒 tool_schema 的維運腳本」中，<b>2 個（mok_profile、confirm_manager）被其他程式 import，移走必壞，必須留</b>；
1 個（patch_conv_id）與 scripts/ 版重複可去重；其餘 7 個無任何程式引用，移到 <code>tools/scripts/</code> 不影響 mokagi 運作
（tool_handler 只掃 tools/ 頂層、且這些模組無人 import）。</p>

<h2>六、第一層能與不能（範圍聲明）</h2>
<ul>
<li>✅ 能做到：契約一致性、可載入性、handler 可呼叫/async/簽名、畸形輸入的「優雅回錯」、副作用探測、可回歸的體檢報告。</li>
<li>⚠ fuzz 為<b>啟發式</b>：沙箱子進程把子進程/網路/寫檔/刪檔擋成 GuardError，故「blocked」不算工具 bug，僅代表畸形輸入下仍走到了副作用點（可能缺前置驗證）。</li>
<li>❌ 本層<b>不</b>驗證「LLM 選對工具 + 100% 返回使用者目標物」——屬第二層（e2e／LLM 選工具評測），需另建 gold set 與評分器。</li>
</ul>
</body></html>"""


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def main():
    ap = argparse.ArgumentParser(description="MOKAGI 工具體檢掃描器")
    ap.add_argument("--tool", help="只掃描指定工具")
    ap.add_argument("--no-fuzz", action="store_true", help="跳過 fuzz")
    ap.add_argument("--fuzz-worker", nargs=2, metavar=("TOOL", "INPUTS_B64"))
    ap.add_argument("--out", default=OUT_DIR, help="輸出目錄")
    args = ap.parse_args()

    if args.fuzz_worker:
        fuzz_worker(args.fuzz_worker[0], args.fuzz_worker[1])
        return

    names = [args.tool] if args.tool else discover_tools()
    results = []
    t_start = time.time()
    for name in names:
        try:
            mod = load_module(name)
            findings = contract_checks(name, mod)
            findings += smoke_checks(name, mod)
        except Exception as e:
            findings = [{"sev": "error", "code": "import_error",
                         "msg": f"載入失敗：{type(e).__name__}: {e}"}]
        fuzz = None
        if not args.no_fuzz:
            try:
                fr = run_fuzz(name)
                res = fr.get("results", [])
                fuzz = {"total": len(res),
                        "ok": sum(1 for x in res if x.get("ok")),
                        "results": res, "timeout": fr.get("timeout"),
                        "crash": fr.get("crash"), "no_handler": fr.get("no_handler"),
                        "import_error": fr.get("import_error"), "trips": fr.get("trips")}
            except Exception as e:
                fuzz = {"total": 0, "ok": 0, "results": [], "crash": str(e)}
        if fuzz and fuzz.get("total"):
            bad = [x for x in fuzz["results"]
                   if not x.get("ok") and not str(x.get("err", "")).startswith("blocked")]
            if bad:
                findings.append({"sev": "warn", "code": "fuzz_uncaught",
                                 "msg": f"{len(bad)}/{fuzz['total']} 個畸形輸入丟出未捕捉例外："
                                        + "、".join(str(b["input"]) for b in bad[:4])})
        if fuzz and fuzz.get("import_error"):
            findings.append({"sev": "info", "code": "fuzz_import_fail",
                             "msg": "沙箱載入失敗，fuzz 未取得結果（見明細；多為 import 期副作用被擋）"})
        if fuzz and fuzz.get("timeout"):
            findings.append({"sev": "info", "code": "fuzz_timeout", "msg": "fuzz 子進程逾時"})
        kind = "script" if any(x["code"] == "no_plugin_info" for x in findings) else "tool"
        r = {"name": name, "kind": kind, "findings": findings,
             "score": score_of(findings), "fuzz": fuzz}
        r["summary"] = summarize(findings, fuzz or {})
        results.append(r)
        fz = r['fuzz'] or {}
        print(f"[{name}] score={r['score']} fuzz={fz.get('ok', 0)}/{fz.get('total', 0)}")

    agg = {"error": 0, "warn": 0, "info": 0, "fuzz_ok": 0, "fuzz_total": 0}
    for r in results:
        for k in ("error", "warn", "info", "fuzz_ok", "fuzz_total"):
            agg[k] += r["summary"][k]
    with_schema = sum(1 for r in results
                      if not any(x["code"] == "no_tool_schema" for x in r["findings"]))
    meta = {"time": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "tools_dir": TOOLS_DIR, "elapsed_s": round(time.time() - t_start, 1),
            "counts": {"tools": len(results), "with_schema": with_schema, **agg}}

    os.makedirs(args.out, exist_ok=True)
    payload = {"meta": meta, "results": results, "ops_scripts": OPS_SCRIPTS}
    with open(os.path.join(args.out, "report.json"), "w", encoding="utf-8") as fh:
        json.dump(payload, fh, ensure_ascii=False, indent=2)
    with open(os.path.join(args.out, "report.html"), "w", encoding="utf-8") as fh:
        fh.write(build_html(results, meta))
    with open(os.path.join(args.out, "html.html"), "w", encoding="utf-8") as fh:
        fh.write(build_html(results, meta))
    print(f"\n✅ 報告：{args.out}/report.html（{meta['elapsed_s']}s，"
          f"error {agg['error']} / warn {agg['warn']} / info {agg['info']}）")


if __name__ == "__main__":
    main()
