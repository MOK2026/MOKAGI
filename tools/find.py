#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""find.py — 統一檢索入口（P1）

一句話：agent 想「哪個技能/工具能做 X」時，只記一個入口 —— find("<需求>")，
回傳候選卡（名稱 / 一句話 / 為何匹配 / 下一步指令），不必背所有工具名、也不必
一輪輪 skill list → skill search 盲猜。

【何時用我】
- 不確定現成工具/技能叫什麼名字（生成圖片、下載素材、發 IG、備份…）。
- 你打算連續呼叫 skill list / skill search 時 —— 先 find()。
- 某工具已失敗 2 次，想換條路。
【何時不用我】
- 已知工具名（例如 tts、vision）→ 直接呼叫。
- 純聊天、單純讀檔、單純跑指令。
【例子】
- find("生成一張龜吃麵的圖") → 命中 技能 pollinations，下一步 skill view pollinations outline
"""
import html
import json
import os
import re
import sys

TOOLS_DIR = os.path.expanduser("~/.mok/tools")
SKILL_DIR = os.path.expanduser("~/.mok/skill")
FEEDBACK = os.path.join(SKILL_DIR, "_route_feedback.json")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

# 輕量同義擴充（免 LLM，工具呼叫內不再等一次模型）
SYN = {
    "圖": ["圖片", "繪圖", "畫圖", "生成圖", "文生圖", "image", "png"],
    "畫": ["繪圖", "圖片", "畫圖", "生成圖", "漫畫", "分鏡"],
    "影片": ["視頻", "video", "短片", "剪輯", "mp4", "動畫", "運鏡"],
    "片": ["影片", "視頻", "video", "短片", "剪輯"],
    "聲音": ["語音", "配音", "tts", "音檔", "朗讀", "克隆"],
    "語音": ["tts", "配音", "朗讀", "錄音", "克隆", "voxcpm"],
    "抓": ["爬取", "爬蟲", "下載", "fetch", "擷取"],
    "網站": ["網頁", "web", "url", "http", "瀏覽器"],
    "登入": ["login", "登錄", "帳號", "登入狀態", "session"],
    "發文": ["發帖", "貼文", "post", "發佈", "排程"],
    "賺錢": ["接單", "報價", "客戶", "商機", "變現", "客服"],
    "客服": ["客服", "回覆", "訊息", "客戶"],
    "備份": ["backup", "還原", "快照", "備份中心"],
    "排程": ["cron", "定時", "每天", "定時任務"],
    "記憶": ["remember", "recall", "長期記憶", "知識庫"],
    "履歷": ["cv", "resume", "rendercv"],
    "素材": ["stock", "背景", "影片庫", "mixkit"],
    "上傳": ["upload", "發片", "發布", "youtube"],
    "分析圖": ["vision", "看圖", "截圖", "ocr"],
    "瀏覽器": ["browser", "chromium", "playwright", "gui"],
}

PLUGIN_INFO = {
    "command": "/find",
    "icon": "🔎",
    "handler": "handle_find",
    "description": "統一檢索入口：一個入口同時搜技能+工具，回候選卡與下一步指令",
    "intent_keywords": [("/找工具", "/find"), ("/find", "/find")],
    "tool_schema": {
        "name": "find",
        "description": (
            "【用途】找「哪個技能/工具能做某件事」的統一入口，回傳候選卡（名稱 / 一句話 / 為何匹配 / 下一步指令）。\n"
            "【何時用】① 不知道現成工具或技能叫什麼名字；② 你正想連續呼叫 skill list / skill search 時（先 find 再動手）；"
            "③ 某工具連續失敗 2 次，想換條路。\n"
            "【何時不用】已知工具名（直接呼叫）；純聊天；單純讀檔或單純跑指令。\n"
            "【例子】find(\"生成一張圖\") → 命中 技能 pollinations，下一步 skill view pollinations outline。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "用自然語言描述你要做的事，例如「生成一張圖」「把影片上傳 YouTube」「備份資料」"},
                "top": {"type": "integer", "description": "每個類別最多回幾張卡（預設 5）"},
            },
            "required": ["query"],
        },
    },
}


def _clip(s, n=90):
    s = re.sub(r"\s+", " ", s or "").strip()
    return s[:n].rstrip() + "…" if len(s) > n else s


def _norm(s):
    s = html.unescape(s or "").lower()
    return re.sub(r"[^\w\u4e00-\u9fff]+", "", s)


def _grams(text):
    q = _norm(text)
    out = list(re.findall(r"[a-z0-9_]{2,}", q))
    for seg in re.findall(r"[\u4e00-\u9fff]+", q):
        L = len(seg)
        for n in (4, 3, 2):
            if L >= n:
                for i in range(L - n + 1):
                    out.append(seg[i:i + n])
        if L <= 3:
            out.append(seg)
    return list(dict.fromkeys(out))


def _expand(query):
    base = _grams(query)
    nq = _norm(query)
    extra = []
    for k, vs in SYN.items():
        if k in nq:
            for v in vs:
                extra += _grams(v)
    extra = [g for g in dict.fromkeys(extra) if g not in base]
    # 同義擴充只當「加分項」（penalty 集），不當直接命中，避免把 PNG 當成生成圖片
    return base, set(extra)


def _learned_boost(name):
    try:
        with open(FEEDBACK, encoding="utf-8") as f:
            d = json.load(f)
        return float(((d.get("learned") or {}).get(name) or {}).get("count", 0))
    except Exception:
        return 0.0


def _skill_docs():
    docs = []
    try:
        import skill_index
        idx = skill_index.load_index()
    except Exception:
        idx = {"skills": []}
    for s in idx.get("skills", []):
        if s.get("stub"):
            continue
        docs.append({
            "kind": "技能",
            "name": s["name"],
            "desc": _clip(s.get("description") or s.get("title") or "", 100),
            "hay": _norm(" ".join([s["name"], s.get("title", ""), s.get("description", ""), " ".join(s.get("triggers") or [])])),
            "next": "skill view %s outline" % s["name"],
        })
    return docs


def _tool_docs():
    docs = []
    mods = None
    try:
        import tool_handler
        mods = tool_handler.get_tools()
    except Exception:
        mods = None
    if mods:
        for mname, mod in mods.items():
            info = getattr(mod, "PLUGIN_INFO", None)
            if not isinstance(info, dict):
                continue
            ts = info.get("tool_schema") or {}
            name = ts.get("name") or (info.get("command") or "").lstrip("/") or mname
            desc = ts.get("description") or info.get("description") or ""
            docs.append({"kind": "工具", "name": name, "desc": _clip(desc, 100),
                         "hay": _norm(name + " " + desc), "next": name})
            for sub in (info.get("sub_tools") or []):
                sname = sub.get("name")
                if sname:
                    sdesc = sub.get("description", "")
                    docs.append({"kind": "工具", "name": sname, "desc": _clip(sdesc, 100),
                                 "hay": _norm(sname + " " + sdesc), "next": sname})
    if not docs:
        docs = _tool_docs_fallback()
    return docs


def _tool_docs_fallback():
    docs = []
    try:
        for fn in os.listdir(TOOLS_DIR):
            if not fn.endswith(".py"):
                continue
            txt = open(os.path.join(TOOLS_DIR, fn), encoding="utf-8", errors="ignore").read()
            m = re.search(r'"name"\s*:\s*"([a-z_][\w]*)"', txt)
            d = re.search(r'"description"\s*:\s*"([^"]{12,400})"', txt)
            if m:
                docs.append({"kind": "工具", "name": m.group(1), "desc": _clip(d.group(1) if d else "", 100),
                             "hay": _norm(m.group(1) + " " + (d.group(1) if d else "")), "next": m.group(1)})
    except Exception:
        pass
    return docs


COV_STOP = set("的了我你他她要幫請嗎呢把個這那有是和就在都很會能可以一件做給來去過張想用")


def _qchars(query):
    return [c for c in dict.fromkeys(_norm(query)) if "\u4e00" <= c <= "\u9fff" and c not in COV_STOP]


def _score(doc, grams, penalty, qchars=()):
    hay, name_hay = doc["hay"], _norm(doc["name"])
    sc, hit, soft = 0, [], []
    for g in grams:
        if not g:
            continue
        if g in hay:
            w = len(g) ** 2
            if g in name_hay:
                w *= 3
            sc += w
            hit.append(g)
        elif g in penalty:
            sc += 1
            soft.append(g)
    hit = [h for h in hit if not any(h != o and h in o for o in hit)]
    if qchars:  # 覆蓋率加分：需求裡的字有多少真的出現在這張卡
        sc += sum(2 for c in qchars if c in hay)
    return sc, hit, soft


def run(query, top=5):
    if not query or not str(query).strip():
        return 'find：請給需求，例如 find("生成一張圖")'
    grams, penalty = _expand(str(query))
    qchars = _qchars(str(query))
    scored = []
    for d in _skill_docs() + _tool_docs():
        sc, hit, soft = _score(d, grams, penalty, qchars)
        # 只有聯想詞命中（沒有直接命中）時，要求至少 3 個聯想詞才收，避免雜訊洗版
        if sc <= 0 or (not hit and len(soft) < 3):
            continue
        if d["kind"] == "技能":
            b = min(_learned_boost(d["name"]), 5)
            if b:
                sc += int(b * 8)
                if "learned" not in hit:
                    hit = hit + ["（過往成功配對 x%d）" % int(b)]
        scored.append((sc, hit, d))
    scored.sort(key=lambda x: (-x[0], x[2]["kind"], x[2]["name"]))
    out = ['🔎 find：%s' % _clip(str(query), 60)]
    for kind in ("技能", "工具"):
        rows = [r for r in scored if r[2]["kind"] == kind][:top]
        if not rows:
            continue
        out.append("\n【%s】" % ("技能（命中直接用 skill view）" if kind == "技能" else "工具（命中直接呼叫）"))
        for i, (sc, hit, d) in enumerate(rows, 1):
            out.append("%d. %s  ★%d — %s" % (i, d["name"], sc, d["desc"]))
            out.append("   為何匹配：%s" % ("、".join(hit[:6]) if hit else "聯想詞相關"))
            out.append("   下一步：%s" % d["next"])
    with __import__("contextlib").suppress(Exception): __import__("route_feedback").hook(query, [d["name"] for _s, _h, d in scored if _s > 0][:top])
    if len(out) == 1:
        out.append("沒有明顯候選。換個說法再 find 一次（例如把「做一個動畫」改成「影片 生成 剪輯」），或用 skill list 看全部。")
    else:
        out.append("\n（挑第一個候選直接動手；確定了就用它的『下一步』，不要再多輪 list/search。）")
    return "\n".join(out)


async def handle_find(args, mode="command", user_id=None, agent_name=None, **kwargs):
    if isinstance(args, dict):
        q = args.get("query") or args.get("content") or ""
        top = args.get("top") or 5
    else:
        q = str(args or "")
        top = 5
    try:
        top = max(1, min(int(top), 10))
    except Exception:
        top = 5
    return run(q, top)


def naturalize_find_result(result):
    return result


if __name__ == "__main__":
    print(run(" ".join(sys.argv[1:]) or "生成一張圖"))
