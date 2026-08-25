#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
wa_auto.py — WhatsApp Web 自動掃描 /信息 並回覆（常駐輪詢版）
放置：/home/ubuntu/.mok/skill/whatsappWeb/wa_auto.py

功能：
  1. 啟動持久化 Chromium（browser_profiles/wa（profile=wa），與 browser 工具同設定檔）
  2. 檢查登入（出現 QR → 存 /tmp/wa_qr.png 並通知，等待用戶掃碼）
  3. 掃描前 5 個聊天（最新優先，排除封存區），找出 / 開頭的信息（一次 JS，不逐個點擊）
  4. 對未回覆的 /信息 產生回覆並送出（規則式範本，可擴充）
  5. 驗證發送（輸入框清空）

用法：
  python3 wa_auto.py                 # 跑一輪（可放 cron）
  python3 wa_auto.py --loop 60       # 每 60 秒跑一輪（常駐）

注意：
  - 勿與 browser 工具同時執行（同設定檔會被 lock）
  - 依賴: playwright (pip install playwright && playwright install chromium)
  - 回覆已接入 mokagi LLM（ws客服 靈魂，context_files=['agent.md','user.md']）
  - 回覆失敗時自動 fallback 規則範本 REPLY_RULES
"""
import argparse, json, os, sys, time, html

PROFILE_DIR = os.path.expanduser("/home/ubuntu/.mok/browser_profiles/wa")
WA_URL = "https://web.whatsapp.com"

# ---------- JS 片段（與技能文件同步） ----------
JS_LOGIN_CHECK = """(() => {
  const hasQR = !!document.querySelector('canvas');
  const hasChatList = !!document.querySelector('#side');
  return JSON.stringify({ hasQR, hasChatList });
})()"""

JS_SCAN_ALL = """(() => {
  const MAX_SCAN = 5;  // 🔧 只掃前 N 個（最新優先），排除封存區
  const chats = Array.from(document.querySelectorAll('#pane-side div[role="row"]'))
    .filter(row => !row.closest('[data-testid="chat-list-archive"]'))
    .slice(0, MAX_SCAN)
    .map((row, idx) => {
    const titleEl = row.querySelector('span[dir="auto"]') || row.querySelector('div[title]');
    const title = titleEl ? (titleEl.getAttribute('title') || titleEl.innerText || '') : (row.innerText.split('\\n')[0] || '');
    const text = row.innerText.replace(/\\n/g, ' | ').slice(0, 200);
    return { title: title.trim(), text, index: idx };
  });
  return JSON.stringify({ total: chats.length, chats });
})()"""

JS_READ_LAST = """(() => {
  const main = document.querySelector('#main');
  if (!main) return JSON.stringify([]);
  const msgs = Array.from(main.querySelectorAll('[data-testid="msg-container"]')).slice(-10).map(el => {
    // 在訊息容器內找 copyable-text，再向上找氣泡背景色（綠=OUT 自己發出，白=IN 收到）
    const copy = el.querySelector('div[class*="copyable-text"]');
    let node = copy, bg = '';
    for (let i = 0; i < 6 && node; i++) {
      const b = getComputedStyle(node).backgroundColor;
      if (b && b !== 'rgba(0, 0, 0, 0)' && b !== 'transparent') { bg = b; break; }
      node = node.parentElement;
    }
    const isOut = bg === 'rgb(217, 253, 211)';
    const text = (copy || el).innerText.replace(/\\n/g, ' | ').slice(0, 150);
    return (isOut ? 'OUT:' : 'IN:') + ' ' + text;
  });
  return JSON.stringify(msgs);
})()"""

JS_VERIFY_SENT = """(() => {
  const box = document.querySelector('div[contenteditable="true"][data-tab="10"]');
  return JSON.stringify({ inputBoxCleared: box ? box.innerText.trim() === '' : false });
})()"""

# ---------- 回覆規則（可擴充；要接 LLM 就改 reply_hook） ----------
REPLY_RULES = {
    "/早晨": "早晨！☀️ 今日早餐建議：雞蛋三文治+無糖豆漿，或皮蛋瘦肉粥+腸粉，有蛋白質先夠醒神～😊",
    "/信息": "你好！收到你的信息啦～ 我是客服，有什麼可以幫到你？",
    "/help": "可用指令：/早晨、/信息、/help",
    "/hi": "Hi~ 👋 有什麼可以幫到你？",
    "/hello": "Hello! 👋 How can I help you today?",
}

def reply_hook(cmd: str, sender: str) -> str:
    """回覆產生器：優先接 mokagi LLM（ws客服 靈魂，只載入 agent.md + user.md）；
    失敗時 fallback 規則表。"""
    try:
        import asyncio, sys, threading
        core_dir = "/home/ubuntu/.mok/core"
        if core_dir not in sys.path:
            sys.path.insert(0, core_dir)
        from mokagi import process_message
        result = {}
        def _run_llm():
            try:
                result['reply'] = asyncio.run(process_message(
                    user_id=f"wa_{sender}",
                    text=cmd,
                    agent_name="ws客服",
                    context_files=["agent.md", "user.md"],
                ))
            except Exception as e:
                result['error'] = e
        t = threading.Thread(target=_run_llm, daemon=True)
        t.start()
        t.join(timeout=120)
        if 'error' in result:
            raise result['error']
        reply = result.get('reply')
        if reply and reply.strip() and reply.strip() != "（無回覆）":
            return reply.strip()
    except Exception as e:
        print(f"[LLM-ERR] {sender}: {e}", flush=True)
    # 🔧 LLM 不可用時：誠實告知客戶，不用機械式預設文字敷衍
    return f"抱歉，AI 客服暫時無法連線，請稍後再試。您的訊息「{cmd}」已收到，稍後會有真人跟進～"

# ---------- 主流程 ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--loop", type=int, default=0, help="常駐模式，每 N 秒跑一輪（0=只跑一輪）")
    args = ap.parse_args()

    from playwright.sync_api import sync_playwright

    while True:
        try:
            run_once()
        except Exception as e:
            print(f"[{time.strftime('%H:%M:%S')}] 錯誤: {e}", flush=True)
        if args.loop <= 0:
            break
        time.sleep(args.loop)

def run_once():
    from playwright.sync_api import sync_playwright
    with sync_playwright() as p:
        ctx = p.chromium.launch_persistent_context(
            PROFILE_DIR,
            headless=False,
            args=["--no-sandbox", "--disable-dev-shm-usage"],
        )
        page = ctx.pages[0] if ctx.pages else ctx.new_page()
        page.goto(WA_URL, wait_until="domcontentloaded")
        page.wait_for_timeout(8000)

        # 1. 登入檢查
        st = json.loads(page.evaluate(JS_LOGIN_CHECK))
        if st.get("hasQR") and not st.get("hasChatList"):
            page.screenshot(path="/tmp/wa_qr.png")
            print("[QR] 需要掃碼登入！QR 已存 /tmp/wa_qr.png，請在 60 秒內掃描", flush=True)
            # 等待登入（最多 90 秒）
            for _ in range(45):
                page.wait_for_timeout(2000)
                st = json.loads(page.evaluate(JS_LOGIN_CHECK))
                if st.get("hasChatList"):
                    print("[OK] 已登入", flush=True)
                    break
            else:
                print("[QR] 等待超時，未完成登入", flush=True)
                ctx.close()
                return
        elif not st.get("hasChatList"):
            # 首次載入可能較慢，最多再等 15 秒
            for _ in range(5):
                page.wait_for_timeout(3000)
                st = json.loads(page.evaluate(JS_LOGIN_CHECK))
                if st.get("hasChatList"):
                    break
            if not st.get("hasChatList"):
                print("[ERR] 無法確認登入狀態（已重試）", flush=True)
                ctx.close()
                return
            print("[OK] 已登入（延遲確認）", flush=True)
        else:
            print("[OK] 已登入（免 QR）", flush=True)

        # 2. 掃描所有聊天（任何新訊息都用 LLM 回覆）
        scan = json.loads(page.evaluate(JS_SCAN_ALL))
        print(f"[SCAN] 共 {scan['total']} 個聊天", flush=True)
        for chat in scan["chats"]:
            title = chat["title"]
            # 點開聊天（WhatsApp Business 版 title 可能為空，改用 index 定位 + Playwright 真實滑鼠事件）
            idx = chat.get("index", 0)
            try:
                page.locator('#pane-side div[role="row"]').nth(idx).click(timeout=5000)
            except Exception as e:
                print(f"[SKIP] 點不開 {title}（index={idx}）：{e}", flush=True)
                continue
            page.wait_for_timeout(1800)

            # 讀最後訊息判斷是否已回覆（headless 載入慢，重試直到非空）
            last = []
            for _try in range(4):
                last = json.loads(page.evaluate(JS_READ_LAST))
                if last:
                    break
                page.wait_for_timeout(1500)
            if not last:
                print(f"[SKIP] {title} 讀不到訊息（#main 未載入）", flush=True)
                continue
            if last and last[-1].startswith("OUT:"):
                print(f"[SKIP] {title} 已回覆（最後一條是發出）", flush=True)
                continue

            # 找最後一條 IN 的訊息（只回覆 / 開頭的指令；跳過系統提示）
            cmd = None
            for m in reversed(last):
                if m.startswith("IN:"):
                    body = m[3:].strip()
                    # 跳過 WhatsApp 系統提示（刪除訊息、時間戳等）
                    if body and body.startswith("/") and "已刪除此訊息" not in body and not body.replace(':', '').replace('-', '').strip().isdigit():
                        cmd = body
                        break
            if not cmd:
                print(f"[SKIP] {title} 無新訊息可回", flush=True)
                continue

            reply = reply_hook(cmd, title)
            print(f"[REPLY] {title} <- {cmd}: {reply[:50]}...", flush=True)

            # 輸入並送出
            box = page.query_selector('div[contenteditable="true"][data-tab="10"]')
            if not box:
                print(f"[ERR] {title} 找不到輸入框（需更新 selector）", flush=True)
                continue
            box.click()
            page.keyboard.type(reply)
            page.keyboard.press("Enter")
            page.wait_for_timeout(1200)

            # 驗證
            v = json.loads(page.evaluate(JS_VERIFY_SENT))
            print(f"[SENT] {title} 驗證: {v}", flush=True)

        ctx.close()
        print(f"[DONE] {time.strftime('%Y-%m-%d %H:%M:%S')} 本輪完成", flush=True)

if __name__ == "__main__":
    main()
