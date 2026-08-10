#!/usr/bin/env python3
import os, json, hashlib, time
SKILL_DIR = os.path.expanduser("~/.mok/skill")

PLUGIN_INFO = {
    "command": "/skill",
    "icon": "📜",
    "handler": "handle_skill",
    "description": "技能系統：管理 .mok/skill/ 目錄中的技能文件",
    "intent_keywords": [
        ("/技能", "/skill list"),
        ("/skill", "/skill"),
        ("/技能列表", "/skill list"),
    ],
    "tool_schema": {
        "name": "skill",
        "description": "管理技能文件系統。技能是存放於 ~/.mok/skill/ 的 Markdown 文件。支援五個動作：list, view, search, create, delete。",
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
                    "description": "技能文件名稱（用於 view / create / delete）。例如 user.md"
                },
                "query": {
                    "type": "string",
                    "description": "搜尋關鍵詞（用於 search）。在所有 .md 文件中搜尋。"
                },
                "content": {
                    "type": "string",
                    "description": "技能文件內容（用於 create）。支援 Markdown 格式。"
                }
            },
            "required": ["action"]
        }
    }
}

def _list_skills():
    if not os.path.isdir(SKILL_DIR):
        return json.dumps({"error": "SKILL_DIR_NOT_FOUND: " + SKILL_DIR})
    files = sorted([f for f in os.listdir(SKILL_DIR) if f.endswith(".md") and os.path.isfile(os.path.join(SKILL_DIR, f))])
    if not files:
        return json.dumps({"skills": [], "message": "技能目錄為空", "dir": SKILL_DIR}, ensure_ascii=False)
    result = []
    for f in files:
        path = os.path.join(SKILL_DIR, f)
        result.append({"name": f, "size": os.path.getsize(path)})
    return json.dumps({"skills": result, "dir": SKILL_DIR, "total": len(result)}, ensure_ascii=False)

def _view_skill(filename):
    if not filename:
        return json.dumps({"error": "缺少文件名"})
    safe_name = os.path.basename(filename)
    path = os.path.join(SKILL_DIR, safe_name)
    if not os.path.isfile(path) and not safe_name.endswith(".md"):
        path_md = os.path.join(SKILL_DIR, safe_name + ".md")
        if os.path.isfile(path_md):
            path = path_md
            safe_name = safe_name + ".md"
    if not os.path.isfile(path):
        return json.dumps({"error": f"技能文件不存在: {safe_name}"})
    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        return json.dumps({"filename": safe_name, "content": content, "size": len(content)}, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"error": str(e)})

def _search_skills(query):
    if not query:
        return json.dumps({"error": "缺少搜尋關鍵詞"})
    if not os.path.isdir(SKILL_DIR):
        return json.dumps({"error": "SKILL_DIR_NOT_FOUND"})
    results = []
    files = sorted([f for f in os.listdir(SKILL_DIR) if f.endswith(".md") and os.path.isfile(os.path.join(SKILL_DIR, f))])
    ql = query.lower()
    for f in files:
        path = os.path.join(SKILL_DIR, f)
        try:
            with open(path, "r", encoding="utf-8") as fh:
                content = fh.read()
            if ql in content.lower():
                lines = content.split("\\n")
                matched = []
                for i, line in enumerate(lines):
                    if ql in line.lower():
                        s = max(0, i-2)
                        e = min(len(lines), i+3)
                        matched.append({"line": i+1, "context": "\\n".join(lines[s:e])})
                results.append({"file": f, "matches": len(matched), "snippets": matched[:5]})
        except:
            continue
    if not results:
        return json.dumps({"query": query, "results": [], "message": "無匹配結果"}, ensure_ascii=False)
    return json.dumps({"query": query, "results": results, "total_files": len(results)}, ensure_ascii=False)

def _create_skill(filename, content):
    if not filename or not content:
        return json.dumps({"error": "缺少 filename 或 content"})
    safe_name = os.path.basename(filename)
    if not safe_name.endswith(".md"):
        safe_name += ".md"
    path = os.path.join(SKILL_DIR, safe_name)
    if os.path.exists(path):
        return json.dumps({"error": "技能已存在: " + safe_name})
    token = hashlib.md5((safe_name + str(time.time())).encode()).hexdigest()[:8]
    tmp = "/tmp/skill_create_" + token + ".json"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"filename": safe_name, "content": content, "path": path}, f, ensure_ascii=False)
    return "CONFIRM_SPLIT:建立技能 " + safe_name + " (" + str(len(content)) + " 字符)\n---CONFIRM_SPLIT---\n/admin confirm " + token

def _delete_skill(filename):
    if not filename:
        return json.dumps({"error": "缺少文件名"})
    safe_name = os.path.basename(filename)
    if not safe_name.endswith(".md"):
        safe_name += ".md"
    path = os.path.join(SKILL_DIR, safe_name)
    if not os.path.isfile(path):
        return json.dumps({"error": "技能不存在: " + safe_name})
    token = hashlib.md5((safe_name + str(time.time())).encode()).hexdigest()[:8]
    tmp = "/tmp/skill_delete_" + token + ".json"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"filename": safe_name, "path": path}, f, ensure_ascii=False)
    size = os.path.getsize(path)
    return "CONFIRM_SPLIT:刪除技能 " + safe_name + " (" + str(size) + " bytes) - 不可復原\n---CONFIRM_SPLIT---\n/admin confirm " + token

async def handle_skill(args, mode="command", user_id=None, agent_name=None, **kwargs):
    if mode == "function":
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
            return _view_skill(rest)
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
        return _view_skill(filename)
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
