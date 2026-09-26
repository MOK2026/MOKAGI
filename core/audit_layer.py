# -*- coding: utf-8 -*-
# audit_layer.py — 中央動作審計與政策層（仿 OpenBot 的中央閘道）
# 2026-09-25，對應 OpenBot 借鏡點 1 / 2 / 4：
#   1. 事前決策 + 事後審計：工具呼叫前先寫 pending，執行後補結果、耗時、成敗。
#   2. initiator kind：標記動作來源 person / routine / handoff / unknown。
#   4. 政策用資料驅動表達式（CEL-like），可 fail-closed。
# 包裹式：不改動任何既有工具，只在統一入口外層加一道 audited_call()。
# 環境變數：
#   MOK_AUDIT_ENABLED 1/0（預設1）  MOK_AUDIT_ENFORCE 1/0（預設0） 
#   MOK_POLICY_FILE 政策檔  MOK_POLICY_FAILCLOSED 1/0（預設0）

import ast
import contextvars
import json
import os
import re
import sqlite3
import threading
import time
from datetime import datetime, timezone

MOK_DIR = os.path.expanduser(os.environ.get("MOK_DIR", "~/.mok"))
AUDIT_DB = os.path.join(MOK_DIR, ".memory", "action_audit.db")
POLICY_FILE = os.path.expanduser(os.environ.get("MOK_POLICY_FILE", "~/.mok/policy/action_policy.json"))


def _flag(name, default):
    v = os.environ.get(name)
    if v is None:
        return default
    return v.strip().lower() in ("1", "true", "yes", "on")


AUDIT_ENABLED = _flag("MOK_AUDIT_ENABLED", True)
AUDIT_ENFORCE = _flag("MOK_AUDIT_ENFORCE", False)
POLICY_FAILCLOSED = _flag("MOK_POLICY_FAILCLOSED", False)

_initiator_ctx = contextvars.ContextVar("mok_initiator", default=None)


def set_initiator(kind, detail=""):
    _initiator_ctx.set({"kind": str(kind), "detail": str(detail)})


def get_initiator():
    v = _initiator_ctx.get()
    if v:
        return v
    return {"kind": "person", "detail": "default"}


def set_initiator_if_unset(kind, detail=""):
    if _initiator_ctx.get() is None:
        _initiator_ctx.set({"kind": str(kind), "detail": str(detail)})


def infer_initiator(agent_config=None):
    cur = _initiator_ctx.get()
    if cur:
        return cur
    cfg = agent_config or {}
    for key in ("MOK_INITIATOR", "MOK_TRIGGER", "MOK_INITIATOR_KIND"):
        val = cfg.get(key)
        if val:
            return {"kind": str(val), "detail": "agent_config:" + key}
    return {"kind": "person", "detail": "inferred"}

_lock = threading.Lock()
_initialized = False


def _conn():
    d = os.path.dirname(AUDIT_DB)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)
    c = sqlite3.connect(AUDIT_DB, timeout=10.0)
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("PRAGMA busy_timeout=5000")
    return c


def init_db():
    global _initialized
    if _initialized:
        return
    with _lock:
        if _initialized:
            return
        with _conn() as c:
            c.execute("CREATE TABLE IF NOT EXISTS action_audit ("
                      "id INTEGER PRIMARY KEY AUTOINCREMENT,"
                      "ts REAL NOT NULL, iso TEXT NOT NULL,"
                      "agent TEXT, chat_id TEXT,"
                      "initiator TEXT, initiator_detail TEXT,"
                      "tool TEXT, args TEXT,"
                      "decision TEXT, policy_rule TEXT, policy_reason TEXT,"
                      "elapsed_ms REAL, success INTEGER,"
                      "result TEXT, error TEXT)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_audit_ts ON action_audit (ts)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_audit_agent ON action_audit (agent)")
            c.execute("CREATE INDEX IF NOT EXISTS idx_audit_initiator ON action_audit (initiator)")
        _initialized = True


def _short(x, n=1000):
    if x is None:
        return None
    s = x if isinstance(x, str) else json.dumps(x, ensure_ascii=False, default=str)
    return s[:n]


def log_start(tool, args, chat_id, agent, initiator, decision, rule, reason):
    if not AUDIT_ENABLED:
        return 0
    try:
        init_db()
        now = time.time()
        iso = datetime.now(timezone.utc).astimezone().isoformat()
        with _conn() as c:
            cur = c.execute(
                "INSERT INTO action_audit (ts, iso, agent, chat_id, initiator, initiator_detail,"
                " tool, args, decision, policy_rule, policy_reason) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (now, iso, agent, str(chat_id), initiator.get("kind"), initiator.get("detail"),
                 tool, _short(args), decision, rule, reason))
            return cur.lastrowid
    except Exception:
        return 0


def log_end(row_id, success, result=None, error=None, elapsed_ms=None):
    if not AUDIT_ENABLED or not row_id:
        return
    try:
        with _conn() as c:
            c.execute("UPDATE action_audit SET success=?, result=?, error=?, elapsed_ms=? WHERE id=?",
                      (1 if success else 0, _short(result), _short(error), elapsed_ms, row_id))
    except Exception:
        pass

_ALLOWED_FUNCS = {
    "startsWith": lambda s, p: str(s).startswith(str(p)),
    "endsWith": lambda s, p: str(s).endswith(str(p)),
    "contains": lambda s, p: str(p) in str(s),
    "matches": lambda s, p: re.search(str(p), str(s)) is not None,
    "lower": lambda s: str(s).lower(),
    "upper": lambda s: str(s).upper(),
    "len": lambda s: len(s),
}
_CMP = {
    ast.Eq: lambda a, b: a == b,
    ast.NotEq: lambda a, b: a != b,
    ast.In: lambda a, b: a in b,
    ast.NotIn: lambda a, b: a not in b,
    ast.Lt: lambda a, b: a < b,
    ast.LtE: lambda a, b: a <= b,
    ast.Gt: lambda a, b: a > b,
    ast.GtE: lambda a, b: a >= b,
}


def _ev(node, ctx):
    if isinstance(node, ast.Expression):
        return _ev(node.body, ctx)
    if isinstance(node, ast.BoolOp):
        vals = [_ev(v, ctx) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _ev(node.operand, ctx)
    if isinstance(node, ast.Compare):
        left = _ev(node.left, ctx)
        for op, comp in zip(node.ops, node.comparators):
            right = _ev(comp, ctx)
            fn = _CMP.get(type(op))
            if fn is None or not fn(left, right):
                return False
            left = right
        return True
    if isinstance(node, ast.Name):
        return ctx.get(node.id)
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.List):
        return [_ev(e, ctx) for e in node.elts]
    if isinstance(node, ast.Tuple):
        return tuple(_ev(e, ctx) for e in node.elts)
    if isinstance(node, ast.Attribute):
        base = _ev(node.value, ctx)
        if isinstance(base, dict):
            return base.get(node.attr)
        return getattr(base, node.attr, None)
    if isinstance(node, ast.Subscript):
        base = _ev(node.value, ctx)
        key = _ev(node.slice, ctx)
        try:
            return base[key]
        except Exception:
            return None
    if isinstance(node, ast.Call):
        if isinstance(node.func, ast.Name) and node.func.id in _ALLOWED_FUNCS:
            try:
                return _ALLOWED_FUNCS[node.func.id](*[_ev(a, ctx) for a in node.args])
            except Exception:
                return None
        return None
    return None


_policy_cache = {"mtime": None, "rules": [], "default": "allow"}


def load_policy():
    try:
        m = os.path.getmtime(POLICY_FILE)
    except OSError:
        return {"rules": [], "default": ("deny" if POLICY_FAILCLOSED else "allow")}
    if _policy_cache.get("mtime") == m:
        return _policy_cache
    try:
        with open(POLICY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        _policy_cache.update({
            "mtime": m,
            "rules": data.get("rules", []),
            "default": data.get("default", ("deny" if POLICY_FAILCLOSED else "allow")),
        })
    except Exception:
        _policy_cache.update({"mtime": m, "rules": [], "default": "allow"})
    return _policy_cache


def evaluate_policy(tool, args, agent, initiator, chat_id="web"):
    pol = load_policy()
    ctx = {
        "tool": str(tool).lstrip("/"),
        "tool_raw": tool,
        "args": args if isinstance(args, dict) else {},
        "agent": agent,
        "initiator": initiator.get("kind") if isinstance(initiator, dict) else str(initiator),
        "initiator_detail": initiator.get("detail") if isinstance(initiator, dict) else "",
        "chat_id": str(chat_id),
    }
    for rule in pol.get("rules", []):
        expr = rule.get("expr", "")
        expr = expr.replace("&&", " and ").replace("||", " or ")
        expr = re.sub(r"!(?!=)", " not ", expr)
        try:
            hit = bool(_ev(ast.parse(expr, mode="eval"), ctx))
        except Exception:
            hit = False
        if hit:
            decision = rule.get("action", "deny")
            return decision, rule.get("id", "?"), rule.get("reason", "")
    default = pol.get("default", "allow")
    if POLICY_FAILCLOSED and default == "allow":
        default = "deny"
    return default, "default", ""


async def audited_call(tool, args, chat_id, agent_config, run):
    cfg = agent_config or {}
    agent = cfg.get("MOK_AGENT_NAME") or cfg.get("agent_name") or "unknown"
    initiator = infer_initiator(cfg)
    decision, rule, reason = evaluate_policy(tool, args, agent, initiator, chat_id)

    if decision == "deny" and AUDIT_ENFORCE:
        row = log_start(tool, args, chat_id, agent, initiator, "deny", rule, reason)
        msg = "BLOCKED by policy (rule " + str(rule) + ")"
        if reason:
            msg += ": " + str(reason)
        log_end(row, False, None, "blocked by policy: " + str(reason or rule), 0.0)
        return json.dumps({"success": False, "error": msg}, ensure_ascii=False)

    row = log_start(tool, args, chat_id, agent, initiator, decision, rule, reason)
    t0 = time.time()
    try:
        result = await run()
        elapsed = (time.time() - t0) * 1000.0
        log_end(row, True, result, None, elapsed)
        return result
    except Exception as e:
        elapsed = (time.time() - t0) * 1000.0
        log_end(row, False, None, type(e).__name__ + ": " + str(e), elapsed)
        raise


def recent(limit=20, agent=None, initiator=None, tool=None, decision=None):
    init_db()
    q = ("SELECT id, iso, initiator, agent, tool, decision, success, elapsed_ms, substr(args,1,90)"
         " FROM action_audit WHERE 1=1")
    p = []
    if agent:
        q += " AND agent=?"; p.append(agent)
    if initiator:
        q += " AND initiator=?"; p.append(initiator)
    if tool:
        q += " AND tool=?"; p.append(tool)
    if decision:
        q += " AND decision=?"; p.append(decision)
    q += " ORDER BY id DESC LIMIT ?"; p.append(int(limit))
    with _conn() as c:
        return c.execute(q, p).fetchall()


def stats(days=1):
    init_db()
    since = time.time() - days * 86400
    with _conn() as c:
        total = c.execute("SELECT COUNT(*) FROM action_audit WHERE ts>=?", (since,)).fetchone()[0]
        by_ini = c.execute("SELECT initiator, COUNT(*) FROM action_audit WHERE ts>=? GROUP BY initiator", (since,)).fetchall()
        denied = c.execute("SELECT COUNT(*) FROM action_audit WHERE ts>=? AND decision=?", (since, "deny")).fetchone()[0]
        fails = c.execute("SELECT COUNT(*) FROM action_audit WHERE ts>=? AND success=0", (since,)).fetchone()[0]
    return {"total": total, "by_initiator": by_ini, "denied": denied, "failed": fails}


def format_recent(limit=20, **kw):
    rows = recent(limit=limit, **kw)
    if not rows:
        return "(no audit records yet)"
    lines = []
    for r in rows:
        rid, iso, ini, agent, tool, decision, success, elapsed, args = r
        mark = "RUN" if success is None else ("OK " if success else "ERR")
        if decision == "deny":
            mark = "BLK"
        t = iso[11:19] if iso and len(iso) >= 19 else str(iso)
        el = int(elapsed) if elapsed is not None else 0
        lines.append("#%-5s %s [%s] %-8s %-12s %-8s %sms  %s" % (rid, t, ini, str(agent)[:8], str(tool)[:12], mark, el, args))
    return chr(10).join(lines)


def ensure_policy_file():
    d = os.path.dirname(POLICY_FILE)
    if d and not os.path.exists(d):
        os.makedirs(d, exist_ok=True)
    if not os.path.exists(POLICY_FILE):
        q = chr(34)
        demo_expr = ("initiator == " + q + "routine" + q + " && tool == " + q + "admin" + q
                     + " && args.args != None && contains(args.args, " + q + "purge-all" + q + ")")
        demo = {"default": "allow", "rules": [
            {"id": "example_routine_cannot_purge", "expr": demo_expr, "action": "deny",
             "reason": "排程觸發時禁止破壞性操作（示範規則，可刪）"}]}
        with open(POLICY_FILE, "w", encoding="utf-8") as f:
            json.dump(demo, f, ensure_ascii=False, indent=2)
    return POLICY_FILE


if __name__ == "__main__":
    import sys
    ensure_policy_file()
    if len(sys.argv) > 1 and sys.argv[1] == "stats":
        print(json.dumps(stats(1), ensure_ascii=False, indent=2))
    elif len(sys.argv) > 1 and sys.argv[1] == "recent":
        print(format_recent(30))
    else:
        print("policy:", POLICY_FILE)
        print("auditdb:", AUDIT_DB)
        print("enabled=%s enforce=%s failclosed=%s" % (AUDIT_ENABLED, AUDIT_ENFORCE, POLICY_FAILCLOSED))
