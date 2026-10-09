/* 會議模式 — P0 增強模組 (p0.js) — 需在主 script 之後載入 */
(function () {
'use strict';

var ROOM = document.getElementById('meetingRoom');
if (!ROOM) { console.warn('[p0] no meetingRoom'); return; }

var LS_KEY = 'meeting_p0_v1';
function byId(id) { return document.getElementById(id); }
function fmtMs(ms) { return (ms == null) ? '\u2014' : (ms < 1000 ? Math.round(ms) + 'ms' : (ms / 1000).toFixed(1) + 's'); }
function estTokens(s) { return s ? Math.max(1, Math.round(String(s).length / 1.6)) : 0; }
function esc(t) { var d = document.createElement('div'); d.textContent = (t == null ? '' : String(t)); return d.innerHTML; }
function nowMs() { return (window.performance && performance.now) ? performance.now() : Date.now(); }

function mdSafe(text) {
  var src = String(text == null ? '' : text);
  if (!src) return '';
  if (window.marked && window.DOMPurify) {
    try {
      var raw = window.marked.parse(src, { gfm: true, breaks: true });
      return window.DOMPurify.sanitize(raw, { USE_PROFILES: { html: true } });
    } catch (e) { console.warn('[p0] markdown render failed', e); }
  }
  return esc(src).replace(/\n/g, '<br>');
}

var live = document.createElement('div');
live.id = 'p0Live';
live.className = 'p0-sr-only';
live.setAttribute('aria-live', 'polite');
live.setAttribute('role', 'status');
document.body.appendChild(live);
var liveTimer = null;
function ariaSay(msg) {
  clearTimeout(liveTimer);
  liveTimer = setTimeout(function () { live.textContent = String(msg == null ? '' : msg); }, 250);
}

var STATUS = {
  waiting: '\u23F3 等待',
  running: '\uD83D\uDFE1 執行中',
  done: '\u2705 完成',
  failed: '\u274C 失敗',
  blocked: '\u26D4 依賴失敗'
};
function setStatus(msg) { var s = byId('meetingStatus'); if (s) s.textContent = msg; }

var grid = byId('agentCardsGrid');
var banner = document.createElement('div');
banner.id = 'p0FailureBanner';
banner.setAttribute('role', 'alert');
banner.innerHTML = '<span class="p0-fb-text"></span><button type="button" class="p0-fb-retry">\u21BB 全部重試</button>';
if (grid && grid.parentNode) grid.parentNode.insertBefore(banner, grid);
banner.querySelector('.p0-fb-retry').addEventListener('click', function () {
  var bad = tasks.filter(function (t) { return t.status === 'failed' || t.status === 'blocked'; });
  if (!bad.length) return;
  bad.forEach(function (t) { t.status = 'waiting'; t.error = null; });
  renderTaskList();
  window.runAllTasks();
});
function refreshBanner() {
  var bad = tasks.filter(function (t) { return t.status === 'failed' || t.status === 'blocked'; });
  if (!bad.length) { banner.classList.remove('show'); return; }
  banner.querySelector('.p0-fb-text').textContent = '\u26A0\uFE0F ' + bad.length + ' 個任務未成功：' + bad.map(function (t) { return t.agent; }).join('\u3001');
  banner.classList.add('show');
  ariaSay(bad.length + ' 個任務失敗，已顯示重試選項');
}

var conclusionText = '';
var conclusion = document.createElement('section');
conclusion.id = 'p0Conclusion';
conclusion.innerHTML =
  '<header class="p0-conc-head">' +
    '<span class="p0-conc-title">\uD83C\uDFDB\uFE0F 會議結論</span>' +
    '<span class="p0-conc-badge" hidden>AI 彙整</span>' +
    '<span class="p0-conc-actions">' +
      '<button type="button" class="p0-btn" data-act="synth">\uD83E\uDDE0 產生結論</button>' +
      '<button type="button" class="p0-btn" data-act="copy">\uD83D\uDCCB 複製</button>' +
      '<button type="button" class="p0-btn" data-act="export">\u2B07\uFE0F 匯出 Markdown</button>' +
    '</span>' +
  '</header>' +
  '<div class="p0-conc-body" id="p0ConclusionBody">' +
    '<div class="p0-empty">所有任務完成後會自動彙整；亦可隨時按「產生結論」。彙整時會<b>明確標註 Agent 之間的衝突</b>，避免 LLM 捏造不存在的共識。</div>' +
  '</div>';
ROOM.appendChild(conclusion);
var concBody = byId('p0ConclusionBody');
var concBadge = conclusion.querySelector('.p0-conc-badge');
function setConcEmpty(msg) { conclusionText = ''; concBadge.hidden = true; concBody.innerHTML = '<div class="p0-empty">' + msg + '</div>'; }
function resetConclusion() { setConcEmpty('所有任務完成後會自動彙整；亦可隨時按「產生結論」。'); }

function costLine(t) {
  if (!t.model && t.ms == null && !t.tokens) return '成本：\u2014';
  return '成本：模型 ' + (t.model || '\u2014') + ' ｜ 耗時 ' + fmtMs(t.ms) + ' ｜ token ~' + (t.tokens || 0);
}

function getOutput(t) {
  if (t && t.cardId && agentOutputs[t.cardId] != null) return agentOutputs[t.cardId];
  return (t && t.output) ? t.output : '';
}
function snapshot() {
  return {
    v: 1, ts: Date.now(),
    input: (byId('meetingInput') || {}).value || '',
    selected: Array.from(selectedAgents),
    tasks: tasks.map(function (t) {
      return {
        id: t.id, agent: t.agent, task: t.task, priority: t.priority, status: t.status,
        dependsOn: (t.dependsOn || []).slice(),
        model: t.model || null, ms: (t.ms == null ? null : t.ms), tokens: t.tokens || null,
        error: t.error || null, output: String(getOutput(t)).slice(0, 8000)
      };
    }),
    conclusion: conclusionText
  };
}
var saveTimer = null;
function scheduleSave() { clearTimeout(saveTimer); saveTimer = setTimeout(persist, 800); }
function persist() {
  var snap = snapshot();
  try { localStorage.setItem(LS_KEY, JSON.stringify(snap)); } catch (e) { console.warn('[p0] localStorage failed', e); }
  try {
    fetch('/api/meeting/save', {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(snap)
    }).catch(function () { });
  } catch (e) { }
}
