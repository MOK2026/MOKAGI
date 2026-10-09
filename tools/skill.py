#!/usr/bin/env python3
import os, json, hashlib, time, shutil
SKILL_DIR = os.path.expanduser("~/.mok/skill")

PLUGIN_INFO = {
    "command": "/skill",
    "icon": "📜",
    "handler": "handle_skill",
    "description": "技能系統：每個技能一個資料夾（.mok/skill/<技能>/），說明文件為 README.md",
    "intent_keywords": [
        ("/技能", "/skill list"),
        ("/skill", "/skill"),
        ("/技能列表", "/skill list"),
    ],
    "tool_schema": {
        "name": "skill",
        "description": "管理技能文件系統（.mok/skill/<技能名>/README.md）。\n【何時用】① 系統提示的「技能索引」有命中 → 直接 skill view <name> outline；② 索引沒命中、find() 也沒結果 → skill search <關鍵詞>；③ 要新建技能 → create / delete。\n【何時不用】已知有哪個技能就直接 view，不要一輪輪 list 亂翻；技能正文很長，不要一開始就 full。\n【漸進披露】先 outline 看大綱 → section N 只讀需要的那節 → 真的必要才 full。\n【例子】skill view pollinations outline / skill view pollinations section 3。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["list", "view", "search", "create", "delete"],
                    "description": "動作：list(列出所有技能), view(查看指定技能內容), search(搜尋技能), create(建立新技能), delete(刪除技能)"
                },
                "filename": {
                    "type": "string",
                    "description": "技能名稱（用於 view / create / delete）。例如 gmail（對應 .mok/skill/gmail/README.md）"
                },
                "query": {
                    "type": "string",
                    "description": "搜尋關鍵詞（用於 search）。在所有技能資料夾的 README.md 中搜尋。"
                },
                "content": {
                    "type": "string",
                    "description": "技能文件內容（用於 create）。支援 Markdown 格式。"
                },
                "mode": {
                    "type": "string",
                    "enum": ["outline", "section", "full"],
                    "description": "view 的模式：outline=只回標題大綱（最快，預設先用這個）, section=只回某一節, full=整份 README（很長，非必要不用）"
                },
                "section": {
                    "type": "integer",
                    "description": "view + mode=section 時的節號（先 outline 看有哪幾節）"
                }
            },
            "required": ["action"]
        }
    }
}

def _skill_dir(name):
    """標準化技能名 → (技能名, 資料夾路徑)。去掉 .md 後綴。"""
    name = os.path.basename(str(name).strip())
    if name.endswith(".md"):
        name = name[:-3]
    return name, os.path.join(SKILL_DIR, name)

def _list_skills():
    if not os.path.isdir(SKILL_DIR):
        return json.dumps({"error": "SKILL_DIR_NOT_FOUND: " + SKILL_DIR})
    skills = []
    # 掃描所有子目錄 = 技能資料夾
    for entry in sorted(os.listdir(SKILL_DIR)):
        path = os.path.join(SKILL_DIR, entry)
        if os.path.isdir(path):
            readme = os.path.join(path, "README.md")
            info = {"name": entry, "dir": path}
            if os.path.isfile(readme):
                info["readme"] = True
                info["size"] = os.path.getsize(readme)
            else:
                info["readme"] = False
                info["size"] = 0
            skills.append(info)
        elif entry.endswith(".md") and os.path.isfile(path) and entry != "README.md":
            # 防呆：頂層遺留 .md（新機制下不應存在）
            # 例外：頂層 README.md 是技能系統本身的說明（唯一來源公告），不算 legacy
            skills.append({"name": entry, "legacy": True, "size": os.path.getsize(path)})
    if not skills:
        return json.dumps({"skills": [], "message": "技能目錄為空", "dir": SKILL_DIR}, ensure_ascii=False)
    # P0：補上 description / triggers（取自 ~/.mok/skill/_index.json），失敗不影響主流程
    try:
        import skill_index as _si
        dmap = {x.get("name"): x for x in _si.load_index().get("skills", [])}
        for info in skills:
            x = dmap.get(info.get("name")) or {}
            if x.get("description"):
                info["description"] = x["description"]
            if x.get("triggers"):
                info["triggers"] = x["triggers"]
            if x.get("stub"):
                info["stub"] = True
    except Exception as e:
        import logging
        logging.warning("skill list 補 description 失敗: %s" % e)
    return json.dumps({"skills": skills, "dir": SKILL_DIR, "total": len(skills)}, ensure_ascii=False)

def _parse_view_arg(rest):
    """把 'pollinations outline' / 'pollinations section 3' 拆成 (name, mode, section)。"""
    rest = (rest or "").strip()
    mode, section = "full", None
    if not rest:
        return "", mode, section
    parts = rest.split()
    low = parts[-1].lower()
    if low in ("outline", "full"):
        mode = low
        parts = parts[:-1]
    elif len(parts) >= 2 and parts[0].lower() == "section":
        mode, section, parts = "section", parts[1], parts[2:]
    elif len(parts) >= 3 and parts[-2].lower() == "section":
        mode, section, parts = "section", parts[-1], parts[:-2]
    return " ".join(parts), mode, section


def _view_skill(filename, mode="full", section=None):
    with __import__("contextlib").suppress(Exception): __import__("route_feedback").record_use(str(filename).strip().split(" ")[0].replace(".md", ""))
    if not filename:
        return json.dumps({"error": "缺少技能名稱"})
    if mode == "full":
        nm, md, sec = _parse_view_arg(filename)
        if md != "full":
            filename, mode, section = nm, md, sec
    name, d = _skill_dir(filename)
    path = os.path.join(d, "README.md")
    if not os.path.isdir(d) or not os.path.isfile(path):
        legacy = os.path.join(SKILL_DIR, name + ".md")
        if os.path.isfile(legacy):
            path = legacy
        else:
            return json.dumps({"error": f"技能不存在: {name}（應為 {name}/README.md）"})
    mode = (mode or "full").lower()
    if mode in ("outline", "section"):
        try:
            import skill_index as _si
            if mode == "outline":
                return _si.outline_str(name)
            try:
                secn = int(section) if section is not None else 1
            except Exception:
                secn = 1
            return _si.section_str(name, secn)
        except Exception as e:
            import logging
            logging.warning("skill_index 不可用，退回全文: %s" % e)
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return json.dumps({"filename": name + "/README.md", "content": content, "size": len(content)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})

def _search_skills(query):
    if not query:
        return json.dumps({"error": "缺少搜尋關鍵詞"})
    if not os.path.isdir(SKILL_DIR):
        return json.dumps({"error": "SKILL_DIR_NOT_FOUND: " + SKILL_DIR})
    ql = query.lower()
    results = []
    # 搜索每個技能資料夾下的 .md 文件（以 README.md 為主）
    for entry in sorted(os.listdir(SKILL_DIR)):
        d = os.path.join(SKILL_DIR, entry)
        if not os.path.isdir(d):
            continue
        md_files = sorted([f for f in os.listdir(d) if f.endswith(".md") and os.path.isfile(os.path.join(d, f))])
        if not md_files:
            continue
        for f in md_files:
            path = os.path.join(d, f)
            try:
                with open(path, "r", encoding="utf-8") as fh:
                    content = fh.read()
                if ql in content.lower():
                    lines = content.split("\n")
                    matched = []
                    for i, line in enumerate(lines):
                        if ql in line.lower():
                            s = max(0, i - 2)
                            e = min(len(lines), i + 3)
                            matched.append({"line": i + 1, "context": "\n".join(lines[s:e])})
                    results.append({"skill": entry, "file": f, "matches": len(matched), "snippets": matched[:5]})
            except:
                continue
    if not results:
        return json.dumps({"query": query, "results": [], "total_files": 0}, ensure_ascii=False)
    return json.dumps({"query": query, "results": results, "total_files": len(results)}, ensure_ascii=False)

def _create_skill(filename, content):
    if not filename or not content:
        return json.dumps({"error": "缺少 filename 或 content"})
    name, d = _skill_dir(filename)
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, "README.md")
    if os.path.exists(path):
        return json.dumps({"error": "技能已存在: " + name + "（" + path + "）"})
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    return json.dumps({"success": True, "message": "✅ 技能已建立: " + name, "path": path, "size": len(content)}, ensure_ascii=False)

def _delete_skill(filename):
    if not filename:
        return json.dumps({"error": "缺少技能名稱"})
    name, d = _skill_dir(filename)
    if os.path.isdir(d):
        shutil.rmtree(d)
        return json.dumps({"success": True, "message": "✅ 技能已刪除: " + name + "（整個資料夾）"}, ensure_ascii=False)
    legacy = os.path.join(SKILL_DIR, name + ".md")
    if os.path.isfile(legacy):
        os.remove(legacy)
        return json.dumps({"success": True, "message": "✅ 技能已刪除: " + name}, ensure_ascii=False)
    return json.dumps({"error": "技能不存在: " + name})

async def handle_skill(args, mode="command", user_id=None, agent_name=None, **kwargs):
    if isinstance(args, dict):
        action = args.get("action", "list")
        filename = args.get("filename", "")
        query = args.get("query", "")
        content = args.get("content", "")
    else:
        parts = args.split(maxsplit=2) if args else []
        if not parts:
            return _list_skills()
        action = parts[0].lower()
        rest = parts[1] if len(parts) > 1 else ""
        extra = parts[2] if len(parts) > 2 else ""
        if action == "list":
            return _list_skills()
        elif action == "view":
            nm, md, sec = _parse_view_arg(rest)
            return _view_skill(nm, md, sec)
        elif action == "search":
            return _search_skills(rest)
        elif action == "create":
            return _create_skill(rest, extra)
        elif action == "delete":
            return _delete_skill(rest)
        else:
            return json.dumps({"error": "未知動作: " + action + "，支援: list, view, search, create, delete"}, ensure_ascii=False)
    if action == "list":
        return _list_skills()
    elif action == "view":
        return _view_skill(filename,
                           args.get("mode", "full") if isinstance(args, dict) else "full",
                           args.get("section") if isinstance(args, dict) else None)
    elif action == "search":
        return _search_skills(query)
    elif action == "create":
        return _create_skill(filename, content)
    elif action == "delete":
        return _delete_skill(filename)
    else:
        return json.dumps({"error": "未知動作: " + action}, ensure_ascii=False)

def naturalize_skill_result(result):
    try:
        data = json.loads(result)
        if isinstance(data, dict) and data.get("error"):
            return "技能操作失敗: " + data["error"]
    except:
        pass
    return result
