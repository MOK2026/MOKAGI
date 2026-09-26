#!/bin/bash
# df.sh — 磁碟用量速查（Agent：磁碟運維侍女｜group=侍女官）
# 用法: df.sh [警告閾值%]   預設 85
T=${1:-85}
df -hP -x tmpfs -x devtmpfs -x squashfs -x overlay 2>/dev/null | awk -v t="$T" '
NR==1{print; next}
{u=$5; gsub("%","",u); m=(u+0>=t)?sprintf("   ⚠️ ≥%d%%",t):""; printf "%s%s\n", $0, m}'
