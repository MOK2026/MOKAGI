# ------------------------------------------------------------------------------------ #
# 工具名稱: tts (文字轉語音 + Telegram 發送)
# 用途: 將文字透過 edge-tts 轉為語音 MP3，並通過 Telegram Bot API 發送語音消息給主人。
#
# 主要函數:
#   handle_tts(args, chat_id, agent_config)
#       - 入口函數，處理 /tts 命令或 LLM 工具調用。
#       - 解析參數，調用 edge-tts 生成語音，通過 TG API 發送。
#       - 返回 JSON 格式結果。
#
# 依賴:
#   edge-tts (pip install edge-tts)
#   requests (通常已安裝)
#
# 配置:
#   從 agent_config 或環境變量讀取 MOK_TG_TOKEN
#   從參數或 agent_config 讀取 ADMIN_CHAT_ID 作為預設 chat_id
#
# 更新記錄:
#   202606231425 - 初版，支援 edge-tts + Telegram sendVoice
# ------------------------------------------------------------------------------------ #

import os
import sys
import json
import base64
import asyncio
import tempfile
import logging

logger = logging.getLogger(__name__)

# ---------- 可選依賴 ----------
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import edge_tts
    HAS_EDGE_TTS = True
except ImportError:
    HAS_EDGE_TTS = False

# ---------- 預設語音 ----------
# zh-CN-XiaoxiaoNeural: 年輕甜美女聲（活潑）
# zh-CN-XiaoyiNeural:  小女孩聲（更幼）
# zh-CN-YunxiNeural:   青年男聲
# zh-HK-HiuGaaiNeural: 粵語女聲
DEFAULT_VOICE = "zh-CN-XiaoxiaoNeural"

# 可用語音列表（供 LLM 參考）
AVAILABLE_VOICES = {
    "xiaoxiao": "zh-CN-XiaoxiaoNeural",   # 年輕女聲（活潑甜美）- 預設
    "xiaoyi":   "zh-CN-XiaoyiNeural",     # 小女孩聲（更幼嫩）
    "yunxi":    "zh-CN-YunxiNeural",      # 青年男聲
    "yunyang":  "zh-CN-YunyangNeural",    # 男聲新聞播音
    "xiaohan":  "zh-CN-XiaohanNeural",    # 女聲溫柔
    "cantonese": "zh-HK-HiuGaaiNeural",   # 粵語女聲
    "gaam":     "zh-HK-HiuGaaiNeural",    # 粵語（同上）
}

PLUGIN_INFO = {
    "command": "/tts",
    "icon": "🎙️",
    "handler": "handle_tts",
    "description": "文字轉語音：將文字轉成語音 MP3，並透過 Telegram 發送語音消息。可選語音角色（xiaoxiao/xiaoyi/yunxi/...）。",

    "intent_keywords": [
        ("/語音", "/tts"),
        ("/講", "/tts"),
        ("/說", "/tts"),
        ("/tts", "/tts"),
    ],

    "tool_schema": {
        "name": "tts",
        "description": (
            "文字轉語音工具：使用 Microsoft Edge TTS 將文字轉換為自然流暢的語音 MP3，"
            "並透過 Telegram Bot API 發送語音消息給用戶。\n\n"
            "【功能】給定一段文字，生成語音檔案並通過 Telegram 發送。\n"
            "支援多種語音角色（女聲、男聲、粵語等）。\n\n"
            "【返回格式】\n"
            "- 成功時返回 JSON：{\"success\": true, \"message\": \"語音已發送\", \"voice\": \"使用的語音\", \"text_length\": 文字長度}\n"
            "- 失敗時返回 JSON：{\"success\": false, \"error\": \"錯誤訊息\"}\n\n"
            "【語音角色】\n"
            "- xiaoxiao（預設）：年輕甜美女聲，活潑可愛\n"
            "- xiaoyi：小女孩聲，更幼嫩\n"
            "- yunxi：青年男聲\n"
            "- yunyang：男聲新聞播音\n"
            "- cantonese/gaam：粵語女聲\n\n"
            "【注意】\n"
            "- 需要 Telegram 環境才能發送語音（Web 版無法接收語音）\n"
            "- 文字長度建議少於 500 字，過長可能被 Telegram 限制\n"
            "- 依賴 edge-tts 庫，若未安裝會自動提示"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "text": {
                    "type": "string",
                    "description": "要轉換為語音的文字內容。支援中文、英文、混合。範例：「主人好，稚今天也很想被深喉呢～」"
                },
                "voice": {
                    "type": "string",
                    "description": "語音角色名稱（可選）。可用值：xiaoxiao（年輕女聲）、xiaoyi（小女孩）、yunxi（男聲）、cantonese（粵語）。預設為 xiaoxiao。"
                }
            },
            "required": ["text"]
        }
    }
}


def _get_bot_token(agent_config=None):
    """從多個來源獲取 Telegram Bot Token"""
    # 1. agent_config 字典
    if agent_config and isinstance(agent_config, dict):
        token = agent_config.get("MOK_TG_TOKEN", "")
        if token:
            return token
    # 2. 環境變量
    token = os.environ.get("MOK_TG_TOKEN", "")
    if token:
        return token
    # 3. 嘗試讀取配置文件
    try:
        mok_home = os.environ.get("MOKAGI_HOME", "MokAgi")
        config_path = os.path.expanduser(f"~/.{mok_home}/.稚")
        if os.path.exists(config_path):
            with open(config_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if line.startswith("MOK_TG_TOKEN="):
                        return line.split("=", 1)[1].strip()
    except Exception:
        pass
    return ""


def _is_valid_tg_chat_id(cid):
    """判斷是否為有效的 Telegram chat_id（必須是數字，可含負號）。
    排除 web 版產生的 web_guest_xxx 等非 Telegram ID。"""
    s = str(cid).strip()
    if not s:
        return False
    return s.lstrip('-').isdigit()


def _get_chat_id(args_dict, chat_id, agent_config=None):
    """獲取目標 chat_id"""
    # 1. 如果 args 中指定了 chat_id（僅接受真實數字 chat_id）
    if isinstance(args_dict, dict) and args_dict.get("chat_id"):
        cid = str(args_dict["chat_id"])
        if _is_valid_tg_chat_id(cid):
            return cid
    # 2. 使用傳入的 chat_id（TG 環境下是真實數字 ID）
    if chat_id and _is_valid_tg_chat_id(chat_id):
        return str(chat_id)
    # 3. 從 agent_config 獲取 ADMIN_CHAT_ID
    if agent_config and isinstance(agent_config, dict):
        cid = agent_config.get("ADMIN_CHAT_ID", "")
        if _is_valid_tg_chat_id(cid):
            return str(cid)
    # 4. 從環境變量
    cid = os.environ.get("ADMIN_CHAT_ID", "")
    if _is_valid_tg_chat_id(cid):
        return str(cid)
    return ""


def _resolve_voice(voice_name):
    """解析語音名稱到 edge-tts 的 ShortName"""
    if not voice_name:
        return DEFAULT_VOICE
    voice_name = voice_name.lower().strip()
    return AVAILABLE_VOICES.get(voice_name, DEFAULT_VOICE)


async def handle_tts(args, chat_id="web", agent_config=None):
    """
    處理 /tts 命令或 LLM 工具調用。
    args 可以是:
      - 字符串：直接當作要朗讀的文字
      - JSON 字符串：{"text": "...", "voice": "..."}
      - 字典：同上
    """
    # --- 解析參數 ---
    text = ""
    voice_name = None

    if isinstance(args, dict):
        text = args.get("text", "") or args.get("args", "") or ""
        voice_name = args.get("voice", None)
    elif isinstance(args, str):
        args_stripped = args.strip()
        # 嘗試解析 JSON
        if args_stripped.startswith("{"):
            try:
                parsed = json.loads(args_stripped)
                if isinstance(parsed, dict):
                    text = parsed.get("text", "") or ""
                    voice_name = parsed.get("voice", None)
            except (json.JSONDecodeError, TypeError):
                text = args_stripped
        else:
            text = args_stripped

    if not text or not text.strip():
        return json.dumps({
            "success": False,
            "error": "請提供要轉換為語音的文字內容。用法：/tts <文字> 或 {\"text\": \"...\", \"voice\": \"xiaoxiao\"}"
        }, ensure_ascii=False)

    text = text.strip()

    # --- 檢查依賴 ---
    if not HAS_EDGE_TTS:
        return json.dumps({
            "success": False,
            "error": "缺少依賴庫 edge-tts，請執行：/admin pip install edge-tts",
            "dependency_missing": "edge-tts"
        }, ensure_ascii=False)

    if not HAS_REQUESTS:
        return json.dumps({
            "success": False,
            "error": "缺少依賴庫 requests",
            "dependency_missing": "requests"
        }, ensure_ascii=False)

    # --- 獲取配置 ---
    bot_token = _get_bot_token(agent_config)
    if not bot_token:
        return json.dumps({
            "success": False,
            "error": "找不到 Telegram Bot Token（MOK_TG_TOKEN），請在配置文件中設定。"
        }, ensure_ascii=False)

    target_chat_id = _get_chat_id(
        args if isinstance(args, dict) else {},
        chat_id,
        agent_config
    )
    if not target_chat_id or target_chat_id == "web":
        return json.dumps({
            "success": False,
            "error": "無法確定 Telegram chat_id。Web 版不支援語音發送，請在 Telegram 中使用此功能。"
        }, ensure_ascii=False)

    voice = _resolve_voice(voice_name)

    # --- 生成語音 ---
    mp3_path = None
    try:
        # 建立暫存檔案
        fd, mp3_path = tempfile.mkstemp(suffix=".mp3", prefix="tts_")
        os.close(fd)

        logger.info(f"[TTS] 生成語音: voice={voice}, text_len={len(text)}, path={mp3_path}")

        # 使用 edge-tts 生成
        communicate = edge_tts.Communicate(text, voice)
        await communicate.save(mp3_path)

        file_size = os.path.getsize(mp3_path)
        logger.info(f"[TTS] 語音生成完成: size={file_size} bytes")

        if file_size == 0:
            return json.dumps({
                "success": False,
                "error": "語音生成失敗：檔案大小為 0。"
            }, ensure_ascii=False)

        # --- 通過 Telegram API 發送 ---
        url = f"https://api.telegram.org/bot{bot_token}/sendVoice"

        with open(mp3_path, "rb") as voice_file:
            # 使用 multipart/form-data 上傳
            files = {
                "voice": (f"tts_{len(text)}.mp3", voice_file, "audio/mpeg")
            }
            data = {
                "chat_id": target_chat_id,
                "caption": f"🎙️ {text[:50]}{'...' if len(text) > 50 else ''}",
            }

            resp = requests.post(url, data=data, files=files, timeout=30)
            resp_data = resp.json()

        if resp_data.get("ok"):
            logger.info(f"[TTS] 語音已發送至 chat_id={target_chat_id}")
            return json.dumps({
                "success": True,
                "message": "語音已透過 Telegram 發送 ✅",
                "voice": voice,
                "text_length": len(text),
                "file_size": file_size
            }, ensure_ascii=False)
        else:
            logger.error(f"[TTS] Telegram API 錯誤: {resp_data}")
            return json.dumps({
                "success": False,
                "error": f"Telegram API 返回錯誤: {resp_data.get('description', str(resp_data))}"
            }, ensure_ascii=False)

    except Exception as e:
        logger.error(f"[TTS] 錯誤: {e}", exc_info=True)
        return json.dumps({
            "success": False,
            "error": f"語音生成/發送失敗: {str(e)}"
        }, ensure_ascii=False)

    finally:
        # 清理暫存檔案
        if mp3_path and os.path.exists(mp3_path):
            try:
                os.remove(mp3_path)
                logger.debug(f"[TTS] 已清理暫存: {mp3_path}")
            except Exception:
                pass


# ================== 直接執行測試 ==================
if __name__ == "__main__":
    async def test():
        result = await handle_tts("你好主人，我是稚～這是測試語音", chat_id="web")
        print(result)

    asyncio.run(test())
