"""
mok_tg.py
Telegram 適配器 - 支持流式輸出（實時顯示思考過程）
核心對話能力由 mokagi 提供。
202608260224_我覺得可以版
"""

import asyncio
import logging
import os
import re
import sys
import json
from functools import partial

from telegram import Update, BotCommand, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import ApplicationBuilder, CommandHandler, MessageHandler, filters, ContextTypes, CallbackQueryHandler

# 導入統一核心模塊
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'core'))

import mokagi
from mokagi import process_message, clear_history, reload_tools, MOKAGI_home
from global_gate import gated as gate_call, gate_held, gate_stats

# ================== 載入配置文件（僅用於 Telegram 特有配置）==================
def load_agent_config():
    MOK_AGENT_NAME = os.environ.get("MOK_AGENT_NAME")
    if not MOK_AGENT_NAME:
        proc_name = os.environ.get("PM2_PROGRAM_NAME") or sys.argv[0]
        # 匹配進程名中的 agent 名稱，例如 mok_溟
        match = re.search(rf'{MOKAGI_home}_(.+)$', proc_name)
        MOK_AGENT_NAME = match.group(1) if match else "default"
    # 配置文件路徑：~/.mok/agent/{agent_name}/.{agent_name}
    config_path = os.path.join(os.path.expanduser("~"), f".{MOKAGI_home}", "agent", MOK_AGENT_NAME, f".{MOK_AGENT_NAME}")
    if not os.path.exists(config_path):
        raise RuntimeError(f"配置文件 {config_path} 不存在")
    config = {}
    with open(config_path, 'r') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            if '=' in line:
                key, value = line.split('=', 1)
                config[key.strip()] = value.strip()
    return config, MOK_AGENT_NAME

config, MOK_AGENT_NAME = load_agent_config()

# Telegram 特有配置
MOK_TG_TOKEN = config.get("MOK_TG_TOKEN")
if not MOK_TG_TOKEN:
    raise RuntimeError("配置文件中缺少 MOK_TG_TOKEN")
ADMIN_CHAT_ID = config.get("ADMIN_CHAT_ID", "")
ALLOWED_USERS_STR = config.get("MOK_ALLOWED_USERS", "")
ALLOWED_USERS = set()
if ALLOWED_USERS_STR:
    for uid in ALLOWED_USERS_STR.split(","):
        uid = uid.strip()
        if uid:
            ALLOWED_USERS.add(int(uid) if uid.isdigit() else uid)

# 固定消息文本
WELCOME_MSG = config.get("MOK_welcome_msg", "你好！我是有記憶的 AI 助手。")
START_MSG = config.get("MOK_start_msg", "🎉 已成功部署並24小時在線！")
UNAUTHORIZED_MSG = config.get("MOK_unAllowed_msg", "您未獲得使用權限。")

# 侍女工作中動畫（可選自訂影片路徑）
WORKING_VIDEO_PATH = config.get("WORKING_VIDEO_PATH", "")
WORKING_VIDEO_CAPTION = config.get("WORKING_VIDEO_CAPTION", "🌸 春工作中...")

logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ================== 輔助函數 ==================
def sanitize(s: str) -> str:
    """清理字符串中的不可見字符"""
    s = re.sub(r'[\u200b\u200c\u200d\u200e\u200f\ufeff]', '', s)
    s = ''.join(ch for ch in s if ch.isprintable() or ch in ('\n', '\t'))
    return s.strip()





# 手動實現文本分段（兼容舊版 telegram-bot）
def split_text(text: str, max_length: int = 4096) -> list:
    """將長文本按最大長度分段，優先在換行符處分割"""
    if len(text) <= max_length:
        return [text]
    parts = []
    while text:
        split_pos = max_length
        # 優先在換行符處切割
        if text.rfind('\n', 0, max_length) != -1:
            split_pos = text.rfind('\n', 0, max_length) + 1
        elif text.rfind(' ', 0, max_length) != -1:
            split_pos = text.rfind(' ', 0, max_length) + 1
        parts.append(text[:split_pos].rstrip())
        text = text[split_pos:].lstrip()
    return parts










# ================== per-chat 序列化鎖 ==================
# 背景：main() 用 concurrent_updates(True) 讓「不同 chat」的 update 可並行處理，
#       避免單一慢請求（長回覆、生圖、轉檔等）卡住其他使用者的訊息。
# 但「同一個 chat」仍必須序列化，否則會出事：
#   (1) 串流佔位訊息被多個協程同時 edit_message，造成內容交錯、更新亂序；
#   (2) chat 級共享狀態（對話歷史、working video 訊息 id 等）被併發讀改而競爭。
# 做法：以 chat_id 為粒度各持一把 asyncio.Lock —— 同 chat 排隊、跨 chat 互不阻塞。
_chat_locks: dict = {}

def _serialized(func):
    """per-chat 序列化裝飾器：同一 chat_id 依序執行，不同 chat 並行。

    用法：加在 Telegram handler 上（@_serialized）。被包裝的 handler 第一個參數
    需為 Update，且能取得 effective_chat / message.chat / callback_query.message.chat。
    """
    from functools import wraps

    @wraps(func)
    async def wrapper(update, context, *args, **kwargs):
        # 1) 優先由 effective_chat 取 chat_id（一般訊息與 callback 皆適用）
        chat = getattr(update, "effective_chat", None)
        chat_id = getattr(chat, "id", None)
        # 2) 後備路徑：部分 update 無 effective_chat，改由 message / callback_query.message 取；
        #    兩者都拿不到則退為 "unknown"，讓這類無 chat 的 update 共用同一把鎖、仍維持順序
        if chat_id is None:
            obj = getattr(update, "message", None) or getattr(update, "callback_query", None)
            chat_id = getattr(getattr(obj, "chat", None), "id", "unknown")
        # 3) 取得（必要時建立）此 chat 專屬的鎖，並快取回 _chat_locks
        lock = _chat_locks.get(chat_id)
        if lock is None:
            lock = asyncio.Lock()
            _chat_locks[chat_id] = lock
        # 4) 持鎖執行原 handler：同 chat 的請求在此排隊，不同 chat 各用其鎖並行
        async with lock:
            return await func(update, context, *args, **kwargs)

    return wrapper


# ================== 全域工作中影片追蹤 ==================
# key: chat_id, value: message_id，用於在發送新影片前清理舊的
_working_video_msgs: dict = {}

async def _cleanup_working_video(chat_id: str, context, current_msg_id: int = None):
    """清理指定 chat_id 的舊工作中影片。若 current_msg_id 提供，則只清理不等於它的。"""
    old_msg_id = _working_video_msgs.pop(chat_id, None)
    if old_msg_id and old_msg_id != current_msg_id:
        try:
            await context.bot.delete_message(chat_id=int(chat_id), message_id=old_msg_id)
        except Exception:
            pass  # 消息可能已被刪除

# ================== L3 結構化媒體（20261008 indexPage） ==================
def _resolve_tg_media_source(url):
    """把協議 URL 轉成 Telegram 可用來源：站內相對路徑 -> 本機檔案；http(s) -> 原網址。"""
    if not url:
        return None
    if url.startswith("http://") or url.startswith("https://"):
        return url
    if url.startswith("/"):
        rel = url.split("?", 1)[0].split("#", 1)[0].lstrip("/")
        base = os.path.join(os.path.expanduser("~/.mok/html"), rel)
        if os.path.isfile(base):
            return open(base, "rb")
    return None


async def _send_tg_media(context, chat_id, items):
    """依媒體型別逐一發送（圖/影/音）；單項失敗只記 log，不中斷其餘。"""
    sent = 0
    for it in (items or []):
        if not isinstance(it, dict):
            continue
        _type = it.get("type")
        _url = it.get("url")
        src = _resolve_tg_media_source(_url)
        if src is None:
            continue
        fh = src if hasattr(src, "read") else None
        try:
            cap = it.get("alt") or None
            if _type == "video":
                await context.bot.send_video(chat_id=chat_id, video=src, caption=cap, supports_streaming=True)
            elif _type == "audio":
                await context.bot.send_audio(chat_id=chat_id, audio=src, caption=cap)
            else:
                await context.bot.send_photo(chat_id=chat_id, photo=src, caption=cap)
            sent += 1
        except Exception as e:
            logging.warning("send media 失敗 url=%s: %s", _url, e)
        finally:
            if fh is not None:
                try:
                    fh.close()
                except Exception:
                    pass
    return sent


# ================== 流式回調函數（核心） ==================
async def stream_callback(update: Update, context: ContextTypes.DEFAULT_TYPE, temp_msg, state: dict, event: dict, user_text: str = ""):
    """
    處理 mokagi 產生的事件，實時更新 Telegram 消息
    event 格式：
        , "content": "思考內容片段"}
        {"type": "reply", "content": "回覆內容片段"}
        {"type": "done"}
    
    state 為每個請求獨立的可變字典，包含 think_content 和 full_reply，
    避免多個並發請求共享全域狀態導致內容混雜。
    """
    try:
        # L3 結構化媒體（20261008 indexPage）：累積本輪媒體，done 時統一發送
        if event.get("type") == "tool_result" and event.get("media"):
            state.setdefault("media", []).extend(event["media"])
        if event["type"] == "think":
            state["think_content"] += event["content"]
            # 只顯示思考部分（回覆還沒開始）
            new_text = f"💭\n{state['think_content']}"
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=temp_msg.message_id,
                text=new_text,
                parse_mode="Markdown"
            )
        elif event["type"] == "reply":
            state["full_reply"] += event["content"]
            # 同時顯示思考內容和回覆內容
            new_text = f"```🤔\n{state['think_content']}🤔```\n\n💬 回覆：\n{state['full_reply']}"
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=temp_msg.message_id,
                text=new_text,
                parse_mode="Markdown"
            )
        elif event["type"] == "done":
            final_reply = state["full_reply"] or "（無回覆）"
            # 最終也保留思考內容
            new_text = f"```💡\n{state['think_content']}💡```\n\n💬 回覆：\n{final_reply}"
            # 如果消息過長（超過 4096 字符），截斷並提示
            if len(new_text) > 4096:
                new_text = new_text[:4096-200] + "\n\n... (內容過長，已截斷)"
            await context.bot.edit_message_text(
                chat_id=update.effective_chat.id,
                message_id=temp_msg.message_id,
                text=new_text,
                parse_mode="Markdown"
            )
            # L3 結構化媒體（20261008 indexPage）：文字回覆送出後，再補發本輪圖/影/音
            try:
                await _send_tg_media(context, update.effective_chat.id, state.get("media") or [])
            except Exception as _me:
                logging.warning("發送結構化媒體失敗: %s", _me)
            # 清理累積器，為下一次對話準備
            state["think_content"] = ""
            state["full_reply"] = ""
            state["media"] = []
            # ✨ A/B: girl gate — 偵測一般模型的安全拒答 → GIRL_AUTO=1 直接開 girl，否則送確認按鈕
            try:
                import girl_gate
                g = girl_gate.check(user_text, final_reply, MOK_AGENT_NAME)
                if g["needs_girl"]:
                    girl_gate.save_pending(str(update.effective_chat.id), user_text=user_text)
                    if g.get("auto"):
                        # ★ GIRL_AUTO=1：檢查到色情過濾(安全拒答) → 直接打開 girl 模型
                        try:
                            import girl_engine as _ge_auto
                            _ge_auto.spawn_auto_start(str(update.effective_chat.id), MOK_AGENT_NAME)
                            _auto_txt = "🚦 偵測到一般模型安全拒答（色情過濾）→ 已直接幫主人打開 vast girl 模型（qwen-Claude 27B），上線後會自動切換。"
                        except Exception as _ae:
                            _auto_txt = f"🚦 偵測到安全拒答，但自動開 girl 失敗：{_ae}"
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text=_auto_txt,
                        )
                    else:
                        kb = InlineKeyboardMarkup([[
                            InlineKeyboardButton("🖥️ 開 vast girl（qwen-Claude）", callback_data="girl_go"),
                            InlineKeyboardButton("不用", callback_data="girl_no"),
                        ]])
                        await context.bot.send_message(
                            chat_id=update.effective_chat.id,
                            text="🧠 一般模型出現安全拒答。要切到「vast girl」引擎（qwen-Claude 27B）重新回答嗎？",
                            reply_markup=kb,
                        )
            except Exception as ge:
                logging.warning(f"girl_gate 失敗: {ge}")
    except Exception as e:
        logging.error(f"流式回調出錯: {e}")


# ================== girl 引擎確認按鈕 callback（B/D/E） ==================
@_serialized
async def girl_confirm_cb(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """「開 vast girl / 不用」按鈕回調：no → 關閉；go → 呼叫 girl_engine.request_girl_start"""
    q = update.callback_query
    if q is None:
        return
    await q.answer()
    try:
        chat_id = str(q.message.chat_id)
        if q.data == "girl_no":
            try:
                await q.edit_message_text(f"👌 好的主人～{MOK_AGENT_NAME}維持原樣，不開 vast girl 了。需要時再叫{MOK_AGENT_NAME}～")
            except Exception:
                pass
            return
        if q.data == "girl_go":
            try:
                import girl_engine
                ok, msg = await girl_engine.request_girl_start(chat_id, MOK_AGENT_NAME)
            except Exception as ge:
                ok, msg = False, f"❌ girl_engine 呼叫失敗: {ge}"
            if ok:
                # E 里程碑：引擎已上線 → 切 MOK_CURRENT_MODEL=girl:qwen-Claude + 延遲重載
                try:
                    import girl_switch
                    s_ok, s_msg = girl_switch.switch(MOK_AGENT_NAME)
                    msg = msg + "\n" + s_msg if s_ok else msg
                except Exception as se:
                    msg = msg + f"\n(girl_switch 失敗: {se})"
                try:
                    # E reload：通知前端熱重載（絕不 pm2 restart mok_agi）
                    import urllib.request as _ur
                    _req = _ur.Request("http://127.0.0.1:5000/api/girl/reload",
                                       data=json.dumps({"agent": MOK_AGENT_NAME}).encode("utf-8"),
                                       headers={"Content-Type": "application/json"}, method="POST")
                    _ur.urlopen(_req, timeout=5).read()
                    msg = msg + "\n🔄 已通知前端熱重載，切換至 girl:qwen-Claude 即刻生效（不重啟服務）。"
                except Exception:
                    pass
            msg = f"🖥️ 收到主人～{MOK_AGENT_NAME}正在幫您開機 vast girl 引擎（qwen-Claude 27B）。\n\n" + msg
            # ✨ 侍女以「對話回答」形式輸出開機狀態：發一則獨立訊息（不是只改那顆按鈕的彈出提示），
            #    TG 訊息本身會保留，所以換侍女／重開對話後都還看得到，不會不見。
            # 先把原本那顆按鈕訊息收掉鍵盤、改成簡短狀態，避免重複觸發。
            try:
                await q.edit_message_text("🖥️ 已為主人按下「開 vast girl」，開機狀態請看下方對話👇")
            except Exception:
                try:
                    await q.edit_message_reply_markup(reply_markup=None)
                except Exception:
                    pass
            try:
                await context.bot.send_message(chat_id=update.effective_chat.id, text=msg)
            except Exception:
                # 真的發不出去時，退而求其次：至少把按鈕訊息改成開機狀態
                try:
                    await q.edit_message_text(msg)
                except Exception:
                    pass
    except Exception as e:
        logging.warning(f"girl_confirm_cb 失敗: {e}")


# ================== 指令權限閘（2026-10-03 by 稚）==================
# start / clear / tools 一律先過白名單（ADMIN_CHAT_ID 或 MOK_ALLOWED_USERS）；
# 未授權者只回制式訊息、不做任何事。授權判定與 handle_message 完全一致。
def _cmd_allowed(update) -> bool:
    try:
        cid = str(update.message.chat_id)
    except Exception:
        return False
    if ADMIN_CHAT_ID and cid == str(ADMIN_CHAT_ID):
        return True
    if not ALLOWED_USERS:
        return True          # 未設白名單時維持舊行為，避免把主人自己鎖在門外
    return cid in set(map(str, ALLOWED_USERS))


async def _reject_cmd(update):
    try:
        await update.message.reply_text(UNAUTHORIZED_MSG)
    except Exception:
        pass


# ================== Telegram 命令處理器 ==================
@_serialized
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _cmd_allowed(update):
        await _reject_cmd(update)
        return
    await update.message.reply_text(WELCOME_MSG)

@_serialized
async def clear(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _cmd_allowed(update):
        await _reject_cmd(update)
        return
    chat_id = str(update.message.chat_id)
    clear_history(chat_id)
    await update.message.reply_text("記憶已清除，我們重新開始。")

@_serialized
async def tools_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not _cmd_allowed(update):
        await _reject_cmd(update)
        return
    from mokagi import tool_handler
    tools = tool_handler.get_tools()
    text = "🧰 已安裝的工具:\n"
    for mod in tools.values():
        if hasattr(mod, "PLUGIN_INFO"):
            info = mod.PLUGIN_INFO
            text += f"  {info['command']} — {info['description']}\n"
    text += "\n ➕ 增加工具: https://github.com/MOK2026/MOKAGI/tree/main/tools"
    await update.message.reply_text(text, disable_web_page_preview=True)

@_serialized
async def reload(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("🔄 立即停止所有服務及緊急重啟，請稍候...")
    import subprocess
    subprocess.Popen("pm2 restart mok_agi", shell=True)
    # 注意：重啟後當前進程會被殺死，無法回覆後續消息。所以先回復再重啟。

async def update_bot_commands(app):
    from mokagi import tool_handler
    base_commands = [
        BotCommand(sanitize("start"), sanitize("開始對話")),
        BotCommand(sanitize("clear"), sanitize("清除會話記憶")),
        # [停用 2026-10-03 by 稚] /reload 已停用，選單同步移除
        # BotCommand(sanitize("reload"), sanitize("緊急重啟")),
        BotCommand(sanitize("tools"), sanitize("工具箱")),
    ]
    plugin_commands = []
    for mod in tool_handler.get_tools().values():
        if hasattr(mod, "PLUGIN_INFO"):
            info = mod.PLUGIN_INFO
            cmd = sanitize(info["command"]).lstrip("/")
            desc = sanitize(info["description"])[:250]
            if cmd and desc:
                plugin_commands.append(BotCommand(cmd, desc))
    await app.bot.set_my_commands(base_commands + plugin_commands)

async def send_welcome(app):
    if ADMIN_CHAT_ID:
        try:
            await app.bot.send_message(chat_id=ADMIN_CHAT_ID, text=START_MSG)
        except Exception as e:
            logging.warning(f"無法發送歡迎消息給 {ADMIN_CHAT_ID}: {e}")

# ================== 圖片處理 ==================
@_serialized
async def handle_photo(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """處理 Telegram 圖片訊息：下載圖片 → vision 分析 → 回覆描述"""
    chat_id = str(update.message.chat_id)

    # 權限檢查
    if ALLOWED_USERS and str(chat_id) not in map(str, ALLOWED_USERS):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    caption = update.message.caption or ""
    status_msg = await update.message.reply_text("📸 收到圖片，正在分析中…")

    try:
        # 下載圖片（photo[-1] = 最高解析度）
        photo_file = await update.message.photo[-1].get_file()

        # 保存目錄：~/.mok/agent/{agent}/uploads/
        import time as _time
        upload_dir = os.path.join(MOKAGI_home, "agent", MOK_AGENT_NAME, "uploads")
        os.makedirs(upload_dir, exist_ok=True)
        filename = f"tg_{_time.strftime('%Y%m%d_%H%M%S')}_{update.message.message_id}.jpg"
        file_path = os.path.join(upload_dir, filename)
        await photo_file.download_to_drive(file_path)

        # 用 vision 工具分析圖片
        from mokagi import tool_handler
        cmd = f"/vision {file_path}"
        if caption:
            cmd += f" {caption}"
        result = await tool_handler.process_message(
            user_text=cmd,
            chat_id=chat_id,
            ollama_api=mokagi.OLLAMA_API,
            model_name=mokagi.MOK_MODEL_NAME,
            cmd_map=tool_handler.get_cmd_map(),
            tools=tool_handler.get_tools(),
            agent_config=mokagi._agent_config
        )

        # 回覆結果
        if result:
            if result.startswith("CONFIRM_SPLIT:"):
                parts = result.split("---CONFIRM_SPLIT---", 1)
                await update.message.reply_text(parts[0].strip())
                if len(parts) > 1:
                    await update.message.reply_text(parts[1].strip())
            else:
                for part in split_text(result, max_length=4096):
                    try:
                        await update.message.reply_text(part, parse_mode='HTML')
                    except Exception:
                        await update.message.reply_text(part)
        else:
            await update.message.reply_text("❌ 圖片分析失敗（無結果）")
    except Exception as e:
        await update.message.reply_text(f"❌ 圖片處理失敗：{e}")
    finally:
        try:
            await status_msg.delete()
        except Exception:
            pass


# ================== 消息處理（流式） ==================
@_serialized
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_text = update.message.text
    chat_id = str(update.message.chat_id)

    # 權限檢查
    if ALLOWED_USERS and str(chat_id) not in map(str, ALLOWED_USERS):
        await update.message.reply_text(UNAUTHORIZED_MSG)
        return

    # ---------- 直接處理以 '/' 開頭的命令 ----------
    if user_text.startswith('/'):
        #print2(f"收到 / 命令: {user_text}")
        # 🔧 先清理舊的工作中影片，避免切換 agent/模型時殘留
        await _cleanup_working_video(chat_id, context)
        # 導入 tool_handler（已在 mokagi 中導入，這裡直接引用）
        from mokagi import tool_handler
        # 調用統一的命令處理函數（非流式）
        result = await tool_handler.process_message(
            user_text=user_text,
            chat_id=chat_id,
            ollama_api=mokagi.OLLAMA_API,
            model_name=mokagi.MOK_MODEL_NAME,
            cmd_map=tool_handler.get_cmd_map(),
            tools=tool_handler.get_tools()
        )
        if result:
            # 檢查是否為確認拆分消息
            if result.startswith("CONFIRM_SPLIT:"):
                # 去掉前綴
                content = result[len("CONFIRM_SPLIT:"):]
                # 按分隔符拆分
                if "---CONFIRM_SPLIT---" in content:
                    parts = content.split("---CONFIRM_SPLIT---", 1)
                    warning_part = parts[0].strip()
                    confirm_part = parts[1].strip()
                    # 發送警告部分（加上模型標籤）
                    await update.message.reply_text(warning_part + mokagi.get_model_tag(), parse_mode='HTML')
                    # 發送確認命令部分（不加模型標籤）
                    await update.message.reply_text(confirm_part)
                else:
                    # 降級處理：直接發原消息
                    for part in split_text(result, max_length=4096):
                        await update.message.reply_text(part, parse_mode='HTML')
                return
        # 如果沒有匹配的命令，繼續下面的流程（可能當作普通聊天處理）
        # 注意：這裡不返回，讓後續流程嘗試意圖識別或普通聊天

    # 特殊處理：工作流自動執行標記（由 workflow 工具返回）
    if user_text.startswith("WORKFLOW_AUTO_EXEC:"):
        #print2(f"收到 工作流: {user_text}")
        parts = user_text.split(":", 3)
        if len(parts) >= 4:
            goal = parts[3].split(":", 1)[0] if ":" in parts[3] else parts[3]
            steps_json = parts[3].split(":", 1)[1] if ":" in parts[3] else ""
            if steps_json:
                steps = json.loads(steps_json)
                result = await execute_multi_step(chat_id, goal, forced_steps=steps)
                for part in split_text(result, max_length=4096):
                    await update.message.reply_text(part)
                return
        await update.message.reply_text("❌ 工作流自動執行標記無效")
        return

    # 普通消息：先清理舊的工作中影片，再發送新的
    working_video_msg = None
    await _cleanup_working_video(chat_id, context)
    if WORKING_VIDEO_PATH and os.path.exists(WORKING_VIDEO_PATH):
        try:
            with open(WORKING_VIDEO_PATH, "rb") as video_file:
                working_video_msg = await update.message.reply_video(
                    video=video_file,
                    caption=WORKING_VIDEO_CAPTION,
                    supports_streaming=True
                )
            if working_video_msg:
                _working_video_msgs[chat_id] = working_video_msg.message_id
        except Exception as e:
            logging.warning(f"發送工作中影片失敗，降級為文字: {e}")

    # 為每個請求創建獨立的狀態容器，避免並發請求共享全域狀態
    stream_state = {"think_content": "", "full_reply": "", "media": []}
    
    temp_msg = await update.message.reply_text("💭 思考中...")
    try:
        cb = partial(stream_callback, update, context, temp_msg, stream_state, user_text)
        await gate_call(process_message, user_id=chat_id, text=user_text, stream_callback=cb, agent_config=mokagi._agent_config)
    except Exception as e:
        logging.exception("處理消息時出錯")
        await context.bot.edit_message_text(
            chat_id=update.effective_chat.id,
            message_id=temp_msg.message_id,
            text=f"❌ 處理消息時出錯: {str(e)}"
        )
    finally:
        # 清理工作中動畫：僅在該影片仍為當前活動影片時才刪除
        if working_video_msg:
            current_msg_id = working_video_msg.message_id
            if _working_video_msgs.get(chat_id) == current_msg_id:
                # 仍是當前活動的影片，可以安全刪除
                _working_video_msgs.pop(chat_id, None)
            try:
                await context.bot.delete_message(
                    chat_id=update.effective_chat.id,
                    message_id=current_msg_id
                )
            except Exception as e:
                logging.warning(f"刪除工作中影片失敗: {e}")

# ================== 主函數 ==================
async def post_init(app):
    """啟動完成後自動執行初始化任務"""
    await update_bot_commands(app)
    await send_welcome(app)

def main():
    reload_tools()
    app = ApplicationBuilder().token(MOK_TG_TOKEN).concurrent_updates(True).post_init(post_init).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("clear", clear))
    # [停用 2026-10-03 by 稚] /reload 是「無閘門」的強制重啟指令，任何人皆可用；
    # 依主人指示停用（保留函數本體，不刪碼）。要恢復：取消下一行註解即可。
    # app.add_handler(CommandHandler("reload", reload))
    app.add_handler(CommandHandler("tools", tools_command))
    
    app.add_handler(MessageHandler(filters.TEXT, handle_message))
    app.add_handler(MessageHandler(filters.PHOTO, handle_photo))
    app.add_handler(CallbackQueryHandler(girl_confirm_cb, pattern="^(girl_go|girl_no)$"))
    



    #print2(f"✅ {MOK_AGENT_NAME} 啟動中... （流式輸出已啟用）")
    app.run_polling()

if __name__ == "__main__":
    main()