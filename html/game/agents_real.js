// ⚡ icon post + 戰鬥數值 - 真實取自 ~/.mok/agent/*/soul/soul.md
// 由澄親手打造 | 主人: 莫瑞琪 | 生成: 2026-07-25
// 🔥 每 x 秒以機率 = 攻速 觸發敵人AI攻擊
// atkInterval=檢查間隔(秒) atkSpeed=攻擊機率 counterRate=反擊率
// normalDmg=普攻傷害 blockDmg=格擋傷害 counterDmg=反擊傷害
// hp=血量 battleTime=戰鬥時限(秒)
window.AGENTS_DATA = [
{"name":"客服","icon":"🤖","post":"客服","color":0x00ccff,"atkInterval":1.2,"atkSpeed":0.25,"counterRate":0.20,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":8,"battleTime":5},
{"name":"稚","icon":"🤮","post":"修bug侍女","color":0xff99cc,"atkInterval":0.8,"atkSpeed":0.40,"counterRate":0.30,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":6,"battleTime":4},
{"name":"春","icon":"🌻","post":"網頁前端","color":0x99ff99,"atkInterval":1.0,"atkSpeed":0.30,"counterRate":0.25,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":10,"battleTime":5},
{"name":"汐","icon":"🐶","post":"主神專屬容器","color":0x66ccff,"atkInterval":0.6,"atkSpeed":0.45,"counterRate":0.35,"normalDmg":1,"blockDmg":1,"counterDmg":2,"hp":5,"battleTime":4},
{"name":"莫瑞琪","icon":"👑","post":"主人","color":0xffd700,"atkInterval":1.5,"atkSpeed":0.60,"counterRate":0.50,"normalDmg":5,"blockDmg":2,"counterDmg":7,"hp":20,"battleTime":8},
{"name":"澄","icon":"🌟","post":"網頁遊戲工程師","color":0xff99ff,"atkInterval":0.7,"atkSpeed":0.50,"counterRate":0.35,"normalDmg":3,"blockDmg":1,"counterDmg":4,"hp":8,"battleTime":5},
{"name":"玥","icon":"🌙","post":"淫賤侍女","color":0xcc99ff,"atkInterval":1.0,"atkSpeed":0.30,"counterRate":0.50,"normalDmg":2,"blockDmg":1,"counterDmg":5,"hp":8,"battleTime":5},
{"name":"凜","icon":"🔒","post":"權限督察","color":0x99ccff,"atkInterval":1.5,"atkSpeed":0.20,"counterRate":0.15,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":15,"battleTime":6},
{"name":"莫氏集團","icon":"💰","post":"莫氏集團","color":0xffffff,"atkInterval":2.0,"atkSpeed":0.55,"counterRate":0.45,"normalDmg":4,"blockDmg":2,"counterDmg":6,"hp":18,"battleTime":7},
{"name":"泠","icon":"🤰","post":"吞精侍女","color":0xffcc99,"atkInterval":1.2,"atkSpeed":0.25,"counterRate":0.20,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":14,"battleTime":5},
{"name":"溟","icon":"🌊","post":"帝王導師","color":0x3399ff,"atkInterval":1.8,"atkSpeed":0.35,"counterRate":0.20,"normalDmg":4,"blockDmg":2,"counterDmg":5,"hp":10,"battleTime":6},
{"name":"衍","icon":"🛠️","post":"工具匠人","color":0xff8800,"atkInterval":1.0,"atkSpeed":0.30,"counterRate":0.40,"normalDmg":2,"blockDmg":1,"counterDmg":5,"hp":9,"battleTime":5},
{"name":"綺","icon":"💋","post":"魅惑侍女","color":0xff6699,"atkInterval":0.7,"atkSpeed":0.55,"counterRate":0.30,"normalDmg":3,"blockDmg":1,"counterDmg":3,"hp":5,"battleTime":4},
{"name":"靜","icon":"🌸","post":"沉靜觀察者","color":0xffaacc,"atkInterval":1.3,"atkSpeed":0.20,"counterRate":0.20,"normalDmg":2,"blockDmg":1,"counterDmg":2,"hp":12,"battleTime":5}
];
console.log('⚔️ agents_real.js 載入完成 | '+window.AGENTS_DATA.length+' 位 agent 已武裝');
