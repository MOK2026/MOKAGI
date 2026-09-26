#!/bin/bash
# du.sh — 揪出最佔空間的目錄（Agent：磁碟運維侍女｜group=侍女官）
# 用法: du.sh [路徑] [數量]   預設 ~ 15
P=${1:-$HOME}; N=${2:-15}
echo "📂 Top $N 空間佔用（$P，同檔案系統不跨掛載）"
du -x -h --max-depth=2 "$P" 2>/dev/null | sort -rh | head -n "$N"
