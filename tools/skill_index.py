#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""skill_index.py — 技能索引產生器（P0）＋ 漸進披露助手（P2）

掃描 ~/.mok/skill/<技能>/README.md：
  1. 解析 YAML frontmatter（name / description / triggers）
  2. 沒有 frontmatter 就退化成 H1 標題 + 第一段 / 「> 用途」行
產出：~/.mok/skill/_index.json
提供：block() 給 system prompt 注入的壓縮「技能索引」區塊
      outline_str() / section_str() 供 skill view 的漸進披露

本檔案「不」註冊工具（無 PLUGIN_INFO），只被 import 或 CLI 呼叫。
CLI: python3 skill_index.py [build|show]
"""
import json
import os
import re
import time

SKILL_DIR = os.path.expanduser("~/.mok/skill")
INDEX_PATH = os.path.join(SKILL_DIR, "_index.json")
MAX_DESC = 88
BLOCK_MAX = 60
STUB_SIZE = 300
PINS_PATH = os.path.join(SKILL_DIR, "_pins.json")
USAGE_LOG = os.path.join(SKILL_DIR, "_route_log.jsonl")
SECTION_MAX = 4000

_MD_PATS = [
    (re.compile(r"`([^`]*)`"), r"\1"),
    (re.compile(r"\*\*([^*]+)\*\*"), r"\1"),
    (re.compile(r"\[([^\]]*)\]\([^)]*\)"), r"\1"),
    (re.compile(r"<[^>]+>"), ""),
]
TRIGGER_STOP = {"技能", "agent", "skill", "說明", "用途", "適用", "一句話", "本技能", "使用", "功能", "內容",
                "觸發", "觸發詞", "關鍵詞", "什麼時候用", "什麼時候用我", "什麼時候不要用我", "適用場景", "何時用", "何時不用"}
META_LINE = re.compile(r"^(適用|帳號|帳戶|Account|Email|電子郵件|排程|位置|建立|最後更新|本技能|實測|運作方式|目錄|登入|網址|版本|狀態|規則|限制|前置|環境|授權|金鑰|Token|Key|Zone|ID|網域)")
JUNK_PAT = re.compile(r"(?:r\s*m\s+-rf|sudo\s+r\s*m|python3\s+<<|^skill$)")


def _clean(s):
    s = s or ""
    for pat, rep in _MD_PATS:
        s = pat.sub(rep, s)
    s = re.sub(r"\s+", " ", s)
    return s.strip(" \t>#*-—:：")


def parse_frontmatter(text):
    """極簡 YAML frontmatter 解析（支援 key: value 與 | / > 區塊）。回傳 (meta, body)。"""
    if not text.lstrip().startswith("---"):
        return {}, text
    m = re.match(r"^\s*---\s*\n(.*?)\n\s*---\s*\n?", text, re.S)
    if not m:
        return {}, text
    raw, body = m.group(1), text[m.end():]
    meta, key, buf = {}, None, []

    def flush():
        nonlocal key
        if key:
            meta[key] = " ".join(x.strip() for x in buf if x.strip()).strip()
            key = None

    for line in raw.split("\n"):
        if key is not None and re.match(r"^\s+\S", line):
            buf.append(line)
            continue
        flush()
        m2 = re.match(r"^\s*([A-Za-z_][\w-]*)\s*:\s*(.*)$", line)
        if m2:
            k, v = m2.group(1).lower(), m2.group(2).strip()
            if v in ("", "|", ">", "|-", ">-", "|+"):
                key, buf = k, []
            else:
                meta[k] = v.strip().strip("'\"")
    flush()
    for k in list(meta):
        if isinstance(meta[k], str):
            meta[k] = meta[k].strip().strip("'\"")
    return meta, body


def _h1(body):
    for line in body.split("\n"):
        if line.startswith("# "):
            return _clean(line[2:])
    return ""


def _first_paragraph(body):
    """挑一句最能代表技能的說明：優先「用途/一句話」標記行，其次第一個像人話的段落。"""
    cands = []
    for line in body.split("\n")[:80]:
        raw = line.strip()
        if not raw or raw.startswith(("#", "---", "|", "```", "<!--")):
            continue
        marked = raw.startswith(">")
        s = _clean(raw.lstrip("> ").strip())
        if len(s) < 6:
            continue
        m = re.match(r"^(用途|一句話|適用場景|做什麼|介紹|簡介|說明)\s*[:：]\s*(.+)$", s)
        if m and len(m.group(2)) >= 6:
            s = m.group(2)
        score = 0
        if marked:
            score += 3
        if re.match(r"^(用途|一句話|適用場景)", _clean(raw.lstrip("> ").strip())):
            score += 2
        if META_LINE.match(s):
            score -= 4
        if "@" in s or "http" in s:
            score -= 3
        if JUNK_PAT.search(s):
            score -= 6
        if 20 <= len(s) <= 120:
            score += 2
        cands.append((score, len(cands), s))
    if not cands:
        return ""
    cands.sort(key=lambda x: (-x[0], x[1]))
    return cands[0][2]


_SENT_END = ("。", "！", "？", "；", "!", "?", ";")
_COMMA_END = ("，", "、", ",", "：", ":")


def _clip_desc(s, limit=None):
    """智慧截斷：過長時優先切在句末（。！？；），其次逗號，最後才硬切補「…」。"""
    limit = limit or MAX_DESC
    s = (s or "").strip()
    if len(s) <= limit:
        return s
    head = s[:limit]
    for sep in _SENT_END:
        i = head.rfind(sep)
        if i >= int(limit * 0.5):
            return head[: i + 1]
    for sep in _COMMA_END:
        i = head.rfind(sep)
        if i >= int(limit * 0.6):
            return head[:i].rstrip() + "…"
    sp = head.rfind(" ")
    if sp >= int(limit * 0.7):
        return head[:sp].rstrip() + "…"
    return head.rstrip() + "…"


def extract_meta(name, path):
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            text = f.read()
    except Exception as e:
        return {"name": name, "description": "", "triggers": [], "size": 0, "stub": True, "error": str(e)}
    meta, body = parse_frontmatter(text)
    title = _h1(body) or _clean(meta.get("name")) or name
    desc = _clean(meta.get("description")) or _first_paragraph(body)
    if re.match(r"^(\d+[.、)）]|[-*•]|⚠|⛔|🚨|http|Account)", desc) or len(desc) < 12:
        desc = _clean(title) or desc
    desc = _clip_desc(desc)
    triggers = []
    for k in ("triggers", "trigger", "keywords", "when_to_use", "觸發", "觸發詞", "關鍵詞"):
        if meta.get(k):
            triggers += re.split(r"[,，、/|;；]", str(meta[k]))
    for line in body.split("\n")[:60]:
        if re.search(r"觸發|關鍵詞", line):
            triggers += re.split(r"[,，、/|;；：:]", _clean(line))
    seen, out = set(), []
    for t in (x.strip() for x in triggers):
        if 2 <= len(t) <= 14 and t not in TRIGGER_STOP and t not in seen:
            seen.add(t)
            out.append(t)
    return {
        "name": name,
        "title": _clean(title),
        "description": desc,
        "triggers": out[:8],
        "size": len(text),
        "stub": len(text) < STUB_SIZE or ("# " not in text[:400]) or bool(JUNK_PAT.search(text[:800])),
    }


# ---------------- 排序：可 pin 的重要性序（2026-10-04 由泠修正） ----------------
# 背景：舊版純按資料夾名（UTF-8 位元序）排序，第 31 條之後被靜默砍掉，
#       而且「誰被砍」純屬檔名運氣。改成：
#         pin 清單（主人指定，永遠在最前） > 使用頻率 > 近期更新 > 手動權重 > 名稱
# 設定檔 ~/.mok/skill/_pins.json：
#   {"pins": ["X發文製片"], "hide": ["舊技能"], "weights": {"gmail": 10}}
#   容忍純 list（視為 pins）。

def _mtime(path):
    try:
        return os.path.getmtime(path)
    except OSError:
        return 0.0


def _readme_of(name):
    return os.path.join(SKILL_DIR, name or "", "README.md")


def load_pins(path=None):
    """回傳 (pins, hide, weights)；檔案不存在或壞掉 → 空設定，不報錯。"""
    try:
        with open(path or PINS_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return [], set(), {}
    if isinstance(data, list):
        return [str(x) for x in data], set(), {}
    if not isinstance(data, dict):
        return [], set(), {}
    pins = [str(x) for x in (data.get("pins") or [])]
    hide = {str(x) for x in (data.get("hide") or [])}
    weights = {}
    for k, v in (data.get("weights") or {}).items():
        try:
            weights[str(k)] = float(v)
        except (TypeError, ValueError):
            continue
    return pins, hide, weights


def load_usage(path=None, tail=5000):
    """從 route_feedback 的 _route_log.jsonl 尾段統計每個技能被 view 幾次。
    只讀不寫；讀不到就當 0，索引仍可用。"""
    counts = {}
    try:
        with open(path or USAGE_LOG, "r", encoding="utf-8", errors="ignore") as f:
            lines = f.readlines()[-tail:]
    except Exception:
        return counts
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            r = json.loads(line)
        except Exception:
            continue
        if isinstance(r, dict) and r.get("kind") == "use" and r.get("name"):
            n = str(r["name"])
            counts[n] = counts.get(n, 0) + 1
    return counts


def score_skill(s, usage=None, weights=None, now=None):
    """重要性分數：使用頻率為主、近期更新為輔、手動權重加成。"""
    usage, weights = usage or {}, weights or {}
    now = now if now is not None else time.time()
    name = s.get("name") or ""
    sc = min(usage.get(name, 0), 30) * 3.0
    sc += float(weights.get(name, 0.0))
    age = now - _mtime(_readme_of(name))
    if age < 7 * 86400:
        sc += 6.0
    elif age < 30 * 86400:
        sc += 3.0
    elif age < 180 * 86400:
        sc += 1.0
    if s.get("triggers"):
        sc += 1.0
    return round(sc, 2)


def order_skills(skills, pins=None, hide=None, weights=None, usage=None):
    """排序並標記：pin（照清單序）→ 分數 → 最近更新 → 檔名；stub 一律墊底。"""
    pins, hide = list(pins or []), set(hide or {})
    weights, usage = weights or {}, usage or {}
    by_name = {}
    for s in skills:
        by_name.setdefault(s.get("name"), s)
    head, ranked = [], set()
    for p in pins:
        s = by_name.get(p)
        if s is None or s.get("name") in ranked:
            continue
        ranked.add(s.get("name"))
        s["pin"] = True
        head.append(s)
    rest = []
    for s in skills:
        if s.get("name") in ranked or s.get("name") in hide:
            continue
        s["pin"] = False
        s["score"] = score_skill(s, usage, weights)
        rest.append(s)
    rest.sort(key=lambda s: (1 if s.get("stub") else 0,
                             -float(s.get("score") or 0),
                             -_mtime(_readme_of(s.get("name"))),
                             s.get("name") or ""))
    return head + rest


def build_index():
    skills = []
    if os.path.isdir(SKILL_DIR):
        for entry in sorted(os.listdir(SKILL_DIR)):
            d = os.path.join(SKILL_DIR, entry)
            if not os.path.isdir(d) or entry.startswith("_") or entry.startswith("."):
                continue
            readme = os.path.join(d, "README.md")
            if not os.path.isfile(readme):
                skills.append({"name": entry, "title": entry, "description": "", "triggers": [],
                               "size": 0, "stub": True, "readme": False})
                continue
            info = extract_meta(entry, readme)
            info["readme"] = True
            skills.append(info)
    pins, hide, weights = load_pins()
    skills = order_skills(skills, pins, hide, weights, load_usage())
    idx = {
        "generated_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        "dir": SKILL_DIR,
        "total": len(skills),
        "active": sum(1 for s in skills if not s.get("stub")),
        "pins": pins,
        "hidden": sorted(hide),
        "order": "pin > usage > mtime > weight > name",
        "skills": skills,
    }
    try:
        with open(INDEX_PATH, "w", encoding="utf-8") as f:
            json.dump(idx, f, ensure_ascii=False, indent=1)
    except Exception:
        pass
    return idx


def load_index(max_age=0):
    """讀索引；不存在、解析失敗或超過 max_age 秒就重建。"""
    try:
        st = os.path.getmtime(INDEX_PATH)
    except OSError:
        return build_index()
    if max_age and (time.time() - st) > max_age:
        return build_index()
    try:
        with open(INDEX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return build_index()


def _stale():
    try:
        ist = os.path.getmtime(INDEX_PATH)
    except OSError:
        return True
    # pin 清單或使用紀錄有變 → 索引視為過期（排序需要重建）
    for extra in (PINS_PATH, USAGE_LOG):
        try:
            if os.path.getmtime(extra) > ist:
                return True
        except OSError:
            pass
    try:
        for e in os.listdir(SKILL_DIR):
            r = os.path.join(SKILL_DIR, e, "README.md")
            if os.path.isfile(r) and os.path.getmtime(r) > ist:
                return True
    except OSError:
        pass
    return False


def render_block(idx, max_items=BLOCK_MAX):
    skills = [s for s in idx.get("skills", []) if not s.get("stub")]
    if not skills:
        return ""
    lines = ["【技能索引】只列「技能名（觸發詞）」；找技能看這張表，命中就直接用，"
             "不要再一輪輪 skill list / skill search 亂翻。找不到才試 find(\"<你的需求>\")。"]
    shown = skills[:max_items]
    for s in shown:
        # 2026-10-04 稚：瘦身——只輸出「技能名（觸發詞）」，描述改為按需 skill view 才載，省每輪 token。
        trig = "、".join(s["triggers"][:5]) if s.get("triggers") else ""
        mark = "📌 " if s.get("pin") else ""
        lines.append("- " + mark + s["name"] + (("（" + trig + "）") if trig else ""))
    if len(skills) > max_items:
        lines.append("- …另有 %d 個技能，用 find(\"<需求>\") 查。" % (len(skills) - max_items))
    if any(s.get("pin") for s in shown):
        lines.append("（📌＝核心技能，由主人 pin 在最前；其後按使用頻率排序，不再固定檔名序。）")
    lines.append("（技能描述與正文不常駐、按需載入：先 skill view <name> outline 看大綱，再 section N 取單節。）")
    return "\n".join(lines)


_BLOCK_CACHE = {"key": None, "text": ""}

_SELF_PATH = os.path.abspath(__file__)
_SELF_MTIME = _mtime(_SELF_PATH)


def _self_refresh():
    """原始碼被改過就自我重載：已 import 的長命程序（pm2）不必重啟也能生效。"""
    global _SELF_MTIME
    try:
        mt = os.path.getmtime(_SELF_PATH)
    except OSError:
        return
    if mt <= _SELF_MTIME:
        return
    _SELF_MTIME = mt
    try:
        import importlib
        import sys as _sys
        mod = _sys.modules.get(__name__)
        if mod is not None:
            importlib.reload(mod)
    except Exception:
        pass


def block(max_items=BLOCK_MAX):
    """給 system prompt 用；有 mtime 快取，多數呼叫零成本。"""
    _self_refresh()
    try:
        if _stale():
            build_index()
        key = os.path.getmtime(INDEX_PATH)
    except OSError:
        build_index()
        key = None
    if _BLOCK_CACHE["key"] == key and _BLOCK_CACHE["text"]:
        return _BLOCK_CACHE["text"]
    text = render_block(load_index(), max_items=max_items)
    _BLOCK_CACHE["key"] = key
    _BLOCK_CACHE["text"] = text
    return text


# ---------------- P2：漸進披露 ----------------

def read_skill_md(name):
    name = (name or "").strip().rstrip("/")
    if name.endswith(".md"):
        name = name[:-3]
    path = os.path.join(SKILL_DIR, name, "README.md")
    if not os.path.isfile(path):
        legacy = os.path.join(SKILL_DIR, name + ".md")
        if os.path.isfile(legacy):
            path = legacy
        else:
            return None, None
    try:
        with open(path, "r", encoding="utf-8", errors="ignore") as f:
            return f.read(), path
    except Exception:
        return None, None


def sections(text):
    lines = text.split("\n")
    heads, fence = [], False
    for i, l in enumerate(lines):
        if l.strip().startswith("```"):
            fence = not fence
            continue
        if fence:
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", l)
        if m:
            heads.append((i, len(m.group(1)), _clean(m.group(2))))
    secs = []
    for j, (i, lv, title) in enumerate(heads):
        end = heads[j + 1][0] if j + 1 < len(heads) else len(lines)
        body = "\n".join(lines[i:end]).strip()
        secs.append({"idx": j + 1, "level": lv, "title": title, "line": i + 1, "chars": len(body)})
    return secs


def outline_str(name):
    text, path = read_skill_md(name)
    if text is None:
        return json.dumps({"error": "技能不存在: " + str(name)}, ensure_ascii=False)
    secs = sections(text)
    out = ["# 技能大綱：%s（%d 字）" % (name, len(text))]
    for s in secs:
        out.append("%s%2d. %s  〔%d 字〕" % ("  " * (s["level"] - 1), s["idx"], s["title"], s["chars"]))
    out.append("")
    out.append("要讀某一節：skill view %s section N（例如 section 2）。要全文才用 full。" % name)
    return "\n".join(out)


def section_str(name, n):
    text, path = read_skill_md(name)
    if text is None:
        return json.dumps({"error": "技能不存在: " + str(name)}, ensure_ascii=False)
    secs = sections(text)
    try:
        n = int(n)
    except Exception:
        return json.dumps({"error": "section 需要節號（整數），先用 outline 看有哪些節"}, ensure_ascii=False)
    hit = [s for s in secs if s["idx"] == n]
    if not hit:
        return json.dumps({"error": "第 %d 節不存在（共 %d 節），先用 outline 看大綱" % (n, len(secs))}, ensure_ascii=False)
    s = hit[0]
    lines = text.split("\n")
    end = None
    for t in secs:
        if t["idx"] == n + 1:
            end = t["line"] - 1
    body = "\n".join(lines[s["line"] - 1: (end if end else len(lines))]).strip()
    truncated = False
    if len(body) > SECTION_MAX:
        body = body[:SECTION_MAX]
        truncated = True
    head = "# %s ｜ 第 %d 節：%s\n\n" % (name, n, s["title"])
    tail = "\n\n…（本節過長已截斷，可再用 section %d 之外的方式或 full 取全文）" % n if truncated else ""
    return head + body + tail


if __name__ == "__main__":
    import sys
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    if cmd == "show":
        print(block())
    else:
        idx = build_index()
        print("✅ 索引已建立：%s" % INDEX_PATH)
        print("技能總數 %d，其中可用 %d" % (idx["total"], idx["active"]))
        for s in idx["skills"]:
            flag = "○stub" if s.get("stub") else ("  📌 " if s.get("pin") else " " * 5)
            print("  %s %-16s %s" % (flag, s["name"], s.get("description", "")))
