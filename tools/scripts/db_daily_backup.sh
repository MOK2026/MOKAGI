#!/bin/bash
# ============================================================
# 每日 DB 自動備份：chat_history.db + conversation_history.db
# 用 sqlite3 .backup 產生一致性快照（避免 WAL/鎖問題），保留最近 7 天
# 排程建議：每天 04:10（低峰） / cron: 10 4 * * *
# ============================================================
MOK=/home/ubuntu/.mok
MEM=$MOK/.memory
BK=$MOK/backups/db
LOG=$MOK/backups/db_backup.log
KEEP=7

TZ_OFF=$(grep -E "^MOK_ADMIN_TIME_ZONE=" "$MOK/env.env" 2>/dev/null | tail -1 | cut -d= -f2 | tr -d "[:space:]")
TZ_OFF=${TZ_OFF:-8}
ADM_DATE() { date -u -d "+${TZ_OFF} hours" "$@"; }

STAMP=$(ADM_DATE +%Y%m%d_%H%M%S)
DEST="$BK/$STAMP"
mkdir -p "$DEST"

echo "===== $(ADM_DATE '+%F %T') start $STAMP =====" >> "$LOG"

for db in chat_history conversation_history; do
  SRC="$MEM/$db.db"
  if [ -f "$SRC" ]; then
    # 用 sqlite backup API 產一致性快照，timeout 給足（60s）避免 database is locked
    sqlite3 -cmd ".timeout 60000" "$SRC" ".backup '$DEST/$db.db'" 2>>"$LOG"
    RC=$?
    if [ $RC -eq 0 ]; then
      SZ=$(du -h "$DEST/$db.db" | cut -f1)
      echo "OK   $db.db -> $DEST ($SZ)" >> "$LOG"
    else
      echo "FAIL $db.db rc=$RC" >> "$LOG"
    fi
  else
    echo "SKIP $db.db (not found)" >> "$LOG"
  fi
done

# 只留最近 KEEP 天（依資料夾名稱排序刪除最舊）
cd "$BK" 2>/dev/null || exit 0
ls -1d [0-9]* 2>/dev/null | sort | head -n -"$KEEP" | while read d; do
  rm -rf "$BK/$d"
  echo "CLEAN removed $d" >> "$LOG"
done

echo "===== $(ADM_DATE '+%F %T') done =====" >> "$LOG"
