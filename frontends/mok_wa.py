"""
mok_wa.py
WhatsApp 適配器 - 使用 Twilio WhatsApp API
核心對話能力由 mokagi 提供。
202607210159_稚製作

使用方法：
1. 在 agent 配置文件 (e.g. ~/.mok/agent/稚/.稚) 中加入：
   MOK_WS_SID=你的Twilio_Account_SID
   MOK_WS_TOKEN=你的Twilio_Auth_Token
   MOK_WS_FROM=whatsapp:+14155238886
   MOK_WS_PORT=5001
   MOK_WS_ALLOWED=85212345678,886987654321  # 可選，白名單

2. 在 Twilio Console → WhatsApp → Sandbox 設置 Webhook URL：
   https://你的域名/whatsapp

3. 用 pm2 啟動：
   pm2 start /home/ubuntu/.mok/frontends/mok_wa.py --interpreter python3 --name mok_wa_稚
"""

import asyncio
import logging
import os
import re
import sys
import json
from functools import partial
from threading import Thread, Event

from flask import Flask, request, jsonify

# Twilio
from twilio.rest import Client as TwilioClient
from twilio.twiml.messaging_response import MessagingResponse

# 導入統一核心模塊
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(__file__)), 'core'))

import mokagi
from mokagi import process_message, clear_history, reload_tools, MOKAGI_home

# ================== 載入配置文件 ==================
def load_agent_config():
    MOK_AGENT_NAME = os.environ.get("MOK_AGENT_NAME")
    if not MOK_AGENT_NAME:
        proc_name = os.environ.get("PM2_PROGRAM_NAME") or sys.argv[0]
        match = re.search(rf'{MOKAGI_home}_(.+)$', proc_name)
        MOK_AGENT_NAME = match.group(1) if match else "default"
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

# WhatsApp 特有配置
MOK_WS_SID = config.get("MOK_WS_SID")
MOK_WS_TOKEN = config.get("MOK_WS_TOKEN")
MOK_WS_FROM = config.get("MOK_WS_FROM")       # e.g. "whatsapp:+14155238886"
MOK_WS_PORT = int(config.get("MOK_WS_PORT", "5001"))
MOK_WS_ALLOWED_RAW = config.get("MOK_WS_ALLOWED", "")
MOK_WS_ALLOWED = [n.strip() for n in MOK_WS_ALLOWED_RAW.split(",") if n.strip()] if MOK_WS_ALLOWED_RAW else []

if not MOK_WS_SID or not MOK_WS_TOKEN or not MOK_WS_FROM:
    raise RuntimeError(
        "❌ 配置文件中缺少 MOK_WS_SID / MOK_WS_TOKEN / MOK_WS_FROM\n"
        "請在 ~/.mok/agent/稚/.稚 中加入 Twilio 憑證"
    )

WELCOME_MSG = config.get("MOK_welcome_msg", "你好！我是有記憶的 AI 助手。")
UNAUTHORIZED_MSG = config.get("MOK_unAllowed_msg", "您未獲得使用權限。")

logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(message)s')
logging.getLogger("httpx").setLevel(logging.WARNING)
logging.getLogger("flask").setLevel(logging.WARNING)
logging.getLogger("twilio").setLevel(logging.WARNING)

# ================== Twilio Client ==================
twilio_client = TwilioClient(MOK_WS_SID, MOK_WS_TOKEN)

# ================== 輔助函數 ==================
def split_text(text: str, max_length: int = 1600) -> list:
    """將長文本分段（WhatsApp 建議 1600 字符以內）"""
    if len(text) <= max_length:
        return [text]
    parts = []
    while text:
        split_pos = max_length
        if text.rfind('\n', 0, max_length) != -1:
            split_pos = text.rfind('\n', 0, max_length) + 1
        elif text.rfind('。', 0, max_length) != -1:
            split_pos = text.rfind('。', 0, max_length) + 1
        elif text.rfind(' ', 0, max_length) != -1:
            split_pos = text.rfind(' ', 0, max_length) + 1
        parts.append(text[:split_pos].strip())
        text = text[split_pos:].strip()
    return [p for p in parts if p]

def wa_send(wa_to: str, wa_from: str, body: str):
    """透過 Twilio 發送 WhatsApp 訊息"""
    try:
        for part in split_text(body):
            twilio_client.messages.create(
                from_=wa_to,   # Twilio 號碼（系統發送方）
                body=part,
                to=wa_from     # 用戶號碼（接收方）
            )
    except Exception as e:
        logging.error(f"❌ Twilio 發送失敗: {e}")

# ================== 背景事件循環 ==================
class AsyncWorker:
    """在單獨線程中運行 asyncio 事件循環"""
    def __init__(self):
        self.loop = None
        self._thread = None
        self._ready = Event()

    def start(self):
        self._thread = Thread(target=self._run, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=5)
        logging.info("✅ AsyncWorker 背景事件循環已啟動")

    def _run(self):
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self._ready.set()
        self.loop.run_forever()

    def submit(self, coro):
        """提交非同步任務到背景循環"""
        if self.loop and self.loop.is_running():
            asyncio.run_coroutine_threadsafe(coro, self.loop)
            return True
        logging.error("❌ AsyncWorker 事件循環未運行")
        return False

    def stop(self):
        if self.loop:
            self.loop.call_soon_threadsafe(self.loop.stop)

worker = AsyncWorker()

# ================== 流式回調 ==================
class WACallbackState:
    """保存每個用戶的流式回調狀態"""
    def __init__(self, wa_to, wa_from):
        self.wa_to = wa_to       # Twilio 號碼（系統發送方）
        self.wa_from = wa_from   # 用戶號碼（接收方）
        self.think_content = ""
        self.full_reply = ""
        self.last_sent_text = ""
        self.update_count = 0

_wa_states = {}

async def wa_stream_callback(wa_to: str, event: dict):
    state = _wa_states.get(wa_to)
    if not state:
        return

    try:
        if event["type"] == "think":
            state.think_content += event["content"]
            # 每 5 個片段才發一次更新，減少 API 調用
            state.update_count += 1
            if state.update_count % 5 == 0:
                text = f"💭\n{state.think_content}"
                if len(text) > 1500:
                    text = text[:1450] + "\n\n...（思考中）"
                # 只有內容變化才發送
                if text != state.last_sent_text:
                    wa_send(state.wa_to, state.wa_from, text)
                    state.last_sent_text = text

        elif event["type"] == "reply":
            state.full_reply += event["content"]
            state.update_count += 1
            # 每收到回覆片段就發送（流式輸出）
            if state.update_count % 2 == 0 or event.get("flush"):
                text = state.full_reply
                if text != state.last_sent_text:
                    wa_send(state.wa_to, state.wa_from, text)
                    state.last_sent_text = text

        elif event["type"] == "done":
            # 完成：發送最終完整回覆
            if state.full_reply and state.full_reply != state.last_sent_text:
                wa_send(state.wa_to, state.wa_from, state.full_reply)
            logging.info(f"✅ WhatsApp 回覆完成: {state.full_reply[:50]}...")
            _wa_states.pop(wa_to, None)

    except Exception as e:
        logging.error(f"❌ WhatsApp 流式回調出錯: {e}")
        _wa_states.pop(wa_to, None)

# ================== Flask Webhook ==================
app = Flask(__name__)

@app.route("/whatsapp", methods=["POST"])
def whatsapp_webhook():
    """接收 Twilio 的 WhatsApp 訊息 webhook"""
    wa_from = request.form.get("From", "")       # 用戶 WhatsApp 號碼
    wa_to = request.form.get("To", "")           # Twilio 號碼
    body = request.form.get("Body", "").strip()
    wa_from_num = wa_from.replace("whatsapp:", "").strip()

    logging.info(f"📩 WhatsApp 收到: from={wa_from_num} body={body[:60]}")

    # 權限檢查
    if MOK_WS_ALLOWED and wa_from_num not in MOK_WS_ALLOWED:
        logging.warning(f"⛔ 未授權訪問: {wa_from_num}")
        wa_send(wa_to, wa_from, UNAUTHORIZED_MSG)
        return str(MessagingResponse()), 200

    if not body:
        return str(MessagingResponse()), 200

    chat_id = f"wa_{wa_from_num}"

    # 處理命令（以 / 開頭）- 同步處理
    if body.startswith("/"):
        try:
            from mokagi import tool_handler
            result = tool_handler.process_message(
                user_text=body,
                chat_id=chat_id,
                ollama_api=mokagi.OLLAMA_API,
                model_name=mokagi.MOK_MODEL_NAME,
                cmd_map=tool_handler.get_cmd_map(),
                tools=tool_handler.get_tools()
            )
            if result:
                if result.startswith("CONFIRM_SPLIT:"):
                    content = result[len("CONFIRM_SPLIT:"):]
                    if "---CONFIRM_SPLIT---" in content:
                        parts = content.split("---CONFIRM_SPLIT---", 1)
                        wa_send(wa_to, wa_from, parts[0].strip())
                        wa_send(wa_to, wa_from, parts[1].strip())
                    else:
                        wa_send(wa_to, wa_from, result)
                else:
                    wa_send(wa_to, wa_from, result)
            return str(MessagingResponse()), 200
        except Exception as e:
            logging.exception(f"❌ 命令處理失敗: {e}")
            wa_send(wa_to, wa_from, f"❌ 命令執行失敗: {str(e)[:200]}")
            return str(MessagingResponse()), 200

    # 普通消息：背景非同步處理
    state = WACallbackState(wa_to, wa_from)
    _wa_states[wa_to] = state

    # 在背景事件循環中處理
    async def process_async():
        try:
            await process_message(
                user_id=chat_id,
                text=body,
                stream_callback=lambda e: asyncio.create_task(wa_stream_callback(wa_to, e))
            )
        except Exception as e:
            logging.exception(f"❌ 處理 WhatsApp 消息時出錯")
            wa_send(wa_to, wa_from, f"❌ 處理消息時出錯: {str(e)[:200]}")
            _wa_states.pop(wa_to, None)

    if not worker.submit(process_async()):
        wa_send(wa_to, wa_from, "❌ 系統忙碌中，請稍後再試")

    return str(MessagingResponse()), 200


@app.route("/health", methods=["GET"])
def health():
    return jsonify({
        "status": "ok",
        "agent": MOK_AGENT_NAME,
        "active_sessions": len(_wa_states)
    }), 200


# ================== 啟動 ==================
def main():
    reload_tools()
    worker.start()
    print(f"\n{'='*50}")
    print(f"✅ {MOK_AGENT_NAME} WhatsApp 前端啟動中...")
    print(f"📡 監聽端口: {MOK_WS_PORT}")
    print(f"📞 Webhook URL: POST /whatsapp")
    print(f"   Twilio Console → WhatsApp → Sandbox 設置:")
    print(f"   WHEN A MESSAGE COMES IN: https://你的域名/whatsapp")
    print(f"   ！！！重要！！！")
    print(f"   必須在配置文件中加入以下三項：")
    print(f"   MOK_WS_SID=你的Twilio_Account_SID")
    print(f"   MOK_WS_TOKEN=你的Twilio_Auth_Token")
    print(f"   MOK_WS_FROM=whatsapp:+14155238886")
    if MOK_WS_ALLOWED:
        print(f"🔒 白名單: {', '.join(MOK_WS_ALLOWED)}")
    else:
        print(f"⚠️  未設置白名單（MOK_WS_ALLOWED），所有人均可使用")
    print(f"{'='*50}\n")
    app.run(host="0.0.0.0", port=MOK_WS_PORT, debug=False, use_reloader=False)

if __name__ == "__main__":
    main()
