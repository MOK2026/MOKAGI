

# 202607220448
  
  mokagi 開源 web 聊天接口
  https://github.com/MOK2026/MOKAGI/


https://64071181.github.io/mokagi開源web聊天接口.md


![mokagi 開源 web 聊天接口](image.png)



===

.agent 必須上傳到 mokagi官方




===

# mok_web.py
@socketio.on('chat_message')
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

    <script>
      window.MOKAGI_AGENT = '汐';
      window.MOKAGI_USER_ID = localStorage.getItem('mokagi_user_id') || undefined;
      window.MOKAGI_SERVER = 'https://64071881.xyz';
      window.position = 'bottom-right';


      // 其他配置可選
      //window.agent_soul = '我是一個智能客服小美，專注於協助用戶解決問題，提供即時幫助和資訊。我的目標是確保每位用戶都能獲得最佳的使用體驗。';
      
      // 前端 UI 配置
      //window.title = '在線客服';
      //window.sayHi = '✅ 已連接，歡迎使用6！';
      //window.saySorry = '⚠️ 連接已斷開，嘗試重連...';
      //window.MOKAGI_AGENT_ICON = '🤖';  // 任意 Emoji 或純文本
      // window.MOKAGI_THEME = '#4A90D9'
      
    </script>
    <script src="https://64071881.xyz/static/api.js"></script>












