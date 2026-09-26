#!/bin/bash
# ============================================================
# disk_guard.sh — 磁碟容量守護（每日巡檢 + 超閾值告警 + 安全清理）
# 管理者：Agent「磁碟運維侍女」(group=侍女官，歸侍女官 泠 統籌)
# 規則：刪除一律先入回收站（~/.mok/tools/trash.sh）
# 用法：disk_guard.sh [--dry-run] [--no-clean]
# ============================================================
set -uo pipefail

THRESH_WARN=${THRESH_WARN:-85}      # 警告閾值 %
THRESH_CRIT=${THRESH_CRIT:-90}      # 危急閾值 %
CACHE_KEEP_DAYS=${CACHE_KEEP_DAYS:-30}  # .cache 超過幾天未動才回收
BACKUP_KEEP=${BACKUP_KEEP:-5}       # 備份保留最新幾份
PM2_LOG_MAX_MB=${PM2_LOG_MAX_MB:-100}   # pm2 log 超過幾 MB 就截斷

DRY_RUN=0; NO_CLEAN=0
for a in "$@"; do case "$a" in --dry-run) DRY_RUN=1;; --no-clean) NO_CLEAN=1;; esac; done

H="$HOME"
AGENT_DIR="$H/.mok/agent/磁碟運維侍女"
REPORT_DIR="$AGENT_DIR/jobs/disk_guard"
LOG_DIR="$AGENT_DIR/logs"
TRASH="$H/.mok/tools/trash.sh"
STAMP=$(date +%Y%m%d_%H%M%S)
REPORT="$REPORT_DIR/$STAMP.md"
ALERTFILE="$REPORT_DIR/ALERT.md"
LOG="$LOG_DIR/disk_guard.log"
mkdir -p "$REPORT_DIR" "$LOG_DIR"

log(){ echo "$(date '+%F %T') $*" >> "$LOG"; }

notify(){
  local msg="$1" tok chat
  tok=$(grep -m1 '^MOK_TG_TOKEN=' "$H/.mok/agent/稚/.稚" 2>/dev/null | cut -d= -f2-)
  chat=$(grep -m1 '^ADMIN_CHAT_ID=' "$AGENT_DIR/.磁碟運維侍女" 2>/dev/null | cut -d= -f2-)
  [ -z "$chat" ] && chat=859730272
  if [ -z "$tok" ]; then log "無 TG token，略過推送"; return; fi
  curl -s -m 15 -X POST "https://api.telegram.org/bot${tok}/sendMessage" \
    --data-urlencode "chat_id=${chat}" --data-urlencode "text=${msg}" >/dev/null 2>&1 \
    || log "TG 推送失敗"
}

# ---------- 1) 掃描 df ----------
DF_RAW=$(df -hP -x tmpfs -x devtmpfs -x squashfs -x overlay 2>/dev/null)
ALERTS=$(echo "$DF_RAW" | awk -v t="$THRESH_WARN" -v c="$THRESH_CRIT" 'NR>1{gsub("%","",$5); if($5+0>=t){sev=($5+0>=c)?"🔴 危急":"⚠️ 警告"; printf "- %s `%s` 用量 **%s%%**（已用 %s / 共 %s）\n", sev, $6, $5, $3, $2}}')

# ---------- 2) 寫報告 ----------
{
  echo "# 💽 磁碟巡檢報告 — $STAMP"
  echo
  echo "## 檔案系統用量"
  echo '```'
  echo "$DF_RAW"
  echo '```'
  echo
  if [ -n "$ALERTS" ]; then
    echo "## ⚠️ 超閾值掛載點（≥ ${THRESH_WARN}%）"
    echo "$ALERTS"
  else
    echo "## ✅ 全部掛載點低於 ${THRESH_WARN}%"
  fi
  echo
} > "$REPORT"
ln -sf "$REPORT" "$REPORT_DIR/latest.md" 2>/dev/null

# ---------- 2b) du 熱點定位（僅在超閾值時執行，避免每日全碟掃描耗時）----------
if [ -n "$ALERTS" ]; then
  {
    echo
    echo "## 🔎 du 熱點定位（根檔案系統前 10 大目錄）"
    echo '```'
    timeout 180 du -x -h -d1 / 2>/dev/null | sort -rh | head -11
    echo '```'
  } >> "$REPORT"
fi


# ---------- 3) 超閾值 → 清理 ----------
do_clean(){
  echo "## 🧹 自動清理記錄" >> "$REPORT"
  local avail_before avail_after
  avail_before=$(df -kP / | awk 'NR==2{print $4}')

  # a) 舊備份：保留最新 $BACKUP_KEEP 份 mok_backup_*.tar.gz
  local i=0 f
  while IFS= read -r f; do
    i=$((i+1))
    [ "$i" -le "$BACKUP_KEEP" ] && continue
    if [ "$DRY_RUN" = 1 ]; then
      echo "- [dry] 回收舊備份 $(basename "$f")" >> "$REPORT"
    else
      bash "$TRASH" "$f" "${f}.meta.json" >/dev/null 2>&1
      echo "- 回收舊備份 $(basename "$f")" >> "$REPORT"
    fi
  done < <(ls -1t "$H/.mok/backups"/mok_backup_*.tar.gz 2>/dev/null)

  # b) ~/.cache 超過 N 天未動者（排除貴重模型快取）
  local PROTECT=" huggingface modelscope whisper chroma "
  while IFS= read -r p; do
    [ -z "$p" ] && continue
    local b; b=$(basename "$p")
    if [[ "$PROTECT" == *" $b "* ]]; then echo "- 略過保護項 .cache/$b" >> "$REPORT"; continue; fi
    if [ "$DRY_RUN" = 1 ]; then
      echo "- [dry] 回收 .cache/$b" >> "$REPORT"
    else
      bash "$TRASH" "$p" >/dev/null 2>&1
      echo "- 回收 .cache/$b" >> "$REPORT"
    fi
  done < <(find "$H/.cache" -mindepth 1 -maxdepth 1 -mtime +"$CACHE_KEEP_DAYS" 2>/dev/null)

  # c) pm2 巨型 log 截斷
  while IFS= read -r f; do
    [ -z "$f" ] && continue
    if [ "$DRY_RUN" = 1 ]; then
      echo "- [dry] 截斷 $(basename "$f")" >> "$REPORT"
    else
      : > "$f"; echo "- 截斷 $(basename "$f")" >> "$REPORT"
    fi
  done < <(find "$H/.pm2/logs" -type f -size +"${PM2_LOG_MAX_MB}"M 2>/dev/null)

  avail_after=$(df -kP / | awk 'NR==2{print $4}')
  echo "- 根磁碟釋放：$(( (avail_after-avail_before)/1024 )) MB" >> "$REPORT"
}

if [ -n "$ALERTS" ] && [ "$NO_CLEAN" = 0 ]; then
  do_clean
fi

# ---------- 4) 告警輸出 ----------
if [ -n "$ALERTS" ]; then
  {
    echo "# ⚠️ 磁碟容量告警 — $(date '+%F %T')"
    echo
    echo "$ALERTS"
    echo
    echo "報告：\`$REPORT\`"
  } > "$ALERTFILE"
  [ "$DRY_RUN" = 1 ] || notify "💽 磁碟運維侍女｜容量告警
${ALERTS}
已自動清理 .cache／舊備份，詳見報告。"
  log "ALERT 已觸發並推送"
else
  printf '# ✅ 目前無磁碟容量告警（%s）\n' "$(date '+%F %T')" > "$ALERTFILE"
  log "巡檢完成：無告警"
fi
echo "報告已產生：$REPORT"
