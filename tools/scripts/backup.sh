#!/bin/bash
# ============================================================
# 備份中心 - MOK 系統備份腳本
# 更新：2026-08-22
#   fix1: RC<=1 視為成功（tar 因 db 運行中變動返回 1，非致命）
#   fix2: exclude mpt / browser_profile / browser_profile2（可重建大目錄）
#   fix3: --warning=no-file-changed 抑制變動警告
#   fix4: nice -n 19 降低優先級
#   fix5: 排除 browser_profiles(3.8G,漏網) / live2d素材(1.1G) / mp4影片 / _tmp / 聲音工作 → 備份 2.8G→322M 解決超時
# ============================================================
MOK=/home/ubuntu/.mok
BK=$MOK/backups
LOG=$BK/backup_cron.log
# 統一使用 MOK_ADMIN_TIME_ZONE (+8) 產生時間
TZ_OFF=$(grep -E "^MOK_ADMIN_TIME_ZONE=" "$MOK/env.env" 2>/dev/null | tail -1 | cut -d= -f2 | tr -d "[:space:]")
TZ_OFF=${TZ_OFF:-8}
ADM_DATE() { date -u -d "+${TZ_OFF} hours" "$@"; }
mkdir -p "$BK"
FN="mok_backup_$(ADM_DATE +%Y%m%d_%H%M%S).tar.gz"
FP="$BK/$FN"
echo "===== $(ADM_DATE "+%F %T") start $FN =====" >> "$LOG"

cd "$MOK" && nice -n 19 tar \
  --exclude=backups \
  --exclude=__pycache__ \
  --exclude=.git \
  --exclude=node_modules \
  --exclude=playwright-browsers \
  --exclude=.chroma_data \
  --exclude=.speech2text_models \
  --exclude=whisper_models \
  --exclude=.pending_cron_confirm \
  --exclude=*.bak \
  --exclude=*.bak* \
  --exclude=CPU_上傳.bat \
  --exclude=CPU_備份.bat \
  --exclude=*/__pycache__/ \
  --exclude=trash \
  --exclude=logs \
  --exclude=mpt \
  --exclude=browser_profile \
  --exclude=browser_profile2 \
  --exclude=browser_profiles \
  --exclude=_tmp \
  --exclude=*.mp4 \
  --exclude=*/videos \
  --exclude=*/live2d \
  --exclude=*/jobs/聲音工作 \
  --warning=no-file-changed \
  -czf "$FP" . 2>>"$LOG"

RC=$?
# tar 退出碼：0=成功；1=僅檔案變動警告（對話 db 持續寫入，備份仍有效）
if [ $RC -le 1 ] && [ -s "$FP" ]; then
  echo "done: $FP ($(du -h "$FP" | cut -f1)) rc=$RC" >> "$LOG"
else
  echo "fail rc=$RC" >> "$LOG"
  rm -f "$FP"
  exit 1
fi

# 只保留最近 7 份
ls -1t "$BK"/mok_backup_*.tar.gz 2>/dev/null | tail -n +8 | xargs -r rm -f
echo "count: $(ls -1 "$BK"/mok_backup_*.tar.gz 2>/dev/null | wc -l)" >> "$LOG"
