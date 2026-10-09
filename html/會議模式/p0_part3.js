
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
