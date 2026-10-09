/*! MOKAGI 標頭會員名稱（共用）— 凜 2026-10-01
 * 用法：頁面放 <span id="headerMemberName">…</span>，並引入
 *       <script src="/static/header_member.js" defer></script>
 * 規則：未登入 → 「未登入訪客」；已登入 → 會員顯示名。
 *       單一真源＝後端 /api/member/header（依登入 session 分開）。
 */
(function () {
  var API = '/api/member/header';
  window.MOKAGI_IDENTITY = window.MOKAGI_IDENTITY || '';
  window.mokIdentity = function () {
    try {
      return window.MOKAGI_IDENTITY || localStorage.getItem('web_user_id') ||
             localStorage.getItem('mokagi_user_id') || '';
    } catch (e) { return window.MOKAGI_IDENTITY || ''; }
  };
  function paint(el, d) {
    var loggedIn = !!(d && d.logged_in);
    var uid = loggedIn ? String(d.username || '') : String((d && d.temp_id) || '');
    el.textContent = loggedIn ? (d.display_name || uid || '會員') : '未登入訪客';
    el.title = ((d && d.plan_label) ? d.plan_label + '｜' : '') +
               (loggedIn ? ('會員：' + uid) : ('臨時 ID：' + (uid || '—')));
    el.dataset.loggedIn = loggedIn ? '1' : '0';
    window.MOKAGI_IDENTITY = uid;
    window.MOKAGI_LOGGED_IN = loggedIn;
    try {
      if (uid) {
        localStorage.setItem('web_user_id', uid);
        localStorage.setItem('mokagi_user_id', uid);
      }
    } catch (e) {}
  }
  function run() {
    var el = document.getElementById('headerMemberName');
    if (!el) return;
    if (!el.textContent || el.textContent === '載入中…') el.textContent = '載入中…';
    fetch(API, { credentials: 'same-origin' })
      .then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d && d.success !== false) paint(el, d); })
      .catch(function () { el.textContent = '未登入訪客'; });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', run);
  else run();
})();
