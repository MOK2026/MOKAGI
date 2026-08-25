#!/bin/bash
# 賺錢王 - 自動執行：saferun.sh(掃描) → build_mokcs.py(建 MokCs 克隆頁)
# 僅在 香港時間 08:00-22:00 執行（cron 每30分鐘喚醒一次）
cd /home/ubuntu/.mok/skill/賺錢王 || exit 1

HK_HOUR=$(TZ=Asia/Hong_Kong date +%H)
if [ "$HK_HOUR" -lt 8 ] || [ "$HK_HOUR" -ge 22 ]; then
    exit 0
fi

# 若掃描已在進行，跳過本次（避免重複）
if pgrep -f "scanner.py" > /dev/null; then
    exit 0
fi

echo "===== $(date '+%Y-%m-%d %H:%M:%S') auto_run 開始 (香港時 $HK_HOUR) =====" >> auto_run.log
bash saferun.sh
/usr/bin/python3 build_mokcs.py >> mokcs_build.log 2>&1
echo "===== $(date '+%Y-%m-%d %H:%M:%S') auto_run 結束 =====" >> auto_run.log
