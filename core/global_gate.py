# -*- coding: utf-8 -*-
"""global_gate.py - MOK 對話准入閘（per-agent 版）

2026-09-29 由 架 建立（全域 5 條版）。
2026-10-04 由 稚 改為 per-agent 版（多侍女並行慢 的核心修正）。

設計（2026-10-04 正解）：
- 同一個 agent 同時只跑 1 回合（自己序列化）；不同 agent 之間全並行、不互卡。
- 理由：舊版把「整個 process_message 回合」塞進只有 5 張票的『全域』閘，
  而一張票會被整回合握著（含等雲端模型數十秒），於是第 6 個侍女起全部排隊
  —— 那才是「多侍女並行就慢」的真水管。等雲端模型是 I/O，多開不吃 CPU，
  不該用全域閘擋。真正該擋的 CPU/磁碟密集段交由「重工離線化」處理。

模式（env MOK_GATE_MODE）：
- per_agent（預設）：同 agent 1 條（跨行程，以 /tmp 槽位檔實作）。
- global：舊行為，全體共用 N 條（MOK_MAX_CONCURRENT，預設 5）。保留以便一鍵回滾。

安全閥：
- fail-open：取不到名額（逾時／目錄不可寫）就照舊放行，絕不讓服務卡死。
- 殭屍自清：持有行程已消失，或槽位存在超過 MOK_GATE_STALE_SEC 時自動回收。
- MOK_GATE_GLOBAL_POOL：per_agent 模式下可另設一個全域安全池（預設 0＝不設）。

可調環境變數：
- MOK_GATE_MODE            per_agent（預設）| global
- MOK_AGENT_MAX_CONCURRENT 每個 agent 併發回合數（預設 1）
- MOK_MAX_CONCURRENT      global 模式的全域名額（預設 5）
- MOK_GATE_GLOBAL_POOL    per_agent 模式的選配全域安全池（預設 0）
- MOK_GATE_WAIT_SEC       排隊等最久幾秒（預設 900）
- MOK_GATE_STALE_SEC      槽位視為殭屍的秒數（預設 1800）
- MOK_GATE_DIR            全域槽位目錄（預設 /tmp/mok_global_gate）
- MOK_AGENT_GATE_DIR      per-agent 槽位根目錄（預設 /tmp/mok_agent_gate）

觀測：
    from global_gate import gate_held, gate_stats, agent_gate_held
    gate_held()            # global 模式目前佔用幾個槽位
    agent_gate_held('稚')  # 某 agent 目前佔用幾個槽位
    gate_stats()           # 完整統計
"""
import asyncio
import glob as _glob
import os
import re as _re
import time

MODE = (os.environ.get("MOK_GATE_MODE", "per_agent") or "per_agent").strip().lower()
MAX_CONCURRENT = max(1, int(os.environ.get("MOK_MAX_CONCURRENT", "5") or 5))
AGENT_MAX_CONCURRENT = max(1, int(os.environ.get("MOK_AGENT_MAX_CONCURRENT", "1") or 1))
GLOBAL_POOL = max(0, int(os.environ.get("MOK_GATE_GLOBAL_POOL", "0") or 0))
GATE_DIR = os.environ.get("MOK_GATE_DIR", "/tmp/mok_global_gate")
AGENT_GATE_DIR = os.environ.get("MOK_AGENT_GATE_DIR", "/tmp/mok_agent_gate")
WAIT_SEC = float(os.environ.get("MOK_GATE_WAIT_SEC", "900") or 900)
STALE_SEC = float(os.environ.get("MOK_GATE_STALE_SEC", "1800") or 1800)
STATS = {
    "acquired": 0, "timeout": 0, "released": 0, "reaped": 0,
    "agent_acquired": 0, "agent_timeout": 0, "agent_waited": 0, "agent_max_wait": 0.0,
}


def _safe_agent(name):
    s = _re.sub(r"[^0-9A-Za-z_\u4e00-\u9fff.\-]", "_", str(name or "unknown"))
    return s[:96] or "unknown"


def _reap_dir(d):
    """回收 d 目錄下殭屍槽位：持有行程已死，或存在時間超過 STALE_SEC。"""
    now = time.time()
    for p in _glob.glob(os.path.join(d, "slot_*")):
        try:
            with open(p, "r") as f:
                parts = f.read().split()
            pid = int(parts[0])
            ts = float(parts[1]) if len(parts) > 1 else 0.0
        except Exception:
            pid, ts = -1, 0.0
        dead = (now - ts > STALE_SEC)
        if not dead and pid > 0:
            try:
                os.kill(pid, 0)
            except OSError:
                dead = True
        if dead:
            try:
                os.unlink(p)
                STATS["reaped"] += 1
            except Exception:
                pass


def _reap():
    _reap_dir(GATE_DIR)


def _acquire_in(d, n, timeout, prefix, label):
    """在目錄 d 取 1 個（共 n 個）槽位；逾時回 None（fail-open）。prefix: acquired/agent_acquired。"""
    try:
        os.makedirs(d, exist_ok=True)
    except Exception as e:
        print("[gate] 無法建立 %s：%s → 本輪放行" % (d, e))
        return None
    t0 = time.time()
    deadline = t0 + timeout
    while True:
        _reap_dir(d)
        for i in range(n):
            path = os.path.join(d, "slot_%d" % i)
            try:
                fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o644)
            except FileExistsError:
                continue
            except Exception:
                continue
            try:
                os.write(fd, ("%d %f" % (os.getpid(), time.time())).encode())
            finally:
                os.close(fd)
            STATS[prefix] += 1
            w = time.time() - t0
            if prefix == "agent_acquired" and w > 0.01:
                STATS["agent_waited"] += 1
                STATS["agent_max_wait"] = max(STATS["agent_max_wait"], w)
            if w >= 0.5:
                print("[gate] %s 排隊 %.1fs 才開始（名額 %d）" % (label, w, n))
            return path
        if time.time() >= deadline:
            STATS[prefix.replace("acquired", "timeout")] += 1
            print("[gate] %s 名額已滿 %d，等待逾時 → 本輪放行(fail-open)" % (label, n))
            return None
        time.sleep(0.1)


def gate_acquire(timeout=None):
    """取得一個全域槽位。成功回傳槽位路徑；逾時回傳 None（fail-open）。"""
    timeout = WAIT_SEC if timeout is None else timeout
    return _acquire_in(GATE_DIR, MAX_CONCURRENT, timeout, "acquired", "全域")


def agent_gate_acquire(agent, timeout=None):
    """取得指定 agent 的槽位（同 agent 序列化）。逾時回 None（fail-open）。"""
    timeout = WAIT_SEC if timeout is None else timeout
    d = os.path.join(AGENT_GATE_DIR, _safe_agent(agent))
    return _acquire_in(d, AGENT_MAX_CONCURRENT, timeout, "agent_acquired", "agent[%s]" % _safe_agent(agent))


def gate_release(slot):
    """釋放槽位；slot 為 None 時直接返回。global 與 per-agent 皆可用（皆為檔案路徑）。"""
    if not slot:
        return
    try:
        if os.path.exists(slot):
            os.unlink(slot)
            STATS["released"] += 1
    except Exception:
        pass


def gate_held():
    """目前全域被佔用的槽位數（唯讀觀測）。"""
    try:
        return len(_glob.glob(os.path.join(GATE_DIR, "slot_*")))
    except Exception:
        return -1


def agent_gate_held(agent=None):
    """目前佔用槽位數：指定 agent 或全部 agent 合計。"""
    try:
        if agent is not None:
            return len(_glob.glob(os.path.join(AGENT_GATE_DIR, _safe_agent(agent), "slot_*")))
        return len(_glob.glob(os.path.join(AGENT_GATE_DIR, "*", "slot_*")))
    except Exception:
        return -1


def gate_stats():
    d = dict(STATS)
    d.update({
        "mode": MODE,
        "max": MAX_CONCURRENT,
        "agent_max": AGENT_MAX_CONCURRENT,
        "global_pool": GLOBAL_POOL,
        "held": gate_held(),
        "agent_held": agent_gate_held(),
        "dir": GATE_DIR,
        "agent_dir": AGENT_GATE_DIR,
    })
    return d


def _extract_agent(args, kwargs):
    a = kwargs.get("agent_name")
    if a:
        return a
    cfg = kwargs.get("agent_config")
    if isinstance(cfg, dict):
        n = cfg.get("MOK_AGENT_NAME")
        if n:
            return n
    return None


async def gated(func, *args, **kwargs):
    """在准入閘內執行 coroutine func；取不到名額就照舊執行（fail-open）。

    per_agent 模式：同 agent 序列化（預設 1 條），不同 agent 全並行。
    global 模式：沿用舊的全域 N 條。
    """
    agent = _extract_agent(args, kwargs)
    slots = []
    try:
        loop = asyncio.get_running_loop()
        if MODE == "per_agent" and agent:
            s = await loop.run_in_executor(None, agent_gate_acquire, agent)
            slots.append(s)
            if GLOBAL_POOL > 0:
                g = await loop.run_in_executor(None, gate_acquire)
                slots.append(g)
        else:
            g = await loop.run_in_executor(None, gate_acquire)
            slots.append(g)
    except Exception:
        slots = []
    try:
        return await func(*args, **kwargs)
    finally:
        for s in slots:
            gate_release(s)
