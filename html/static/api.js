

// api.js - 可嵌入聊天組件
(function() {
    // 配置項（可由外部覆蓋）
    const CONFIG = {
        
        // 後端必須配置
        agent: window.MOKAGI_AGENT || '客服',                    // 默認使用莫氏 Agent
        user_id: window.MOKAGI_USER_ID || localStorage.getItem('mokagi_user_id') || generateUUID(),
        server: window.MOKAGI_SERVER || window.location.origin, // 後端服務器地址

        // 其他配置可選
        agent_soul: window.agent_soul || '',  // 默認 Agent Soul（可選）
        contact: window.contact || '',  // ai無法回答的緊急聯絡

        // 前端 UI 配置
        position: window.position || 'bottom-right',
        title: window.title || '在線客服',
        sayHi: window.agent_soul || '✅ 已連接，歡迎使用！',
        saySorry: window.agent_soul || '⚠️ 連接已斷開，嘗試重連...',
        quickLinks: window.quickLinks || [],  // 快速查詢：[{text:"查訂單", query:"幫我查訂單"}]
        agentIcon: window.MOKAGI_AGENT_ICON || '🤖',   // 新增：Agent 圖標
        theme: window.MOKAGI_THEME || '#4A90D9',

    };
    console.log('CONFIG 初始化:', CONFIG);


        function getPayload(text) {
            // 白名單提取 定義需要發送後端字段
            const keys = ['agent', 'user_id', 'agent_soul'];
            const pageUrl = window.location.href;
            const pageTitle = document.title || '';
            // 增加接收ai無法回答的緊急聯絡
            const contactInfo = CONFIG.contact;

            // 🔧 客服模式：將頁面網址與系統指令嵌入訊息，讓 Agent 用 web_fetch 查找答案
            const systemHint = '[系統指令] 請優先使用 web_fetch 工具抓取上述客服頁面內容來回答。' +
                '若需要，也可打開頁面上的相關連結取得更多資訊。' +
                '若最終仍無法回答，請告知用戶以下緊急聯絡方式：" + (contactInfo || "請直接聯絡網頁管理員") + "。';
            const enhancedMessage = '【客服頁面】' + pageTitle + '\n網址：' + pageUrl + '\n\n' +
                text + '\n\n' + systemHint;

            const payload = {
                message: enhancedMessage,
                url: pageUrl,
                page_url: pageUrl,
                page_title: pageTitle
            };
            keys.forEach(key => { if (CONFIG[key] !== undefined) payload[key] = CONFIG[key]; });
            // 🔧 API 客服模式：只載入 agent.md（不含 soul.md、user.md）
            payload.context_files = CONFIG.context_files || ['agent.md'];
            return payload;
        }











    // 生成唯一用戶 ID（持久保存在 localStorage）
    function generateUUID() {
        let id = 'xxxxxxxx-xxxx-4xxx-yxxx-xxxxxxxxxxxx'.replace(/[xy]/g, function(c) {
            let r = Math.random() * 16 | 0, v = c === 'x' ? r : (r & 0x3 | 0x8);
            return v.toString(16);
        });
        localStorage.setItem('mokagi_user_id', id);
        return id;
    }

    // 加載 SocketIO 客戶端庫（如果尚未加載）
    function loadSocketIO(callback) {
        if (typeof io !== 'undefined') {
            callback();
            return;
        }
        let script = document.createElement('script');
        script.src = 'https://cdn.socket.io/4.7.2/socket.io.min.js';
        script.onload = callback;
        document.head.appendChild(script);
    }

    // 創建聊天 UI
    function createUI() {
        // 容器（浮動按鈕 + 聊天窗口）
        const container = document.createElement('div');
        container.id = 'mokagi-widget';
        container.style.cssText = `
            position: fixed;
            ${CONFIG.position === 'bottom-right' ? 'right: 20px; bottom: 20px;' : 'left: 20px; bottom: 20px;'}
            z-index: 9999;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        `;

        // 聊天窗口（默認隱藏）
        const chatWindow = document.createElement('div');
        chatWindow.id = 'mokagi-chat-window';
        chatWindow.style.cssText = `
            display: none;
            width: 360px;
            max-height: 500px;
            background: #fff;
            border-radius: 12px;
            box-shadow: 0 8px 30px rgba(0,0,0,0.2);
            overflow: hidden;
            flex-direction: column;
            margin-bottom: 10px;
        `;

        // 標題欄
        const header = document.createElement('div');
        header.style.cssText = `
            background: ${CONFIG.theme};
            color: #fff;
            padding: 12px 16px;
            font-weight: 600;
            display: flex;
            justify-content: space-between;
            align-items: center;
            cursor: pointer;
        `;
        header.innerHTML = `
        <span>${CONFIG.agentIcon} ${CONFIG.agent}</span>
        <a href="https://64071181.github.io/" target="_blank">power by Mokagi</a>
        <span style="font-size:20px;cursor:pointer;" id="mokagi-close-btn">&times;</span></a>`;
        header.querySelector('#mokagi-close-btn').addEventListener('click', toggleChat);

        // 消息區域
        const messagesDiv = document.createElement('div');
        messagesDiv.id = 'mokagi-messages';
        messagesDiv.style.cssText = `
            flex: 1;
            padding: 16px;
            overflow-y: auto;
            max-height: 350px;
            background: #f9f9f9;
            font-size: 14px;
            line-height: 1.6;
        `;
        // 快速查詢連結點擊代理
        messagesDiv.addEventListener('click', (e) => {
            const btn = e.target.closest('.mokagi-quick-link');
            if (!btn) return;
            const query = btn.getAttribute('data-query');
            if (query) {
                addMessage('user', query);
                if (socket) {
                    socket.emit('chat_message', getPayload(query));
                }
            }
        });

        // 輸入區域
        const inputArea = document.createElement('div');
        inputArea.style.cssText = `
            padding: 12px;
            border-top: 1px solid #eee;
            display: flex;
            background: #fff;
        `;
        const input = document.createElement('input');
        input.type = 'text';
        input.placeholder = '輸入消息...';
        input.style.cssText = `
            flex: 1;
            border: 1px solid #ddd;
            border-radius: 20px;
            padding: 8px 14px;
            outline: none;
            font-size: 14px;
        `;
        const sendBtn = document.createElement('button');
        sendBtn.textContent = '發送';
        sendBtn.style.cssText = `
            margin-left: 8px;
            background: ${CONFIG.theme};
            color: #fff;
            border: none;
            border-radius: 20px;
            padding: 8px 16px;
            cursor: pointer;
            font-size: 14px;
        `;
        inputArea.appendChild(input);
        inputArea.appendChild(sendBtn);

        chatWindow.appendChild(header);
        chatWindow.appendChild(messagesDiv);
        chatWindow.appendChild(inputArea);

        // 浮動按鈕
        const toggleBtn = document.createElement('button');
        toggleBtn.id = 'mokagi-toggle-btn';
        toggleBtn.textContent = CONFIG.agentIcon;
        toggleBtn.style.cssText = `
            width: 56px;
            height: 56px;
            border-radius: 50%;
            background: rgba(0,0,0,0);
            color: #fff;
            border: none;
            font-size: 28px;
            box-shadow: 0 4px 12px rgba(0,0,0,0.15);
            cursor: pointer;
            transition: transform 0.2s;
        `;
        toggleBtn.addEventListener('mouseenter', () => toggleBtn.style.transform = 'scale(1.05)');
        toggleBtn.addEventListener('mouseleave', () => toggleBtn.style.transform = 'scale(1)');
        toggleBtn.addEventListener('click', toggleChat);

        container.appendChild(chatWindow);
        container.appendChild(toggleBtn);
        document.body.appendChild(container);

        // 切換顯示
        function toggleChat() {
            const isOpen = chatWindow.style.display === 'flex';
            chatWindow.style.display = isOpen ? 'none' : 'flex';
            if (!isOpen) {
                input.focus();
            }
        }




        // 發送消息
        function sendMessage() {
            const text = input.value.trim();
            if (!text) return;
            addMessage('user', text);
            input.value = '';
            if (socket) {
                socket.emit('chat_message', getPayload(text));
                console.log('📤 發送數據:', getPayload(text));
            } else {
                addMessage('assistant', '⚠️ 連接未就緒，請稍後重試。');
            }
        }



        sendBtn.addEventListener('click', sendMessage);
        input.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') sendMessage();
        });

        // 添加消息到界面
        function addMessage(role, content) {
            const msgDiv = document.createElement('div');
            msgDiv.style.cssText = `
                margin-bottom: 12px;
                display: flex;
                flex-direction: ${role === 'user' ? 'row-reverse' : 'row'};
            `;
            const bubble = document.createElement('div');
            bubble.style.cssText = `
                max-width: 80%;
                background: ${role === 'user' ? CONFIG.theme : '#e9ecef'};
                color: ${role === 'user' ? '#fff' : '#333'};
                padding: 8px 14px;
                border-radius: 18px;
                word-break: break-word;
                white-space: pre-wrap;
                font-size: 14px;
            `;
            // 支援 HTML 渲染（以 __HTML__ 前綴標記）
            if (typeof content === 'string' && content.startsWith('__HTML__')) {
                bubble.innerHTML = content.substring(8);
            } else {
                bubble.textContent = content;
            }
            msgDiv.appendChild(bubble);
            messagesDiv.appendChild(msgDiv);
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
        }

        // 暴露添加消息給外部（用於流式更新）
        window.mokagiWidget = { addMessage, toggleChat };
        return { addMessage, sendMessage };
    }

    // 建立含快速查詢連結的歡迎訊息
    function buildWelcomeHTML() {
        const base = CONFIG.sayHi;
        const links = CONFIG.quickLinks;
        if (!links || links.length === 0) return base;
        let html = '__HTML__' + base + '<div style="margin-top:10px;display:flex;flex-wrap:wrap;gap:6px;">';
        links.forEach((link, i) => {
            const safeQuery = String(link.query || '').replace(/'/g, '&#39;').replace(/"/g, '&quot;');
            const safeText = String(link.text || '').replace(/</g, '&lt;').replace(/>/g, '&gt;');
            html += '<button class="mokagi-quick-link" data-query="' + safeQuery + '" style="display:inline-block;padding:6px 12px;background:#4A90D9;color:#fff;border:none;border-radius:16px;text-decoration:none;font-size:12px;cursor:pointer;">' + safeText + '</button>';
        });
        html += '</div>';
        return html;
    }

    // 初始化 SocketIO 連接
    let socket = null;
    let addMessageFn = null;

    function initSocket() {
        console.log('MOKAGI_SERVER:', CONFIG.server);
        socket = io(CONFIG.server, {
            transports: ['websocket', 'polling'],
            reconnection: true,
        });

        socket.on('connect_error', (err) => {
            console.error('Socket 連線錯誤:', err);
            addMessageFn('assistant', '❌ 無法連線至客服伺服器');
        });


        socket.on('connect', () => {
            addMessageFn('assistant', buildWelcomeHTML());
        });

        socket.on('disconnect', () => {
            addMessageFn('assistant', CONFIG.saySorry);
        });

        // 監聽流式回覆
        socket.on('chat_stream', (event) => {
            if (event.type === 'reply') {
                // 處理流式回覆（累積顯示）
                const msgId = 'stream-msg-' + Date.now();
                // 簡單實現：直接完整追加（如果需要流式效果，可維護一個臨時消息）
                // 這裡我們直接創建或更新最後一條消息
                const lastBubble = document.querySelector('#mokagi-messages .stream-msg div');
                if (lastBubble) {
                    lastBubble.textContent += event.content;
                } else {
                    const msgDiv = document.createElement('div');
                    msgDiv.className = 'stream-msg';
                    msgDiv.style.cssText = `
                        margin-bottom: 12px;
                        display: flex;
                        flex-direction: row;
                    `;
                    const bubble = document.createElement('div');
                    bubble.style.cssText = `
                        max-width: 80%;
                        background: #e9ecef;
                        color: #333;
                        padding: 8px 14px;
                        border-radius: 18px;
                        word-break: break-word;
                        white-space: pre-wrap;
                        font-size: 14px;
                    `;
                    bubble.textContent = event.content;
                    msgDiv.appendChild(bubble);
                    document.querySelector('#mokagi-messages').appendChild(msgDiv);
                }
                // 滾動到底部
                const messagesDiv = document.querySelector('#mokagi-messages');
                messagesDiv.scrollTop = messagesDiv.scrollHeight;


                
            } else if (event.type === 'think') {
                // 累積顯示思考內容（只顯示一個 💭 在開頭）
                const messagesDiv = document.querySelector('#mokagi-messages');
                let thinkMsg = messagesDiv.querySelector('.think-msg');
                if (!thinkMsg) {
                    // 首次創建
                    thinkMsg = document.createElement('div');
                    thinkMsg.className = 'think-msg';
                    thinkMsg.style.cssText = `
                        margin-bottom: 12px;
                        display: flex;
                        flex-direction: row;
                    `;
                    const bubble = document.createElement('div');
                    bubble.style.cssText = `
                        max-width: 80%;
                        background: #e9ecef;
                        color: #333;
                        padding: 8px 14px;
                        border-radius: 18px;
                        word-break: break-word;
                        white-space: pre-wrap;
                        font-size: 14px;
                    `;
                    // 先放一個 💭 前綴（但後續追加時不再重複添加）
                    bubble.textContent = '💭 ' + event.content;
                    thinkMsg.appendChild(bubble);
                    messagesDiv.appendChild(thinkMsg);
                } else {
                    // 已存在，追加內容（不再加 💭）
                    const bubble = thinkMsg.querySelector('div');
                    bubble.textContent += event.content;
                }
                messagesDiv.scrollTop = messagesDiv.scrollHeight;
            } else if (event.type === 'done') {
                // 完成，移除流式標記
                const msgs = document.querySelectorAll('#mokagi-messages .stream-msg');
                msgs.forEach(el => el.classList.remove('stream-msg'));
            }
        });
    }

    // 啟動 Widget
    function initWidget() {
        try {
            loadSocketIO(() => {
                try {
                    const ui = createUI();
                    addMessageFn = ui.addMessage;
                    initSocket();
                } catch(e) {
                    console.error('UI 或 Socket 初始化失敗:', e);
                }
            });
        } catch(e) {
            console.error('Widget 啟動失敗:', e);
        }
    }

    // 如果頁面已加載完成，直接啟動
    if (document.readyState === 'loading') {
        console.log('Page is loading...');
        document.addEventListener('DOMContentLoaded', initWidget);
    } else {
        console.log('加載完成，初始化 Widget...');
        initWidget();
    }
})();








// https://chat.deepseek.com/share/on5ltjxctqpr7jetij
//必須在 Cloudflare Acces Zero Trust Applications
//https://dash.cloudflare.com/e5901af06f1c166b2d76e9ea9db2e96f/one/access-controls/policies
//64071881.xyz/static/api.js
//64071881.xyz/socket.io/
//原則 所有人 Bypass


`
1. 前端發送消息
用戶在聊天窗口輸入消息，點擊“發送”或按回車。

api.js 中的 sendMessage() 函數被觸發，執行：

javascript
socket.emit('chat_message', {
    message: text,
    agent: CONFIG.agent,
    user_id: CONFIG.user_id,
    url: window.location.href
});


2. 後端接收並處理
後端 mok_web.py 中註冊了 chat_message 事件監聽器：

python
@socketio.on('chat_message')
def handle_chat_message(data):
    # 解析前端數據
    user_msg = data.get('message')
    agent_name = data.get('agent')
    user_id = data.get('user_id')
    # 調用核心處理函數
    asyncio.run(run_with_autofix())

// https://chat.deepseek.com/share/4sl4ywrq11dp09a0hs
// 用法:
<script>
    window.MOKAGI_AGENT = '客服小美';
    window.MOKAGI_AGENT_ICON = '🤖';  // 任意 Emoji 或純文本
    // 其他配置可選
    window.MOKAGI_SERVER = 'https://your-backend.com';
</script>
<script src="https://your-domain.com/static/api.js"></script>
`