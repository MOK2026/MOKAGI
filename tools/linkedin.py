# ------------------------------------------------------------------------------------ #
# 工具名稱: linkedin (LinkedIn 經營 / 讀寫)
# 用途: 以 Playwright 驅動此侍女專屬的 Chromium profile（browser_profiles/<侍女名>；舊版向後相容 browser_profile2），
#       提供 LinkedIn 發文、讀動態、看個人檔案等操作（路線 B：真實瀏覽器）。
# 主要函數: handle_linkedin(args, chat_id, agent_config)
# 依賴: 系統 python3 + playwright（已裝）
# 實作: 在獨立執行緒內跑 sync Playwright，避免與 browser 工具的 asyncio 迴圈衝突。
# 帳號: Google 帳號 mok20260316ci@gmail.com 登入（見 skill/linkedin）
# 更新記錄: 20260914 - 初版 status/feed/post/profile
#          20260917 - 改用侍女專屬 profile（依 agent_config.MOK_AGENT_NAME 推導）
# ------------------------------------------------------------------------------------ #

import os
import re
import sys
import json
import threading

MOK = os.path.join(os.path.expanduser("~"), ".mok")
LI_DIR = os.path.join(MOK, "agent", "領妹", "linkedin_libs")
if LI_DIR not in sys.path:
    sys.path.insert(0, LI_DIR)

import mok_profile as mok_profile  # 全系統統一的 profile 推導（單一真相來源）

_TIMEOUT = 200


def _agent_profile_name(agent_config):
    """由呼叫端侍女推導 profile 名稱（統一交由 mok_profile 模組，單一真相來源）。
    無可用名稱時回傳空字串（沿用「以侍女專屬 profile 為主、否則回退共用」語意）。"""
    name = mok_profile.agent_profile_name(agent_config)
    return "" if name == "default" else name


def _agent_profile_dir(agent_config, explicit=None):
    """回傳此侍女專屬的 Chromium profile 目錄（統一由 mok_profile 推導）。
    不存在時自動從 browser_profile2 複製種子（含 LinkedIn 登入狀態）。"""
    return mok_profile.resolve_profile_dir(explicit=explicit, agent_config=agent_config)


def _call(action, arg="", publish=False, video="", profile_dir=None):
    result = {}

    def work():
        try:
            import importlib.util as _ilu
            _spec = _ilu.spec_from_file_location("li_web_fresh", os.path.join(LI_DIR, "li_web.py"))
            li_web = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(li_web)
            from playwright.sync_api import sync_playwright
            with sync_playwright() as p:
                ctx = li_web._ctx(p, profile_dir)
                page = ctx.new_page()
                try:
                    if action == "status":
                        result.update(li_web.op_status(page))
                    elif action == "feed":
                        result.update(li_web.op_feed(page, arg or 5))
                    elif action == "post":
                        result.update(li_web.op_post(page, arg, publish=publish))
                    elif action == "post_video":
                        result.update(li_web.op_post_video(page, arg, video, publish=publish))
                    elif action == "profile":
                        result.update(li_web.op_profile(page, arg))
                    elif action == "like":
                        result.update(li_web.op_like_feed(page, int(arg) if str(arg).isdigit() else 8, dry=not publish))
                    elif action == "discover":
                        result.update(li_web.op_discover(page, arg))
                    elif action == "follow":
                        result.update(li_web.op_follow(page, arg, dry=not publish))
                    elif action == "comment":
                        result.update(li_web.op_comment_feed(page, arg, dry=not publish))
                    else:
                        result.update({"success": False, "error": "未知 action: %s" % action})
                finally:
                    ctx.close()
        except Exception as e:
            result.update({"success": False, "error": repr(e)})

    t = threading.Thread(target=work, daemon=True)
    t.start()
    t.join(timeout=_TIMEOUT)
    if t.is_alive():
        return {"success": False, "error": "執行逾時（%ss）" % _TIMEOUT}
    return result or {"success": False, "error": "無結果"}


def handle_linkedin(args, chat_id=None, agent_config=None):
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except Exception:
            args = {"action": "status"}
    if not isinstance(args, dict):
        args = {"action": "status"}

    action = (args.get("action") or "status").strip().lower()
    text = args.get("text") or args.get("content") or ""
    publish = bool(args.get("publish"))
    video = args.get("video") or args.get("video_path") or ""

    # 使用此侍女專屬的瀏覽器 profile（可用 args.profile 明確覆寫）
    pdir = _agent_profile_dir(agent_config, args.get("profile"))

    if action in ("status", "check"):
        r = _call("status", profile_dir=pdir)
    elif action == "feed":
        r = _call("feed", args.get("count") or 5, profile_dir=pdir)
    elif action in ("post", "publish"):
        if not text:
            r = {"success": False, "error": "缺少 text（要發佈的內容）"}
        elif video:
            r = _call("post_video", text, publish=publish, video=video, profile_dir=pdir)
        else:
            r = _call("post", text, publish=publish, profile_dir=pdir)
    elif action in ("profile", "get_profile"):
        r = _call("profile", args.get("public_id") or args.get("url") or "", profile_dir=pdir)
    elif action == "like":
        r = _call("like", args.get("count") or args.get("text") or "", publish=publish, profile_dir=pdir)
    elif action == "discover":
        r = _call("discover", args.get("text") or args.get("query") or args.get("keywords") or args.get("public_id") or "", profile_dir=pdir)
    elif action == "follow":
        r = _call("follow", args.get("text") or args.get("url") or args.get("public_id") or "", publish=publish, profile_dir=pdir)
    elif action == "comment":
        r = _call("comment", args.get("text") or "", publish=publish, profile_dir=pdir)
    else:
        r = {"success": False, "error": "未知 action: %s" % action}

    return json.dumps(r, ensure_ascii=False)


def naturalize_linkedin(user_text="", raw_result="", ollama_api="", model_name="", temp_msg=None, context=None):
    result = raw_result
    try:
        d = json.loads(result) if isinstance(result, str) else result
    except Exception:
        return str(result)
    if not isinstance(d, dict):
        return str(d)
    if not d.get("success"):
        return "❌ LinkedIn 操作失敗：%s" % d.get("error", d)
    if d.get("logged_in") is not None:
        return "✅ LinkedIn 登入狀態：%s（%s）" % ("已登入" if d["logged_in"] else "未登入", d.get("title", ""))
    if d.get("dry_run"):
        return "📝 已填入貼文框（未發佈，dry-run）：\n%s" % d.get("text", "")
    if d.get("posted"):
        return "✅ 已發佈 LinkedIn 貼文：\n%s" % d.get("text", "")
    if d.get("posts") is not None:
        return "📰 LinkedIn 動態（%d 則）:\n%s" % (d.get("count", 0), "\n---\n".join(d.get("posts", []))[:2000])
    if d.get("liked") is not None:
        rows = ["- %s：%s" % (x.get("author") or "?", (x.get("text") or "").replace("\n", " ")[:60]) for x in d.get("liked", [])]
        return "👍 按讚 %d 則（%s）：\n%s" % (d.get("count", 0), "dry-run" if d.get("dry_run") else "已執行", "\n".join(rows)[:1500])
    if d.get("followed") is not None:
        rows = ["- %s %s" % ("OK" if x.get("ok") else "FAIL", x.get("target")) for x in d.get("followed", [])]
        return "➕ 關注結果（%s）：\n%s" % ("dry-run" if d.get("dry_run") else "已執行", "\n".join(rows))
    if d.get("comments") is not None:
        rows = ["- %s：「%s」%s" % (x.get("author") or "?", x.get("comment", ""), "OK" if x.get("commented") else ("dry" if x.get("dry") else "FAIL " + str(x.get("error", "")))) for x in d.get("comments", [])]
        return "💬 留言 %d 則（%s）：\n%s" % (d.get("count", 0), "dry-run" if d.get("dry_run") else "已執行", "\n".join(rows)[:1500])
    if d.get("urls") is not None:
        return "🔎 找到 %d 人：\n%s" % (d.get("count", 0), "\n".join(d.get("urls", []))[:1500])
    return json.dumps(d, ensure_ascii=False)[:2000]


# ========== PLUGIN_INFO ==========
PLUGIN_INFO = {
    "command": "/linkedin",
    "icon": "in",
    "handler": "handle_linkedin",
    "naturalize_func": "naturalize_linkedin",
    "description": "LinkedIn 經營工具（路線B，真實瀏覽器）：檢查登入、讀動態、發文、看個人檔案。",
    "intent_keywords": [
        ("linkedin", "/linkedin"),
        ("/li", "/linkedin"),
        ("領英", "/linkedin"),
        ("linkedin 發文", "/linkedin post"),
        ("linkedin 動態", "/linkedin feed"),
    ],
    "tool_schema": {
        "name": "linkedin",
        "description": (
            "LinkedIn 經營工具（路線B：用已登入的 Chromium 真實瀏覽器操作）。\n\n"
            "【功能】\n"
            "- status: 檢查 LinkedIn 登入狀態\n"
            "- feed: 讀取首頁動態（可選 count）\n"
            "- post: 發佈貼文（需要 text；可加 video 附上影片；publish=false 時只填不發）\n"
            "- profile: 讀取個人檔案頁（需要 public_id 或 url）\n"
            "- like: 對首頁動態按讚（讚好），arg=數量（預設8），publish=true 才真的按\n"
            "- discover: 以關鍵字搜尋人物，arg=關鍵字，回傳個人檔案 URL\n"
            "- follow: 關注帳號，arg=以逗號分隔的網址或 id，publish=true 才真的關注\n"
            "- comment: 對首頁動態留言，arg=JSON 陣列留言文字，publish=true 才真的留言\n\n"
            "【返回格式】成功 {\"success\": true, ...}；失敗 {\"success\": false, \"error\": \"...\"}\n\n"
            "【注意】使用持久化 profile browser_profile2，需保持已登入。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["status", "feed", "post", "profile", "post_video", "like", "discover", "follow", "comment"],
                    "description": "要執行的動作",
                },
                "text": {"type": "string", "description": "貼文內容（post 用）"},
                "video": {"type": "string", "description": "影片檔案路徑（post 用，附上即改為影片貼文）"},
                "count": {"type": "integer", "description": "讀取動態則數（feed 用，預設 5）"},
                "public_id": {"type": "string", "description": "LinkedIn public id 或 url（profile 用）"},
                "publish": {"type": "boolean", "description": "post 時是否真的發佈（預設 false）"},
            },
            "required": ["action"],
        },
    },
}
