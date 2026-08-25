#!/bin/bash
# ============================================
# 賺錢王 - 香港商家 AI 客服機會自動掃描器
# 每日自動執行（由 cron 呼叫） / 手動執行（可自訂行業）
# ============================================
cd /home/ubuntu/.mok/skill/賺錢王
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 掃描開始 =====" >> scanner.log
if [ -t 0 ] && [ $# -eq 0 ]; then
    echo ""
    echo "🤖 賺錢王掃描器 - 手動模式（可自訂搜尋要求）"
    echo "預設行業：餐廳 美容 髮型屋 裝修工程 寵物店 補習社 健身室 牙科診所 汽車維修 洗衣店 花店 攝影"
    echo ""
    read -p "請輸入要掃描的行業/關鍵詞（Enter=用預設，多個用空格分隔）: " CATS
    if [ -n "$CATS" ]; then
        /usr/bin/python3 scanner.py $CATS >> scanner.log 2>&1
    else
        /usr/bin/python3 scanner.py >> scanner.log 2>&1
    fi
else
    /usr/bin/python3 scanner.py "$@" >> scanner.log 2>&1
fi
echo "===== $(date '+%Y-%m-%d %H:%M:%S') 掃描結束 =====" >> scanner.log
