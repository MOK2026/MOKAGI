#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""dream_core.py — 「做夢」EXP 反思核心（MOKAGI 核心補丁）

讓 agent 定時讀自己的 logs 與 soul，沉澱經驗、追加到 soul/EXP.md。

鐵律
  1. 只寫 soul/EXP.md 與 soul/EXP_archive/；絕不碰 agent.md / user.md。
  2. 寫入 EXP.md 前先走「進化鎖」（skill 進化 editlock.py start / done）。
  3. 權限：agent 設定檔要有 MOK_dream_EXP=1 才執行。
  4. 觸發制：累積足量新 logs 才做（預設 10 份，MOK_dream_min_new_logs）。
     單次最多讀 10 份（MOK_dream_max_log_files）。
     可選時間節流 MOK_dream_interval_h（預設 0＝不等 24h）。
  4b. 做夢成功、經驗落 EXP.md 後，把剛吃掉的這批 log『軟刪』進系統資源回收筒
      （~/.mok/trash/，政策唯一真相 core/log_policy.py；可 restore，絕不硬刪）。
  5. 分批：內容過長自動切批，逐批丟 LLM；全系統另有 gap 分批節流。

流程（單一 agent）
  權限 → 節流 ＋ 新 logs 檢查 → 分批讀（soul 全部 .md，排除 EXP.md，加新 logs）
  → LLM 生成 3 段式 → 鎖 start → append soul/EXP.md → 鎖 done
  → 更新 soul/.dream.json（last_log / last_dream_at）
  → 整理：讀新 EXP.md → LLM 濃縮 → 鎖 → 覆寫
  → 若超過上限行數 → 歸檔到 soul/EXP_archive/EXP_<YYYY-MM>.md
"""

import os
import re
import sys
import json
import time
import subprocess

HOME = os.path.expanduser("~")
MOK = os.path.join(HOME, ".mok")
AGENT_DIR = os.path.join(MOK, "agent")
CORE_DIR = os.path.join(MOK, "core")
if CORE_DIR not in sys.path:
    sys.path.insert(0, CORE_DIR)
# 日誌政策唯一真相：LOG_KEEP / DREAM_TRIGGER / DREAM_BATCH / consume(軟刪)  2026-10-04 by 衍
try:
    from log_policy import (DREAM_TRIGGER as _LP_TRIGGER,
                            DREAM_BATCH as _LP_BATCH,
                            consume as _lp_consume)
except Exception:  # 政策檔缺失時退回安全預設，且不做任何刪除
    _LP_TRIGGER, _LP_BATCH = 10, 10

    def _lp_consume(base_dir, paths):
        return []
EVO_DIR = os.path.join(MOK, "skill", "進化")
EDITLOCK_PY = os.path.join(EVO_DIR, "editlock.py")
PATCH_DIR = os.path.join(CORE_DIR, "做夢補丁")
SCAN_STATE = os.path.join(PATCH_DIR, ".dream_scan.json")

EXP_HEADER = "# 經驗記錄 (EXP)"

# --- owner-only 標記保護（保留 marker） 2026-10-04 by 凜 ---
_OWN_B = "<!-- MOK_OWNER_ONLY -->"
_OWN_E = "<!-- /MOK_OWNER_ONLY -->"
_SLOT = "<!-- __MOK_PROTECTED_%d__ -->"


def _split_protected(text):
    """抽出 owner-only 區塊，以 _SLOT 佔位取代；回傳 (遮罩後文字, [原始區塊...])。"""
    if not text or "MOK_OWNER_ONLY" not in text:
        return text, []
    out, blocks, i = [], [], 0
    while True:
        a = text.find(_OWN_B, i)
        if a == -1:
            out.append(text[i:])
            break
        out.append(text[i:a])
        b = text.find(_OWN_E, a + len(_OWN_B))
        if b == -1:
            block, i = text[a:], len(text)
        else:
            block, i = text[a:b + len(_OWN_E)], b + len(_OWN_E)
        out.append(_SLOT % len(blocks))
        blocks.append(block)
    return "".join(out), blocks


def _strip_owner_only(text):
    """直接移除 owner-only 區塊（餵給 LLM 前淨化）。"""
    prot, _ = _split_protected(text)
    return re.sub(r"<!-- __MOK_PROTECTED_\d+__ -->", "", prot)


def _restore_protected(text, blocks):
    """把 _SLOT 佔位還原成原始區塊；LLM 若吃掉佔位則補回檔尾，保證不遺失。"""
    if not blocks:
        return text
    t = text or ""
    miss = []
    for idx, blk in enumerate(blocks):
        tok = _SLOT % idx
        if tok in t:
            t = t.replace(tok, blk)
        else:
            miss.append(blk)
    if miss:
        t = t.rstrip() + "\n\n" + "\n\n".join(miss) + "\n"
    return t


def _marker_balance(lines):
    b = 0
    for ln in lines:
        b += ln.count(_OWN_B) - ln.count(_OWN_E)
    return b

DEFAULTS = {
    "interval_h": 0.0,
    "min_new_logs": _LP_TRIGGER,   # 政策：log_policy.DREAM_TRIGGER（預設 10）
    "max_lines": 200,
    "batch_chars": 12000,
    "gap_s": 90,
    "consolidate": 1,
    "consolidate_min_lines": 40,
    "max_log_files": _LP_BATCH,    # 政策：log_policy.DREAM_BATCH（預設 10）
    "log_chars": 6000,
    "soul_chars": 4000,
    "num_predict": 32768,
}

_SYS = ("你是 MOKAGI 系統中的 agent，正在做每日反思（代號「做夢」）。"
        "只根據提供的日誌與靈魂檔歸納，不要虛構、不要客套。一律用繁體中文。")


# ------------------------------------------------------------------ utils
def cfg(agent_config, key, default=None):
    if default is None:
        default = DEFAULTS.get(key)
    raw = (agent_config or {}).get("MOK_dream_" + key)
    if raw is None or str(raw).strip() == "":
        return default
    try:
        if isinstance(default, float):
            return float(str(raw).strip())
        if isinstance(default, int) and not isinstance(default, bool):
            return int(float(str(raw).strip()))
        return str(raw).strip()
    except Exception:
        return default


def is_enabled(agent_config):
    v = str((agent_config or {}).get("MOK_dream_EXP", "")).strip().lower()
    return v in ("1", "true", "yes", "on")


def _read_text(path, limit=None):
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            t = f.read()
    except Exception:
        return ""
    if limit and len(t) > limit:
        t = t[:limit] + "\n...(截斷)..."
    return t


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _write_atomic(path, text, mode="w"):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    if mode == "a":
        with open(path, "a", encoding="utf-8") as f:
            f.write(text)
        return
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


def _strip_fence(t):
    t = (t or "").strip()
    m = re.match(r"^```[a-zA-Z]*\s*\n(.*?)\n```$", t, re.S)
    if m:
        return m.group(1).strip()
    return t


# ------------------------------------------------------------ 進化鎖 (editlock)
def _editlock(args, timeout=30):
    try:
        r = subprocess.run([sys.executable, EDITLOCK_PY] + list(args),
                           capture_output=True, text=True, timeout=timeout)
        return r.returncode, (r.stdout or "") + (r.stderr or "")
    except Exception as e:
        return 1, "editlock 呼叫失敗: %s" % e


def lock_start(path, agent, purpose):
    rc, out = _editlock(["start", path, agent, purpose, "--ttl", "900"])
    if rc != 0:
        return None, out
    m = re.search(r"ID\s*[：:]\s*(\S+)", out)
    return (m.group(1) if m else ""), out


def lock_done(lock_id, path, note):
    _editlock(["done", lock_id or path, note or "dream"])


def write_locked(path, text, agent, purpose, note, mode="w"):
    """依進化鎖規則寫入：start 寫 done。回傳 (ok, msg)。"""
    lid, out = lock_start(path, agent, purpose)
    if lid is None:
        return False, (out or "").strip()
    try:
        _write_atomic(path, text, mode=mode)
    except Exception as e:
        lock_done(lid, path, "dream 寫入失敗: %s" % e)
        return False, "寫入失敗: %s" % e
    lock_done(lid, path, note)
    return True, ""


def _write_state(agent_config, path, text):
    """工具私有狀態檔（.dream.json / .dream_scan.json）。
    預設直接原子寫（屬工具私有狀態，不進登記簿）；
    若要嚴格全登記，設 MOK_dream_lock_state=1。"""
    v = str((agent_config or {}).get("MOK_dream_lock_state", "0")).strip().lower()
    if v in ("1", "true", "yes", "on"):
        return write_locked(path, text, (agent_config or {}).get("MOK_AGENT_NAME", "dream"),
                            "做夢：更新狀態檔", "state", mode="w")
    try:
        _write_atomic(path, text, mode="w")
        return True, ""
    except Exception as e:
        return False, str(e)


# ------------------------------------------------------------------- 掃描
def _state_path(agent_dir):
    return os.path.join(agent_dir, "soul", ".dream.json")


def _new_logs(agent_dir, last_log, max_files=10):
    """回傳「尚未處理」的 log 檔（由舊到新），單次最多 max_files 份。

    游標 last_log 是「上次處理的最後一份」檔名。檔名為 YYYYMMDD_HHMMSS_*.md，
    字串序＝時間序，故直接比對檔名即可（即使該檔已被軟刪進回收筒、不存在了也照樣正確）：
      * 有 last_log → 取檔名嚴格大於它的（尚未處理的）
      * 無 last_log → 首輪，取最舊的一批
    修正舊版 fallback「取最新」的漏讀 bug（會跳過最舊未處理的幾份）。
    """
    d = os.path.join(agent_dir, "logs")
    if not os.path.isdir(d):
        return [], None
    files = sorted(f for f in os.listdir(d) if f.endswith(".md"))
    if not files:
        return [], None
    latest = files[-1]
    if last_log:
        new = [f for f in files if f > str(last_log)]
    else:
        new = list(files)
    # 單次最多讀 max_files 份：取「最舊尚未處理」的那批，剩下下一輪再處理
    if max_files and len(new) > max_files:
        new = new[:max_files]
    return [os.path.join(d, f) for f in new], latest


def _soul_blocks(agent_dir, soul_chars):
    d = os.path.join(agent_dir, "soul")
    out = []
    if not os.path.isdir(d):
        return out
    for f in sorted(os.listdir(d)):
        if not f.endswith(".md") or f == "EXP.md":
            continue
        t = _strip_owner_only(_read_text(os.path.join(d, f), limit=soul_chars))
        if t.strip():
            out.append("#### %s\n%s" % (f, t.strip()))
    return out


def _gather_blocks(agent_dir, log_paths, agent_config):
    blocks = []
    soul = _soul_blocks(agent_dir, cfg(agent_config, "soul_chars", 4000))
    if soul:
        blocks.append("### 【靈魂檔 soul】\n" + "\n\n".join(soul))
    log_blocks = []
    for p in log_paths:
        t = _strip_owner_only(_read_text(p, limit=cfg(agent_config, "log_chars", 6000)))
        if t.strip():
            log_blocks.append("#### log: %s\n%s" % (os.path.basename(p), t.strip()))
    if log_blocks:
        blocks.append("### 【新增日誌 logs】\n" + "\n\n".join(log_blocks))
    return blocks


def _chunk_blocks(blocks, max_chars):
    flat = []
    for b in blocks:
        if len(b) <= max_chars:
            flat.append(b)
        else:
            for i in range(0, len(b), max_chars):
                flat.append(b[i:i + max_chars])
    batches, cur, n = [], [], 0
    for b in flat:
        if cur and n + len(b) > max_chars:
            batches.append(cur)
            cur, n = [], 0
        cur.append(b)
        n += len(b)
    if cur:
        batches.append(cur)
    return batches


# --------------------------------------------------------------------- LLM
async def _llm(prompt, agent_config, system=None, **opts):
    from mokagi import call_llm
    res = await call_llm(prompt=prompt, system_prompt=system or _SYS,
                         agent_config=agent_config, stream=False, **opts)
    if isinstance(res, str):
        return res
    if isinstance(res, dict):
        return str(res.get("content") or res.get("reply") or "")
    parts = []
    try:
        async for ev in res:
            if isinstance(ev, str):
                parts.append(ev)
            elif isinstance(ev, dict):
                parts.append(str(ev.get("content") or ev.get("text") or ""))
    except TypeError:
        return str(res)
    return "".join(parts)


def _prompt_extract(agent_name, idx, total, body):
    return (
        "以下是 agent「%s」的部分日誌與靈魂檔（第 %d/%d 批）。\n"
        "請歸納成 3 段（每段一到三則 bullet）：\n"
        "1) 學到什麼：可複用的做法、關鍵指令、API、流程\n"
        "2) 踩了什麼坑：錯誤、限制、環境陷阱\n"
        "3) 下次怎麼做：具體、可執行\n"
        "規定：只寫有根據的內容；某段無內容就寫「（無）」；不要重述任務、不要客套；"
        "不要用 # 或 ## 標題，直接用 1) 2) 3) 三段輸出。\n\n"
        "---- 內容開始 ----\n%s\n---- 內容結束 ----" % (agent_name, idx, total, body)
    )


def _prompt_consolidate(agent_name, text):
    return (
        "以下是 agent「%s」的 EXP.md 全文。請做『整理』：\n"
        "- 合併重複或高度相似的條目\n"
        "- 保留所有具體事實（指令、路徑、連結、錯誤訊息、參數）\n"
        "- 依日期由舊到新排序，同一天合併為一節\n"
        "- 刪除空話與重複敘述，讓內容更精煉\n"
        "嚴禁刪除或改寫 <!-- __MOK_PROTECTED_n__ --> 這類佔位註解，必須原樣保留在原位置；\n"
        "輸出『完整的新版 EXP.md』Markdown 全文（第一行必須是 %s），"
        "不要任何解說、不要 code fence。\n\n"
        "---- EXP.md 開始 ----\n%s\n---- EXP.md 結束 ----"
        % (agent_name, EXP_HEADER, text)
    )


def _format_entry(agent_name, sections, log_paths):
    lines = ["", "## %s — 做夢（自動沉澱）" % time.strftime("%Y-%m-%d %H:%M")]
    multi = len(sections) > 1
    for i, s in enumerate(sections):
        if multi:
            lines.append("### 批次 %d" % (i + 1))
        lines.append((s or "").strip() or "（無）")
    if log_paths:
        lines.append("- 來源 logs：%s" % ", ".join(os.path.basename(p) for p in log_paths[-5:]))
    return "\n".join(lines).rstrip() + "\n"


# ------------------------------------------------------------------- 歸檔
def _archive_if_needed(exp_path, agent_name, max_lines, agent_config):
    txt = _read_text(exp_path)
    lines = txt.splitlines()
    if len(lines) <= max_lines:
        return None
    cut = len(lines) - max_lines
    # 保留 marker：切口不得落在 owner-only 區塊中間（整塊一起保留）
    while cut > 0 and _marker_balance(lines[:cut]) != 0:
        cut -= 1
    if cut <= 0:
        return None
    keep = lines[cut:]
    old = lines[:cut]
    while keep and not keep[0].strip():
        keep.pop(0)
    old_body = "\n".join(old).strip()
    arch_dir = os.path.join(os.path.dirname(exp_path), "EXP_archive")
    arch = os.path.join(arch_dir, "EXP_%s.md" % time.strftime("%Y-%m"))
    if old_body:
        ok, _ = write_locked(arch, "\n\n" + old_body + "\n", agent_name,
                             "做夢：歸檔 EXP.md", "歸檔 EXP 舊條目", mode="a")
        if not ok:
            return None
    new_exp = EXP_HEADER + "\n\n" + "\n".join(keep).strip() + "\n"
    ok, _ = write_locked(exp_path, new_exp, agent_name,
                         "做夢：EXP.md 超過 %d 行，滾動截斷" % max_lines,
                         "保留最近 %d 行，其餘歸檔" % max_lines, mode="w")
    return arch if ok else None


# -------------------------------------------------------------- 單一 agent
async def dream_one(agent_name, agent_config=None, dry_run=False, force=False,
                    ignore_throttle=False):
    """ignore_throttle=True：只略過『自節流 + 份數門檻』，仍要求有新 log（不空轉）。"""
    from config import load_agent_config
    agent_dir = os.path.join(AGENT_DIR, agent_name)
    if agent_config is None:
        agent_config = load_agent_config(agent_name)
    res = {"agent": agent_name, "status": "ok", "appended": False}

    if not is_enabled(agent_config):
        res["status"] = "disabled"
        res["msg"] = "未啟用（需在該 agent 設定檔加 MOK_dream_EXP=1）"
        return res
    if not os.path.isdir(agent_dir):
        res["status"] = "no_agent_dir"
        return res

    st_path = _state_path(agent_dir)
    st = _read_json(st_path)
    last_log = st.get("last_log")
    last_at = float(st.get("last_dream_at") or 0)
    now = time.time()
    interval_h = cfg(agent_config, "interval_h")
    min_new = int(cfg(agent_config, "min_new_logs") or 0)

    log_paths, latest = _new_logs(agent_dir, last_log, cfg(agent_config, "max_log_files"))
    res["new_logs"] = len(log_paths)
    res["latest_log"] = latest

    if not force:
        # 沒有任何新 log → 一定跳過（強制立即也不空轉）
        if not log_paths:
            res["status"] = "skip_no_logs"
            return res
        if not ignore_throttle:
            if interval_h and last_at and (now - last_at) < interval_h * 3600:
                res["status"] = "skip_throttle"
                res["next_in_h"] = round((interval_h * 3600 - (now - last_at)) / 3600.0, 2)
                return res
            if min_new > 0 and len(log_paths) < min_new:
                res["status"] = "skip_not_enough_new_logs"
                res["pending_logs"] = len(log_paths)
                res["need_logs"] = min_new
                return res

    blocks = _gather_blocks(agent_dir, log_paths, agent_config)
    batches = _chunk_blocks(blocks, cfg(agent_config, "batch_chars", 12000))
    res["blocks"] = len(blocks)
    res["batches"] = len(batches)
    exp_path = os.path.join(agent_dir, "soul", "EXP.md")
    res["exp_path"] = exp_path

    if dry_run:
        res["status"] = "dry_run"
        res["lines_before"] = len(_read_text(exp_path).splitlines())
        return res

    sections = []
    for i, b in enumerate(batches):
        try:
            txt = await _llm(_prompt_extract(agent_name, i + 1, len(batches), "\n\n".join(b)),
                             agent_config, num_predict=int(cfg(agent_config, "num_predict", 32768)))
        except Exception as e:
            res["status"] = "llm_error"
            res["msg"] = "第 %d 批生成失敗: %s" % (i + 1, e)
            return res
        sections.append((txt or "").strip())

    entry = _format_entry(agent_name, sections, log_paths)

    if not os.path.exists(exp_path):
        ok, msg = write_locked(exp_path, EXP_HEADER + "\n", agent_name,
                               "做夢：建立 EXP.md", "初始化 EXP.md", mode="w")
        if not ok:
            res["status"] = "locked"
            res["msg"] = msg
            return res
    ok, msg = write_locked(exp_path, entry, agent_name,
                           "做夢：追加經驗到 EXP.md", "append 做夢結果", mode="a")
    if not ok:
        res["status"] = "locked"
        res["msg"] = msg
        return res
    res["appended"] = True

    # 經驗已落 EXP.md → 把剛吃掉的這批 log 軟刪進系統資源回收筒（政策唯一真相 log_policy）
    _moved = _lp_consume(os.path.join(agent_dir, "logs"), log_paths)
    res["logs_trashed"] = len(_moved)
    res["logs_trash_failed"] = len(log_paths) - len(_moved)

    cursor = os.path.basename(log_paths[-1]) if log_paths else latest
    st.update({"last_log": cursor,
               "last_dream_at": now,
               "last_run": time.strftime("%Y-%m-%d %H:%M:%S")})
    _write_state(agent_config, st_path, json.dumps(st, ensure_ascii=False, indent=2))

    do_c = cfg(agent_config, "consolidate", 1)
    try:
        do_c = int(do_c)
    except Exception:
        do_c = 1
    cur = _read_text(exp_path)
    if do_c and len(cur.splitlines()) >= int(cfg(agent_config, "consolidate_min_lines", 40)):
        cur_prot, _prot = _split_protected(cur)
        newtxt = ""
        for attempt in range(2):
            try:
                cand = _strip_fence(await _llm(
                    _prompt_consolidate(agent_name, cur_prot), agent_config,
                    num_predict=int(cfg(agent_config, "num_predict", 32768)),
                    temperature=0.3))
            except Exception as e:
                res["consolidate_msg"] = "整理失敗：%s" % e
                cand = ""
            if cand and len(cand.strip()) > 30 and cand.lstrip().startswith("#"):
                newtxt = cand
                break
        if newtxt:
            newtxt = _restore_protected(newtxt, _prot)
            ok2, _ = write_locked(exp_path, newtxt.rstrip() + "\n", agent_name,
                                  "做夢：整理 EXP.md", "LLM 濃縮整理", mode="w")
            res["consolidated"] = bool(ok2)
        else:
            res["consolidated"] = False
            res.setdefault("consolidate_msg", "輸出無效，保留原檔")

    arch = _archive_if_needed(exp_path, agent_name,
                              int(cfg(agent_config, "max_lines", 200)), agent_config)
    if arch:
        res["archived"] = arch
    res["lines_after"] = len(_read_text(exp_path).splitlines())
    return res

# --------------------------------------------------------------- 全部 agent
def list_agents():
    if not os.path.isdir(AGENT_DIR):
        return []
    return sorted(n for n in os.listdir(AGENT_DIR) if os.path.isdir(os.path.join(AGENT_DIR, n)))


async def dream_all(dry_run=False, force=False, limit=None, only=None,
                    ignore_throttle=False):
    from config import load_agent_config
    names = list_agents() if only is None else list(only)
    out, done = [], 0
    for name in names:
        try:
            cf = load_agent_config(name)
        except Exception:
            continue
        if not is_enabled(cf):
            continue
        try:
            r = await dream_one(name, cf, dry_run=dry_run, force=force,
                                ignore_throttle=ignore_throttle)
        except Exception as e:
            r = {"agent": name, "status": "error", "msg": str(e)}
        out.append(r)
        if r.get("appended") or r.get("status") == "dry_run":
            done += 1
        if limit and done >= limit:
            break
    return out


def should_dream_now(agent_name, agent_config):
    """便宜的前置檢查：只有在「真的會做夢」時才佔用 gap 時段。
    修正飢餓 bug：原本 claim_slot 只檢查 is_enabled，導致排序最前面的
    啟用 agent 每次 gap 到期都把時段佔走；若它正被 24h 自節流擋下
    （skip_throttle），後面的 agent 永遠輪不到。"""
    if not is_enabled(agent_config):
        return False
    agent_dir = os.path.join(AGENT_DIR, agent_name)
    if not os.path.isdir(agent_dir):
        return False
    st = _read_json(_state_path(agent_dir))
    last_log = st.get("last_log")
    last_at = float(st.get("last_dream_at") or 0)
    interval_h = cfg(agent_config, "interval_h")
    min_new = int(cfg(agent_config, "min_new_logs") or 0)
    log_paths, latest = _new_logs(agent_dir, last_log,
                                  cfg(agent_config, "max_log_files"))
    if latest and last_log and latest == last_log:
        return False
    if interval_h and last_at and (time.time() - last_at) < interval_h * 3600:
        return False
    if not log_paths:
        return False
    if min_new > 0 and len(log_paths) < min_new:
        return False
    return True


def claim_slot(agent_name, agent_config):
    """同步檢查並佔用全系統 gap 時段（分批）。回傳 True 表示可執行。
    必須是同步的：心跳引擎是循序呼叫，先佔位才能保證一輪只放行一個 agent。"""
    if not should_dream_now(agent_name, agent_config):
        return False
    gap = cfg(agent_config, "gap_s", 90)
    st = _read_json(SCAN_STATE)
    now = time.time()
    if now - float(st.get("last_ts") or 0) < gap:
        return False
    st.update({"last_ts": now, "last_agent": agent_name,
               "last_run": time.strftime("%Y-%m-%d %H:%M:%S")})
    _write_state(agent_config, SCAN_STATE, json.dumps(st, ensure_ascii=False, indent=2))
    return True


async def heartbeat_dream(agent_name, agent_config):
    """heart 每輪對每個 agent 呼叫一次；在此做 權限 + gap 分批 + 自節流。"""
    if not claim_slot(agent_name, agent_config):
        return
    try:
        await dream_one(agent_name, agent_config)
    except Exception:
        import traceback
        traceback.print_exc()


# --------------------------------------------------------------------- CLI
def _ensure_path():
    if CORE_DIR not in sys.path:
        sys.path.insert(0, CORE_DIR)


def main():
    import argparse
    _ensure_path()
    ap = argparse.ArgumentParser(description="做夢 — EXP 反思（建議先 dry-run）")
    ap.add_argument("--agent", help="指定 agent（預設用 MOK_AGENT_NAME）")
    ap.add_argument("--all", action="store_true", help="掃描所有已啟用 agent")
    ap.add_argument("--dry-run", action="store_true", help="只回報計畫，不呼叫 LLM、不寫檔")
    ap.add_argument("--force", action="store_true", help="略過自節流與新 logs 檢查（可能空轉）")
    ap.add_argument("--ignore-throttle", action="store_true",
                    help="只略過自節流與份數門檻；仍要求有新 log（不空轉）。面板一鍵全體做夢即用此模式")
    ap.add_argument("--ignore-permission", action="store_true", help="忽略 MOK_dream_EXP（僅測試）")
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()

    from app_loop import run_async
    from config import load_agent_config

    if a.all:
        res = run_async(dream_all(dry_run=a.dry_run, force=a.force, limit=a.limit,
                                  ignore_throttle=a.ignore_throttle))
    else:
        name = a.agent or os.environ.get("MOK_AGENT_NAME") or ""
        if not name:
            print(json.dumps({"success": False, "error": "未指定 agent"}, ensure_ascii=False))
            return 1
        cf = load_agent_config(name)
        if a.ignore_permission:
            cf = dict(cf)
            cf["MOK_dream_EXP"] = "1"
        res = run_async(dream_one(name, cf, dry_run=a.dry_run, force=a.force,
                                  ignore_throttle=a.ignore_throttle))
    print(json.dumps(res, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())

