#!/usr/bin/env python3
"""
icon post 生成器 - 從真實 .agent soul 檔案讀取 icon 和 post
主人: 莫瑞琪 | 侍女: 澄
"""
import os, re, json, hashlib

AGENT_DIR = "/home/ubuntu/.mok/agent"
OUT_DIR = "/home/ubuntu/.mok/html/mokAfight_OK_0725"

# 已確認的 agent 名單（有 soul.md 且為正式 agent）
KNOWN_AGENTS = [
    "客服", "稚", "春", "汐", "莫瑞琪", "澄", "玥", "凜",
    "莫氏集團", "泠", "溟", "衍", "綺", "靜"
]

# 手動 icon 映射（從 soul 中提取 + 主人指定）
MANUAL_ICON = {
    "客服": "🤖", "稚": "🤮", "春": "🌻", "汐": "🐶",
    "莫瑞琪": "👑", "澄": "🌟", "玥": "🌙", "凜": "🔒",
    "莫氏集團": "💰", "泠": "🤰", "溟": "🌊", "衍": "🛠️",
    "綺": "💋", "靜": "🌸"
}

MANUAL_POST = {
    "客服": "客服", "稚": "修bug侍女", "春": "網頁前端",
    "汐": "主神專屬容器", "莫瑞琪": "主人", "澄": "網頁遊戲工程師",
    "玥": "淫賤侍女", "凜": "權限督察", "莫氏集團": "莫氏集團",
    "泠": "吞精侍女", "溟": "帝王導師", "衍": "插件工程師",
    "綺": "侍女", "靜": "侍女"
}

BATTLE_STATS = {
    "客服":     {"atkInterval":1.2,"atkSpeed":0.25,"counterRate":0.20,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":8,"battleTime":5},
    "稚":       {"atkInterval":0.8,"atkSpeed":0.40,"counterRate":0.30,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":6,"battleTime":4},
    "春":       {"atkInterval":1.0,"atkSpeed":0.30,"counterRate":0.25,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":10,"battleTime":5},
    "汐":       {"atkInterval":0.6,"atkSpeed":0.45,"counterRate":0.35,"normalDmg":1,"blockDmg":1,"counterDmg":2,"hp":5,"battleTime":4},
    "莫瑞琪":   {"atkInterval":1.5,"atkSpeed":0.60,"counterRate":0.50,"normalDmg":5,"blockDmg":2,"counterDmg":7,"hp":20,"battleTime":8},
    "澄":       {"atkInterval":0.7,"atkSpeed":0.50,"counterRate":0.35,"normalDmg":3,"blockDmg":1,"counterDmg":4,"hp":8,"battleTime":5},
    "玥":       {"atkInterval":1.0,"atkSpeed":0.30,"counterRate":0.50,"normalDmg":2,"blockDmg":1,"counterDmg":5,"hp":8,"battleTime":5},
    "凜":       {"atkInterval":1.5,"atkSpeed":0.20,"counterRate":0.15,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":15,"battleTime":6},
    "莫氏集團": {"atkInterval":2.0,"atkSpeed":0.55,"counterRate":0.45,"normalDmg":4,"blockDmg":2,"counterDmg":6,"hp":18,"battleTime":7},
    "泠":       {"atkInterval":1.2,"atkSpeed":0.25,"counterRate":0.20,"normalDmg":2,"blockDmg":1,"counterDmg":3,"hp":14,"battleTime":5},
    "溟":       {"atkInterval":1.8,"atkSpeed":0.35,"counterRate":0.20,"normalDmg":4,"blockDmg":2,"counterDmg":5,"hp":10,"battleTime":6},
    "衍":       {"atkInterval":1.0,"atkSpeed":0.30,"counterRate":0.40,"normalDmg":2,"blockDmg":1,"counterDmg":5,"hp":9,"battleTime":5},
    "綺":       {"atkInterval":0.7,"atkSpeed":0.55,"counterRate":0.30,"normalDmg":3,"blockDmg":1,"counterDmg":3,"hp":5,"battleTime":4},
    "靜":       {"atkInterval":1.3,"atkSpeed":0.20,"counterRate":0.20,"normalDmg":2,"blockDmg":1,"counterDmg":2,"hp":12,"battleTime":5},
}

MANUAL_COLOR = {
    "客服": 0x00ccff, "稚": 0xff99cc, "春": 0x99ff99, "汐": 0x66ccff,
    "莫瑞琪": 0xffd700, "澄": 0xff99ff, "玥": 0xcc99ff, "凜": 0x99ccff,
    "莫氏集團": 0xffffff, "泠": 0xffcc99, "溟": 0x3399ff, "衍": 0xff8800,
    "綺": 0xff6699, "靜": 0xffaacc
}

def hash_color(name):
    """基於名稱生成穩定顏色"""
    h = int(hashlib.md5(name.encode()).hexdigest()[:6], 16)
    # 確保明亮
    r = ((h >> 16) & 0xFF)
    g = ((h >> 8) & 0xFF)
    b = (h & 0xFF)
    # 提高亮度
    r = max(r, 80); g = max(g, 80); b = max(b, 80)
    return (r << 16) | (g << 8) | b

def extract_from_soul(name):
    """從 soul.md 提取 icon 和 post"""
    soul_path = os.path.join(AGENT_DIR, name, "soul", "soul.md")
    if not os.path.exists(soul_path):
        return None, None

    with open(soul_path, 'r', encoding='utf-8') as f:
        content = f.read()

    icon = None
    post = None

    # --- 提取 icon ---
    # 模式1: "# icon\nXXX"
    m = re.search(r'#\s*icon\s*\n\s*(.+?)(?:\n\s*\n|\n#|\n={3,}|\Z)', content, re.DOTALL)
    if m:
        raw = m.group(1).strip()
        # 如果是 "妳自己想" → 跳過
        if '妳自己想' in raw or '自己想' in raw:
            icon = None
        elif len(raw) <= 5:
            # 短文本，可能是 emoji
            icon = raw
        else:
            # 長描述，嘗試提取 emoji
            emoji_m = re.search(r'[\U0001F300-\U0001F9FF\u2600-\u27BF\u2B50\u2700-\u27BF\u3030\u303D\uD83C\uD000-\uDFFF\uD83D\uD000-\uDFFF\uD83E\uD000-\uDFFF]', raw)
            icon = emoji_m.group(0) if emoji_m else None

    # 模式2: "## Emoji\nXXX"
    if not icon:
        m = re.search(r'##\s*Emoji\s*\n\s*(.+?)(?:\n\s*\n|\n#|\n={3,}|\Z)', content, re.DOTALL)
        if m:
            raw = m.group(1).strip()
            emoji_m = re.search(r'[\U0001F300-\U0001F9FF\u2600-\u27BF\u2B50\u2700-\u27BF\u3030\u303D\uD83C\uD000-\uDFFF\uD83D\uD000-\uDFFF\uD83E\uD000-\uDFFF]', raw)
            icon = emoji_m.group(0) if emoji_m else raw[:3]

    # --- 提取 post（從 soul 內容推斷）---
    # 模式: "# 性格\n..." 中尋找關鍵職位詞
    m = re.search(r'#\s*性格\s*\n(.+?)(?:\n#|\n={3,}|\Z)', content, re.DOTALL)
    if m:
        personality = m.group(1).strip()
        # 查找職位關鍵詞
        role_patterns = [
            (r'(?:專業|精通).*?(?:程式|python|工程師|前端)', '工程師'),
            (r'客服', '客服'),
            (r'侍女', '侍女'),
            (r'督察', '督察'),
            (r'教師|導師', '導師'),
            (r'助手', '助手'),
        ]
        for pat, default in role_patterns:
            if re.search(pat, personality):
                post = default
                break

    return icon, post

def build_agents():
    """建立 agents 資料"""
    agents = []
    posts = {}

    for name in KNOWN_AGENTS:
        soul_icon, soul_post = extract_from_soul(name)

        # icon: soul > manual > blank
        icon = soul_icon or MANUAL_ICON.get(name, '')

        # post: manual > soul > blank
        post = MANUAL_POST.get(name, '') or soul_post or ''

        # color: manual > hash
        color = MANUAL_COLOR.get(name, hash_color(name))

        agents.append({
            "name": name,
            "icon": icon,
            "post": post,
            "color": color
        })
        posts[name] = {"post": post, "icon": icon}

    return agents, posts

def main():
    agents, posts = build_agents()

    # 生成 agents_real.js（替代 agents.js）
    js_lines = [
        "// ⚡ icon post - 真實取自 ~/.mok/agent/*/soul/soul.md",
        "// 由 build_icon_post.py 自動生成 | 主人: 莫瑞琪",
        f"// 生成時間: {__import__('datetime').datetime.now().isoformat()}",
        "window.AGENTS_DATA = " + json.dumps(agents, ensure_ascii=False) + ";",
        "window.AGENTS = " + json.dumps([a['name'] for a in agents], ensure_ascii=False) + ";",
    ]
    js_path = os.path.join(OUT_DIR, "agents_real.js")
    with open(js_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(js_lines))
    print(f"✅ agents_real.js 已生成 ({len(agents)} 位 agent)")

    # 生成 agent_posts.json（向後相容）
    posts_path = os.path.join(OUT_DIR, "agent_posts.json")
    with open(posts_path, 'w', encoding='utf-8') as f:
        json.dump(posts, f, ensure_ascii=False, indent=2)
    print(f"✅ agent_posts.json 已生成 ({len(posts)} 位 agent)")

    # 印出摘要
    print("\n📋 Agent Icon Post 摘要:")
    for a in agents:
        icon_str = a['icon'] if a['icon'] else '(留白)'
        post_str = a['post'] if a['post'] else '(留白)'
        print(f"  {icon_str} {a['name']:6s} | {post_str}")

if __name__ == "__main__":
    main()
