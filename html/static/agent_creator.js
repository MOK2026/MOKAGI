/**
 * 🏗️ Agent 建立器 — 模態對話框 + 角色選擇
 * 搭配 index.html 中的 #createAgentModal 使用
 */

// ===== 全局狀態 =====
let allRoles = [];           // 所有角色
let selectedRoleUrl = '';    // 當前選中的角色 raw URL

// ===== Modal 控制 =====
function showCreateAgentModal() {
    const modal = document.getElementById('createAgentModal');
    if (!modal) return;
    modal.style.display = 'flex';
    document.getElementById('newAgentName').value = '';
    document.getElementById('roleSearchInput').value = '';
    document.getElementById('roleSelect').value = '';
    document.getElementById('roleInfo').style.display = 'none';
    selectedRoleUrl = '';
    document.getElementById('newAgentName').focus();
    // 載入角色列表（若尚未載入）
    if (allRoles.length === 0) {
        loadAgencyRoles();
    }
}

function hideCreateAgentModal() {
    const modal = document.getElementById('createAgentModal');
    if (modal) modal.style.display = 'none';
}

// ===== 載入角色 =====
async function loadAgencyRoles() {
    const loadingEl = document.getElementById('roleLoading');
    const selectEl = document.getElementById('roleSelect');
    if (loadingEl) loadingEl.style.display = 'block';
    
    try {
        const res = await fetch('/api/agency_roles');
        const data = await res.json();
        if (data.status === 'ok' && data.roles) {
            allRoles = data.roles;
            renderRoleOptions(allRoles);
        } else {
            selectEl.innerHTML = '<option value="">-- 無法載入角色列表 --</option>';
        }
    } catch (e) {
        console.error('載入角色失敗:', e);
        selectEl.innerHTML = '<option value="">-- 載入失敗，請稍後再試 --</option>';
    }
    if (loadingEl) loadingEl.style.display = 'none';
}

function renderRoleOptions(roles) {
    const selectEl = document.getElementById('roleSelect');
    if (!selectEl) return;
    
    // 保留第一個「不選角色」選項
    let html = '<option value="">-- 不選角色（建立空白 agent.md）--</option>';
    
    // 按 division 分組
    const grouped = {};
    roles.forEach(r => {
        const div = r.division || '其他';
        if (!grouped[div]) grouped[div] = [];
        grouped[div].push(r);
    });
    
    for (const [div, divRoles] of Object.entries(grouped)) {
        html += `<optgroup label="${div}">`;
        divRoles.forEach(r => {
            html += `<option value="${r.raw_url}" data-id="${r.id}">${r.name} — ${r.specialty}</option>`;
        });
        html += '</optgroup>';
    }
    
    selectEl.innerHTML = html;
    
    // 綁定選擇事件
    selectEl.onchange = function() {
        const selected = this.options[this.selectedIndex];
        if (selected.value) {
            selectedRoleUrl = selected.value;
            showRoleInfo(selected.text, selected.getAttribute('data-id'));
        } else {
            selectedRoleUrl = '';
            document.getElementById('roleInfo').style.display = 'none';
        }
    };
}

function showRoleInfo(name, id) {
    const infoEl = document.getElementById('roleInfo');
    if (infoEl) {
        infoEl.style.display = 'block';
        infoEl.innerHTML = `✅ 已選擇：<strong>${name}</strong><br><small>角色 .md 將自動下載並寫入 soul/agent.md</small>`;
    }
}

// ===== 搜尋過濾 =====
document.addEventListener('DOMContentLoaded', () => {
    const searchInput = document.getElementById('roleSearchInput');
    if (searchInput) {
        searchInput.addEventListener('input', function() {
            const query = this.value.toLowerCase().trim();
            if (!query) {
                renderRoleOptions(allRoles);
                return;
            }
            const filtered = allRoles.filter(r => 
                r.name.toLowerCase().includes(query) ||
                r.specialty.toLowerCase().includes(query) ||
                (r.division && r.division.toLowerCase().includes(query))
            );
            renderRoleOptions(filtered);
        });
    }
});

// ===== 建立 Agent =====
async function doCreateAgent() {
    const nameInput = document.getElementById('newAgentName');
    const name = nameInput.value.trim();
    
    if (!name) {
        alert('請輸入 Agent 名字');
        nameInput.focus();
        return;
    }
    
    if (!/^[a-zA-Z0-9\u4e00-\u9fa5_-]+$/.test(name)) {
        alert('名字只能包含字母、數字、中文、下劃線和中劃線');
        nameInput.focus();
        return;
    }
    
    const confirmBtn = document.getElementById('createAgentConfirm');
    confirmBtn.disabled = true;
    confirmBtn.textContent = '⏳ 建立中...';
    
    try {
        const body = { name: name };
        if (selectedRoleUrl) {
            body.role_url = selectedRoleUrl;
        }
        
        const res = await fetch('/api/create_agent', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body)
        });
        
        const data = await res.json();
        
        if (data.status === 'ok') {
            const extraMsg = data.agent_md_created ? '\n✅ 角色模板已寫入 soul/agent.md' : '\n📝 已建立空白 soul/agent.md';
            alert(data.message + extraMsg);
            hideCreateAgentModal();
            location.reload();
        } else {
            alert('❌ ' + data.message);
        }
    } catch (e) {
        console.error('建立 Agent 失敗:', e);
        alert('❌ 建立失敗: ' + e.message);
    }
    
    confirmBtn.disabled = false;
    confirmBtn.textContent = '✨ 建立 Agent';
}

// ===== 事件綁定 =====
function bindCreateAgentEvents() {
    // 打開模態框
    const createBtn = document.getElementById('createQiBtn');
    if (createBtn) {
        // 移除舊監聽器
        const newBtn = createBtn.cloneNode(true);
        createBtn.parentNode.replaceChild(newBtn, createBtn);
        newBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            showCreateAgentModal();
        });
    }
    
    // 關閉按鈕
    document.getElementById('createAgentClose')?.addEventListener('click', hideCreateAgentModal);
    document.getElementById('createAgentCancel')?.addEventListener('click', hideCreateAgentModal);
    
    // 點擊 overlay 關閉
    document.getElementById('createAgentOverlay')?.addEventListener('click', hideCreateAgentModal);
    
    // 確認建立
    document.getElementById('createAgentConfirm')?.addEventListener('click', doCreateAgent);
    
    // Enter 鍵送出
    document.getElementById('newAgentName')?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter') {
            e.preventDefault();
            doCreateAgent();
        }
    });
    
    // ESC 關閉
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape') {
            const modal = document.getElementById('createAgentModal');
            if (modal && modal.style.display === 'flex') {
                hideCreateAgentModal();
            }
        }
    });
}

// 初始化（等 DOM 就緒）
if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bindCreateAgentEvents);
} else {
    bindCreateAgentEvents();
}

console.log('✅ agent_creator.js 已載入');
