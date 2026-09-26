/* ============================================================
   QuickNav v1.0  快捷鍵提示套件  by indexPage
   ------------------------------------------------------------
   【統一】所有快捷鍵共用同一套觸發/渲染/選中機制。
           日後新增 /工具、/skill 只需 QuickNav.register({...})。
   【兼容】不綁定任何特定輸入框 id，預設抓 #chatInput，可 init 覆寫。
           全部 class 用 .qn- 前綴，不污染宿主頁面。
   【簡單】一個 css + 一個 js + 一行 init 即完成接入。

   用法（在 index.html 加幾行）：
     <link rel="stylesheet" href="/static/quicknav/quicknav.css">
     <script src="/static/quicknav/quicknav.js"></script>
     <script>QuickNav.init({ input: '#chatInput' });</script>

   資料來源：static/quicknav/mok_dirs.json（由 refresh.sh 生成）
             └─ 完全自包含，無需改動後端。
   ============================================================ */
(function (global) {
    'use strict';

    // ---------- 內部狀態 ----------
    var triggers = [];          // 已註冊的快捷鍵觸發器
    var inputEl = null;         // 綁定的輸入框
    var wrapperEl = null;       // 輸入框父容器（panel 掛載點）
    var panelEl = null;         // 提示面板 DOM
    var listEl = null;          // 列表容器
    var currentItems = [];      // 目前顯示的候選項
    var activeIndex = -1;       // 鍵盤選中索引
    var activeTrigger = null;   // 目前命中的觸發器
    var initialized = false;

    // ---------- 小工具 ----------
    function escapeHtml(s) {
        return String(s).replace(/[&<>"']/g, function (c) {
            return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c];
        });
    }

    function copyText(text) {
        if (navigator.clipboard && navigator.clipboard.writeText) {
            return navigator.clipboard.writeText(text).catch(function () { legacyCopy(text); });
        }
        legacyCopy(text);
        return Promise.resolve();
    }
    function legacyCopy(text) {
        var ta = document.createElement('textarea');
        ta.value = text;
        ta.style.position = 'fixed';
        ta.style.opacity = '0';
        document.body.appendChild(ta);
        ta.select();
        try { document.execCommand('copy'); } catch (e) {}
        document.body.removeChild(ta);
    }

    function showToast(msg) {
        var old = document.querySelector('.qn-toast');
        if (old) old.remove();
        var t = document.createElement('div');
        t.className = 'qn-toast';
        t.textContent = msg;
        document.body.appendChild(t);
        setTimeout(function () { t.remove(); }, 1200);
    }

    // ---------- 面板 DOM ----------
    function ensurePanel() {
        if (panelEl) return;
        wrapperEl = inputEl.parentElement || inputEl.parentNode;
        var pos = getComputedStyle(wrapperEl).position;
        if (pos === 'static') { wrapperEl.style.position = 'relative'; }

        panelEl = document.createElement('div');
        panelEl.className = 'qn-panel';
        panelEl.style.display = 'none';

        var title = document.createElement('div');
        title.className = 'qn-title';
        title.innerHTML = '<span class="qn-badge">快捷</span><span class="qn-title-text"></span>';
        panelEl.appendChild(title);

        listEl = document.createElement('ul');
        listEl.className = 'qn-list';
        panelEl.appendChild(listEl);

        wrapperEl.appendChild(panelEl);
    }

    // ---------- 渲染 ----------
    function renderPanel(trigger, items) {
        ensurePanel();
        currentItems = items || [];
        activeIndex = currentItems.length ? 0 : -1;

        var titleEl = panelEl.querySelector('.qn-title-text');
        titleEl.textContent = trigger.title || (trigger.prefix + ' 提示');

        listEl.innerHTML = '';
        if (!currentItems.length) {
            var li = document.createElement('li');
            li.className = 'qn-empty';
            li.textContent = '（無可用項目）';
            listEl.appendChild(li);
        } else {
            currentItems.forEach(function (item, i) {
                var li = document.createElement('li');
                li.className = 'qn-item' + (i === 0 ? ' qn-active' : '');
                li.setAttribute('data-i', i);

                var label = document.createElement('span');
                label.className = 'qn-label';
                label.innerHTML = highlight(item.label, trigger.prefix);

                var hint = document.createElement('span');
                hint.className = 'qn-hint';
                hint.textContent = item.hint || '點擊複製';

                li.appendChild(label);
                li.appendChild(hint);
                li.addEventListener('mousedown', function () {
                    applyItem(parseInt(li.getAttribute('data-i'), 10));
                });
                listEl.appendChild(li);
            });
        }
        panelEl.style.display = 'block';
    }

    function highlight(text, prefix) {
        var esc = escapeHtml(text);
        var p = escapeHtml(prefix);
        var idx = esc.indexOf(p);
        if (idx < 0) return esc;
        return esc.slice(0, idx) + '<span class="qn-match">' + p + '</span>' + esc.slice(idx + p.length);
    }

    function hidePanel() {
        if (panelEl) panelEl.style.display = 'none';
        activeIndex = -1;
        activeTrigger = null;
        currentItems = [];
    }

    function setActive(i) {
        if (!listEl) return;
        var items = listEl.querySelectorAll('.qn-item');
        items.forEach(function (el, k) { el.classList.toggle('qn-active', k === i); });
        activeIndex = i;
        var el = listEl.querySelector('.qn-item.qn-active');
        if (el && el.scrollIntoView) { el.scrollIntoView({ block: 'nearest' }); }
    }

    // ---------- 套用選中項 ----------
    function applyItem(i) {
        var item = currentItems[i];
        if (!item) return;
        var value = item.value || item.label;
        copyText(value);
        var val = inputEl.value;
        var prefix = activeTrigger ? activeTrigger.prefix : '';
        if (prefix && val.slice(0, prefix.length) === prefix) {
            inputEl.value = value + val.slice(prefix.length);
        }
        showToast('已複製 ' + value);
        hidePanel();
        inputEl.focus();
        try { inputEl.dispatchEvent(new Event('input', { bubbles: true })); } catch (e) {}
    }

    // ---------- 匹配邏輯 ----------
    function currentQuery() {
        var el = inputEl;
        if (document.activeElement !== el) return '';
        if (el.selectionStart !== el.selectionEnd) return '';
        if (el.selectionStart !== el.value.length) return '';
        return el.value;
    }

    function findTrigger(query) {
        for (var i = 0; i < triggers.length; i++) {
            if (query.indexOf(triggers[i].prefix) === 0) return triggers[i];
        }
        return null;
    }

    function onInput() {
        var query = currentQuery();
        if (!query) { hidePanel(); return; }
        var t = findTrigger(query);
        if (!t) { hidePanel(); return; }
        activeTrigger = t;
        resolveSource(t, function (items) {
            if (activeTrigger !== t) return;
            if (currentQuery().indexOf(t.prefix) !== 0) { hidePanel(); return; }
            renderPanel(t, items);
        });
    }

    function resolveSource(trigger, cb) {
        var src = trigger.source;
        if (Array.isArray(src)) { cb(formatItems(trigger, src)); return; }
        if (typeof src === 'function') {
            var r = src(trigger);
            if (r && typeof r.then === 'function') {
                r.then(function (d) { cb(formatItems(trigger, d)); });
            } else {
                cb(formatItems(trigger, r));
            }
            return;
        }
        if (typeof src === 'string') {
            fetch(src).then(function (resp) { return resp.json(); })
                .then(function (json) { cb(formatItems(trigger, json)); })
                .catch(function () { cb(formatItems(trigger, [])); });
            return;
        }
        cb(formatItems(trigger, []));
    }

    function formatItems(trigger, data) {
        var fmt = trigger.format;
        var list = (data && data.items) ? data.items : data;
        if (!Array.isArray(list)) list = [];
        return fmt ? list.map(fmt) : list;
    }

    // ---------- 鍵盤 ----------
    function onKeydown(e) {
        if (!panelEl || panelEl.style.display === 'none') return;
        if (!currentItems.length) {
            if (e.key === 'Escape') hidePanel();
            return;
        }
        switch (e.key) {
            case 'ArrowDown':
                e.preventDefault();
                setActive((activeIndex + 1) % currentItems.length);
                break;
            case 'ArrowUp':
                e.preventDefault();
                setActive((activeIndex - 1 + currentItems.length) % currentItems.length);
                break;
            case 'Enter':
            case 'Tab':
                e.preventDefault();
                applyItem(activeIndex >= 0 ? activeIndex : 0);
                break;
            case 'Escape':
                e.preventDefault();
                hidePanel();
                break;
        }
    }

    function onDocumentClick(e) {
        if (!panelEl || panelEl.style.display === 'none') return;
        if (panelEl.contains(e.target) || e.target === inputEl) return;
        hidePanel();
    }

    // ---------- 公開 API ----------
    function register(trigger) {
        if (!trigger || !trigger.prefix) return;
        triggers.push(trigger);
        triggers.sort(function (a, b) { return b.prefix.length - a.prefix.length; });
    }

    function init(options) {
        options = options || {};
        if (initialized) return;
        initialized = true;
        var selector = options.input || '#chatInput';
        inputEl = typeof selector === 'string' ? document.querySelector(selector) : selector;
        if (!inputEl) return;
        ensurePanel();
        inputEl.addEventListener('input', onInput);
        inputEl.addEventListener('keydown', onKeydown);
        document.addEventListener('mousedown', onDocumentClick);
    }

    // ---------- 內建觸發器：.mok 文件樹提示 ----------
    // 資料來源：static/quicknav/mok_dirs.json（refresh.sh 生成）
    // 完全自包含、零後端依賴、載入輕量（幾 KB）。
    var mokTreeCache = null;
    var MOK_FALLBACK = [
        { label: '.mok/html/',    value: '.mok/html/',    hint: '📁' },
        { label: '.mok/agent/',   value: '.mok/agent/',   hint: '📁' },
        { label: '.mok/work/',    value: '.mok/work/',    hint: '📁' },
        { label: '.mok/skill/',   value: '.mok/skill/',   hint: '📁' },
        { label: '.mok/tools/',   value: '.mok/tools/',   hint: '📁' },
        { label: '.mok/core/',    value: '.mok/core/',    hint: '📁' },
        { label: '.mok/frontends/', value: '.mok/frontends/', hint: '📁' },
        { label: '.mok/backups/', value: '.mok/backups/', hint: '📁' }
    ];

    function fetchMokDirs(cb) {
        if (mokTreeCache) { cb(mokTreeCache); return; }
        fetch('/static/quicknav/mok_dirs.json', { cache: 'no-store' })
            .then(function (r) { return r.json(); })
            .then(function (json) {
                var dirs = [];
                (json && json.dirs || []).forEach(function (d) {
                    var disp = d.display || ('.mok/' + d.name + '/');
                    dirs.push({ label: disp, value: disp, hint: '📁' });
                });
                if (!dirs.length) dirs = MOK_FALLBACK;
                mokTreeCache = dirs;
                cb(dirs);
            })
            .catch(function () { cb(MOK_FALLBACK); });
    }

    register({
        id: 'mok',
        prefix: '.mok',
        title: '文件樹關鍵字提示',
        source: function () {
            return new Promise(function (resolve) { fetchMokDirs(resolve); });
        },
        format: function (item) { return item; }
    });

    global.QuickNav = {
        init: init,
        register: register,
        version: '1.0.0',
        author: 'indexPage'
    };
})(window);
