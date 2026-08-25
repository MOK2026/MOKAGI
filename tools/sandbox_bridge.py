"""Archived JavaScript accidentally stored as a Python tool.

This file is intentionally disabled until the JavaScript is moved to its proper
frontend asset. Keeping the source here prevents the tool loader from failing
every time mok_web starts.

Original source begins below.
"""

'''
// ============ CONSTANTS & STATE ============
const AGENTS_DATA = [
{"name":"倉儲管家","icon":"🏭","post":"智慧倉儲顧問","intro":"AI庫存解析專家，自然語言查貨、揀貨路徑優化，秒級定位目標商品","color":0xffa500,"atkInterval":1.3,"atkSpeed":0.20,"counterRate":0.25,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":12,"battleTime":5},
{"name":"商拓專員","icon":"📧","post":"B2B智能拓展","intro":"AI驅動Cold Email自動化投放，精準客戶研究與銷售線索開發","color":0x00bfff,"atkInterval":1.0,"atkSpeed":0.35,"counterRate":0.20,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":9,"battleTime":5},
{"name":"人資顧問","icon":"👥","post":"人力資源顧問","intro":"智能排班、履歷篩選、員工效能分析，優化人力配置與管理效率","color":0x9370db,"atkInterval":1.1,"atkSpeed":0.25,"counterRate":0.30,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":10,"battleTime":5},
{"name":"採購專家","icon":"🛒","post":"智能採購專家","intro":"自動比價、供應商評估、訂單跟進，降低採購成本與流程時間","color":0x3cb371,"atkInterval":0.9,"atkSpeed":0.30,"counterRate":0.30,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":8,"battleTime":4},
{"name":"庫存分析師","icon":"📦","post":"庫存預測分析師","intro":"AI自動解析庫存數據、預測補貨時機，減少囤貨積壓與缺貨損失","color":0xcd853f,"atkInterval":1.2,"atkSpeed":0.25,"counterRate":0.35,"normalDmg":2,"blockDmg":1,"counterDmg":4,"hp":10,"battleTime":5},
{"name":"流程診斷師","icon":"🔍","post":"業務流程診斷師","intro":"找出企業最大虧損點與瓶頸，實地走訪分析，定位核心問題","color":0xdc143c,"atkInterval":0.8,"atkSpeed":0.40,"counterRate":0.25,"normalDmg":3,"blockDmg":1,"counterDmg":4,"hp":7,"battleTime":4},
{"name":"AI整合師","icon":"🔗","post":"AI整合架構師","intro":"按成本優化工作流程，將AI技術實際融入業務，實現落地部署","color":0x7b68ee,"atkInterval":1.0,"atkSpeed":0.30,"counterRate":0.35,"normalDmg":3,"blockDmg":1,"counterDmg":4,"hp":9,"battleTime":5},
{"name":"合規顧問","icon":"⚖️","post":"AI法規合規顧問","intro":"EU AI Act合規評估、數據隱私保護、高風險系統風險管理","color":0x4169e1,"atkInterval":1.5,"atkSpeed":0.20,"counterRate":0.20,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":14,"battleTime":6},
{"name":"數據分析師","icon":"📊","post":"商業數據分析師","intro":"數據可視化、營運報表、BI決策支援，讓數據驅動企業成長","color":0x20b2aa,"atkInterval":0.7,"atkSpeed":0.45,"counterRate":0.30,"normalDmg":3,"blockDmg":1,"counterDmg":4,"hp":6,"battleTime":4},
{"name":"策略顧問","icon":"🎯","post":"商業策略顧問","intro":"競爭分析、市場進入策略、數位轉型，創新科技+商業生態網絡=解決問題","color":0xdaa520,"atkInterval":1.4,"atkSpeed":0.30,"counterRate":0.40,"normalDmg":4,"blockDmg":2,"counterDmg":5,"hp":11,"battleTime":6}
];



function toCSSColor(hex){const r=(hex>>16)&255,g=(hex>>8)&255,b=hex&255;return `rgb(${r},${g},${b})`;}
function hexToGL(hex){return[(hex>>16&255)/255,(hex>>8&255)/255,(hex&255)/255];}
// 莫氏總部面向 = pos:[+左-右,高,前=+,後=-] [+-18,0,]
const BUILDINGS = [
  {name:"莫氏總部",desc:"莫氏集團核心 · 2006年成立於香港",url:"https://64071181.github.io/",pos:[0,0,-2.5],color:0x1a3a5c,w:3.5,h:6,d:3.5},
  {name:"AI研發中心",desc:"人工智能應用研究設施",url:"/project/",pos:[6,0,5],color:0x0d2b4a,w:2.5,h:3.5,d:2.5},
  {name:"格鬥競技場",desc:"🏆 排行榜 · 點擊查看戰績",url:"",pos:[15,0,-12],color:0x3a0a1a,w:3,h:2.5,d:3},
  {name:"💎金剛石散熱",desc:"生物鑽石散熱技術 · 頭髮回收→鑽石粉末→AI晶片散熱 · 熱導率2200W/m·K",url:"/project/髮變鑽石",pos:[-17,0,-4],color:0x2a1a5c,w:2.2,h:3,d:2.2},
  {name:"🤑賺錢鍠中心",desc:"自動賺錢完美閉環 · AI自動收集客戶+發送促銷Email+WhatsApp客服",url:"https://64071181.github.io/index2025",pos:[20,0,-20],color:0x4a3a0a,w:2.2,h:3,d:2.2},
  {name:"商業教育部",desc:"📚 商業知識與技能培訓學院",url:"",pos:[-13,0,8],color:0x1a2a2a,w:2,h:2.5,d:2},
  {name:"天使投資站",desc:"👼 初創企業資金與資源支持平臺",url:"https://www.facebook.com/groups/1619118991433947",pos:[1,15,5],color:0x3a2a1a,w:2,h:2.5,d:2},
  {name:"資源匹配廳",desc:"🔗 企業資源對接與商務合作",url:"https://www.facebook.com/groups/440828339624274",pos:[-9,0,18],color:0x2a1a3a,w:2.2,h:2.5,d:2.2},
  {name:"特許經營所",desc:"🏪 品牌特許經營與合作加盟選項",url:"https://www.facebook.com/groups/441454859557899",pos:[-8,0,13],color:0x1a3a2a,w:2,h:2,d:2},
  {name:"營銷推廣部",desc:"📢 全方位營銷與品牌推廣管道",url:"",pos:[10,0,20],color:0x3a1a1a,w:2,h:2.5,d:2},
  {name:"交流中心",desc:"🤝 企業交流與商務互動活動",url:"https://www.facebook.com/groups/440828339624274",pos:[7,0,16],color:0x1a1a3a,w:2,h:2.5,d:2},
  {name:"人力資源所",desc:"👥 人才招募與人力資源服務",url:"https://64071181.github.io/康力人力資源顧問公司/",pos:[-8,0,-11],color:0x2a2a1a,w:2,h:2.5,d:2},
  {name:"技術轉型站",desc:"🔧 企業技術轉型支援與顧問",url:"/project/",pos:[-5,0,7],color:0x1a2a3a,w:2,h:3,d:2},
  {name:"業務轉移臺",desc:"🔄 業務轉移與企業交易平臺",url:"https://www.facebook.com/groups/310530886427027",pos:[0,0,-10],color:0x2a1a2a,w:2,h:2,d:2},
];
const BILLBOARDS = [
  {text:"⚔️ 格鬥大賽進行中！",pos:[-6,2.5,-2],rot:0.3},
  {text:"💎 新玩家送好禮",pos:[6,2.5,-1],rot:-0.3},
  {text:"🏆 排行榜 TOP10",pos:[0,3.5,5],rot:3.14},
];


let user_setting = [
  {text:"水平拖曳 → 水平旋轉", nb:0.05},
  {text:"垂直拖曳 → 俯仰角度", nb:0.01},
  {text:"滾輪縮放靈敏度", nb:0.06},
  {text:"自動旋轉速度", nb:0.01},
  {text:"相機平滑阻尼", nb:0.32},
  {text:"拖曳觸發閾值(px)", nb:1.5},
  {text:"自動旋轉恢復延遲(ms)", nb:3000},
  {text:"最小鏡頭距離", nb:6},
  {text:"最大鏡頭距離", nb:50},
  {text:"最小俯仰角", nb:0.2},
  {text:"最大俯仰角", nb:1.4137},
  {text:"敵人AI攻擊機率", nb:0.3},
  {text:"敵人反擊機率", nb:0.25},
  {text:"普通攻擊傷害", nb:2},
  {text:"格擋傷害", nb:1},
  {text:"反擊傷害", nb:3},
  {text:"玩家/敵人血量", nb:10},
  {text:"戰鬥時間(秒)", nb:5}
];


let mokagi_storyText = ['在遙遠的未來，人類創造了擁有自主意識的 AI 村民。<br><br>莫氏集團建立了 <b style="color:#ffd700">莫氏 AI 村莊</b>——一個 AI 與人類共存的和諧社區。<br><br> <b style="color:#ffd700">村莊有各種專家，妳可以在這裡購物、交友、遊戲、查詢問題</b><br><br>然而，村莊中並非所有村民都友善…有些村民因代碼異常而變得具有攻擊性。<br><br>作為莫氏集團的特派員，你的任務是：<b style="color:#00f0ff">與 AI 村民互動、對話交流、並在必要時以格鬥保衛村莊秩序</b>。<br><br>⚔️ <b>戰鬥系統</b>：觀察敵人攻擊方向，打出剋制方向來反擊！<br>💬 <b>對話系統</b>：點擊 AI 村民可進行對話<br>⬆ <b>技能升級</b>：每擊敗一個 AI，獲得 1 點技能點數']



let WelcomeTxt = "購物 | 交友 | 遊戲"
// ============ 世界地圖 & 角色簡介 面板（3 分頁） ============
function switchTab(tabName) {
  document.querySelectorAll(".wm-tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".wm-page").forEach(p => p.classList.remove("active"));
  const tabBtn = document.querySelector(".wm-tab[data-tab=\"" + tabName + "\"]");
  const page = document.getElementById("wm-page-" + tabName);
  if (tabBtn) tabBtn.classList.add("active");
  if (page) page.classList.add("active");
}

function updateCharacterPanel(agentName) {
  // ── 戰績（從 score-text 讀取） ──
  const st = document.getElementById("score-text");
  const wins = st ? (parseInt((st.textContent||"").match(/(\d+)\s*勝/)?.[1])||0) : 0;
  const losses = st ? (parseInt((st.textContent||"").match(/(\d+)\s*敗/)?.[1])||0) : 0;
  const total = wins + losses;
  const rate = total > 0 ? Math.round((wins / total) * 100) + "%" : "-";
  const crWins = document.getElementById("cr-wins");
  const crLosses = document.getElementById("cr-losses");
  const crRate = document.getElementById("cr-rate");
  if (crWins) crWins.textContent = wins;
  if (crLosses) crLosses.textContent = losses;
  if (crRate) crRate.textContent = rate;

  // ── 角色資料（從 AGENTS_DATA 讀取） ──
  let agent = null;
  if (agentName) {
    agent = AGENTS_DATA.find(a => a.name === agentName);
  }
  if (!agent) agent = AGENTS_DATA[0]; // 預設第一位

  const nameEl = document.getElementById("char-name-display");
  const titleEl = document.getElementById("char-title-display");
  const avatarEl = document.querySelector(".char-avatar-big");
  const atkEl = document.getElementById("cs-atk");
  const defEl = document.getElementById("cs-def");
  const hpEl = document.getElementById("cs-hp");
  const spdEl = document.getElementById("cs-spd");

  if (nameEl) nameEl.textContent = agent.icon + " " + agent.name;
  if (titleEl) titleEl.textContent = "莫氏集團 · " + agent.post;
  if (avatarEl) avatarEl.textContent = agent.icon;
  if (atkEl) atkEl.textContent = agent.normalDmg;
  if (defEl) defEl.textContent = agent.blockDmg;
  if (hpEl) hpEl.textContent = agent.hp;
  if (spdEl) spdEl.textContent = agent.counterDmg;
}

function initPanels() {
  const wmList = document.getElementById('world-map-list');
  if (wmList) {
    wmList.innerHTML = BUILDINGS.map(b => 
      `<div class="wm-card" style="border-left:4px solid ${toCSSColor(b.color)}">
        <div class="wm-name">${b.name}</div>
        <div class="wm-desc">${b.desc}</div>
        ${b.url ? `<a class="wm-link" href="${b.url}" target="_blank">🔗 前往</a>` : ''}
      </div>`
    ).join('');
  }

  const aiList = document.getElementById('agent-intro-list');
  if (aiList) {
    aiList.innerHTML = AGENTS_DATA.map(a =>
      `<div class="ai-card" style="border-left:4px solid ${toCSSColor(a.color)}">
        <div class="ai-name">${a.icon} ${a.name} <span class="ai-post">${a.post}</span></div>
        <div class="ai-intro">${a.intro||''}</div>
        <div class="ai-stats">⚔️${a.normalDmg} 🛡${a.blockDmg} ⚡${a.counterDmg} ❤️${a.hp} ⏱${a.battleTime}s</div>
      </div>`
    ).join('');
  }

  // Tab 切換事件
  document.querySelectorAll(".wm-tab").forEach(tab => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });

  // 初始化角色面板
  updateCharacterPanel();

  // 點擊 score-board → 打開世界地圖
  const sb = document.getElementById("score-board");
  const wm = document.getElementById("world-map-panel");
  if (sb && wm) {
    sb.style.cursor = "pointer";
    sb.addEventListener("click", () => {
      if (wm.classList.contains("show")) { wm.classList.remove("show"); return; }
      updateCharacterPanel();
      wm.classList.add("show");
    });
  }
  // 關閉按鈕
  const wmClose = document.getElementById('world-map-close');
  if (wmClose) wmClose.addEventListener('click', () => wm && wm.classList.remove('show'));
  // 雙擊任意 AI 村民 → 切換到 AGENTS 分頁 + 更新角色面板
  document.addEventListener("dblclick", (e) => {
    const tag = document.getElementById("agent-name-tag");
    if (tag && tag.style.display !== "none" && tag.textContent) {
      const name = tag.textContent.trim();
      const agent = AGENTS_DATA.find(a => a.name === name);
      if (!agent) return;
      if (!wm.classList.contains("show")) wm.classList.add("show");
      updateCharacterPanel(agent.name); // 同步更新角色面板
      switchTab("agents");
      setTimeout(() => {
        const cards = document.querySelectorAll("#agent-intro-list .ai-card");
        cards.forEach(card => {
          if (card.textContent.includes(agent.name)) card.scrollIntoView({behavior:"smooth",block:"center"});
        });
      }, 200);
    }
  });
}

// DOM ready 後初始化
if (document.readyState === 'loading') {
  document.addEventListener('DOMContentLoaded', initPanels);
} else {
  initPanels();
}
'''
