#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gpu_billing.py — 侍女 GPU 用量帳單（核心模組）
=================================================================
目的
    每位「侍女」(Agent) 各用一個獨立 SQLite 資料庫，記錄她租用 GPU 的
    每一次「開機 → 關機」明細：
        · 開機時間      boot_ts
        · 預計關機時間  planned_shutdown_ts（該侍女 vastai .env 的 DURATION_HOURS）
        · 實際關機時間  shutdown_ts
        · 單價          price_per_hour（USD/hr，vast.ai dph_total）
        · 費用明細      cost_usd =（關機-開機 小時數）× 單價

    資料庫位置（每侍女分開一個）：
        ~/.mok/agent/<侍女>/jobs/vastai/gpu_billing.db

資料來源
    1. vast.ai API  /instances/     （讀各侍女 .env 的 VAST_API_KEY，去重後查詢）
    2. 各侍女 jobs/vastai/instance.json（紀錄目前實例 id / ip / gpu / price）
    3. 各侍女 vastai .env 的 DURATION_HOURS（推算預計關機時間）

對外函式
    sync(now=None)                    同步所有侍女 → 更新各 DB，回傳摘要
    status_payload(sync_first=True)   產生前端 /api/gpu/status 用的完整資料
    db_path(maid) / discover_maids()

CLI
    python3 gpu_billing.py sync       # 單次同步（可放 cron 每分鐘）
    python3 gpu_billing.py status     # 印出 JSON（除錯用）
"""

import json
import os
import sqlite3
import sys
import time
import urllib.error
import urllib.request

# ------------------------------------------------------------------ 路徑設定
_HOME_CANDIDATES = [
    os.path.expanduser("~/.mok"),
    os.path.expanduser("~/.%s" % os.environ.get("MOKAGI_HOME", "mok")),
]
HOME = next((p for p in _HOME_CANDIDATES if os.path.isdir(p)), _HOME_CANDIDATES[0])
AGENT_ROOT = os.path.join(HOME, "agent")
SKILL_VASTAI = os.path.join(HOME, "skill", "vastai")
VAST_BASE = "https://console.vast.ai/api/v0"

RUNNING_STATES = {
    "running", "loading", "initializing", "offering", "created",
    "starting", "rebooting", "scheduling",
}
STOPPED_STATES = {
    "exited", "stopped", "destroyed", "error", "failed", "deleted",
    "offline", "unknown_destroyed",
}

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS sessions (
    instance_id         TEXT PRIMARY KEY,
    maid                TEXT,
    gpu                 TEXT,
    num_gpus            INTEGER DEFAULT 1,
    price_per_hour      REAL,
    boot_ts             REAL,
    planned_shutdown_ts REAL,
    shutdown_ts         REAL,
    status              TEXT,
    ip                  TEXT,
    ssh_port            INTEGER,
    machine_id          TEXT,
    label               TEXT,
    first_seen          REAL,
    last_seen           REAL,
    cost_usd            REAL DEFAULT 0,
    note                TEXT
);
CREATE TABLE IF NOT EXISTS events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    ts          REAL,
    instance_id TEXT,
    kind        TEXT,
    detail      TEXT
);
CREATE INDEX IF NOT EXISTS idx_sessions_boot   ON sessions(boot_ts);
CREATE INDEX IF NOT EXISTS idx_sessions_status ON sessions(status);
CREATE INDEX IF NOT EXISTS idx_events_inst     ON events(instance_id);
"""


# ------------------------------------------------------------------ 小工具
def _read_env(path):
    env = {}
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    except Exception:
        pass
    return env


def _load_json(path):
    try:
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f) or {}
    except Exception:
        pass
    return {}


def _num(v):
    try:
        if v is None or v == "":
            return None
        return float(v)
    except Exception:
        return None


def discover_maids():
    """回傳所有擁有 jobs/vastai 目錄的侍女名稱（= 會用到 GPU 的侍女）。"""
    maids = []
    try:
        for name in sorted(os.listdir(AGENT_ROOT)):
            d = os.path.join(AGENT_ROOT, name, "jobs", "vastai")
            if os.path.isdir(d):
                maids.append(name)
    except Exception:
        pass
    return maids


def maid_dir(maid):
    return os.path.join(AGENT_ROOT, maid, "jobs", "vastai")


def maid_env(maid):
    """侍女 vastai 設定 = 共用 skill 設定 + 侍女個人覆寫。"""
    env = _read_env(os.path.join(SKILL_VASTAI, ".env"))
    env.update(_read_env(os.path.join(maid_dir(maid), ".env")))
    return env


def maid_instance_file(maid):
    return os.path.join(maid_dir(maid), "instance.json")


def db_path(maid):
    """每位侍女一個獨立資料庫。"""
    return os.path.join(maid_dir(maid), "gpu_billing.db")


def _connect(maid):
    p = db_path(maid)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    conn = sqlite3.connect(p, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA_SQL)
    conn.commit()
    return conn


# ------------------------------------------------------------------ vast.ai API
def collect_api_keys():
    """收集所有侍女（含共用 skill）的 VAST_API_KEY，去重。"""
    keys, seen = [], set()
    cands = [os.path.join(SKILL_VASTAI, ".env")]
    for m in discover_maids():
        cands.append(os.path.join(maid_dir(m), ".env"))
    for p in cands:
        k = _read_env(p).get("VAST_API_KEY")
        if k and k not in seen:
            seen.add(k)
            keys.append(k)
    return keys


def _api_get(path, key, timeout=15):
    req = urllib.request.Request(
        VAST_BASE + path,
        headers={"Authorization": "Bearer " + key, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.load(r)


def fetch_instances(keys=None):
    """回傳 (instances_by_id, errors, ok_count)。"""
    keys = collect_api_keys() if keys is None else keys
    out, errors, ok = {}, [], 0
    for k in keys:
        try:
            data = _api_get("/instances/", k)
            ok += 1
            insts = data.get("instances", []) if isinstance(data, dict) else (data or [])
            for i in insts:
                if isinstance(i, dict) and i.get("id") is not None:
                    out[str(i["id"])] = i
        except urllib.error.HTTPError as e:
            errors.append("HTTP %s" % e.code)
        except Exception as e:
            errors.append(type(e).__name__ + ": " + str(e))
    return out, errors, ok


def fetch_account(keys=None):
    """回傳 vast 帳戶資訊（餘額 / credit / 累計消費）。"""
    keys = collect_api_keys() if keys is None else keys
    for k in keys:
        try:
            u = _api_get("/users/current/", k)
            return {
                "username": u.get("username"),
                "balance": u.get("balance"),
                "credit": u.get("credit"),
                "total_spend": u.get("total_spend"),
            }
        except Exception:
            continue
    return {}


# ------------------------------------------------------------------ 同步核心
def _extract(iid, info, inst_json, row, dur_h, now, api_ok=True):
    """從 vast API info / instance.json 抽出統一欄位。

    api_ok：本輪 vast API 是否成功取得實例清單。
        True  且查不到此 iid → 視為已銷毀（stopped），停止繼續累加費用。
        False（API 失敗）    → 保守沿用先前狀態，避免誤標。
    """
    in_json = str(inst_json.get("id")) == iid
    actual = str(info.get("actual_status") or info.get("cur_state") or "").lower()

    if info:
        status = "stopped" if actual in STOPPED_STATES else "running"
    elif not api_ok:
        # API 整體查詢失敗：沿用資料庫既有狀態（查無則依 instance.json 判斷）
        status = (row["status"] if (row and row["status"]) else ("running" if in_json else "stopped"))
    else:
        # API 正常運作卻查不到此實例 → 已被 vast 銷毀/不存在，標記停止
        status = "stopped"

    gpu = info.get("gpu_name") or (inst_json.get("gpu") if in_json else None) or (row["gpu"] if row else None)
    price = _num(info.get("dph_total")) or _num(info.get("price")) \
        or (_num(inst_json.get("price")) if in_json else None) \
        or (row["price_per_hour"] if row else None)
    num_gpus = int(_num(info.get("num_gpus")) or (row["num_gpus"] if row and row["num_gpus"] else 1))

    boot = (_num(info.get("start_date")) or _num(info.get("created_at"))
            or (_num(inst_json.get("created")) if in_json else None)
            or (row["boot_ts"] if row else None) or now)

    planned = boot + dur_h * 3600.0 if dur_h else (row["planned_shutdown_ts"] if row else None)

    return {
        "status": status,
        "gpu": gpu,
        "price": price,
        "num_gpus": num_gpus,
        "boot_ts": boot,
        "planned_shutdown_ts": planned,
        "ip": info.get("public_ipaddr") or info.get("ssh_host") or (inst_json.get("ip") if in_json else None),
        "ssh_port": info.get("ssh_port") or (inst_json.get("ssh_port") if in_json else None),
        "machine_id": info.get("machine_id"),
        "label": info.get("label"),
    }


def sync(now=None):
    now = float(now or time.time())
    maids = discover_maids()
    api_insts, errors, ok = fetch_instances()
    api_ok = ok > 0
    summary = {"updated_at": now, "api_ok": api_ok, "api_errors": errors, "maids": {}}

    for maid in maids:
        env = maid_env(maid)
        dur_h = _num(env.get("DURATION_HOURS")) or 0.0
        inst_json = _load_json(maid_instance_file(maid))
        cur_id = str(inst_json.get("id")) if inst_json.get("id") else None

        conn = _connect(maid)
        cur = conn.cursor()

        wanted = set()
        if cur_id:
            wanted.add(cur_id)
        for r in cur.execute("SELECT instance_id FROM sessions WHERE status IN ('running','unknown')"):
            wanted.add(str(r["instance_id"]))
        for iid, info in api_insts.items():
            lbl = str(info.get("label") or "").strip().lower()
            if lbl and lbl == str(maid).strip().lower():
                wanted.add(iid)

        n_run = 0
        for iid in sorted(wanted):
            info = api_insts.get(iid) or {}
            row = cur.execute("SELECT * FROM sessions WHERE instance_id=?", (iid,)).fetchone()
            d = _extract(iid, info, inst_json, row, dur_h, now, api_ok=api_ok)

            shutdown_ts = row["shutdown_ts"] if row else None
            prev_status = row["status"] if row else None
            if d["status"] == "stopped" and not shutdown_ts:
                shutdown_ts = now
            if d["status"] == "running":
                shutdown_ts = None

            end = shutdown_ts or now
            cost = 0.0
            if d["boot_ts"]:
                cost = max(0.0, (end - d["boot_ts"]) / 3600.0) * (d["price"] or 0.0)

            if row is None:
                cur.execute(
                    "INSERT INTO sessions (instance_id,maid,gpu,num_gpus,price_per_hour,boot_ts,"
                    "planned_shutdown_ts,shutdown_ts,status,ip,ssh_port,machine_id,label,"
                    "first_seen,last_seen,cost_usd,note) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (iid, maid, d["gpu"], d["num_gpus"], d["price"], d["boot_ts"],
                     d["planned_shutdown_ts"], shutdown_ts, d["status"], d["ip"], d["ssh_port"],
                     d["machine_id"], d["label"], now, now, round(cost, 6), None))
                cur.execute(
                    "INSERT INTO events (ts,instance_id,kind,detail) VALUES (?,?,?,?)",
                    (d["boot_ts"] or now, iid, "boot",
                     "開機 GPU=%s 單價=%s/hr" % (d["gpu"], d["price"])))
            else:
                cur.execute(
                    "UPDATE sessions SET maid=?,gpu=?,num_gpus=?,price_per_hour=?,boot_ts=?,"
                    "planned_shutdown_ts=?,shutdown_ts=?,status=?,ip=?,ssh_port=?,machine_id=?,"
                    "label=?,last_seen=?,cost_usd=? WHERE instance_id=?",
                    (maid, d["gpu"], d["num_gpus"], d["price"], d["boot_ts"],
                     d["planned_shutdown_ts"], shutdown_ts, d["status"], d["ip"], d["ssh_port"],
                     d["machine_id"], d["label"], now, round(cost, 6), iid))
                if prev_status == "running" and d["status"] == "stopped":
                    cur.execute(
                        "INSERT INTO events (ts,instance_id,kind,detail) VALUES (?,?,?,?)",
                        (now, iid, "shutdown", "關機 費用=US$%.4f" % cost))

            if d["status"] == "running":
                n_run += 1

        conn.commit()
        conn.close()
        summary["maids"][maid] = {"running": n_run, "sessions": len(wanted)}

    # Fix 2：餘額告警（credit < 門檻時通知主人補值）
    try:
        acct = fetch_account()
    except Exception:
        acct = {}
    summary["vast_account"] = acct
    if _low_balance_alert(acct, now):
        summary["balance_alert_sent"] = True
    return summary


# ------------------------------------------------------------------ 輸出資料
def _session_dict(r, now):
    boot = r["boot_ts"]
    end = r["shutdown_ts"] or (now if r["status"] == "running" else (r["last_seen"] or now))
    hours = max(0.0, (end - boot) / 3600.0) if boot else 0.0
    return {
        "instance_id": r["instance_id"],
        "maid": r["maid"],
        "gpu": r["gpu"],
        "num_gpus": r["num_gpus"] or 1,
        "price_per_hour": r["price_per_hour"],
        "boot_ts": boot,
        "planned_shutdown_ts": r["planned_shutdown_ts"],
        "shutdown_ts": r["shutdown_ts"],
        "status": r["status"],
        "ip": r["ip"],
        "ssh_port": r["ssh_port"],
        "machine_id": r["machine_id"],
        "label": r["label"],
        "hours": round(hours, 3),
        "cost_usd": round(r["cost_usd"] or 0.0, 4),
        "note": r["note"],
    }


def gpu_price_info():
    """用戶端費率（未定 => display 顯示費率待定）。"""
    info = {"markup": None, "usd_to_hkd": None, "display": "費率待定"}
    try:
        import importlib
        mp = importlib.import_module("mok_price")
        markup = getattr(mp, "MOKAGI_GPU_MARKUP", None)
        fx = getattr(mp, "MOKAGI_GPU_USD_TO_HKD", None)
        info["markup"] = markup
        info["usd_to_hkd"] = fx
        if markup and fx:
            info["display"] = "成本 × %.2f（US$1 = HK$%.2f）" % (float(markup), float(fx))
    except Exception:
        pass
    return info


def status_payload(sync_first=True, now=None):
    now = float(now or time.time())
    if sync_first:
        try:
            sync(now=now)
        except Exception:
            pass

    maids_payload = []
    total_cost = total_hours = 0.0
    total_sessions = running_now = 0

    for maid in discover_maids():
        try:
            conn = _connect(maid)
        except Exception:
            continue
        rows = list(conn.execute("SELECT * FROM sessions ORDER BY COALESCE(boot_ts,0) DESC"))
        sessions = [_session_dict(r, now) for r in rows]
        conn.close()

        m_cost = sum(s["cost_usd"] for s in sessions)
        m_hours = sum(s["hours"] for s in sessions)
        m_run = [s for s in sessions if s["status"] == "running"]

        maids_payload.append({
            "maid": maid,
            "running": len(m_run),
            "sessions": sessions,
            "total_sessions": len(sessions),
            "total_hours": round(m_hours, 3),
            "total_cost_usd": round(m_cost, 4),
        })

    maids_payload.sort(key=lambda m: (-m["running"], -m["total_cost_usd"]))

    # Fix 1b：同一 instance_id 可能同時存在多位侍女 DB（同一台機）→ 總計以 instance_id 去重，只算一次。
    by_id = {}
    for m in maids_payload:
        for s in m["sessions"]:
            iid = s["instance_id"]
            prev = by_id.get(iid)
            if prev is None or (s.get("cost_usd") or 0) > (prev.get("cost_usd") or 0):
                by_id[iid] = s
    total_cost = round(sum((s.get("cost_usd") or 0) for s in by_id.values()), 4)
    total_hours = round(sum((s.get("hours") or 0) for s in by_id.values()), 3)
    total_sessions = len(by_id)
    running_now = sum(1 for s in by_id.values() if s["status"] == "running")

    return {
        "ok": True,
        "updated_at": now,
        "vast_account": fetch_account(),
        "maids": maids_payload,
        "totals": {
            "running": running_now,
            "sessions": total_sessions,
            "hours": round(total_hours, 3),
            "cost_usd": round(total_cost, 4),
        },
        "price": gpu_price_info(),
    }


# ------------------------------------------------------------------ 餘額告警
BALANCE_ALERT_USD = 3.0
BALANCE_COOLDOWN = 6 * 3600
_ALERT_STATE = os.path.join(HOME, "core", ".gpu_balance_alert.json")


def _tg_notify(text):
    """透過 Telegram Bot 推播給管理員（沿用 稚/.稚 的 token 與 chat_id）。"""
    try:
        cfg = _read_env(os.path.join(HOME, "agent", "稚", ".稚"))
        tok = cfg.get("MOK_TG_TOKEN")
        chat = cfg.get("ADMIN_CHAT_ID") or cfg.get("MOK_TG_CHAT_ID")
        if not tok or not chat:
            return False
        body = json.dumps({"chat_id": chat, "text": text,
                           "disable_web_page_preview": True}).encode("utf-8")
        req = urllib.request.Request(
            "https://api.telegram.org/bot%s/sendMessage" % tok,
            data=body, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=15) as r:
            return 200 <= r.status < 300
    except Exception:
        return False


def _low_balance_alert(account, now=None):
    """credit < BALANCE_ALERT_USD → 通知主人補值（冷卻 BALANCE_COOLDOWN，避免洗版）。"""
    now = float(now or time.time())
    if not account:
        return False
    bal = _num(account.get("credit"))
    if bal is None:
        bal = _num(account.get("balance"))
    if bal is None:
        return False
    if bal >= BALANCE_ALERT_USD:
        if os.path.exists(_ALERT_STATE):        # 餘額回補 → 清狀態，下次再跌破才通知
            try:
                os.remove(_ALERT_STATE)
            except Exception:
                pass
        return False
    state = _load_json(_ALERT_STATE)
    if now - (_num(state.get("last_ts")) or 0.0) < BALANCE_COOLDOWN:
        return False
    msg = ("\u26a0\ufe0f vast.ai \u9918\u984d\u4e0d\u8db3\uff0c\u8acb\u88dc\u503c\n"
           "\u76ee\u524d credit = US$%.2f\uff08\u544a\u8b66\u9580\u6abb US$%.2f\uff09\u3002\n"
           "\u9918\u984d\u904e\u4f4e\u6642 vast \u6703\u56de\u6536\u6a5f\u5668\uff0c\u51fa\u7247/\u751f\u5716\u6d41\u7a0b\u53ef\u80fd\u4e2d\u65b7\u3002"
           % (bal, BALANCE_ALERT_USD))
    if not _tg_notify(msg):
        return False
    try:
        with open(_ALERT_STATE, "w", encoding="utf-8") as f:
            json.dump({"last_ts": now, "last_balance": bal}, f, ensure_ascii=False)
    except Exception:
        pass
    return True


# ------------------------------------------------------------------ CLI
def _main(argv):
    cmd = argv[1] if len(argv) > 1 else "status"
    if cmd == "sync":
        s = sync()
        print(json.dumps(s, ensure_ascii=False, indent=2))
    elif cmd == "status":
        print(json.dumps(status_payload(sync_first=False), ensure_ascii=False, indent=2))
    else:
        print("用法: python3 gpu_billing.py [sync|status]")


if __name__ == "__main__":
    _main(sys.argv)
