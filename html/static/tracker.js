(function () {
  'use strict';
  if (window.__mokTrackerLoaded) return;
  window.__mokTrackerLoaded = true;
  var ENDPOINT = (window.MOK_TRACK_ENDPOINT || "/t");
  var IDLE_MS = 30 * 60 * 1000;
  var TEXT_MAX = 80;
  function ls(k, v) { try { if (v === undefined) return localStorage.getItem(k); localStorage.setItem(k, v); } catch (e) { return null; } }
  function ss(k, v) { try { if (v === undefined) return sessionStorage.getItem(k); sessionStorage.setItem(k, v); } catch (e) { return null; } }
  function uuid() {
    try { if (window.crypto && crypto.randomUUID) return crypto.randomUUID(); } catch (e) {}
    return 'xxxxxxxxyxxxxyxxxx'.replace(/[xy]/g, function (c) {
      var r = Math.random() * 16 | 0, v = c === 'x' ? r : (r & 0x3 | 0x8);
      return v.toString(16);
    }) + Date.now().toString(16);
  }
  var VID = ls('mok_vid');
  if (!VID) { VID = uuid(); ls('mok_vid', VID); }
  function newSid() { var s = uuid(); ss('mok_sid', s); ls('mok_last', String(Date.now())); return s; }
  var SID = ss('mok_sid');
  var lastSeen = parseInt(ls('mok_last') || '0', 10);
  var idle = !lastSeen || (Date.now() - lastSeen > IDLE_MS);
  if (!SID || idle) SID = newSid();
  var isNewSession = idle || !lastSeen;
  function qs(name) {
    try {
      var m = new RegExp('[?&]' + name + '=([^&#]*)').exec(location.search);
      return m ? decodeURIComponent(m[1].replace(/\+/g, ' ')) : '';
    } catch (e) { return ''; }
  }
  var UTM = {
    utm_source: qs('utm_source'), utm_medium: qs('utm_medium'),
    utm_campaign: qs('utm_campaign'), utm_term: qs('utm_term'), utm_content: qs('utm_content')
  };
  var REF = document.referrer || '';
  var LANDING = ss('mok_landing');
  if (!LANDING) { LANDING = location.href; ss('mok_landing', LANDING); }
  var CTX = { member_id: '', agent: '', plan: '' };
  window.mokTrackSetContext = function (o) { if (o && typeof o === 'object') { for (var k in o) if (o[k] != null) CTX[k] = String(o[k]); } };
  function autoCtx() {
    try {
      if (!CTX.agent) CTX.agent = window.MOK_AGENT_NAME || window.MOKAGI_AGENT || '';
      if (!CTX.member_id && window.MOK_LOGGED_IN && window.MOK_MEMBER_ID) CTX.member_id = String(window.MOK_MEMBER_ID);
    } catch (e) {}
  }
  function send(ev) {
    autoCtx();
    var p = {
      vid: VID, sid: SID, event: ev.event, ts: Date.now(),
      url: location.href, title: (document.title || '').slice(0, 200),
      ref: REF, landing: LANDING,
      lang: navigator.language || '', tz: (function () { try { return Intl.DateTimeFormat().resolvedOptions().timeZone; } catch (e) { return ''; } })(),
      screen_w: screen.width, screen_h: screen.height,
      viewport_w: window.innerWidth, viewport_h: window.innerHeight,
      member_id: CTX.member_id, agent: CTX.agent, plan: CTX.plan
    };
    for (var k in UTM) p[k] = UTM[k];
    for (var k2 in ev) p[k2] = ev[k2];
    var body = JSON.stringify(p);
    ls('mok_last', String(Date.now()));
    try {
      if (navigator.sendBeacon) {
        var blob = new Blob([body], { type: 'application/json' });
        if (navigator.sendBeacon(ENDPOINT, blob)) return;
      }
    } catch (e) {}
    try {
      fetch(ENDPOINT, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: body, keepalive: true, credentials: 'omit' });
    } catch (e) {}
  }
  window.mokTrack = function (name, props) {
    var ev = { event: name || 'custom' };
    if (props && typeof props === 'object') { for (var k in props) ev[k] = props[k]; }
    send(ev);
  };
  window.mokTrackSend = send;
  function txt(el) {
    try { var t = (el.innerText || el.textContent || el.value || el.getAttribute('aria-label') || '').replace(/\s+/g, ' ').trim(); return t.slice(0, TEXT_MAX); }
    catch (e) { return ''; }
  }
  function ident(el) {
    if (!el) return { elem_id: '', elem_tag: '', elem_text: '', elem_class: '', elem_role: '', elem_href: '' };
    var cls = (typeof el.className === 'string' ? el.className : '').split(/\s+/).slice(0, 3).join(' ');
    var role = '';
    try { var b = el.closest ? el.closest('button,a,[role=button]') : null; if (b && b !== el) role = b.id || b.tagName.toLowerCase(); } catch (e) {}
    return {
      elem_id: el.id || el.getAttribute('data-track') || '',
      elem_tag: (el.tagName || '').toLowerCase(),
      elem_text: txt(el),
      elem_class: cls,
      elem_role: el.getAttribute('role') || role,
      elem_href: (el.tagName === 'A' && el.href) ? el.href.slice(0, 300) : ''
    };
  }
  var depths = { 25: false, 50: false, 75: false, 100: false };
  function onScroll() {
    try {
      var h = document.documentElement.scrollHeight - window.innerHeight;
      var pct = h > 0 ? Math.round((window.scrollY / h) * 100) : 100;
      [25, 50, 75, 100].forEach(function (d) { if (!depths[d] && pct >= d) { depths[d] = true; send({ event: 'scroll_depth', depth: d }); } });
    } catch (e) {}
  }
  var lastClick = {};
  document.addEventListener('click', function (e) {
    try {
      var el = e.target;
      if (!el) return;
      var node = (el.closest ? el.closest('a,button,[role=button],input[type=submit],input[type=button],label,[data-track],.btn') : null) || el;
      if (node.getAttribute && node.getAttribute('data-mok-track') === 'off') return;
      var id = ident(node);
      var key = VID + '|' + (id.elem_id || id.elem_tag + '#' + id.elem_text) + '|' + location.pathname;
      var now = Date.now();
      if (lastClick[key] && now - lastClick[key] < 300) return;
      lastClick[key] = now;
      var isOut = id.elem_href && id.elem_href.indexOf(location.host) === -1;
      send({
        event: isOut ? 'outbound_click' : 'click',
        elem_id: id.elem_id, elem_tag: id.elem_tag, elem_text: id.elem_text,
        elem_class: id.elem_class, elem_role: id.elem_role, elem_href: id.elem_href,
        x: e.clientX || 0, y: e.clientY || 0, scroll_y: Math.round(window.scrollY || 0)
      });
    } catch (err) {}
  }, true);
  document.addEventListener('submit', function (e) {
    try { var id = ident(e.target); send({ event: 'form_submit', elem_id: id.elem_id, elem_tag: 'form', elem_text: id.elem_text }); } catch (err) {}
  }, true);
  var lastUrl = '';
  function pageview() {
    if (location.href === lastUrl) return;
    lastUrl = location.href;
    send({ event: 'pageview' });
  }
  function patchHistory() {
    ['pushState', 'replaceState'].forEach(function (fn) {
      var o = history[fn];
      if (!o) return;
      history[fn] = function () { var r = o.apply(this, arguments); setTimeout(pageview, 60); return r; };
    });
    window.addEventListener('popstate', function () { setTimeout(pageview, 60); });
    window.addEventListener('hashchange', function () { setTimeout(pageview, 60); });
  }
  function endSession() {
    if (window.__mokSidEnded) return;
    window.__mokSidEnded = true;
    try { send({ event: 'session_end' }); } catch (e) {}
  }
  window.addEventListener('pagehide', endSession);
  document.addEventListener('visibilitychange', function () { if (document.visibilityState === 'hidden') endSession(); });
  function boot() {
    if (isNewSession) { send({ event: 'session_start' }); window.__mokSidEnded = false; }
    pageview();
    patchHistory();
    window.addEventListener('scroll', onScroll, { passive: true });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', boot);
  else boot();
})();
