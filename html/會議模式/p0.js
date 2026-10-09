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

function depCandidates(t) { return tasks.filter(function (x) { return x.id !== t.id; }); }

window.renderTaskList = function p0RenderTaskList() {
  if (!tasks.length) {
    $taskList.innerHTML = '<div class="placeholder-text"><span class="icon">&#127963;</span>輸入總體任務<br>讓總 AI 分析並分工</div>';
    $btnRunAll.disabled = true;
    refreshBanner();
    return;
  }
  $taskList.innerHTML = tasks.map(function (t, i) {
    var cls = (typeof getAgentColorClass === 'function') ? getAgentColorClass(t.agent) : '';
    var st = STATUS[t.status] || STATUS.waiting;
    var depHtml = depCandidates(t).map(function (x) {
      var on = (t.dependsOn || []).indexOf(x.id) >= 0;
      return '<label><input type="checkbox" data-dep="1" value="' + x.id + '"' + (on ? ' checked' : '') + '>#' + x.id + ' ' + esc(x.agent) + '</label>';
    }).join('') || '<span style="opacity:.6">無其他任務</span>';
    return '<div class="task-item p0 ' + t.status + '" id="task-' + t.id + '" data-id="' + t.id + '">' +
        '<div class="task-item-header">' +
          '<span class="task-agent-tag ' + cls + '">' + esc(t.agent) + '</span>' +
          '<span class="task-status">' + st + '</span>' +
          '<span class="p0-task-ctrl">' +
            '<button type="button" class="p0-mini" data-act="up" title="上移排序">&#9650;</button>' +
            '<button type="button" class="p0-mini" data-act="down" title="下移排序">&#9660;</button>' +
            '<button type="button" class="p0-mini" data-act="edit" title="編輯任務">&#9998;</button>' +
            '<button type="button" class="p0-mini" data-act="retry" title="重試此任務">&#8635;</button>' +
            '<button type="button" class="p0-mini danger" data-act="remove" title="移除本任務">&#10005;</button>' +
          '</span>' +
        '</div>' +
        '<div class="task-desc">' + esc(t.task) + '</div>' +
        (t.error ? '<div style="font-size:11px;color:var(--danger,#f85149);margin-top:4px">錯誤：' + esc(t.error) + '</div>' : '') +
        '<details class="p0-dep"><summary>依賴前置任務（' + (t.dependsOn || []).length + '）</summary><div>' + depHtml + '</div></details>' +
        '<div class="task-cost">' + costLine(t) + '</div>' +
      '</div>';
  }).join('');
  $btnRunAll.disabled = false;
  refreshBanner();
};

window.updateTaskUI = function p0UpdateTaskUI(task) {
  var el = byId('task-' + task.id);
  if (!el) return;
  el.className = 'task-item p0 ' + task.status;
  var st = el.querySelector('.task-status');
  if (st) st.textContent = STATUS[task.status] || task.status;
  var cost = el.querySelector('.task-cost');
  if (cost) cost.textContent = costLine(task);
};

$taskList.addEventListener('click', function (ev) {
  var btn = ev.target && ev.target.closest ? ev.target.closest('button[data-act]') : null;
  if (!btn) return;
  var item = btn.closest('.task-item');
  if (!item) return;
  var id = Number(item.getAttribute('data-id'));
  var idx = -1;
  for (var i = 0; i < tasks.length; i++) { if (tasks[i].id === id) { idx = i; break; } }
  if (idx < 0) return;
  var t = tasks[idx];
  var act = btn.getAttribute('data-act');
  if (act === 'retry') { retryTask(t); return; }
  if (act === 'edit') { startEdit(item, t); return; }
  if (act === 'up' && idx > 0) { tasks.splice(idx - 1, 0, tasks.splice(idx, 1)[0]); }
  else if (act === 'down' && idx < tasks.length - 1) { tasks.splice(idx + 1, 0, tasks.splice(idx, 1)[0]); }
  else if (act === 'remove') { tasks.splice(idx, 1); }
  else return;
  tasks.forEach(function (x, k) { x.priority = k + 1; });
  renderTaskList(); scheduleSave();
});

$taskList.addEventListener('change', function (ev) {
  var cb = ev.target;
  if (!cb || !cb.getAttribute || !cb.getAttribute('data-dep')) return;
  var item = cb.closest('.task-item');
  if (!item) return;
  var id = Number(item.getAttribute('data-id'));
  var t = null;
  for (var i = 0; i < tasks.length; i++) { if (tasks[i].id === id) { t = tasks[i]; break; } }
  if (!t) return;
  var depId = Number(cb.value);
  t.dependsOn = t.dependsOn || [];
  if (cb.checked) { if (t.dependsOn.indexOf(depId) < 0) t.dependsOn.push(depId); }
  else { t.dependsOn = t.dependsOn.filter(function (x) { return x !== depId; }); }
  if (hasCycle(t)) {
    if (cb.checked) { t.dependsOn = t.dependsOn.filter(function (x) { return x !== depId; }); }
    else { t.dependsOn.push(depId); }
    cb.checked = !cb.checked;
    showToast('此依賴會造成循環，已取消');
    return;
  }
  var s = item.querySelector('.p0-dep summary');
  if (s) s.textContent = '依賴前置任務 ' + t.dependsOn.length;
  renderTaskList(); scheduleSave();
});

function hasCycle(start) {
  var seen = {};
  function walk(id) {
    if (seen[id]) return false;
    seen[id] = 1;
    var t = null;
    for (var i = 0; i < tasks.length; i++) { if (tasks[i].id === id) { t = tasks[i]; break; } }
    if (!t) return false;
    var deps = t.dependsOn || [];
    for (var j = 0; j < deps.length; j++) {
      if (deps[j] === start.id) return true;
      if (walk(deps[j])) return true;
    }
    return false;
  }
  return walk(start.id);
}

function startEdit(item, t) {
  var desc = item.querySelector('.task-desc');
  if (!desc || item.querySelector('textarea')) return;
  var ta = document.createElement('textarea');
  ta.value = t.task;
  desc.parentNode.replaceChild(ta, desc);
  ta.focus();
  var done = false;
  function commit(save) {
    if (done) return; done = true;
    var v = ta.value.trim();
    if (save && v) { t.task = v; scheduleSave(); }
    renderTaskList();
  }
  ta.addEventListener('blur', function () { commit(true); });
  ta.addEventListener('keydown', function (e) {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) { e.preventDefault(); ta.blur(); }
    if (e.key === 'Escape') { done = true; renderTaskList(); }
  });
}

function retryTask(t) {
  if (isExecuting) { showToast('\u26A0\uFE0F 執行中，請稍候'); return; }
  t.status = 'waiting'; t.error = null;
  isExecuting = true;
  $btnRunAll.disabled = true;
  renderTaskList();
  window.runSingleTask(t, function () {
    isExecuting = false;
    $btnRunAll.disabled = false;
    renderTaskList();
    if (tasks.length && tasks.every(function (x) { return x.status === 'done'; })) maybeSynthesis(0);
  });
}

function paintCard(task, text, opts) {
  opts = opts || {};
  var card = byId(task.cardId);
  if (!card) return;
  card.classList.remove('running', 'done', 'failed');
  card.classList.add(opts.failed ? 'failed' : (opts.running ? 'running' : 'done'));
  var st = card.querySelector('.agent-status');
  if (st) {
    st.textContent = opts.failed ? '\u274C 失敗' : (opts.running ? '\uD83D\uDFE1 執行中…' : '\u2705 完成');
    st.className = 'agent-status ' + (opts.failed ? 'failed' : (opts.running ? 'running' : 'done'));
  }
  var out = byId(task.cardId + '-output');
  if (out) { out.textContent = text || ''; out.classList.remove('streaming-cursor'); }
  var old = card.querySelector('.agent-card-retry');
  if (old) old.parentNode.removeChild(old);
  if (opts.failed) {
    var rb = document.createElement('button');
    rb.type = 'button'; rb.className = 'agent-card-retry'; rb.textContent = '\u21BB 重試';
    rb.addEventListener('click', function (e) { e.stopPropagation(); retryTask(task); });
    var hdr = card.querySelector('.agent-card-header');
    if (hdr) hdr.appendChild(rb);
  }
  var cost = card.querySelector('.agent-card-cost');
  if (!cost) {
    cost = document.createElement('span');
    cost.className = 'agent-card-cost';
    var h2 = card.querySelector('.agent-card-header');
    if (h2) h2.appendChild(cost);
  }
  cost.textContent = (task.ms == null && !task.tokens) ? '' :
    (' | ' + fmtMs(task.ms) + ' | ~' + (task.tokens || 0) + ' tok' + (task.model && task.model !== '-' ? ' | ' + task.model : ''));
}

window.runSingleTask = function p0RunSingleTask(task, onComplete) {
  task.status = 'running';
  task.startedAt = nowMs();
  task.error = null;
  updateTaskUI(task);
  paintCard(task, getOutput(task), { running: true });
  var card = byId(task.cardId);
  if (card && card.scrollIntoView) { try { card.scrollIntoView({ behavior: 'smooth', block: 'center' }); } catch (e) {} }

  var accumulated = '';
  var resolved = false;
  var tokens = 0;
  var model = null;

  function finish(ok, errMsg) {
    if (resolved) return;
    resolved = true;
    socket.off('chat_stream', handler);
    task.ms = Math.round(nowMs() - (task.startedAt || nowMs()));
    task.tokens = tokens || estTokens(accumulated);
    task.model = model || '\u2014';
    task.status = ok ? 'done' : 'failed';
    task.error = ok ? null : (errMsg || '未知錯誤');
    agentOutputs[task.cardId] = accumulated;
    paintCard(task, accumulated, { failed: !ok });
    updateTaskUI(task);
    refreshBanner();
    ariaSay(task.agent + (ok ? ' 完成' : ' 失敗：' + task.error));
    scheduleSave();
    if (typeof onComplete === 'function') onComplete(task);
  }

  var handler = function (data) {
    if (resolved || !data) return;
    if (data.agent && data.agent !== task.agent) return;
    if (data.model) model = data.model;
    if (data.usage) tokens = data.usage.total_tokens || data.usage.completion_tokens || tokens;
    if (data.type === 'reply') {
      accumulated += (data.content || '');
      var out = byId(task.cardId + '-output');
      if (out) { out.textContent = accumulated; out.classList.add('streaming-cursor'); out.scrollTop = out.scrollHeight; }
    } else if (data.type === 'error') {
      finish(false, data.content || data.message || '後端錯誤');
    } else if (data.type === 'done') {
      finish(true);
    }
  };

  socket.on('chat_stream', handler);
  socket.emit('chat_message', {
    agent: task.agent,
    message: task.task,
    user_id: 'web_meeting',
    context_files: ['agent.md', 'user.md']
  });
  setTimeout(function () { if (!resolved) finish(false, '逾時（120s 未回應）'); }, 120000);
};

window.runAllTasks = function p0RunAllTasks() {
  if (!tasks.length) { showToast('無任務可執行'); return; }
  Object.keys(agentOutputs).forEach(function (k) { agentOutputs[k] = ''; });
  var outs = document.querySelectorAll('.agent-card-output');
  for (var i = 0; i < outs.length; i++) { outs[i].textContent = ''; outs[i].classList.remove('streaming-cursor'); }
  var cs = document.querySelectorAll('.agent-card');
  for (var j = 0; j < cs.length; j++) {
    cs[j].classList.remove('running', 'done', 'failed');
    var s = cs[j].querySelector('.agent-status');
    if (s) { s.textContent = '\uD83D\uDFE2 待命'; s.className = 'agent-status idle'; }
    var r = cs[j].querySelector('.agent-card-retry');
    if (r && r.parentNode) r.parentNode.removeChild(r);
    var co = cs[j].querySelector('.agent-card-cost');
    if (co) co.textContent = '';
  }
  banner.classList.remove('show');
  resetConclusion();

  tasks.forEach(function (t) { t.status = 'waiting'; t.error = null; t.ms = null; t.tokens = null; t.model = null; t.dependsOn = t.dependsOn || []; });
  renderTaskList();

  var cyc = tasks.filter(function (t) { return hasCycle(t); });
  if (cyc.length) {
    showToast('偵測到循環依賴，請先修正');
    cyc.forEach(function (t) { t.status = 'blocked'; t.error = '循環依賴'; });
    renderTaskList();
    return;
  }

  isExecuting = true;
  $btnRunAll.disabled = true;
  var qd = byId('btnQuickDraft'); if (qd) qd.disabled = true;
  var an = byId('btnAnalyze'); if (an) an.disabled = true;

  var total = tasks.length;
  var finished = 0;
  var running = 0;
  setStatus('DAG 排程啟動：' + total + ' 個任務');
  showToast('已派發 ' + total + ' 個任務（含依賴排序）');

  function findById(id) { for (var i = 0; i < tasks.length; i++) { if (tasks[i].id === id) return tasks[i]; } return null; }
  function ready(t) {
    if (t.status !== 'waiting') return false;
    var d = t.dependsOn || [];
    for (var i = 0; i < d.length; i++) { var p = findById(d[i]); if (p && p.status !== 'done') return false; }
    return true;
  }
  function blockedBy(t) {
    var d = t.dependsOn || [];
    for (var i = 0; i < d.length; i++) { var p = findById(d[i]); if (p && (p.status === 'failed' || p.status === 'blocked')) return true; }
    return false;
  }

  function pump() {
    var launched;
    do {
      launched = 0;
      for (var i = 0; i < tasks.length; i++) {
        var t = tasks[i];
        if (t.status === 'waiting' && blockedBy(t)) { t.status = 'blocked'; t.error = '依賴任務失敗'; finished++; updateTaskUI(t); continue; }
        if (ready(t)) {
          running++; launched++;
          (function (task) { runSingleTask(task, function () { running--; finished++; pump(); }); })(t);
        }
      }
    } while (launched > 0);

    if (running === 0 && finished < total) {
      tasks.forEach(function (t) { if (t.status === 'waiting') { t.status = 'blocked'; t.error = '無法排程（依賴未滿足）'; finished++; } });
      renderTaskList();
    }
    if (finished >= total && running === 0) { allFinished(); return; }
    setStatus('執行中… 完成 ' + finished + '/' + total + '（並行 ' + running + '）');
  }

  function allFinished() {
    isExecuting = false;
    $btnRunAll.disabled = false;
    if (qd) qd.disabled = false;
    if (an) an.disabled = false;
    var bad = tasks.filter(function (t) { return t.status === 'failed' || t.status === 'blocked'; }).length;
    setStatus((bad ? '完成（' + bad + ' 個失敗）' : '全部完成') + '：共 ' + total + ' 個任務');
    showToast(bad ? '有 ' + bad + ' 個任務未成功，可用「全部重試」' : '所有 Agent 任務完成！');
    renderTaskList();
    refreshBanner();
    ariaSay('全部任務結束');
    maybeSynthesis(bad);
  }

  pump();
};

function maybeSynthesis(bad) {
  var done = tasks.filter(function (t) { return t.status === 'done'; });
  if (bad) { setConcEmpty('有任務失敗，未自動彙整。修正後可手動按「產生結論」。'); return; }
  if (done.length >= 2) runSynthesis(true);
  else if (done.length === 1) setConcEmpty('只有一個任務，無需彙整；如需仍可按「產生結論」。');
}

var synthBusy = false;
function runSynthesis(auto) {
  if (synthBusy) { showToast('結論產生中，請稍候'); return; }
  var doneTasks = tasks.filter(function (t) { return t.status === 'done'; });
  if (!doneTasks.length) { showToast('尚無完成的任務'); return; }
  var sel = byId('analyzerSelect');
  var synthAgent = (sel && sel.value) ? sel.value : doneTasks[0].agent;
  var body = doneTasks.map(function (t) {
    return '### ' + t.agent + '（任務：' + t.task + '，耗時 ' + fmtMs(t.ms) + '）\n' + String(getOutput(t)).slice(0, 6000);
  }).join('\n\n---\n\n');

  var prompt = '你是本次多 Agent 會議的總指揮。以下各 Agent 的輸出是唯一事實來源。\n' +
    '請用繁體中文 Markdown 輸出，且嚴格遵守：不得引入未出現的資訊、不得捏造共識；若某結論只有單一 Agent 提出，需標註其來源。\n' +
    '固定輸出四段（標題需完全一致）：\n' +
    '## 一、會議結論\n（3～7 條列點，每條末尾標註來源 Agent）\n' +
    '## 二、分歧與衝突\n（逐條列出 Agent 之間互相矛盾的說法；若無，明確寫「無明顯衝突」）\n' +
    '## 三、待辦事項\n（可執行下一步，標註建議負責 Agent）\n' +
    '## 四、風險與未解問題\n（資訊缺口與需要人類決策之處）\n\n' +
    '====== 各 Agent 原始輸出 ======\n' + body;

  synthBusy = true;
  concBadge.hidden = false;
  concBadge.textContent = 'AI 彙整中…';
  concBody.innerHTML = '<div class="p0-empty">總指揮彙整中…</div>';
  setStatus('總指揮彙整會議結論中…');
  ariaSay('開始彙整會議結論');

  var acc = '';
  var resolved = false;
  var t0 = nowMs();

  function finish(ok, err) {
    if (resolved) return;
    resolved = true;
    socket.off('chat_stream', handler);
    synthBusy = false;
    if (ok && acc.trim()) {
      conclusionText = acc;
      concBody.innerHTML = mdSafe(acc);
      concBadge.textContent = 'AI 彙整 · ' + fmtMs(Math.round(nowMs() - t0));
      setStatus('會議結論已生成');
      showToast('會議結論已生成');
      ariaSay('會議結論已生成');
      scheduleSave();
    } else {
      concBadge.hidden = true;
      concBody.innerHTML = '<div class="p0-empty">彙整失敗：' + esc(err || '未知錯誤') + '</div>';
      setStatus('彙整失敗');
    }
  }

  var handler = function (data) {
    if (resolved || !data) return;
    if (data.agent && data.agent !== synthAgent) return;
    if (data.type === 'reply') {
      acc += (data.content || '');
      concBody.innerHTML = mdSafe(acc);
      concBody.scrollTop = concBody.scrollHeight;
    } else if (data.type === 'error') {
      finish(false, data.content || data.message || '後端錯誤');
    } else if (data.type === 'done') {
      finish(true);
    }
  };

  socket.on('chat_stream', handler);
  socket.emit('chat_message', {
    agent: synthAgent,
    message: prompt,
    user_id: 'web_meeting',
    context_files: ['agent.md', 'user.md']
  });
  setTimeout(function () { if (!resolved) finish(false, '逾時（180s 未回應）'); }, 180000);
}

function buildMarkdown() {
  var L = [];
  L.push('# 會議紀錄');
  L.push('');
  L.push('- 時間：' + new Date().toLocaleString());
  L.push('- 議題：' + ((byId('meetingInput') || {}).value || '(未記錄)'));
  L.push('- 參與 Agent：' + tasks.map(function (t) { return t.agent; }).join('、'));
  L.push('');
  L.push('## 一、會議結論（總指揮彙整）');
  L.push('');
  L.push(conclusionText || '_（尚未產生結論）_');
  L.push('');
  L.push('## 二、各 Agent 輸出明細');
  tasks.forEach(function (t) {
    L.push('');
    L.push('### ' + t.agent + ' — ' + (STATUS[t.status] || t.status));
    L.push('- 任務：' + t.task);
    L.push('- 成本：模型 ' + (t.model || '\u2014') + '｜耗時 ' + fmtMs(t.ms) + '｜token ~' + (t.tokens || 0));
    if (t.dependsOn && t.dependsOn.length) L.push('- 依賴：' + t.dependsOn.map(function (i) { return '#' + i; }).join(', '));
    if (t.error) L.push('- 錯誤：' + t.error);
    L.push('');
    L.push(String(getOutput(t) || '（無輸出）'));
  });
  return L.join('\n');
}

conclusion.addEventListener('click', function (ev) {
  var b = (ev.target && ev.target.closest) ? ev.target.closest('button[data-act]') : null;
  if (!b) return;
  var act = b.getAttribute('data-act');
  if (act === 'synth') { runSynthesis(false); return; }
  if (act === 'copy') {
    var txt = conclusionText || buildMarkdown();
    if (navigator.clipboard && navigator.clipboard.writeText) {
      navigator.clipboard.writeText(txt).then(function () { showToast('已複製到剪貼簿'); },
        function () { showToast('複製失敗，請手動選取'); });
    } else { showToast('此瀏覽器不支援自動複製'); }
    return;
  }
  if (act === 'export') {
    var blob = new Blob([buildMarkdown()], { type: 'text/markdown;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'meeting_minutes_' + new Date().toISOString().slice(0, 19).replace(/[:T]/g, '') + '.md';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 2000);
    showToast('已匯出 Markdown 會議紀錄');
  }
});

function restoreSnapshot() {
  var raw = null;
  try { raw = localStorage.getItem(LS_KEY); } catch (e) { return false; }
  if (!raw) return false;
  var s = null;
  try { s = JSON.parse(raw); } catch (e) { return false; }
  if (!s || !s.tasks || !s.tasks.length) return false;
  var mi = byId('meetingInput');
  if (mi && s.input) mi.value = s.input;
  if (s.selected && s.selected.length) {
    selectedAgents.clear();
    s.selected.forEach(function (a) { if (agentList.indexOf(a) >= 0) selectedAgents.add(a); });
    if (typeof renderAgentSelection === 'function') renderAgentSelection();
  }
  tasks = s.tasks.map(function (t) {
    var nt = {
      id: t.id, agent: t.agent, task: t.task, priority: t.priority,
      status: (t.status === 'running' ? 'waiting' : (t.status || 'waiting')),
      dependsOn: (t.dependsOn || []).slice(),
      model: t.model || null, ms: (t.ms == null ? null : t.ms), tokens: t.tokens || null,
      error: t.error || null, output: t.output || '',
      cardId: agentCardMap[t.agent] || null
    };
    if (nt.cardId) agentOutputs[nt.cardId] = t.output || '';
    return nt;
  });
  var mx = taskIdCounter || 0;
  tasks.forEach(function (t) { if (t.id > mx) mx = t.id; });
  taskIdCounter = mx;
  tasks.forEach(function (t) { if (t.cardId) paintCard(t, getOutput(t), { failed: t.status === 'failed' }); });
  if (s.conclusion) {
    conclusionText = s.conclusion;
    concBody.innerHTML = mdSafe(s.conclusion);
    concBadge.hidden = false;
    concBadge.textContent = 'AI 彙整（已還原）';
  }
  renderTaskList();
  refreshBanner();
  showToast('已還原上次會議進度');
  return true;
}

var restoreTries = 0;
function tryRestore() {
  if (Object.keys(agentCardMap).length > 0) { restoreSnapshot(); return; }
  if (++restoreTries > 40) return;
  setTimeout(tryRestore, 300);
}

if (typeof window.parseAndCreateTasks === 'function') {
  var origParse = window.parseAndCreateTasks;
  window.parseAndCreateTasks = function () {
    var r = origParse.apply(this, arguments);
    tasks.forEach(function (t) { t.dependsOn = t.dependsOn || []; });
    resetConclusion();
    renderTaskList();
    scheduleSave();
    return r;
  };
}

var btnRun = byId('btnRunAll');
if (btnRun && btnRun.parentNode) {
  btnRun.parentNode.addEventListener('click', function (ev) {
    var t = ev.target;
    var hit = t && ((t.id === 'btnRunAll') || (t.closest && t.closest('#btnRunAll')));
    if (!hit) return;
    ev.preventDefault();
    ev.stopPropagation();
    if (btnRun.disabled) return;
    window.runAllTasks();
  }, true);
}

var mi2 = byId('meetingInput');
if (mi2) mi2.addEventListener('change', function () { scheduleSave(); });

setTimeout(tryRestore, 400);
console.log('[p0] 會議模式增強模組已載入');

})();
