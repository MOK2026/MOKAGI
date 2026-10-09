/* 工具列 data-driven（/static/tools.json）by indexPage 2026-09-27 - B 方案 */

(function () {
  var SKIP_IDS = { agentInfo: 1 };
  function applyAdmin(btn, def) {
    if (def.adminOnly) { btn.setAttribute('data-admin-only', ''); btn.classList.toggle('mok-hidden', !window.MOK_IS_ADMIN); }
    else { btn.removeAttribute('data-admin-only'); btn.classList.remove('mok-hidden'); }
  }
  function openPage(page) {
    var box = document.getElementById('toolsContent');
    if (!box) return;
    box.innerHTML = '';
    var f = document.createElement('IFRAME');
    f.style.cssText = 'width:100%;height:100%;border:none;border-radius:8px;';
    f.setAttribute('src', page);
    box.appendChild(f);
  }
  function bindNew(btn, def) {
    btn.addEventListener('click', function (e) {
      e.stopPropagation();
      if (window.MOK_LOGGED_IN !== true) return;
      if (def.type === 'iframe' && def.page) { openPage(def.page); return; }
      var fn = window.__MOK_RENDER_FNS && window.__MOK_RENDER_FNS[def.id];
      if (typeof fn === 'function') { try { fn(); } catch (err) { console.warn('[tools] render error', def.id, err); } }
    });
  }
  function buildPanel1(arr) {
    var container = document.querySelector('#toolsPanel .tools-header .tools-header-buttons');
    if (!container) return;
    var ref = document.getElementById('openToolsPanel2') || document.getElementById('toolsMoreBtn') || document.getElementById('toggleToolsPanel');
    arr.forEach(function (def) {
      if ((def.panel || 1) !== 1) return;
      if (def.type === 'action') return;
      if (SKIP_IDS[def.id]) return;
      var btn = container.querySelector('button[data-tool="' + def.id + '"]');
      if (!btn) { btn = document.createElement('button'); btn.setAttribute('data-tool', def.id); bindNew(btn, def); }
      btn.innerHTML = (def.icon ? def.icon + ' ' : '') + (def.label || def.id);
      if (def.label) btn.title = def.label;
      applyAdmin(btn, def);
      if (ref) container.insertBefore(btn, ref); else container.appendChild(btn);
    });
  }
  function loadToolsJson() {
    return fetch('/static/tools.json', { cache: 'no-store' }).then(function (r) { return r.json(); }).then(function (data) {
      var arr = (data && data.tools) ? data.tools : [];
      window.__MOK_TOOL_DEFS = {};
      window.__MOK_IFRAME_TOOLS = window.__MOK_IFRAME_TOOLS || {};
      arr.forEach(function (t) {
        window.__MOK_TOOL_DEFS[t.id] = t;
        if (t.type === 'iframe' && t.page) window.__MOK_IFRAME_TOOLS[t.id] = t.page;
      });
      buildPanel1(arr);
      if (typeof updateToolsHeaderOverflow === 'function') { try { updateToolsHeaderOverflow(); } catch (e) {} }
      if (typeof window.MOK_refreshAdmin === 'function') { try { window.MOK_refreshAdmin(); } catch (e) {} }
      console.log('[tools.json] 工具列已套用，共 ' + arr.length + ' 項');
      return arr;
    }).catch(function (err) { console.warn('[tools.json] load failed:', err); });
  }
  window.MOK_reloadTools = loadToolsJson;
  if (document.readyState === 'loading') { document.addEventListener('DOMContentLoaded', loadToolsJson); } else { loadToolsJson(); }
})();
