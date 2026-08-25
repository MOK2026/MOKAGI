#!/bin/bash
# 重新掃描執行器：若 scanner 已在跑則跳過，避免重複
if pgrep -f "scanner.py" > /dev/null; then
    exit 0
fi
cd /home/ubuntu/.mok/skill/賺錢王
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 重新掃描開始 =====" >> scanner.log
/usr/bin/python3 scanner.py >> scanner.log 2>&1
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 掃描結束 =====" >> scanner.log
