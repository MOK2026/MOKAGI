

    // ===== Monaco Editor 配置與輔助函數 =====
    const MONACO_CDN = "https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min";
    
    // 設定 Monaco Worker 從 CDN 載入
    window.MonacoEnvironment = {
        getWorkerUrl: function(workerId, label) {
            const workerMap = {
                editorWorkerService: "/vs/editor/editor.worker.js",
                json: "/vs/language/json/json.worker.js",
                css: "/vs/language/css/css.worker.js",
                html: "/vs/language/html/html.worker.js",
                typescript: "/vs/language/typescript/ts.worker.js",
                javascript: "/vs/language/typescript/ts.worker.js",
            };
            const path = workerMap[label] || "/vs/editor/editor.worker.js";
            return MONACO_CDN + path;
        }
    };

    function loadMonacoLoader() {
        return new Promise((resolve, reject) => {
            const monacoRequire = window.require || window.requirejs;
            if (typeof monacoRequire !== "undefined" && typeof monacoRequire.config === "function") {
                return resolve(monacoRequire);
            }
            const script = document.createElement('script');
            script.src = MONACO_CDN + "/vs/loader.js";
            script.onload = () => {
                const loadedRequire = window.require || window.requirejs;
                if (typeof loadedRequire !== "undefined" && typeof loadedRequire.config === "function") {
                    resolve(loadedRequire);
                } else {
                    reject(new Error('Monaco loader loaded but require is undefined'));
                }
            };
            script.onerror = () => reject(new Error('Failed to load Monaco loader from CDN'));
            document.head.appendChild(script);
        });
    }

    window._monacoReady = false;
    window._monacoPending = [];

    loadMonacoLoader().then((requireFn) => {
        requireFn.config({ paths: { vs: MONACO_CDN + "/vs" } });
        requireFn(["vs/editor/editor.main"], function() {
            window._monacoReady = true;
            // 定義 VS Dark 主題微調
            monaco.editor.defineTheme("mokagiDark", {
                base: "vs-dark",
                inherit: true,
                rules: [],
                colors: {
                    "editor.background": "#1e1e1e",
                    "editor.foreground": "#d4d4d4",
                    "editorLineNumber.foreground": "#6e6e6e",
                    "editorCursor.foreground": "#4ec9b0",
                    "editor.selectionBackground": "#264f78",
                }
            });
            const pending = window._monacoPending;
            window._monacoPending = [];
            pending.forEach(cb => cb());
        });
    }).catch((err) => {
        console.error('Monaco initialization failed:', err);
    });
    
    // 當 Monaco 載入完成後執行回調
    function whenMonacoReady(cb) {
        if (window._monacoReady && typeof monaco !== "undefined") {
            cb();
        } else {
            window._monacoPending.push(cb);
        }
    }
    
    // 語言偵測：根據副檔名回傳 Monaco language ID
    function getLanguageFromPath(path) {
        if (!path) return "plaintext";
        const ext = path.split(".").pop().toLowerCase();
        const map = {
            js: "javascript", mjs: "javascript", ts: "typescript",
            py: "python", pyw: "python",
            html: "html", htm: "html", css: "css", scss: "scss", less: "less",
            json: "json", xml: "xml", yaml: "yaml", yml: "yaml",
            md: "markdown", markdown: "markdown",
            sh: "shell", bash: "shell", zsh: "shell", bat: "bat", ps1: "powershell",
            sql: "sql", c: "c", cpp: "cpp", cc: "cpp", cxx: "cpp", h: "c", hpp: "cpp",
            java: "java", rs: "rust", go: "go", rb: "ruby", php: "php",
            swift: "swift", kt: "kotlin", scala: "scala",
            txt: "plaintext", log: "plaintext", conf: "ini", ini: "ini",
            toml: "ini", cfg: "ini", dockerfile: "dockerfile",
        };
        return map[ext] || "plaintext";
    }
    
    // Monaco 輔助物件（全域）
    window.monacoHelp = {
        _editor: null,
        _container: null,
        getValue: function() {
            return this._editor ? this._editor.getValue() : "";
        },
        setValue: function(val) {
            if (this._editor) this._editor.setValue(val);
        },
        init: function(container, value, language) {
            if (this._editor) this._editor.dispose();
            this._container = container;
            this._editor = monaco.editor.create(container, {
                value: value || "",
                language: language || "plaintext",
                theme: "mokagiDark",
                automaticLayout: true,
                minimap: { enabled: false },
                scrollBeyondLastLine: false,
                fontSize: 14,
                fontFamily: "Cascadia Code, 'Courier New', monospace",
                cursorBlinking: "smooth",
                wordWrap: "on",
            });
        },
        dispose: function() {
            if (this._editor) {
                this._editor.dispose();
                this._editor = null;
                this._container = null;
            }
        },
        setLanguage: function(language) {
            if (this._editor) {
                monaco.editor.setModelLanguage(this._editor.getModel(), language);
            }
        },
        pasteAtCursor: function(text) {
            if (this._editor) {
                const position = this._editor.getPosition();
                this._editor.executeEdits('', [{
                    range: new monaco.Range(position.lineNumber, position.column, position.lineNumber, position.column),
                    text: text,
                    forceMoveMarkers: true,
                }]);
                this._editor.focus();
            }
        }
    };

    // 全局狀態
    let currentAgent = null;
    let agentList = [];
    let agentStates = {};  // { agentName: { isRunning: false, hasNewCompleted: false } }
    let mokConfig = {};
    let agentStartMsg = '';
    let modelsList = [];
    let currentModelIndex = 0;
    let currentTool = 'files';
    // ===== 分頁加載相關 =====
    let allMessages = [];
    let chatHistoryOffset = 0;
    let chatHistoryHasMore = true;
    let loadingMore = false;
    const CHAT_HISTORY_PAGE_SIZE = 20;

    const socket = (typeof io !== 'undefined') ? io() : {
        on: function() { console.warn('Socket.IO 未載入，忽略 on()', arguments); },
        emit: function() { console.warn('Socket.IO 未載入，忽略 emit()', arguments); },
        off: function() { console.warn('Socket.IO 未載入，忽略 off()', arguments); },
        disconnect: function() {},
    };

    if (!socket || typeof socket.on !== 'function') {
        console.error('Socket.IO 初始化失敗：請確認 html/index.html 已正確載入 socket.io.js');
    }

// Toast 提示：1秒後漸變透明消失
function showQuoteToast(message) {
    const toast = document.createElement("div");
    toast.textContent = message;
    toast.style.cssText = "position:fixed; bottom:50%; left:50%; transform:translateX(-50%); background:#4ec9b0; color:#000; padding:8px 20px; border-radius:8px; font-size:14px; z-index:9999; opacity:1; transition:opacity 0.5s ease; pointer-events:none;";
    document.body.appendChild(toast);
    setTimeout(() => { toast.style.opacity = "0"; }, 1000);
    setTimeout(() => { toast.remove(); }, 1500);
}

let currentFileMode = 'preview';  // 'preview' 或 'edit'
let currentHtmlContent = '';      // 儲存當前 HTML 檔案的完整內容
    let chatMessagesDiv = null;
    let currentThinkDiv = {};     // { agentName: DOM element }
    let currentReplyDiv = {};     // { agentName: DOM element }
    let currentAssistantDiv = {}; // { agentName: DOM element }
    let accumulatedReply = {};    // { agentName: string }
    let accumulatedThink = {};    // { agentName: string }
    let streamGen = {};           // { agentName: number } 追蹤當前世代，防止舊 done 污染新 UI
    let fileContentPre, imageViewer, videoViewer, currentFilenameSpan;
    let scrollBtn = null;
    let quickJumpPanel = null;  // 快速跳轉面板的DOM
    

    // 按 Agent 存儲分類內容
    let agentClassifiedData = {};  // { agentName: { toolProcess: '', semanticSearch: '', experience: '' } }
    // ===== 結束 =====

    // 獲取當前 Agent 的分類數據
    function getAgentClassified(agent) {
        if (!agentClassifiedData[agent]) {
            agentClassifiedData[agent] = { toolProcess: '', semanticSearch: '', experience: '' };
        }
        return agentClassifiedData[agent];
    }


    const Mok_web_lines = 10



    const MokAgi_Token_Price_HK = 18 / 1_000_000; // 18 HKD per million tokens，與 token_billing.html 一致
    // 平時輸出價格 (6元/百萬tokens) 估算(隨時轉)
    // https://api-docs.deepseek.com/zh-cn/quick_start/pricing/




    // main.js
    let userId = localStorage.getItem('web_user_id');
    if (!userId) {
        userId = 'web_guest_' + Math.random().toString(36).substring(2, 10);
        localStorage.setItem('web_user_id', userId);
    }


// --- 程式碼庫 直接將檔傳給llm ----

    // 附件列表
    let attachments = [];


// 附件 UI 更新函數
function updateAttachmentsUI() {
    const container = document.getElementById('attachmentsContainer');
    if (attachments.length === 0) {
        container.style.display = 'none';
        container.innerHTML = '';
        return;
    }
    container.style.display = 'flex';
    container.innerHTML = '';
    attachments.forEach((att, idx) => {
        const chip = document.createElement('div');
        chip.className = 'attachment-chip';
        const icon = att.type.startsWith('text/') ? '📄' : (att.type.startsWith('image/') ? '🖼️' : (att.type.startsWith('audio/') ? '🎵' : '🎥'));
        chip.innerHTML = `${icon} ${escapeHtml(att.name)} <button class="remove-attach" data-idx="${idx}">✖</button>`;
        chip.querySelector('.remove-attach').addEventListener('click', () => {
            attachments.splice(idx, 1);
            updateAttachmentsUI();
        });
        container.appendChild(chip);
    });
}

//  文件處理函數（支持文本、圖片等）
async function handleFiles(files) {
    const textMimeTypes = ['text/plain', 'text/markdown', 'text/html', 'text/css', 'text/javascript', 'application/json', 'application/xml', 'text/csv'];
    for (const file of files) {
        // 文本文件限制大小（2MB，代碼庫可放寬至15MB）
        if (textMimeTypes.some(mime => file.type.includes(mime))) {
            const maxSize = (file.name.includes('程式碼庫') || file.name.includes('MOKAGI_完整')) ? 15 * 1024 * 1024 : 2 * 1024 * 1024;
            if (file.size > maxSize) {
                alert(`「${file.name}」檔案過大(>${maxSize/1024/1024}MB)，無法加入附件`);
                continue;
            }
        }
        const att = { name: file.name, type: file.type, size: file.size };
        if (textMimeTypes.some(mime => file.type.includes(mime)) || /\.(txt|md|json|js|html|css|py|cpp|c|java|go|rs)$/i.test(file.name)) {
            try {
                const text = await file.text();
                att.content = text;
                attachments.push(att);
            } catch (err) {
                console.error('讀取檔案失敗', err);
                alert(`無法讀取 ${file.name}`);
            }
        } else if (att.type.startsWith('image/') || att.type.startsWith('video/')) {
            // 圖片/影片：讀取 base64 → 發送給 vision 分析
            att.processing = true;
            attachments.push(att);
            updateAttachmentsUI();
            const reader = new FileReader();
            reader.onload = async (e) => {
                const dataUrl = e.target.result;
                const base64 = dataUrl.split(',')[1] || dataUrl;
                socket.emit('upload_media', {
                    filename: file.name,
                    data: base64,
                    mime_type: file.type
                });
            };
            reader.readAsDataURL(file);
        } else {
            // 非文字文件只記錄基本信息
            attachments.push(att);
        }
    }
    updateAttachmentsUI();
}
// 構建包含附件的消息文本
function buildMessageWithAttachments(userMessage) {
    if (attachments.length === 0) return userMessage;
    let attachmentText = '使用者附加了以下檔案：\n';
    for (const att of attachments) {
        if (att.content !== undefined) {
            let contentPreview = att.content;
            attachmentText += `- 📄 ${att.name} (文字檔案)\n\`\`\`\n${contentPreview}\n\`\`\`\n`;
        } else if (att.type.startsWith('image/')) {
            attachmentText += `- 🖼️ ${att.name} (圖片檔案，無法直接分析，請根據名稱推測內容)\n`;
        } else if (att.type.startsWith('audio/')) {
            attachmentText += `- 🎵 ${att.name} (錄音檔案，無法直接分析，請描述其內容)\n`;
        } else if (att.type.startsWith('video/')) {
            attachmentText += `- 🎥 ${att.name} (影片檔案，無法直接分析，請描述其內容)\n`;
        } else {
            attachmentText += `- 📎 ${att.name} (其他檔案類型)\n`;
        }
    }
    attachmentText += `\n使用者的問題：${userMessage}\n請根據上述檔案內容回答。`;
    return attachmentText;
}


// 載入程式碼庫（使用 File System Access API）
async function loadCodeLibrary() {
    try {
        const dirHandle = await window.showDirectoryPicker({ mode: 'read' });
        const allFiles = [];
        await collectTextFiles(dirHandle, '', allFiles);
        if (allFiles.length === 0) {
            alert('未找到任何文字檔案（.py / .md / .json / .js 等）');
            return;
        }
        let mergedContent = '# MOKAGI 程式碼庫\n\n';
        for (const { path, content } of allFiles) {
            mergedContent += `## 📁 ${path}\n\`\`\`\n${content}\n\`\`\`\n\n`;
        }
        const blob = new Blob([mergedContent], { type: 'text/plain' });
        const fakeFile = new File([blob], 'MOKAGI_完整程式碼庫.md', { type: 'text/plain' });
        await handleFiles([fakeFile]);
        alert(`✅ 已載入 ${allFiles.length} 個檔案，總大小 ${(blob.size / 1024).toFixed(1)}KB`);
    } catch (err) {
        if (err.name !== 'AbortError') {
            console.error('載入程式碼庫失敗', err);
            alert('❌ 載入失敗：' + err.message);
        }
    }
}

async function collectTextFiles(dirHandle, prefix, result) {
    const textExtensions = /\.(py|md|json|js|html|css|cpp|c|java|go|rs|ts|sh|yml|yaml|txt|cfg|conf|ini|env|toml|bat|ps1)$/i;
    for await (const entry of dirHandle.values()) {
        if (entry.kind === 'directory') {
            await collectTextFiles(entry, `${prefix}${entry.name}/`, result);
        } else if (entry.kind === 'file' && textExtensions.test(entry.name)) {
            const file = await entry.getFile();
            if (file.size > 500 * 1024) continue; // 單文件最大500KB
            const content = await file.text();
            if (content.length > 0) {
                result.push({ path: `${prefix}${entry.name}`, content });
            }
        }
    }
}
// --- / 程式碼庫 直接將檔傳給llm ----








    // ---------- 輔助 ----------
    function escapeHtml(str) { return str?.replace(/[&<>]/g, m => m === '&' ? '&amp;' : (m === '<' ? '&lt;' : '&gt;')) || ''; }
    // 滾動控制
    let autoScrollEnabled = true;

    function scrollToBottom() {
        if (!chatMessagesDiv || !autoScrollEnabled) return;
        const el = chatMessagesDiv;
        setTimeout(() => {
            if (el) el.scrollTop = el.scrollHeight;
        }, 50);
    }

    // Markdown 渲染（包含程式碼塊複製按鈕功能）
    // ----- 從稚自.html 移植的 renderMarkdown (含複製按鈕) -----



function renderMarkdown(text) {
    let html = marked.parse(text || '', { breaks: true });
    const temp = document.createElement('div');
    temp.innerHTML = html;

    temp.querySelectorAll('pre').forEach(pre => {
        if (pre.closest('.think-container')) return;

        const wrapper = document.createElement('div');
        wrapper.style.position = 'relative';
        wrapper.style.overflow = 'visible';

        const copyBtn = document.createElement('button');
        copyBtn.textContent = '📋 複製';
        copyBtn.className = 'copy-btn';
        copyBtn.style.cssText = `
            position: absolute; top: 6px; right: 6px;
            background: #4ec9b0; border: none; border-radius: 6px;
            padding: 8px 14px; font-size: 0.9rem;
            cursor: pointer; opacity: 0.9; z-index: 999;
            touch-action: manipulation;
        `;

        pre.parentNode.insertBefore(wrapper, pre);
        wrapper.appendChild(pre);
        wrapper.appendChild(copyBtn);
    });

    temp.querySelectorAll('pre code').forEach(block => hljs.highlightElement(block));
    return temp.innerHTML;
}
    


    // ------------------------------------------------------------


    // 滾動按鈕控制
    function initScrollButton() {
        scrollBtn = document.getElementById('scrollToBottomBtn');
        if (!scrollBtn || !chatMessagesDiv) return;
        const checkScroll = () => {
            const isAtBottom = chatMessagesDiv.scrollHeight - chatMessagesDiv.scrollTop - chatMessagesDiv.clientHeight < 10;
            scrollBtn.style.display = isAtBottom ? 'none' : 'flex';
        };
        chatMessagesDiv.addEventListener('scroll', checkScroll);
        scrollBtn.addEventListener('click', () => {
            chatMessagesDiv.scrollTo({ top: chatMessagesDiv.scrollHeight, behavior: 'smooth' });
        });
        checkScroll();  // <--- 新增這一行
    }

    // ---------- 快速跳轉面板 ----------

    let jumpDropdownVisible = false;

    function buildJumpDropdown() {
        const dropdown = document.getElementById('jumpDropdown');
        if (!dropdown) return;
        const userMessages = chatMessagesDiv.querySelectorAll('.message.user');
        if (userMessages.length === 0) {
            dropdown.innerHTML = '<div class="jump-item" style="color:#888; cursor:default;">尚無訊息</div>';
            return;
        }
        dropdown.innerHTML = '';
        // 從最新到最舊（與原面板一致）
        const reversed = Array.from(userMessages).reverse();
        reversed.forEach((msg) => {
            const text = msg.querySelector('.message-bubble')?.innerText || '訊息';
            const preview = text.length > 30 ? text.substring(0,30)+'…' : text;
            const id = msg.dataset.id || '?';
            const convId = msg.dataset.conv_id || '?';
            const item = document.createElement('div');
            item.className = 'jump-item';
            // 優先顯示 conv_id（conversation_history 的 ID），如果沒有則顯示 id
            const displayId = (convId && convId !== 'null' && convId !== 'undefined') ? convId : '?';
            item.textContent = `[ID:${displayId}] ${preview}`;
            item.addEventListener('click', () => {
                msg.scrollIntoView({ behavior: 'smooth', block: 'start' });
                toggleJumpDropdown(false);
            });
            dropdown.appendChild(item);
        });
    }

    function toggleJumpDropdown(show) {
        const dropdown = document.getElementById('jumpDropdown');
        if (!dropdown) return;
        if (show === undefined) {
            jumpDropdownVisible = !jumpDropdownVisible;
        } else {
            jumpDropdownVisible = show;
        }
        dropdown.style.display = jumpDropdownVisible ? 'block' : 'none';
        if (jumpDropdownVisible) {
            buildJumpDropdown(); // 每次顯示時刷新內容
        }
    }


    
    // 點擊頁面其他區域關閉下拉
    document.addEventListener('click', () => {
        if (jumpDropdownVisible) toggleJumpDropdown(false);
    });
    // 點擊下拉內部不關閉（阻止冒泡）
    document.getElementById('jumpDropdown')?.addEventListener('click', (e) => e.stopPropagation());

    // 每次新增用戶消息時，無需自動更新下拉（點擊按鈕時會重新構建）
    // 但為了保持即時性，可以在 sendUserMessage 中呼叫 buildJumpDropdown？但可選。











    // ---------- 工具列表 ----------
    function renderToolsContent() {
        const container = document.getElementById('toolsContent');
        container.innerHTML = '<div class="tools-loading">加載工具列表中...</div>';

        fetch('/api/tools')
            .then(res => res.json())
            .then(data => {
                const tools = data.tools;
                if (!tools.length) {
                    container.innerHTML = '<div>⚠️ 沒有加載任何工具。</div>';
                    return;
                }
                let html = `
                <div style="padding: 8px;">
                    <h3>🧰 已安裝工具</h3>
                    <ul style="list-style: none; padding-left: 0;">

                        <li style="margin-bottom: 16px; border-bottom: 1px solid #3e3e42; padding-bottom: 8px;">
                            <a href="javascript:void(0);" style="text-decoration: none; color: #4ec9b0;" id="toolsPanelReloadBtn">
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <span style="font-size: 1.4rem;">🔄</span>
                                <code style="background: #2d2d30; padding: 2px 6px; border-radius: 6px;">/reload</code>
                            </div>
                            <div style="margin-top: 6px; color: #ccc;">重新加載工具</div>
                            </a>
                        </li>


                        <li style="margin-bottom: 16px; border-bottom: 1px solid #3e3e42; padding-bottom: 8px;">
                            <a href="https://github.com/MOK2026/MOKAGI/tree/main/tools" style="text-decoration: none; color: #4ec9b0;" target="_blank">
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <span style="font-size: 1.4rem;">➕</span>
                                <code style="background: #2d2d30; padding: 2px 6px; border-radius: 6px;">/tools</code>
                            </div>
                            <div style="margin-top: 6px; color: #ccc;">增加工具</div>
                            </a>
                        </li>
                    `;
                tools.forEach(tool => {
                    html += `
                        <li style="margin-bottom: 16px; border-bottom: 1px solid #3e3e42; padding-bottom: 8px;">
                            <div style="display: flex; align-items: center; gap: 8px;">
                                <span style="font-size: 1.4rem;">${tool.icon || '🔧'}</span>
                                <code style="background: #2d2d30; padding: 2px 6px; border-radius: 6px;">${escapeHtml(tool.command)}</code>
                            </div>
                            <div style="margin-top: 6px; color: #ccc;">${escapeHtml(tool.description)}</div>
                        </li>
                    `;
                });
                html += '</ul></div>';
                container.innerHTML = html;

            // 為工具面板中的重新加載按鈕綁定事件（注意 id 改為 toolsPanelReloadBtn）
            const reloadBtn = document.getElementById('toolsPanelReloadBtn');
            if (reloadBtn) {
                reloadBtn.addEventListener('click', async () => {
                    await fetch('/api/reload_tools', { method: 'POST' });
                    alert('工具已重新加載');
                    renderToolsContent();  // 重新刷新工具列表
                });
            }
        })
        .catch(err => {
                console.error('加載工具列表失敗', err);
                container.innerHTML = '<div>❌ 加載工具列表失敗，請檢查網絡或服務器狀態。</div>';
            });
    }

    // ---------- Agent ----------
    async function loadAgentList() {
        try {
            const res = await fetch('/api/env_files', {
                credentials: 'same-origin'  // 或 'include'
            });
            const data = await res.json();
            // 儲存完整的 agent 資訊（含 last_active）
            agentList = data.agents.map(agent => ({ name: agent.name, file: agent.file, post: agent.post || "", last_active: agent.last_active || 0 }));
            agentIcons = {};
            agentPosts = {};
            data.agents.forEach(agent => {
                agentIcons[agent.name] = agent.icon;
                agentPosts[agent.name] = agent.post || "";
                // 初始化 agentStates（保留原有狀態，增加 lastActive）
                if (!agentStates[agent.name]) {
                    agentStates[agent.name] = { isRunning: agent.is_running || false, hasNewCompleted: false };
                } else {
                    agentStates[agent.name].isRunning = agent.is_running || false;
                }
                agentStates[agent.name].lastActive = agent.last_active || 0;
            });
            renderAgentList();
            
            if (agentList.length && (!currentAgent || !agentList.some(a => a.name === currentAgent))) {
                await activateAgent(agentList[0].name);
            }
        } catch (err) { console.error(err); }
    }

    

function renderAgentList() {
    const container = document.getElementById('agentList');
    if (!container) return;
    container.innerHTML = '';

    // ----- 按 lastActive 降序排序 -----
    const sortedAgents = [...agentList].sort((a, b) => {
        const aTime = agentStates[a.name]?.lastActive || 0;
        const bTime = agentStates[b.name]?.lastActive || 0;
        return bTime - aTime;
    });

    sortedAgents.forEach(agent => {
        const state = agentStates[agent.name] || { isRunning: false, hasNewCompleted: false };
        const icon = agentIcons[agent.name] || '🌸';
        const statusMark = state.isRunning ? ':...' : (state.hasNewCompleted ? '🔔' : '');
        let classes = 'agent-item';
        if (currentAgent === agent.name) {
            classes += ' active';
        } else if (state.isRunning) {
            classes += ' running';
        } else if (state.hasNewCompleted) {
            classes += ' has-new';
        }
        const div = document.createElement('div');
        div.className = classes;
        div.title = agent.name + ':' + (state.isRunning ? ' (執行中)' : (state.hasNewCompleted ? ' (有新完成)' : '')) + escapeHtml(agent.post);
        div.innerHTML = `<div class="agent-icon">${icon}</div><span class="agent-name">${escapeHtml(agent.name)}</span> <small class="agent-post" style="color:#888;font-size:0.7rem;">${escapeHtml(agent.post)}</small> ${statusMark}`;
        // 內聯點擊監聽
        div.addEventListener('click', function() {
            if (currentAgent !== agent.name) {
                activateAgent(agent.name);
            }
        });
        container.appendChild(div);
    });
}
    

// 在 activateAgent 函數開頭增加對 headerDisplay 和 agentList 的檢查，避免因元素缺失導致異常：
async function activateAgent(agentName) {

    const headerDisplay = document.getElementById('agentHeaderDisplay');
    if (!headerDisplay) {
        console.error('agentHeaderDisplay 元素不存在');
        return;
    }

    // ===== 在切換前，儲存當前 Agent 的編輯狀態 =====
    if (currentAgent) {
        const path = window.currentFilePath || '';
        const name = document.getElementById('edit-filename-span')?.textContent || '';
        const content = getEditorContent();
        saveEditorState(path, name, content);
    }



// 臨時顯示 Agent 名稱（即使後續請求失敗也能看到）
const agentInfo = agentList.find(a => a.name === agentName);
const tempIcon = agentInfo ? (agentIcons[agentName] || '🌸') : '🌸';
const tempPost = agentInfo ? (agentPosts[agentName] || '') : '';
if (headerDisplay) {
    headerDisplay.innerHTML = `${escapeHtml(tempIcon)} · <span id="currentAgentName">${escapeHtml(agentName)}</span> <small style="color:#888;font-size:0.8rem;">${escapeHtml(tempPost)}</small>`;
}


    
    // 從 agentList 中獲取實際的 file 路徑（後端返回的）
    //const agentInfo = agentList.find(a => a.name === agentName);
    const fileName = agentInfo ? agentInfo.file : ('.' + agentName);
    window.currentAgentFile = `.mok/agent/${agentName}/${fileName}`;   // 保存當前 Agent 的 .agent 檔案路徑
    try {
        // ===== 先切換 Agent 配置，確保後續請求獲取到最新配置 =====
        const envRes = await fetch('/api/set_env', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ filename: fileName })
        });
        if (!envRes.ok) {
            throw new Error(`set_env 請求失敗: ${envRes.status}`);
        }
        const envData = await envRes.json();
        if (envData.status !== 'ok') {
            throw new Error(`set_env 返回錯誤: ${envData.message || '未知錯誤'}`);
        }

        // ===== 再並行獲取配置、模型列表和歷史消息 =====
        const [configRes, modelsRes, historyRes] = await Promise.all([
            fetch('/api/mok_config'),
            fetch('/api/models'),
            fetch(`/api/chat_history?agent=${encodeURIComponent(agentName)}&limit=20&offset=0`)
        ]);

        // 獲取配置數據
        let configData = {};
        try {
            configData = await configRes.json();
        } catch (e) {
            console.warn('獲取配置失敗:', e);
        }
        mokConfig = configData;
        agenticon = mokConfig.MOK_AGENT_ICON || '🌸';

        // 獲取模型列表
        let modelsData = { models: [], current_index: 0 };
        try {
            modelsData = await modelsRes.json();
        } catch (e) {
            console.warn('獲取模型列表失敗:', e);
        }
        modelsList = modelsData.models || [];
        currentModelIndex = modelsData.current_index || 0;

        // 獲取歷史消息（用於 loadChatHistory）
        let historyData = { messages: [], has_more: false };
        try {
            historyData = await historyRes.json();
        } catch (e) {
            console.warn('獲取歷史消息失敗:', e);
        }

        // ===== 所有請求成功，開始更新 UI =====
        currentAgent = agentName;
        clearRestartWarning();  // 切換 Agent 時清除重啟警告

        // 🔧 根據新侍女的運行狀態，更新輸入框顯示/隱藏
        const newAgentState = agentStates[agentName];
        if (newAgentState && newAgentState.isRunning) {
            showWorkingIndicator();
        } else {
            hideWorkingIndicator();
        }

        // 更新左側列表的圖示
        agentIcons[agentName] = agenticon;

        // 設置頭部顯示（直接使用外層的 headerDisplay，不再重複聲明）
        if (headerDisplay) {
            headerDisplay.innerHTML = `${escapeHtml(agenticon)} · <span id="currentAgentName">${escapeHtml(currentAgent)}</span> <small style="color:#888;font-size:0.8rem;">${escapeHtml(agentPosts[currentAgent])}</small>`;
        }

        // 更新浮動圓點的圖示
        const menuToggle = document.getElementById('menuToggle');
        if (menuToggle) {
            menuToggle.innerText = agenticon;
        }

        // 更新右側工具面板的 agent icon 按鈕
        const agentInfoBtn = document.getElementById('agentInfoBtn');
        if (agentInfoBtn) {
            agentInfoBtn.textContent = agenticon;
            agentInfoBtn.title = `查看 ${agentName} 的 Soul / Jobs / Logs`;
        }

        // 若當前面板是 agent 資訊，自動刷新
        if (currentTool === 'agent') {
            renderAgentInfo();
        }

        renderAgentList();
        agentStartMsg = mokConfig.MOK_start_msg || '低語：您好～';

        // 更新 settings 面板（如果當前是 settings）
        if (currentTool === 'settings') {
            renderSettingsContent();
        }

        // ===== 使用並行獲取的歷史數據渲染聊天 =====
        // 直接使用 historyData 構建 allMessages
        const messages = historyData.messages || [];
        chatHistoryHasMore = historyData.has_more || false;
        chatHistoryOffset = 0;

        if (messages.length === 0) {
            // 無歷史，創建默認歡迎語並保存到服務器
            const defaultMsg = {
                role: 'assistant',
                content: `${agenticon} ${currentAgent}${agentStartMsg}`,
                thinkContent: null,
                timestamp: Date.now() / 1000
            };
            await saveChatMessageToServer(defaultMsg);
            allMessages = [defaultMsg];
        } else {
            // 反轉順序（後端返回的是新→舊，我們顯示需要舊→新）
            allMessages = messages.reverse().map(msg => ({
                id: msg.id,
                conv_id: msg.conv_id || null,
                role: msg.role,
                content: msg.content,
                thinkContent: msg.thinkContent,
                timestamp: msg.timestamp * 1000
            }));
        }

        // 渲染消息
        renderChatMessages(allMessages);

        // 檢測未完成工作列表
        let foundPendingList = null;
        for (const msg of allMessages) {
            if (msg.role === 'assistant' && msg.content) {
                if (msg.content.includes('未完成的工作') && msg.content.includes('繼續碼')) {
                    foundPendingList = msg.content;
                }
            }
        }
        if (foundPendingList) {
            window.pendingWorkList = foundPendingList;
            document.getElementById('showPendingBtn').style.display = 'inline-block';
        } else {
            window.pendingWorkList = null;
            document.getElementById('showPendingBtn').style.display = 'none';
        }

        // 切換 Agent 後，載入該 Agent 的編輯狀態
        loadEditorState();

        // 其他原有邏輯...
        const classifiedData = getAgentClassified(agentName);
        document.getElementById('showToolProcessBtn').style.display = classifiedData.toolProcess ? 'inline-block' : 'none';
        document.getElementById('showSemanticBtn').style.display = classifiedData.semanticSearch ? 'inline-block' : 'none';
        document.getElementById('showExperienceBtn').style.display = classifiedData.experience ? 'inline-block' : 'none';

        if (agentStates[agentName]) {
            agentStates[agentName].hasNewCompleted = false;
            renderAgentList();
        }
        window.pendingWorkList = null;
        document.getElementById('showPendingBtn').style.display = 'none';

    } catch (err) {
        console.error('activateAgent 錯誤:', err);
        // 嘗試重新加載 Agent 列表
        await loadAgentList();
        // 如果仍無 Agent，顯示錯誤信息
        if (headerDisplay) {
            headerDisplay.textContent = '❌ 加載失敗，請刷新頁面';
        }
    }
}




    // 模型
    async function loadModels() {
        try {
            const res = await fetch('/api/models');
            const data = await res.json();
            modelsList = data.models;
            currentModelIndex = data.current_index;
            if (currentTool === 'settings') renderSettingsContent();
        } catch (err) { console.error(err); }
    }
    async function switchModel(index) {
        try {
            const res = await fetch('/api/set_model', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ index })
            });
            const data = await res.json();
            if (data.status === 'ok') {
                currentModelIndex = index;
                localStorage.setItem(`agent_model_${currentAgent}`, index);
                if (currentTool === 'settings') renderSettingsContent();
                // 顯示後端返回的完整消息（與 Telegram 版一致）
                alert(data.message);
                setTimeout(() => {
                    location.reload();
                }, 3000);
            } else {
                alert(`切換失敗: ${data.message || '未知錯誤'}`);
            }
        } catch (err) {
            console.error(err);
            alert('請求失敗，請檢查網絡或服務器狀態');
        }
    }




















































// ---- 輔助：純文本 + 摺疊（穩健版） ----
function renderPlainTextWithFold(text) {
    if (!text) return '';

    // 存儲需要保護的標籤（用佔位符替代）
    const placeholders = [];

    // 1. 處理 ```html ... ``` 塊 → iframe
    let processed = text.replace(/```html\s*\n([\s\S]*?)```/g, function(match, htmlContent) {
        const safeContent = htmlContent
            .replace(/&/g, '&amp;')
            .replace(/"/g, '&quot;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;');
        const iframe = `<iframe srcdoc="${safeContent}" sandbox="allow-scripts" style="width:100%; min-height:200px; border:1px solid #4ec9b0; border-radius:8px; background:white;"></iframe>`;
        const placeholder = `__PLACEHOLDER_${placeholders.length}__`;
        placeholders.push(iframe);
        return placeholder;
    });

    // 2. 處理其他語言代碼塊 → 摺疊 details
    let withDetails = processed.replace(/```(\w*)\n([\s\S]*?)```/g, function(match, lang, code) {
        const codeEscaped = escapeHtml(code);
        const langDisplay = lang ? ` (${escapeHtml(lang)})` : '';
        const details = `<details style="margin:4px 0;">
            <summary style="cursor:pointer;color:#4ec9b0;font-weight:bold;">📄 點擊展開代碼塊${langDisplay}</summary>
            <pre style="background:#1e1e1e;padding:8px;border-radius:4px;overflow-x:auto;white-space:pre-wrap;word-break:break-all;"><code>${codeEscaped}</code></pre>
        </details>`;
        const placeholder = `__PLACEHOLDER_${placeholders.length}__`;
        placeholders.push(details);
        return placeholder;
    });

    // 2.3. 處理 code_index 📝 程式碼內容 → 摺疊（保留【N】標題和 📁 路徑可見）
    withDetails = withDetails.replace(/📝 (.+?)(?=\n【|\n*$)/gs, function(match, code) {
        const codeEscaped = escapeHtml(code);
        const details = `<details style="margin:2px 0 2px 20px;">
            <summary style="cursor:pointer;color:#ce9178;font-size:0.9rem;">📝 點擊展開程式碼</summary>
            <pre style="background:#1e1e1e;padding:6px 8px;border-radius:4px;overflow-x:auto;white-space:pre-wrap;word-break:break-all;font-size:0.82rem;"><code>${codeEscaped}</code></pre>
        </details>`;
        const placeholder = `__PLACEHOLDER_${placeholders.length}__`;
        placeholders.push(details);
        return placeholder;
    });

    // 2.5. 檢測並摺疊工具輸出區塊（以行為單位，連續工具輸出自動合併為一個摺疊塊）
    (function() {
        const lines = withDetails.split("\n");
        const resultLines = [];
        let toolBlock = [];
        let inToolBlock = false;

        function isToolLine(line) {
            const t = line.trim();
            if (!t) return false;
            // JSON 工具回應（長行）
            if (/^\{.*"(action|success|error_type|status|tool|error_message)".*\}$/.test(t) && t.length > 60) return true;
            // chunked 檔案讀取狀態
            if (/^\{"action":\s*"read_file"/.test(t)) return true;
            // 工具結果標頭
            if (/^(✅|📚|📋|📄|📝|🔧)/.test(t)) return true;
            // 搜索結果標題行 → 永遠顯示，不摺疊
            // CONFIRM_SPLIT 行
            if (t.includes("CONFIRM_SPLIT")) return true;
            return false;
        }

        for (let i = 0; i < lines.length; i++) {
            const line = lines[i];
            if (isToolLine(line)) {
                if (!inToolBlock) {
                    inToolBlock = true;
                    toolBlock = [];
                }
                toolBlock.push(line);
            } else {
                if (inToolBlock) {
                    const escapedBlock = escapeHtml(toolBlock.join("\n"));
                    const ph = "__PLACEHOLDER_" + placeholders.length + "__";
                    placeholders.push("<details class=\"tool-collapse\" style=\"margin:4px 0;\"><summary class=\"tool-summary\" title=\"🔧 工具輸出 — 點擊展開\"></summary><div style=\"margin-top:2px;padding:6px 10px;background:#252526;border-radius:4px;font-size:0.85rem;\">" + escapedBlock + "</div></details>");
                    resultLines.push(ph);
                    inToolBlock = false;
                }
                resultLines.push(line);
            }
        }
        if (inToolBlock) {
            const escapedBlock = escapeHtml(toolBlock.join("\n"));
            const ph = "__PLACEHOLDER_" + placeholders.length + "__";
            placeholders.push("<details class=\"tool-collapse\" style=\"margin:4px 0;\"><summary class=\"tool-summary\" title=\"🔧 工具輸出 — 點擊展開\"></summary><div style=\"margin-top:2px;padding:6px 10px;background:#252526;border-radius:4px;font-size:0.85rem;\">" + escapedBlock + "</div></details>");
            resultLines.push(ph);
        }

        withDetails = resultLines.join("\n");
    })();

    // 3. 對剩餘文本進行 HTML 轉義（佔位符不會被轉義）
    let escaped = escapeHtml(withDetails);

    // 4. 將佔位符替換回真實的 HTML 標籤
    for (let i = 0; i < placeholders.length; i++) {
        const placeholder = `__PLACEHOLDER_${i}__`;
        escaped = escaped.replace(new RegExp(placeholder, 'g'), placeholders[i]);
    }

    // 5. 換行轉 <br>
    let final = escaped.replace(/\n/g, '<br>');

    return `<div style="margin:4px 0;">${final}</div>`;
}






















async function loadChatHistoryFromServer() {
    chatHistoryOffset = 0;
    chatHistoryHasMore = true;
    try {
        const res = await fetch(`/api/chat_history?agent=${encodeURIComponent(currentAgent)}&limit=${CHAT_HISTORY_PAGE_SIZE}&offset=0`);
        const data = await res.json();
        let messages = data.messages || [];
        chatHistoryHasMore = data.has_more || false;
        if (messages.length === 0) {
            // 無歷史，創建默認歡迎語並保存到服務器
            const defaultMsg = {
                role: 'assistant',
                content: `${agenticon} ${currentAgent}${agentStartMsg}`,
                thinkContent: null,
                timestamp: Date.now() / 1000
            };
            await saveChatMessageToServer(defaultMsg);
            return [defaultMsg];
        }
        // 反轉順序（後端返回的是新→舊，我們顯示需要舊→新）
        return messages.reverse().map(msg => ({
            id: msg.id,
            conv_id: msg.conv_id || null,   // 如果沒有 conv_id 則設為 null
            role: msg.role,
            content: msg.content,
            thinkContent: msg.thinkContent,
            timestamp: msg.timestamp * 1000
        }));
    } catch (err) {
        console.error('加載歷史失敗', err);
        return [{
            role: 'assistant',
            content: `${agenticon} ${currentAgent}${agentStartMsg}`,
            thinkContent: null,
            timestamp: Date.now()
        }];
    }
}




// 加載聊天曆史（主入口）
async function loadChatHistory() {
    allMessages = await loadChatHistoryFromServer();
    renderChatMessages(allMessages);
    
    // 檢測未完成工作列表
    let foundPendingList = null;
    for (const msg of allMessages) {
        if (msg.role === 'assistant' && msg.content) {
            if (msg.content.includes('未完成的工作') && msg.content.includes('繼續碼')) {
                foundPendingList = msg.content;
            }
        }
    }
    if (foundPendingList) {
        window.pendingWorkList = foundPendingList;
        document.getElementById('showPendingBtn').style.display = 'inline-block';
    } else {
        window.pendingWorkList = null;
        document.getElementById('showPendingBtn').style.display = 'none';
    }
}




async function saveChatMessageToServer(msg) {
    try {
        await fetch('/api/chat_history', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                agent: msg.agent || currentAgent,
                role: msg.role,
                content: msg.content,
                thinkContent: msg.thinkContent || null,
                conv_id: msg.conv_id || null,
                timestamp: (msg.timestamp || Date.now()) / 1000
            })
        });
    } catch (err) {
        console.error('保存消息失敗', err);
    }
}

async function clearChatHistoryOnServer() {
    try {
        await fetch(`/api/chat_history?agent=${encodeURIComponent(currentAgent)}`, { method: 'DELETE' });
    } catch (err) {
        console.error('清除歷史失敗', err);
    }
}


// 刪除原有的 getChatStorageKey、loadChatHistory（基於localStorage）、saveChatMessage 等

// 新的 loadChatHistory (異步)
async function loadMoreChatHistory() {
    if (!chatHistoryHasMore || loadingMore) return [];
    loadingMore = true;
    chatHistoryOffset += CHAT_HISTORY_PAGE_SIZE;
    try {
        const res = await fetch(`/api/chat_history?agent=${encodeURIComponent(currentAgent)}&limit=${CHAT_HISTORY_PAGE_SIZE}&offset=${chatHistoryOffset}`);
        const data = await res.json();
        chatHistoryHasMore = data.has_more || false;
        const newMessages = (data.messages || []).reverse().map(msg => ({
            id: msg.id,
            conv_id: msg.conv_id || null,
            role: msg.role,
            content: msg.content,
            thinkContent: msg.thinkContent,
            timestamp: msg.timestamp * 1000
        }));
        // 將更舊的消息插入到最前面
        allMessages = newMessages.concat(allMessages);
        renderChatMessages(allMessages);
        return newMessages;
    } catch (err) {
        console.error('加載更多歷史失敗', err);
        chatHistoryOffset -= CHAT_HISTORY_PAGE_SIZE;
        return [];
    } finally {
        loadingMore = false;
    }
}

// renderChatMessages 保持不變（但注意 msg.timestamp 現在是毫秒）
// ---- 渲染聊天消息（純文本摺疊版） ----
function renderChatMessages(messages) {
    if (!chatMessagesDiv) chatMessagesDiv = document.getElementById('message-list');
    if (!chatMessagesDiv) return;
    chatMessagesDiv.innerHTML = '';
    let seqNum = 0;

    // ===== 智能代碼檢測與摺疊（內部輔助函數）=====
    function isCodeMessage(text) {
        if (!text || typeof text !== 'string') return false;
        const codePatterns = [
            /def\s+\w+\s*\([^)]*\)\s*:/,
            /class\s+\w+/,
            /import\s+\w+/,
            /from\s+\w+\s+import/,
            /tool_calls\s*:/,
            /"function"\s*:/,
            /"arguments"\s*:/,
            /"name"\s*:\s*"admin_/,
            /\/home\/ubuntu\/\.mok\//,
            /\/ubuntu\/\.mok\//,
            /grep\s+-rn/,
            /admin_exec/,
            /admin_read_file/,
            /```\w*/,
            /\bfunction\s*\(/,
            /=>\s*{/,
            /<[a-z][\s\S]*>/i,
            /\\"args\\":/,
        ];
        for (const pattern of codePatterns) {
            if (pattern.test(text)) return true;
        }
        if (text.includes('{') && text.includes('}') && text.length > 120) {
            return true;
        }
        const lines = text.split('\n');
        let indentCount = 0;
        for (const line of lines) {
            if (/^\s{4,}/.test(line)) indentCount++;
            if (indentCount > 2) return true;
        }
        return false;
    }

    function wrapWithFold(text, label = '📄 程式碼') {
        if (!text) return '';
        if (text.includes('code-fold-details')) return text;
        if (!isCodeMessage(text)) return text;
        const escaped = escapeHtml(text);
        const withBr = escaped.replace(/\n/g, '<br>');
        const contentHtml = `<div style="padding:8px 12px; overflow-x:auto; white-space:pre-wrap; word-break:break-word; font-family:'Cascadia Code','Courier New',monospace; font-size:0.85rem; background:#1a1a1a; border-radius:0 0 8px 8px; max-height:400px; overflow-y:auto;"><pre style="margin:0;padding:0;background:transparent;font-family:inherit;white-space:pre-wrap;word-break:break-word;">${withBr}</pre></div>`;
        return `<details style="margin:4px 0; border:1px solid #3e3e42; border-radius:8px; background:#1e1e1e;" open>
            <summary style="padding:6px 12px; cursor:pointer; color:#4ec9b0; font-weight:bold; background:#252526; border-radius:8px 8px 0 0; user-select:none; display:flex; align-items:center; gap:8px;">${label} (點擊展開/摺疊)</summary>
            ${contentHtml}
        </details>`;
    }

    function renderMessageContent(content, role) {
        if (!content) return '';
        if (isCodeMessage(content)) {
            const label = role === 'user' ? '📝 使用者程式碼' : '📄 程式碼';
            return wrapWithFold(content, label);
        }
        return escapeHtml(content).replace(/\n/g, '<br>');
    }
    // ===== 結束輔助函數 =====

    messages.forEach(msg => {
        seqNum++;
        const time = new Date(msg.timestamp).toLocaleString();
        const rawContent = msg.content || '';

        if (msg.role === 'user') {
            const div = document.createElement('div');
            div.className = "message user";
            div.dataset.id = msg.id || Date.now();
            div.dataset.conv_id = msg.conv_id;
            const renderedContent = renderMessageContent(rawContent, 'user');
            // --- 新增：估算 Token 與費用 ---
            const estimatedTokens = Math.ceil(rawContent.length / 4);
            const estimatedCost = (estimatedTokens * MokAgi_Token_Price_HK).toFixed(6);
            // -------------------------------
            // 用戶消息
            console.log('msg.conv_id:', msg.conv_id, 'msg.id:', msg.id);
            div.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;">
                    <div class="message-bubble" style="flex:1;">
                        ${renderedContent}
                        <button class="copy-msg-btn" data-msg="${escapeHtml(rawContent).replace(/"/g, '&quot;')}" style="background:none; border:none; cursor:pointer; color:#ccc; font-size:12px;">📋</button>
                    </div>
                </div>
                <div class="message-meta">[#${seqNum}] <button class="quote-id-btn" data-conv-id="${msg.conv_id || '?'}" style="background:#333; color:#4ec9b0; border:1px solid #4ec9b0; border-radius:4px; cursor:pointer; font-size:inherit; padding:0 4px;" title="複製引用對話ID">[ID:${msg.conv_id || '?'}]</button> ${time} · <button style="background:#333; color:#4ec9b0; border:1px solid #4ec9b0; border-radius:4px; cursor:pointer; font-size:inherit; padding:0 4px;"  onclick="renderTokenStats()" title="HK$18 每百萬詞元 · MOKAGI">~💰$${estimatedCost}/${estimatedTokens}🧮~</button></div>
            `;
            chatMessagesDiv.appendChild(div);
        } else {
            // 助手消息
            const div = document.createElement('div');
            div.className = 'message assistant';
            const content = escapeHtml(rawContent);
            const time = new Date(msg.timestamp).toLocaleString();

            // ---- 先使用純文本摺疊渲染（轉義 + 摺疊代碼塊） ----
            let rendered = '';
            try {
                rendered = renderPlainTextWithFold(rawContent);
            } catch (e) {
                rendered = `<div style="margin:4px 0;">${escapeHtml(rawContent)}</div>`;
            }

            // ---- 根據內容類型包裹摺疊塊 ----
            const contentText = rawContent;
            const isWork = contentText.includes('未完成的工作') && contentText.includes('繼續碼');

            const lines = rawContent.split('\n');
            const isLong = lines.length > Mok_web_lines;

            let processedHtml = '';
            if (isWork) {
                processedHtml = `<details style="margin:8px 0;" open>
                    <summary style="cursor:pointer;color:#4ec9b0;font-weight:bold;">📋 未完成的工作列表</summary>
                    <div style="margin-top:4px;padding:8px;background:#252526;border-radius:4px;">${rendered}</div>
                </details>`;
            } else {
                // 一般消息，檢查行數
                if (isLong) {
                    // 分割成舊內容（前部分）和新內容（最後 Mok_web_lines 行）
                    const oldLines = lines.slice(0, -Mok_web_lines);
                    const newLines = lines.slice(-Mok_web_lines);
                    const oldContent = oldLines.join('\n');
                    const newContent = newLines.join('\n');
                    const oldHtml = renderPlainTextWithFold(oldContent);
                    const newHtml = renderPlainTextWithFold(newContent);
                    // 舊內容摺疊（默認展開，以便用戶知道有更多內容）
                    processedHtml = `<details style="margin:8px 0;" open>
                        <summary style="cursor:pointer;color:#4ec9b0;font-weight:bold;">📄 較舊內容（點擊摺疊/展開）</summary>
                        <div style="margin-top:4px;padding:8px;background:#252526;border-radius:4px;">${oldHtml}</div>
                    </details>${newHtml}`;
                } else {
                    processedHtml = rendered;
                }
            }

            // ---- 思考區摺疊（默認收起） ----
            let thinkFoldHtml = '';
            if (msg.thinkContent) {
                const thinkEscaped = escapeHtml(msg.thinkContent);
                thinkFoldHtml = `
                    <details class="think-fold" style="margin:4px 0; border:1px solid #3a3a2e; border-radius:6px; background:#1e1e18; overflow:hidden;">
                        <summary style="cursor:pointer; color:#a09060; font-weight:bold; padding:6px 12px; font-size:0.85rem; background:#252520; user-select:none;">💭 思考過程（點擊展開）</summary>
                        <div class="think-container" style="max-height:30vh; overflow-y:auto; padding:8px 12px;">
                            <div style="font-size:0.7rem; color:#a09060; margin-bottom:4px;">${agenticon} ${currentAgent} 思考中...</div>
                            <div class="think-content" style="font-size:0.85rem; line-height:1.5; white-space:pre-wrap; word-break:break-word; color:#b0a080;">${thinkEscaped}</div>
                        </div>
                    </details>
                `;
            }

            // ---- 複製按鈕 ----
            const copyBtnHtml = `<button class="copy-msg-btn" data-msg="${content.replace(/"/g, '&quot;')}" style="background:none; border:none; cursor:pointer; color:#ccc; font-size:12px;">📋</button>`;

            // ---- 組裝 ----
            // --- 新增：估算 Token 與費用（包含思考內容） ---
            const totalText = (msg.thinkContent || '') + rawContent;
            const estimatedTokens = Math.ceil(totalText.length / 4);
            const estimatedCost = (estimatedTokens * MokAgi_Token_Price_HK).toFixed(6);
            // -------------------------------------------
            // 助手消息
            div.innerHTML = `
                <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;">
                    <div class="message-bubble" style="flex:1;">
                        ${thinkFoldHtml}
                        ${processedHtml}
                        ${copyBtnHtml}
                    </div>
                </div>
                <div class="message-meta">[#${seqNum}] <button class="quote-id-btn" data-conv-id="${msg.conv_id || '?'}" style="background:#333; color:#4ec9b0; border:1px solid #4ec9b0; border-radius:4px; cursor:pointer; font-size:inherit; padding:0 4px;" title="複製引用對話ID">[ID:${msg.conv_id || '?'}]</button> ${currentAgent} · ${time} · <button style="background:#333; color:#4ec9b0; border:1px solid #4ec9b0; border-radius:4px; cursor:pointer; font-size:inherit; padding:0 4px;"  onclick="renderTokenStats()" title="HK$18 每百萬詞元 · MOKAGI">~💰$${estimatedCost}/${estimatedTokens}🧮~</button></div>
            `;
            chatMessagesDiv.appendChild(div);
        }
    });

    // 綁定複製按鈕事件
    document.querySelectorAll('.copy-msg-btn').forEach(btn => {
        btn.removeEventListener('click', copyMsgHandler);
        btn.addEventListener('click', copyMsgHandler);
    });

    // 綁定引用對話 ID 按鈕事件
    document.querySelectorAll('.quote-id-btn').forEach(btn => {
        btn.removeEventListener('click', quoteIdClickHandler);
        btn.addEventListener('click', quoteIdClickHandler);
    });

    // 重新構建快速跳轉面板
    if (quickJumpPanel) quickJumpPanel.remove();

    // 滾動到底部（延遲確保 DOM 更新）
    setTimeout(scrollToBottom, 50);

    // 更新滾動按鈕狀態
    if (scrollBtn) {
        const isAtBottom = chatMessagesDiv.scrollHeight - chatMessagesDiv.scrollTop - chatMessagesDiv.clientHeight < 10;
        scrollBtn.style.display = isAtBottom ? 'none' : 'flex';
    } else {
        // 如果 scrollBtn 為空，嘗試重新獲取（保險）
        scrollBtn = document.getElementById('scrollToBottomBtn');
        if (scrollBtn && chatMessagesDiv) {
            const isAtBottom = chatMessagesDiv.scrollHeight - chatMessagesDiv.scrollTop - chatMessagesDiv.clientHeight < 10;
            scrollBtn.style.display = isAtBottom ? 'none' : 'flex';
        }
    }
}



















async function addUserMessageAndSave(content) {
    const msg = {
        role: 'user',
        content: content,
        thinkContent: null,
        timestamp: Date.now()
    };
    await saveChatMessageToServer(msg);
    const div = document.createElement('div');
    div.className = "message user";
    div.dataset.id = Date.now();
    const escapedContent = escapeHtml(content);
    // --- 新增：估算 Token 與費用 ---
    const estimatedTokens = Math.ceil(content.length / 4);
    // 使用 V4 Pro 輸出價格（18元/百萬tokens），即 18/1,000,000 元/token
    const estimatedCost = (estimatedTokens * MokAgi_Token_Price_HK).toFixed(6);
    // -------------------------------
    div.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;"><div class="message-bubble" style="flex:1;">${escapedContent}<button class="copy-msg-btn" data-msg="${escapedContent.replace(/"/g, '&quot;')}" style="background:none; border:none; cursor:pointer; color:#ccc; font-size:12px;">📋</button></div></div>
        <div class="message-meta">輸出已捷斷 ${new Date().toLocaleString()} · <button style="background:#333; color:#4ec9b0; border:1px solid #4ec9b0; border-radius:4px; cursor:pointer; font-size:inherit; padding:0 4px;"  onclick="renderTokenStats()" title="HK$18 每百萬詞元 · MOKAGI">~💰$${estimatedCost}/${estimatedTokens}🧮~</button></div>
    `;
    const copyBtn = div.querySelector('.copy-msg-btn');
    copyBtn.addEventListener('click', copyMsgHandler);
    chatMessagesDiv.appendChild(div);
    scrollToBottom();
}

// 添加助手消息並保存到服務器
async function addAssistantMessageAndSave(thinkContent, replyContent) {
    const msg = {
        role: 'assistant',
        content: replyContent,
        thinkContent: thinkContent || null,
        timestamp: Date.now()
    };
    await saveChatMessageToServer(msg);
    const div = document.createElement('div');
    div.className = 'message assistant';
    const content = escapeHtml(replyContent);
    const thinkHtml = thinkContent ? `<div class="think-container"><div style="font-size:0.7rem; color:#e0a800;">${agenticon} ${currentAgent}思考中...</div><div class="think-content">${escapeHtml(thinkContent)}</div></div>` : '';
    const renderedContent = renderPlainTextWithFold(replyContent);
    // --- 新增：估算 Token 與費用（包含思考內容） ---
    const totalText = (thinkContent || '') + replyContent;
    const estimatedTokens = Math.ceil(totalText.length / 4);
    // 使用 mokagi 輸出價格（18元/百萬tokens），即 18/1,000,000 元/token
    const estimatedCost = (estimatedTokens * MokAgi_Token_Price_HK).toFixed(6);
    // -------------------------------------------
    div.innerHTML = thinkHtml + `
        <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;">
            <div class="message-bubble" style="flex:1;">${renderedContent}<button class="copy-msg-btn" data-msg="${content.replace(/"/g, '&quot;')}" style="background:none; border:none; cursor:pointer; color:#ccc; font-size:12px;">📋</button></div>
        </div>
        <div class="message-meta">${currentAgent} · ${new Date().toLocaleString()} · <button style="background:#333; color:#4ec9b0; border:1px solid #4ec9b0; border-radius:4px; cursor:pointer; font-size:inherit; padding:0 4px;"  onclick="renderTokenStats()" title="HK$18 每百萬詞元 · MOKAGI">~💰$${estimatedCost}/${estimatedTokens}🧮~</button></div>
    `;
    const copyBtn = div.querySelector('.copy-msg-btn');
    copyBtn.addEventListener('click', copyMsgHandler);
    chatMessagesDiv.appendChild(div);
    scrollToBottom();
}

// 清除所有對話
async function clearAllChats() {
    if (confirm(`確定清除「${currentAgent}」的所有對話記錄嗎？`)) {
        await clearChatHistoryOnServer();
        await loadChatHistory();   // 重新加載（顯示默認歡迎語）
    }
}

// 🔧 SSE 串流發送（繞過 Socket.IO 502，使用 HTTP SSE 作為主要傳輸層）
let _activeSSEController = null;  // 用於取消正在進行的 SSE 請求

async function sendViaSSE(message, agent) {
    // 取消之前的 SSE 請求（如果有的話）
    if (_activeSSEController) {
        _activeSSEController.abort();
    }
    _activeSSEController = new AbortController();
    const controller = _activeSSEController;

    try {
        const response = await fetch('/api/chat', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ message, agent, user_id: userId }),
            signal: controller.signal
        });

        if (!response.ok) {
            console.error('[sendViaSSE] HTTP 錯誤:', response.status);
            // 🔧 fallback：HTTP 失敗時走 Socket.IO
            socket.emit('chat_message', { message, agent, source: 'web', user_id: userId });
            return;
        }

        const reader = response.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';

        while (true) {
            const { done, value } = await reader.read();
            if (done) {
                console.log('[sendViaSSE] SSE 串流結束');
                break;
            }

            buffer += decoder.decode(value, { stream: true });
            const lines = buffer.split('\n');
            buffer = lines.pop() || '';  // 保留不完整的行

            for (const line of lines) {
                if (line.startsWith('data: ')) {
                    try {
                        const eventData = JSON.parse(line.slice(6));
                        console.log('[sendViaSSE] 收到事件:', eventData.type, eventData.agent);
                        // 通過 CustomEvent 橋接，觸發與 Socket.IO 相同的處理器
                        window.dispatchEvent(new CustomEvent('chat_stream_sse', { detail: eventData }));
                    } catch (parseErr) {
                        console.warn('[sendViaSSE] JSON 解析失敗:', line.slice(0, 80), parseErr);
                    }
                }
            }
        }
    } catch (err) {
        if (err.name === 'AbortError') {
            console.log('[sendViaSSE] 請求已取消');
        } else {
            console.error('[sendViaSSE] 串流錯誤:', err);
            // 🔧 fallback：SSE 失敗時走 Socket.IO
            if (!socket.connected) {
                socket.connect();
            }
            socket.emit('chat_message', { message, agent, source: 'web', user_id: userId });
        }
    } finally {
        if (_activeSSEController === controller) {
            _activeSSEController = null;
        }
    }
}

async function sendUserMessage(content) {
    if (!currentAgent) {
        alert('請稍等，正在加載 Agent...');
        return;
    }
    // 🔧 確保 chatMessagesDiv 已初始化
    if (!chatMessagesDiv) {
        chatMessagesDiv = document.getElementById('chatMessages') || document.getElementById('message-list');
        if (!chatMessagesDiv) {
            console.error('[sendUserMessage] 找不到 chatMessagesDiv，無法發送訊息');
            return;
        }
    }
    let finalMessage = content;
    if (attachments.length > 0) {
        finalMessage = buildMessageWithAttachments(content);
    }
    // 🔧 僅清除當前 agent 的舊流式佔位（保留其他 agent 的背景工作中訊息）
    document.querySelectorAll('.message.assistant').forEach(el => {
        const meta = el.querySelector('.message-meta');
        if (meta && meta.innerText.includes('回應中') && meta.innerText.includes(currentAgent)) {
            el.remove();
        }
    });
    // 🔧 清除舊的 div 參考，防止舊 done 事件殘留
    currentThinkDiv[currentAgent] = null;
    currentReplyDiv[currentAgent] = null;
    currentAssistantDiv[currentAgent] = null;
    // 重置累積變數（僅當前侍女）
    accumulatedReply[currentAgent] = '';
    // 🔧 遞增世代計數器，防止舊 done 事件污染新 UI
    streamGen[currentAgent] = (streamGen[currentAgent] || 0) + 1;
    const myGen = streamGen[currentAgent];
    accumulatedThink[currentAgent] = '';

    // ---- 修復：確保 stream-container 存在 ----
    let streamContainer = document.getElementById('stream-container');
    if (!streamContainer) {
        // 若不存在，動態創建並插入到 chatMessagesDiv 中
        streamContainer = document.createElement('div');
        streamContainer.id = 'stream-container';
        streamContainer.style.cssText = 'display:flex; flex-shrink:0; height:35%; min-height:180px; max-height:45vh; border-top:1px solid #3e3e42; background:#1e1e1e; position:relative; overflow:hidden;';
        streamContainer.innerHTML = `
            <div style="display:flex; flex-direction:column; height:100%;">
                <div id="think-panel" style="max-height:30vh; overflow-y:auto; padding:6px 10px; border-bottom:1px solid #3e3e42; white-space:pre-wrap; word-break:break-word;">
                    <div style="font-weight:bold; font-size:0.85rem; color:#a09060; margin-bottom:4px;">💭 思考</div>
                    <div id="think-content" style="font-size:0.9rem; line-height:1.5;"></div>
                </div>
                <div id="reply-panel" style="flex:1; overflow-y:auto; padding:6px 10px; white-space:pre-wrap; word-break:break-word;">
                    <div style="font-weight:bold; font-size:0.9rem; color:#4ec9b0; margin-bottom:4px;">💬 回應</div>
                    <div id="reply-content" style="font-size:0.9rem; line-height:1.5;"></div>
                </div>
            </div>
            <button id="expand-stream-btn" style="position:absolute; top:6px; right:6px; background:#4ec9b0; border:none; border-radius:16px; padding:2px 12px; cursor:pointer; font-size:0.8rem; z-index:5;">🔍 展開</button>
        `;
        // 插入到 chatMessagesDiv 最後
        chatMessagesDiv.appendChild(streamContainer);
        // 重新綁定展開按鈕事件（若需要）
        streamContainer.querySelector('#expand-stream-btn')?.addEventListener('click', function() {
            const thinkText = document.getElementById('think-content')?.textContent || '';
            const replyText = document.getElementById('reply-content')?.textContent || '';
            if (!thinkText && !replyText) {
                alert('目前沒有流式內容可查看');
                return;
            }
            const modal = document.createElement('div');
            modal.className = 'expand-modal';
            modal.innerHTML = `
                <div class="modal-box">
                    <div class="modal-header">
                        <h3>📄 完整內容</h3>
                        <button class="modal-close">&times;</button>
                    </div>
                    <div class="modal-body">
                        <div class="panel">
                            <div class="label think">💭 思考</div>
                            <div class="content">${escapeHtml(thinkText) || '（無思考內容）'}</div>
                        </div>
                        <div class="panel">
                            <div class="label reply">💬 回應</div>
                            <div class="content">${escapeHtml(replyText) || '（無回應內容）'}</div>
                        </div>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
            modal.querySelector('.modal-close').addEventListener('click', () => modal.remove());
            modal.addEventListener('click', (e) => { if (e.target === modal) modal.remove(); });
        });
    } else {
        // 若已存在，確保顯示（修復 opacity:0 導致內容隱形）
        streamContainer.style.display = 'flex';
        streamContainer.style.opacity = '1';
        const tp = document.getElementById('think-panel');
        const rp = document.getElementById('reply-panel');
        if (tp) tp.style.opacity = '1';
        if (rp) rp.style.opacity = '1';
    }

    // 清空流式內容
    const thinkContent = document.getElementById('think-content');
    const replyContent = document.getElementById('reply-content');
    if (thinkContent) thinkContent.textContent = '';
    if (replyContent) replyContent.textContent = '';

    // 加入使用者訊息
    const time = new Date().toLocaleTimeString();
    const escapedContent = escapeHtml(content);
    const userDiv = document.createElement('div');
    userDiv.className = 'message user';
    userDiv.dataset.id = Date.now();
    userDiv.innerHTML = `
        <div style="display: flex; justify-content: space-between; align-items: flex-start; gap: 8px;"><div class="message-bubble" style="flex:1;">${escapedContent}<button class="copy-msg-btn" data-msg="${escapedContent.replace(/"/g, '&quot;')}" style="background:none; border:none; cursor:pointer; color:#ccc; font-size:12px;">📋</button></div></div>
        <div class="message-meta">${time}</div>
    `;
    chatMessagesDiv.appendChild(userDiv);
    const copyBtn = userDiv.querySelector('.copy-msg-btn');
    copyBtn.addEventListener('click', copyMsgHandler);
    scrollToBottom();

    // 建立助手佔位（僅供歷史記錄使用，流式內容將獨立顯示）
    const assistantDiv = document.createElement('div');
    assistantDiv.className = 'message assistant';
    assistantDiv.innerHTML = `<div class="think-container"><div style="font-size:0.7rem; color:#e0a800;">${agenticon} ${currentAgent}思考中...</div><div class="think-content"></div></div>
                            <div class="message-bubble" style="background:#3a3a3d;"></div>
                            <div class="message-meta">[ID:?] ${currentAgent} · 回應中</div>`;
    chatMessagesDiv.appendChild(assistantDiv);
    assistantDiv.dataset.streamGen = streamGen[currentAgent];
    scrollToBottom();

    currentAssistantDiv[currentAgent] = assistantDiv;
    currentThinkDiv[currentAgent] = assistantDiv.querySelector('.think-content');
    currentReplyDiv[currentAgent] = assistantDiv.querySelector('.message-bubble');

    // 🔧 確保 stream-container 可見（在發送前就顯示，避免延遲）
    const _sc = document.getElementById('stream-container');
    if (_sc) {
        _sc.style.display = 'flex';
        _sc.style.opacity = '1';
    }

    // 🔧 確認 socket 已連接
    if (!socket.connected) {
        console.warn('[sendUserMessage] Socket.IO 未連接，嘗試重新連接...');
        socket.connect();
    }
    console.log('[sendUserMessage] 發送 SSE 請求, agent=', currentAgent);
    // 🔧 使用 SSE 作為主要傳輸（繞過 Socket.IO 502），保留 socket.emit 作為 fallback
    sendViaSSE(finalMessage, currentAgent);

    // 🔧 立即標記工作中（不等 stream 事件回傳）
    if (agentStates[currentAgent]) {
        agentStates[currentAgent].isRunning = true;
        agentStates[currentAgent].hasNewCompleted = false;
    } else {
        agentStates[currentAgent] = { isRunning: true, hasNewCompleted: false };
    }
    renderAgentList();

    // 顯示工作中指示器
    showWorkingIndicator();
}



// === 工作指示器超時防護 | idx | 修復動畫卡住不關閉 | 202607292320 ===
let _workingTimer = null;
const WORKING_TIMEOUT_MS = 5 * 60 * 1000; // 5 分鐘超時，防止動畫永久卡住（安全網，正常 done 事件會先到）

// 顯示工作中指示器，隱藏輸入框（保留 .btnBox 發送按鈕）
function showWorkingIndicator() {
    const indicator = document.getElementById('workingIndicator');
    const inputWrapper = document.getElementById('chatInputWrapper');
    if (indicator) indicator.style.setProperty("display", "flex", "important");
    if (inputWrapper) inputWrapper.style.setProperty("display", "none", "important");
    // 更新 3D 角色頭部 icon 為當前 agent
    const headIcon = document.getElementById('workHeadIcon');
    if (headIcon && currentAgent && agentIcons[currentAgent]) {
        headIcon.textContent = agentIcons[currentAgent];
    }
    // 更新工作中文字為當前 agent 名稱 | idx | 移除硬寫「侍女」改為動態 agent 名 | 202607300150
    const workingText = document.getElementById('workingText');
    if (workingText && currentAgent) {
        workingText.textContent = currentAgent + ' 工作中...';
    }
    // 🔧 超時防護：5 分鐘後自動關閉（防止 done 事件遺失導致動畫卡住）
    if (_workingTimer) clearTimeout(_workingTimer);
    _workingTimer = setTimeout(() => {
        console.warn('⏰ 工作中指示器超時（5分鐘），自動關閉');
        hideWorkingIndicator();
    }, WORKING_TIMEOUT_MS);
}

// 隱藏工作中指示器，顯示輸入框
function hideWorkingIndicator() {
    const indicator = document.getElementById('workingIndicator');
    const inputWrapper = document.getElementById('chatInputWrapper');
    if (indicator) indicator.style.setProperty("display", "none", "important");
    if (inputWrapper) inputWrapper.style.setProperty("display", "block", "important");
    // 🔧 清除超時計時器
    if (_workingTimer) {
        clearTimeout(_workingTimer);
        _workingTimer = null;
    }
}

// ===========================================================
// === 重啟警告系統 | idx | 202607290024 ===
// ===========================================================

// 檢查 LLM 回覆是否需要重啟 pm2/mokagi
function checkRestartWarning(text) {
    if (!text || typeof text !== "string") return false;
    const hasPm2 = /重啟.*pm2|restart.*pm2|pm2.*重啟|pm2.*restart/i.test(text);
    const hasMokagi = /重啟.*mokagi|restart.*mokagi|mokagi.*重啟|mokagi.*restart/i.test(text);
    return hasPm2 && hasMokagi;
}

// 觸發重啟警告：左側面板動畫 + 對話框
function triggerRestartWarning(agent, reply) {
    const sidebar = document.getElementById("agentSidebar");
    if (sidebar) sidebar.classList.add("restart-warning");
    const activeItem = document.querySelector(".agent-item.active");
    if (activeItem) activeItem.classList.add("restart-warning");
    if (!chatMessagesDiv) chatMessagesDiv = document.getElementById("message-list");
    if (!chatMessagesDiv) return;
    const warningDiv = document.createElement("div");
    warningDiv.className = "message assistant restart-dialog";
    warningDiv.id = "restartWarningDialog";
    warningDiv.innerHTML = "<button class=\"restart-dismiss-btn\" onclick=\"clearRestartWarning()\" title=\"關閉警告\">✕</button>" +
        "<div class=\"message-bubble\">" +
        "<span class=\"restart-icon\">⚠️</span> " +
        "<span class=\"restart-text\">🛑 需要手動重啟！</span><br><br>" +
        "<span style=\"color:#ffaaaa;\">LLM 輸出中包含重啟指令，但系統無法自動執行。</span><br>" +
        "<span style=\"color:#ff9999;\">請在終端機手動執行：</span><br>" +
        "<code style=\"background:#0d0d0d; color:#ff6b6b; padding:6px 12px; border-radius:6px; display:inline-block; margin:6px 0; font-size:0.95rem;\">pm2 restart mokagi</code><br>" +
        "<span style=\"color:#ff9999; font-size:0.8rem;\">或分別重啟 pm2 和 mokagi 服務。</span>" +
        "</div>" +
        "<div class=\"message-meta\">⚠️ 系統警告 · " + new Date().toLocaleString() + "</div>";
    chatMessagesDiv.appendChild(warningDiv);
    chatMessagesDiv.scrollTo({ top: chatMessagesDiv.scrollHeight, behavior: "smooth" });
}

// 關閉重啟警告
function clearRestartWarning() {
    const sidebar = document.getElementById("agentSidebar");
    if (sidebar) sidebar.classList.remove("restart-warning");
    const activeItem = document.querySelector(".agent-item.restart-warning");
    if (activeItem) activeItem.classList.remove("restart-warning");
    const dialog = document.getElementById("restartWarningDialog");
    if (dialog) dialog.remove();
}
// ===========================================================

function stopGeneration() {
    // 🔧 取消正在進行的 SSE 請求
    if (_activeSSEController) {
        _activeSSEController.abort();
        _activeSSEController = null;
        console.log('[stopGeneration] SSE 請求已取消');
    }
    // 觸發後端緊急重啟
    socket.emit('stop_generation');
    // 等待 3 秒後刷新頁面，確保重啟完成
    setTimeout(() => {
        location.reload();
    }, 3000);
}

    // 文件樹
    async function fetchTree() {
        const treeContainer = document.getElementById('tree-root');
        const haveOnline = document.getElementById('haveOnline');

        
        if (!treeContainer) return;
        try {
            console.log('fetchTree 被呼叫');
            const response = await fetch('/api/tree');
            console.log('API 回應:', response);
            const data = await response.json();
            console.log('樹資料:', data);
            renderTree(data.tree, treeContainer);
        } catch (err) {
            //檢查是否連線失敗
            treeContainer.innerHTML = '加載失敗';
            haveOnline.innerHTML = `❌ 被${currentAgent}趕出房間了`;
        }
    }
    function renderTree(nodes, container) {
        container.innerHTML = '';
        const ul = document.createElement('ul');
        nodes.forEach(node => {
            const li = document.createElement('li');
            const span = document.createElement('span');
            span.className = node.is_dir ? 'dir' : 'file';
            span.innerText = node.name;
            li.appendChild(span);
            if (node.is_dir) {
                const childUl = document.createElement('ul');
                childUl.style.display = 'none';
                span.classList.add('collapsed');
                if (node.children) renderTree(node.children, childUl);
                li.appendChild(childUl);
                span.onclick = (e) => {
                    e.stopPropagation();
                    const isHidden = childUl.style.display === 'none';
                    childUl.style.display = isHidden ? 'block' : 'none';
                    span.classList.toggle('collapsed', !isHidden);
                    // ===== 新增：更新當前目錄 =====
                    setCurrentDirectory(node.path);
                };
            } else {
                span.onclick = async (e) => {
                    e.stopPropagation();
                    await loadFile(node.path, node.name);
                };
            }
            li.onclick = e => e.stopPropagation();
            ul.appendChild(li);
        });
        container.appendChild(ul);
    }

    

// ===== Monaco 編輯器內容輔助（自動處理初始化 + 自動保存監聽）=====
let _monacoSaveTimeout = null;
function setEditorContent(value, language) {
    const container = document.getElementById('file-editor');
    if (!container) return;
    const lang = language || getLanguageFromPath(window.currentFilePath || '');
    
    function doInit() {
        window.monacoHelp.init(container, value, lang);
        // 綁定自動保存監聽
        if (window.monacoHelp._editor) {
            window.monacoHelp._editor.getModel().onDidChangeContent(function() {
                const status = document.getElementById('save-status');
                if (status) {
                    status.textContent = '⏳ 儲存中...';
                    status.style.color = '#e0a800';
                }
                clearTimeout(_monacoSaveTimeout);
                _monacoSaveTimeout = setTimeout(() => {
                    saveFileContent(true);
                    const s = document.getElementById('save-status');
                    if (s) { s.textContent = '✅ 已儲存'; s.style.color = '#69db7c'; }
                }, 800);
            });
        }
    }
    
    if (!window.monacoHelp._editor || window.monacoHelp._container !== container) {
        window.monacoHelp.dispose();
        if (window._monacoReady) {
            doInit();
        } else {
            whenMonacoReady(doInit);
        }
    } else {
        window.monacoHelp.setValue(value);
        window.monacoHelp.setLanguage(lang);
    }
}
function getEditorContent() {
    return window.monacoHelp.getValue();
}


async function loadFile(path, name, silent = false) {
    // 檢查必要元素
    if (!imageViewer || !videoViewer || !currentFilenameSpan) return;

    const mediaView = document.getElementById('media-view');
    const editorArea = document.getElementById('editor-area');
    const editFilenameSpan = document.getElementById('edit-filename-span');
    const htmlPreview = document.getElementById('html-preview');
    const modeToggleBar = document.getElementById('mode-toggle-bar');
    const modeFilename = document.getElementById('mode-filename');
    const previewBtn = document.getElementById('previewModeBtn');
    const editBtn = document.getElementById('editModeBtn');

    // 重置顯示狀態
    imageViewer.style.display = 'none';
    videoViewer.style.display = 'none';
    htmlPreview.style.display = 'none';
    mediaView.style.display = 'block';   // 預設顯示媒體區（但內部元素隱藏）
    editorArea.style.display = 'none';
    modeToggleBar.style.display = 'none';

    // 更新檔名
    currentFilenameSpan.innerText = name;
    if (editFilenameSpan) editFilenameSpan.innerText = name;
    if (modeFilename) modeFilename.textContent = name;

    // 記錄目前檔案路徑
    window.currentFilePath = path;

    const ext = name.split('.').pop().toLowerCase();
    const rawUrl = `/api/raw/${encodeURIComponent(path)}`;

    // ---- 輔助：更新路徑輸入框 ----
    function updatePathInput() {
        const pathInput = document.getElementById('edit-filename-input');
        if (pathInput) pathInput.value = path;
    }

    // ---- 處理圖片 ----
    if (['jpg','jpeg','png','gif','webp','svg'].includes(ext)) {
        imageViewer.src = rawUrl;
        imageViewer.style.display = 'block';
        videoViewer.style.display = 'none';
        htmlPreview.style.display = 'none';
        editorArea.style.display = 'none';
        mediaView.style.display = 'block';
        modeToggleBar.style.display = 'none';
        updatePathInput();
        return;
    }
    // ---- 處理影片 ----
    else if (['mp4','webm','ogg'].includes(ext)) {
        videoViewer.src = rawUrl;
        videoViewer.style.display = 'block';
        imageViewer.style.display = 'none';
        htmlPreview.style.display = 'none';
        videoViewer.load();
        editorArea.style.display = 'none';
        mediaView.style.display = 'block';
        modeToggleBar.style.display = 'none';
        updatePathInput();
        return;
    }

    // ---- 處理 HTML 檔案（預覽/編輯） ----
    if (ext === 'html' || ext === 'htm') {
        // 載入檔案內容
        let content = '';
        try {
            const response = await fetch(`/api/file/${encodeURIComponent(path)}`);
            const data = await response.json();
            if (data.error) {
                console.error('載入檔案內容-讀取檔案失敗: ' + data.error);
                //alert('讀取檔案失敗: ' + data.error);
                return;
            }
            content = data.content;
            currentHtmlContent = content;
        } catch (err) {
            console.error('載入檔案內容-連線失敗: ' + err);
            //alert('連線失敗');
            return;
        }

        // 顯示模式切換列
        modeToggleBar.style.display = 'flex';
        mediaView.style.display = 'block';   // 顯示媒體區（內部切換）
        editorArea.style.display = 'none';   // 預設隱藏編輯器

        // 預設為預覽模式
        currentFileMode = 'preview';
        previewBtn.className = 'mode-btn active';
        previewBtn.style.background = '#4ec9b0';
        editBtn.className = 'mode-btn';
        editBtn.style.background = '#2d2d30';

        // 設定 Monaco 編輯器內容（HTML 模式）
        setEditorContent(content, 'html');
        // 顯示預覽
        htmlPreview.style.display = 'block';
        htmlPreview.srcdoc = content;
        // 隱藏圖片/影片
        imageViewer.style.display = 'none';
        videoViewer.style.display = 'none';
        // 隱藏編輯器
        editorArea.style.display = 'none';

        updatePathInput();

        // ---- 綁定切換按鈕事件（避免重複綁定） ----
        const newPreviewBtn = previewBtn.cloneNode(true);
        const newEditBtn = editBtn.cloneNode(true);
        previewBtn.parentNode.replaceChild(newPreviewBtn, previewBtn);
        editBtn.parentNode.replaceChild(newEditBtn, editBtn);

        newPreviewBtn.addEventListener('click', function() {
            if (currentFileMode === 'preview') return;
            // 若編輯器內容有變，更新 currentHtmlContent
            const editorContent = getEditorContent();
            if (editorContent !== currentHtmlContent) {
                currentHtmlContent = editorContent;
            }
            currentFileMode = 'preview';
            newPreviewBtn.className = 'mode-btn active';
            newPreviewBtn.style.background = '#4ec9b0';
            newEditBtn.className = 'mode-btn';
            newEditBtn.style.background = '#2d2d30';
            // 顯示預覽 iframe，並顯示 media-view 容器
            htmlPreview.style.display = 'block';
            htmlPreview.srcdoc = currentHtmlContent;
            mediaView.style.display = 'block';   // 恢復顯示媒體區
            editorArea.style.display = 'none';
        });
        newEditBtn.addEventListener('click', function() {
            if (currentFileMode === 'edit') return;
            currentFileMode = 'edit';
            newEditBtn.className = 'mode-btn active';
            newEditBtn.style.background = '#4ec9b0';
            newPreviewBtn.className = 'mode-btn';
            newPreviewBtn.style.background = '#2d2d30';
            // 隱藏預覽 iframe，並隱藏整個 media-view 容器
            htmlPreview.style.display = 'none';
            mediaView.style.display = 'none';    // 🔥 關鍵：隱藏 media-view
            editorArea.style.display = 'flex';
            // 確保編輯器內容為最新
            setEditorContent(currentHtmlContent, 'html');
        });
        // 保存按鈕的儲存功能仍沿用原有的 saveFileContent
        return;
    }

    // ---- 其他文字檔案（可編輯） ----
    const textExts = ['txt','md','json','js','css','py','cpp','c','java','go','rs','sh','yml','yaml','cfg','conf','ini','env','toml','bat','ps1','xml','csv'];
    let isTextFile = textExts.includes(ext);
    if (!isTextFile && name.startsWith('.')) {
        isTextFile = true;
    }

    if (isTextFile) {
        mediaView.style.display = 'none';
        editorArea.style.display = 'flex';
        modeToggleBar.style.display = 'none';
        try {
            const response = await fetch(`/api/file/${encodeURIComponent(path)}`);
            const data = await response.json();
            if (data.error) {
                console.error('文字檔案可編輯-讀取檔案失敗: ' + data.error);
                // alert('讀取檔案失敗: ' + data.error);
                editorArea.style.display = 'none';
                updatePathInput();
                return;
            }
            setEditorContent(data.content);
            if (!editorArea.style.height || editorArea.style.height === 'auto') {
                editorArea.style.height = '150px';
            }
        } catch (err) {
            console.error('文字檔案可編輯-連線失敗: ' + err);
            // alert('連線失敗');
            editorArea.style.display = 'none';
            updatePathInput();
            return;
        }
        updatePathInput();
        return;
    }

    // ---- 其他類型（不支援） ----
    mediaView.style.display = 'none';
    editorArea.style.display = 'none';
    modeToggleBar.style.display = 'none';
    if (!silent) console.warn('不支援的檔案類型: ' + ext);
        
        //alert('不支援的檔案類型');
    updatePathInput();
}
    





// ===== 當前目錄（用於新建檔案/資料夾）=====
let currentDirectory = '';

// 更新當前目錄（點擊目錄時調用）
function setCurrentDirectory(path) {
    currentDirectory = path;
    const dirPathEl = document.getElementById('current-dir-path');
if (dirPathEl) {
    dirPathEl.textContent = '/' + (path || '');
}
    // 移除對 edit-filename-input 的更新，避免幹擾 loadFile 的路徑顯示
}

// ===== 新建檔案 =====
async function createNewFile() {
    const name = prompt('請輸入新檔案名稱（例如 筆記.md）：');
    if (!name) return;
    const dir = currentDirectory || '';
    const fullPath = dir ? dir + '/' + name : name;
    try {
        const res = await fetch('/api/create_file', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: fullPath, content: '' })
        });
        const data = await res.json();
        if (data.status === 'ok') {
            // 重新整理樹
            fetchTree();
            // 開啟檔案
            loadFile(fullPath, name);
            // 儲存狀態（含路徑）
            saveEditorState(fullPath, name, '');
        } else {
            alert('建立檔案失敗：' + (data.error || '未知錯誤'));
        }
    } catch (err) {
        alert('連線錯誤：' + err.message);
    }
}

// ===== 新建資料夾 =====
async function createNewFolder() {
    const name = prompt('請輸入新資料夾名稱：');
    if (!name) return;
    const dir = currentDirectory || '';
    const fullPath = dir ? dir + '/' + name : name;
    try {
        const res = await fetch('/api/create_folder', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: fullPath })
        });
        const data = await res.json();
        if (data.status === 'ok') {
            fetchTree();
        } else {
            alert('建立資料夾失敗：' + (data.error || '未知錯誤'));
        }
    } catch (err) {
        alert('連線錯誤：' + err.message);
    }
}

// ===== 貼上剪貼簿（Monaco 版本）=====
async function pasteClipboard() {
    try {
        const text = await navigator.clipboard.readText();
        if (window.monacoHelp._editor) {
            window.monacoHelp.pasteAtCursor(text);
        }
    } catch (err) {
        alert('無法讀取剪貼簿：' + err.message);
    }
}

// ===== 儲存編輯狀態（localStorage）=====
function saveEditorState(path, name, content) {
    if (!currentAgent) return;
    const key = 'editor_state_' + currentAgent;
    try {
        localStorage.setItem(key, JSON.stringify({ path, name, content }));
    } catch (e) {}
}

// ===== 載入編輯狀態（localStorage）=====
function loadEditorState() {
    if (!currentAgent) return;
    const key = 'editor_state_' + currentAgent;
    let state = null;
    try {
        const raw = localStorage.getItem(key);
        if (raw) state = JSON.parse(raw);
    } catch (e) {}
    if (state && state.path) {
        // 如果檔案存在則開啟，否則顯示空白（使用者可自行建立）
        loadFile(state.path, state.name);
        // 若內容有變，填入編輯器
        if (state.content !== undefined) {
            setEditorContent(state.content);
        }
        // 更新儲存按鈕的顯示
        const filenameSpan = document.getElementById('edit-filename-span');
        if (filenameSpan) filenameSpan.textContent = state.name;
        const currentFilenameSpan = document.getElementById('current-filename');
        if (currentFilenameSpan) currentFilenameSpan.textContent = state.name;
        // ===== 更新路徑輸入框 =====
        const pathInput = document.getElementById('edit-filename-input');
        if (pathInput) pathInput.value = state.path;
        window.currentFilePath = state.path;
    } else {
        // 無狀態 → 建立預設檔案「新建文件.md」
        const defaultPath = '';
        const defaultName = '';
        // 先嘗試建立該檔案（若不存在）
        fetch('/api/create_file', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: defaultPath, content: '' })
        }).then(() => {
            loadFile(defaultPath, defaultName);
            saveEditorState(defaultPath, defaultName, '');
        }).catch(() => {
            // 若建立失敗（權限問題），仍試著開啟（可能顯示錯誤）
            loadFile(defaultPath, defaultName);
        });
    }
}

// ===== 改良 loadFile：在開啟檔案時記錄路徑並更新狀態 =====
const originalLoadFile = loadFile;
loadFile = async function(path, name) {
    await originalLoadFile(path, name);
    // 更新當前目錄（從路徑中提取目錄部分）
    const dir = path.includes('/') ? path.substring(0, path.lastIndexOf('/')) : '';
    setCurrentDirectory(dir);
    // 儲存狀態（包含當前內容）
    const content = getEditorContent();
    saveEditorState(path, name, content);
};

// ===== 改良 saveFileContent（支援靜默模式，且若檔案不存在則自動建立）=====
const originalSaveFileContent = saveFileContent;
saveFileContent = async function(silent = false) {
    const filenameSpan = document.getElementById('edit-filename-span');
    const path = window.currentFilePath;
    if (!path) {
        if (!silent) alert('沒有打開任何文件');
        return;
    }
    const content = getEditorContent();
    try {
        // 先嘗試儲存（若檔案不存在，後端會自動建立？我們統一用 save_file 但它不會建立，所以先檢查）
        // 我們改為先呼叫 /api/create_file 確保存在，再呼叫 /api/save_file
        // 但為了簡化，我們直接使用 create_file 並帶入內容（upsert）
        const res = await fetch('/api/create_file', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: path, content: content })
        });
        const data = await res.json();
        if (data.status === 'ok') {
            if (!silent) alert('✅ 檔案已儲存');
            // 更新狀態（時間戳等）
            saveEditorState(path, filenameSpan.textContent, content);
            // 更新儲存狀態顯示
            const status = document.getElementById('save-status');
            if (status) {
                status.textContent = '✅ 已儲存';
                status.style.color = '#69db7c';
            }
            // 重新整理文件樹（可選）
            fetchTree();
        } else {
            if (!silent) alert('❌ 儲存失敗：' + (data.error || '未知錯誤'));
        }
    } catch (err) {
        if (!silent) alert('❌ 請求失敗：' + err.message);
    }
};










function initContentResize() {
    const handle = document.getElementById('content-resize-handle');
    const contentArea = document.getElementById('content-area');
    const container = document.getElementById('toolsContent');
    if (!handle || !contentArea || !container) return;

    let startY, startHeight;

    function startDrag(clientY) {
        startY = clientY;
        startHeight = contentArea.offsetHeight;
        document.body.style.userSelect = 'none';
        document.body.style.touchAction = 'none';
    }

    function onDrag(clientY) {
        const delta = startY - clientY;          // 向上拖 → 增加高度
        const containerHeight = container.clientHeight;
        let newHeight = startHeight + delta;
        // 限制範圍 10% ~ 90%
        const minHeight = containerHeight * 0.05;
        const maxHeight = containerHeight * 1;
        newHeight = Math.min(Math.max(newHeight, minHeight), maxHeight);
        contentArea.style.height = newHeight + 'px';
        // 上方樹區會自動 flex:1 填滿其餘空間
    }

    function endDrag() {
        document.body.style.userSelect = '';
        document.body.style.touchAction = '';
    }

    // 滑鼠事件
    handle.addEventListener('mousedown', (e) => {
        startDrag(e.clientY);
        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
        e.preventDefault();
    });
    function onMouseMove(e) { onDrag(e.clientY); }
    function onMouseUp() {
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);
        endDrag();
    }

    // 觸控事件
    handle.addEventListener('touchstart', (e) => {
        e.preventDefault();
        const touch = e.touches[0];
        startDrag(touch.clientY);
        document.addEventListener('touchmove', onTouchMove);
        document.addEventListener('touchend', onTouchEnd);
    });
    function onTouchMove(e) {
        e.preventDefault();
        const touch = e.touches[0];
        onDrag(touch.clientY);
    }
    function onTouchEnd() {
        document.removeEventListener('touchmove', onTouchMove);
        document.removeEventListener('touchend', onTouchEnd);
        endDrag();
    }

    // 初始設為 50%
    contentArea.style.height = '50%';
}

function initInputResize() {
    const handle = document.getElementById('inputResizeHandle');
    const inputWrapper = document.getElementById('chatInputWrapper');
    if (!handle || !inputWrapper) return;

    let startY, startHeight;

    function startDrag(clientY) {
        startY = clientY;
        startHeight = inputWrapper.offsetHeight;
        document.body.style.userSelect = 'none';
        document.body.style.touchAction = 'none';
        handle.style.animation = 'none';
        handle.style.transform = 'scale(1.4)';
        handle.style.background = 'radial-gradient(circle, #10d8df 30%, transparent 70%)';
        handle.style.boxShadow = '0 0 20px #4ec9b0, 0 0 40px #4ec9b060';
    }

    function onDrag(clientY) {
        const delta = startY - clientY;          // 向上拖 → 增加高度
        let newHeight = startHeight + delta;
        // 限制範圍：最小 60px，最大 50vh
        const maxHeight = window.innerHeight * 0.5;
        newHeight = Math.max(60, Math.min(maxHeight, newHeight));
        inputWrapper.style.height = newHeight + 'px';
        inputWrapper.style.flexShrink = '0';
    }

    function endDrag() {
        document.body.style.userSelect = '';
        document.body.style.touchAction = '';
        handle.style.animation = '';
        handle.style.transform = '';
        handle.style.background = '';
        handle.style.boxShadow = '';
    }

    // 滑鼠事件
    handle.addEventListener('mousedown', (e) => {
        e.preventDefault();
        startDrag(e.clientY);
        document.addEventListener('mousemove', onMouseMove);
        document.addEventListener('mouseup', onMouseUp);
    });
    function onMouseMove(e) { onDrag(e.clientY); }
    function onMouseUp() {
        document.removeEventListener('mousemove', onMouseMove);
        document.removeEventListener('mouseup', onMouseUp);
        endDrag();
    }

    // 觸控事件
    handle.addEventListener('touchstart', (e) => {
        e.preventDefault();
        const touch = e.touches[0];
        startDrag(touch.clientY);
        document.addEventListener('touchmove', onTouchMove);
        document.addEventListener('touchend', onTouchEnd);
    });
    function onTouchMove(e) {
        e.preventDefault();
        const touch = e.touches[0];
        onDrag(touch.clientY);
    }
    function onTouchEnd() {
        document.removeEventListener('touchmove', onTouchMove);
        document.removeEventListener('touchend', onTouchEnd);
        endDrag();
    }
}












    // b) 保存文件內容（saveFileContent）
// ===== 儲存檔案（靜默模式可選）=====
async function saveFileContent(silent = false) {
    const pathInput = document.getElementById('edit-filename-input');
    const path = pathInput ? pathInput.value.trim() : window.currentFilePath;
    if (!path) {
        if (!silent) alert('沒有指定檔案路徑');
        return;
    }
    const content = window.monacoHelp.getValue();
    try {
        const res = await fetch('/api/create_file', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: path, content: content })
        });
        const data = await res.json();
        if (data.status === 'ok') {
            if (!silent) alert('✅ 檔案已儲存');
            // 更新狀態
            const status = document.getElementById('save-status');
            if (status) {
                status.textContent = '✅ 已儲存';
                status.style.color = '#69db7c';
            }
            // 更新 localStorage 狀態
            const name = path.split('/').pop();
            saveEditorState(path, name, content);
            // 刷新檔案樹
            fetchTree();
            // 若輸入框與實際路徑不同，同步
            if (pathInput) pathInput.value = path;
            window.currentFilePath = path;
        } else {
            if (!silent) alert('❌ 儲存失敗：' + (data.error || '未知錯誤'));
        }
    } catch (err) {
        if (!silent) alert('❌ 請求失敗：' + err.message);
    }
}












































    function renderAsciiContent() {
        document.getElementById('toolsContent').innerHTML = '<iframe src="/webTools/ASCII.html" style="width:100%; height:100%; border:none; border-radius:8px;"></iframe>';
    }

    function renderNovncContent() {
        document.getElementById('toolsContent').innerHTML = '<iframe src="/webTools/novnc/index.html" style="width:100%; height:100%; border:none; border-radius:8px;"></iframe>';
    }









function renderSettingsContent() {
    const container = document.getElementById('toolsContent');
    if (!window.currentAgentFile) {
        container.innerHTML = '<div style="padding:12px;color:#888;">⏳ 加載中...</div>';
        return;
    }

    // 先獲取配置
    fetch('/api/mok_config')
        .then(res => res.json())
        .then(configData => {
            mokConfig = configData;
            // 再獲取文件內容（如果失敗也不影響顯示）
            fetch('/api/file/' + encodeURIComponent(window.currentAgentFile))
                .then(res => res.json())
                .then(fileData => {
                    const rawContent = fileData.content || '';
                    window.rawAgentContent = rawContent;
                    renderConfigUI(configData, rawContent);
                })
                .catch(err => {
                    console.warn('讀取文件內容失敗，只顯示配置:', err);
                    window.rawAgentContent = '';
                    renderConfigUI(configData, '');
                });
        })
        .catch(err => {
            console.error('獲取配置失敗', err);
            container.innerHTML = '<div style="padding:12px;color:#ff6b6b;">❌ 無法讀取 Agent 配置</div>';
        });



    function renderConfigUI(config, rawContent) {
        // ----- 改進解析：按行保留註釋和順序 -----
        const lines = rawContent.split('\n');
        const entries = [];
        let commentBuffer = [];

        for (const line of lines) {
            const trimmed = line.trim();
            if (trimmed.startsWith('#')) {
                commentBuffer.push(trimmed);
            } else if (trimmed.includes('=')) {
                const eqIdx = trimmed.indexOf('=');
                const key = trimmed.substring(0, eqIdx).trim();
                const value = trimmed.substring(eqIdx + 1).trim();
                entries.push({
                    type: 'keyvalue',
                    key: key,
                    value: value,
                    comments: commentBuffer.slice()
                });
                commentBuffer = [];
            } else if (trimmed === '') {
                // 保留空行作為視覺分隔
                commentBuffer.push('');
            } else {
                // 非註釋非鍵值行（如分隔線）也保留
                commentBuffer.push(trimmed);
            }
        }

        // ----- 如果解析結果為空，直接顯示原始內容（只讀預覽）-----
        if (entries.length === 0) {
            container.innerHTML = `
                <div style="padding:12px;">
                    <h3 style="color:#4ec9b0; margin-bottom:12px;">${currentAgent} · 屬性</h3>
                    <p style="color:#888; font-size:0.9rem;">（無法解析鍵值對，顯示原始文件內容）</p>
                    <pre style="white-space:pre-wrap; background:#1e1e1e; padding:12px; border-radius:8px; border:1px solid #3e3e42; max-height:400px; overflow-y:auto;">${escapeHtml(rawContent)}</pre>
                </div>
            `;
            return;
        }

        // ----- 正常渲染（帶註釋）-----
        let html = `
            <div style="padding:12px;">
                <h3 style="color:#4ec9b0; margin-bottom:12px;">${currentAgent} · 屬性</h3>
                <div style="display:flex; flex-direction:column; gap:8px; margin-bottom:16px;">
        `;

        for (const entry of entries) {
            if (entry.type === 'keyvalue') {
                // 顯示註釋（按原始順序）
                if (entry.comments && entry.comments.length > 0) {
                    for (const comment of entry.comments) {
                        if (comment.trim() === '') {
                            html += `<div style="color:#888; font-size:0.8rem; padding-left:8px; height:1.2em;">&nbsp;</div>`;
                        } else {
                            html += `<div style="color:#888; font-size:0.8rem; padding-left:8px;">${escapeHtml(comment)}</div>`;
                        }
                    }
                }

                const key = entry.key;
                const value = entry.value;
                let inputHtml = '';
                if (typeof value === 'boolean' || value === 'true' || value === 'false') {
                    const checked = (value === true || value === 'true') ? 'checked' : '';
                    inputHtml = `<input type="checkbox" ${checked} data-key="${key}" style="width:20px; height:20px; accent-color:#4ec9b0;" />`;
                } else if (!isNaN(value) && value !== '') {
                    inputHtml = `<input type="number" value="${value}" data-key="${key}" style="flex:1; background:#1e1e1e; border:1px solid #4ec9b0; border-radius:4px; padding:4px 8px; color:#d4d4d4; font-family:inherit;" />`;
                } else {
                    inputHtml = `<input type="text" value="${escapeHtml(value)}" data-key="${key}" style="flex:1; background:#1e1e1e; border:1px solid #4ec9b0; border-radius:4px; padding:4px 8px; color:#d4d4d4; font-family:inherit;" />`;
                }
                html += `
                    <div style="display:flex; align-items:center; gap:12px; border-bottom:1px solid #3e3e42; padding-bottom:4px;">
                        <label style="width:160px; flex-shrink:0; color:#d4d4d4; font-weight:bold; font-size:0.9rem;">${escapeHtml(key)}</label>
                        ${inputHtml}
                    </div>
                `;
            }
        }

        html += `
                </div>
                <div style="display:flex; align-items:center; gap:16px;">
                    <button id="saveAgentConfigBtn" style="background:#4ec9b0; border:none; border-radius:20px; padding:6px 24px; font-weight:bold; cursor:pointer;">💾 儲存配置</button>
                    <span id="saveConfigStatus" style="font-size:0.8rem; color:#888;"></span>
                </div>
            </div>
        `;

        container.innerHTML = html;
        document.getElementById('saveAgentConfigBtn').addEventListener('click', saveAgentConfig);
    }

        



}

async function saveAgentConfig() {
    const inputs = document.querySelectorAll('#toolsContent input[data-key]');
    const updates = {};
    inputs.forEach(input => {
        const key = input.dataset.key;
        let val;
        if (input.type === 'checkbox') {
            val = input.checked ? 'true' : 'false';
        } else {
            val = input.value.trim();
        }
        updates[key] = val;
    });

    const statusEl = document.getElementById('saveConfigStatus');
    if (!window.currentAgentFile) {
        statusEl.textContent = '❌ 無法確定 Agent 文件名';
        statusEl.style.color = '#ff6b6b';
        return;
    }

    // 獲取原始內容
    let raw = window.rawAgentContent || '';
    // 更新鍵值
    const lines = raw.split('\n');
    const newLines = lines.map(line => {
        const trimmed = line.trim();
        if (trimmed.includes('=') && !trimmed.startsWith('#')) {
            const eqIdx = trimmed.indexOf('=');
            const key = trimmed.substring(0, eqIdx).trim();
            if (updates.hasOwnProperty(key)) {
                return `${key}=${updates[key]}`;
            }
        }
        return line;
    });
    const newContent = newLines.join('\n');

    try {
        const res = await fetch('/api/create_file', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ path: window.currentAgentFile, content: newContent })
        });
        const data = await res.json();
        if (data.status === 'ok') {
            statusEl.textContent = '✅ 儲存成功';
            statusEl.style.color = '#69db7c';
            // 重新獲取配置和原始內容
            const [configRes, fileRes] = await Promise.all([
                fetch('/api/mok_config'),
                fetch('/api/file/' + encodeURIComponent(window.currentAgentFile))
            ]);
            const configData = await configRes.json();
            const fileData = await fileRes.json();
            mokConfig = configData;
            window.rawAgentContent = fileData.content || '';
            setTimeout(() => { statusEl.textContent = ''; }, 3000);
        } else {
            throw new Error(data.error || '未知錯誤');
        }
    } catch (err) {
        statusEl.textContent = '❌ 儲存失敗: ' + err.message;
        statusEl.style.color = '#ff6b6b';
    }
}




    function renderMonitorContent() {
        const container = document.getElementById('toolsContent');
        container.innerHTML = '<iframe src="/webTools/monitor" style="width:100%; height:100%; border:none; border-radius:8px;"></iframe>';
    }

    function rendergame() {
        const container = document.getElementById('toolsContent');
        container.innerHTML = '<iframe src="/game" style="width:100%; height:100%; border:none; border-radius:8px;"></iframe>';
    }
    // Token 統計
    function renderTokenStats() {
        const container = document.getElementById('toolsContent');
        container.innerHTML = '<iframe src="/webTools/token_billing" style="width:100%; height:100%; border:none; border-radius:8px;"></iframe>';
    }

async function renderFilesContent() {
    const container = document.getElementById('toolsContent');

    // 1. 顯示本地視頻（循環播放）
    container.innerHTML = `
        <video id="loadingVideo" autoplay muted loop style="width:100%; height:100%; object-fit:cover;">
            <source src=".mok/work/mp4/88df647d-c401-4a89-9c71-a8549b044e24.mp4" type="video/mp4">
        </video>
    `;

    // 2. 模擬加載（實際等待文件樹數據）
    //    注意：原 fetchTree() 依賴 DOM 節點 #tree-root，但此時它還不存在，所以要等真正渲染時才調用。
    //    因此先延遲一小段時間，讓視頻顯示出來，再構建真正的界面。
    await new Promise(resolve => setTimeout(resolve, 300)); // 至少讓視頻播放 300ms（可調整）

    // 3. 構建真正的界面（複製原 renderFilesContent 的全部 HTML 字符串）
    container.innerHTML = `
        <div style="height:100%; display:flex; flex-direction:column;">
            <div id="tree-container" style="flex:1; flex-shrink:0; overflow-y:auto; padding:8px;">
                <div id="tree-root"></div>
            </div>
            <div id="content-resize-handle"></div>
            <div id="content-area" style="height:50%; display:flex; flex-direction:column; overflow:hidden;">
                <!-- 模式切換列 -->
                <div id="mode-toggle-bar" style="display:none; background:#252526; padding:4px 8px; border-bottom:1px solid #3e3e42; flex-shrink:0;">
                    <button id="previewModeBtn" class="mode-btn active" style="background:#4ec9b0; border:none; border-radius:12px; padding:2px 12px; cursor:pointer; font-size:0.8rem;">🌐 預覽</button>
                    <button id="editModeBtn" class="mode-btn" style="background:#2d2d30; border:none; border-radius:12px; padding:2px 12px; cursor:pointer; font-size:0.8rem;">✏️ 編輯</button>
                    <span id="mode-filename" style="color:#4ec9b0; margin-left:12px; font-size:0.8rem;"></span>
                </div>
                <div id="media-view" style="display:none; flex:1; overflow:auto; padding:8px;">
                    <img id="image-viewer" style="display:none; max-width:100%; border-radius:8px;" />
                    <video id="video-viewer" style="display:none; max-width:100%; border-radius:8px;" controls></video>
                    <iframe id="html-preview" style="display:none; width:100%; height:100%; border:none; border-radius:8px; background:white;"></iframe>
                    <div id="current-filename" style="display:none;"></div>
                </div>
                <div id="editor-area" style="display:none; flex:1; flex-direction:column; padding:8px;">
                    <div style="display:flex; gap:6px; align-items:center; margin-bottom:4px; flex-wrap:wrap;">
                        <span style="color:#4ec9b0; font-size:0.8rem;">📄 路徑</span>
                        <input id="edit-filename-input" type="text" style="flex:1; min-width:120px; background:#1e1e1e; color:#d4d4d4; border:1px solid #4ec9b0; border-radius:4px; padding:2px 6px; font-family:inherit; font-size:0.9rem;" value="" spellcheck="false">
                        <span id="save-status" style="font-size:0.7rem; color:#888; margin-left:auto;">已儲存</span>
                    </div>
                    <div id="file-editor" style="flex:1; min-height:200px;"></div>
                </div>
            </div>
            <div style="padding:4px 8px; display:flex; gap:6px; align-items:center; flex-shrink:0; flex-wrap:wrap; background:#252526; border-top:1px solid #3e3e42;">
                <button id="create-from-path-btn" style="background:#e0a800; padding:2px 12px;">📂 建立</button>
                <button id="download-file-btn" style="background:#4ec9b0; padding:2px 12px;">⬇️ 下載</button>
                <button id="save-file-btn" style="background:#4ec9b0; padding:2px 12px;">💾 儲存</button>
                <button id="paste-clipboard-btn" style="background:#4ec9b0; padding:2px 12px;">📋 貼上</button>
                <span id="current-dir-path" style="display:none;"></span>
            </div>
        </div>
    `;

    // 4. 重新綁定所有元素引用和事件（原 renderFilesContent 中的綁定）
    imageViewer = document.getElementById('image-viewer');
    videoViewer = document.getElementById('video-viewer');
    currentFilenameSpan = document.getElementById('current-filename');

    document.getElementById('save-file-btn').addEventListener('click', function(){ saveFileContent(false); });
    document.getElementById('download-file-btn').addEventListener('click', downloadCurrentFile);
    document.getElementById('create-from-path-btn').addEventListener('click', createFromPath);
    document.getElementById('paste-clipboard-btn').addEventListener('click', pasteClipboard);

    initContentResize();
    // 加載文件樹（真正獲取數據）
    await fetchTree();
    loadEditorState();


}




















// ===== 下載目前編輯的檔案 =====
function downloadCurrentFile() {
    const pathInput = document.getElementById('edit-filename-input');
    const content = getEditorContent();
    let filename = pathInput ? pathInput.value.trim() : '下載.txt';
    if (!filename) filename = '下載.txt';
    // 只取最後一節作為檔名
    const parts = filename.split('/');
    const name = parts[parts.length-1] || '下載.txt';
    const blob = new Blob([content], { type: 'text/plain;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = name;
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    URL.revokeObjectURL(url);
}

// ===== 根據路徑輸入框建立檔案或資料夾 =====
async function createFromPath() {
    const pathInput = document.getElementById('edit-filename-input');
    const path = pathInput.value.trim();
    if (!path) {
        alert('請在路徑輸入框中輸入要建立的路徑（例如：folder/ 或 file.md）');
        return;
    }
    const isDir = path.endsWith('/');
    const content = getEditorContent();

    try {
        if (isDir) {
            const dirPath = path.slice(0, -1);
            const res = await fetch('/api/create_folder', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: dirPath })
            });
            const data = await res.json();
            if (data.status === 'ok') {
                fetchTree();
                alert('📁 資料夾已建立');
                // 更新當前目錄為該資料夾
                setCurrentDirectory(dirPath);
                pathInput.value = dirPath + '/';
            } else {
                alert('❌ 建立資料夾失敗：' + (data.error || '未知錯誤'));
            }
        } else {
            const res = await fetch('/api/create_file', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ path: path, content: content })
            });
            const data = await res.json();
            if (data.status === 'ok') {
                fetchTree();
                loadFile(path, path.split('/').pop());
                alert('✅ 檔案已建立');
            } else {
                alert('❌ 建立檔案失敗：' + (data.error || '未知錯誤'));
            }
        }
    } catch (err) {
        alert('❌ 請求失敗：' + err.message);
    }
}





















    function switchTool(tool) {
        // 若離開日誌頁面，取消訂閱
        if (currentTool === 'logs' && tool !== 'logs') {
            unsubscribeLogs();
        }
        currentTool = tool;
        // 更新按鈕 active 狀態（原有邏輯）
        // ===== 事件委派：工具按鈕 =====
        const toolsHeader = document.querySelector('.tools-header-buttons');
        if (toolsHeader) {
            toolsHeader.addEventListener('click', (e) => {
                const btn = e.target.closest('button');
                if (btn && btn.dataset.tool) {
                    switchTool(btn.dataset.tool);
                }
            });
        }

        if (tool === 'files') renderFilesContent();
        else if (tool === 'ascii') renderAsciiContent();
        else if (tool === 'settings') renderSettingsContent();
        else if (tool === 'monitor') renderMonitorContent();
        else if (tool === 'tools') renderToolsContent();
        else if (tool === 'tokenstats') renderTokenStats();
        else if (tool === 'logs') renderLogsContent();
        else if (tool === 'search') renderSearchContent();

        else if (tool === 'game') rendergame();
        else if (tool === 'novnc') renderNovncContent();
        else if (tool === 'agent') renderAgentInfo();

    }

    // 側邊欄拖拽調整寬度
    function initResize() {
        const leftSidebar = document.getElementById('agentSidebar');
        const rightPanel = document.getElementById('toolsPanel');
        const rightHandle = document.getElementById('right-resize-handle');
        let startX, startWidth;

        function startDrag(e, isLeft) {
            startX = e.clientX;
            startWidth = isLeft ? leftSidebar.clientWidth : rightPanel.clientWidth;
            document.body.style.userSelect = 'none';
            document.addEventListener('mousemove', onMouseMove);
            document.addEventListener('mouseup', onMouseUp);
            function onMouseMove(e) {
                const dx = e.clientX - startX;
                let newWidth = startWidth + (isLeft ? dx : -dx);
                // 側面板（.tools-panel）的最大可拖動寬度
                const maxWidth = window.innerWidth * 0.9;
                newWidth = Math.min(Math.max(newWidth, 60), maxWidth);
                if (isLeft) leftSidebar.style.width = newWidth + 'px';
                else rightPanel.style.width = newWidth + 'px';
            }
            function onMouseUp() {
                document.removeEventListener('mousemove', onMouseMove);
                document.removeEventListener('mouseup', onMouseUp);
                document.body.style.userSelect = '';
            }
            e.preventDefault();
        }
        rightHandle?.addEventListener('mousedown', e => startDrag(e, false));
    }

    // 側邊欄摺疊/展開 + 手機按鈕
    function initSidebars() {
        const leftSidebar = document.getElementById('agentSidebar');
        const rightPanel = document.getElementById('toolsPanel');
        const toggleLeft = document.getElementById('toggleAgentSidebar');
        const toggleRight = document.getElementById('toggleToolsPanel');
        const mobileLeftBtn = document.getElementById('mobileOpenLeftBtn');
        const mobileRightBtn = document.getElementById('mobileOpenRightBtn');
        const isMobile = () => window.innerWidth <= 768;

        // 桌面端摺疊按鈕
        if (toggleLeft) {
            toggleLeft.addEventListener('click', () => {
                if (isMobile()) leftSidebar.classList.toggle('mobile-open');
                else leftSidebar.classList.toggle('collapsed');
            });
        }
        if (toggleRight) {
            toggleRight.addEventListener('click', () => {
                if (isMobile()) rightPanel.classList.toggle('mobile-open');
                else rightPanel.classList.toggle('collapsed');
            });
        }

        // 手機端專用：點擊聊天區關閉側邊欄
        if (mobileLeftBtn) {
            mobileLeftBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                leftSidebar.classList.add('mobile-open');
            });
        }
        if (mobileRightBtn) {
            mobileRightBtn.addEventListener('click', (e) => {
                e.stopPropagation();
                rightPanel.classList.add('mobile-open');
            });
        }
        // 點擊聊天主區域關閉所有浮動面板（手機）
        document.querySelector('.chat-main')?.addEventListener('click', () => {
            if (isMobile()) {
                leftSidebar.classList.remove('mobile-open');
                rightPanel.classList.remove('mobile-open');
            }
        });
        // 窗口大小變化時，如果回到桌面模式，需要移除手機樣式
        window.addEventListener('resize', () => {
            if (window.innerWidth > 768) {
                leftSidebar.classList.remove('mobile-open');
                rightPanel.classList.remove('mobile-open');
            }
        });
    }

    // 🔧 檢查 DOM 元素是否仍屬於當前活躍的流式訊息
    // 若 div 已脫離 DOM 或 meta 不再顯示「回應中」（已被其他 done 完成），拒絕更新
    function _checkStreamGen(agent, el) {
        if (!el || !el.closest) return false;
        const msgDiv = el.closest('.message.assistant');
        if (!msgDiv) return false;
        if (!document.contains(msgDiv)) return false;
        // 🔧 放寬檢查：只要 meta 存在且 div 在 DOM 中就允許更新
        // 之前的嚴格「回應中」檢查導致 done 事件時 meta 已被更新而無法渲染
        const meta = msgDiv.querySelector('.message-meta');
        if (!meta) return false;
        return true;
    }

    // 🔧 若當前侍女但流式 UI（stream-container + assistantDiv）遺失，重建之
    function _ensureStreamUI(agent) {
        if (agent !== currentAgent) return;
        let sc = document.getElementById('stream-container');
        if (!sc) {
            sc = document.createElement('div');
            sc.id = 'stream-container';
            sc.style.cssText = 'display:flex; flex-shrink:0; height:35%; min-height:180px; max-height:45vh; border-top:1px solid #3e3e42; background:#1e1e1e; position:relative; overflow:hidden;';
            sc.innerHTML = '<div style=\"display:flex; flex-direction:column; height:100%;\"><div id=\"think-panel\" style=\"max-height:30vh; overflow-y:auto; padding:6px 10px; border-bottom:1px solid #3e3e42; white-space:pre-wrap; word-break:break-word;\"><div style=\"font-weight:bold; font-size:0.85rem; color:#a09060; margin-bottom:4px;\">💭 思考</div><div id=\"think-content\" style=\"font-size:0.9rem; line-height:1.5;\"></div></div><div id=\"reply-panel\" style=\"max-height:30vh; overflow-y:auto; padding:6px 10px; white-space:pre-wrap; word-break:break-word;\"><div style=\"font-weight:bold; font-size:0.85rem; color:#4ec9b0; margin-bottom:4px;\">💬 回應</div><div id=\"reply-content\" style=\"font-size:0.9rem; line-height:1.5;\"></div></div></div>';
            const msgList = document.getElementById('message-list');
            if (msgList) msgList.appendChild(sc);
            const tc = document.getElementById('think-content');
            const rc = document.getElementById('reply-content');
            if (tc) tc.textContent = accumulatedThink[agent] || '';
            if (rc) rc.textContent = accumulatedReply[agent] || '';
            sc.style.display = 'flex';
            sc.style.opacity = '1';
        }
        if (!currentAssistantDiv[agent] || !document.contains(currentAssistantDiv[agent])) {
            const msgList = document.getElementById('message-list');
            if (msgList) {
                const assistantDiv = document.createElement('div');
                assistantDiv.className = 'message assistant';
                const agenticon = agentIcons[agent] || '🌸';
                assistantDiv.innerHTML = '<div class=\"think-container\"><div style=\"font-size:0.7rem; color:#e0a800;\">' + agenticon + ' ' + agent + '思考中...</div><div class=\"think-content\"></div></div><div class=\"message-bubble\" style=\"background:#3a3a3d;\"></div><div class=\"message-meta\">[ID:?] ' + agent + ' · 回應中</div>';
                assistantDiv.dataset.streamGen = streamGen[agent] || 0;
                msgList.appendChild(assistantDiv);
                currentAssistantDiv[agent] = assistantDiv;
                currentThinkDiv[agent] = assistantDiv.querySelector('.think-content');
                currentReplyDiv[agent] = assistantDiv.querySelector('.message-bubble');
                if (currentThinkDiv[agent]) currentThinkDiv[agent].innerText = accumulatedThink[agent] || '';
                if (currentReplyDiv[agent]) currentReplyDiv[agent].innerText = accumulatedReply[agent] || '';
            }
        }
    }

    // Socket 事件
function _onChatStream(data) {
    console.log('[chat_stream] 收到事件:', data.type, data.agent, data.content ? data.content.substring(0, 50) : '');
    const agent = data.agent || currentAgent;
    if (!agent) {
        console.warn('[chat_stream] 無法識別 agent，跳過狀態更新');
        return;
    }
    if (!agentStates[agent]) {
        agentStates[agent] = { isRunning: false, hasNewCompleted: false };
    }
    // 🔧 done 事件不需要標記工作中
    if (data.type !== 'done') {
        const wasRunning = agentStates[agent].isRunning;
        agentStates[agent].isRunning = true;
        // 🔧 只在狀態真正變化時才重建列表（避免每個 chunk 都重建 DOM）
        if (!wasRunning) {
            renderAgentList();
        }
        // 🔧 只有當前侍女工作中才隱藏輸入框（背景侍女不影響）
        if (agent === currentAgent) {
            showWorkingIndicator();
        }
    }

    if (data.type === 'think') {
        accumulatedThink[agent] = (accumulatedThink[agent] || '') + data.content;
        // 🔧 若當前侍女但流式 DOM 遺失（切換過侍女），重建 UI
        if (agent === currentAgent) {
            _ensureStreamUI(agent);
            // 🔧 確保 stream-container 可見
            const _sc = document.getElementById('stream-container');
            if (_sc && _sc.style.display === 'none') {
                _sc.style.display = 'flex';
                _sc.style.opacity = '1';
            }
        }
        const thinkPanel = document.getElementById('think-content');
        if (thinkPanel && agent === currentAgent) {
            thinkPanel.textContent = accumulatedThink[agent];
            document.getElementById('think-panel').scrollTop = document.getElementById('think-panel').scrollHeight;
        }
        // 🔧 只更新與當前世代匹配的 div
        if (currentThinkDiv[agent] && _checkStreamGen(agent, currentThinkDiv[agent])) {
            currentThinkDiv[agent].innerText = accumulatedThink[agent];
        }
    } else if (data.type === 'reply') {
        const subtype = data.subtype || 'normal';
        const content = data.content || '';
        // 🔧 確保 stream-container 可見（每次 reply 都檢查，防止被意外隱藏）
        if (agent === currentAgent) {
            const _sc = document.getElementById('stream-container');
            if (_sc && _sc.style.display === 'none') {
                _sc.style.display = 'flex';
                _sc.style.opacity = '1';
            }
        }
        if (subtype === 'pending_list') {
            pendingWorkList = content;
            document.getElementById('showPendingBtn').style.display = 'inline-block';
            return;
        }
        if (subtype === 'tool_process') {
            const agentData = getAgentClassified(agent);
            agentData.toolProcess += content + '\n\n';
            if (agent === currentAgent) {
                document.getElementById('showToolProcessBtn').style.display = 'inline-block';
            }
            return;
        }
        if (subtype === 'semantic_search') {
            const agentData = getAgentClassified(agent);
            agentData.semanticSearch += content + '\n\n';
            if (agent === currentAgent) {
                document.getElementById('showSemanticBtn').style.display = 'inline-block';
            }
            return;
        }
        if (subtype === 'experience') {
            const agentData = getAgentClassified(agent);
            agentData.experience += content + '\n\n';
            if (agent === currentAgent) {
                document.getElementById('showExperienceBtn').style.display = 'inline-block';
            }
            return;
        }
        accumulatedReply[agent] = (accumulatedReply[agent] || '') + content;
        // 🔧 若當前侍女但流式 DOM 遺失，重建 UI
        if (agent === currentAgent) {
            _ensureStreamUI(agent);
        }
        const replyPanel = document.getElementById('reply-content');
        if (replyPanel && agent === currentAgent) {
            replyPanel.textContent = accumulatedReply[agent];
            document.getElementById('reply-panel').scrollTop = document.getElementById('reply-panel').scrollHeight;
        }
        // 🔧 只更新與當前世代匹配的 div
        if (currentReplyDiv[agent] && _checkStreamGen(agent, currentReplyDiv[agent])) {
            let displayContent = content;
            if (subtype === 'tool_result') {
                displayContent = `📋 執行結果\n\n${content}`;
            }
            currentReplyDiv[agent].innerText += displayContent;
        }
        const assistantMsg = currentReplyDiv[agent]?.closest('.message.assistant');
        if (assistantMsg) {
            const thinkHeader = assistantMsg.querySelector('.think-container > div:first-child');
            if (thinkHeader && thinkHeader.innerText.includes('思考中')) {
                thinkHeader.innerText = ` ${agent}回答：`;
            }
        }
    } else if (data.type === 'done') {
        const reply = accumulatedReply[agent] || '';
        const think = accumulatedThink[agent] || '';
        // 🔧 捕獲 done 時的世代，防止舊 done 污染新 UI
        const doneGen = streamGen[agent] || 0;
        console.log('🏁 done 事件觸發，累積回覆長度:', reply.length);
        // === 重啟警告檢測 | idx | 202607290024 ===
        if (checkRestartWarning(reply)) {
            triggerRestartWarning(agent, reply);
        }
        if (agentStates[agent]) {
            agentStates[agent].isRunning = false;
            if (agent !== currentAgent) {
                agentStates[agent].hasNewCompleted = true;
            } else {
                agentStates[agent].hasNewCompleted = false;
            }
            renderAgentList();
        }
        // 🔧 無論 agentStates 狀態如何，done 就該關閉工作中指示器
            if (agent === currentAgent) {
                hideWorkingIndicator();
            } else {
                // 背景侍女完成，檢查當前侍女是否工作中
                const curState = agentStates[currentAgent];
                if (!curState || !curState.isRunning) {
                    hideWorkingIndicator();
                }
                // 若當前侍女仍在工作中，保持輸入框隱藏
        }
        if (agentStates[agent]) {
            agentStates[agent].lastActive = Date.now() / 1000;
        }

        if (reply || think) {
            const isPending = reply.includes('未完成的工作') && reply.includes('繼續碼');
            const isTool = reply.includes('### LLM 迭代') || reply.includes('### 工具調用');
            const isSemantic = reply.includes('相關歷史對話（語義搜索）') || reply.includes('找到以下相關對話');
            const isExperience = reply.includes('相關經驗參考');
            if (!isPending && !isTool && !isSemantic && !isExperience) {
                saveChatMessageToServer({
                    role: 'assistant',
                    content: reply,
                    thinkContent: think,
                    conv_id: data.conv_id || null,
                    timestamp: Date.now(),
                    agent: agent
                });
                // 🔧 渲染最終內容到 assistant div（含 fallback）
                let renderedContent = renderPlainTextWithFold(reply);
                const lines = reply.split('\n');
                if (lines.length > Mok_web_lines) {
                    const oldLines = lines.slice(0, -Mok_web_lines);
                    const newLines = lines.slice(-Mok_web_lines);
                    const oldContent = oldLines.join('\n');
                    const newContent = newLines.join('\n');
                    const oldHtml = renderPlainTextWithFold(oldContent);
                    const newHtml = renderPlainTextWithFold(newContent);
                    renderedContent = `<details style="margin:8px 0;" open>
                        <summary style="cursor:pointer;color:#4ec9b0;font-weight:bold;">📄 較舊內容（點擊摺疊/展開）</summary>
                        <div style="margin-top:4px;padding:8px;background:#252526;border-radius:4px;">${oldHtml}</div>
                    </details>${newHtml}`;
                }
                // 主要路徑：透過 currentReplyDiv 渲染
                let rendered = false;
                if (currentReplyDiv[agent] && _checkStreamGen(agent, currentReplyDiv[agent])) {
                    currentReplyDiv[agent].innerHTML = renderedContent;
                    rendered = true;
                }
                // Fallback：找最後一個屬於此 agent 的 assistant div
                if (!rendered) {
                    const allAssistant = chatMessagesDiv.querySelectorAll('.message.assistant');
                    for (let i = allAssistant.length - 1; i >= 0; i--) {
                        const div = allAssistant[i];
                        const meta = div.querySelector('.message-meta');
                        if (meta && meta.innerText.includes(agent)) {
                            const bubble = div.querySelector('.message-bubble');
                            if (bubble) {
                                bubble.innerHTML = renderedContent;
                                meta.innerText = `${agent} · ${new Date().toLocaleTimeString()}`;
                                rendered = true;
                            }
                            break;
                        }
                    }
                }
                // 最後 fallback：動態建立 assistant div
                if (!rendered && agent === currentAgent) {
                    const agenticon = agentIcons[agent] || '🌸';
                    const fallbackDiv = document.createElement('div');
                    fallbackDiv.className = 'message assistant';
                    fallbackDiv.innerHTML = `<div class="think-container"><div style="font-size:0.7rem; color:#e0a800;">${agenticon} ${agent}</div><div class="think-content">${escapeHtml(think)}</div></div>
                        <div class="message-bubble">${renderedContent}</div>
                        <div class="message-meta">${agent} · ${new Date().toLocaleTimeString()}</div>`;
                    chatMessagesDiv.appendChild(fallbackDiv);
                    scrollToBottom();
                }
                // 更新 meta（若 primary 路徑成功）
                if (rendered && currentAssistantDiv[agent] && _checkStreamGen(agent, currentAssistantDiv[agent])) {
                    const meta = currentAssistantDiv[agent].querySelector('.message-meta');
                    if (meta) meta.innerText = `${agent} · ${new Date().toLocaleTimeString()}`;
                }
        // 更新 conv_id（從 done 事件獲取）
        if (data.conv_id) {
            var _lastUser = chatMessagesDiv.querySelector(".message.user:last-of-type");
            var _lastAssistant = chatMessagesDiv.querySelector(".message.assistant:last-of-type");
            if (_lastUser) { _lastUser.dataset.conv_id = data.conv_id; }
            if (_lastAssistant) { _lastAssistant.dataset.conv_id = data.conv_id; }
            /* 同時更新 meta 顯示 - 直接設置完整的 conv_id */
            if (_lastAssistant) {
        var _meta = _lastAssistant.querySelector(".message-meta");
        if (_meta) {
                // 替換現有的 [ID:...] 為 [ID:conv_id]
                _meta.innerHTML = _meta.innerHTML.replace(/\[ID:[^\]]*\]/, `[ID:${data.conv_id}]`);
            }
        }
            if (_lastUser) {
                var _meta2 = _lastUser.querySelector(".message-meta");
                if (_meta2) {
                    _meta2.innerHTML = _meta2.innerHTML.replace(/[ID:?]/g, "[ID:" + data.conv_id + "]");
                    if (_meta2.innerHTML.indexOf("[ID:" + data.conv_id + "]") === -1) {
                        _meta2.innerHTML = "[ID:" + data.conv_id + "] " + _meta2.innerHTML;
                    }
                }
            }
        }

            }
        }
        // 清空流式區域並隱藏（僅當前侍女）
        const streamContainer = document.getElementById('stream-container');
        if (agent === currentAgent && streamContainer) {
            streamContainer.style.display = 'none';
            const tc = document.getElementById('think-content');
            const rc = document.getElementById('reply-content');
            if (tc) tc.textContent = '';
            if (rc) rc.textContent = '';
        }

        // 🔧 僅在世代未變時清除參考（防止清除新訊息的 UI 參考）
        if (streamGen[agent] === doneGen) {
            currentThinkDiv[agent] = null;
            currentReplyDiv[agent] = null;
            currentAssistantDiv[agent] = null;
            accumulatedReply[agent] = '';
            accumulatedThink[agent] = '';
        }
    }
}

// 註冊 Socket.IO 和 SSE 雙通道監聽
socket.on('chat_stream', _onChatStream);
window.addEventListener('chat_stream_sse', (e) => _onChatStream(e.detail));











// ---------- 接收 PM2 日誌事件 ----------
socket.on('log_line', function(data) {
    console.log('📨 收到 log_line:', data);   // 方便除錯
    if (currentTool === 'logs') {
        appendLog(data);
    }
});




    socket.on('stream_stopped', () => console.log('已停止生成'));

    function initChatInput() {
        const textarea = document.getElementById('chatInput');
        const sendBtn = document.getElementById('sendBtn');
        const stopBtn = document.getElementById('stopBtn');
        const clearBtn = document.getElementById('clearChatBtn');
        const send = () => {
            const msg = textarea.value.trim();
            if (!msg) return;
            sendUserMessage(msg);
            textarea.value = '';
            textarea.style.height = 'auto';
        };
        sendBtn.onclick = send;
        textarea.onkeydown = (e) => {
            if (e.key === 'Enter' && e.shiftKey) {
                e.preventDefault();
                send();
            }
            // 普通 Enter 不攔截，瀏覽器會默認換行
        };
        textarea.oninput = function() { const prevH = this.clientHeight; this.style.height = 'auto'; this.style.height = Math.max(prevH, this.scrollHeight, 44) + 'px'; };
        stopBtn.onclick = stopGeneration;
        clearBtn.onclick = clearAllChats;
        chatMessagesDiv = document.getElementById('chatMessages');
        initScrollButton();

        // ==== 新增滾動控制 ====
        chatMessagesDiv.addEventListener('scroll', function() {
            const threshold = 50;
            const isAtBottom = chatMessagesDiv.scrollHeight - chatMessagesDiv.scrollTop - chatMessagesDiv.clientHeight < threshold;
            autoScrollEnabled = isAtBottom;
        });

        // 點擊滾動按鈕強制滾動到底部並啟用自動滾動
        // 點擊滾動按鈕強制滾動到底部並啟用自動滾動
        document.getElementById('scrollToBottomBtn').addEventListener('click', function() {
            chatMessagesDiv.scrollTo({ top: chatMessagesDiv.scrollHeight, behavior: 'smooth' });
            autoScrollEnabled = true;
        });

        // 對話歷史按鈕（確保在 chatMessagesDiv 初始化後綁定）
        document.getElementById('jumpBtn')?.addEventListener('click', (e) => {
            e.stopPropagation();
            toggleJumpDropdown();
        });
    

        // ===== 新增：滾動到頂部加載更多歷史 =====
        chatMessagesDiv.addEventListener('scroll', function() {
            if (chatMessagesDiv.scrollTop === 0 && chatHistoryHasMore && !loadingMore) {
                loadMoreChatHistory();
            }
        });
    }








    function copyMsgHandler(e) {
        const btn = e.currentTarget;
        const text = btn.getAttribute('data-msg');
        if (text) {
            navigator.clipboard.writeText(text).then(() => {
                const originalText = btn.innerText;
                btn.innerText = '✓';
                setTimeout(() => { btn.innerText = originalText; }, 1000);
            }).catch(() => alert('複製失敗'));
        }
        e.stopPropagation();
    }

    // 引用對話 ID 按鈕：複製 <引用對話: [ID:xxx> 到剪貼簿
    function quoteIdClickHandler(e) {
        const btn = e.currentTarget;
        const convId = btn.getAttribute('data-conv-id') || '?';
        const text = '<引用對話: [ID:' + convId + ']>';
        navigator.clipboard.writeText(text).then(() => {
            showQuoteToast('✅ 已複製: ' + text);
        }).catch(() => alert('複製失敗'));
        e.stopPropagation();
    }







    // 建新 agent 的按鈕事件 → 已整合至 bindCreateAgentEvents()，此處不再重複綁定














// ---------- 日誌工具 ----------
let logSubscribed = false;
let logLinesCount = 0;
const MAX_LOG_LINES = 500;

function subscribeLogs() {
    if (!logSubscribed) {
        socket.emit('subscribe_logs');
        logSubscribed = true;
    }
}

function unsubscribeLogs() {
    if (logSubscribed) {
        socket.emit('unsubscribe_logs');
        logSubscribed = false;
    }
}


// ========== 🔍 全域對話搜尋 ==========
function renderSearchContent() {
    const container = document.getElementById("toolsContent");
    container.innerHTML = '' +
        '<div style="height:100%; display:flex; flex-direction:column;">' +
            '<div style="padding:8px; flex-shrink:0; border-bottom:1px solid #3e3e42;">' +
                '<div style="display:flex; gap:6px;">' +
                    '<input id="searchQueryInput" type="text" placeholder="輸入關鍵詞搜尋全主機對話..." ' +
                        'style="flex:1; background:#2d2d30; color:#d4d4d4; border:1px solid #3e3e42; border-radius:6px; padding:6px 10px; font-size:0.85rem; outline:none;" ' +
                        'autofocus>' +
                    '<button id="searchDoBtn" style="background:#4ec9b0; color:#1e1e1e; border:none; border-radius:6px; padding:6px 14px; cursor:pointer; font-weight:bold;">搜尋</button>' +
                '</div>' +
                '<div style="margin-top:4px; font-size:0.7rem; color:#888;">' +
                    '搜尋範圍：chat_history + conversation_history（全主機所有 agent 及使用者對話）' +
                '</div>' +
            '</div>' +
            '<div id="searchResults" style="flex:1; overflow-y:auto; padding:8px;">' +
                '<div style="color:#888; text-align:center; margin-top:40px;">🔍 請輸入關鍵詞開始搜尋</div>' +
            '</div>' +
        '</div>';

    function doSearch() {
        const q = document.getElementById("searchQueryInput").value.trim();
        const resultsDiv = document.getElementById("searchResults");
        if (!q) {
            resultsDiv.innerHTML = '<div style="color:#e0a800; text-align:center; margin-top:40px;">請輸入關鍵詞</div>';
            return;
        }
        resultsDiv.innerHTML = '<div style="color:#888; text-align:center; margin-top:40px;">⏳ 搜尋中...</div>';

        fetch('/api/search_all_conversations?q=' + encodeURIComponent(q) + '&limit=50')
            .then(res => res.json())
            .then(data => {
                if (data.error) {
                    resultsDiv.innerHTML = '<div style="color:#e0a800; text-align:center; margin-top:40px;">⚠️ ' + data.error + '</div>';
                    return;
                }
                if (!data.results || data.results.length === 0) {
                    resultsDiv.innerHTML = '<div style="color:#888; text-align:center; margin-top:40px;">😕 沒有找到相關對話</div>';
                    return;
                }
                let html = '<div style="margin-bottom:8px; font-size:0.8rem; color:#4ec9b0;">📊 找到 <b>' + data.total + '</b> 筆結果（關鍵詞：「' + data.query + '」）</div>';
                data.results.forEach(function(r) {
                    var ts = r.timestamp ? new Date(r.timestamp * 1000).toLocaleString("zh-TW") : "未知時間";
                    var roleIcon = r.role === "user" ? "👤" : r.role === "assistant" ? "🤖" : "💬";
                    var sourceLabel = r.source === "chat_history" ? "💬Web" : "📝完整";
                    var agentLabel = r.agent ? " @" + r.agent : "";
                    html += '<div style="margin-bottom:10px; padding:8px; background:#252526; border-radius:8px; border-left:3px solid #4ec9b0;">' +
                        '<div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:4px;">' +
                        '<span style="font-size:0.75rem; color:#4ec9b0;">' + roleIcon + sourceLabel + agentLabel + '</span>' +
                        '<span style="font-size:0.7rem; color:#888;">' + ts + '</span></div>' +
                        '<div style="font-size:0.8rem; color:#d4d4d4; white-space:pre-wrap; word-break:break-word; line-height:1.5;">' + escapeHtml(r.snippet) + '</div></div>';
                });
                resultsDiv.innerHTML = html;
            })
            .catch(function(err) {
                resultsDiv.innerHTML = '<div style="color:#f44747; text-align:center; margin-top:40px;">❌ 搜尋失敗：' + err.message + '</div>';
            });
    }

    document.getElementById("searchDoBtn").addEventListener("click", doSearch);
    document.getElementById("searchQueryInput").addEventListener("keydown", function(e) {
        if (e.key === "Enter") doSearch();
    });
}


function renderLogsContent() {
    const container = document.getElementById('toolsContent');
    container.innerHTML = `
        <div style="height:100%; display:flex; flex-direction:column;">
            <div style="flex:1; overflow-y:auto; background:#1e1e1e; padding:8px; font-family: monospace; white-space: pre-wrap; font-size:0.8rem;" id="logContainer">
                <div style="color:#888;">連線至 PM2 日誌中...</div>
            </div>
            <div style="padding:4px; display:flex; gap:8px; flex-shrink:0;">
                <button id="clearLogsBtn">🗑️ 清空</button>
                <button id="refreshLogsBtn">🔄 重新整理</button>
            </div>
        </div>
    `;
    // 訂閱日誌
    subscribeLogs();
    // 手動發送測試訊息（僅用於診斷）
    setTimeout(() => {
        socket.emit('test_log');  // 可選
    }, 500);

    // 清空按鈕
    document.getElementById('clearLogsBtn')?.addEventListener('click', function() {
        const logContainer = document.getElementById('logContainer');
        if (logContainer) {
            logContainer.innerHTML = '';
            logLinesCount = 0;
        }
    });

    // 重新整理按鈕：取消訂閱再重新訂閱，以重新取得最近歷史
    document.getElementById('refreshLogsBtn')?.addEventListener('click', function() {
        const logContainer = document.getElementById('logContainer');
        if (logContainer) {
            logContainer.innerHTML = '<div style="color:#888;">重新整理中...</div>';
            logLinesCount = 0;
        }
        unsubscribeLogs();
        setTimeout(() => subscribeLogs(), 100);
    });
}

function appendLog(data) {
    const container = document.getElementById('logContainer');
    if (!container) return;
    const line = data.message || '';
    if (!line) return;
    const div = document.createElement('div');
    div.textContent = line;
    // 根據類型上色（可選）
    if (data.type === 'err') div.style.color = '#ff6b6b';
    else if (data.type === 'info') div.style.color = '#69db7c';
    else div.style.color = '#d4d4d4';
    container.appendChild(div);
    // 限制總行數
    while (container.children.length > 500) container.removeChild(container.firstChild);
    container.scrollTop = container.scrollHeight;
}

// Socket 接收日誌事件
function subscribeLogs() {
    if (!logSubscribed) {
        console.log('📡 訂閱 PM2 日誌...');
        socket.emit('subscribe_logs');
        logSubscribed = true;
    }
}













// ========== 🌸 Agent 資訊面板 ==========
function renderAgentInfo() {
    const container = document.getElementById("toolsContent");
    const agentName = currentAgent || "未知";
    const icon = currentAgent ? (agentIcons[currentAgent] || "🌸") : "🌸";
    container.innerHTML = `<div style="height:100%; display:flex; flex-direction:column;">
        <div style="display:flex; gap:4px; padding:6px 8px; border-bottom:1px solid #3e3e42; flex-shrink:0; background:#1e1e1e;">
            <button class="agent-tab-btn active" data-tab="soul">📝 Soul</button>
            <button class="agent-tab-btn" data-tab="jobs">📋 Jobs</button>
            <button class="agent-tab-btn" data-tab="logs">📜 Logs</button>
            <button class="agent-tab-btn" data-tab="settings">⚙️ 設定</button>
        </div>
        <div id="agentTabContent" style="flex:1; overflow-y:auto; padding:8px; font-family:monospace; font-size:0.8rem; white-space:pre-wrap; background:#1e1e1e;">
            <div style="color:#888;">載入中...</div>
        </div>
    </div>`;
    let activeTab = "soul";
    const tabContent = document.getElementById("agentTabContent");
    const loadTab = (tab) => {
        activeTab = tab;
        container.querySelectorAll(".agent-tab-btn").forEach(b => b.classList.remove("active"));
        container.querySelector(`[data-tab="${tab}"]`)?.classList.add("active");
        tabContent.innerHTML = `<div style="color:#888;">載入中...</div>`;
        if (tab === "soul") loadAgentSoul(tabContent, agentName);
        else if (tab === "jobs") loadAgentJobs(tabContent, agentName);
        else if (tab === "logs") loadAgentLogs(tabContent, agentName);
        else if (tab === "settings") loadAgentSettings(tabContent, agentName);
    };
    container.querySelectorAll(".agent-tab-btn").forEach(btn => {
        btn.addEventListener("click", () => loadTab(btn.dataset.tab));
    });
    loadTab("soul");
}

function loadAgentSoul(container, agentName) {
    socket.emit("get_agent_soul", { agent: agentName });
    const handler = (data) => {
        socket.off("agent_soul_result", handler);
        if (data.error) container.innerHTML = `<div style="color:#ff6b6b;">❌ ${data.error}</div>`;
        else container.innerHTML = data.content || "<div style=\"color:#888;\">(無內容)</div>";
    };
    socket.on("agent_soul_result", handler);
    setTimeout(() => { socket.off("agent_soul_result", handler); if (container.innerHTML.includes("載入中")) container.innerHTML = "<div style=\"color:#e0a800;\">⚠️ 載入逾時</div>"; }, 8000);
}

function loadAgentJobs(container, agentName) {
    socket.emit("get_agent_jobs", { agent: agentName });
    const handler = (data) => {
        socket.off("agent_jobs_result", handler);
        if (data.error) container.innerHTML = `<div style="color:#ff6b6b;">❌ ${data.error}</div>`;
        else container.innerHTML = data.content || "<div style=\"color:#888;\">(無工作)</div>";
    };
    socket.on("agent_jobs_result", handler);
    setTimeout(() => { socket.off("agent_jobs_result", handler); if (container.innerHTML.includes("載入中")) container.innerHTML = "<div style=\"color:#e0a800;\">⚠️ 載入逾時</div>"; }, 8000);
}


function loadAgentLogs(container, agentName) {
    socket.emit("get_agent_logs", { agent: agentName });
    const handler = (data) => {
        socket.off("agent_logs_result", handler);
        if (data.error) container.innerHTML = `<div style="color:#ff6b6b;">❌ ${data.error}</div>`;
        else container.innerHTML = data.content || "<div style=\"color:#888;\">(無日誌)</div>";
    };
    socket.on("agent_logs_result", handler);
    setTimeout(() => { socket.off("agent_logs_result", handler); if (container.innerHTML.includes("載入中")) container.innerHTML = "<div style=\"color:#e0a800;\">⚠️ 載入逾時</div>"; }, 8000);
}

function loadAgentSettings(container, agentName) {
    socket.emit("get_agent_settings", { agent: agentName });
    const handler = (data) => {
        socket.off("agent_settings_result", handler);
        if (data.error) { container.innerHTML = `<div style="color:#ff6b6b;">❌ ${data.error}</div>`; return; }
        const raw = data.raw || '';
        if (!raw) { container.innerHTML = "<div style=\"color:#888;\">(無設定)</div>"; return; }
        container.dataset.rawContent = raw;
        const lines = raw.split('\n');
        const entries = [];
        let commentBuffer = [];
        for (const line of lines) {
            const trimmed = line.trim();
            if (trimmed.startsWith('#')) { commentBuffer.push(trimmed); }
            else if (trimmed.includes('=')) {
                const eqIdx = trimmed.indexOf('=');
                const key = trimmed.substring(0, eqIdx).trim();
                const value = trimmed.substring(eqIdx + 1).trim();
                entries.push({ type: 'keyvalue', key, value, comments: commentBuffer.slice() });
                commentBuffer = [];
            } else if (trimmed === '') { commentBuffer.push(''); }
        }
        if (entries.length === 0) {
            container.innerHTML = `<pre style="white-space:pre-wrap; background:#1e1e1e; padding:12px; border-radius:8px;">${escapeHtml(raw)}</pre>`;
            return;
        }
        let html = `<div style="padding:8px;"><h3 style="color:#4ec9b0; margin-bottom:8px;">${agentName} · 屬性</h3><div style="display:flex; flex-direction:column; gap:6px;">`;
        for (const entry of entries) {
            for (const comment of entry.comments) {
                if (comment.trim() === '') html += `<div style="color:#888; font-size:0.75rem; padding-left:8px; height:1em;">&nbsp;</div>`;
                else html += `<div style="color:#888; font-size:0.75rem; padding-left:8px;">${escapeHtml(comment)}</div>`;
            }
            const key = entry.key, value = entry.value;
            let inputHtml;
            if (value === 'true' || value === 'false') {
                inputHtml = `<input type="checkbox" ${value === 'true' ? 'checked' : ''} data-key="${key}" style="width:18px; height:18px; accent-color:#4ec9b0;" />`;
            } else {
                inputHtml = `<input type="text" value="${escapeHtml(value)}" data-key="${key}" style="flex:1; background:#1e1e1e; border:1px solid #4ec9b0; border-radius:4px; padding:3px 6px; color:#d4d4d4; font-family:monospace; font-size:0.75rem;" />`;
            }
            html += `<div style="display:flex; align-items:center; gap:8px; border-bottom:1px solid #3e3e42; padding-bottom:3px;"><label style="width:140px; flex-shrink:0; color:#d4d4d4; font-weight:bold; font-size:0.8rem;">${escapeHtml(key)}</label>${inputHtml}</div>`;
        }
        html += `</div><div style="display:flex; align-items:center; gap:8px; margin-top:12px;"><button id="saveAgentTabSettingsBtn" style="background:#4ec9b0; border:none; border-radius:16px; padding:5px 16px; font-weight:bold; cursor:pointer; font-size:0.8rem;">💾 儲存設定</button><span id="saveAgentTabStatus" style="font-size:0.75rem; color:#888;"></span></div></div>`;
        container.innerHTML = html;
        document.getElementById('saveAgentTabSettingsBtn').addEventListener('click', () => { saveAgentTabSettings(agentName); });
    };
    socket.on("agent_settings_result", handler);
    setTimeout(() => { socket.off("agent_settings_result", handler); if (container.innerHTML.includes("載入中")) container.innerHTML = "<div style=\"color:#e0a800;\">⚠️ 載入逾時</div>"; }, 8000);
}

function saveAgentTabSettings(agentName) {
    const container = document.getElementById('agentTabContent');
    const rawContent = container.dataset.rawContent || '';
    const inputs = document.querySelectorAll('#agentTabContent input[data-key]');
    const changedValues = {};
    inputs.forEach(input => {
        const key = input.dataset.key;
        let val = input.type === 'checkbox' ? (input.checked ? 'true' : 'false') : input.value.trim();
        changedValues[key] = val;
    });
    let newContent;
    if (rawContent) {
        const rawLines = rawContent.split('\n');
        const resultLines = rawLines.map(line => {
            const trimmed = line.trim();
            if (trimmed && !trimmed.startsWith('#') && trimmed.includes('=')) {
                const eqIdx = trimmed.indexOf('=');
                const key = trimmed.substring(0, eqIdx).trim();
                if (key in changedValues) {
                    const leadingWs = line.match(/^(\s*)/)[0];
                    return leadingWs + key + '=' + changedValues[key];
                }
            }
            return line;
        });
        newContent = resultLines.join('\n');
    } else {
        const lines = [];
        inputs.forEach(input => {
            const key = input.dataset.key;
            let val = input.type === 'checkbox' ? (input.checked ? 'true' : 'false') : input.value.trim();
            lines.push(key + '=' + val);
        });
        newContent = lines.join('\n') + '\n';
    }
    const statusEl = document.getElementById('saveAgentTabStatus');
    if (statusEl) { statusEl.textContent = '儲存中...'; statusEl.style.color = '#e0a800'; }
    socket.emit("save_agent_settings", { agent: agentName, content: newContent });
    const saveHandler = (data) => {
        socket.off("agent_settings_saved", saveHandler);
        if (statusEl) {
            if (data.error) { statusEl.textContent = '❌ ' + data.error; statusEl.style.color = '#ff6b6b'; }
            else { statusEl.textContent = '✅ 已儲存'; statusEl.style.color = '#4ec9b0'; container.dataset.rawContent = newContent; setTimeout(() => { statusEl.textContent = ''; }, 3000); }
        }
    };
    socket.on("agent_settings_saved", saveHandler);
    setTimeout(() => { socket.off("agent_settings_saved", saveHandler); if (statusEl) { statusEl.textContent = '⚠️ 逾時'; statusEl.style.color = '#e0a800'; } }, 8000);
}
    async function init() {
        await loadAgentList();
        await loadModels();
        initResize();
        initSidebars();
        initChatInput();
        initInputResize();
        document.querySelectorAll('#toolsPanel .tools-header-buttons button').forEach(btn => {
            btn.addEventListener('click', () => switchTool(btn.dataset.tool));
        });
        switchTool('files');
        // agent 資訊按鈕
        document.getElementById('agentInfoBtn')?.addEventListener('click', () => switchTool('agent'));
        // 🔧 修復：activateAgent() 已透過 loadAgentList() 載入並渲染歷史，此處不再重複加載，避免覆蓋

document.getElementById('loadCodeBtn')?.addEventListener('click', loadCodeLibrary);
document.getElementById('attachFileBtn')?.addEventListener('click', () => {
    const input = document.createElement('input');
    input.type = 'file';
    input.multiple = true;
    input.accept = '*/*';
    input.onchange = (e) => {
        if (e.target.files) handleFiles(Array.from(e.target.files));
    };
    input.click();
});

        // ===== 綁定 Agent 建立器（模態對話框 + 角色選擇） =====
        bindCreateAgentEvents();
        // ===== 結束 =====

        // ---------- 拖放檔案 ----------
        const chatInputArea = document.querySelector('.chat-input-area');

        function preventDragDefaults(e) {
            e.preventDefault();
            e.stopPropagation();
        }

        if (chatInputArea) {
            ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(eventName => {
                chatInputArea.addEventListener(eventName, preventDragDefaults, false);
                document.body.addEventListener(eventName, preventDragDefaults, false);
            });

            // 視覺反饋（高亮）
            chatInputArea.addEventListener('dragenter', (e) => {
                if (e.dataTransfer.types.includes('Files')) {
                    chatInputArea.classList.add('drag-over');
                }
            });
            chatInputArea.addEventListener('dragover', (e) => {
                // 必須，否則 drop 不觸發
            });
            chatInputArea.addEventListener('dragleave', (e) => {
                if (!chatInputArea.contains(e.relatedTarget)) {
                    chatInputArea.classList.remove('drag-over');
                }
            });
            chatInputArea.addEventListener('drop', (e) => {
                chatInputArea.classList.remove('drag-over');
                const files = e.dataTransfer.files;
                if (files.length > 0) {
                    handleFiles(Array.from(files));
                    document.getElementById('chatInput')?.focus();
                }
            });
        } else {
            console.warn('聊天輸入區域 (.chat-input-area) 未找到，跳過拖放事件綁定');
        }



// ---- 展開按鈕事件（安全性強化） ----
document.addEventListener('click', function(e) {
    if (e.target && e.target.id === 'expand-stream-btn') {
        const thinkEl = document.getElementById('think-content');
        const replyEl = document.getElementById('reply-content');
        const thinkText = thinkEl ? thinkEl.textContent : '';
        const replyText = replyEl ? replyEl.textContent : '';
        if (!thinkText && !replyText) {
            alert('目前沒有流式內容可查看');
            return;
        }
        const modal = document.createElement('div');
        modal.className = 'expand-modal';
        modal.innerHTML = `
            <div class="modal-box">
                <div class="modal-header">
                    <h3>📄 完整內容</h3>
                    <button class="modal-close">&times;</button>
                </div>
                <div class="modal-body">
                    <div class="panel">
                        <div class="label think">💭 思考</div>
                        <div class="content">${escapeHtml(thinkText) || '（無思考內容）'}</div>
                    </div>
                    <div class="panel">
                        <div class="label reply">💬 回應</div>
                        <div class="content">${escapeHtml(replyText) || '（無回應內容）'}</div>
                    </div>
                </div>
            </div>
        `;
        document.body.appendChild(modal);
        modal.querySelector('.modal-close').addEventListener('click', () => modal.remove());
        modal.addEventListener('click', (ev) => { if (ev.target === modal) modal.remove(); });
    }
});



        // ---------- 手機版浮動圓點功能 ----------
        // ---------- 手機版浮動圓點功能 ----------
        const btnBox = document.querySelector('.btnBox');
        const toggleBtn = document.getElementById('menuToggle');
        let isExpanded = false;

        if (btnBox && toggleBtn) {
            // 點擊切換展開／收合（不依賴窗口寬度，點擊時判斷）
                toggleBtn.addEventListener('click', (e) => {
                    e.stopPropagation();
                    // 僅在手機版（寬度≤768px）生效
                    if (window.innerWidth > 768) return;
                    isExpanded = !isExpanded;
                    btnBox.classList.toggle('expanded', isExpanded);
                    if (isExpanded) {
                        // 展開時清除內聯定位，讓 CSS 的 left:16px; right:16px; bottom:80px 生效
                        btnBox.style.left = '';
                        btnBox.style.top = '';
                        btnBox.style.right = '';
                        btnBox.style.bottom = '';
                        btnBox.style.touchAction = 'auto';
                    } else {
                        btnBox.style.touchAction = 'none';
                        btnBox.style.position = '';
                    }
                });
        }

        // 手機版特有功能：點擊其他按鈕後自動收合 + 拖拽
        if (window.innerWidth <= 768) {
            if (btnBox) {
                // 點擊按鈕後自動收合（可選）
                btnBox.querySelectorAll('button:not(.mobile-menu-toggle)').forEach(btn => {
                    btn.addEventListener('click', () => {
                        if (isExpanded) {
                            isExpanded = false;
                            btnBox.classList.remove('expanded');
                            btnBox.style.touchAction = 'none';
                        btnBox.style.position = '';
                        }
                    });
                });

            }
        }










    // 展開按鈕事件
    const expandBtn = document.getElementById('expand-stream-btn');
    if (expandBtn) {
        expandBtn.addEventListener('click', function() {
            const thinkText = document.getElementById('think-content').textContent;
            const replyText = document.getElementById('reply-content').textContent;
            if (!thinkText && !replyText) {
                alert('目前沒有流式內容可查看');
                return;
            }
            const modal = document.createElement('div');
            modal.className = 'expand-modal';
            modal.innerHTML = `
                <div class="modal-box">
                    <div class="modal-header">
                        <h3>📄 完整內容</h3>
                        <button class="modal-close">&times;</button>
                    </div>
                    <div class="modal-body">
                        <div class="panel">
                            <div class="label think">💭 思考</div>
                            <div class="content">${escapeHtml(thinkText) || '（無思考內容）'}</div>
                        </div>
                        <div class="panel">
                            <div class="label reply">💬 回應</div>
                            <div class="content">${escapeHtml(replyText) || '（無回應內容）'}</div>
                        </div>
                    </div>
                </div>
            `;
            document.body.appendChild(modal);
            modal.querySelector('.modal-close').addEventListener('click', () => modal.remove());
            modal.addEventListener('click', (e) => { if (e.target === modal) modal.remove(); });
        });
    }





































        // ===== 事件委派：Agent 列表 =====
        const agentListContainer = document.getElementById('agentList');
        if (agentListContainer) {
            agentListContainer.addEventListener('click', (e) => {
                const item = e.target.closest('.agent-item');
                if (item && item.dataset.agent) {
                    const name = item.dataset.agent;
                    if (currentAgent !== name) {
                        activateAgent(name);
                    }
                }
            });
        } else {
            console.warn('agentList 元素未找到，請檢查 HTML 結構');
        }








        // ===== 事件委派：複製按鈕 =====
        const messageList = document.getElementById('message-list');
        if (messageList) {
            messageList.addEventListener('click', (e) => {
                const btn = e.target.closest('.copy-msg-btn');
                if (btn) {
                    const text = btn.getAttribute('data-msg');
                    if (text) {
                        navigator.clipboard.writeText(text).then(() => {
                            const originalText = btn.innerText;
                            btn.innerText = '✓';
                            setTimeout(() => { btn.innerText = originalText; }, 1000);
                        }).catch(() => alert('複製失敗'));
                    }
                    e.stopPropagation();
                }
            });
        }




    }


// 確保 init 在 DOM 就緒後執行
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
} else {
    init();
}



















































































// ===== 新增：顯示工具過程 =====
function showToolProcessList() {
    const old = document.getElementById('toolProcessModal');
    if (old) old.remove();
    const data = getAgentClassified(currentAgent);
    if (!data.toolProcess) {
        alert('當前沒有工具執行過程記錄。');
        return;
    }
    showModal('🔧 工具執行過程', data.toolProcess, 'toolProcessModal');
}

function showSemanticList() {
    const old = document.getElementById('semanticModal');
    if (old) old.remove();
    const data = getAgentClassified(currentAgent);
    if (!data.semanticSearch) {
        alert('當前沒有語義搜索記錄。');
        return;
    }
    showModal('🔍 語義搜索結果', data.semanticSearch, 'semanticModal');
}

function showExperienceList() {
    const old = document.getElementById('experienceModal');
    if (old) old.remove();
    const data = getAgentClassified(currentAgent);
    if (!data.experience) {
        alert('當前沒有經驗參考記錄。');
        return;
    }
    showModal('📚 經驗參考', data.experience, 'experienceModal');
}

function showPendingList() {
    const old = document.getElementById('pendingModal');
    if (old) old.remove();
    if (!window.pendingWorkList) {
        alert('當前沒有待處理工作。');
        return;
    }
    showModal('📋 待處理工作', window.pendingWorkList, 'pendingModal');
}
















































































// ===== 通用彈窗函數 =====
function showModal(title, content, modalId) {
    const modal = document.createElement('div');
    modal.id = modalId;
    modal.style.cssText = `
        position: fixed; top: 0; left: 0; right: 0; bottom: 0;
        background: rgba(0,0,0,0.7);
        display: flex; justify-content: center; align-items: center;
        z-index: 10000;
        backdrop-filter: blur(4px);
        animation: fadeIn 0.2s ease;
    `;
    modal.onclick = function(e) { if (e.target === modal) this.remove(); };

    const contentDiv = document.createElement('div');
    contentDiv.style.cssText = `
        background: #2d2d30;
        border-radius: 12px;
        padding: 20px;
        max-width: 90%;
        max-height: 80vh;
        overflow-y: auto;
        box-shadow: 0 8px 32px rgba(0,0,0,0.8);
        border: 1px solid #4ec9b0;
        position: relative;
        width: 700px;
        user-select: text;
    `;

    const headerDiv = document.createElement('div');
    headerDiv.style.cssText = 'display:flex; justify-content:space-between; align-items:center; margin-bottom:12px;';
    const titleSpan = document.createElement('span');
    titleSpan.textContent = title;
    titleSpan.style.cssText = 'color:#4ec9b0; font-weight:bold; font-size:1.2rem;';
    const closeBtn = document.createElement('button');
    closeBtn.textContent = '✕';
    closeBtn.style.cssText = 'background:none; border:none; color:#ccc; font-size:1.5rem; cursor:pointer;';
    closeBtn.onclick = function() { modal.remove(); };
    headerDiv.appendChild(titleSpan);
    headerDiv.appendChild(closeBtn);
    contentDiv.appendChild(headerDiv);

    const msgDiv = document.createElement('div');
    msgDiv.style.cssText = 'max-height: 60vh; overflow-y: auto; user-select: text;';
    const pre = document.createElement('pre');
    pre.style.cssText = 'margin: 0; padding: 0; white-space: pre-wrap; word-break: break-word; font-family: inherit; font-size: 0.9rem; color: #d4d4d4;';
    pre.textContent = content;
    msgDiv.appendChild(pre);
    contentDiv.appendChild(msgDiv);

    modal.appendChild(contentDiv);
    document.body.appendChild(modal);
}





// ============================================================
// 🏗️ Agent 建立器 — 模態對話框 + 角色選擇（整合自 agent_creator.js）
// ============================================================

let allRoles = [];
let selectedRoleUrl = '';

function showCreateAgentModal() {
    const modal = document.getElementById('createAgentModal');
    if (!modal) return;
    modal.style.display = 'flex';
    document.getElementById('newAgentName').value = '';
    document.getElementById('roleSearchInput').value = '';
    document.getElementById('roleSelect').value = '';
    document.getElementById('roleInfo').style.display = 'none';
    selectedRoleUrl = '';
    document.getElementById('newAgentName').focus();
    if (allRoles.length === 0) {
        loadAgencyRoles();
    }
}

function hideCreateAgentModal() {
    const modal = document.getElementById('createAgentModal');
    if (modal) modal.style.display = 'none';
}

async function loadAgencyRoles() {
    const loading = document.getElementById('roleLoading');
    const select = document.getElementById('roleSelect');
    if (loading) loading.style.display = 'block';
    try {
        const res = await fetch('/api/agency_roles');
        const data = await res.json();
        if (data.status === 'ok' && data.roles) {
            allRoles = data.roles;
            renderRoleOptions(allRoles);
        }
    } catch (e) {
        console.error('載入角色失敗:', e);
        if (select) {
            select.innerHTML = '<option value="">-- 載入失敗，仍可建立空白 Agent --</option>';
        }
    } finally {
        if (loading) loading.style.display = 'none';
    }
}

function renderRoleOptions(roles) {
    const select = document.getElementById('roleSelect');
    if (!select) return;
    select.innerHTML = '<option value="">-- 不選角色（建立空白 agent.md）--</option>';
    roles.forEach(role => {
        const opt = document.createElement('option');
        opt.value = role.raw_url || '';
        opt.textContent = `${role.emoji || '📋'} ${role.name} — ${role.specialty}`;
        opt.dataset.name = role.name;
        opt.dataset.specialty = role.specialty;
        opt.dataset.division = role.division || '';
        select.appendChild(opt);
    });
    select.onchange = function() {
        const sel = this.options[this.selectedIndex];
        const info = document.getElementById('roleInfo');
        if (sel && sel.value) {
            selectedRoleUrl = sel.value;
            if (info) {
                info.style.display = 'block';
                info.innerHTML = `<strong>${sel.dataset.name}</strong><br><small>${sel.dataset.specialty}</small><br><small>📂 ${sel.dataset.division}</small>`;
            }
        } else {
            selectedRoleUrl = '';
            if (info) info.style.display = 'none';
        }
    };
}

async function doCreateAgent() {
    const nameInput = document.getElementById('newAgentName');
    const name = nameInput ? nameInput.value.trim() : '';
    if (!name) { alert('請輸入 Agent 名字'); return; }
    if (!/^[a-zA-Z0-9\u4e00-\u9fa5_-]+$/.test(name)) {
        alert('名字只能包含字母、數字、中文、下劃線和中劃線');
        return;
    }
    const confirmBtn = document.getElementById('createAgentConfirm');
    if (confirmBtn) { confirmBtn.disabled = true; confirmBtn.textContent = '⏳ 建立中...'; }

    try {
        const body = { name: name };
        if (selectedRoleUrl) {
            body.role_url = selectedRoleUrl;
        }
        const res = await fetch('/api/create_agent', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        const data = await res.json();
        if (data.status === 'ok') {
            const extraMsg = data.agent_md_created ? '\n✅ 角色模板已寫入 soul/agent.md' : '\n📝 已建立空白 soul/agent.md';
            alert(data.message + extraMsg);
            hideCreateAgentModal();
            location.reload();
        } else {
            alert('❌ ' + data.message);
        }
    } catch (e) {
        console.error('建立 Agent 失敗:', e);
        alert('❌ 建立失敗: ' + e.message);
    }
    if (confirmBtn) { confirmBtn.disabled = false; confirmBtn.textContent = '✨ 建立 Agent'; }
}

function bindCreateAgentEvents() {
    // 綁定所有 createQiBtn（可能有多個）
    document.querySelectorAll('#createQiBtn').forEach(createBtn => {
        const newBtn = createBtn.cloneNode(true);
        createBtn.parentNode.replaceChild(newBtn, createBtn);
        newBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            showCreateAgentModal();
        });
    });

    document.getElementById('createAgentClose')?.addEventListener('click', hideCreateAgentModal);
    document.getElementById('createAgentCancel')?.addEventListener('click', hideCreateAgentModal);
    document.getElementById('createAgentOverlay')?.addEventListener('click', hideCreateAgentModal);
    document.getElementById('createAgentConfirm')?.addEventListener('click', doCreateAgent);
    document.getElementById('newAgentName')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') { e.preventDefault(); doCreateAgent(); }
    });
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            const modal = document.getElementById('createAgentModal');
            if (modal && modal.style.display === 'flex') hideCreateAgentModal();
        }
    });

    // 搜尋過濾
    const searchInput = document.getElementById('roleSearchInput');
    if (searchInput) {
        searchInput.addEventListener('input', function() {
            const query = this.value.toLowerCase().trim();
            if (!query) { renderRoleOptions(allRoles); return; }
            const filtered = allRoles.filter(r =>
                r.name.toLowerCase().includes(query) ||
                r.specialty.toLowerCase().includes(query) ||
                (r.division && r.division.toLowerCase().includes(query))
            );
            renderRoleOptions(filtered);
        });
    }
    
    console.log('✅ Agent 建立器已綁定（模態對話框模式）');
}

// ============================================================

// 綁定按鈕點擊事件
document.addEventListener('DOMContentLoaded', function() {
    const btn = document.getElementById('showPendingBtn');
    if (btn) {
        btn.addEventListener('click', showPendingList);
    }

// 綁定分類按鈕
document.getElementById('showToolProcessBtn')?.addEventListener('click', showToolProcessList);
document.getElementById('showSemanticBtn')?.addEventListener('click', showSemanticList);
document.getElementById('showExperienceBtn')?.addEventListener('click', showExperienceList);
document.getElementById('desktopTopBtn')?.addEventListener('click', () => { window.open('/webTools/novnc/index.html', '_blank'); });


});
// 由於頁面加載時可能 init 已執行，但按鈕可能還未添加，所以使用 DOMContentLoaded 確保。
// 但 init 在腳本中直接執行，所以需在 init 之後執行此綁定，但可用 setTimeout 保證。
setTimeout(function() {
    const btn = document.getElementById('showPendingBtn');
    if (btn) btn.addEventListener('click', showPendingList);
}, 100);

// ===== 第二組浮動工具面板 =====
let currentTool2 = "files";
let panel2Active = false;

function renderForPanel2(fn) {
    const orig = document.getElementById;
    document.getElementById = function(id) {
        if (id === "toolsContent") return orig.call(document, "toolsContent2");
        if (id === "toolsContent2") return orig.call(document, "toolsContent2");
        return orig.call(document, id);
    };
    try { fn(); } finally { document.getElementById = orig; }
}

function switchTool2(tool) {
    currentTool2 = tool;
    const header = document.querySelector("#toolsPanel2 .tools-header-buttons");
    if (header) {
        header.querySelectorAll("button[data-tool]").forEach(function(b) {
            b.classList.toggle("active", b.dataset.tool === tool);
        });
    }
    if (tool === "files") renderForPanel2(renderFilesContent);
    else if (tool === "ascii") renderForPanel2(renderAsciiContent);
    else if (tool === "settings") renderForPanel2(renderSettingsContent);
    else if (tool === "monitor") renderForPanel2(renderMonitorContent);
    else if (tool === "game") renderForPanel2(rendergame);
    else if (tool === "tokenstats") renderForPanel2(renderTokenStats);
    else if (tool === "tools") renderForPanel2(renderToolsContent);
    else if (tool === "logs") renderForPanel2(renderLogsContent);
    else if (tool === "search") renderForPanel2(renderSearchContent);
    else if (tool === "novnc") renderForPanel2(renderNovncContent);
}

(function() {
    function initPanel2() {
        var openBtn = document.getElementById("openToolsPanel2");
        var closeBtn = document.getElementById("closeToolsPanel2");
        var panel2 = document.getElementById("toolsPanel2");
        if (openBtn && panel2) {
            openBtn.addEventListener("click", function() {
                panel2.style.display = "flex";
                panel2Active = true;
                openBtn.style.color = "#f48771";
                switchTool2(currentTool2);
            });
        }
        if (closeBtn && panel2) {
            closeBtn.addEventListener("click", function() {
                panel2.style.display = "none";
                panel2Active = false;
                var ob = document.getElementById("openToolsPanel2");
                if (ob) ob.style.color = "#4ec9b0";
            });
        }
        if (panel2) {
            var hdrBtns = panel2.querySelector(".tools-header-buttons");
            if (hdrBtns) {
                hdrBtns.addEventListener("click", function(e) {
                    var btn = e.target.closest("button");
                    if (btn && btn.dataset.tool) switchTool2(btn.dataset.tool);
                });
            }
        }
    }

    var dragging = false, startX, startY, startLeft, startTop;
    function initDrag2() {
        var panel = document.getElementById("toolsPanel2");
        var handle = document.getElementById("toolsPanel2DragHandle");
        if (!panel || !handle) return;
        handle.addEventListener("mousedown", function(e) {
            if (e.target.tagName === "BUTTON") return;
            dragging = true;
            startX = e.clientX; startY = e.clientY;
            startLeft = panel.offsetLeft; startTop = panel.offsetTop;
            panel.style.transition = "none";
            document.body.style.userSelect = "none";
            e.preventDefault();
        });
        document.addEventListener("mousemove", function(e) {
            if (!dragging) return;
            var panel = document.getElementById("toolsPanel2");
            if (!panel) return;
            panel.style.left = (startLeft + e.clientX - startX) + "px";
            panel.style.top = (startTop + e.clientY - startY) + "px";
        });
        document.addEventListener("mouseup", function() {
            if (dragging) { dragging = false; document.body.style.userSelect = ""; }
        });
    }

    if (document.readyState === "loading") {
        document.addEventListener("DOMContentLoaded", function() { initPanel2(); setTimeout(initDrag2, 300); });
    } else {
        initPanel2(); setTimeout(initDrag2, 300);
    }
})();




