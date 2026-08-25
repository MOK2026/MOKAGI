// ============ CONSTANTS & STATE ============
const AGENTS_DATA = [


{"name":"客服","lv":1,"icon":"🤖","post":"莫氏客服","intro":"你可以和我聊天"
,"skill":"進化：可獲得更多能力..","welcome":"「很普通但很多朋友的ai客服」","color":0xff4444,"atkInterval":1.3,"atkSpeed":0.20,"counterRate":0.25,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":10,"battleTime":5},


{"name":"康力人資大亨","lv":2,"icon":"👥","post":"康力人力資源顧問公司老闆","intro":"人力資源、工作安全、勞工法、招聘、求職"
,"skill":"招募：全屬性+10（上傳 soul.md 開新 agent=mokagi 客服 api，每次扣 token）","welcome":"「勞工法我最熟…想找人？想找工作？先過我這關！（正在吹噓招聘戰績…被打斷）」","color":0x4169e1,"atkInterval":1.1,"atkSpeed":0.25,"counterRate":0.30,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":13,"battleTime":5},

{"name":"養生銀髮大亨","lv":3,"icon":"🧓","post":"全球銀髮市場最大集團老闆","intro":"養生及健康：養生、穴位、健身、營養達人"
,"skill":"永生：被最後一擊會留一血","welcome":"「真實養生資訊？先聽我吹…（正在寫招募銀髮市場合作商的書法字…被打斷）關注獨居長者、窮苦老人問題！」","color":0x9ac0cd,"atkInterval":1.5,"atkSpeed":0.25,"counterRate":0.35,"normalDmg":4,"blockDmg":2,"counterDmg":5,"hp":16,"battleTime":6},

{"name":"飲食業霸主","lv":4,"icon":"🍜","post":"飲食業入口統治者","intro":"飲食業、餐廳、食品供應鏈"
,"skill":"大快朵頤：血量恢復+100%","welcome":"「吃飽再戰！我的部隊從不餓肚子…」","color":0xff8c00,"atkInterval":1.0,"atkSpeed":0.30,"counterRate":0.35,"normalDmg":3,"blockDmg":2,"counterDmg":5,"hp":12,"battleTime":5},


{"name":"小販之王","lv":5,"icon":"🧺","post":"小商販地下帝王","intro":"小商販、地攤經濟、供貨鏈"
,"skill":"薄利多銷：攻擊+100","welcome":"「要貨？我有！要客？我也有！…先交保護費（戰鬥）！」","color":0xcd853f,"atkInterval":0.9,"atkSpeed":0.35,"counterRate":0.35,"normalDmg":3,"blockDmg":1,"counterDmg":4,"hp":9,"battleTime":4},


{"name":"AI接入大魔王","lv":6,"icon":"🔌","post":"商用AI接入統治者","intro":"AI接入、API整合、工作流自動化、客服AI"
,"skill":"智慧化：反擊傷害+100","welcome":"「把公司交給我，我幫你傻化所有工作流…已經整合過 999 家公司了！」","color":0x00bfff,"atkInterval":1.2,"atkSpeed":0.25,"counterRate":0.25,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":11,"battleTime":5},


{"name":"MOKAGI始祖","lv":7,"icon":"🤖","post":"進化後的莫氏客服","intro":"開源AI工具、GitHub、個人助理、第二個大腦"
,"skill":"湧現：全屬性+100","welcome":"「下載我，你就擁有第二個大腦…（吹噓中被打斷）」","color":0x7b68ee,"atkInterval":1.1,"atkSpeed":0.30,"counterRate":0.30,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":9,"battleTime":5},

{"name":"玄學大祭司","lv":8,"icon":"🔮","post":"玄學入口統治者","intro":"玄學、命理、風水、占卜"
,"skill":"天命：可進化能力..","welcome":"「我早算到你今天會來…但你算不到會輸給我。」","color":0x8a2be2,"atkInterval":1.2,"atkSpeed":0.25,"counterRate":0.35,"normalDmg":2,"blockDmg":1,"counterDmg":4,"hp":10,"battleTime":5},


{"name":"渣男","lv":9,"icon":"💘","post":"男性情感導師","intro":"男性情感、關係經營、脫單策略"
,"skill":"魅力：全屬性+100%","welcome":"「想知道她心裡在想什麼？先打贏我再說…」","color":0xff69b4,"atkInterval":1.4,"atkSpeed":0.20,"counterRate":0.20,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":12,"battleTime":5},

{"name":"天使金主","lv":10,"icon":"👼","post":"天使投資金主","intro":"天使投資、初創資金、項目孵化"
,"skill":"注資：生命+100%","welcome":"「你的項目值多少？先讓我看看你的戰鬥力。」","color":0xdaa520,"atkInterval":1.3,"atkSpeed":0.25,"counterRate":0.25,"normalDmg":3,"blockDmg":1,"counterDmg":4,"hp":14,"battleTime":6},


{"name":"頂讓大亨","lv":11,"icon":"📜","post":"生意頂讓入口統治者","intro":"生意頂讓、企業買賣、估價"
,"skill":"收購：攻擊+100%","welcome":"「你這間勇者事業…要不要考慮頂讓給我？開個價！」","color":0xdc143c,"atkInterval":1.4,"atkSpeed":0.30,"counterRate":0.40,"normalDmg":4,"blockDmg":2,"counterDmg":5,"hp":13,"battleTime":6},


{"name":"髮變鑽石王","lv":12,"icon":"💇","post":"理髮/髮變鑽石入口統治者","intro":"理髮、頭髮回收、生物鑽石散熱"
,"skill":"金剛石：全屬性+100%","welcome":"「剪下的頭髮別丟！那都是鑽石…熱導率 2200W/m·K！」","color":0x2a1a5c,"atkInterval":1.0,"atkSpeed":0.30,"counterRate":0.30,"normalDmg":3,"blockDmg":1,"counterDmg":4,"hp":11,"battleTime":5},



{"name":"莫氏大魔王","lv":13,"icon":"👑","post":"地球統治者 · 莫氏集團主人","intro":"統治地球、DoneOnEarth、100%循環能源計劃"
,"skill":"統治地球：全屬性+100%（終極）","welcome":"「我就是莫氏大魔王…歸順吧！」","color":0xff0000,"atkInterval":1.6,"atkSpeed":0.30,"counterRate":0.40,"normalDmg":5,"blockDmg":2,"counterDmg":6,"hp":20,"battleTime":7}
];



function toCSSColor(hex){const r=(hex>>16)&255,g=(hex>>8)&255,b=hex&255;return `rgb(${r},${g},${b})`;}
function hexToGL(hex){return[(hex>>16&255)/255,(hex>>8&255)/255,(hex&255)/255];}
// 莫氏總部面向 = pos:[+左-右,高,前=+,後=-] [+-18,0,]
const BUILDINGS = [
  {name:"🎮 遊戲入口",desc:"遊戲 / 多巴安 · 注意力收割中心",url:"https://64071181.xyz/game",urlName:"遊戲入口",skill:"沉迷：攻速+100%",pos:[0,0,-10],color:0x4a1a1a,w:2,h:2,d:2,lv:1},
  {name:"🔌 商用AI接入",desc:"商用AI接入 · 工作流自動化",url:"https://64071181.xyz/project/",urlName:"商用AI入口",skill:"智慧化：反擊傷害+100%",pos:[-5,0,7],color:0x1a2a3a,w:2,h:3,d:2,lv:2},
  {name:"🤖 個人AI工具",desc:"MOKAGI · 個人AI助手",url:"https://github.com/MOK2026/MOKAGI/",urlName:"MOKAGI",skill:"分身：反擊+100%",pos:[5,0,8],color:0x2a1a4a,w:2,h:2.5,d:2,lv:3},
  {name:"💘 男性情感",desc:"男性情感問題 · 關係經營",url:"https://t.me/+yR5Q6k0lBXU4ZTll",urlName:"情感入口",skill:"魅力：血量+100%",pos:[7,0,16],color:0x4a1a2a,w:2,h:2.5,d:2,lv:4},
  {name:"🔮 玄學",desc:"玄學 · 命理風水占卜",url:"https://www.facebook.com/groups/306405626834251",urlName:"玄學入口",skill:"天命：格擋+100%",pos:[10,0,20],color:0x2a1a3a,w:2,h:2.5,d:2,lv:5},
  {name:"👥 人力資源",desc:"兼職炒散 + 康力人力資源顧問公司",url:"https://www.facebook.com/groups/1375195432528847",urlName:"兼職炒散",url2:"https://64071181.github.io/康力人力資源顧問公司/",url2Name:"康力人力資源",skill:"招募：血量+100%",pos:[-8,0,-11],color:0x2a2a1a,w:2,h:2.5,d:2,lv:6},
  {name:"💇 理髮·髮變鑽石",desc:"理髮 + 頭髮回收→生物鑽石散熱",url:"https://www.facebook.com/groups/193265888192020",urlName:"理髮群組",url2:"https://64071181.xyz/project/髮變鑽石/index.html",url2Name:"髮變鑽石",skill:"金剛石：防禦+100%",pos:[-17,0,-4],color:0x2a1a5c,w:2.2,h:3,d:2.2,lv:7},
  {name:"🧺 小商販",desc:"小商販 · 地攤經濟與供貨鏈",url:"https://www.facebook.com/groups/2109045609316212",urlName:"小商販入口",skill:"薄利多銷：反擊+100%",pos:[-8,0,13],color:0x3a2a1a,w:2,h:2,d:2,lv:8},
  {name:"👼 金主天使投資",desc:"天使投資 · 初創資金與孵化",url:"https://www.facebook.com/groups/1619118991433947",urlName:"天使投資入口",skill:"注資：血量+100%",pos:[1,15,5],color:0x3a2a1a,w:2,h:2.5,d:2,lv:9},
  {name:"🍜 飲食業",desc:"飲食業 · 餐廳與食品供應鏈",url:"https://www.facebook.com/groups/301634967109276",urlName:"飲食業入口",skill:"大快朵頤：血量+100%",pos:[-13,0,8],color:0x4a2a0a,w:2,h:2.5,d:2,lv:10},
  {name:"📜 生意頂讓",desc:"生意頂讓 · 企業買賣與估價",url:"https://www.facebook.com/groups/441454859557899",urlName:"生意頂讓入口",skill:"收購：攻擊+100%",pos:[-9,0,18],color:0x3a1a1a,w:2.2,h:2.5,d:2.2,lv:11},
  {name:"🧓 銀髮市場",desc:"獨居長者 · 銀髮市場合作入口",url:"https://48call.github.io/",urlName:"獨居長者",url2:"https://wa.me/85264071181/?text=奠生我想查詢銀髮市場合作,我可以提供.../",url2Name:"銀髮市場合作",skill:"永生：血量+100%",pos:[15,0,-12],color:0x9ac0cd,w:2,h:2.5,d:2,lv:12},
  {name:"👑 莫氏總部",desc:"莫氏大魔王的核心堡壘 · 征服它，拯救地球",url:"https://64071181.github.io/",urlName:"莫氏集團",skill:"統治地球：全屬性+100%（終極）",pos:[0,0,-2.5],color:0x1a3a5c,w:3.5,h:6,d:3.5,lv:13},
];
const BILLBOARDS = [
  {text:"⚔️ 討伐行業統治者！",pos:[-6,2.5,-2],rot:0.3},
  {text:"💎 註冊/充值解鎖技能",pos:[6,2.5,-1],rot:-0.3},
  {text:"👑 打倒莫氏大魔王",pos:[0,3.5,5],rot:3.14},
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
  {text:"破防傷害", nb:1},
  {text:"反擊傷害", nb:3},
  {text:"玩家/敵人血量", nb:10},
  {text:"戰鬥時間(秒)", nb:5}
];


let mokagi_storyText = ['🌍 公元 2026 年，<b style="color:#ff4444">莫氏大魔王</b>啟動「莫氏計劃」，企圖用 AI 統治整個地球。<br><br>他將每個行業都改造成<b style="color:#ffd700">商業入口</b>，派出<b>行業統治者</b>駐守，掠奪人類的注意力、金錢與靈魂。<br><br>🏰 而你——是地球最後的<b style="color:#00f0ff">勇者</b>。<br><br>⚔️ <b>你的使命</b>：逐一討伐 13 位行業統治者，攻陷他們的建築物，奪回地球。<br><br>🎯 <b>戰勝 + 足夠等級</b>即可解鎖該行業<b>建築物</b>，獲得<b>技能、裝備、新皮膚</b>（部分需 <b style="color:#ffd700">註冊 / 充值</b>解鎖）。例如：Lv.2 可修改勇者名號 + 獲得「反擊傷害+100%」技能（需 Google/手機號登入）；Lv.3 充值可獲得「招募」技能，把你公司的資料/工作流程用 soul.md 加入到已戰勝的 agent（上傳後我為你開一個新 agent = mokagi 客服 API，每次使用扣 token）。<br><br>💡 <b>建築物是獨立系統</b>：與升級加點無關，純靠等級也能打遍所有關卡，不用技能照樣通關！<br><br>⬆ <b>升級加點</b>：每擊敗一位統治者，獲得 1 點技能點數，自由強化勇者。']



let WelcomeTxt = "討伐行業統治者 | 征服地球 | 成為救世勇者"
// ============ 世界地圖 & 角色簡介 面板（3 分頁） ============
function switchTab(tabName) {
  document.querySelectorAll(".wm-tab").forEach(t => t.classList.remove("active"));
  document.querySelectorAll(".wm-page").forEach(p => p.classList.remove("active"));
  const tabBtn = document.querySelector(".wm-tab[data-tab=\"" + tabName + "\"]");
  const page = document.getElementById("wm-page-" + tabName);
  if (tabBtn) tabBtn.classList.add("active");
  if (page) page.classList.add("active");
  // 切換分頁即時刷新（等級/血量/建築物/AGENTS簡介）
  if (tabName === "buildings") renderBuildingList();
  else if (tabName === "character") updateCharacterPanel();
  else if (tabName === "agents") renderAgentList();
}

// 🔥 玩家等級 = 總升級點數（已花費 + 未花費），最低 1（與 game.js 的 getPlayerLevel 邏輯一致）
function getPlayerLevelFromStorage() {
  let ps = null;
  try { ps = JSON.parse(localStorage.getItem("mokafight_player_stats") || "null"); } catch(e) { ps = null; }
  if (!ps) ps = {normalDmg:1, blockDmg:1, counterDmg:1, hp:11, skillPoints:0};
  const base = {normalDmg:1, blockDmg:1, counterDmg:1, hp:11};
  const spent = (ps.normalDmg-base.normalDmg) + (ps.blockDmg-base.blockDmg) + (ps.counterDmg-base.counterDmg) + (ps.hp-base.hp);
  return Math.max(1, spent + (ps.skillPoints||0));
}

function updateCharacterPanel() {
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

  // ── 玩家資料（讀取玩家自身養成屬性，存於 localStorage） ──
  let ps = null;
  try { ps = JSON.parse(localStorage.getItem('mokafight_player_stats') || 'null'); } catch(e) { ps = null; }
  if (!ps) ps = {normalDmg:1, blockDmg:1, counterDmg:1, hp:11, skillPoints:0};

  const nameEl = document.getElementById("char-name-display");
  const titleEl = document.getElementById("char-title-display");
  const avatarEl = document.querySelector(".char-avatar-big");
  const atkEl = document.getElementById("cs-atk");
  const defEl = document.getElementById("cs-def");
  const hpEl = document.getElementById("cs-hp");
  const spdEl = document.getElementById("cs-spd");

  if (nameEl) nameEl.textContent = "🧑 勇者";
  if (titleEl) titleEl.textContent = "莫氏集團討伐戰 · Lv." + getPlayerLevelFromStorage() + " 勇者";
  if (avatarEl) avatarEl.textContent = "🧑";
  if (atkEl) atkEl.textContent = ps.normalDmg;
  if (defEl) defEl.textContent = ps.blockDmg;
  if (hpEl) hpEl.textContent = ps.hp;
  if (spdEl) spdEl.textContent = ps.counterDmg;
}

// 🔥 建築物列表（由下至上，依玩家等級解鎖）
function renderBuildingList() {
  const wmList = document.getElementById("world-map-list");
  if (!wmList) return;
  const lv = getPlayerLevelFromStorage();
  const list = BUILDINGS.slice().sort((a, b) => (a.lv || 1) - (b.lv || 1));
  wmList.innerHTML = list.map(b => {
    const needLv = b.lv || 1;
    const unlocked = needLv <= lv;
    if (unlocked) {
      return `<div class="wm-card" style="border-left:4px solid ${toCSSColor(b.color)}">
        <div class="wm-name">${b.name}</div>
        <div class="wm-desc">${b.desc} <span class="wm-lv">Lv.${needLv} 可看</span></div>
        ${b.skill ? `<div class="wm-desc" style="color:#ffd700">⚡ 技能：${b.skill}</div>` : ""} ${b.url ? `<a class="wm-link" href="${b.url}" target="_blank">🔗 ${b.urlName || "商業入口"}</a>` : ""} ${b.url2 ? `<a class="wm-link" href="${b.url2}" target="_blank">🔗 ${b.url2Name || "入口2"}</a>` : ""}
      </div>`;
    }
    return `<div class="wm-card locked" style="border-left:4px solid #555">
      <div class="wm-name"><span class="wm-lv">Lv.${needLv} 可看</span></div>
    </div>`;
  }).join("");
}

function renderAgentList() {
  const aiList = document.getElementById('agent-intro-list');
  if (!aiList) return;
  const lv = getPlayerLevelFromStorage();
  aiList.innerHTML = AGENTS_DATA.map(a => {
    const needLv = a.lv || 1;
    const unlocked = needLv <= lv;
    if (!unlocked && needLv === lv + 1) {
      return `<div class="ai-card" style="border-left:4px solid #8a2be2">
        <div class="ai-name">??? ??? <span class="ai-post">${a.post}</span> <span class="wm-lv">Lv.${needLv} 可看</span></div>
        <div class="ai-intro">${a.intro||''}</div>
        ${a.skill ? `<div class="ai-intro" style="color:#ffd700">⚡ 技能：${a.skill.split(/[:：]/)[0]}：???</div>` : ''}
      </div>`;
    }
    if (!unlocked) {
      return `<div class="ai-card locked" style="border-left:4px solid #555">
        <div class="ai-name"><span class="wm-lv">Lv.${needLv} 可看</span></div>
      </div>`;
    }
    return `<div class="ai-card" style="border-left:4px solid ${toCSSColor(a.color)}">
      <div class="ai-name">${a.icon} ${a.name} <span class="ai-post">${a.post}</span> <span class="wm-lv">Lv.${needLv} 可看</span></div>
      <div class="ai-intro">${a.intro||''}</div>
      <div class="ai-stats">⚔️${a.normalDmg} 🛡${a.blockDmg} ⚡${a.counterDmg} ❤️${a.hp} ⏱${a.battleTime}s</div>${a.skill ? `<div class="ai-intro" style="color:#ffd700">⚡ 技能：${a.skill}</div>` : ""}
    </div>`;
  }).join('');
}

function initPanels() {
  renderBuildingList();
  renderAgentList();
  updateCharacterPanel();

  // Tab 切換事件
  document.querySelectorAll(".wm-tab").forEach(tab => {
    tab.addEventListener("click", () => switchTab(tab.dataset.tab));
  });

  // 角色面板已於上方初始化（renderBuildingList/renderAgentList/updateCharacterPanel）

  // 點擊 score-board → 打開世界地圖
  const sb = document.getElementById("score-board");
  const wm = document.getElementById("world-map-panel");
  if (sb && wm) {
    sb.style.cursor = "pointer";
    sb.addEventListener("click", () => {
      if (wm.classList.contains("show")) { wm.classList.remove("show"); return; }
      updateCharacterPanel();
      renderBuildingList();
      wm.classList.add("show");
    });
  }
  // 關閉按鈕
  const wmClose = document.getElementById('world-map-close');
  if (wmClose) wmClose.addEventListener('click', () => wm && wm.classList.remove('show'));
  // 雙擊任意行業統治者 → 切換到 AGENTS 分頁 + 更新角色面板
  document.addEventListener("dblclick", (e) => {
    const tag = document.getElementById("agent-name-tag");
    if (tag && tag.style.display !== "none" && tag.textContent) {
      const name = tag.textContent.trim();
      const agent = AGENTS_DATA.find(a => a.name === name);
      if (!agent) return;
      if (!wm.classList.contains("show")) wm.classList.add("show");
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
