

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

        // 前端 UI 配置
        position: window.position || 'bottom-right',
        title: window.title || '在線客服',
        sayHi: window.MOKAGI_SAY_HI || '✅ 已連接，歡迎使用！',
        saySorry: window.MOKAGI_SAY_SORRY || '⚠️ 連接已斷開，嘗試重連...',
        agentIcon: window.MOKAGI_AGENT_ICON || '🤖',   // 新增：Agent 圖標
        theme: window.MOKAGI_THEME || '#4A90D9',

    };
    console.log('CONFIG 初始化:', CONFIG);

    // 🔧 SSE 串流控制器（用於取消舊請求）
    let _activeSSEController = null;

    // 🔧 處理串流事件（SSE 與 Socket.IO 共用）
    function handleStreamEvent(event) {
        if (event.type === 'reply') {
            const content = event.content || '';
            if (!content) return;
            const lastBubble = document.querySelector('#mokagi-messages .stream-msg div');
            if (lastBubble) {
                lastBubble.textContent += content;
            } else {
                const msgDiv = document.createElement('div');
                msgDiv.className = 'stream-msg';
                msgDiv.style.cssText = `margin-bottom: 12px; display: flex; flex-direction: row;`;
                const bubble = document.createElement('div');
                bubble.style.cssText = `max-width: 80%; background: #e9ecef; color: #333; padding: 8px 14px; border-radius: 18px; word-break: break-word; white-space: pre-wrap; font-size: 14px;`;
                bubble.textContent = content;
                msgDiv.appendChild(bubble);
                const messagesDiv = document.querySelector('#mokagi-messages');
                if (messagesDiv) messagesDiv.appendChild(msgDiv);
            }
            const messagesDiv = document.querySelector('#mokagi-messages');
            if (messagesDiv) messagesDiv.scrollTop = messagesDiv.scrollHeight;
        } else if (event.type === 'think') {
            const thinkContent = event.content || '';
            if (!thinkContent) return;
            const messagesDiv = document.querySelector('#mokagi-messages');
            if (!messagesDiv) return;
            let thinkMsg = messagesDiv.querySelector('.think-msg');
            if (!thinkMsg) {
                thinkMsg = document.createElement('div');
                thinkMsg.className = 'think-msg';
                thinkMsg.style.cssText = `margin-bottom: 12px; display: flex; flex-direction: row;`;
                const bubble = document.createElement('div');
                bubble.style.cssText = `max-width: 80%; background: #e9ecef; color: #333; padding: 8px 14px; border-radius: 18px; word-break: break-word; white-space: pre-wrap; font-size: 14px;`;
                bubble.textContent = '💭 ' + thinkContent;
                thinkMsg.appendChild(bubble);
                messagesDiv.appendChild(thinkMsg);
            } else {
                const bubble = thinkMsg.querySelector('div');
                if (bubble) bubble.textContent += thinkContent;
            }
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
        } else if (event.type === 'done') {
            const msgs = document.querySelectorAll('#mokagi-messages .stream-msg');
            msgs.forEach(el => el.classList.remove('stream-msg'));
        }
    }

        function getPayload(text) {
            // 白名單提取 定義需要發送後端字段
            const keys = ['agent', 'user_id', 'agent_soul'];
            const pageUrl = window.location.href;
            const pageTitle = document.title || '';

            // 🔧 客服模式：將頁面網址與系統指令嵌入訊息，讓 Agent 用 web_fetch 查找答案
            const systemHint = '[系統指令] 請優先使用 web_fetch 工具抓取上述客服頁面內容來回答。' +
                '若需要，也可打開頁面上的相關連結取得更多資訊。' +
                '若最終仍無法回答，請告知用戶直接聯絡莫生。';
            const enhancedMessage = '【客服頁面】' + pageTitle + '\n網址：' + pageUrl + '\n\n' +
                text + '\n\n' + systemHint;

            const payload = {
                message: enhancedMessage,
                url: pageUrl,
                page_url: pageUrl,
                page_title: pageTitle,
                source: 'api'
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




        // 🔧 SSE 發送消息（主要傳輸，比 Socket.IO 更穩定）
        async function sendViaSSE(text) {
            if (_activeSSEController) {
                _activeSSEController.abort();
            }
            _activeSSEController = new AbortController();
            const controller = _activeSSEController;

            try {
                const payload = getPayload(text);
                const response = await fetch(CONFIG.server + '/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({
                        message: payload.message,
                        agent: payload.agent,
                        user_id: payload.user_id,
                        context_files: payload.context_files
                    }),
                    signal: controller.signal
                });

                if (!response.ok) {
                    console.error('[sendViaSSE] HTTP 錯誤:', response.status);
                    // 🔧 fallback：HTTP 失敗時走 Socket.IO
                    if (socket && socket.connected) {
                        socket.emit('chat_message', payload);
                    }
                    return;
                }

                const reader = response.body.getReader();
                const decoder = new TextDecoder();
                let buffer = '';

                while (true) {
                    const { done, value } = await reader.read();
                    if (done) break;
                    buffer += decoder.decode(value, { stream: true });
                    const lines = buffer.split('\n');
                    buffer = lines.pop() || '';
                    for (const line of lines) {
                        if (line.startsWith('data: ')) {
                            const dataStr = line.slice(6);
                            if (!dataStr || dataStr === '[DONE]') continue;
                            try {
                                const eventData = JSON.parse(dataStr);
                                handleStreamEvent(eventData);
                            } catch (parseErr) {
                                console.warn('[sendViaSSE] JSON parse error:', parseErr);
                            }
                        }
                    }
                }
            } catch (err) {
                if (err.name === 'AbortError') {
                    console.log('[sendViaSSE] 請求已取消');
                } else {
                    console.error('[sendViaSSE] SSE 失敗:', err);
                    // 🔧 SSE 失敗時走 Socket.IO fallback
                    if (socket && socket.connected) {
                        socket.emit('chat_message', getPayload(text));
                    } else {
                        addMessage('assistant', '⚠️ 連線失敗，請刷新頁面重試。');
                    }
                }
            }
        }

        // 發送消息（SSE 優先，Socket.IO 為備援 — Cloudflare 下 HTTP 比 WebSocket 更穩定）
        function sendMessage() {
            const text = input.value.trim();
            if (!text) return;
            addMessage('user', text);
            input.value = '';
            // 🔧 SSE 優先（HTTP POST + SSE 回應，Cloudflare 下比 WebSocket 可靠）
            // sendViaSSE 內部失敗時會自動回退到 Socket.IO
            sendViaSSE(text);
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
            bubble.textContent = content;
            msgDiv.appendChild(bubble);
            messagesDiv.appendChild(msgDiv);
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
        }

        // 暴露添加消息給外部（用於流式更新）
        window.mokagiWidget = { addMessage, toggleChat };
        return { addMessage, sendMessage };
    }

    // 初始化 SocketIO 連接
    let socket = null;
    let addMessageFn = null;

    function initSocket() {
        console.log('MOKAGI_SERVER:', CONFIG.server);
        socket = io(CONFIG.server, {
            transports: ['websocket', 'polling'],
            reconnection: true,
            reconnectionDelay: 1000,
            reconnectionDelayMax: 10000,
            reconnectionAttempts: Infinity,
            pingTimeout: 120000,
            pingInterval: 30000,
        });

        let _firstConnect = true;
        let _disconnectTimer = null;

        socket.on('connect_error', (err) => {
            console.error('Socket 連線錯誤:', err);
        });

        socket.on('connect', () => {
            if (_disconnectTimer) {
                clearTimeout(_disconnectTimer);
                _disconnectTimer = null;
            }
            if (_firstConnect) {
                _firstConnect = false;
                addMessageFn('assistant', CONFIG.sayHi);
            } else {
                // 重連成功，不顯示訊息（或在控制台記錄）
                console.log('🔄 Socket 已重新連線');
            }
            // 🔧 關鍵：註冊 user_id 房間，讓後端能在 sid 變更時仍找到客戶端
            socket.emit('join_room', { user_id: CONFIG.user_id, agent: CONFIG.agent });
        });

        socket.on('disconnect', (reason) => {
            console.log('Socket 斷線:', reason);
            // 🔧 SSE 活躍中則不顯示斷線訊息（SSE 自己會處理資料傳輸）
            if (_activeSSEController && !_activeSSEController.signal.aborted) {
                console.log('[Socket] SSE 活躍中，略過斷線提示');
                return;
            }
            // 🔧 不立即顯示斷線，等待 8 秒看是否重連成功
            _disconnectTimer = setTimeout(() => {
                // 再次確認 SSE 未在運行
                if (!_activeSSEController || _activeSSEController.signal.aborted) {
                    addMessageFn('assistant', CONFIG.saySorry);
                }
                _disconnectTimer = null;
            }, 8000);
        });

        socket.on('reconnect_attempt', (attempt) => {
            console.log('🔄 重連嘗試 #' + attempt);
        });

        socket.on('reconnect_failed', () => {
            if (_disconnectTimer) {
                clearTimeout(_disconnectTimer);
                _disconnectTimer = null;
            }
            addMessageFn('assistant', '❌ 無法重新連線，請刷新頁面');
        });

        // 監聽流式回覆（共用 handleStreamEvent，SSE 與 Socket.IO 一致）
        socket.on('chat_stream', (event) => {
            handleStreamEvent(event);
        });

        // 🔧 監聽完整回覆（後備機制：當 chat_stream 沒收到時使用）
        socket.on('chat_reply', (data) => {
            if (data && data.message) {
                // 先清除可能殘留的 stream-msg
                const streamMsgs = document.querySelectorAll('#mokagi-messages .stream-msg');
                streamMsgs.forEach(el => el.classList.remove('stream-msg'));
                // 顯示完整回覆
                addMessageFn('assistant', data.message);
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
//64071181.xyz/static/api.js
//64071181.xyz/socket.io/
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