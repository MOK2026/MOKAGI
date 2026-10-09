// ============================================================
// Mok6407Nav.js — 一鍵嵌入「語言切換器 + AI客服 + 聯繫列」插件
// ============================================================
// 🚀 用法（在新網頁加入這一句）：
//
//   <script src="https://64071181.xyz/static/Mok6407Nav/mok6407nav.js"></script>
//
// 再設定「佔位」（預設查詢文字，會自動帶入 WhatsApp 聯絡連結）：
//
//   <script>window.MOK6407NAV = { placeholder: '莫生我要查詢頭髮鑽石散熱' };</script>
//   或 <script src="https://64071181.xyz/static/Mok6407Nav/mok6407nav.js"
//              data-placeholder="莫生我要查詢頭髮鑽石散熱"></script>
//
// 插件會自動幫你注入：右上角多語言切換器、AI客服視窗(api.js)、
// 底部聯絡列(ContactAKI)、ScrollReveal 動畫、以及所有依賴資源。
// ============================================================
(function () {
  'use strict';

  // ---------- 讀取配置 ----------
  var cfg = window.MOK6407NAV || {};
  var scriptEl = document.currentScript ||
    (function () { var s = document.getElementsByTagName('script'); return s[s.length - 1]; })();
  var dataPH = scriptEl && scriptEl.getAttribute ? scriptEl.getAttribute('data-placeholder') : null;

  // 佔位：優先順序 data-placeholder > window.MOK6407NAV.placeholder > 頁面既有 <samp id="莫生查詢">
  var existingPH = null;
  try {
    var samp = document.getElementById('莫生查詢');
    if (samp && samp.textContent.trim()) existingPH = samp.textContent.trim();
  } catch (e) { existingPH = null; }

  var CONFIG = {
    placeholder: dataPH || cfg.placeholder || window.MOK6407NAV_PLACEHOLDER || existingPH || '莫生我要查詢頭髮鑽石散熱',
    agent: cfg.agent || window.MOKAGI_AGENT || '客服',
    server: cfg.server || window.MOKAGI_SERVER || 'https://64071181.xyz',
    position: cfg.position || window.position || 'bottom-right',
    contact: cfg.contact || window.contact || 'https://wa.me/85264071181/?text=',
    quickLinks: cfg.quickLinks || window.quickLinks || [
      { text: "📋 服務項目", query: "你們提供哪些AI顧問服務？" },
      { text: "💰 詢價", query: "我想了解收費方案" },
      { text: "📅 預約諮詢", query: "我想預約免費諮詢" },
      { text: "📞 聯絡我們", query: "如何聯絡你們？", answer: "我們的 WhatsApp：852-6407 1181（https://wa.me/85264071181/）。辦公時間：星期一至五 10:00–19:00（公眾假期休息）。歡迎隨時聯絡我們，會盡快回覆您！😊" }
    ],
    contacts: cfg.contacts || window.MOK6407_CONTACTS || [
      'mok20260316ci@gmail.com',
      'https://wa.me/85264071181/',
      'https://i.meee.com.tw/atcX6QM.png',
      'https://www.instagram.com/strangestoriesamazingfactshk/',
      'https://line.me/ti/p/UlSPd7p9zh/',
      'https://www.facebook.com/profile.php?id=61593603774378',
      'https://www.linkedin.com/in/lingmei-mok/','https://github.com/MOK2026/MOKAGI/','https://xhslink.cn/m/4aEqI43LvsI','https://v.douyin.com/ZlFKhK7Y95A/','https://www.threads.com/@strangestoriesamazingfactshk','https://www.youtube.com/@mokagi2026','https://x.com/mokagi6407'
    ],
    revealSelectors: cfg.revealSelectors ||
      "section, .section, .hero, .card, .step, .ai-item, .news-item, .stat-card, .footer, h1, h2, h3, .section-title, .section-sub, .n-title, .news-title",
    autoStart: cfg.autoStart !== false,
    enabled: cfg.enabled !== false
  };

  if (!CONFIG.enabled) return;
  var PH = CONFIG.placeholder;

  // 聯絡連結（佔位帶入 WhatsApp 文字）
  var contactUrl = CONFIG.contact + encodeURIComponent(PH) + '/';

  // ============================================================
  // 1️⃣ 注入 CSS（語言切換器 + 聯絡列樣式）
  // ============================================================
  var CSS_TEXT = [
    '/* ===== 語言切換器 ===== */',
    '.lang-switcher {',
    '  position: fixed;',
    '  top: 18px;',
    '  right: 20px;',
    '  z-index: 10000;',
    '  font-family: "Segoe UI", "Noto Sans TC", sans-serif;',
    '}',
    '.lang-btn {',
    '  display: flex;',
    '  align-items: center;',
    '  gap: 8px;',
    '  background: rgba(20, 20, 30, 0.85);',
    '  backdrop-filter: blur(12px);',
    '  -webkit-backdrop-filter: blur(12px);',
    '  border: 1px solid rgba(212, 168, 67, 0.35);',
    '  color: #d4a843;',
    '  padding: 10px 16px;',
    '  border-radius: 28px;',
    '  cursor: pointer;',
    '  font-size: 14px;',
    '  font-weight: 500;',
    '  letter-spacing: 0.5px;',
    '  transition: all 0.3s ease;',
    '  box-shadow: 0 4px 16px rgba(0,0,0,0.3);',
    '}',
    '.lang-btn:hover {',
    '  background: rgba(30, 30, 45, 0.95);',
    '  border-color: rgba(212, 168, 67, 0.7);',
    '  box-shadow: 0 6px 24px rgba(212, 168, 67, 0.15);',
    '  transform: translateY(-2px);',
    '}',
    '.lang-btn i { font-size: 16px; }',
    '.lang-dropdown {',
    '  position: absolute;',
    '  top: calc(100% + 8px);',
    '  right: 0;',
    '  background: rgba(20, 20, 30, 0.95);',
    '  backdrop-filter: blur(16px);',
    '  -webkit-backdrop-filter: blur(16px);',
    '  border: 1px solid rgba(212, 168, 67, 0.3);',
    '  border-radius: 14px;',
    '  min-width: 170px;',
    '  overflow: hidden;',
    '  opacity: 0;',
    '  visibility: hidden;',
    '  transform: translateY(-8px);',
    '  transition: all 0.25s ease;',
    '}',
    '.lang-dropdown.open {',
    '  opacity: 1;',
    '  visibility: visible;',
    '  transform: translateY(0);',
    '}',
    '.lang-option {',
    '  display: flex;',
    '  align-items: center;',
    '  gap: 10px;',
    '  padding: 12px 18px;',
    '  color: #ccc;',
    '  cursor: pointer;',
    '  font-size: 14px;',
    '  transition: all 0.2s ease;',
    '  border-bottom: 1px solid rgba(255,255,255,0.05);',
    '}',
    '.lang-option:last-child { border-bottom: none; }',
    '.lang-option:hover { background: rgba(212, 168, 67, 0.12); color: #d4a843; }',
    '.lang-option .flag { font-size: 18px; }',
    '.lang-option .lang-name { flex: 1; }',
    '.lang-option .lang-native { font-size: 11px; color: #888; }',
    '@media (max-width: 768px) {',
    '  .lang-switcher { top: 10px; right: 10px; }',
    '  .lang-btn { padding: 8px 13px; font-size: 13px; }',
    '}',
    '/* ===== 聯絡列 footer ===== */',
    '.mok6407-footer { text-align: center; padding: 30px 20px 40px; }',
    '.mok6407-footer .ContactAKI { display: flex; flex-wrap: wrap; justify-content: center; gap: 14px; margin-bottom: 12px; }',
    '.mok6407-footer .ContactAKI a, .mok6407-footer .ContactAKI span { color: #d4a843; text-decoration: none; padding: 6px 12px; border: 1px solid rgba(212,168,67,0.3); border-radius: 20px; font-size: 13px; }',
    '.mok6407-footer .ContactAKI a:hover { background: rgba(212,168,67,0.12); }',
    '.mok6407-footer .mokJsApi_copyright { color: #888; font-size: 12px; margin-top: 8px; }',
    '.scroll-top { position: fixed; bottom: 90px; right: 22px; width: 42px; height: 42px; border-radius: 50%; background: rgba(212,168,67,0.9); color: #1a1a24; font-size: 18px; font-weight: bold; display: flex; align-items: center; justify-content: center; cursor: pointer; z-index: 9999; opacity: 0; visibility: hidden; transition: all 0.3s ease; box-shadow: 0 4px 14px rgba(0,0,0,0.3); }',
    '.scroll-top.show { opacity: 1; visibility: visible; }',
    '.scroll-top:hover { transform: translateY(-3px); }'
  ].join('\n');

  function injectCSS(cssText) {
    if (document.getElementById('mok6407nav-css')) return;
    var st = document.createElement('style');
    st.id = 'mok6407nav-css';
    st.textContent = cssText;
    (document.head || document.documentElement).appendChild(st);
  }

  injectCSS(CSS_TEXT);

  // ============================================================
  // 2️⃣ 注入 HTML（語言切換器 + 佔位 samp + 聯絡列 + Google 翻譯）
  // ============================================================
  var LANGS = cfg.langs || [
    ['zh-HK', '🇭🇰', '繁體中文', '繁體'],
    ['zh-CN', '🇨🇳', '简体中文', '简体'],
    ['en', '🇺🇸', 'English', 'English'],
    ['ja', '🇯🇵', '日本語', '日本語'],
    ['ko', '🇰🇷', '한국어', '한국어'],
    ['th', '🇹🇭', 'ภาษาไทย', 'ไทย'],
    ['vi', '🇻🇳', 'Tiếng Việt', 'Việt'],
    ['id', '🇮🇩', 'Bahasa Indonesia', 'Indonesia'],
    ['fr', '🇫🇷', 'Français', 'Français'],
    ['de', '🇩🇪', 'Deutsch', 'Deutsch'],
    ['pt', '🇵🇹', 'Português', 'Português'],
    ['ru', '🇷🇺', 'Русский', 'Русский'],
    ['ar', '🇸🇦', 'العربية', 'عربي']
  ];

  var langHTML = LANGS.map(function (l) {
    return '<div class="lang-option" onclick="switchLanguage(\'' + l[0] + '\')">' +
      '<span class="flag">' + l[1] + '</span>' +
      '<span class="lang-name">' + l[2] + '</span>' +
      '<span class="lang-native">' + l[3] + '</span></div>';
  }).join('\n    ');

  var HTML_TEXT = [
    '<!-- ===== Mok6407Nav 語言切換器 ===== -->',
    '<div class="lang-switcher">',
    '  <button class="lang-btn" onclick="toggleLangDropdown()" aria-label="切換語言">',
    '    <i class="fas fa-globe"></i> 語言 / Lang',
    '    <i class="fas fa-chevron-down" style="font-size:11px;"></i>',
    '  </button>',
    '  <div class="lang-dropdown" id="langDropdown">',
    '    ' + langHTML,
    '  </div>',
    '</div>',
    '<!-- ===== 佔位 samp（供 JS / 聯絡連結讀取） ===== -->',
    '<samp id="莫生查詢" style="display:none;">' + PH + '</samp>',
    '<!-- ===== Mok6407Nav 聯絡列 footer ===== -->',
    '<div class="mok6407-footer">',
    '  <div id="ContactAKI" class="ContactAKI"><!-- JS _顯示聯莫 --></div>',
    '  <p class="mokJsApi_copyright">版權所有 © 莫氏企業</p>',
    '</div>',
    '<div class="scroll-top" id="scrollTop" onclick="scrollToTop()">↑</div>',
    '<div id="google_translate_element" style="display:none;"></div>'
  ].join('\n');

  function injectHTML(htmlText) {
    if (document.getElementById('langDropdown')) return; // 已有，不重複注入
    var wrap = document.createElement('div');
    wrap.innerHTML = htmlText;
    while (wrap.firstChild) {
      document.body.appendChild(wrap.firstChild);
    }
  }

  // ---------- 注入 window 配置（供 api.js 讀取） ----------
  function setupWindow() {
    if (!window.MOKAGI_AGENT) window.MOKAGI_AGENT = CONFIG.agent;
    if (!window.contact) window.contact = contactUrl;
    if (!window.MOKAGI_SERVER) window.MOKAGI_SERVER = CONFIG.server;
    if (!window.quickLinks) window.quickLinks = CONFIG.quickLinks;
    if (!window.position) window.position = CONFIG.position;
    // 供頁面其他 JS 讀取佔位
    window.MOK6407NAV_PLACEHOLDER = PH;
    window.MOK6407_CONTACTS = CONFIG.contacts;
  }

  // ============================================================
  // 3️⃣ 依賴載入器（自動補齊所有資源，已存在則跳過）
  // ============================================================
  var DEP_CSS = [
    { id: 'mok-fa5', url: 'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/5.15.3/css/all.min.css' },
    { id: 'mok-fa4', url: 'https://cdnjs.cloudflare.com/ajax/libs/font-awesome/4.7.0/css/font-awesome.min.css' },
    { id: 'mok-aki-css', url: 'https://64071181.github.io/mokJsapi/aki.css' }
  ];
  var DEP_JS = [
    { id: 'mok-jquery', url: 'https://code.jquery.com/jquery-3.7.0.js', check: function () { return !!window.jQuery; } },
    { id: 'mok-mokjsapi', url: 'https://64071181.github.io/mokJsapi/mokJsApi.js', check: function () { return !!(window.mokJsApi || window._顯示聯莫); } },
    { id: 'mok-api', url: CONFIG.server + '/static/api.js', check: function () { return !!window.MOKAGI_API || !!window.__MOKAGI_SELF_CHECK__; } },
    { id: 'mok-scrollreveal', url: CONFIG.server + '/static/scroll-reveal.js', check: function () { return !!window.ScrollReveal; } }
  ];

  function loadCSS(dep) {
    return new Promise(function (resolve) {
      if (document.getElementById(dep.id)) return resolve();
      var link = document.createElement('link');
      link.id = dep.id;
      link.rel = 'stylesheet';
      link.href = dep.url;
      link.onload = link.onerror = resolve;
      document.head.appendChild(link);
    });
  }

  function loadJS(dep) {
    return new Promise(function (resolve) {
      try { if (dep.check && dep.check()) return resolve(); } catch (e) {}
      if (document.getElementById(dep.id)) return resolve();
      var s = document.createElement('script');
      s.id = dep.id;
      s.src = dep.url;
      s.async = false;
      s.onload = s.onerror = function () {
        // 若載入失敗（跨域/302），自動 fallback 到當前 origin 的同路徑資源
        setTimeout(function () {
          var ok = true;
          try { if (dep.check) ok = dep.check(); } catch (e) { ok = false; }
          if (!ok) {
            var fbId = dep.id + '-fb';
            if (!document.getElementById(fbId)) {
              var localUrl = dep.url.replace(/^https?:\/\/[^\/]+/, window.location.origin);
              if (localUrl !== dep.url) {
                var s2 = document.createElement('script');
                s2.id = fbId;
                s2.src = localUrl;
                s2.async = false;
                s2.onload = s2.onerror = resolve;
                document.head.appendChild(s2);
                return;
              }
            }
          }
          resolve();
        }, 400);
      };
      document.head.appendChild(s);
    });
  }

  function loadAllDeps() {
    var chain = Promise.resolve();
    DEP_CSS.forEach(function (d) { chain = chain.then(function () { return loadCSS(d); }); });
    DEP_JS.forEach(function (d) { chain = chain.then(function () { return loadJS(d); }); });
    return chain;
  }

  // ============================================================
  // 4️⃣ 語言切換功能（Google Translate cookie 機制）
  // ============================================================
  function injectLangScript() {
    if (document.getElementById('mok6407-lang-script')) return;
    var s = document.createElement('script');
    s.id = 'mok6407-lang-script';
    s.textContent = [
      'function toggleLangDropdown() {',
      '  var d = document.getElementById("langDropdown");',
      '  if (d) d.classList.toggle("open");',
      '}',
      'document.addEventListener("click", function(e) {',
      '  var dropdown = document.getElementById("langDropdown");',
      '  var btn = document.querySelector(".lang-btn");',
      '  if (!dropdown || !btn) return;',
      '  if (!dropdown.contains(e.target) && !btn.contains(e.target)) {',
      '    dropdown.classList.remove("open");',
      '  }',
      '});',
      'function switchLanguage(lang) {',
      '  var d = document.getElementById("langDropdown");',
      '  if (d) d.classList.remove("open");',
      '  try { localStorage.setItem("mok_lang", lang); } catch (e) {}',
      '  if (lang === "zh-HK") {',
      '    document.cookie = "googtrans=; expires=Thu, 01 Jan 1970 00:00:00 UTC; path=/;";',
      '    location.reload();',
      '    return;',
      '  }',
      '  document.cookie = "googtrans=/zh-HK/" + lang + "; path=/";',
      '  var select = document.querySelector("#google_translate_element select");',
      '  if (select) {',
      '    select.value = lang;',
      '    select.dispatchEvent(new Event("change"));',
      '    return;',
      '  }',
      '  if (!document.getElementById("google-translate-script")) {',
      '    var script = document.createElement("script");',
      '    script.id = "google-translate-script";',
      '    script.src = "https://translate.google.com/translate_a/element.js?cb=googleTranslateElementInit";',
      '    document.head.appendChild(script);',
      '  } else {',
      '    location.reload();',
      '  }',
      '}',
      'function googleTranslateElementInit() {',
      '  if (typeof google === "undefined" || !google.translate) return;',
      '  new google.translate.TranslateElement({',
      '    pageLanguage: "zh-HK",',
      '    includedLanguages: "zh-CN,en,ja,ko",',
      '    layout: google.translate.TranslateElement.InlineLayout.HORIZONTAL,',
      '    autoDisplay: false',
      '  }, "google_translate_element");',
      '}',
      '(function() {',
      '  var savedLang = null;',
      '  try { savedLang = localStorage.getItem("mok_lang"); } catch (e) {}',
      '  if (savedLang && savedLang !== "zh-HK") {',
      '    document.cookie = "googtrans=/zh-HK/" + savedLang + "; path=/";',
      '  }',
      '})();'
    ].join('\n');
    document.head.appendChild(s);
  }

  // ============================================================
  // 5️⃣ 聯絡列（ContactAKI） + 返回頂部
  // ============================================================
    function injectContactScript() {
    if (document.getElementById('mok6407-contact-script')) return;
    var s = document.createElement('script');
    s.id = 'mok6407-contact-script';
    s.textContent = 'window.聯莫 = ' + JSON.stringify(CONFIG.contacts) + ';';
    document.head.appendChild(s);
  }

function injectScrollTopScript() {
    if (document.getElementById('mok6407-scrolltop-script')) return;
    var s = document.createElement('script');
    s.id = 'mok6407-scrolltop-script';
    s.textContent = [
      'function scrollToTop() { window.scrollTo({ top: 0, behavior: "smooth" }); }',
      '(function(){',
      '  var btn = document.getElementById("scrollTop");',
      '  if (!btn) return;',
      '  function onScroll() {',
      '    if (window.scrollY > 300) btn.classList.add("show");',
      '    else btn.classList.remove("show");',
      '  }',
      '  window.addEventListener("scroll", onScroll, { passive: true });',
      '  onScroll();',
      '})();'
    ].join('\n');
    document.head.appendChild(s);
  }

  // ============================================================
  // 6️⃣ ScrollReveal 動畫
  // ============================================================
  function initReveal() {
    if (typeof window.ScrollReveal === 'undefined') return;
    try {
      window.ScrollReveal.revealAll(CONFIG.revealSelectors, { effect: 'fade-up' });
    } catch (e) {}
  }

  // ============================================================
  // 🚀 啟動
  // ============================================================
  function boot() {
    if (document.body) {
      injectHTML(HTML_TEXT);
    } else {
      document.addEventListener('DOMContentLoaded', function () { injectHTML(HTML_TEXT); });
    }
    setupWindow();
    injectLangScript();
    injectContactScript();
    injectScrollTopScript();
    loadAllDeps().then(function () {
      // api.js 載入後，若它提供初始化則自動完成；再補 ScrollReveal
      // mokJsApi.js + jQuery 已就緒 → 生成聯絡卡
      try { if (typeof window._顯示聯莫 === 'function' && window.聯莫) window._顯示聯莫(window.聯莫); } catch (e) {}
      initReveal();
      setTimeout(initReveal, 800);
      setTimeout(initReveal, 2500);
    });
  }

  if (CONFIG.autoStart) {
    if (document.readyState === 'loading') {
      document.addEventListener('DOMContentLoaded', boot);
    } else {
      boot();
    }
  } else {
    window.Mok6407NavBoot = boot; // 手動啟動：Mok6407NavBoot()
  }

  // 對外 API
  window.Mok6407Nav = {
    config: CONFIG,
    placeholder: PH,
    boot: boot,
    refresh: function () { setupWindow(); initReveal(); }
  };

})();
