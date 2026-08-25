    // 🔧 渲染輪次分組區塊到 assistant div
    function _renderRoundBlocks(agent) {
        if (!rounds[agent] || !rounds[agent].length) return;
        if (agent !== currentAgent) return;
        var msgList = document.getElementById('message-list');
        if (!msgList) return;
        var assistantDiv = currentAssistantDiv[agent];
        if (!assistantDiv || !document.contains(assistantDiv)) {
            assistantDiv = document.createElement('div');
            assistantDiv.className = 'message assistant';
            var agenticon = agentIcons[agent] || '🌸';
            assistantDiv.innerHTML = '<div class="round-blocks-container"></div><div class="message-meta">' + agenticon + ' ' + agent + ' · 工作中...</div>';
            assistantDiv.dataset.streamGen = streamGen[agent] || 0;
            msgList.appendChild(assistantDiv);
            currentAssistantDiv[agent] = assistantDiv;
        }
        var container = assistantDiv.querySelector('.round-blocks-container');
        if (!container) return;
        var html = '';
        for (var i = 0; i < rounds[agent].length; i++) {
            var r = rounds[agent][i];
            var isLast = (i === rounds[agent].length - 1);
            var isActive = (i === currentRoundIdx[agent]);
            var rNum = r.iteration || (i + 1);
            html += '<div class="round-block" style="margin-bottom:10px;border:1px solid #3e3e42;border-radius:8px;overflow:hidden;">';
            html += '<div style="background:#252526;padding:6px 12px;font-size:0.8rem;color:#e0a800;display:flex;justify-content:space-between;">';
            html += '<span>🔄 第' + rNum + '輪 ' + (isActive ? '進行中' : '完成') + '</span>';
            if (r.tool_calls && r.tool_calls.length) {
                html += '<span style="font-size:0.7rem;color:#90949f;">' + r.tool_calls.map(function(t){return t.name}).join(', ') + '</span>';
            }
            html += '</div>';
            if (r.think) {
                html += '<details style="background:#1a1a1d;padding:6px 12px;" ' + (isActive&&isLast?'open':'') + '>';
                html += '<summary style="color:#a09060;font-size:0.8rem;">思考</summary>';
                html += '<div style="white-space:pre-wrap;font-size:0.85rem;color:#c0c0c0;max-height:200px;overflow-y:auto;">' + escapeHtml(r.think) + '</div>';
                html += '</details>';
            }
            if (r.tool_calls && r.tool_calls.length) {
                html += '<details style="background:#1a1a1d;padding:6px 12px;" ' + (isActive&&isLast?'open':'') + '>';
                html += '<summary style="color:#5b9bd5;font-size:0.8rem;">使用工具 (' + r.tool_calls.length + ')</summary>';
                for (var j = 0; j < r.tool_calls.length; j++) {
                    var tc = r.tool_calls[j];
                    html += '<div style="margin:4px 0;padding:6px;background:#1e2a3a;border-radius:4px;font-size:0.8rem;">';
                    html += '<span style="color:#5b9bd5;">' + escapeHtml(tc.name) + '</span>';
                    html += '<div style="color:#90949f;max-height:80px;overflow-y:auto;">' + escapeHtml(JSON.stringify(tc.arguments,null,2)) + '</div></div>';
                }
                html += '</details>';
            }
            if (r.tool_results && r.tool_results.length) {
                html += '<details style="background:#1a1a1d;padding:6px 12px;" ' + (isActive&&isLast?'open':'') + '>';
                html += '<summary style="color:#00b894;font-size:0.8rem;">工具結果 (' + r.tool_results.length + ')</summary>';
                for (var k = 0; k < r.tool_results.length; k++) {
                    var tr = r.tool_results[k];
                    html += '<div style="margin:4px 0;padding:6px;background:#1a2e1a;border-radius:4px;font-size:0.8rem;">';
                    html += '<span style="color:#00b894;">' + escapeHtml(tr.name) + '</span>';
                    html += '<div style="color:#b0b0b0;max-height:150px;overflow-y:auto;">' + escapeHtml(String(tr.content).substring(0,800)) + '</div></div>';
                }
                html += '</details>';
            }
            if (r.reply) {
                html += '<div style="padding:8px 12px;font-size:0.9rem;color:#e4e4e7;white-space:pre-wrap;">' + renderPlainTextWithFold(r.reply) + '</div>';
            }
            html += '</div>';
        }
        container.innerHTML = html;
        scrollToBottom();
    }

