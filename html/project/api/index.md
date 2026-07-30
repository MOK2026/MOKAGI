

# MOKAGI 開源 Web 聊天接口 — 使用說明

> **設計目標**：最精簡但詳細，給 LLM 看的完整使用說明  
> **版本**：2026-07-30  
> **倉庫**：https://github.com/MOK2026/MOKAGI/  
> **api.js**：https://64071881.xyz/static/api.js

---

## 🚀 30 秒嵌入

在 HTML 的 `</body>` 前加入：

```html
<script src="https://64071881.xyz/static/api.js"></script>
```

完成。頁面右下角出現 AI 客服氣泡。

> ⚠️ 後端需運行 `mok_web.py`（Socket.IO 伺服器），Agent 設定檔放在 `~/.mok/agent/<名稱>/agent.md`

---

## ⚙️ 配置項總覽

在 `api.js` **之前**設定 `window` 變數：

| window 變數 | 類型 | 預設值 | 說明 |
|---|---|---|---|
| `MOKAGI_AGENT` | string | `"客服"` | Agent 名稱 |
| `MOKAGI_SERVER` | string | 當前網域 | Socket.IO 後端地址 |
| `MOKAGI_USER_ID` | string | 自動 UUID | 用戶識別（持久存 localStorage） |
| `MOKAGI_AGENT_ICON` | string | `"🤖"` | 氣泡圖示 |
| `MOKAGI_THEME` | string | `"#4A90D9"` | 主題色 |
| `agent_soul` | string | `""` | AI 性格提示詞 |
| `contact` | string | `""` | 緊急聯絡方式 |
| `title` | string | `"在線客服"` | 聊天窗標題 |
| `position` | string | `"bottom-right"` | 氣泡位置 |
| `quickLinks` | array | `[]` | ⭐ 快速查詢按鈕（見下方） |

---

## 🔘 快速查詢按鈕 `window.quickLinks`

```html
<!-- 在 api.js 之前設定 -->
<script>
window.quickLinks = [
    { text: "📦 查訂單",   query: "我想查詢訂單狀態" },
    { text: "🔄 退換貨",   query: "我想申請退換貨" },
    { text: "💰 運費查詢", query: "運費怎麼算？" },
    { text: "📞 聯絡客服", query: "我要聯絡客服人員" },
];
</script>
<script src="https://64071881.xyz/static/api.js"></script>
```

**技術實現**（api.js 第 146-155 行）：事件代理監聽 `.mokagi-quick-link` 點擊：

```js
messagesDiv.addEventListener("click", (e) => {
    const btn = e.target.closest(".mokagi-quick-link");
    if (!btn) return;
    const query = btn.getAttribute("data-query");
    if (query) {
        addMessage("user", query);              // 顯示用戶消息
        socket.emit("chat_message", getPayload(query)); // 發送後端
    }
});
```

---

## 📦 完整嵌入範例

```html
<!DOCTYPE html>
<html>
<head><meta charset="UTF-8"></head>
<body>
  <h1>我的商店</h1>

  <script>
    window.MOKAGI_AGENT      = "客服";
    window.MOKAGI_SERVER     = "https://64071881.xyz";
    window.MOKAGI_AGENT_ICON = "💬";
    window.agent_soul       = "你是一位熱情的網店客服，語氣親切。";
    window.contact          = "客服專線：0800-123-456";
    window.quickLinks       = [
      { text: "📦 查訂單", query: "我想查詢訂單狀態" },
      { text: "🔄 退換貨", query: "退換貨政策是什麼？" },
    ];
  </script>

  <script src="https://64071881.xyz/static/api.js"></script>
</body>
</html>
```

---

## 🔌 後端接口：mok_web.py

前端透過 Socket.IO 發送 `chat_message` 事件：

| 欄位 | 類型 | 必填 | 說明 |
|---|---|---|---|
| `message` | string | ✅ | 用戶訊息文字 |
| `agent` | string | ✅ | Agent 名稱 |
| `user_id` | string | | 用戶識別碼 |
| `url` | string | | 頁面網址 |
| `agent_soul` | string | | AI 性格提示詞 |
| `page_url` | string | | 頁面網址（備份） |
| `page_title` | string | | 頁面標題 |
| `context_files` | array | | 上下文檔案，預設 `["agent.md"]` |

### 事件流向

| 事件名 | 方向 | 說明 |
|---|---|---|
| `chat_message` | 前端→後端 | 用戶發送訊息 |
| `chat_reply` | 後端→前端 | AI 完整回覆 |
| `chat_stream` | 後端→前端 | ⭐ AI 流式回覆 |

### 後端範例

```python
@socketio.on("chat_message")
def handle_chat_message(data):
    user_msg    = data.get("message", "").strip()
    agent_name  = data.get("agent", "")
    user_id     = data.get("user_id")
    page_url    = data.get("url", "")
    agent_soul  = data.get("agent_soul", "")

    reply = call_ai_agent(agent_name, user_msg, agent_soul)
    socketio.emit("chat_reply", {"message": reply})

    # 或流式回覆
    for chunk in call_ai_stream(agent_name, user_msg):
        socketio.emit("chat_stream", {"type": "reply", "content": chunk})
```

---

## 🤖 給 LLM 的回答模板

當用戶問「如何嵌入 MOKAGI 聊天」時：

1. 在 `</body>` 前加入 `<script src="https://64071881.xyz/static/api.js"></script>`
2. （可選）在 api.js 之前設定 `window.MOKAGI_AGENT` 指定 Agent
3. （可選）設定 `window.quickLinks` 加入快速查詢按鈕
4. 後端需運行 Socket.IO 伺服器，監聽 `chat_message` 事件
5. Agent 設定檔：`~/.mok/agent/<名稱>/agent.md`

> 💡 api.js 自動收集 `document.title`、當前 URL，包裝成增強訊息發送。

---

## 📤 Agent 發佈

`.agent` 設定檔可上傳到 MOKAGI 官方平台：https://github.com/MOK2026/MOKAGI/('chat_message')
def handle_chat_message(data):
    # 解析前端數據
    user_msg = data.get('message', '').strip() # 獲取用戶消息
    agent_name = data.get('agent', '')  # 獲取 agent 名稱
    user_id = data.get('user_id')   # 獲取 user_id（可選）
    page_url = data.get('url', '')   # 獲取 URL

    agent_soul = data.get('agent_soul', '')   # 獲取 agent_soul（可選）


===

# api.js

負責收集用戶消息到 mok_web.py

    // 配置項（可由外部覆蓋）
    const CONFIG = {
        
        // 後端必須配置
        agent: window.MOKAGI_AGENT || '客服',                    // 默認使用莫氏 Agent
        user_id: window.MOKAGI_USER_ID || localStorage.getItem('mokagi_user_id') || generateUUID(),
        server: window.MOKAGI_SERVER || window.location.origin, // 後端服務器地址

        // 其他配置可選
        agent_soul: window.agent_soul || '',  // 默認 Agent Soul（可選）

        // 前端 UI 配置
        position: window.position || 'bottom-right',
        title: window.title || '在線客服',
        sayHi: window.agent_soul || '✅ 已連接，歡迎使用！',
        saySorry: window.agent_soul || '⚠️ 連接已斷開，嘗試重連...',
        agentIcon: window.MOKAGI_AGENT_ICON || '🤖',   // 新增：Agent 圖標
        theme: window.MOKAGI_THEME || '#4A90D9',

    };
    console.log('CONFIG 初始化:', CONFIG);


        function getPayload(text) {
            // 白名單提取 定義需要發送的後端字段
            const keys = ['agent', 'user_id', 'agent_soul'];
            const payload = { message: text, url: window.location.href };
            keys.forEach(key => { if (CONFIG[key] !== undefined) payload[key] = CONFIG[key]; });
            return payload;
        }

===

# index.html

任何網頁
使用 web_fetch 工具抓取上述客服頁面內容 +
window.agent_soul +
主機角色文件 soul/agent.md
來回答

    <script>
      window.MOKAGI_AGENT = '小美';
      window.MOKAGI_USER_ID = localStorage.getItem('mokagi_user_id') || undefined;
      window.contact = 'https://wa.me/85264071181/?text=莫生我要查詢人力資源/' // ai無法回答的緊急聯絡
      window.MOKAGI_SERVER = 'https://64071881.xyz';
      


      // 其他配置可選
      //window.agent_soul = '我是一個智能客服小美，專注於協助用戶解決問題，提供即時幫助和資訊。我的目標是確保每位用戶都能獲得最佳的使用體驗。';
      
      // 前端 UI 配置
      window.position = 'bottom-right';
      //window.title = '在線客服';
      //window.sayHi = '✅ 已連接，歡迎使用6！';
      //window.saySorry = '⚠️ 連接已斷開，嘗試重連...';
      //window.MOKAGI_AGENT_ICON = '🤖';  // 任意 Emoji 或純文本
      // window.MOKAGI_THEME = '#4A90D9'

      
    </script>
    <script src="https://64071881.xyz/static/api.js"></script>












