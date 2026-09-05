# ------------------------------------------------------------------------------------ #
# 工具名稱: instagram (Instagram 自動發帖)
# 用途: 使用 instagrapi 私有 API，實現登入、發帖、排程等功能。
#
# 主要函數:
#   handle_instagram(args, chat_id, agent_config)
#       - 入口函數，處理 /instagram 命令或 LLM 工具調用。
#       - 支援 action: login, post, schedule, status, logout
#
# 依賴: instagrapi (pip install instagrapi)
#
# 配置檔: ~/.mok/agent/<agent>/instagram_config.json
#   儲存 IG 帳號、session、排程任務等（<agent> 為當前 agent，動態取得）
# 帳號密碼來源: ~/.mok/agent/<agent>/.<agent> 中的 MOK_instagram_ac / MOK_instagram_pw
#
# 更新記錄:
#   20260727 - 初版，支援登入、發圖片貼文、狀態查詢
# ------------------------------------------------------------------------------------ #

import os
import sys
import json
import logging
import hashlib
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------- 可選依賴 ----------
try:
    from instagrapi import Client
    from instagrapi.exceptions import LoginRequired, ChallengeRequired
    HAS_INSTAGRAPI = True
except ImportError:
    HAS_INSTAGRAPI = False

# ---------- 配置路徑 ----------
# 動態取得當前 agent 目錄（~/.mok/agent/<agent>/），不再硬編碼單一 agent。
# 由 handle_instagram 進入時設置 _AGENT_CONFIG（agent_config），
# IG 帳號密碼從 ~/.mok/agent/<agent>/.<agent> 讀取 MOK_instagram_ac / MOK_instagram_pw。
_AGENT_CONFIG = {}


def _get_agent_name():
    """取得當前 agent 名稱"""
    if _AGENT_CONFIG:
        name = _AGENT_CONFIG.get("MOK_AGENT_NAME")
        if name:
            return name
    return os.environ.get("MOK_AGENT_NAME", "衍")


def _get_agent_dir():
    """動態取得當前 agent 的目錄（如 ~/.mok/agent/懂王）"""
    return Path(os.path.expanduser(f"~/.mok/agent/{_get_agent_name()}"))


def _get_config_path():
    """當前 agent 的 instagram 配置檔路徑"""
    return _get_agent_dir() / "instagram_config.json"


def _get_session_dir():
    """當前 agent 的 session 目錄（不存在則建立）"""
    session_dir = _get_agent_dir() / "instagram_sessions"
    session_dir.mkdir(parents=True, exist_ok=True)
    return session_dir


def _load_agent_env():
    """從 ~/.mok/agent/<agent>/.<agent> 讀取環境配置（鍵=值格式，跳過 # 註解與空行）"""
    config_file = _get_agent_dir() / f".{_get_agent_name()}"
    env = {}
    if config_file.exists():
        with open(config_file, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _get_default_credentials():
    """從 agent 配置文件讀取默認 IG 帳號密碼（MOK_instagram_ac / MOK_instagram_pw）"""
    env = _load_agent_env()
    return env.get("MOK_instagram_ac"), env.get("MOK_instagram_pw")


def _load_config():
    """載入配置（使用當前 agent 目錄下的 instagram_config.json）"""
    config_path = _get_config_path()
    if config_path.exists():
        with open(config_path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"accounts": {}, "scheduled_posts": [], "post_history": []}


def _save_config(config):
    """儲存配置（到當前 agent 目錄）"""
    config_path = _get_config_path()
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w", encoding="utf-8") as f:
        json.dump(config, f, ensure_ascii=False, indent=2)


def _get_session_path(username: str) -> Path:
    """取得 session 檔案路徑（用 hash 避免特殊字元）"""
    h = hashlib.md5(username.encode()).hexdigest()[:12]
    return _get_session_dir() / f"session_{h}.json"


# ---------- Client 管理 ----------
def _create_client(username: str = None) -> Client:
    """建立 IG client，若有 session 則嘗試載入"""
    cl = Client()
    cl.delay_range = [1, 3]  # 模擬人類操作的延遲
    
    if username:
        session_path = _get_session_path(username)
        if session_path.exists():
            try:
                cl.load_settings(session_path)
                logger.info(f"已載入 {username} 的 session")
            except Exception as e:
                logger.warning(f"載入 session 失敗: {e}")
    
    return cl


def _save_session(cl: Client, username: str):
    """保存 session"""
    session_path = _get_session_path(username)
    cl.dump_settings(session_path)
    logger.info(f"已保存 {username} 的 session 到 {session_path}")


# ---------- Actions ----------
def _action_login(args: dict, config: dict) -> dict:
    """登入 Instagram"""
    username = args.get("username") or args.get("account")
    password = args.get("password")
    
    if not username or not password:
        # 優先從 agent 配置文件讀取默認帳號密碼（.mok/agent/<agent>/.<agent> 的 MOK_instagram_ac / MOK_instagram_pw）
        env_ac, env_pw = _get_default_credentials()
        username = username or env_ac
        password = password or env_pw
    
    if not username or not password:
        # 再嘗試從 config 讀取
        if username and username in config.get("accounts", {}):
            password = config["accounts"][username].get("password")
        else:
            return {"success": False, "error": "請提供 username 和 password（或檢查 agent 配置 MOK_instagram_ac / MOK_instagram_pw）"}
    
    try:
        cl = _create_client()
        cl.login(username, password)
        
        # 保存 session
        _save_session(cl, username)
        
        # 保存帳號資訊
        if "accounts" not in config:
            config["accounts"] = {}
        config["accounts"][username] = {
            "password": password,  # TODO: 考慮加密儲存
            "last_login": datetime.now().isoformat(),
        }
        _save_config(config)
        
        user_info = cl.user_info(cl.user_id)
        return {
            "success": True,
            "message": f"✅ 登入成功！歡迎 @{user_info.username}（{user_info.full_name}）",
            "username": user_info.username,
            "user_id": str(cl.user_id),
            "followers": user_info.follower_count,
            "following": user_info.following_count,
        }
    except ChallengeRequired as e:
        return {"success": False, "error": f"⚠️ 需要驗證（Challenge Required）。請先在手機 IG App 中確認登入請求，或到 Instagram 網站完成驗證後再試。"}
    except Exception as e:
        return {"success": False, "error": f"登入失敗: {str(e)}"}


def _action_post(args: dict, config: dict) -> dict:
    """發佈貼文（圖片）"""
    username = args.get("username") or args.get("account")
    image_path = args.get("image") or args.get("image_path") or args.get("photo")
    caption = args.get("caption") or ""
    
    if not username:
        # 使用 agent 配置文件中的默認帳號（MOK_instagram_ac）
        env_ac, _ = _get_default_credentials()
        username = env_ac
    
    if not username:
        return {"success": False, "error": "請提供 username（IG 帳號）"}
    if not image_path:
        return {"success": False, "error": "請提供 image_path（圖片檔案路徑）"}
    
    # 檢查圖片是否存在
    img = Path(image_path)
    if not img.exists():
        return {"success": False, "error": f"圖片不存在: {image_path}"}
    
    try:
        cl = _create_client(username)
        
        # 嘗試用 session 登入
        session_path = _get_session_path(username)
        if session_path.exists():
            cl.load_settings(session_path)
            try:
                cl.get_timeline_feed()  # 驗證 session 是否有效
            except LoginRequired:
                # session 過期，需要重新登入
                if username in config.get("accounts", {}):
                    pwd = config["accounts"][username].get("password")
                    if pwd:
                        cl.login(username, pwd)
                        _save_session(cl, username)
                    else:
                        return {"success": False, "error": f"Session 過期且 config 中無密碼，請重新 /instagram login"}
                else:
                    return {"success": False, "error": f"Session 過期，請重新 /instagram login"}
        else:
            # 沒有 session，嘗試用 config 密碼登入
            if username in config.get("accounts", {}):
                pwd = config["accounts"][username].get("password")
                if pwd:
                    cl.login(username, pwd)
                    _save_session(cl, username)
                else:
                    return {"success": False, "error": f"找不到密碼，請先 /instagram login"}
            else:
                return {"success": False, "error": f"找不到帳號 {username}，請先 /instagram login"}
        
        # 判斷檔案類型：影片（Reels）或圖片
        is_video = str(img).lower().endswith(('.mp4', '.mov', '.m4v', '.mkv', '.webm', '.avi'))
        if is_video:
            # 上傳為 Reels（clip_upload）
            media = cl.clip_upload(
                path=str(img),
                caption=caption,
            )
        else:
            # 上傳圖片
            media = cl.photo_upload(
                path=str(img),
                caption=caption,
            )
        
        # 記錄發帖歷史
        if "post_history" not in config:
            config["post_history"] = []
        config["post_history"].append({
            "username": username,
            "media_id": str(media.id),
            "code": media.code,
            "caption": caption[:100],
            "image": str(img),
            "time": datetime.now().isoformat(),
        })
        _save_config(config)
        
        return {
            "success": True,
            "message": f"✅ 發帖成功！",
            "url": f"https://www.instagram.com/p/{media.code}/",
            "media_id": str(media.id),
            "code": media.code,
            "caption_preview": caption[:200],
        }
    except Exception as e:
        return {"success": False, "error": f"發帖失敗: {str(e)}"}


def _action_status(args: dict, config: dict) -> dict:
    """查看登入狀態"""
    username = args.get("username") or args.get("account")
    
    if username:
        # 查看特定帳號狀態
        session_path = _get_session_path(username)
        has_session = session_path.exists()
        in_config = username in config.get("accounts", {})
        
        result = {
            "username": username,
            "has_session": has_session,
            "in_config": in_config,
        }
        
        if has_session:
            try:
                cl = _create_client(username)
                cl.load_settings(session_path)
                cl.get_timeline_feed()
                result["session_valid"] = True
                user_info = cl.user_info(cl.user_id)
                result["full_name"] = user_info.full_name
                result["followers"] = user_info.follower_count
            except:
                result["session_valid"] = False
        
        return {"success": True, **result}
    else:
        # 列出所有帳號
        accounts = []
        for uname, info in config.get("accounts", {}).items():
            session_path = _get_session_path(uname)
            accounts.append({
                "username": uname,
                "last_login": info.get("last_login", "未知"),
                "has_session": session_path.exists(),
            })
        
        post_count = len(config.get("post_history", []))
        return {
            "success": True,
            "accounts": accounts,
            "total_accounts": len(accounts),
            "total_posts": post_count,
        }


def _action_logout(args: dict, config: dict) -> dict:
    """登出"""
    username = args.get("username") or args.get("account")
    if not username:
        env_ac, _ = _get_default_credentials()
        username = env_ac
    if not username:
        return {"success": False, "error": "請提供 username"}
    
    session_path = _get_session_path(username)
    if session_path.exists():
        session_path.unlink()
    
    return {"success": True, "message": f"✅ 已登出 {username}，session 已清除"}


def _action_schedule(args: dict, config: dict) -> dict:
    """排程發帖（儲存到 config，由 cron 定期執行）"""
    action = args.get("schedule_action") or args.get("sa") or "add"
    
    if action == "list":
        posts = config.get("scheduled_posts", [])
        return {
            "success": True,
            "scheduled_posts": posts,
            "count": len(posts),
        }
    
    elif action == "add":
        username = args.get("username") or args.get("account")
        image_path = args.get("image") or args.get("image_path") or args.get("photo")
        caption = args.get("caption") or ""
        schedule_time = args.get("time") or args.get("schedule_time") or args.get("at")
        
        if not all([username, image_path, schedule_time]):
            return {"success": False, "error": "請提供 username, image_path, time（格式: YYYY-MM-DD HH:MM）"}
        
        if "scheduled_posts" not in config:
            config["scheduled_posts"] = []
        
        post = {
            "id": len(config["scheduled_posts"]) + 1,
            "username": username,
            "image": image_path,
            "caption": caption,
            "scheduled_time": schedule_time,
            "status": "pending",
            "created_at": datetime.now().isoformat(),
        }
        config["scheduled_posts"].append(post)
        _save_config(config)
        
        return {"success": True, "message": f"✅ 已排程: {schedule_time} 發帖到 @{username}", "post": post}
    
    elif action == "delete":
        post_id = args.get("id") or args.get("post_id")
        if not post_id:
            return {"success": False, "error": "請提供 id（排程編號）"}
        
        try:
            post_id = int(post_id)
        except ValueError:
            return {"success": False, "error": "id 必須是數字"}
        
        posts = config.get("scheduled_posts", [])
        new_posts = [p for p in posts if p.get("id") != post_id]
        if len(new_posts) == len(posts):
            return {"success": False, "error": f"找不到排程 #{post_id}"}
        
        config["scheduled_posts"] = new_posts
        _save_config(config)
        return {"success": True, "message": f"✅ 已刪除排程 #{post_id}"}
    
    else:
        return {"success": False, "error": f"未知的 schedule_action: {action}，可用: list, add, delete"}


# ---------- 歷史查詢 ----------
def _action_history(args: dict, config: dict) -> dict:
    """查看發帖歷史"""
    limit = args.get("limit") or args.get("n") or 10
    try:
        limit = int(limit)
    except (ValueError, TypeError):
        limit = 10
    
    history = config.get("post_history", [])
    recent = history[-limit:] if len(history) > limit else history
    
    return {
        "success": True,
        "total": len(history),
        "recent": list(reversed(recent)),  # 最新的在前面
    }


# ========== 入口 ==========
def handle_instagram(args, chat_id=None, agent_config=None):
    """
    處理 Instagram 工具請求。
    
    args 可以是:
        - dict: {"action": "login", "username": "...", "password": "..."}
        - str: JSON string
        - str: 純文字命令（如 "/instagram login"）
    
    agent_config: 當前 agent 的配置 dict（含 MOK_AGENT_NAME），
                  用於動態定位 ~/.mok/agent/<agent>/ 並讀取 MOK_instagram_ac / MOK_instagram_pw。
    """
    global _AGENT_CONFIG
    if agent_config:
        _AGENT_CONFIG = agent_config
    
    if not HAS_INSTAGRAPI:
        return json.dumps({
            "success": False,
            "error": "instagrapi 未安裝。請執行: pip install instagrapi"
        }, ensure_ascii=False)
    
    # 解析 args
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            # 嘗試解析為簡單命令
            parts = args.strip().split(maxsplit=1)
            action = parts[0].lstrip("/").replace("instagram", "").strip() if parts else ""
            if action:
                args = {"action": action}
            else:
                return json.dumps({"success": False, "error": "無法解析命令"}, ensure_ascii=False)
    
    if not isinstance(args, dict):
        return json.dumps({"success": False, "error": f"args 格式錯誤: {type(args)}"}, ensure_ascii=False)
    
    action = args.get("action", "").lower().strip()
    
    # 載入配置
    config = _load_config()
    
    # 路由
    action_map = {
        "login": _action_login,
        "post": _action_post,
        "upload": _action_post,
        "schedule": _action_schedule,
        "status": _action_status,
        "logout": _action_logout,
        "history": _action_history,
    }
    
    if action not in action_map:
        return json.dumps({
            "success": False,
            "error": f"未知 action: '{action}'，可用: {', '.join(action_map.keys())}"
        }, ensure_ascii=False)
    
    try:
        result = action_map[action](args, config)
        return json.dumps(result, ensure_ascii=False, default=str)
    except Exception as e:
        logger.exception(f"Instagram {action} 執行失敗")
        return json.dumps({"success": False, "error": f"執行失敗: {str(e)}"}, ensure_ascii=False)


# ========== PLUGIN_INFO ==========
PLUGIN_INFO = {
    "command": "/instagram",
    "icon": "📸",
    "handler": "handle_instagram",
    "description": "Instagram 自動發帖：登入、上傳圖片/影片、撰寫 caption、排程發布。使用 instagrapi 私有 API。",
    "intent_keywords": [
        ("/ig", "/instagram"),
        ("/instagram", "/instagram"),
        ("/發ig", "/instagram"),
        ("/ig發文", "/instagram"),
        ("/ig登入", "/instagram login"),
    ],
    "tool_schema": {
        "name": "instagram",
        "description": (
            "Instagram 自動發帖工具：使用 instagrapi 私有 API 實現登入、發帖、排程。\n\n"
            "【功能】\n"
            "- login: 登入 IG 帳號（需要 username + password）\n"
            "- post: 發佈圖片貼文（需要 username + image_path + caption）\n"
            "- schedule: 排程管理（add/list/delete）\n"
            "- status: 查看登入狀態與帳號資訊\n"
            "- logout: 登出並清除 session\n"
            "- history: 查看發帖歷史\n\n"
            "【返回格式】成功時返回 {\"success\": true, ...}，失敗返回 {\"success\": false, \"error\": \"...\"}\n\n"
            "【注意】首次使用需先 login。Session 會自動保存，後續操作無需重複登入。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["login", "post", "schedule", "status", "logout", "history"],
                    "description": "要執行的動作：login(登入), post(發文), schedule(排程), status(狀態), logout(登出), history(歷史)"
                },
                "username": {"type": "string", "description": "IG 帳號使用者名稱"},
                "password": {"type": "string", "description": "IG 密碼（僅 login 需要）"},
                "image_path": {"type": "string", "description": "圖片檔案路徑（post 需要）"},
                "caption": {"type": "string", "description": "貼文說明文字"},
                "schedule_action": {"type": "string", "enum": ["add", "list", "delete"], "description": "排程操作"},
                "time": {"type": "string", "description": "排程時間，格式 YYYY-MM-DD HH:MM"},
            },
            "required": ["action"]
        }
    }
}

logger.info("📸 Instagram 工具已載入")
