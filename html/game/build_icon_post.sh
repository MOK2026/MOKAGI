#!/bin/bash
# icon post 生成器 (shell版) - 從真實 soul.md 讀取 icon 和 post
# 主人: 莫瑞琪 | 侍女: 澄

AGENT_DIR="/home/ubuntu/.mok/agent"
OUT_DIR="/home/ubuntu/.mok/html/mokAfight_OK_0725"

echo "// ⚡ icon post - 真實取自 ~/.mok/agent/*/soul/soul.md"
echo "// 由 build_icon_post.sh 自動生成 | 主人: 莫瑞琪"
echo "// 生成時間: $(date -Iseconds)"
echo "window.AGENTS_DATA = ["

first=true
for d in "$AGENT_DIR"/*/; do
    name=$(basename "$d")
    soul="$d/soul/soul.md"
    [ -f "$soul" ] || continue
    
    # 提取 icon
    icon=$(grep -A1 '^# icon$' "$soul" 2>/dev/null | tail -1 | tr -d ' \t\r\n')
    # 過濾 "妳自己想"
    [[ "$icon" == *"妳自己想"* || "$icon" == *"自己想"* ]] && icon=""
    # 檢查是否為純 emoji（簡易判斷）
    [[ ${#icon} -gt 4 ]] && icon=""
    
    # 提取 post（從性格/身份）
    post=""
    
    echo "  TODO: $name icon=[$icon]"
done

echo "];"
echo "// 請手動維護上方資料，或使用 build_icon_post.py"
