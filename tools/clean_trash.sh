#!/bin/bash
# 全系統定時清理：回收站(trash) + 臨時資料夾(/tmp, ~/.mok/_tmp)
LOG="$HOME/.mok/trash/.clean.log"
echo "===== clean 開始 $(date "+%F %T") =====" >> "$LOG"

# 1) 回收站 trash/ 中超過 30 天未動的項目 → 物理清除（保留規則說明與來源紀錄）
find "$HOME/.mok/trash" -mindepth 1 -mtime +30 ! -name ".origin.log" ! -name "README.txt" ! -name ".clean.log" -exec rm -rf {} + 2>>"$LOG"

# 2) /tmp 中超過 7 天未動的一般檔案 → 清除（不動目錄結構與 socket）
find /tmp -type f -mtime +7 -delete 2>>"$LOG"

# 3) ~/.mok/_tmp 中超過 7 天未動的一般檔案 → 清除（臨時文件夾，保留目錄結構）
find "$HOME/.mok/_tmp" -type f -mtime +7 -delete 2>>"$LOG"

# 3b) ~/.mok/_tmp 內已空的子目錄 → 清除（保留 _tmp 本身；避免 gui_agent/shots 等空殼堆積）
find "$HOME/.mok/_tmp" -mindepth 1 -type d -empty -delete 2>>"$LOG"

# 4) 清掉 trash 內已空的子目錄
find "$HOME/.mok/trash" -mindepth 1 -type d -empty -delete 2>>"$LOG"

echo "===== clean 完成 $(date "+%F %T") =====" >> "$LOG"
