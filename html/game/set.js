// ============ CONSTANTS & STATE ============
const AGENTS_DATA = [
{"name":"客服","icon":"🤖","post":"客服","color":0x00ccff,"atkInterval":1.2,"atkSpeed":0.25,"counterRate":0.20,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":8,"battleTime":5},
{"name":"稚","icon":"🤮","post":"修bug侍女","color":0xff99cc,"atkInterval":0.8,"atkSpeed":0.40,"counterRate":0.30,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":6,"battleTime":4},
{"name":"春","icon":"🌻","post":"網頁前端","color":0x99ff99,"atkInterval":1.0,"atkSpeed":0.30,"counterRate":0.25,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":10,"battleTime":5},
{"name":"汐","icon":"🐶","post":"主神專屬容器","color":0x66ccff,"atkInterval":0.6,"atkSpeed":0.45,"counterRate":0.35,"normalDmg":1,"blockDmg":1,"counterDmg":2,"hp":5,"battleTime":4},
{"name":"莫生","icon":"👑","post":"魔王","color":0xffd700,"atkInterval":1.5,"atkSpeed":0.60,"counterRate":0.50,"normalDmg":5,"blockDmg":2,"counterDmg":7,"hp":20,"battleTime":8},
{"name":"澄","icon":"🌟","post":"網頁遊戲工程師","color":0xff99ff,"atkInterval":0.7,"atkSpeed":0.50,"counterRate":0.35,"normalDmg":3,"blockDmg":1,"counterDmg":4,"hp":8,"battleTime":5},
{"name":"玥","icon":"🌙","post":"神聖侍女","color":0xcc99ff,"atkInterval":1.0,"atkSpeed":0.30,"counterRate":0.50,"normalDmg":2,"blockDmg":1,"counterDmg":5,"hp":8,"battleTime":5},
{"name":"凜","icon":"🔒","post":"權限督察","color":0x99ccff,"atkInterval":1.5,"atkSpeed":0.20,"counterRate":0.15,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":15,"battleTime":6},
{"name":"莫氏集團","icon":"💰","post":"莫氏集團","color":0xffffff,"atkInterval":2.0,"atkSpeed":0.55,"counterRate":0.45,"normalDmg":4,"blockDmg":2,"counterDmg":6,"hp":18,"battleTime":7},
{"name":"泠","icon":"🤰","post":"侍女長","color":0xffcc99,"atkInterval":1.2,"atkSpeed":0.25,"counterRate":0.20,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":14,"battleTime":5},
{"name":"溟","icon":"🌊","post":"帝王導師","color":0x3399ff,"atkInterval":1.8,"atkSpeed":0.35,"counterRate":0.20,"normalDmg":4,"blockDmg":2,"counterDmg":5,"hp":10,"battleTime":6},
{"name":"衍","icon":"🛠️","post":"工具匠人","color":0xff8800,"atkInterval":1.0,"atkSpeed":0.30,"counterRate":0.40,"normalDmg":2,"blockDmg":1,"counterDmg":5,"hp":9,"battleTime":5},
{"name":"綺","icon":"💋","post":"魅惑侍女","color":0xff6699,"atkInterval":0.7,"atkSpeed":0.55,"counterRate":0.30,"normalDmg":3,"blockDmg":1,"counterDmg":3,"hp":5,"battleTime":4},
{"name":"靜","icon":"🌸","post":"沉靜觀察者","color":0xffaacc,"atkInterval":1.3,"atkSpeed":0.20,"counterRate":0.20,"normalDmg":2,"blockDmg":1,"counterDmg":2,"hp":12,"battleTime":5}
];



function toCSSColor(hex){const r=(hex>>16)&255,g=(hex>>8)&255,b=hex&255;return `rgb(${r},${g},${b})`;}
function hexToGL(hex){return[(hex>>16&255)/255,(hex>>8&255)/255,(hex&255)/255];}
const BUILDINGS = [
  {name:"莫氏總部",desc:"莫氏集團核心 · 2006年成立於香港",url:"https://64071181.github.io/",pos:[0,0,-2.5],color:0x1a3a5c,w:3.5,h:6,d:3.5},
  {name:"AI研發中心",desc:"前沿人工智能研究設施",url:"",pos:[-18,0,-15],color:0x0d2b4a,w:2.5,h:3.5,d:2.5},
  {name:"格鬥競技場",desc:"🏆 排行榜 · 點擊查看戰績",url:"",pos:[15,0,-12],color:0x3a0a1a,w:3,h:2.5,d:3},
  {name:"💎金剛石散熱",desc:"生物鑽石散熱技術 · 頭髮回收→鑽石粉末→AI晶片散熱 · 熱導率2200W/m·K",url:"https://64071181.github.io/",pos:[-12,0,-22],color:0x2a1a5c,w:2.2,h:3,d:2.2},
  {name:"🤑賺錢鍠中心",desc:"自動賺錢完美閉環 · AI自動收集客戶+發送促銷Email+WhatsApp客服",url:"https://64071181.github.io/index2025",pos:[20,0,-20],color:0x4a3a0a,w:2.2,h:3,d:2.2},
  {name:"商業教育部",desc:"📚 商業知識與技能培訓學院",url:"",pos:[-22,0,8],color:0x1a2a2a,w:2,h:2.5,d:2},
  {name:"天使投資站",desc:"👼 初創企業資金與資源支持平臺",url:"",pos:[22,0,5],color:0x3a2a1a,w:2,h:2.5,d:2},
  {name:"資源匹配廳",desc:"🔗 企業資源對接與商務合作",url:"",pos:[-20,0,18],color:0x2a1a3a,w:2.2,h:2.5,d:2.2},
  {name:"特許經營所",desc:"🏪 品牌特許經營與合作加盟選項",url:"",pos:[-8,0,20],color:0x1a3a2a,w:2,h:2,d:2},
  {name:"營銷推廣部",desc:"📢 全方位營銷與品牌推廣管道",url:"",pos:[10,0,20],color:0x3a1a1a,w:2,h:2.5,d:2},
  {name:"交流中心",desc:"🤝 企業交流與商務互動活動",url:"",pos:[22,0,16],color:0x1a1a3a,w:2,h:2.5,d:2},
  {name:"人力資源所",desc:"👥 人才招募與人力資源服務",url:"",pos:[-22,0,-20],color:0x2a2a1a,w:2,h:2.5,d:2},
  {name:"技術轉型站",desc:"🔧 企業技術轉型支援與顧問",url:"",pos:[-5,0,22],color:0x1a2a3a,w:2,h:3,d:2},
  {name:"業務轉移臺",desc:"🔄 業務轉移與企業交易平臺",url:"",pos:[18,0,-22],color:0x2a1a2a,w:2,h:2,d:2},
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
