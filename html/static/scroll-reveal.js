/*!
 * ============================================================
 *  scroll-reveal.js  —  滾動顯現動畫（Scroll Reveal）
 *  俗稱：滾動觸發動畫 / AOS / ScrollReveal / 流水式加載 / 文字漸入
 * ============================================================
 *  版本：2026081502
 *  功能：元素滑入畫面時，以「淡入 + 位移 + 模糊 + 縮放」等效果顯現。
 *        支援瀑布式（cascade）依序出現、逐字／逐詞漸入。
 *
 *  一行引入（任何網站皆可用）：
 *      <script src="https://64071181.xyz/static/scroll-reveal.js"></script>
 *
 *  用法：
 *      <div data-reveal="fade-up">從下往上淡入</div>
 *      <div data-reveal="fade-left" data-reveal-delay="200">延遲淡入</div>
 *      <div data-reveal="blur-in">模糊聚焦</div>
 *      <p  data-reveal="text">這段文字會逐字逐字慢慢加入</p>
 *
 *      <div data-reveal-group>          <!-- 子項瀑布式依序出現 -->
 *        <div data-reveal="fade-up">1</div>
 *        <div data-reveal="fade-up">2</div>
 *        <div data-reveal="fade-up">3</div>
 *      </div>
 *
 *  可選全域設定（放在 script 之前）：
 *      window.SR_CONFIG = { duration: 800, distance: '40px' };
 *  授權：MIT
 * ============================================================
 */
(function (global) {
  'use strict';

  var VER = '2026081502';

  /* 防止重複載入 */
  if (global.__SCROLL_REVEAL__) {
    if (global.__SCROLL_REVEAL_VER__ === VER) return;
  }
  global.__SCROLL_REVEAL__ = true;
  global.__SCROLL_REVEAL_VER__ = VER;

  /* ============ 預設設定（可被 window.SR_CONFIG 覆寫）============ */
  var DEFAULTS = {
    origin: 'bottom',                     // 起始方向 bottom/top/left/right
    distance: '30px',                     // 位移距離
    duration: 700,                        // 動畫時長(ms)
    delay: 0,                             // 延遲(ms)
    interval: 110,                        // 瀑布式每個子項間隔(ms)
    easing: 'cubic-bezier(0.22, 0.61, 0.36, 1)',
    opacity: 0,                           // 起始透明度
    scale: 1,                             // 起始縮放
    blur: 0,                              // 起始模糊(px)
    rotate: 0,                            // 起始旋轉(deg)
    once: true,                           // 只顯現一次
    threshold: 0.12,                      // 觸發門檻(0~1)
    rootMargin: '0px 0px -8% 0px'
  };
  var gcfg = global.SR_CONFIG || {};
  var k;
  for (k in gcfg) { if (DEFAULTS.hasOwnProperty(k)) DEFAULTS[k] = gcfg[k]; }

  /* 內建特效 */
  var EFFECTS = {
    'fade-in':    {},
    'fade-up':    { origin: 'bottom', distance: '36px' },
    'fade-down':  { origin: 'top',    distance: '36px' },
    'fade-left':  { origin: 'right',  distance: '48px' },
    'fade-right': { origin: 'left',   distance: '48px' },
    'zoom-in':    { scale: 0.82 },
    'zoom-out':   { scale: 1.14 },
    'blur-in':    { blur: 12, distance: '0px' },
    'flip':       { rotate: 6, scale: 0.95 },
    'none':       null                  // 不隱藏（僅供 group 控制）
  };

  /* ============ 注入 CSS ============ */
  var CSS = [
    '[data-reveal] {',
    '  transition-property: opacity, transform, filter;',
    '  transition-duration: var(--sr-duration, 700ms);',
    '  transition-timing-function: var(--sr-easing, cubic-bezier(0.22, 0.61, 0.36, 1));',
    '  transition-delay: var(--sr-delay, 0ms);',
    '  will-change: opacity, transform, filter;',
    '}',
    '[data-reveal].sr-hidden {',
    '  opacity: var(--sr-opacity, 0);',
    '  transform: translateX(var(--sr-tx, 0px)) translateY(var(--sr-ty, 0px))',
    '             scale(var(--sr-scale, 1)) rotate(var(--sr-rotate, 0deg));',
    '  filter: blur(var(--sr-blur, 0px));',
    '}',
    '[data-reveal].sr-visible {',
    '  opacity: 1;',
    '  transform: none;',
    '  filter: none;',
    '}',
    '[data-reveal].sr-word { display: inline-block; }',
    '@media (prefers-reduced-motion: reduce) {',
    '  [data-reveal] { transition: none !important; }',
    '  [data-reveal].sr-hidden { opacity: 1 !important; transform: none !important; filter: none !important; }',
    '}'
  ].join('\n');

  function injectCSS() {
    var style = document.createElement('style');
    style.setAttribute('data-scroll-reveal', VER);
    style.textContent = CSS;
    (document.head || document.documentElement).appendChild(style);
  }
  injectCSS();

  /* ============ 工具函式 ============ */
  function parseVal(el, key, def) {
    var v = el.getAttribute('data-reveal-' + key);
    if (v === null || v === '') return def;
    if (v === 'true') return true;
    if (v === 'false') return false;
    var n = Number(v);
    return (!isNaN(n) && v.trim() !== '') ? n : v;
  }

  function readConfig(el) {
    var cfg = {}, k2;
    for (k2 in DEFAULTS) cfg[k2] = DEFAULTS[k2];

    var name = el.getAttribute('data-reveal') || 'fade-up';
    if (name === 'none') return null;
    var eff = EFFECTS[name];
    if (eff) { for (k2 in eff) cfg[k2] = eff[k2]; }

    var keys = ['origin', 'distance', 'duration', 'delay', 'interval', 'easing',
                'opacity', 'scale', 'blur', 'rotate', 'once', 'threshold'];
    for (var i = 0; i < keys.length; i++) {
      var v = parseVal(el, keys[i], undefined);
      if (v !== undefined && v !== null) cfg[keys[i]] = v;
    }
    return cfg;
  }

  function applyHidden(el, cfg) {
    var tx = 0, ty = 0;
    if (cfg.origin === 'left')   tx = '-' + cfg.distance;   // 起始在左，向右移入
    if (cfg.origin === 'right')  tx = cfg.distance;         // 起始在右，向左移入
    if (cfg.origin === 'top')    ty = '-' + cfg.distance;   // 起始在上，向下移入
    if (cfg.origin === 'bottom') ty = cfg.distance;         // 起始在下，向上移入

    el.style.setProperty('--sr-tx', tx);
    el.style.setProperty('--sr-ty', ty);
    el.style.setProperty('--sr-duration', cfg.duration + 'ms');
    el.style.setProperty('--sr-delay', cfg.delay + 'ms');
    el.style.setProperty('--sr-easing', cfg.easing);
    el.style.setProperty('--sr-opacity', cfg.opacity);
    el.style.setProperty('--sr-scale', cfg.scale);
    el.style.setProperty('--sr-rotate', cfg.rotate + 'deg');
    el.style.setProperty('--sr-blur', cfg.blur + 'px');
  }

  function reveal(el) {
    el.classList.add('sr-visible');
    el.classList.remove('sr-hidden');
  }
  function hide(el) {
    el.classList.add('sr-hidden');
    el.classList.remove('sr-visible');
  }

  function hasCJK(s) {
    return /[\u3400-\u9fff\uf900-\ufaff\u3040-\u30ff]/.test(s);
  }

  /* ============ 逐字 / 逐詞 漸入 ============ */
  function splitText(el, mode) {
    if (el.getAttribute('data-sr-split') === '1') return;
    el.setAttribute('data-sr-split', '1');

    var text = el.textContent;
    if (!text || !text.trim()) return;

    var parts;
    if (mode === 'words') {
      parts = text.split(/(\s+)/);
    } else {
      /* 預設逐字（中英皆可） */
      parts = text.split('');
    }

    var interval = parseInt(el.getAttribute('data-reveal-interval'), 10);
    if (isNaN(interval)) interval = (mode === 'words') ? 80 : 40;

    var frag = document.createDocumentFragment();
    for (var i = 0; i < parts.length; i++) {
      if (parts[i] === '') continue;
      var span = document.createElement('span');
      span.className = 'sr-word';
      span.setAttribute('data-reveal', 'fade-up');
      span.setAttribute('data-reveal-distance', '8px');
      span.setAttribute('data-reveal-delay', String(i * interval));
      span.textContent = parts[i];
      frag.appendChild(span);
    }
    el.textContent = '';
    el.appendChild(frag);

    /* 立即初始化剛產生的字詞，避免首幀閃現 */
    var spans = el.querySelectorAll('.sr-word');
    for (var j = 0; j < spans.length; j++) process(spans[j]);
  }

  /* ============ Observer ============ */
  var observer = null;

  function initObserver() {
    if (observer || !('IntersectionObserver' in global)) return;
    observer = new IntersectionObserver(function (entries) {
      for (var i = 0; i < entries.length; i++) {
        var entry = entries[i];
        var el = entry.target;
        var cfg = readConfig(el);
        if (entry.isIntersecting) {
          reveal(el);
          if (!cfg || cfg.once !== false) observer.unobserve(el);
        } else if (cfg && cfg.once === false) {
          hide(el);   /* 離開畫面還原，再次進入重新顯現 */
        }
      }
    }, {
      threshold: DEFAULTS.threshold,
      rootMargin: DEFAULTS.rootMargin
    });
  }

  /* 處理單一元素 */
  function process(el) {
    var name = el.getAttribute('data-reveal') || '';
    if (name === 'text' || name === 'text-words' || name === 'chars' || name === 'words') {
      splitText(el, (name === 'words' || name === 'text-words') ? 'words' : 'chars');
      return;
    }
    var cfg = readConfig(el);
    if (cfg === null) return;
    applyHidden(el, cfg);
    hide(el);
    if (observer) observer.observe(el);
    else reveal(el);
  }

  /* 處理 group：子項瀑布式依序出現 */
  function processGroup(container) {
    var interval = parseInt(container.getAttribute('data-reveal-interval'), 10);
    if (isNaN(interval)) interval = DEFAULTS.interval;
    var base = parseInt(container.getAttribute('data-reveal-delay'), 10) || 0;

    var items = container.querySelectorAll('[data-reveal]');
    for (var i = 0; i < items.length; i++) {
      var el = items[i];
      if (el.closest && el.closest('[data-reveal-group]') !== container) continue;
      if (el.hasAttribute('data-reveal-delay')) continue;  /* 手動設定過就不覆寫 */
      el.setAttribute('data-reveal-delay', String(base + i * interval));
    }
  }

  /* 掃描並初始化全部 */
  function refresh() {
    initObserver();

    var groups = document.querySelectorAll('[data-reveal-group]');
    for (var g = 0; g < groups.length; g++) processGroup(groups[g]);

    var els = document.querySelectorAll('[data-reveal]');
    for (var i = 0; i < els.length; i++) {
      var el = els[i];
      if (el.classList.contains('sr-hidden') || el.classList.contains('sr-visible')) continue;
      process(el);
    }
  }

  /* ============ 公開 API ============ */
  var ScrollReveal = {
    version: VER,
    refresh: refresh,
    reveal: function (sel) {
      if (typeof sel === 'string') sel = document.querySelector(sel);
      if (sel) reveal(sel);
    },
    hide: function (sel) {
      if (typeof sel === 'string') sel = document.querySelector(sel);
      if (sel) hide(sel);
    },
    /* 程式化：ScrollReveal.revealAll('.card', {effect:'fade-up', delay:100}) */
    revealAll: function (selector, opts) {
      opts = opts || {};
      var els = document.querySelectorAll(selector);
      for (var i = 0; i < els.length; i++) {
        var el = els[i];
        var effect = opts.effect || el.getAttribute('data-reveal') || 'fade-up';
        el.setAttribute('data-reveal', effect);
        if (opts.origin)   el.setAttribute('data-reveal-origin', opts.origin);
        if (opts.distance) el.setAttribute('data-reveal-distance', opts.distance);
        if (opts.duration) el.setAttribute('data-reveal-duration', String(opts.duration));
        if (opts.delay !== undefined)   el.setAttribute('data-reveal-delay', String(opts.delay));
        if (opts.interval !== undefined) el.setAttribute('data-reveal-interval', String(opts.interval));
        if (opts.blur !== undefined)    el.setAttribute('data-reveal-blur', String(opts.blur));
        if (opts.scale !== undefined)   el.setAttribute('data-reveal-scale', String(opts.scale));
        if (opts.rotate !== undefined)  el.setAttribute('data-reveal-rotate', String(opts.rotate));
        if (opts.once === false) el.setAttribute('data-reveal-once', 'false');
        process(el);
      }
    }
  };

  global.ScrollReveal = ScrollReveal;

  /* ============ 自動啟動 + 動態內容監聽 ============ */
  function autoInit() {
    refresh();
    if ('MutationObserver' in global) {
      var mo = new MutationObserver(function () {
        if (autoInit._t) return;
        autoInit._t = setTimeout(function () {
          autoInit._t = null;
          refresh();
        }, 300);
      });
      mo.observe(document.body || document.documentElement, { childList: true, subtree: true });
    }
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', autoInit);
  } else {
    autoInit();
  }

})(typeof window !== 'undefined' ? window : this);
