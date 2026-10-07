// api.js - 可嵌入聊天組件
// ============================================================
// 🚀 版本自檢（Cache-Busting）：每次載入都確保拿到最新版
//   改版時只需更新 __MOKAGI_VER__（格式 YYYYMMDDNN），
//   所有使用者下次開啟頁面即自動載入新版，無需手動清快取。
// ============================================================
(function() {
    // 🔖 目前版本號（改版必改）
    const __MOKAGI_VER__ = "2026100802";

    // 🚀 啟動時自我檢查：若瀏覽器/CDN 快取了舊版，自動換成最新版
    (function selfCheck() {
        if (window.__MOKAGI_SELF_CHECK__) return;
        window.__MOKAGI_SELF_CHECK__ = true;
        // 找到 api.js 自身的 src
        let src = "";
        if (document.currentScript) src = document.currentScript.src;
        if (!src) {
            const scripts = document.getElementsByTagName("script");
            for (let i = scripts.length - 1; i >= 0; i--) {
                const s = scripts[i].src || "";
                if (s.indexOf("api.js") !== -1) { src = s; break; }
            }
        }
        if (!src) return;
        const baseSrc = src.split("?")[0];
        // 防呆：30 秒內已重載過就不重複重載（避免 CDN 異常造成無限循環）
        if (window.__MOKAGI_RELOAD_TS__ && (Date.now() - window.__MOKAGI_RELOAD_TS__) < 30000) return;
        fetch(baseSrc + "?__selfcheck=" + Date.now(), { cache: "no-store" })
            .then(r => r.text())
            .then(code => {
                const m = code.match(/__MOKAGI_VER__\s*=\s*["']([^"']+)["']/);
                if (m && m[1] && m[1] !== __MOKAGI_VER__) {
                    console.warn("[MOKAGI] 偵測到舊版 api.js，自動載入最新版…");
                    window.__MOKAGI_RELOAD_TS__ = Date.now();
                    // 移除舊版 UI，避免閃爍/重複
                    const old = document.getElementById("mokagi-widget");
                    if (old) old.remove();
                    // 注入最新版
                    const s = document.createElement("script");
                    s.src = baseSrc + "?v=" + Date.now();
                    (document.body || document.documentElement).appendChild(s);
                }
            })
            .catch(() => { /* 自檢失敗（如離線/跨域）不影響正常運作 */ });
    })();

    // 配置項（可由外部覆蓋）
    const CONFIG = {
        
        // 後端必須配置
        agent: window.MOKAGI_AGENT || '客服',                    // 默認使用莫氏 Agent
        user_id: resolveWidgetUserId(),
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
        quickLinks: window.quickLinks || [],           // 快速查詢按鈕列表 [{text, query, answer?}, ...]（有 answer 直接顯示、不打 LLM）

        // 🖥️ 沉浸式佈局配置
        desktopLayout: window.MOKAGI_DESKTOP_LAYOUT !== undefined ? window.MOKAGI_DESKTOP_LAYOUT : true,
        chatWidth: window.MOKAGI_CHAT_WIDTH || 380,
        mobileOpacity: window.MOKAGI_MOBILE_OPACITY || 0.35,
        breakpoint: window.MOKAGI_BREAKPOINT || 768,
        showOpacitySlider: window.MOKAGI_SHOW_OPACITY_SLIDER === true,  // 預設隱藏透明度滑桿；設 window.MOKAGI_SHOW_OPACITY_SLIDER=true 可開啟

    };
    console.log('CONFIG 初始化:', CONFIG);

    // ============================================================
    // 📝 內建 Markdown 渲染（無外部依賴，可安全嵌入任何網站）
    // ============================================================
    function _mokEsc(t) {
        return String(t == null ? '' : t)
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
    }
    function _mokInline(t) {
        t = _mokEsc(t);
        var ics = [];
        t = t.replace(/`([^`]+)`/g, function (m, c) { ics.push(c); return '\u0001' + (ics.length - 1) + '\u0001'; });
        t = t.replace(/\*\*\*([^*]+)\*\*\*/g, '<strong><em>$1</em></strong>');
        t = t.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
        t = t.replace(/(^|[^*])\*([^*\n]+)\*/g, '$1<em>$2</em>');
        t = t.replace(/__([^_]+)__/g, '<strong>$1</strong>');
        t = t.replace(/~~([^~]+)~~/g, '<del>$1</del>');
        t = t.replace(/\[([^\]]+)\]\((https?:\/\/[^)\s]+)\)/g, '<a href="$2" target="_blank" rel="noopener noreferrer">$1</a>');
        t = t.replace(/(^|[^"=\w>])(https?:\/\/[^\s<>"]+)/g, '$1<a href="$2" target="_blank" rel="noopener noreferrer">$2</a>');
        t = t.replace(/\u0001(\d+)\u0001/g, function (m, i) { return '<code class="mok-md-code">' + ics[+i] + '</code>'; });
        return t;
    }
    function _mokMd(src) {
        if (src == null) return '';
        var text = String(src);
        var blocks = [];
        text = text.replace(/```[^\n]*\n?([\s\S]*?)```/g, function (m, code) {
            blocks.push(code.replace(/\n+$/, ''));
            return '\u0002CODE' + (blocks.length - 1) + 'CODE\u0002';
        });
        var lines = text.replace(/\r\n/g, '\n').split('\n');
        var out = '', listType = null, para = [], quote = [];
        function closeList() { if (listType) { out += '</' + listType + '>'; listType = null; } }
        function flushPara() { if (para.length) { out += '<p>' + para.map(_mokInline).join('<br>') + '</p>'; para = []; } }
        function flushQuote() { if (quote.length) { out += '<blockquote>' + quote.map(_mokInline).join('<br>') + '</blockquote>'; quote = []; } }
        function flushAll() { flushPara(); flushQuote(); closeList(); }
        for (var i = 0; i < lines.length; i++) {
            var ln = lines[i];
            var cm = ln.match(/^\u0002CODE(\d+)CODE\u0002$/);
            if (cm) { flushAll(); out += '<pre class="mok-md-pre"><code>' + _mokEsc(blocks[+cm[1]]) + '</code></pre>'; continue; }
            if (!ln.trim()) { flushAll(); continue; }
            var h = ln.match(/^\s{0,3}(#{1,6})\s+(.*)$/);
            if (h) { flushAll(); var lv = h[1].length; out += '<h' + lv + '>' + _mokInline(h[2].replace(/\s+#+\s*$/, '')) + '</h' + lv + '>'; continue; }
            if (/^\s{0,3}(-{3,}|\*{3,}|_{3,})\s*$/.test(ln)) { flushAll(); out += '<hr>'; continue; }
            var bq = ln.match(/^\s{0,3}>\s?(.*)$/);
            if (bq) { flushPara(); closeList(); quote.push(bq[1]); continue; } if (ln.indexOf("|") !== -1 && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(lines[i + 1])) { flushAll(); var _cells = function (row) { row = row.trim().replace(/^\|/, "").replace(/\|$/, ""); return row.split("|").map(function (c) { return c.trim(); }); }; var _head = _cells(ln); out += "<table><thead><tr>"; for (var _hi = 0; _hi < _head.length; _hi++) out += "<th>" + _mokInline(_head[_hi]) + "</th>"; out += "</tr></thead><tbody>"; i += 2; while (i < lines.length && lines[i].indexOf("|") !== -1 && lines[i].trim() !== "") { var _row = _cells(lines[i]); out += "<tr>"; for (var _ri = 0; _ri < _head.length; _ri++) out += "<td>" + _mokInline(_row[_ri] || "") + "</td>"; out += "</tr>"; i++; } i--; out += "</tbody></table>"; continue; }
            var ul = ln.match(/^\s*[-*+]\s+(.*)$/);
            if (ul) { flushPara(); flushQuote(); if (listType !== 'ul') { closeList(); out += '<ul>'; listType = 'ul'; } out += '<li>' + _mokInline(ul[1]) + '</li>'; continue; }
            var ol = ln.match(/^\s*\d+[.)]\s+(.*)$/);
            if (ol) { flushPara(); flushQuote(); if (listType !== 'ol') { closeList(); out += '<ol>'; listType = 'ol'; } out += '<li>' + _mokInline(ol[1]) + '</li>'; continue; }
            flushQuote(); closeList(); para.push(ln);
        }
        flushAll();
        out = out.replace(/\u0002CODE(\d+)CODE\u0002/g, function (m, i) { return '<pre class="mok-md-pre"><code>' + _mokEsc(blocks[+i]) + '</code></pre>'; });
        return out;
    }
    function _mokMdCss() {
        if (document.getElementById('mokagi-md-style')) return;
        var st = document.createElement('style');
        st.id = 'mokagi-md-style';
        st.textContent = '.mok-md{white-space:normal !important;line-height:1.7;color:#24292f;}'
        + '.mok-md>*:first-child{margin-top:0;}.mok-md>*:last-child{margin-bottom:0;}'
        + '.mok-md p{margin:.4em 0;}'
        + '.mok-md h1,.mok-md h2,.mok-md h3,.mok-md h4,.mok-md h5,.mok-md h6{margin:.7em 0 .35em;line-height:1.35;font-weight:700;color:#1f2328;}'
        + '.mok-md h1{font-size:1.35em;border-bottom:1px solid rgba(0,0,0,.08);padding-bottom:.2em;}'
        + '.mok-md h2{font-size:1.22em;}.mok-md h3{font-size:1.1em;}'
        + '.mok-md ul,.mok-md ol{margin:.4em 0;padding-left:1.4em;}'
        + '.mok-md li{margin:.18em 0;}'
        + '.mok-md code.mok-md-code{background:rgba(27,31,36,.08);padding:.1em .35em;border-radius:4px;font-family:ui-monospace,Menlo,Consolas,monospace;font-size:.88em;}'
        + '.mok-md pre.mok-md-pre{background:#f6f8fa;border:1px solid #e5e7eb;border-radius:8px;padding:10px 12px;overflow-x:auto;margin:.5em 0;}'
        + '.mok-md pre.mok-md-pre code{background:none;padding:0;font-size:.85em;color:#24292f;white-space:pre;}'
        + '.mok-md a{color:#4A90D9;text-decoration:underline;word-break:break-all;}'
        + '.mok-md blockquote{margin:.5em 0;padding:.2em .8em;border-left:3px solid #d0d7de;color:#57606a;background:rgba(27,31,36,.03);}'
        + '.mok-md hr{border:none;border-top:1px solid rgba(0,0,0,.12);margin:.8em 0;}'
        + '.mok-md table{border-collapse:collapse;width:100%;margin:.5em 0;font-size:.92em;}'
        + '.mok-md th,.mok-md td{border:1px solid #e5e7eb;padding:5px 8px;text-align:left;}'
        + '.mok-md th{background:#f6f8fa;}.mok-md img{max-width:100%;border-radius:6px;}';
        (document.head || document.documentElement).appendChild(st);
    }


    // ========== localStorage 對話歷史管理 ==========
    const HISTORY_KEY = 'mokagi_history_' + CONFIG.user_id + '_' + CONFIG.agent;
    const HISTORY_MAX_SIZE = 4.5 * 1024 * 1024;      // 硬上限：超過立即強制壓縮
    const HISTORY_WARN_SIZE = 3.2 * 1024 * 1024;     // 預警線：接近上限時提示用戶並自動壓縮
    const HISTORY_COMPRESS_THRESHOLD = 100;          // 訊息條數壓縮閾值
    const HISTORY_DISPLAY_LIMIT = 30;                // 流水式顯示時最多顯示最近 N 條

    function _estimateStorageSize() {
        let total = 0;
        for (let key in localStorage) {
            if (localStorage.hasOwnProperty(key)) total += localStorage[key].length + key.length;
        }
        return total * 2;
    }

    function saveMessageToHistory(role, text) {
        try {
            let history = [];
            const raw = localStorage.getItem(HISTORY_KEY);
            if (raw) { try { history = JSON.parse(raw); } catch(e) { history = []; } }
            // 🔧 去重：串流中同一個 assistant 回覆會多次寫入（漸進保存+done），120 秒內合併為同一條；其他角色 3 秒內視為重複覆蓋
            const last = history[history.length - 1];
            const now = Date.now();
            const isAssistantMerge = role === 'assistant' && last && last.role === 'assistant' && (now - last.time) < 120000;
            const isDup = last && last.role === role && (now - last.time) < 3000;
            if (isAssistantMerge || isDup) {
                last.text = text;
                last.time = now;
            } else {
                history.push({ role: role, text: text, time: now });
            }
            if (history.length > HISTORY_COMPRESS_THRESHOLD) history = compressHistoryData(history);
            const json = JSON.stringify(history);
            const estSize = _estimateStorageSize();
            if (estSize > HISTORY_MAX_SIZE) {
                // 超過硬上限：強制壓縮
                history = compressHistoryData(history, true);
                try { localStorage.setItem(HISTORY_KEY, JSON.stringify(history)); }
                catch(e2) { history = history.slice(-20); localStorage.setItem(HISTORY_KEY, JSON.stringify(history)); }
                _notifyUser('💾 對話歷史已自動壓縮以節省空間');
            } else if (estSize > HISTORY_WARN_SIZE) {
                // 接近上限（預警）：提示用戶並自動壓縮舊對話
                history = compressHistoryData(history, true);
                localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
                _notifyUser('⚠️ 瀏覽器儲存空間接近上限，已自動壓縮舊對話以節省空間');
            } else {
                localStorage.setItem(HISTORY_KEY, json);
            }
        } catch(e) {
            console.error('[MOKAGI] 無法保存對話歷史:', e);
            try {
                let history = [];
                const raw = localStorage.getItem(HISTORY_KEY);
                if (raw) { try { history = JSON.parse(raw); } catch(e2){} }
                history = history.slice(-20);
                localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
            } catch(e3) {}
            _notifyUser('⚠️ 瀏覽器儲存空間不足，已保留最近對話');
        }
    }

    function compressHistoryData(history, aggressive) {
        const keepRecent = aggressive ? 30 : 80;
        if (history.length <= keepRecent) return history;
        const recent = history.slice(-keepRecent);
        const old = history.slice(0, -keepRecent);
        const compressed = [];
        for (const msg of old) {
            const last = compressed[compressed.length - 1];
            if (last && last.role === msg.role) {
                last.text += '\n' + msg.text;
                last.time = msg.time;
            } else { compressed.push({...msg}); }
        }
        compressed.push({ role: 'system', text: '📦 以上為壓縮的舊對話（' + old.length + ' 條 → ' + compressed.length + ' 條）', time: Date.now() });
        return compressed.concat(recent);
    }

    function loadHistory() {
        try { const raw = localStorage.getItem(HISTORY_KEY); if (!raw) return []; return JSON.parse(raw); }
        catch(e) { return []; }
    }

    function _notifyUser(msg) {
        const m = document.getElementById('mokagi-messages');
        if (!m) return;
        const d = document.createElement('div');
        d.style.cssText = 'text-align:center;font-size:11px;color:#999;margin:4px 0;';
        d.textContent = msg;
        m.appendChild(d); m.scrollTop = m.scrollHeight;
    }

    function displayHistoryWithFlow(history, messagesDiv) {
        if (!history || history.length === 0) return;
        // 🔧 只顯示最近 N 條，避免全部紀錄一次倒出
        const showList = history.length > HISTORY_DISPLAY_LIMIT ? history.slice(-HISTORY_DISPLAY_LIMIT) : history;
        if (history.length > HISTORY_DISPLAY_LIMIT) {
            const note = document.createElement('div');
            note.style.cssText = 'text-align:center;font-size:11px;color:#999;margin:6px 0;';
            note.textContent = '📦 僅顯示最近 ' + HISTORY_DISPLAY_LIMIT + ' 條對話記錄';
            messagesDiv.appendChild(note);
        }
        let i = 0; const speed = 50;
        function showNext() {
            if (i >= showList.length) { messagesDiv.scrollTop = messagesDiv.scrollHeight; return; }
            const msg = showList[i];
            // 🔧 跳過空訊息（漸進保存可能留下空白）
            if (!msg || !msg.text || !String(msg.text).trim()) { i++; setTimeout(showNext, 10); return; }
            const msgDiv = document.createElement('div');
            const flexDir = msg.role === 'user' ? 'row-reverse' : 'row';
            msgDiv.style.cssText = 'margin-bottom:12px;display:flex;flex-direction:' + flexDir + ';animation:mokagi-fadeInUp 0.35s ease-out both;';
            const bg = msg.role === 'user' ? CONFIG.theme : msg.role === 'system' ? '#f0f0f0' : '#e9ecef';
            const tc = msg.role === 'user' ? '#fff' : msg.role === 'system' ? '#999' : '#333';
            const fs = msg.role === 'system' ? '11px' : '14px';
            const bubble = document.createElement('div');
            bubble.style.cssText = 'max-width:80%;background:' + bg + ';color:' + tc + ';padding:8px 14px;border-radius:18px;word-break:break-word;white-space:pre-wrap;font-size:' + fs + ';';
            if (msg.role === 'user') { bubble.textContent = msg.text; } else { bubble.classList.add('mok-md'); bubble.innerHTML = _mokMd(msg.text); }
            msgDiv.appendChild(bubble);
            messagesDiv.appendChild(msgDiv);
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
            i++; setTimeout(showNext, speed);
        }
        showNext();
    }

    // 🔧 SSE 串流控制器（用於取消舊請求）
    let _activeSSEController = null;
    // 🔧 當主串流中斷時，改用 EventSource 對同一 session 續流
    let _activeEventSource = null;
    let _eventSourceRetryTimer = null;
    let _eventSourceRetryCount = 0;
    const _MAX_EVENTSOURCE_RETRIES = 3;
    let _lastSseSessionId = '';
    // 🔧 累積目前 SSE 串流回覆文字，於 done 事件時寫入 localStorage
    let _pendingReplyText = '';
    // 🔧 防重複：雙 room 送達時同一事件會連續收到兩次，跳過第二次
    let _lastStreamSig = '';
    // 🔧 漸進保存計時器：串流中定期將回覆寫入 localStorage，防止 done 未收到時丟失回覆
    let _progressiveSaveTimer = null;
    function _scheduleProgressiveSave() {
        if (_progressiveSaveTimer) clearTimeout(_progressiveSaveTimer);
        _progressiveSaveTimer = setTimeout(() => {
            _progressiveSaveTimer = null;
            if (_pendingReplyText) saveMessageToHistory('assistant', _pendingReplyText);
        }, 2000);
    }
    function _flushPendingReply() {
        if (_progressiveSaveTimer) { clearTimeout(_progressiveSaveTimer); _progressiveSaveTimer = null; }
        if (_pendingReplyText) {
            saveMessageToHistory('assistant', _pendingReplyText);
            _pendingReplyText = '';
        }
    }

    function closeEventSourceResume() {
        if (_eventSourceRetryTimer) {
            clearTimeout(_eventSourceRetryTimer);
            _eventSourceRetryTimer = null;
        }
        if (_activeEventSource) {
            try { _activeEventSource.close(); } catch (e) {}
            _activeEventSource = null;
        }
    }

    function resumeViaEventSource(sessionId, retryCount = 0) {
        if (!sessionId) return false;
        closeEventSourceResume();
        try {
            _eventSourceRetryCount = retryCount;
            const retryNonce = `${Date.now()}_${Math.floor(Math.random() * 100000)}`;
            _activeEventSource = new EventSource(CONFIG.server + '/api/chat/stream/' + encodeURIComponent(sessionId) + '?r=' + retryNonce);
            _activeEventSource.onmessage = (evt) => {
                if (!evt || !evt.data) return;
                try {
                    const eventData = JSON.parse(evt.data);
                    if (eventData.type === 'stream_meta' && eventData.sse_session_id) {
                        _lastSseSessionId = eventData.sse_session_id;
                        return;
                    }
                    handleStreamEvent(eventData);
                    if (eventData.type === 'done' || eventData.type === 'error') {
                        closeEventSourceResume();
                        _eventSourceRetryCount = 0;
                    }
                } catch (e) {
                    console.warn('[SSE resume] JSON parse error:', e);
                }
            };
            _activeEventSource.onerror = () => {
                const nextRetry = (_eventSourceRetryCount || 0) + 1;
                closeEventSourceResume();
                if (nextRetry <= _MAX_EVENTSOURCE_RETRIES) {
                    const backoffMs = Math.min(1000 * Math.pow(2, nextRetry - 1), 4000);
                    console.warn(`[SSE resume] EventSource failed, retry #${nextRetry} in ${backoffMs}ms`);
                    _eventSourceRetryTimer = setTimeout(() => {
                        _eventSourceRetryTimer = null;
                        resumeViaEventSource(sessionId, nextRetry);
                    }, backoffMs);
                    return;
                }
                _eventSourceRetryCount = 0;
                addMessage('assistant', '⚠️ 串流中斷且續流失敗，請稍後重試。', false);
            };
            return true;
        } catch (e) {
            console.warn('[SSE resume] 建立 EventSource 失敗:', e);
            return false;
        }
    }

    // 🔧 處理串流事件（SSE 與 Socket.IO 共用）
    // ==============================================
    // 🎨 輪次分組渲染（模仿 index.html 的 round-block 布局）
    //   每輪：🔄 第N輪 完成 | 💭 思考 | 🔧 使用工具 (N) | 📦 工具結果 (N) | 💬 回覆
    //   風格：保持 api.js 原有的透明視角感（半透明淡色卡片 + 圓角 + 深色文字）
    // ==============================================
    let _roundsData = [];   // [{iteration, think, toolCalls:[], toolResults:[], reply, done}]
    let _roundEls = [];     // 對應 DOM 元素
    let _curRoundIdx = -1;  // 當前輪索引

    function _curRound() {
        return _curRoundIdx >= 0 ? _roundsData[_curRoundIdx] : null;
    }

    // 取得（或建立）當前輪；forceNew=true 時強制開新輪
    function _ensureRound(iteration, forceNew) {
        const mDiv = document.querySelector('#mokagi-messages');
        if (!mDiv) return null;
        if (!forceNew && _curRoundIdx >= 0 && _curRound() && !_curRound().done) {
            return _curRound();
        }
        // 前一輪若未標記完成，補標完成
        if (_curRoundIdx >= 0 && _curRound() && !_curRound().done) {
            _curRound().done = true;
            _updateRoundHeader(_curRoundIdx);
        }
        const num = iteration || (_roundsData.length + 1);
        const round = { iteration: num, think: '', toolCalls: [], toolResults: [], reply: '', done: false };
        _roundsData.push(round);
        _roundEls.push(_createRoundBlock(round));
        _curRoundIdx = _roundsData.length - 1;
        mDiv.appendChild(_roundEls[_curRoundIdx]);
        mDiv.scrollTop = mDiv.scrollHeight;
        return round;
    }

    // 建立 round-block 骨架（透明視角風格）
    function _createRoundBlock(round) {
        const div = document.createElement('div');
        div.className = 'mokagi-round';
        div.style.cssText = `
            margin-bottom: 12px;
            border: 1px solid #e6e8eb;
            border-radius: 10px;
            overflow: hidden;
            background: #ffffff;
            box-shadow: 0 1px 4px rgba(0,0,0,0.06);
        `;
        // 標題列：🔄 第N輪 完成/進行中 + 工具名
        const header = document.createElement('div');
        header.className = 'round-header';
        header.style.cssText = `
            padding: 6px 12px;
            font-size: 12px;
            color: #b7791f;
            display: flex;
            justify-content: space-between;
            align-items: center;
            background: #f7f8fa;
            border-bottom: 1px solid #eceef1;
        `;
        const titleSpan = document.createElement('span');
        titleSpan.className = 'round-title';
        titleSpan.textContent = '🔄 第' + round.iteration + '輪 進行中';
        const toolsSpan = document.createElement('span');
        toolsSpan.className = 'round-tools';
        toolsSpan.style.cssText = 'font-size:11px;color:#90949f;max-width:60%;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;';
        header.appendChild(titleSpan);
        header.appendChild(toolsSpan);
        div.appendChild(header);
        // 思考
        div.appendChild(_mkDetails('think', '💭 思考', ''));
        // 使用工具 (N)
        div.appendChild(_mkDetails('tools', '🔧 使用工具 (0)', ''));
        // 工具結果 (N)
        div.appendChild(_mkDetails('results', '📦 工具結果 (0)', ''));
        // 回覆區
        const replyDiv = document.createElement('div');
        replyDiv.className = 'round-reply';
        replyDiv.style.cssText = `
            padding: 8px 12px;
            font-size: 14px;
            color: #24292f;
            white-space: pre-wrap;
            word-break: break-word;
            line-height: 1.6;
        `;
        div.appendChild(replyDiv);
        return div;
    }

    // 建立 details/summary 區塊（kind: think/tools/results）
    function _mkDetails(kind, summaryText, bodyHtml) {
        const details = document.createElement('details');
        details.className = kind + '-det';
        details.style.cssText = `background: #f7f8fa; border-top: 1px solid #eceef1;`;
        const summary = document.createElement('summary');
        summary.textContent = summaryText;
        const _sumColor = kind === 'think' ? '#a09060' : kind === 'tools' ? '#5b9bd5' : kind === 'results' ? '#00b894' : '#7a7a85';
        summary.style.cssText = `
            padding: 5px 12px;
            font-size: 12px;
            color: ${_sumColor};
            cursor: pointer;
            user-select: none;
        `;
        const body = document.createElement('div');
        body.className = 'round-details-body ' + kind + '-body';
        body.style.cssText = `padding: 4px 12px 10px 12px; font-size: 13px; color: #4b5563;`;
        if (bodyHtml) body.innerHTML = bodyHtml;
        details.appendChild(summary);
        details.appendChild(body);
        return details;
    }

    // 更新輪次標題列（完成狀態 + 工具名列表）
    function _updateRoundHeader(idx) {
        const round = _roundsData[idx];
        const el = _roundEls[idx];
        if (!round || !el) return;
        const title = el.querySelector('.round-title');
        if (title) title.textContent = '🔄 第' + round.iteration + '輪 ' + (round.done ? '完成' : '進行中');
        const tools = el.querySelector('.round-tools');
        if (tools) tools.textContent = round.toolCalls.map(function(t){ return t.name; }).join(', ');
        var _hasSide = (round.think && round.think.trim().length > 0) || (round.toolCalls && round.toolCalls.length > 0) || (round.toolResults && round.toolResults.length > 0);
        var _hdr = el.querySelector('.round-header');
        if (_hdr) _hdr.style.display = _hasSide ? '' : 'none';
        el.querySelectorAll('details').forEach(function (dt) { dt.style.display = _hasSide ? '' : 'none'; });
    }

    function handleStreamEvent(event) {
        // 🔧 防重複：雙 room 送達時同一事件會連續收到兩次，跳過第二次
        const sig = (event.type || '') + '|' + (event.content || '');
        if ((event.type === 'reply' || event.type === 'think') && sig === _lastStreamSig) {
            return;
        }
        _lastStreamSig = sig;
        const mDiv = document.querySelector('#mokagi-messages');
        if (!mDiv) return;

        // 🆕 新一輪開始
        if (event.type === 'iteration_start') {
            _ensureRound(event.iteration, true);
            return;
        }
        // 💭 思考
        if (event.type === 'think') {
            const thinkContent = event.content || '';
            if (!thinkContent) return;
            const round = _ensureRound(event.iteration);
            if (!round) return;
            round.think += thinkContent;
            const el = _roundEls[_curRoundIdx];
            const body = el ? el.querySelector('.think-body') : null;
            if (body) {
                let txt = body.querySelector('div');
                if (!txt) { txt = document.createElement('div'); body.appendChild(txt); }
                txt.textContent += thinkContent;
            }
            const det = el ? el.querySelector('.think-det') : null;
            mDiv.scrollTop = mDiv.scrollHeight;
            return;
        }
        // 🔧 工具調用列表
        if (event.type === 'tool_calls') {
            const calls = event.calls || [];
            if (!calls.length) return;
            const round = _ensureRound(event.iteration);
            if (!round) return;
            round.toolCalls = calls;
            _updateRoundHeader(_curRoundIdx);
            const el = _roundEls[_curRoundIdx];
            const det = el ? el.querySelector('.tools-det') : null;
            const sum = det ? det.querySelector('summary') : null;
            if (sum) sum.textContent = '🔧 使用工具 (' + calls.length + ')';
            const body = det ? det.querySelector('.tools-body') : null;
            if (body) {
                body.innerHTML = '';
                calls.forEach(function(tc) {
                    const row = document.createElement('div');
                    row.style.cssText = 'margin:4px 0;';
                    const nm = document.createElement('div');
                    nm.style.cssText = 'color:#5b9bd5;font-weight:500;';
                    nm.textContent = tc.name || '未知工具';
                    row.appendChild(nm);
                    if (tc.arguments && Object.keys(tc.arguments).length) {
                        const args = document.createElement('div');
                        args.style.cssText = 'color:#90949f;font-size:12px;word-break:break-all;';
                        args.textContent = JSON.stringify(tc.arguments);
                        row.appendChild(args);
                    }
                    body.appendChild(row);
                });
            }
            mDiv.scrollTop = mDiv.scrollHeight;
            return;
        }
        // 📦 工具結果
        if (event.type === 'tool_result') {
            const name = event.tool_name || '未知工具';
            const content = event.content || '';
            const round = _ensureRound(event.iteration);
            if (!round) return;
            round.toolResults.push({ name: name, content: content });
            const el = _roundEls[_curRoundIdx];
            const det = el ? el.querySelector('.results-det') : null;
            const sum = det ? det.querySelector('summary') : null;
            if (sum) sum.textContent = '📦 工具結果 (' + round.toolResults.length + ')';
            const body = det ? det.querySelector('.results-body') : null;
            if (body) {
                const row = document.createElement('div');
                row.style.cssText = 'margin:6px 0;padding:6px 8px;background:#eef7f2;border-radius:4px;';
                const nm = document.createElement('div');
                nm.style.cssText = 'color:#00b894;font-size:12px;font-weight:500;margin-bottom:2px;';
                nm.textContent = '🔧 ' + name;
                const txt = document.createElement('div');
                txt.style.cssText = 'color:#4b5563;font-size:12px;white-space:pre-wrap;word-break:break-word;max-height:200px;overflow-y:auto;';
                txt.textContent = String(content).substring(0, 1200);
                row.appendChild(nm);
                row.appendChild(txt);
                body.appendChild(row);
            }
            mDiv.scrollTop = mDiv.scrollHeight;
            return;
        }
        // 💬 回覆（串流）
        if (event.type === 'reply') {
            const content = event.content || '';
            if (!content) return;
            _pendingReplyText += content;   // 🔧 累積串流文字，done 時保存
            _scheduleProgressiveSave();   // 🔧 串流中定期保存，防止 done 未收到時丟失回覆
            const round = _ensureRound(event.iteration);
            if (!round) return;
            round.reply += content;
            const el = _roundEls[_curRoundIdx];
            const replyDiv = el ? el.querySelector('.round-reply') : null;
            if (replyDiv) { replyDiv.classList.add('mok-md'); replyDiv.innerHTML = _mokMd(round.reply); }
            mDiv.scrollTop = mDiv.scrollHeight;
            return;
        }
        // ✅ 完成
        if (event.type === 'done') {
            if (_curRoundIdx >= 0 && _curRound()) {
                _curRound().done = true;
                _updateRoundHeader(_curRoundIdx);
            }
            const msgs = document.querySelectorAll('#mokagi-messages .stream-msg');
            msgs.forEach(el => el.classList.remove('stream-msg'));
            // 🔧 串流完成：將累積的完整回覆存入 localStorage（含漸進保存合併）
            _flushPendingReply();
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











    // ----------------------------------------------------------------------
    // 2026-10-08 fix: external-site AI widget returned 401 because its
    // fallback identity was a bare UUID, while the backend only accepts
    // 'web_guest_' or 'guest:' prefixed guests. This resolver returns a valid
    // tenant id and self-heals already-poisoned localStorage values.
    // Member identities are left untouched.
    // ----------------------------------------------------------------------
    function _isValidTenantId(u) {
        return !!u && (u.indexOf('web_guest_') === 0 || u.indexOf('guest:') === 0);
    }
    function _looksLikeLegacyWidgetUUID(u) {
        return /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i.test(u);
    }
    function resolveWidgetUserId() {
        try {
            var _w = window.MOKAGI_USER_ID;
            if (_w && (_isValidTenantId(_w) || !_looksLikeLegacyWidgetUUID(_w))) { return String(_w); }
            var _k = ['mokagi_user_id', 'web_user_id'];
            for (var _i = 0; _i < _k.length; _i++) {
                var _v = localStorage.getItem(_k[_i]) || '';
                if (!_v) continue;
                if (_isValidTenantId(_v)) return _v;
                if (_looksLikeLegacyWidgetUUID(_v)) continue;
                return _v;
            }
        } catch (e) { }
        var _g = 'web_guest_' + generateUUID();
        try { localStorage.setItem('mokagi_user_id', _g); } catch (e) { }
        return _g;
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
        container.id = 'mokagi-widget'; const isDesktop = () => window.innerWidth >= CONFIG.breakpoint && CONFIG.desktopLayout;
        container.style.cssText = isDesktop() ? `
            position: fixed;
            left: 0; top: 0; bottom: 0;
            z-index: 9998;
            pointer-events: none;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        ` : `
            position: fixed;
            ${CONFIG.position === 'bottom-right' ? 'right: 20px; bottom: 20px;' : 'left: 20px; bottom: 20px;'}
            z-index: 9999;
            font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
        `;

        // 聊天窗口（默認隱藏）
        const chatWindow = document.createElement('div');
        chatWindow.id = 'mokagi-chat-window';
        chatWindow.style.cssText = isDesktop() ? `
            display: none;
            position: fixed;
            left: 0; top: 0;
            width: ${CONFIG.chatWidth}px;
            height: 100vh; max-height: 100vh;
            background: transparent;
            border-radius: 0 12px 12px 0;
            box-shadow: 4px 0 30px rgba(0,0,0,0.2);
            overflow: hidden;
            flex-direction: column;
            transform: translateX(-100%);
            transition: transform 0.35s cubic-bezier(0.4,0,0.2,1);
            pointer-events: auto;
        ` : `
            display: none;
            width: 100vw; max-width: 400px;
            max-height: 70vh;
            background: transparent;
            backdrop-filter: none;
            -webkit-backdrop-filter: none;
            border-radius: 12px;
            box-shadow: 0 8px 30px rgba(0,0,0,0.2);
            overflow: hidden;
            flex-direction: column;
            margin-bottom: 10px;
        `;

        // 標題欄
        const header = document.createElement('div');
        header.style.cssText = `
            background: rgba(0, 0, 0, 0);
            color: #fff;
            padding: 6px 12px;
            font-weight: 600;
            font-size: 13px;
            display: flex;
            justify-content: space-between;
            align-items: center;
            cursor: pointer;
        `;
        header.innerHTML = `
        <span>${CONFIG.agentIcon} ${CONFIG.agent}</span>
        <span style="display:flex;align-items:center;gap:8px;">
            <span class="mokagi-opacity-control" style="display:none;align-items:center;gap:4px;font-size:11px;">
                <span>透明度</span>
                <input type="range" id="mokagi-opacity-slider" min="20" max="100" value="${Math.round(CONFIG.mobileOpacity*100)}" style="width:50px;accent-color:#fff;" />
            </span>
            <a href="https://64071181.xyz/project/api/" target="_blank" style="color:#fff;text-decoration:none;font-size:11px;">MOKAGI</a>
            <span style="font-size:16px;cursor:pointer;" id="mokagi-close-btn">&times;</span>
        </span>`;
        header.querySelector('#mokagi-close-btn').addEventListener('click', toggleChat);

        // 消息區域
        const messagesDiv = document.createElement('div');
        messagesDiv.id = 'mokagi-messages';
        messagesDiv.style.cssText = `
            flex: 1;
            padding: 16px;
            overflow-y: auto;
            max-height: ${isDesktop() ? 'calc(100vh - 140px)' : '350px'};
            background: rgba(0, 0, 0, 0);
            font-size: 14px;
            line-height: 1.6;
        `;

        // 輸入區域
        const inputArea = document.createElement('div');
        inputArea.style.cssText = `
            padding: 12px;
            border-top: 1px solid #eee;
            display: flex;
            background: rgba(0, 0, 0, 0);
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

        // 快速查詢按鈕
        const quickLinks = CONFIG.quickLinks || [];
        if (quickLinks.length > 0) {
            const quickLinksContainer = document.createElement("div");
            quickLinksContainer.id = "mokagi-quick-links";
            quickLinksContainer.style.cssText = `
                padding: 8px 12px;
                display: flex;
                flex-wrap: wrap;
                gap: 6px;
                border-top: 1px solid #f0f0f0;
                background: rgba(0, 0, 0, 0);
            `;
            quickLinks.forEach(item => {
                const btn = document.createElement("button");
                btn.textContent = item.text;
                btn.style.cssText = `
                    padding: 6px 12px;
                    border: 1px solid ${CONFIG.theme};
                    border-radius: 16px;
                    background: #fff;
                    color: ${CONFIG.theme};
                    cursor: pointer;
                    font-size: 13px;
                    white-space: nowrap;
                    transition: all 0.2s;
                `;
                btn.addEventListener("mouseenter", () => {
                    btn.style.background = CONFIG.theme;
                    btn.style.color = "#fff";
                });
                btn.addEventListener("mouseleave", () => {
                    btn.style.background = "#fff";
                    btn.style.color = CONFIG.theme;
                });
                btn.addEventListener("click", () => {
                    // ✅ 快捷回答：有 answer 就直接顯示預設答案，完全不呼叫 LLM
                    if (item.answer) {
                        addMessage("user", item.query || item.text);
                        addMessage("assistant", item.answer, true);
                        return;
                    }
                    addMessage("user", item.query);
                    sendViaSSE(item.query);
                });
                quickLinksContainer.appendChild(btn);
            });
            chatWindow.appendChild(quickLinksContainer);
        }

        chatWindow.appendChild(inputArea);

        // 📜 歷史對話改於 toggleChat 打開聊天時統一載入（流水式顯示）

// 浮動按鈕（含動畫效果）
        const toggleBtn = document.createElement('button');
        toggleBtn.id = 'mokagi-toggle-btn';
        const toggleIcon = document.createElement('span');
        toggleIcon.textContent = CONFIG.agentIcon;
        toggleIcon.style.cssText = 'display:inline-block;animation:mokagi-icon-spin 8s linear infinite;';
        toggleBtn.appendChild(toggleIcon);
        toggleBtn.style.cssText = isDesktop() ? `
            position: fixed;
            right: 20px; bottom: 20px;
            width: 48px; height: 48px;
            border-radius: 50%;
            background: transparent;
            color: ${CONFIG.theme};
            border: none;
            outline: none;
            font-size: 24px;
            box-shadow: 0 0 20px ${CONFIG.theme}40, 0 0 40px ${CONFIG.theme}20;
            cursor: pointer;
            z-index: 9997;
            transition: all 0.35s cubic-bezier(0.4,0,0.2,1);
            animation: mokagi-float 3s ease-in-out infinite, mokagi-glow-pulse 2.5s ease-in-out infinite;
            pointer-events: auto;
        ` : `
            width: 56px; height: 56px;
            border-radius: 50%;
            background: transparent;
            color: ${CONFIG.theme};
            border: none;
            outline: none;
            font-size: 28px;
            box-shadow: 0 0 20px ${CONFIG.theme}40, 0 0 40px ${CONFIG.theme}20;
            cursor: pointer;
            z-index: 9999;
            transition: all 0.3s cubic-bezier(0.4,0,0.2,1);
            animation: mokagi-float 3s ease-in-out infinite, mokagi-glow-pulse 2.5s ease-in-out infinite;
            pointer-events: auto;
        `;
        // hover 動畫由 CSS #mokagi-toggle-btn:hover 控制（呼叫脈衝+圖示轉動+光暈）
        toggleBtn.addEventListener('click', toggleChat);

        container.appendChild(chatWindow);
        container.appendChild(toggleBtn);
        document.body.appendChild(container);

// 切換顯示（桌面側欄 / 手機半透明）
        function toggleChat() {
            const isOpen = chatWindow.style.display === 'flex';
            if (isOpen) {
                if (isDesktop()) {
                    chatWindow.style.transform = 'translateX(-100%)';
                    document.body.style.marginLeft = '0';
                    document.body.style.transition = 'margin-left 0.35s cubic-bezier(0.4,0,0.2,1)';
                    const onTransitionEnd = () => {
                        chatWindow.style.display = 'none';
                        chatWindow.removeEventListener('transitionend', onTransitionEnd);
                    };
                    chatWindow.addEventListener('transitionend', onTransitionEnd);
                    setTimeout(() => { chatWindow.style.display = 'none'; }, 400);
                    // 桌面關閉時顯示按鈕
                    toggleBtn.style.opacity = '1';
                    toggleBtn.style.pointerEvents = 'auto';
                    toggleBtn.style.zIndex = '9997';
                } else {
                    chatWindow.style.display = 'none';
                }
            } else {
                chatWindow.style.display = 'flex';
                if (isDesktop()) {
                    requestAnimationFrame(() => {
                        chatWindow.style.transform = 'translateX(0)';
                        document.body.style.marginLeft = CONFIG.chatWidth + 'px';
                        document.body.style.transition = 'margin-left 0.35s cubic-bezier(0.4,0,0.2,1)';
                    });
                    // 桌面打開時隱藏按鈕（放在對話框下層）
                    toggleBtn.style.opacity = '0';
                    toggleBtn.style.pointerEvents = 'none';
                    toggleBtn.style.zIndex = '9996';
                }
                // 📜 每次打開聊天時重新載入歷史對話（流水式顯示，只顯示最近 N 條）
                messagesDiv.innerHTML = "";
                const history = loadHistory();
                if (history.length > 0) {
                    setTimeout(() => {
                        displayHistoryWithFlow(history, messagesDiv);
                    }, 350);
                }
                input.focus();
            }
            updateOpacityControl();
        }




// 🎨 注入 CSS 動畫樣式
        const animStyle = document.createElement('style');
        animStyle.textContent = `
            @keyframes mokagi-float {
                0%, 100% { transform: translateY(0); }
                50% { transform: translateY(-6px); }
            }
            @keyframes mokagi-glow-pulse {
                0%, 100% { box-shadow: 0 0 20px ${CONFIG.theme}40, 0 0 40px ${CONFIG.theme}20; }
                50% { box-shadow: 0 0 36px ${CONFIG.theme}65, 0 0 72px ${CONFIG.theme}35, 0 0 108px ${CONFIG.theme}12; }
            }
            @keyframes mokagi-call-pulse {
                0%, 100% { transform: scale(1); }
                50% { transform: scale(1.25); }
            }
            @keyframes mokagi-icon-spin {
                0% { transform: rotate(0deg); }
                100% { transform: rotate(360deg); }
            }
            @keyframes mokagi-fadeInUp {
                from { opacity: 0; transform: translateY(10px); }
                to { opacity: 1; transform: translateY(0); }
            }
            @keyframes mokagi-bounce {
                0%, 100% { transform: scale(1); }
                30% { transform: scale(1.15); }
                50% { transform: scale(0.95); }
                70% { transform: scale(1.05); }
            }
            #mokagi-toggle-btn:hover {
                animation: mokagi-call-pulse 0.6s ease-in-out infinite, mokagi-glow-pulse 1.2s ease-in-out infinite;
                box-shadow: 0 0 32px ${CONFIG.theme}60, 0 0 64px ${CONFIG.theme}35, 0 0 96px ${CONFIG.theme}15 !important;
            }
            #mokagi-toggle-btn:hover span {
                animation: mokagi-icon-spin 2s linear infinite;
            }
            #mokagi-chat-window.mokagi-open-desktop {
                transform: translateX(0) !important;
            }
            .mokagi-opacity-control { display: none; }
            @media (max-width: ${CONFIG.breakpoint - 1}px) {
                #mokagi-chat-window {
                    transition: background 0.3s, backdrop-filter 0.3s, -webkit-backdrop-filter 0.3s, opacity 0.3s !important;
                }
            }
        `;
        document.head.appendChild(animStyle);
        try { _mokMdCss(); } catch (e) {}

// 🌓 透明度控制
        const opacitySlider = header.querySelector('#mokagi-opacity-slider');
        function updateOpacityControl() {
            // 預設隱藏滑桿：未開啟 MOKAGI_SHOW_OPACITY_SLIDER 時，直接套用 CONFIG.mobileOpacity 透明度
            if (!CONFIG.showOpacitySlider) {
                const ctrl = header.querySelector(".mokagi-opacity-control");
                if (ctrl) ctrl.style.display = "none";
                if (isDesktop()) {
                    chatWindow.style.background = "transparent";
                    chatWindow.style.backdropFilter = "none";
                    chatWindow.style.webkitBackdropFilter = "none";
                    chatWindow.style.opacity = "1";
                    header.style.background = "rgba(0, 0, 0, 0)";
                } else {
                    chatWindow.style.background = "transparent";
                    chatWindow.style.backdropFilter = "none";
                    chatWindow.style.webkitBackdropFilter = "none";
                    chatWindow.style.opacity = "1";
                    header.style.background = "rgba(0, 0, 0, 0)";
                }
                return;
            }
            const ctrl = header.querySelector('.mokagi-opacity-control');
            if (!opacitySlider || !ctrl) return;
            if (isDesktop()) {
                ctrl.style.display = 'none';
                chatWindow.style.background = 'transparent';
                chatWindow.style.backdropFilter = 'none';
                chatWindow.style.webkitBackdropFilter = 'none';
                chatWindow.style.opacity = '1'; header.style.background = "rgba(0, 0, 0, 0)";
            } else {
                ctrl.style.display = 'inline-flex';
                const val = parseInt(opacitySlider.value) / 100;
                chatWindow.style.background = `rgba(255,255,255,${val})`;
                chatWindow.style.backdropFilter = `blur(${val * 12}px)`;
                chatWindow.style.webkitBackdropFilter = `blur(${val * 12}px)`;
                chatWindow.style.opacity = String(val);
                header.style.background = "rgba(0, 0, 0, 0)";
            }
        }
        if (opacitySlider) {
            opacitySlider.addEventListener('input', () => {
                const val = parseInt(opacitySlider.value) / 100;
                chatWindow.style.background = `rgba(255,255,255,${val})`;
                chatWindow.style.backdropFilter = `blur(${val * 12}px)`;
                chatWindow.style.webkitBackdropFilter = `blur(${val * 12}px)`;
                chatWindow.style.opacity = String(val);
                header.style.background = "rgba(0, 0, 0, 0)";
                const msgDiv = document.getElementById('mokagi-messages');

            });
        }

        // 📐 視窗大小變化時更新佈局
        let _resizeTimer;
        window.addEventListener('resize', () => {
            clearTimeout(_resizeTimer);
            _resizeTimer = setTimeout(() => {
                const wasDesktop = container.style.position === 'fixed' && container.style.left === '0px';
                const nowDesktop = isDesktop();
                if (wasDesktop !== nowDesktop) {
                    if (nowDesktop) {
                        container.style.cssText = `position:fixed;left:0;top:0;bottom:0;z-index:9998;pointer-events:none;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;`;
                        chatWindow.style.cssText = `display:${chatWindow.style.display};position:fixed;left:0;top:0;width:${CONFIG.chatWidth}px;height:100vh;max-height:100vh;background:#fff;border-radius:0 12px 12px 0;box-shadow:4px 0 30px rgba(0,0,0,0.2);overflow:hidden;flex-direction:column;transform:${chatWindow.style.display==='flex'?'translateX(0)':'translateX(-100%)'};transition:transform 0.35s cubic-bezier(0.4,0,0.2,1);pointer-events:auto;`;
                        // 桌面版：按鈕透明度控制（不是 display:none）
                        toggleBtn.style.cssText = `position:fixed;right:20px;bottom:20px;width:48px;height:48px;border-radius:50%;background:transparent;color:${CONFIG.theme};border:2px solid ${CONFIG.theme}22;font-size:24px;box-shadow:0 0 16px ${CONFIG.theme}10,0 2px 8px rgba(0,0,0,0.05);cursor:pointer;z-index:9997;transition:all 0.35s cubic-bezier(0.4,0,0.2,1);animation:mokagi-float 3s ease-in-out infinite,mokagi-glow-pulse 2.5s ease-in-out infinite;pointer-events:auto;opacity:1;`;
                        document.body.style.marginLeft = chatWindow.style.display === 'flex' ? CONFIG.chatWidth + 'px' : '0';
                        document.body.style.transition = 'margin-left 0.35s cubic-bezier(0.4,0,0.2,1)';
                    } else {
                        container.style.cssText = `position:fixed;right:20px;bottom:20px;z-index:9999;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Helvetica,Arial,sans-serif;`;
                        chatWindow.style.cssText = `display:${chatWindow.style.display};width:100vw;max-width:400px;max-height:70vh;background:rgba(255,255,255,${CONFIG.mobileOpacity});backdrop-filter:blur(12px);-webkit-backdrop-filter:blur(12px);border-radius:12px;box-shadow:0 8px 30px rgba(0,0,0,0.2);overflow:hidden;flex-direction:column;margin-bottom:10px;`;
                        toggleBtn.style.cssText = `width:56px;height:56px;border-radius:50%;background:transparent;color:${CONFIG.theme};border:2px solid ${CONFIG.theme}33;font-size:28px;box-shadow:0 0 20px ${CONFIG.theme}18,0 4px 12px rgba(0,0,0,0.08);cursor:pointer;z-index:9999;transition:all 0.3s cubic-bezier(0.4,0,0.2,1);animation:mokagi-float 3s ease-in-out infinite,mokagi-glow-pulse 2.5s ease-in-out infinite;pointer-events:auto;`;
                        document.body.style.marginLeft = '0';
                    }
                }
                updateOpacityControl();
            }, 300);
        });
        updateOpacityControl();

        // 🔧 SSE 發送消息（主要傳輸，比 Socket.IO 更穩定）
        async function sendViaSSE(text) {
            if (_activeSSEController) {
                _activeSSEController.abort();
            }
            closeEventSourceResume();
            _activeSSEController = new AbortController();
            const controller = _activeSSEController;
            _pendingReplyText = '';   // 🔧 新一輪串流，重置累積文字
            _lastStreamSig = '';   // 🔧 重置防重複簽名

            try {
                const payload = getPayload(text);
                const response = await fetch(CONFIG.server + '/api/chat', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json', 'Accept': 'text/event-stream' },
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
                    if (response.status === 524) {
                        addMessage('assistant', '⚠️ 524 逾時：上游回應太慢，請稍後重試。', false);
                        return;
                    }
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
                                if (eventData.type === 'stream_meta' && eventData.sse_session_id) {
                                    _lastSseSessionId = eventData.sse_session_id;
                                    continue;
                                }
                                handleStreamEvent(eventData);
                                if (eventData.type === 'done') {
                                    _lastSseSessionId = '';
                                }
                            } catch (parseErr) {
                                console.warn('[sendViaSSE] JSON parse error:', parseErr);
                            }
                        }
                    }
                }
                // 🔧 串流結束：即使沒收到 done 也保存已累積的回覆，防止斷線丟失
                _flushPendingReply();
            } catch (err) {
                if (err.name === 'AbortError') {
                    console.log('[sendViaSSE] 請求已取消');
                } else {
                    console.error('[sendViaSSE] SSE 失敗:', err);
                    if (_lastSseSessionId && resumeViaEventSource(_lastSseSessionId)) {
                        addMessage('assistant', '⚠️ 主串流斷線，已自動切到續流通道。', false);
                        return;
                    }
                    addMessage('assistant', '⚠️ 連線失敗，請稍後重試。', false);
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

        // 添加消息到界面（save=false 時不寫入歷史，如歡迎詞/系統提示）
        function addMessage(role, content, save) {
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
            if (role === 'user') { bubble.textContent = content; } else { bubble.classList.add('mok-md'); bubble.innerHTML = _mokMd(content); }
            msgDiv.appendChild(bubble);
            messagesDiv.appendChild(msgDiv);
            messagesDiv.scrollTop = messagesDiv.scrollHeight;
            // 💾 保存到 localStorage（系統提示如歡迎詞不保存）
            if (save !== false) saveMessageToHistory(role, content);
        }

        // 暴露添加消息給外部（用於流式更新）
        window.mokagiWidget = { addMessage, toggleChat };
        return { addMessage, sendMessage };
    }

    // 初始化 SocketIO 連接
    let socket = null;
    let addMessageFn = null;
    const isLocalHost = false;

    function _createSocketStub() {
        return {
            connected: false,
            on: function() {},
            emit: function() {},
            off: function() {},
            connect: function() {},
            disconnect: function() {}
        };
    }

    function initSocket() {
        if (!isLocalHost) {
            socket = _createSocketStub();
            console.log('[api.js] public host：停用 Socket.IO，僅使用 SSE');
            return;
        }
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
                // 若已有歷史對話記錄，不顯示歡迎詞
                const existingHistory = loadHistory();
                if (existingHistory.length === 0) {
                    addMessageFn('assistant', CONFIG.sayHi, false);
                }
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
                    addMessageFn('assistant', CONFIG.saySorry, false);
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
            addMessageFn('assistant', '❌ 無法重新連線，請刷新頁面', false);
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
            if (!isLocalHost) {
                try {
                    const ui = createUI();
                    addMessageFn = ui.addMessage;
                    initSocket();
                } catch(e) {
                    console.error('UI 或 Socket 初始化失敗:', e);
                }
                return;
            }
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

    // ============ 商家網頁追蹤（Cold Call 客戶開啟統計） ============
    // 當頁面是 MokCs 商家 demo 頁時，回報「頁面被打開」事件到冷呼控制台
    (function trackMokCsPage() {
        try {
            if (window.__MOKAGI_TRACKED__) return;
            window.__MOKAGI_TRACKED__ = true;
            var _path = window.location.pathname || '';
            // 只追蹤 MokCs 商家頁面（避免污染其他站點統計）
            if (_path.indexOf('/project/MokCs/') === -1) return;
            var _startTs = Date.now();
            var _ua = navigator.userAgent || '';
            // ── Session ID（localStorage 持久化，供停留時間合併） ──
            function _newSid() {
                try {
                    if (window.crypto && crypto.getRandomValues) {
                        var a = new Uint8Array(8);
                        crypto.getRandomValues(a);
                        return Array.from(a, function(b){ return ('0'+b.toString(16)).slice(-2); }).join('');
                    }
                } catch(e) {}
                return Date.now().toString(36) + Math.random().toString(36).slice(2, 10);
            }
            var _sid = null;
            try {
                _sid = localStorage.getItem('mokcs_sid') || _newSid();
                localStorage.setItem('mokcs_sid', _sid);
            } catch(e) { _sid = _newSid(); }
            // ── 裝置類型 ──
            var _isMobi = /Mobi|Android|iPhone|iPod|BlackBerry|IEMobile|Opera Mini/i.test(_ua);
            var _isTab = /iPad|Tablet|PlayBook|Silk/i.test(_ua) || (!_isMobi && /Macintosh/.test(_ua) && navigator.maxTouchPoints > 1);
            var _device = _isTab ? '平板' : (_isMobi ? '手機' : '電腦');
            var _payload = JSON.stringify({
                url: window.location.href,
                title: document.title || '',
                referrer: document.referrer || '',
                ua: _ua.slice(0, 200),
                sid: _sid,
                screen: (window.screen ? window.screen.width + 'x' + window.screen.height : ''),
                device: _device,
                lang: navigator.language || '',
                tz: (function(){ try { return Intl.DateTimeFormat().resolvedOptions().timeZone || ''; } catch(e){ return ''; } })()
            });
            var _send = function(duration) {
                try {
                    var data = _payload;
                    if (duration) {
                        try {
                            var obj = JSON.parse(_payload);
                            obj.duration = duration;
                            data = JSON.stringify(obj);
                        } catch(e) {}
                    }
                    if (navigator.sendBeacon) {
                        navigator.sendBeacon(CONFIG.server + '/api/track',
                            new Blob([data], { type: 'application/json' }));
                        return;
                    }
                } catch (e) {}
                try {
                    var xhr = new XMLHttpRequest();
                    xhr.open('POST', CONFIG.server + '/api/track', true);
                    xhr.setRequestHeader('Content-Type', 'application/json');
                    xhr.send(data);
                } catch (e) {}
            };
            // 開啟事件：頁面載入時回報
            if (document.readyState === 'loading') {
                document.addEventListener('DOMContentLoaded', function(){ _send(0); });
            } else {
                _send(0);
            }
            // 離開事件：回報停留時間（秒），後端合併到同一 session 的開啟記錄
            window.addEventListener('pagehide', function() {
                var dur = Math.round((Date.now() - _startTs) / 1000);
                if (dur >= 3) _send(dur);
            });
        } catch(e) {}
    })();
})();
