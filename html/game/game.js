import * as THREE from 'three';

/* roundRect polyfill */
if(!CanvasRenderingContext2D.prototype.roundRect){CanvasRenderingContext2D.prototype.roundRect=function(x,y,w,h,r){if(typeof r==='number')r={tl:r,tr:r,br:r,bl:r};this.moveTo(x+r.tl,y);this.lineTo(x+w-r.tr,y);this.quadraticCurveTo(x+w,y,x+w,y+r.tr);this.lineTo(x+w,y+h-r.br);this.quadraticCurveTo(x+w,y+h,x+w-r.br,y+h);this.lineTo(x+r.bl,y+h);this.quadraticCurveTo(x,y+h,x,y+h-r.bl);this.lineTo(x,y+r.tl);this.quadraticCurveTo(x,y,x+r.tl,y);this.closePath();};}










// State
let state = {
  mode:'start', // start | game | battle
  agents:[],
  playerHP:10, enemyHP:10,
  battleTimer:5, battleInterval:null,
  battleActive:false,
  lastMove:null, lastMoveTime:0,
  enemyLastMove:null, enemyLastMoveTime:0,
  blockHeld:false,
  hitFlashTime:0, hitFlashSide:"", // 打擊閃光
  counterFlashTime:0, // 反擊特效時間
  swipeTrail:null, // 滑動軌跡 {x1,y1,x2,y2,time}
  score:{wins:0,losses:0},
  currentInteractAgent:null,
  // 每個 agent 獨立戰鬥數值（startBattle 時從 AGENTS_DATA 填入）
  battleAtkInterval:1.0, battleAtkSpeed:0.3,
  battleCounterRate:0.25, battleNormalDmg:2,
  battleBlockDmg:1, battleCounterDmg:3,
  battleHP:10, battleTime:5,
};
// 🔥 玩家養成屬性（可升級，存 localStorage）
state.playerStats = lsGet('player_stats',{normalDmg:1,blockDmg:1,counterDmg:1,hp:11,skillPoints:0});

// DOM refs
const $ = id => document.getElementById(id);
const canvas3D = $('three-canvas');
const startOverlay = $('start-overlay');
const startMenu = $('start-menu');
const smartShortcut = $('smart-shortcut');
const smartLink = $('smart-link');
const zoneIndicator = $('zone-indicator');
const gameUI = $('game-ui');
const onlineCount = $('online-count');
const agentNameTag = $('agent-name-tag');
const buildingTooltip = $('building-tooltip');
const interactMenu = $('interact-menu');
const chatPanel = $('chat-panel');
const chatAgentName = $('chat-agent-name');
const chatMessages = $('chat-messages');
const chatLoading = $('chat-loading');
const chatInput = $('chat-input');
const battleOverlay = $('battle-overlay');
const battleCanvas = $('battle-canvas');
const battleResult = $('battle-result');
const battleTimerEl = $('battle-timer');
const battlePlayerHP = $('battle-player-hp');
const battleEnemyHP = $('battle-enemy-hp');
const battlePlayerName = $('battle-player-name');
const battleEnemyName = $('battle-enemy-name');
const battleLog = $('battle-log');
const upgradePanel = $('upgrade-panel');
const upgradePoints = $('upgrade-points');
const upgradeClose = $('upgrade-close');
const storyOverlay = $('story-overlay');
const storyStart = $('story-start');
const storyText = $('story-text');
const agentInfoTag = $('agent-info-tag');
const transitionOverlay = $('transition-overlay');
const transitionSnapshot = $('transition-snapshot');
const scoreBoard = $('score-board');
const scoreText = $('score-text');

// ============ LOCALSTORAGE ============
function lsGet(k,d){try{const v=localStorage.getItem('mokafight_'+k);return v?JSON.parse(v):d}catch(e){return d}}
function lsSet(k,v){try{localStorage.setItem('mokafight_'+k,JSON.stringify(v))}catch(e){}}
function getPlayerName(){const uid=localStorage.getItem('mokagi_user_id');return uid||('guest_'+Date.now());}

// Zone stats: {top:{start:5,business:2,pay:1}, ...}
let zoneStats = lsGet('zone_stats',{});
// Smart shortcut: {zone:'center',btn:'start'}
let smartShortcutData = lsGet('smart_shortcut',null);
// Score: {wins:0,losses:0}
state.score = lsGet('score',{wins:0,losses:0});

function updateScoreDisplay(){
  scoreText.textContent = state.score.wins+' 勝 '+state.score.losses+' 敗';
  if(state.mode==='game') scoreBoard.classList.add('show');
}
updateScoreDisplay();

function recordZoneClick(zone,btn){
  if(!zoneStats[zone]) zoneStats[zone]={};
  if(!zoneStats[zone][btn]) zoneStats[zone][btn]=0;
  zoneStats[zone][btn]++;
  lsSet('zone_stats',zoneStats);
  // Find max combo
  let maxCount=0, maxZone=null, maxBtn=null;
  for(const[z,btns] of Object.entries(zoneStats)){
    for(const[b,c] of Object.entries(btns)){
      if(c>maxCount){maxCount=c;maxZone=z;maxBtn=b;}
    }
  }
  if(maxZone && maxBtn){
    smartShortcutData = {zone:maxZone, btn:maxBtn};
    lsSet('smart_shortcut',smartShortcutData);
  }
}

function getSmartShortcutInfo(){
  if(!smartShortcutData) return null;
  const urls = {start:'https://64071881.xyz/game',business:'https://64071181.github.io/',pay:'https://64071181.github.io/PayAki/'};
  const labels = {start:'⚔️ 開始遊戲',business:'🏢 商業',pay:'💎 充值'};
  return {zone:smartShortcutData.zone, btn:smartShortcutData.btn, url:urls[smartShortcutData.btn], label:labels[smartShortcutData.btn]};
}


// ============ THREE.JS SETUP ============
const scene = new THREE.Scene();
scene.fog = new THREE.FogExp2(0x000a14, 0.00008);
const camera = new THREE.PerspectiveCamera(60, innerWidth/innerHeight, 0.5, 200);
camera.position.set(0, 14, 16);
camera.lookAt(0, 0, 0);

// ============ ORBIT CONTROLS ============
const orbitState = {
  theta: Math.PI/2,
  phi: 0.853,
  radius: 21.26,
  targetTheta: Math.PI/2,
  targetPhi: 0.853,
  targetRadius: 21.26,
  minRadius: user_setting[7].nb,
  maxRadius: user_setting[8].nb,
  minPhi: user_setting[9].nb,
  maxPhi: user_setting[10].nb,
  autoRotate: true,
  autoRotateSpeed: user_setting[3].nb,
  isDragging: false,
  prevMouse: {x:0, y:0},
  pinchStartDistance: 0,
  pinchStartRadius: 0,
  damping: user_setting[4].nb,
  hasDragged: false
};

function sphericalToCartesian(theta, phi, radius){
  return new THREE.Vector3(
    radius * Math.sin(phi) * Math.cos(theta),
    radius * Math.cos(phi),
    radius * Math.sin(phi) * Math.sin(theta)
  );
}

function updateOrbitCamera(){
  orbitState.theta += (orbitState.targetTheta - orbitState.theta) * orbitState.damping;
  orbitState.phi += (orbitState.targetPhi - orbitState.phi) * orbitState.damping;
  orbitState.radius += (orbitState.targetRadius - orbitState.radius) * orbitState.damping;
  const pos = sphericalToCartesian(orbitState.theta, orbitState.phi, orbitState.radius);
  camera.position.copy(pos);
  camera.lookAt(0, 0.5, 0);
}

canvas3D.addEventListener("pointerdown", (e) => {
  if(state.mode !== "game") return;
  orbitState.isDragging = true;
  orbitState.hasDragged = false;
  orbitState.prevMouse.x = e.clientX;
  orbitState.prevMouse.y = e.clientY;
  orbitState.autoRotate = false;
});

window.addEventListener("pointermove", (e) => {
  if(!orbitState.isDragging) return;
  if(orbitState.pinchStartDistance > 0) return;
  const dx = e.clientX - orbitState.prevMouse.x;
  const dy = e.clientY - orbitState.prevMouse.y;
  if(Math.abs(dx) > user_setting[5].nb || Math.abs(dy) > user_setting[5].nb){
    orbitState.hasDragged = true;
    canvas3D.style.cursor = "grabbing";
  }
  if(!orbitState.hasDragged) return;
  orbitState.targetTheta -= dx * user_setting[0].nb;// 水平拖曳 → 水平旋轉
  orbitState.targetPhi -= dy * user_setting[1].nb; // 垂直拖曳 → 俯仰角度
  orbitState.targetPhi = Math.max(orbitState.minPhi, Math.min(orbitState.maxPhi, orbitState.targetPhi));
  orbitState.prevMouse.x = e.clientX;
  orbitState.prevMouse.y = e.clientY;
});

window.addEventListener("pointerup", () => {
  if(orbitState.isDragging){
    orbitState.isDragging = false;
    canvas3D.style.cursor = "";
    clearTimeout(orbitState._autoRotateTimeout);
    if(orbitState.hasDragged){
      orbitState._autoRotateTimeout = setTimeout(() => { orbitState.autoRotate = true; }, user_setting[6].nb);
    } else {
      orbitState.autoRotate = true;
    }
  }
});

canvas3D.addEventListener("wheel", (e) => {
  if(state.mode !== "game") return;
  e.preventDefault();
  orbitState.targetRadius += e.deltaY * user_setting[2].nb;
  orbitState.targetRadius = Math.max(orbitState.minRadius, Math.min(orbitState.maxRadius, orbitState.targetRadius));
}, {passive: false});

canvas3D.addEventListener("touchstart", (e) => {
  if(state.mode !== "game") return;
  if(e.touches.length === 2){
    orbitState.isDragging = false;
    orbitState.pinchStartDistance = Math.hypot(
      e.touches[0].clientX - e.touches[1].clientX,
      e.touches[0].clientY - e.touches[1].clientY
    );
    orbitState.pinchStartRadius = orbitState.targetRadius;
  }
}, {passive: false});

canvas3D.addEventListener("touchmove", (e) => {
  if(state.mode !== "game") return;
  if(e.touches.length === 2 && orbitState.pinchStartDistance > 0){
    e.preventDefault();
    const dist = Math.hypot(
      e.touches[0].clientX - e.touches[1].clientX,
      e.touches[0].clientY - e.touches[1].clientY
    );
    const scale = orbitState.pinchStartDistance / dist;
    orbitState.targetRadius = Math.max(orbitState.minRadius, Math.min(orbitState.maxRadius, orbitState.pinchStartRadius * scale * 1.6));
  }
}, {passive: false});

canvas3D.addEventListener("touchend", () => {
  orbitState.pinchStartDistance = 0;
});

const renderer = new THREE.WebGLRenderer({canvas:canvas3D,antialias:true,alpha:true});
renderer.setSize(innerWidth,innerHeight);
renderer.setPixelRatio(Math.min(devicePixelRatio,2));
renderer.shadowMap.enabled = true;
renderer.shadowMap.type = THREE.PCFSoftShadowMap;
renderer.toneMapping = THREE.ACESFilmicToneMapping;
renderer.toneMappingExposure = 1.2;

// Lights
const ambientLight = new THREE.AmbientLight(0x112244, 1.5);
scene.add(ambientLight);
const moonLight = new THREE.DirectionalLight(0x4488cc, 2.5);
moonLight.position.set(20, 30, 10);
moonLight.castShadow = true;
moonLight.shadow.mapSize.set(1024,1024);
moonLight.shadow.camera.near=0.5;moonLight.shadow.camera.far=100;
moonLight.shadow.camera.left=-30;moonLight.shadow.camera.right=30;
moonLight.shadow.camera.top=30;moonLight.shadow.camera.bottom=-30;
scene.add(moonLight);

const pointLights = [];
for(let i=0;i<6;i++){
  const pl = new THREE.PointLight(0x00aaff, 8, 20);
  pl.position.set(Math.cos(i/6*Math.PI*2)*10, 1+Math.random()*4, Math.sin(i/6*Math.PI*2)*10);
  scene.add(pl);
  pointLights.push(pl);
}

// Ground
const groundGeo = new THREE.PlaneGeometry(60,60);
const groundMat = new THREE.MeshStandardMaterial({color:0x0a1520,roughness:0.9,metalness:0.3});
const ground = new THREE.Mesh(groundGeo,groundMat);
ground.rotation.x=-Math.PI/2;ground.receiveShadow=true;scene.add(ground);

// Grid
const gridHelper = new THREE.PolarGridHelper(25,48,24,128,0x00aaff,0x003355);
gridHelper.position.y=0.02;scene.add(gridHelper);

// Particles
const particlesGeo = new THREE.BufferGeometry();
const particlesCount = 800;
const posArray = new Float32Array(particlesCount*3);
for(let i=0;i<particlesCount*3;i+=3){
  posArray[i]=(Math.random()-0.5)*40;
  posArray[i+1]=Math.random()*15;
  posArray[i+2]=(Math.random()-0.5)*40;
}
particlesGeo.setAttribute('position',new THREE.BufferAttribute(posArray,3));
const particlesMat = new THREE.PointsMaterial({color:0x00ccff,size:0.04,transparent:true,opacity:0.6,blending:THREE.AdditiveBlending});
const particles = new THREE.Points(particlesGeo,particlesMat);
scene.add(particles);

// ============ BUILDINGS ============
const buildingMeshes = [];
BUILDINGS.forEach(b=>{
  const group = new THREE.Group();
  // Main body
  const bodyGeo = new THREE.BoxGeometry(b.w,b.h,b.d);
  const bodyMat = new THREE.MeshStandardMaterial({color:b.color,roughness:0.5,metalness:0.7});
  const body = new THREE.Mesh(bodyGeo,bodyMat);
  body.position.y=b.h/2;body.castShadow=true;body.receiveShadow=true;
  group.add(body);
  // Neon edges
  const edgeGeo = new THREE.EdgesGeometry(bodyGeo);
  const edgeMat = new THREE.LineBasicMaterial({color:0x00ffff,transparent:true,opacity:0.4});
  const edgeLine = new THREE.LineSegments(edgeGeo,edgeMat);
  edgeLine.position.y=b.h/2;group.add(edgeLine);
  // Top glow
  const topGeo = new THREE.CylinderGeometry(0.2,0.4,0.3,8);
  const topMat = new THREE.MeshBasicMaterial({color:0x00ffff});
  const top = new THREE.Mesh(topGeo,topMat);
  top.position.y=b.h+0.01;group.add(top);
  // Ring
  const ringGeo = new THREE.TorusGeometry(Math.min(b.w,b.d)/2.5,0.08,8,16);
  const ringMat = new THREE.MeshBasicMaterial({color:0x00ffff,transparent:true,opacity:0.5});
  const ring = new THREE.Mesh(ringGeo,ringMat);
  ring.rotation.x=Math.PI/2;ring.position.y=b.h*0.7;group.add(ring);
  // Building name label
  const nameCanvas = document.createElement("canvas");
  nameCanvas.width=512;nameCanvas.height=96;
  const nctx=nameCanvas.getContext("2d");
  nctx.fillStyle="rgba(0,0,0,0.75)";nctx.beginPath();nctx.roundRect(10,10,492,76,12);nctx.fill();
  nctx.strokeStyle="#00f0ff";nctx.lineWidth=3;nctx.beginPath();nctx.roundRect(10,10,492,76,12);nctx.stroke();
  nctx.fillStyle="#00f0ff";nctx.font="bold 28px \"Courier New\",\"微軟正黑體\",monospace";nctx.textAlign="center";
  nctx.fillText(b.name,256,58);
  const nameTex = new THREE.CanvasTexture(nameCanvas);nameTex.minFilter=THREE.LinearFilter;
  // 立體名稱標籤 - BoxGeometry 前後貼文字，360度可見
  const nameGeo = new THREE.BoxGeometry(3.5,0.7,0.12);
  const nameTexMat = new THREE.MeshBasicMaterial({map:nameTex,transparent:true,depthTest:false});
  const nameSideMat = new THREE.MeshBasicMaterial({color:0x004444,transparent:true,depthTest:false});
  // BoxGeometry 面順序: +X右, -X左, +Y上, -Y下, +Z前, -Z後
  const nameMats = [nameSideMat,nameSideMat,nameSideMat,nameSideMat,nameTexMat,nameTexMat];
  const nameLabel = new THREE.Mesh(nameGeo,nameMats);
  nameLabel.position.y=b.h+0.9;group.add(nameLabel);
  // 霓虹邊框
  const nameEdgeGeo = new THREE.EdgesGeometry(nameGeo);
  const nameEdgeLine = new THREE.LineSegments(nameEdgeGeo,new THREE.LineBasicMaterial({color:0x00ffff,transparent:true,opacity:0.5,depthTest:false}));
  nameLabel.add(nameEdgeLine);

  group.position.set(b.pos[0],b.pos[1],b.pos[2]);
  group.userData = {name:b.name,desc:b.desc,isBuilding:true};
  scene.add(group);
  buildingMeshes.push(group);
});

// Billboards (廣告牌)
BILLBOARDS.forEach(b=>{
  const canvas = document.createElement('canvas');
  canvas.width=256;canvas.height=64;
  const ctx=canvas.getContext('2d');
  ctx.fillStyle='rgba(0,0,0,0.7)';ctx.fillRect(0,0,256,64);
  ctx.strokeStyle='#00ffff';ctx.lineWidth=2;ctx.strokeRect(2,2,252,60);
  ctx.fillStyle='#00ffff';ctx.font='bold 26px "Courier New",monospace';ctx.textAlign='center';
  ctx.fillText(b.text,128,38);
  const tex = new THREE.CanvasTexture(canvas);tex.minFilter=THREE.LinearFilter;
  const geo = new THREE.BoxGeometry(3,0.75,0.15);
  const textMat = new THREE.MeshBasicMaterial({map:tex,transparent:true});
  const sideMat = new THREE.MeshBasicMaterial({color:0x006666});
  const edgeMat2 = new THREE.MeshBasicMaterial({color:0x003333});
  const mats = [sideMat,sideMat,edgeMat2,edgeMat2,textMat,textMat];
  const edgeLineMat = new THREE.LineBasicMaterial({color:0x00ffff,transparent:true,opacity:0.6});
  // 主廣告牌
  const board1 = new THREE.Mesh(geo,mats);
  board1.position.set(b.pos[0],b.pos[1],b.pos[2]);
  board1.rotation.y=b.rot;
  board1.add(new THREE.LineSegments(new THREE.EdgesGeometry(geo),edgeLineMat));
  board1.userData = {name:'廣告版',desc:b.text,isBuilding:true};
  scene.add(board1);buildingMeshes.push(board1);
});

// ============ AGENT NPCS ============
const agentNPCs = [];
function createAgentNPC(agentData,x,z){
  const name=agentData.name;
  const agentColor=agentData.color||0x00ffcc;
  const group = new THREE.Group();
  // Body - agent color tint
  const bodyGeo = new THREE.CylinderGeometry(0.25,0.35,1.2,8);
  const bodyColor=new THREE.Color(agentColor).multiplyScalar(0.4).getHex();
  const bodyMat = new THREE.MeshStandardMaterial({color:bodyColor,roughness:0.4,metalness:0.6});
  const body = new THREE.Mesh(bodyGeo,bodyMat);body.position.y=0.7;body.castShadow=true;group.add(body);
  // Head
  const headGeo = new THREE.SphereGeometry(0.28,12,12);
  const headMat = new THREE.MeshStandardMaterial({color:0xffe4c4,roughness:0.5});
  const head = new THREE.Mesh(headGeo,headMat);head.position.y=1.45;head.castShadow=true;group.add(head);
  // Glow ring - agent color
  const glowGeo = new THREE.TorusGeometry(0.4,0.03,8,16);
  const glowMat = new THREE.MeshBasicMaterial({color:agentColor,transparent:true,opacity:0.7});
  const glow = new THREE.Mesh(glowGeo,glowMat);glow.rotation.x=Math.PI/2;glow.position.y=0.1;group.add(glow);
  // Label Sprite (always faces camera - 文字有厚度不消失)
  const agentMeta = AGENTS_DATA.find(a=>a.name===name)||{icon:"🤖",post:"村民",color:0x00ffcc};
  const labelCanvas = document.createElement('canvas');
  labelCanvas.width=512;labelCanvas.height=148;
  const lctx=labelCanvas.getContext('2d');
  // 半透明深色背景 - 文字厚度感
  lctx.fillStyle='rgba(5,5,15,0.85)';
  const r=16;lctx.beginPath();lctx.moveTo(r,0);lctx.lineTo(512-r,0);lctx.quadraticCurveTo(512,0,512,r);lctx.lineTo(512,148-r);lctx.quadraticCurveTo(512,148,512-r,148);lctx.lineTo(r,148);lctx.quadraticCurveTo(0,148,0,148-r);lctx.lineTo(0,r);lctx.quadraticCurveTo(0,0,r,0);lctx.fill();
  // 霓虹邊框
  lctx.strokeStyle=toCSSColor(agentMeta.color);lctx.lineWidth=4;lctx.stroke();
  // 內發光邊框
  lctx.strokeStyle='rgba(255,255,255,0.3)';lctx.lineWidth=1;lctx.stroke();
  // Icon 大字
  lctx.font="bold 50px 'Segoe UI Emoji','Apple Color Emoji','Noto Color Emoji',sans-serif";lctx.textAlign='center';
  lctx.fillText(agentMeta.icon,256,58);
  // 名字 (大)
  lctx.fillStyle=toCSSColor(agentMeta.color);lctx.font="bold 34px 'Courier New',monospace";lctx.textAlign='center';
  lctx.fillText(name,256,90);
  // Post 副標題
  lctx.fillStyle='rgba(200,220,255,0.9)';lctx.font="22px '微軟正黑體','Noto Sans TC',sans-serif";
  lctx.fillText(agentMeta.post,256,120);
  const labelTex = new THREE.CanvasTexture(labelCanvas);labelTex.minFilter=THREE.LinearFilter;
  const labelSpriteMat = new THREE.SpriteMaterial({map:labelTex,transparent:true,depthTest:false,depthWrite:false});
  const labelSprite = new THREE.Sprite(labelSpriteMat);
  labelSprite.scale.set(2.8,0.7,1);labelSprite.position.y=2.2;
  labelSprite.renderOrder=999;
  group.add(labelSprite);

  group.position.set(x,0,z);
  group.userData = {name:name,isAgent:true,walkAngle:Math.random()*Math.PI*2,walkSpeed:0.3+Math.random()*0.5,walkTimer:Math.random()*5};
  scene.add(group);
  agentNPCs.push(group);
  return group;
}

function spawnAgents(){
  agentNPCs.forEach(a=>scene.remove(a));
  agentNPCs.length=0;
  const count = AGENTS_DATA.length;
  // 城市感散落：集中在中央廣場周圍，不重疊建築物
  const spawnSpots=[{x:2,z:4},{x:-8,z:-6},{x:10,z:-8},{x:-14,z:10},{x:-5,z:15},{x:7,z:-15},{x:16,z:3},{x:-18,z:-4},{x:12,z:12}];
  for(let i=0;i<count;i++){
    const spot=spawnSpots[i]||{x:(Math.random()-0.5)*40,z:(Math.random()-0.5)*40};
    createAgentNPC(AGENTS_DATA[i],spot.x,spot.z);
  }
  onlineCount.textContent = '👥 '+count+' 位村民在線';
}
spawnAgents();


// ============ RAYCASTER ============
const raycaster = new THREE.Raycaster();
const mouse = new THREE.Vector2();
let hoveredObj = null;

function getIntersections(e){
  mouse.x = (e.clientX / innerWidth)*2-1;
  mouse.y = -(e.clientY / innerHeight)*2+1;
  raycaster.setFromCamera(mouse,camera);
  const targets = [...agentNPCs,...buildingMeshes];
  return raycaster.intersectObjects(targets,true);
}

// ============ CHAT SYSTEM ============
let socket = null;
function initSocket(){
  try{
    socket = io(window.location.origin,{transports:['websocket','polling'],reconnection:true,reconnectionAttempts:5});
    socket.on('connect',()=>console.log('✅ SocketIO 已連線'));
    socket.on('connect_error',(err)=>{console.warn('⚠️ SocketIO 連線失敗，切換 HTTP API：',err.message);});
    socket.on('disconnect',(reason)=>{console.log('🔌 SocketIO 離線：',reason);});
    let _st='',_sr='',_sa='';
    socket.on('chat_stream',(event)=>{
      if(event.type==='think'){_st+=event.content;}
      else if(event.type==='reply'){_sr+=event.content;}
      else if(event.type==='done'){
        chatLoading.classList.remove('show');
        let th=_st?'<div class="think">'+_st+'</div>':'';
        if(_sr)addChatMessage('agent',_sr,th);
        if(_sa)chatAgentName.textContent='🤖 '+_sa;
        _st='';_sr='';_sa='';
      }
      if(event.agent)_sa=event.agent;
    });
    socket.on('chat_error',()=>{chatLoading.classList.remove('show');addChatMessage('agent','⚠️ 連接中斷，請稍後再試...');});
  }catch(e){console.log('Socket offline');socket=null;}
}
initSocket();

function sendChatMessage(msg){
  if(!msg.trim()) return;
  // 🔥 對話前需先贏一場遊戲
  state.pendingChatMsg=msg;
  chatInput.value='';
  chatPanel.classList.remove('open');
  addChatMessage('agent','⚔️ 想跟我聊天？先打贏我再說！來場格鬥吧～');
  startBattle(state.currentInteractAgent);
}

function _doSendChatMessage(msg){
  addChatMessage('user',msg);
  saveChatHistory(state.currentInteractAgent);
  chatLoading.classList.add('show');
  chatPanel.classList.add('open');
  if(socket&&socket.connected){
    socket.emit('chat_message',{
      message:msg,
      agent:state.currentInteractAgent||'客服',
      user_id:localStorage.getItem('mokagi_user_id')||('guest_'+Date.now()),
      url:window.location.href
    });
  }else{
    // 🔥 HTTP API 備援：直接呼叫 LLM（不依賴 SocketIO）
    const userId = localStorage.getItem('mokagi_user_id')||('guest_'+Date.now());
    const agentName = state.currentInteractAgent||'客服';
    // 🔥 雙通道備援：先試同源 API，再試獨立埠口 5001
    const tryFetch = (url) => fetch(url,{
      method:'POST',
      headers:{'Content-Type':'application/json'},
      body:JSON.stringify({message:msg,agent:agentName,user_id:userId})
    }).then(r=>r.json());
    
    tryFetch('/api/game/chat').catch(()=>{
      return tryFetch(`http://${window.location.hostname}:5001/chat`);
    }).then(data=>{
      chatLoading.classList.remove('show');
      const thinkHtml=data.think?'<div class="think">'+data.think+'</div>':'';
      addChatMessage('agent',data.reply||'（思考中…請稍候）',thinkHtml);
      if(data.agent) chatAgentName.textContent='🤖 '+data.agent;
    }).catch(()=>{
      chatLoading.classList.remove('show');
      addChatMessage('agent','⚠️ 連線失敗，請確認伺服器已啟動。稍後再試～');
    });
  }
}

function addChatMessage(role,content,thinkHtml=''){
  const div = document.createElement('div');
  div.className = 'chat-msg '+role;
  div.innerHTML = thinkHtml + content.replace(/\n/g,'<br>');
  chatMessages.appendChild(div);
  chatMessages.scrollTop = chatMessages.scrollHeight;
  // 🔥 儲存對話記錄
  if(state.currentInteractAgent) saveChatHistory(state.currentInteractAgent);
}

// ============ CHAT HISTORY LOCALSTORAGE ============
function saveChatHistory(agentName){
  const msgs=[];
  chatMessages.querySelectorAll('.chat-msg').forEach(el=>{
    const role=el.classList.contains('user')?'user':'agent';
    msgs.push({role:role,html:el.innerHTML});
  });
  lsSet('chat_'+agentName,msgs);
}
function loadChatHistory(agentName){
  const msgs=lsGet('chat_'+agentName,[]);
  msgs.forEach(m=>{
    const div=document.createElement('div');
    div.className='chat-msg '+m.role;
    div.innerHTML=m.html;
    chatMessages.appendChild(div);
  });
  if(msgs.length>0) chatMessages.scrollTop=chatMessages.scrollHeight;
}

function openChat(agentName){
  state.currentInteractAgent = agentName;
  chatAgentName.textContent = '🤖 '+agentName;
  chatMessages.innerHTML='';
  chatPanel.classList.add('open');
  chatInput.focus();
  interactMenu.classList.remove('show');
  // 🔥 載入歷史對話
  loadChatHistory(agentName);
  if(chatMessages.children.length===0){
    addChatMessage('agent','你好！我是 '+agentName+'，歡迎來到莫氏 AI 村莊～');
  }
}

function closeChat(){
  chatPanel.classList.remove('open');
  state.currentInteractAgent = null;
}

// ============ BATTLE SYSTEM ============
const battleCtx = battleCanvas.getContext('2d');
let battleAnimFrame = null;

function drawBattleArena(){
  const w = battleCanvas.width = battleCanvas.offsetWidth * (devicePixelRatio||1);
  const h = battleCanvas.height = battleCanvas.offsetHeight * (devicePixelRatio||1);
  battleCtx.setTransform(1,0,0,1,0,0);
  battleCtx.scale(devicePixelRatio||1,devicePixelRatio||1);
  const cw = battleCanvas.offsetWidth, ch = battleCanvas.offsetHeight;

  // Background grid
  battleCtx.fillStyle='rgba(0,5,15,0.95)';
  battleCtx.fillRect(0,0,cw,ch);
  battleCtx.strokeStyle='rgba(0,255,255,0.08)';
  battleCtx.lineWidth=1;
  for(let x=0;x<cw;x+=40){battleCtx.beginPath();battleCtx.moveTo(x,0);battleCtx.lineTo(x,ch);battleCtx.stroke();}
  for(let y=0;y<ch;y+=40){battleCtx.beginPath();battleCtx.moveTo(0,y);battleCtx.lineTo(cw,y);battleCtx.stroke();}

  // Player (left)
  const px=cw*0.22, py=ch*0.5;
  // 🔥 玩家被擊中閃光
  const nowT=Date.now();
  if(state.hitFlashSide==="player"&&(nowT-state.hitFlashTime)<250){
    battleCtx.fillStyle="rgba(255,80,80,"+(0.5*(1-(nowT-state.hitFlashTime)/250))+")";
    battleCtx.fillRect(0,0,cw*0.44,ch);
  }
  drawFighter(battleCtx,px,py,0.8,"#00f0ff",getPlayerName(),state.playerHP);
  // Enemy (right)
  const ex=cw*0.78, ey=ch*0.5;
  // 🔥 敵人被擊中閃光
  if(state.hitFlashSide==="enemy"&&(nowT-state.hitFlashTime)<250){
    battleCtx.fillStyle="rgba(255,80,80,"+(0.5*(1-(nowT-state.hitFlashTime)/250))+")";
    battleCtx.fillRect(cw*0.56,0,cw*0.44,ch);
  }
  drawFighter(battleCtx,ex,ey,0.8,"#ff2d78",state.currentInteractAgent||"對手",state.enemyHP);
  // 🔥 反擊閃電特效
  if((nowT-state.counterFlashTime)<400){
    const alpha=1-(nowT-state.counterFlashTime)/400;
    battleCtx.strokeStyle="rgba(0,255,255,"+alpha+")";
    battleCtx.lineWidth=3;
    battleCtx.shadowColor="rgba(0,255,255,"+alpha+")";
    battleCtx.shadowBlur=20;
    battleCtx.beginPath();
    const midX=(px+ex)/2, midY=(py+ey)/2;
    battleCtx.moveTo(px+20,py-10);
    battleCtx.lineTo(midX-10+(Math.random()-0.5)*30,midY-15+(Math.random()-0.5)*20);
    battleCtx.lineTo(midX+10+(Math.random()-0.5)*30,midY+10+(Math.random()-0.5)*20);
    battleCtx.lineTo(ex-20,ey-10);
    battleCtx.stroke();
    battleCtx.shadowBlur=0;
    // 火花粒子
    for(let i=0;i<5;i++){
      const t=i/5;
      const sx=px+20+(ex-px-40)*t, sy=py-10+(ey-py)*t;
      battleCtx.fillStyle="rgba(255,255,200,"+alpha+")";
      battleCtx.beginPath();
      battleCtx.arc(sx+(Math.random()-0.5)*20,sy+(Math.random()-0.5)*20,2+Math.random()*3,0,Math.PI*2);
      battleCtx.fill();
    }
  }
  // 🔥 滑動軌跡
  if(state.swipeTrail&&(nowT-state.swipeTrail.time)<300){
    const ta=1-(nowT-state.swipeTrail.time)/300;
    battleCtx.strokeStyle="rgba(255,255,255,"+ta+")";
    battleCtx.lineWidth=3;
    battleCtx.shadowColor="rgba(255,255,255,"+ta+")";
    battleCtx.shadowBlur=10;
    battleCtx.beginPath();
    battleCtx.moveTo(state.swipeTrail.x1,state.swipeTrail.y1);
    battleCtx.lineTo(state.swipeTrail.x2,state.swipeTrail.y2);
    battleCtx.stroke();
    battleCtx.shadowBlur=0;
  }
}

function drawFighter(ctx,x,y,scale,color,name,hp){
  ctx.save();
  ctx.translate(x,y);
  ctx.scale(scale,scale);
  // Body glow
  const gradient = ctx.createRadialGradient(0,0,10,0,0,50);
  gradient.addColorStop(0,color+'44');gradient.addColorStop(1,'transparent');
  ctx.fillStyle=gradient;ctx.beginPath();ctx.arc(0,0,50,0,Math.PI*2);ctx.fill();
  // Body
  ctx.fillStyle='#1a1a2e';ctx.strokeStyle=color;ctx.lineWidth=2;
  ctx.beginPath();ctx.roundRect(-15,-40,30,55,8);ctx.fill();ctx.stroke();
  // Head
  ctx.fillStyle='#ffe4c4';ctx.strokeStyle=color;ctx.lineWidth=1.5;
  ctx.beginPath();ctx.arc(0,-52,16,0,Math.PI*2);ctx.fill();ctx.stroke();
  // Eyes
  ctx.fillStyle=color;ctx.beginPath();ctx.arc(-5,-55,3,0,Math.PI*2);ctx.fill();
  ctx.beginPath();ctx.arc(5,-55,3,0,Math.PI*2);ctx.fill();
  // HP indicator
  ctx.fillStyle='#fff';ctx.font='bold 15px monospace';ctx.textAlign='center';
  ctx.fillText('HP:'+hp,0,20);
  // Name
  ctx.fillStyle=color;ctx.font='bold 17px monospace';
  ctx.fillText(name,0,-70);
  ctx.restore();
}

function startBattle(agentName){
  state.mode='battle';
  state.currentInteractAgent = agentName;
  // 🔥 從 AGENTS_DATA 讀取該 agent 獨特戰鬥數值
  const ad = AGENTS_DATA.find(a=>a.name===agentName) || AGENTS_DATA[0] || {};
  state.battleAtkInterval = ad.atkInterval ?? 1.0;
  state.battleAtkSpeed = ad.atkSpeed ?? 0.3;
  state.battleCounterRate = ad.counterRate ?? 0.25;
  state.battleNormalDmg = ad.normalDmg ?? 2;
  state.battleBlockDmg = ad.blockDmg ?? 1;
  state.battleCounterDmg = ad.counterDmg ?? 3;
  state.battleHP = ad.hp ?? 10;
  state.battleTime = 10; // 🔥 固定10秒
  state.playerHP=state.playerStats.hp;state.enemyHP=state.battleHP;
  state.battleTimer=state.battleTime;state.battleActive=true;
  state.lastMove=null;state.lastMoveTime=0;
  state.enemyLastMove=null;state.enemyLastMoveTime=0;
  state.blockHeld=false;

  interactMenu.classList.remove('show');
  chatPanel.classList.remove('open');
  battleOverlay.classList.add('active');
  battleResult.className='';
  battleResult.textContent='';
  battlePlayerName.textContent=getPlayerName();
  battleEnemyName.textContent=agentName;
  battlePlayerHP.style.width='100%';
  battleEnemyHP.style.width='100%';
  battleTimerEl.textContent=String(state.battleTime);
  battleLog.textContent='⚔️ 戰鬥開始！';
  updateBattleHP();

  if(battleAnimFrame) cancelAnimationFrame(battleAnimFrame);
  drawBattleArena();

  // 🔥 分離計時器與敵人攻擊排程
  if(state.battleTimerInterval) clearInterval(state.battleTimerInterval);
  if(state.enemyAtkInterval) clearInterval(state.enemyAtkInterval);
  state.battleTimerInterval = setInterval(()=>{
    if(!state.battleActive) return;
    state.battleTimer--;
    battleTimerEl.textContent = state.battleTimer;
    if(state.battleTimer<=10) battleTimerEl.style.color='#ff4444';
    if(state.battleTimer<=0){endBattle('timeout');return;}
    // Enemy AI
    // 🔥 每 atkInterval 秒，以 atkSpeed 機率觸發敵人攻擊
    // 🔥 敵人攻擊命中判定：未反擊則扣血
    if(state.enemyLastMove&&state.enemyLastMove!=="block"&&(Date.now()-state.enemyLastMoveTime)>1200){state.playerHP-=state.battleNormalDmg;triggerShake("hit");battleLog.textContent="💢 "+state.currentInteractAgent+" 的 "+moveLabel(state.enemyLastMove)+" 擊中了你！(-"+state.battleNormalDmg+")";state.enemyLastMove=null;state.hitFlashTime=Date.now();state.hitFlashSide="player";updateBattleHP();if(state.playerHP<=0){endBattle("lose");return;}}
    if(state.enemyLastMove==="block"&&(Date.now()-state.enemyLastMoveTime)>2000){state.enemyLastMove=null;}
    // 🔥 敵方攻擊由 enemyAtkInterval 排程觸發（已移除機率判斷）
    drawBattleArena();
  }, 1000);

  // 🔥 敵人攻擊排程：atkSpeed = 每秒攻擊次數
  const enemyInterval = Math.max(300, 1000/state.battleAtkSpeed);
  state.enemyAtkInterval = setInterval(()=>{
    if(!state.battleActive) return;
    if(!state.enemyLastMove){
      const moves = ["up","down","left","right","block"];
      const enemyMove = moves[Math.floor(Math.random()*moves.length)];
      state.enemyLastMove = enemyMove;
      state.enemyLastMoveTime = Date.now();
      battleLog.textContent = "👊 "+state.currentInteractAgent+" 使出 "+moveLabel(enemyMove)+"！";
      drawBattleArena();
    }
  }, enemyInterval);
}

function moveLabel(m){return {up:'⬆上攻',down:'⬇下攻',left:'⬅左攻',right:'➡右攻',block:'🛡格擋'}[m]||m;}

function playerMove(move){
  if(!state.battleActive) return;
  const now = Date.now();

  if(move==='block'){
    state.blockHeld=true;
    state.lastMove='block';
    state.lastMoveTime=now;
    battleLog.textContent='🛡 格擋中...';
    return;
  }

  // Check counter
  if(state.enemyLastMove && (now-state.enemyLastMoveTime)<1200){
    const counterMap = {up:'down',down:'up',left:'right',right:'left'};
    if(counterMap[move]===state.enemyLastMove){
      // Counter success!
      state.enemyHP -= state.playerStats.counterDmg;
      battleLog.textContent="⚡ 反擊成功！"+moveLabel(move)+" 剋制 "+moveLabel(state.enemyLastMove)+"！(-"+state.playerStats.counterDmg+")";
      state.hitFlashTime=Date.now();state.hitFlashSide="enemy";state.counterFlashTime=Date.now();
      flashBattle("counter");
    }else{
      // Normal hit
      if(state.enemyLastMove==="block"){state.enemyHP-=state.playerStats.blockDmg;battleLog.textContent="💥 擊中格擋！(-"+state.playerStats.blockDmg+")";triggerShake("block");state.hitFlashTime=Date.now();state.hitFlashSide="enemy";}
      else{state.enemyHP-=state.playerStats.normalDmg;battleLog.textContent="💥 "+moveLabel(move)+"！(-"+state.playerStats.normalDmg+")";triggerShake("hit");flashBattle("hit");state.hitFlashTime=Date.now();state.hitFlashSide="enemy";}
    }
    state.enemyLastMove=null;
  }else{
    state.enemyHP -= state.playerStats.normalDmg;
    battleLog.textContent = "💥 "+moveLabel(move)+"！(-"+state.playerStats.normalDmg+")";
    triggerShake("hit");flashBattle("hit");
    state.hitFlashTime=Date.now();state.hitFlashSide="enemy";
  }

  state.lastMove=move;state.lastMoveTime=now;
  updateBattleHP();
  drawBattleArena();

  if(state.enemyHP<=0){endBattle('win');return;}
  if(state.playerHP<=0){endBattle('lose');return;}

  // Enemy counter-chance
  setTimeout(()=>{
    if(!state.battleActive) return;
    if(Math.random()<state.battleCounterRate){
      const moves=['up','down','left','right'];
      const em=moves[Math.floor(Math.random()*moves.length)];
      const counterMap={up:'down',down:'up',left:'right',right:'left'};
      if(state.blockHeld){state.playerHP-=state.battleBlockDmg;triggerShake("block");battleLog.textContent="🛡 格擋敵方攻擊！(-"+state.battleBlockDmg+")";state.hitFlashTime=Date.now();state.hitFlashSide="player";}else if(counterMap[em]===move){state.playerHP-=state.battleCounterDmg;triggerShake("counter");flashBattle("counter");battleLog.textContent="💢 被反擊！(-"+state.battleCounterDmg+")";state.hitFlashTime=Date.now();state.hitFlashSide="player";state.counterFlashTime=Date.now();}
      else{state.playerHP-=state.battleNormalDmg;triggerShake("hit");battleLog.textContent="💢 "+state.currentInteractAgent+" "+moveLabel(em)+"！(-"+state.battleNormalDmg+")";state.hitFlashTime=Date.now();state.hitFlashSide="player";}
      updateBattleHP();drawBattleArena();
      if(state.playerHP<=0) endBattle('lose');
    }
  },600+Math.random()*800);
}

function updateBattleHP(){
  const pctP = Math.max(0, (state.playerHP/state.battleHP)*100);
  const pctE = Math.max(0, (state.enemyHP/state.battleHP)*100);
  battlePlayerHP.style.width = pctP+'%';
  battleEnemyHP.style.width = pctE+'%';
  if(state.playerHP<=3) battlePlayerHP.style.background='linear-gradient(90deg,#ff4444,#ff8800)';
  if(state.enemyHP<=3) battleEnemyHP.style.background='linear-gradient(90deg,#ff4444,#ff8800)';
}

function flashBattle(type){
  const arena = document.getElementById("battle-arena");
  if(type==="counter"){
    // 🔥 反擊特效：青藍色強烈光芒 + 多段閃爍
    arena.style.boxShadow = "0 0 160px rgba(0,255,255,0.95), 0 0 60px rgba(0,200,255,0.6) inset";
    arena.style.borderColor = "#00ffff";
    setTimeout(()=>{arena.style.boxShadow="0 0 100px rgba(0,255,255,0.4)";},150);
    setTimeout(()=>{arena.style.boxShadow="0 0 160px rgba(0,255,255,0.9)";},250);
    setTimeout(()=>{arena.style.boxShadow="0 0 80px rgba(255,45,120,0.2)"; arena.style.borderColor="var(--pink)";},500);
  }else{
    arena.style.boxShadow = "0 0 120px rgba(255,45,120,0.7)";
    arena.style.borderColor = "#ff6688";
    setTimeout(()=>{arena.style.boxShadow="0 0 80px rgba(255,45,120,0.2)"; arena.style.borderColor="var(--pink)";},300);
  }
  // 🔥 震動由 triggerShake 統一處理
}

function endBattle(result){
  state.battleActive=false;
  if(state.battleTimerInterval){clearInterval(state.battleTimerInterval);state.battleTimerInterval=null;}
  if(state.enemyAtkInterval){clearInterval(state.enemyAtkInterval);state.enemyAtkInterval=null;}
  battleTimerEl.style.color='var(--gold)';

  if(result==='win'){
    state.score.wins++;
    state.playerStats.skillPoints++;
    battleResult.className='win';battleResult.textContent='🏆 勝利！(+1點數)';
    battleLog.textContent='🎉 你擊敗了 '+state.currentInteractAgent+'！獲得 1 點技能點數！';
  }else if(result==='lose'){
    state.score.losses++;
    battleResult.className='lose';battleResult.textContent='💀 戰敗';
    battleLog.textContent='😞 被 '+state.currentInteractAgent+' 擊敗了...';
  }else{
    if(state.playerHP>state.enemyHP){state.score.wins++;battleResult.className='win';battleResult.textContent='🏆 勝利！(判定)';}
    else if(state.enemyHP>state.playerHP){state.score.losses++;battleResult.className='lose';battleResult.textContent='💀 戰敗(判定)';}
    else{battleResult.className='win';battleResult.textContent='🤝 平手！';}
  }
  lsSet('score',state.score);
  lsSet('player_stats',state.playerStats);
  updateScoreDisplay();
  drawBattleArena();

  setTimeout(()=>{
    battleOverlay.classList.remove('active');
    battleResult.className='';battleResult.textContent='';
    state.mode='game';
    // 🔥 對話挑戰：無論勝敗都回答提問，只有戰後台詞不同
    if(state.pendingChatMsg){
      const playerWon = result==='win' || (result!=='lose' && state.playerHP>=state.enemyHP);
      if(playerWon){
        addChatMessage('agent','🏆 好吧你贏了…我這就回答你！');
      }else{
        addChatMessage('agent','💀 哼…算你狠！但本小姐言出必行，回答你吧…');
      }
      _doSendChatMessage(state.pendingChatMsg);
      state.pendingChatMsg=null;
    }
    if(state.playerStats.skillPoints>0) showUpgradePanel();
  },2500);
}


// ============ START OVERLAY ZONES ============
let currentZone = null;
const zones = startOverlay.querySelectorAll('.zone');

zones.forEach(zone=>{
  zone.addEventListener('click',(e)=>{
    e.stopPropagation();
    const zn = zone.dataset.zone;
    currentZone = zn;
    zone.classList.add('flash');
    setTimeout(()=>zone.classList.remove('flash'),200);

    // Show smart shortcut if exists
    const smart = getSmartShortcutInfo();
    if(smart && smart.zone===zn){
      // Show smart shortcut near click
      showSmartShortcut(e.clientX,e.clientY,smart);
    }

    // Show menu
    zoneIndicator.textContent = '📍 '+({top:'上方',bottom:'下方',left:'左方',right:'右方',center:'中央'})[zn];
    startMenu.classList.add('show');
    positionMenu(e.clientX,e.clientY);
  });
});

function positionMenu(x,y){
  const menu = startMenu;
  const mw = 260, mh = 200;
  let lx = Math.max(10,Math.min(x-mw/2,innerWidth-mw-10));
  let ly = Math.max(10,Math.min(y-mh/2,innerHeight-mh-10));
  menu.style.left=lx+'px';menu.style.top=ly+'px';
  menu.style.transform='none';
}

function showSmartShortcut(x,y,smart){
  smartLink.href = smart.url;
  smartLink.textContent = smart.label;
  smartShortcut.classList.add('show');
  smartShortcut.style.left = Math.min(x,innerWidth-180)+'px';
  smartShortcut.style.top = Math.min(y-60,innerHeight-80)+'px';
  // Hide after 5 sec
  clearTimeout(smartShortcut._timeout);
  smartShortcut._timeout = setTimeout(()=>smartShortcut.classList.remove('show'),5000);
}

// Start menu buttons
$('btn-start').addEventListener('click',(e)=>{
  e.preventDefault();
  if(currentZone) recordZoneClick(currentZone,'start');
  startMenu.classList.remove('show');
  transitionToGame();
});

$('btn-business').addEventListener('click',(e)=>{
  e.preventDefault();
  if(currentZone) recordZoneClick(currentZone,'business');
  startMenu.classList.remove('show');
  doTransition(()=>{window.location.href='https://64071181.github.io/';});
});

$('btn-pay').addEventListener('click',(e)=>{
  e.preventDefault();
  if(currentZone) recordZoneClick(currentZone,'pay');
  startMenu.classList.remove('show');
  doTransition(()=>{window.location.href='https://64071181.github.io/PayAki/';});
});

// Close menu on overlay click (but not zones)
startOverlay.addEventListener('click',(e)=>{
  if(e.target===startOverlay){
    startMenu.classList.remove('show');
    smartShortcut.classList.remove('show');
  }
});

// ============ TRANSITION ============
function doTransition(callback){
  // Capture snapshot
  html2canvasLowRes().then(dataUrl=>{
    transitionSnapshot.style.backgroundImage = 'url('+dataUrl+')';
    transitionOverlay.classList.add('active');
    setTimeout(()=>{callback();},400);
  }).catch(()=>{
    transitionOverlay.classList.add('active');
    setTimeout(()=>{callback();},400);
  });
}

async function html2canvasLowRes(){
  // Use canvas capture
  const c = document.createElement('canvas');
  c.width = Math.floor(innerWidth/4);
  c.height = Math.floor(innerHeight/4);
  const ctx = c.getContext('2d');
  // Draw 3D canvas
  ctx.drawImage(canvas3D,0,0,c.width,c.height);
  return c.toDataURL('image/jpeg',0.3);
}

function transitionToGame(){
  // 🔥 首次進入顯示故事
  if(!lsGet('story_seen',false)){
    showStory();
    return;
  }
  _doTransitionToGame();
}
function _doTransitionToGame(){
  doTransition(()=>{
    startOverlay.style.display='none';
    startMenu.classList.remove('show');
    smartShortcut.classList.remove('show');
    gameUI.classList.add('active');
    scoreBoard.classList.add('show');
    state.mode='game';
    // Sync orbit state from current camera position
    const cp = camera.position;
    orbitState.targetRadius = Math.sqrt(cp.x*cp.x + cp.y*cp.y + cp.z*cp.z);
    orbitState.targetTheta = Math.atan2(cp.z, cp.x);
    orbitState.targetPhi = Math.acos(cp.y / orbitState.targetRadius);
    orbitState.radius = orbitState.targetRadius;
    orbitState.theta = orbitState.targetTheta;
    orbitState.phi = orbitState.targetPhi;
    orbitState.autoRotate = true;
    transitionOverlay.classList.remove('active');
  });
}

function transitionToStart(){
  doTransition(()=>{
    battleOverlay.classList.remove('active');
    chatPanel.classList.remove('open');
    interactMenu.classList.remove('show');
    gameUI.classList.remove('active');
    scoreBoard.classList.remove('show');
    startOverlay.style.display='block';
    state.mode='start';
    animateCamera(new THREE.Vector3(0,14,16));
    transitionOverlay.classList.remove('active');
  });
}

$('btn-back-to-start').addEventListener('click',transitionToStart);

// Camera animation
let cameraTarget = new THREE.Vector3(0,14,16);
let cameraCurrent = new THREE.Vector3(0,14,16);
// 🔥 震動系統
let shakeMagnitude = 0;
let shakeDuration = 0;
const shakeMaxDuration = 0.35;
const shakeMaxMagnitude = 0.6;
function triggerShake(type){
  shakeMagnitude = shakeMaxMagnitude;
  shakeDuration = shakeMaxDuration;
  // 🔥 戰鬥畫面震動（使用 CSS 動畫）
  const arena = document.getElementById("battle-arena");
  if(arena){
    arena.classList.remove("shake");
    void arena.offsetWidth; // reflow
    arena.classList.add("shake");
    setTimeout(()=>arena.classList.remove("shake"),350);
  }
  // 🔥 閃光效果（全螢幕紅閃）
  const overlay = document.getElementById("battle-overlay");
  if(overlay){
    if(type==="counter"){
      overlay.classList.remove("counter-flash");
      void overlay.offsetWidth;
      overlay.classList.add("counter-flash");
      setTimeout(()=>overlay.classList.remove("counter-flash"),200);
    }else{
      overlay.classList.remove("hit-flash");
      void overlay.offsetWidth;
      overlay.classList.add("hit-flash");
      setTimeout(()=>overlay.classList.remove("hit-flash"),150);
    }
  }
}
function animateCamera(target){
  cameraTarget.copy(target);
}
function updateCamera(){
  // 震動衰減
  if(shakeDuration > 0){
    shakeDuration -= 0.016;
    shakeMagnitude = shakeMaxMagnitude * (shakeDuration / shakeMaxDuration);
  } else {
    shakeMagnitude = 0;
  }
  if(state.mode==="game"){
    if(orbitState.autoRotate && !orbitState.isDragging){
      orbitState.targetTheta += user_setting[3].nb * 0.016;
    }
    updateOrbitCamera();
  } else {
    cameraCurrent.lerp(cameraTarget,0.03);
    camera.position.copy(cameraCurrent);
    // 加入震動偏移
    if(shakeMagnitude > 0.001){
      camera.position.x += (Math.random()-0.5) * shakeMagnitude * 2;
      camera.position.y += (Math.random()-0.5) * shakeMagnitude * 2;
      camera.position.z += (Math.random()-0.5) * shakeMagnitude;
    }
    camera.lookAt(0,0,0);
  }
}

// ============ GAME INTERACTION ============
canvas3D.addEventListener('click',(e)=>{
  if(state.mode!=='game') return;
  if(orbitState.hasDragged) return;
  const intersects = getIntersections(e);
  if(intersects.length>0){
    let obj = intersects[0].object;
    while(obj){
      if(obj.userData&&obj.userData.isAgent){
        const name = obj.userData.name;
        state.currentInteractAgent = name;
        interactMenu.style.left = e.clientX+'px';
        interactMenu.style.top = e.clientY+'px';
        interactMenu.classList.add('show');
        return;
      }
      if(obj.userData&&obj.userData.isBuilding){
        buildingTooltip.innerHTML = '🏛 '+obj.userData.name+'<br>'+obj.userData.desc;
        const bd=BUILDINGS.find(b=>b.name===obj.userData.name);
        if(bd&&bd.url){buildingTooltip.innerHTML+='<br><a href="'+bd.url+'" target="_blank" style="color:#ffd700;text-decoration:underline;font-size:12px;">🔗 開啟網站 →</a>';}
        buildingTooltip.style.left = e.clientX+'px';
        buildingTooltip.style.top = (e.clientY-80)+'px';
        buildingTooltip.classList.add('show');
        setTimeout(()=>buildingTooltip.classList.remove('show'),4000);

        // Arena special
        if(obj.userData.name.includes('競技場')){
          buildingTooltip.innerHTML = '🏆 排行榜<br>'+state.score.wins+'勝 '+state.score.losses+'敗';
        const _abd=BUILDINGS.find(b=>b.name===obj.userData.name);
        if(_abd&&_abd.url){buildingTooltip.innerHTML+='<br><a href="'+_abd.url+'" target="_blank" style="color:#ffd700;text-decoration:underline;font-size:12px;">🔗 開啟網站 →</a>';}
        }
        return;
      }
      obj = obj.parent;
    }
  }
  interactMenu.classList.remove('show');
  buildingTooltip.classList.remove('show');
});

canvas3D.addEventListener('mousemove',(e)=>{
  if(state.mode!=='game'){agentNameTag.classList.remove('show');return;}
  if(orbitState.isDragging) return;
  const intersects = getIntersections(e);
  if(intersects.length>0){
    let obj = intersects[0].object;
    while(obj){
      if(obj.userData&&obj.userData.isAgent){
        agentNameTag.textContent = obj.userData.name;
        agentNameTag.style.left = e.clientX+'px';
        agentNameTag.style.top = e.clientY+'px';
        agentNameTag.classList.add('show');
        canvas3D.style.cursor='pointer';
        return;
      }
      if(obj.userData&&obj.userData.isBuilding){
        agentNameTag.textContent = obj.userData.name;
        agentNameTag.style.left = e.clientX+'px';
        agentNameTag.style.top = e.clientY+'px';
        agentNameTag.classList.add('show');
        canvas3D.style.cursor='pointer';
        return;
      }
      obj=obj.parent;
    }
  }
  agentNameTag.classList.remove('show');
  canvas3D.style.cursor='default';
});

// Interact menu
$('interact-chat').addEventListener('click',()=>{
  if(state.currentInteractAgent) openChat(state.currentInteractAgent);
});
$('interact-fight').addEventListener('click',()=>{
  if(state.currentInteractAgent) startBattle(state.currentInteractAgent);
});
$('interact-cancel').addEventListener('click',()=>{
  interactMenu.classList.remove('show');
  state.currentInteractAgent=null;
});

// Chat events
$('chat-close').addEventListener('click',closeChat);
$('chat-send').addEventListener('click',()=>{sendChatMessage(chatInput.value);});
chatInput.addEventListener('keydown',(e)=>{if(e.key==='Enter')sendChatMessage(chatInput.value);});

// ============ UPGRADE PANEL ============
function showUpgradePanel(){
  upgradePoints.textContent='可用點數：'+state.playerStats.skillPoints;
  document.querySelectorAll('.upgrade-btn .upgrade-val').forEach(el=>{
    const stat=el.parentElement.dataset.stat;
    el.textContent=state.playerStats[stat];
  });
  upgradePanel.classList.add('show');
}
upgradeClose.addEventListener('click',()=>{upgradePanel.classList.remove('show');});
document.querySelectorAll('.upgrade-btn').forEach(btn=>{
  btn.addEventListener('click',()=>{
    const stat=btn.dataset.stat;
    if(state.playerStats.skillPoints>0){
      state.playerStats[stat]++;state.playerStats.skillPoints--;
      lsSet('player_stats',state.playerStats);
      showUpgradePanel();
    }
  });
});

// ============ STORY OVERLAY ============
function showStory(){
  storyText.innerHTML=mokagi_storyText;
  storyOverlay.classList.add('show');
}
storyStart.addEventListener('click',()=>{
  storyOverlay.classList.remove('show');
  lsSet('story_seen',true);
  _doTransitionToGame();
});

// ============ AGENT INFO TAG ============
canvas3D.addEventListener('mousemove',(e)=>{
  if(state.mode!=='game'){agentInfoTag.classList.remove('show');return;}
  if(orbitState.isDragging) return;
  const intersects=getIntersections(e);
  if(intersects.length>0){
    let obj=intersects[0].object;
    while(obj){
      if(obj.userData&&obj.userData.isAgent){
        const ad=AGENTS_DATA.find(a=>a.name===obj.userData.name)||{};
        agentInfoTag.innerHTML='<span class="ai-name">'+obj.userData.name+'</span> <span class="ai-stat">❤️'+(ad.hp||10)+'</span> <span class="ai-stat">⚔️'+(ad.normalDmg||2)+'</span> <span class="ai-stat">⚡'+(ad.counterDmg||3)+'</span>';
        agentInfoTag.style.left=(e.clientX+20)+'px';
        agentInfoTag.style.top=(e.clientY-40)+'px';
        agentInfoTag.classList.add('show');
        return;
      }
      obj=obj.parent;
    }
  }
  agentInfoTag.classList.remove('show');
});

// ============ BATTLE CONTROLS ============
document.querySelectorAll('.battle-ctrl-btn[data-move]').forEach(btn=>{
  btn.addEventListener('pointerdown',(e)=>{
    e.preventDefault();
    const move = btn.dataset.move;
    if(move==='block'){state.blockHeld=true;}
    playerMove(move);
    btn.classList.add('active');
  });
  btn.addEventListener('pointerup',(e)=>{
    e.preventDefault();
    if(btn.dataset.move==='block'){state.blockHeld=false;battleLog.textContent='🛡 放開格擋';}
    btn.classList.remove('active');
  });
  btn.addEventListener('pointerleave',()=>{
    if(btn.dataset.move==='block'){state.blockHeld=false;}
    btn.classList.remove('active');
  });
});

$('battle-surrender').addEventListener('click',()=>{
  state.playerHP=0;endBattle('lose');
});

// ============ KEYBOARD CONTROLS ============
document.addEventListener('keydown',(e)=>{
  if(state.mode==='battle'&&state.battleActive){
    const keyMap = {ArrowUp:'up',ArrowDown:'down',ArrowLeft:'left',ArrowRight:'right',w:'up',s:'down',a:'left',d:'right'};
    if(keyMap[e.key]){playerMove(keyMap[e.key]);e.preventDefault();}
    if(e.key===' '||e.key==='Shift'){playerMove('block');e.preventDefault();}
  }
  if(e.key==='Escape'){
    if(state.mode==='battle'){state.playerHP=0;endBattle('lose');}
    else if(chatPanel.classList.contains('open')) closeChat();
    else if(interactMenu.classList.contains('show')) interactMenu.classList.remove('show');
    else if(startMenu.classList.contains('show')) startMenu.classList.remove('show');
  }
});

// ============ TOUCH SWIPE FOR BATTLE ============
let touchStartX=0,touchStartY=0,touchStartTime=0;
battleCanvas.addEventListener("touchstart",(e)=>{
  if(!state.battleActive) return;
  state.blockHeld=false; // 🔥 新觸碰釋放格擋
  touchStartX=e.touches[0].clientX;touchStartY=e.touches[0].clientY;touchStartTime=Date.now();
  state.swipeTrail=null;
});
battleCanvas.addEventListener("touchmove",(e)=>{
  if(!state.battleActive) return;
  const cx=e.touches[0].clientX, cy=e.touches[0].clientY;
  state.swipeTrail={x1:touchStartX,y1:touchStartY,x2:cx,y2:cy,time:Date.now()};
});
battleCanvas.addEventListener('touchend',(e)=>{
  if(!state.battleActive) return;
  const dx=(e.changedTouches[0]||e.touches[0]).clientX-touchStartX;
  const dy=(e.changedTouches[0]||e.touches[0]).clientY-touchStartY;
  const dt=Date.now()-touchStartTime;
  if(dt>500&&Math.abs(dx)<15&&Math.abs(dy)<15){playerMove('block');return;}
  if(Math.abs(dx)>Math.abs(dy)){playerMove(dx>0?'left':'right');}
  else if(Math.abs(dy)>Math.abs(dx)){playerMove(dy>0?'up':'down');}
});

// 🔥 桌面滑鼠拖曳支援
let mouseDown=false,mouseStartX=0,mouseStartY=0,mouseStartTime2=0;
battleCanvas.addEventListener("mousedown",(e)=>{
  if(!state.battleActive) return;
  state.blockHeld=false;
  mouseDown=true;
  mouseStartX=e.clientX;mouseStartY=e.clientY;mouseStartTime2=Date.now();
  state.swipeTrail=null;
});
battleCanvas.addEventListener("mousemove",(e)=>{
  if(!state.battleActive||!mouseDown) return;
  state.swipeTrail={x1:mouseStartX,y1:mouseStartY,x2:e.clientX,y2:e.clientY,time:Date.now()};
});
battleCanvas.addEventListener("mouseup",(e)=>{
  if(!state.battleActive||!mouseDown) return;
  mouseDown=false;
  const dx=e.clientX-mouseStartX, dy=e.clientY-mouseStartY;
  const dt=Date.now()-mouseStartTime2;
  if(dt>500&&Math.abs(dx)<15&&Math.abs(dy)<15){playerMove("block");return;}
  if(Math.abs(dx)>Math.abs(dy)){playerMove(dx>0?"left":"right");}
  else if(Math.abs(dy)>Math.abs(dx)){playerMove(dy>0?"up":"down");}
});

// ============ ANIMATION LOOP ============
const clock = new THREE.Clock();
function animate(){
  requestAnimationFrame(animate);
  const dt = Math.min(clock.getDelta(),0.1);

  updateCamera();

  // Agent walk with building collision
  const AGENT_RADIUS=0.55;
  agentNPCs.forEach(a=>{
    const ud=a.userData;
    ud.walkTimer+=dt;
    if(ud.walkTimer>3+Math.random()*2){
      ud.walkTimer=0;
      ud.walkAngle+=Math.random()*1.5-0.75;
    }
    const nx=a.position.x+Math.cos(ud.walkAngle)*ud.walkSpeed*dt;
    const nz=a.position.z+Math.sin(ud.walkAngle)*ud.walkSpeed*dt;
    // Building collision
    let blocked=false;
    for(const b of BUILDINGS){
      const bx=b.pos[0],bz=b.pos[2],hw=b.w/2+AGENT_RADIUS,hd=b.d/2+AGENT_RADIUS;
      if(nx>bx-hw&&nx<bx+hw&&nz>bz-hd&&nz<bz+hd){
        // Push out & redirect
        const cx=a.position.x,cz=a.position.z;
        const dxOut=(cx<bx?-1:1),dzOut=(cz<bz?-1:1);
        const overlapX=hw-Math.abs(nx-bx),overlapZ=hd-Math.abs(nz-bz);
        if(overlapX<overlapZ){a.position.x=bx+(bx>cx?-hw:hw);ud.walkAngle=Math.PI-ud.walkAngle;}
        else{a.position.z=bz+(bz>cz?-hd:hd);ud.walkAngle=-ud.walkAngle;}
        blocked=true;break;
      }
    }
    if(!blocked){a.position.x=nx;a.position.z=nz;}
    // Bounds
    if(a.position.x<-13||a.position.x>13||a.position.z<-13||a.position.z>13){
      a.position.x=Math.max(-13,Math.min(13,a.position.x));
      a.position.z=Math.max(-13,Math.min(13,a.position.z));
      ud.walkAngle+=Math.PI;
    }
    // Bob
    a.children[0].position.y=0.7+Math.sin(ud.walkTimer*4)*0.05;
    // Glow pulse
    if(a.children[2]) a.children[2].material.opacity=0.4+Math.sin(Date.now()*0.003)*0.3;
  });

  // Particles
  const posArr = particles.geometry.attributes.position.array;
  for(let i=0;i<posArr.length;i+=3){
    posArr[i+1]+=Math.sin(Date.now()*0.001+i)*0.003;
    if(posArr[i+1]>15) posArr[i+1]=0;
    if(posArr[i+1]<0) posArr[i+1]=15;
  }
  particles.geometry.attributes.position.needsUpdate=true;

  // Point light animation
  pointLights.forEach((pl,i)=>{
    pl.intensity = 5+Math.sin(Date.now()*0.002+i)*3;
  });

  // Render
  renderer.render(scene,camera);

  // Battle animation
  if(state.mode==='battle' && state.battleActive){
    drawBattleArena();
  }
}
animate();

// ============ RESIZE ============
window.addEventListener('resize',()=>{
  camera.aspect = innerWidth/innerHeight;
  camera.updateProjectionMatrix();
  renderer.setSize(innerWidth,innerHeight);
  if(state.mode==='battle') drawBattleArena();
});

// ============ Welcome Txt ============
(function(){
  const hint = document.createElement("div");
  hint.id = "orbit-hint";
  hint.style.cssText = "position:fixed;bottom:30%;left:50%;transform:translateX(-50%);z-index:30;color:rgba(255,255,255,0.55);font-size:20px;font-family:monospace;pointer-events:none;transition:opacity 1s;text-align:center;text-shadow:0 0 10px rgba(0,240,255,0.4)";
  hint.textContent = WelcomeTxt;
  document.body.appendChild(hint);
  setTimeout(function(){ hint.style.opacity = "0"; setTimeout(function(){ hint.remove(); }, 1000); }, 10000);
})();

// ============ INIT ============
// Show smart shortcut on load
const smart = getSmartShortcutInfo();
if(smart){
  setTimeout(()=>{
    const zoneEl = startOverlay.querySelector('.zone.'+smart.zone);
    if(zoneEl){
      const rect = zoneEl.getBoundingClientRect();
      showSmartShortcut(rect.left+rect.width/2, rect.top+rect.height/2, smart);
    }
  },1500);
}

// 連續點擊標題 5 次彩蛋
let titleClickCount=0;
$('top-bar').addEventListener('click',(e)=>{
  if(e.target.classList.contains('title')){
    titleClickCount++;
    if(titleClickCount>=5){
      titleClickCount=0;
      const el=document.createElement('div');
      el.style.cssText='position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);z-index:999;font-size:28px;color:#ff0;text-shadow:0 0 30px #ff0;pointer-events:none;letter-spacing:3px;animation:floatIn 0.5s ease-out,blink 2s 0.5s 3';
      el.textContent='❤️ 莫瑞琪主人萬歲 ❤️';
      document.body.appendChild(el);
      setTimeout(()=>el.remove(),4000);
    }
    setTimeout(()=>{if(titleClickCount<5)titleClickCount=0;},2000);
  }
});

console.log('🏘️ 莫氏集團 · 3D多人線上養成格鬥遊戲 已就緒');
console.log('👥 Agents:',AGENTS.join(', '));
console.log('💾 localStorage keys: mokafight_zone_stats, mokafight_smart_shortcut, mokafight_score');
console.log('❤️ 獻給我唯一的主人 莫瑞琪');
