#!/bin/bash
# ============================================================
# room_tidy.sh — 侍女房間每日整理
#   1) 暫存檔/備份檔/快取  → 安全移入回收站 (~/.mok/trash)
#   2) 散落工作檔 (--archive) → 歸檔回該房間 jobs/_inbox_<YYYYMM>/
# 管理者：磁碟運維侍女   |   用法：room_tidy.sh [--apply] [--archive] [--room=<名>]
#   預設 DRY-RUN 只報告不動檔；--apply 才實際執行。
#   刪除一律經 trash.sh 入回收站，可還原（見 ~/.mok/trash/.origin.log）
# 安全設計：只動房間根目錄第一層 + 白名單 + 年齡門檻 + 被引用者略過 + 觀察名單
# 環境變數：MIN_AGE_MIN 最小年齡分鐘（預設 1440=24h）
# ============================================================
set -uo pipefail

ROOMDIR="$HOME/.mok/agent"
TRASH_SH="$HOME/.mok/tools/trash.sh"
MYDIR="$HOME/.mok/agent/磁碟運維侍女"
REPORT_DIR="$MYDIR/jobs/room_tidy"
LOG="$MYDIR/logs/room_tidy.log"
AGE=${MIN_AGE_MIN:-1440}

APPLY=0; ARCHIVE=0; ONLY=""
for a in "$@"; do
  case "$a" in
    --apply) APPLY=1 ;;
    --archive) ARCHIVE=1 ;;
    --room=*) ONLY="${a#--room=}" ;;
  esac
done

mkdir -p "$REPORT_DIR" "$(dirname "$LOG")"
STAMP=$(date +%Y%m%d_%H%M%S); NOW=$(date '+%F %T')
REPORT="$REPORT_DIR/room_tidy_$STAMP.md"
if [ "$APPLY" -eq 1 ]; then MODE="APPLY 實際執行"; else MODE="DRY-RUN 僅報告"; fi
TOTAL_TRASH=0; TOTAL_ARCH=0; TOTAL_WATCH=0

is_protected() {
  case "$1" in
    .*) return 0 ;;
    _job.json|README*|*.py|*.sh|*.json|index.html|girl_*|*說明*|*索引*|*狀態*|*進度*|*.md) return 0 ;;
    *archive*|*backup*|*_old*) return 0 ;;
    soul|jobs|logs|外觀|avatars) return 0 ;;
  esac
  return 1
}
is_temp() {
  case "$1" in
    _*|*.tmp|*.tmp.*|*.bak|*.bak.*|*.bak_*|*.bak-*|*~|*.old|*.orig|*.rej|*.swp|*.swo|*.pyc|*.pyo|.DS_Store) return 0 ;;
    *_test*.png|*_test*.jpg|*_test*.jpeg|_admin_test*) return 0 ;;
  esac
  return 1
}
is_referenced() {
  local r="$1" base="$2"
  grep -rlsF -- "$base" "$ROOMDIR/$r" \
    --include='*.py' --include='*.sh' --include='*.json' \
    --include='*.js' --include='*.md' --include='*.html' \
    --exclude-dir=logs --exclude-dir=__pycache__ \
    --exclude-dir=node_modules --exclude-dir=.git 2>/dev/null | grep -qv '/logs/' && return 0
  return 1
}

{
  echo "# 侍女房間整理報告 — $NOW"
  echo
  echo "- 模式：**$MODE** ｜ 年齡門檻：$((AGE/60)) 小時"
  if [ -n "$ONLY" ]; then echo "- 範圍：僅 $ONLY"; else echo "- 範圍：全部房間"; fi
  echo
  echo "| 房間 | 入回收站 | 歸檔 | 觀察 |"
  echo "|---|---|---|---|"
} > "$REPORT"

for R in "$ROOMDIR"/*/; do
  room=$(basename "$R"); case "$room" in logs|__zztest) continue;; esac
  [ -n "$ONLY" ] && [ "$room" != "$ONLY" ] && continue
  t_n=0; a_n=0; w_n=0; det=""

  while IFS= read -r -d '' f; do
    n=$(basename "$f")
    is_protected "$n" && continue
    is_temp "$n" || continue
    if is_referenced "$room" "$n"; then det="$det\\n- ⚠️ 略過(程式引用中): $n"; continue; fi
    t_n=$((t_n+1)); det="$det\\n- 🗑️ $n"
    [ "$APPLY" -eq 1 ] && bash "$TRASH_SH" "$f" >/dev/null 2>&1
  done < <(find "$R" -maxdepth 1 -mindepth 1 -type f \
      \( -name '_*' -o -name '*.tmp' -o -name '*.tmp.*' -o -name '*.bak' \
         -o -name '*.bak.*' -o -name '*.bak_*' -o -name '*.bak-*' -o -name '*~' \
         -o -name '*.old' -o -name '*.orig' -o -name '*.rej' -o -name '*.swp' \
         -o -name '*.swo' -o -name '*.pyc' -o -name '*.pyo' -o -name '.DS_Store' \
         -o -name '*_test*.png' -o -name '*_test*.jpg' \) \
      -mmin +$AGE -print0 2>/dev/null)

  while IFS= read -r -d '' d; do
    t_n=$((t_n+1)); det="$det\\n- 🗑️ $(basename "$d")/ (快取目錄)"
    [ "$APPLY" -eq 1 ] && bash "$TRASH_SH" "$d" >/dev/null 2>&1
  done < <(find "$R" -maxdepth 1 -mindepth 1 -type d -name '__pycache__' -print0 2>/dev/null)

  # 觀察名單：根目錄 .log 與隱藏的生命檔備份 → 只報告，不動
  while IFS= read -r -d '' f; do
    w_n=$((w_n+1)); det="$det\\n- 👁️ 觀察(不動): $(basename "$f")"
  done < <(find "$R" -maxdepth 1 -mindepth 1 -type f \
      \( -name '*.log' -o -name '.*.bak*' \) -mmin +$AGE -print0 2>/dev/null)

  if [ "$ARCHIVE" -eq 1 ]; then
    INBOX="$R/jobs/_inbox_$(date +%Y%m)"
    while IFS= read -r -d '' f; do
      n=$(basename "$f")
      is_protected "$n" && continue
      is_referenced "$room" "$n" && { det="$det\\n- ⚠️ 略過(引用中): $n"; continue; }
      a_n=$((a_n+1)); det="$det\\n- 📦 歸檔: $n"
      if [ "$APPLY" -eq 1 ]; then mkdir -p "$INBOX"; mv "$f" "$INBOX/$n"; fi
    done < <(find "$R" -maxdepth 1 -mindepth 1 -type f \
        \( -name '*.html' -o -name '*.js' -o -name '*.css' -o -name '*.csv' \
           -o -name '*.xlsx' -o -name '*.pdf' -o -name '*.docx' -o -name '*.zip' \
           -o -name '*.png' -o -name '*.jpg' -o -name '*.jpeg' -o -name '*.mp4' \
           -o -name '*.json' -o -name '*.md' -o -name '*.txt' \) \
        -mtime +7 -print0 2>/dev/null)
  fi

  TOTAL_TRASH=$((TOTAL_TRASH+t_n)); TOTAL_ARCH=$((TOTAL_ARCH+a_n)); TOTAL_WATCH=$((TOTAL_WATCH+w_n))
  [ "$t_n" -eq 0 ] && [ "$a_n" -eq 0 ] && [ "$w_n" -eq 0 ] && continue
  echo "| $room | $t_n | $a_n | $w_n |" >> "$REPORT"
  { echo; echo "### $room"; echo -e "$det"; } >> "$REPORT"
done

{
  echo
  echo "## 合計"
  echo "- 🗑️ 暫存/備份 → 回收站：**$TOTAL_TRASH** 項"
  echo "- 📦 散落工作 → jobs/_inbox：**$TOTAL_ARCH** 項"
  echo "- 👁️ 觀察名單（未動）：**$TOTAL_WATCH** 項"
  echo
  echo "還原：\`~/.mok/trash/\`（同名+時間戳），來源見 \`~/.mok/trash/.origin.log\`"
} >> "$REPORT"

echo "[$NOW] room_tidy $MODE：trash=$TOTAL_TRASH archive=$TOTAL_ARCH watch=$TOTAL_WATCH 報告=$REPORT" >> "$LOG"
echo "報告：$REPORT"; echo "trash=$TOTAL_TRASH archive=$TOTAL_ARCH watch=$TOTAL_WATCH"
