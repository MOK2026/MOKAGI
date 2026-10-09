#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""editlock_hook.py — 編輯登記鎖鉤子（工具層共用）

規則：任何對 ~/.mok 內檔案的「寫入」，都必須先在 skill/進化/編輯登記.json 登記。
      沒登記 / 被別人鎖住 → 擋下。由 replace_in_file、admin exec 等工具呼叫。
緊急停用：環境變數 MOK_EDITLOCK_BYPASS=1
"""
import os, re, importlib.util

HOME = os.path.expanduser("~")
MOK = os.path.join(HOME, ".mok")
EVO = os.path.join(MOK, "skill", "進化")     # 2026-09-12 由 jobs 搬入
EDITLOCK_PY = os.path.join(EVO, "editlock.py")
BYPASS_ENV = "MOK_EDITLOCK_BYPASS"

_EL = None


def _el():
    global _EL
    if _EL is None:
        spec = importlib.util.spec_from_file_location("mok_editlock", EDITLOCK_PY)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _EL = mod
    return _EL


def current_agent(agent_config=None):
    if agent_config is None:
        try:
            import mokagi
            agent_config = getattr(mokagi, "_agent_config", {}) or {}
        except Exception:
            agent_config = {}
    for k in ("MOK_AGENT_NAME", "AGENT_NAME", "AGENT"):
        if agent_config.get(k):
            return str(agent_config[k])
    return "unknown"


def _bypass():
    return os.environ.get(BYPASS_ENV, "").strip().lower() in ("1", "true", "yes", "on")


def guard(file, agent=None, agent_config=None, purpose=None):
    """檢查單一檔案是否可寫。回傳 (ok: bool, msg: str)。"""
    if _bypass():
        return True, ""
    if agent is None:
        agent = current_agent(agent_config)
    try:
        el = _el()
        rf = el.norm_file(file)
        acts = [r for r in el.load() if r.get("file") == rf and el.is_active(r)]
    except Exception:
        return True, ""          # 登記系統異常時放行，避免鎖死全系統
    mine = [r for r in acts if r.get("agent") == agent]
    if mine:                     # 自己已登記
        # 統一硬性備份：目標檔存在，但我的登記沒有 backup_path → 擋下寫入。
        try:
            tgt = os.path.join(MOK, rf)
            # 2026-09-19 修：backups/ 底下的檔案「不備份」（避免備份的備份塞爆碟），
            # 因此這類不再要求 backup_path，直接放行。
            _rf = rf.replace(os.sep, "/")
            while _rf.startswith("./"):
                _rf = _rf[2:]
            _is_backup = (_rf == "backups" or _rf.startswith("backups/"))
            if (not _is_backup) and os.path.isfile(tgt) and not mine[0].get("backup_path"):
                return False, (
                    "⛔ 編輯登記鎖：`%s` 尚未備份，已擋下本次寫入。\n"
                    "👉 請重新登記以自動快照（統一備份區）：\n"
                    "   python3 ~/.mok/skill/進化/editlock.py start %s %s <目的>\n"
                    "   → 備份會存到 ~/.mok/backups/_tmp_backups/editlock/"
                    % (rf, os.path.join(MOK, rf), agent))
        except Exception:
            return True, ""      # 檢查異常時放行，避免鎖死（與原設計一致）
        return True, ""          # 已登記且有備份 → 放行
    others = [r for r in acts if r.get("agent") != agent]
    if others:
        b = others[0]
        try:
            el.write_todo(agent, rf, purpose or "（工具鉤子攔截）",
                          "%s（%s）" % (b.get("agent"), b.get("id")))
        except Exception:
            pass
        return False, (
            "⛔ 編輯登記鎖：`%s` 正由「%s」編輯中（%s），已擋下本次寫入。\n"
            "👉 待對方 `python3 ~/.mok/skill/進化/editlock.py done %s <說明>` 後再登記。"
            % (rf, b.get("agent"), b.get("id"), b.get("id")))
    # 共用檔硬擋（2026-10-04）：目標是共用檔時，登記「必須」附 --ack-parallel，
    # 否則 editlock.py 一樣拒鎖（登了也白登）→ 這裡直接把正確指令給出來。
    try:
        _shared = el.is_shared(rf)
    except Exception:
        _shared = False
    if _shared:
        return False, (
            "⛔ 編輯登記鎖：`%s` 尚未登記，已擋下本次寫入。\n"
            "   ⚠️ 這是「共用檔」（屬 %s）：登記時必須附 --ack-parallel 影響面說明，否則拒鎖。\n"
            "👉 請先登記：python3 ~/.mok/skill/進化/editlock.py start %s %s <目的> --ack-parallel 「影響面一句話」\n"
            "   完成後：python3 ~/.mok/skill/進化/editlock.py done %s <說明>"
            % (rf, "、".join(getattr(el, "SHARED_PREFIXES", ())) or "共用區",
               os.path.join(MOK, rf), agent, rf))
    return False, (
        "⛔ 編輯登記鎖：`%s` 尚未登記，已擋下本次寫入。\n"
        "👉 請先登記：python3 ~/.mok/skill/進化/editlock.py start %s %s <目的>\n"
        "   完成後：python3 ~/.mok/skill/進化/editlock.py done %s <說明>"
        % (rf, os.path.join(MOK, rf), agent, rf))


def ensure(file, agent=None, agent_config=None, purpose=None):
    ok, msg = guard(file, agent, agent_config, purpose)
    return None if ok else msg


# ---------------- shell 命令檢查 ----------------
_WRITE_CMD = re.compile(
    r"(^|[\s;&|(])(rm|mv|cp|rsync|scp|tee|truncate|dd|chmod|chown|touch|mkdir|ln|patch|unzip|tar|shred)(\s|$)")
_SED_I = re.compile(r"sed\s+(-[a-zA-Z]+\s+)*-i")
_REDIR = re.compile(r"(?<![-=0-9])>>?(?!&)")
_PYWRITE = re.compile(
    r"\.write\s*\(|json\.dump\s*\(|shutil\.(copy|move|rmtree)|os\.(remove|unlink|rename|replace)\s*\("
    r"|open\s*\(.*?,\s*['\"][^'\"]*[wax+][^'\"]*['\"]")
_PATH = re.compile(r"(?:/home/[^/\s'\";|&<>()]+)?/?\.mok/[^\s'\";|&<>()]+")
_SKIP = ("/skill/進化/editlock.py", "/skill/進化/編輯登記.json", "/skill/進化/編輯登記.md",
         "/skill/進化/代辦事項", "/skill/進化", "/core/warden", "/core/editlock_hook.py")
# 相對路徑 token 只在這幾種情況視為檔案候選：含 /、實際存在、或常見副檔名。
# 避免把 importlib.util、json.load 這類程式碼片段誤判成檔案。
_EXTS = {".py", ".json", ".md", ".html", ".htm", ".txt", ".js", ".css", ".sh",
         ".db", ".csv", ".yaml", ".yml", ".log", ".bak", ".cfg", ".conf",
         ".ini", ".toml", ".png", ".jpg", ".jpeg", ".gif", ".svg", ".xml", ".mp3", ".mp4"}


def _strip_quoted(cmd):
    """移除單/雙引號內內容，避免字串中的 > 被誤判為重定向。"""
    out, q, i = [], None, 0
    while i < len(cmd):
        ch = cmd[i]
        if q is not None:
            if ch == q:
                q = None
        elif ch in ("'", '"'):
            q = ch
        else:
            out.append(ch)
        i += 1
    return "".join(out)


def _has_write_intent(cmd):
    c = _strip_quoted(cmd)
    if _WRITE_CMD.search(c) or _SED_I.search(c) or _REDIR.search(c):
        return True
    # 程式碼字串通常包在引號中，故 _PYWRITE 仍用原始命令判斷
    return bool(_PYWRITE.search(cmd))


def _candidate_paths(cmd):
    """列出命令中可能指向 ~/.mok 內檔案的路徑（含相對路徑）。"""
    cands = []
    for m in _PATH.finditer(cmd):
        cands.append(m.group(0))
    mok_real = os.path.realpath(MOK)
    base, cd_targets = None, set()
    for m in re.finditer(r"(?:^|[\s;&|(])cd\s+([^\s;&|()]+)", cmd):
        t = m.group(1).strip("'\"")
        cd_targets.add(t)
        base = os.path.realpath(os.path.expanduser(t))
    if base is None:
        base = os.path.realpath(os.getcwd())
    if base == mok_real or base.startswith(mok_real + os.sep):
        for tok in re.findall(r"[^\s;&|()<>'\"]+", cmd):
            if tok in cd_targets or tok.startswith(("-", "/", "~")):
                continue
            if "/" not in tok and "." not in tok:
                continue
            if re.fullmatch(r"[A-Za-z]/[^/]*/[^/]*/?", tok):   # 排除 sed s/a/b/ 這類片段
                continue
            joined = os.path.join(base, tok)
            if "/" not in tok and not os.path.exists(joined) \
                    and os.path.splitext(tok)[1].lower() not in _EXTS:
                continue
            cands.append(joined)
    return cands


_EDITLOCK_CALL = re.compile(
    r"^\s*(?:[A-Za-z_][A-Za-z0-9_]*=\S+\s+)*"
    r"(?:[\w./~-]*/)?python3?\s+\S*editlock\.py(?:\s|$)")
_CMD_SPLIT = re.compile(r"&&|\|\||;|\||\n")


def _is_editlock_call(cmd):
    """命令是否『只』由 editlock.py 呼叫組成（可用 && ; || | 串接）。
    避免 `真正的寫入指令 # editlock.py` 這類註解提及就整條放行。"""
    segs = [s.strip() for s in _CMD_SPLIT.split(cmd) if s.strip()]
    if not segs:
        return False
    for s in segs:
        if _EDITLOCK_CALL.match(s) or re.match(r"^(?:echo|printf|true|:)\b", s):
            continue
        return False
    return True


# ---------------- 全樹掃描攔阻（2026-10-04 靜） ----------------
# 背景：對整個 home / 根目錄做遞迴 grep/find，會把磁碟 I/O 榨乾
# （實測 iowait 52%、sda %util 80%、load 5+，多侍女同時工作時全體變慢）。
# 原則：找檔走 code_index（技能「找檔規範」）；非用 shell 不可就限定目錄 + 排除重目錄。
_BROAD_ROOTS = {
    "/", "/home", "/home/ubuntu", "/home/ubuntu/.mok",
    "~", "~/", "$HOME", "${HOME}", os.path.expanduser("~"), MOK,
}
_SCAN_CMD = re.compile(r"(^|[\s;&|(])(grep|rg|egrep|fgrep|ack|ag|find)\b")
_RECUR_FLAG = re.compile(r"(?:^|\s)(?:-[a-zA-Z]*[rR][a-zA-Z]*|--recursive|--dereference-recursive)(?=\s|$)")
_FIND_MAXDEPTH = re.compile(r"(?:^|\s)-maxdepth\s+([0-9]+)")
_SCAN_ESCAPE = re.compile(r"--exclude-dir|--exclude(?![-a-z])|--glob|(?:^|\s)-path\b|(?:^|\s)-prune\b")


def _norm_root(tok):
    t = tok.strip("'\"")
    t = t.replace("${HOME}", os.path.expanduser("~")).replace("$HOME", os.path.expanduser("~"))
    t = os.path.expanduser(t)
    return os.path.normpath(t) if t else t


def guard_scan(cmd):
    """擋下「掃整個家目錄/根目錄」的遞迴 grep/find。回傳 (ok: bool, msg: str)。"""
    if _bypass() or not cmd:
        return True, ""
    if _SCAN_ESCAPE.search(cmd):          # 已自行排除重目錄 → 放行
        return True, ""
    m = _FIND_MAXDEPTH.search(cmd)
    if m and int(m.group(1)) <= 3:        # 淺層掃描 → 放行
        return True, ""
    hits = []
    for seg in _CMD_SPLIT.split(cmd):
        seg = seg.strip()
        if not seg or not _SCAN_CMD.search(seg):
            continue
        if "find" not in seg and not _RECUR_FLAG.search(seg) \
                and not re.search(r"(^|[\s;&|(])(rg|ack|ag)\b", seg):   # rg/ack/ag 預設就是遞迴
            continue
        for t in re.findall(r"[^\s;&|()<>'\"]+", seg)[1:]:
            if t.startswith("-"):
                continue
            if _norm_root(t) in _BROAD_ROOTS:
                hits.append(t)
                break
    if not hits:
        return True, ""
    return False, (
        "⛔ admin exec 被「全樹掃描攔阻」擋下：偵測到對 `%s` 的遞迴 grep/find。\n"
        "   全樹掃描會打爆磁碟 I/O（曾把 iowait 拉到 52%%，整台機器一起卡）。\n"
        "   ✅ 找檔請走 code_index（技能「找檔規範」）。\n"
        "   ✅ 非用 shell 不可時，請限定目錄並排除重目錄，例如：\n"
        "      grep -rn 關鍵字 /home/ubuntu/.mok/agent/<agent> --exclude-dir=browser_profiles --exclude-dir=node_modules --exclude-dir=backups --exclude-dir=.chroma_data --exclude-dir=.git\n"
        "      find /home/ubuntu/.mok/agent/<agent> -maxdepth 3 -name '*.md'"
        % ", ".join(sorted(set(hits)))
    )


def guard_command(cmd, agent=None, agent_config=None):
    """檢查 shell 命令會寫到的 ~/.mok 檔案。回傳 (ok: bool, msg: str)。"""
    if _bypass() or not cmd:
        return True, ""
    if _is_editlock_call(cmd):               # 只放行「純登記工具呼叫」，避免用註解提及 editlock.py 就整條繞過
        return True, ""
    ok_scan, scan_msg = guard_scan(cmd)       # 2026-10-04 靜：全樹掃描攔阻
    if not ok_scan:
        return False, scan_msg
    if not _has_write_intent(cmd):
        return True, ""
    seen, problems = set(), []
    for p in _candidate_paths(cmd):
        p = p.strip("'\"")
        if p in seen:
            continue
        seen.add(p)
        if any(s in p for s in _SKIP):
            continue
        ok, msg = guard(p, agent, agent_config)
        if not ok:
            problems.append(msg)
    if problems:
        return False, "⛔ admin exec 被編輯登記鎖擋下：\n\n" + "\n".join(problems)
    return True, ""
