#!/bin/bash
# ============================================================
# 系統還原腳本（System Restore Point）
# 從某個備份一鍵還原整個 MOK 系統：
#   1) 先建立「還原前安全備份」（可再回退）
#   2) 保存目前 cron / pm2 快照
#   3) 停止所有 pm2 服務
#   4) 用備份覆蓋系統資料夾
#   5) 還原 crontab，並依 dump 重啟所有 pm2 服務
# 用法: restore.sh <備份檔名|latest> [--no-safety] [--only "a b c"] [--skip "a b c"]
#   --only "a b c" : 只還原這些「頂層項目」（空白分隔），其餘保持現狀（用於：保留現有 agent 對話，只還原 core/html/frontends）
#   --skip "a b c" : 還原全部，但排除這些「頂層項目」
#   兩者同時給時以 --only 為準；都不給 = 完整還原（預設，行為與舊版相同）
# 建立：2026-09-16  更新：2026-09-17（新增選擇性還原 --only/--skip）
#                     更新：2026-09-19（自動判斷有無 ./ 前綴，兩種打包皆可還原）
# ============================================================
MOK=/home/ubuntu/.mok
BK=$MOK/backups
LOG=$BK/restore.log
PM2D=/home/ubuntu/.pm2/dump.pm2
TZ_OFF=$(grep -E "^MOK_ADMIN_TIME_ZONE=" "$MOK/env.env" 2>/dev/null | tail -1 | cut -d= -f2 | tr -d "[:space:]")
TZ_OFF=${TZ_OFF:-8}
ADM_DATE() { date -u -d "+${TZ_OFF} hours" "$@"; }
mkdir -p "$BK"
log() { echo "[$(ADM_DATE '+%F %T')] $*" >> "$LOG"; }

TARGET=""
SAFETY="yes"
ONLY=""
SKIP=""
while [ $# -gt 0 ]; do
  case "$1" in
    --no-safety) SAFETY="no"; shift ;;
    --only) ONLY="$2"; shift 2 ;;
    --only=*) ONLY="${1#--only=}"; shift ;;
    --skip) SKIP="$2"; shift 2 ;;
    --skip=*) SKIP="${1#--skip=}"; shift ;;
    -*) shift ;;
    *) [ -z "$TARGET" ] && TARGET="$1"; shift ;;
  esac
done

[ -z "$TARGET" ] && { log "❌ 未指定備份"; exit 2; }
if [ "$TARGET" = "latest" ] || [ "$TARGET" = "最新" ]; then
  TARGET=$(ls -1t "$BK"/mok_backup_*.tar.gz 2>/dev/null | head -1)
fi
[ -f "$BK/$TARGET" ] && TARGET="$BK/$TARGET"
[ -f "$TARGET" ] || { log "❌ 備份不存在: $TARGET"; exit 2; }
ARCHIVE="$TARGET"
BASE="$(basename "$ARCHIVE")"

echo "===== $(ADM_DATE '+%F %T') restore start $BASE =====" >> "$LOG"
log "🚀 開始還原 → $BASE"
# 留時間讓「已開始還原」的訊息送達使用者，之後才停 pm2
sleep 6

# 0) 驗證備份完整性
if ! tar tzf "$ARCHIVE" >/dev/null 2>&1; then
  log "❌ 備份檔損壞，中止還原"; exit 3
fi
log "✅ 備份完整性檢查通過"

# 1) 還原前安全備份（可再回退到還原前狀態）
if [ "$SAFETY" = "yes" ]; then
  SB="$BK/pre_restore_$(ADM_DATE +%Y%m%d_%H%M%S).tar.gz"
  log "🛟 建立還原前安全備份: $(basename "$SB")"
  nice -n 19 tar -C "$MOK" \
    --exclude=backups --exclude=__pycache__ --exclude=.git --exclude=node_modules \
    --exclude=playwright-browsers --exclude=.chroma_data --exclude=.speech2text_models \
    --exclude=whisper_models --exclude=trash --exclude=logs --exclude=mpt \
    --exclude=browser_profile --exclude=browser_profile2 --exclude=browser_profiles \
    --exclude=browser_profile_fb --exclude=_tmp \
    --exclude=*.mp4 --exclude=*/videos --exclude=*/live2d --exclude=*/jobs/聲音工作 \
    --warning=no-file-changed -czf "$SB" . >>"$LOG" 2>&1
  log "🛟 安全備份完成 ($(du -h "$SB" 2>/dev/null | cut -f1))"
fi

# 2) 保存目前 cron / pm2 快照（作為無還原點資料時的備援）
cp -f "$PM2D" /tmp/pm2_dump_pre_restore.pm2 2>/dev/null || true
TS=$(ADM_DATE +%Y%m%d_%H%M%S)
crontab -l > "$BK/pre_restore_crontab_$TS.txt" 2>/dev/null || true
[ -s "$PM2D" ] && cp -f "$PM2D" "$BK/pre_restore_pm2_$TS.pm2" 2>/dev/null || true
log "🛟 已保存還原前 cron / pm2 快照"

# 3) 停止所有 pm2 服務（本腳本已由 setsid 脫離，不受影響）
log "🛑 停止所有 pm2 服務…"
pm2 stop all >>"$LOG" 2>&1 || true
pm2 save >>"$LOG" 2>&1 || true

# 4) 解壓覆蓋系統資料夾
log "📦 還原檔案到 $MOK …"
EXTRACT_ARGS=()
if [ -n "$ONLY" ]; then
  # 自動偵測備份成員是否帶 ./ 前綴：
  #   新版 backup.sh 逐項列頂層項打包 → 成員形如 core/xxx（不帶 ./）
  #   舊版 backup.sh(tar ... .) 或第三方打包 → 成員形如 ./core/xxx
  # 兩種打包皆需能正確挑選成員（核心修正）
  ARCH_RAW=$(tar -tzf "$ARCHIVE" 2>/dev/null)
  PREFIX=""
  case "$(printf '%s\n' "$ARCH_RAW" | grep -m1 -E '^\./')" in
    ./*) PREFIX="./" ;;
  esac
  log "🔎 偵測成員前綴：${PREFIX:-（無 ./）}"
  # 正規化頂層清單（一律去掉可能的 ./ 前綴，兩種打包都能比對）
  ARCH_TOP=$(printf '%s\n' "$ARCH_RAW" | sed 's|^\./||' | cut -d/ -f1 | sort -u | tr "\n" " ")
  PICK=""
  for it in $ONLY; do
    case " $ARCH_TOP " in
      *" $it "*) PICK="$PICK $it" ;;
      *) log "⚠️ 備份中找不到 $it，已略過" ;;
    esac
  done
  PICK=$(echo $PICK)
  if [ -z "$PICK" ]; then log "❌ 指定還原的項目在備份中都不存在，中止還原"; exit 5; fi
  ONLY="$PICK"
  MODE_DESC="選擇性還原（只還原：$ONLY）"
  for it in $ONLY; do EXTRACT_ARGS+=("${PREFIX}$it"); done
elif [ -n "$SKIP" ]; then
  MODE_DESC="選擇性還原（排除：$SKIP）"
  # 有前綴 / 無前綴兩種寫法都排掉，兩種打包皆生效
  for it in $SKIP; do
    EXTRACT_ARGS+=(--exclude="./$it" --exclude="./$it/*" --exclude="$it" --exclude="$it/*")
  done
else
  MODE_DESC="完整還原"
fi
log "🎯 模式：$MODE_DESC"
tar -C "$MOK" -xzf "$ARCHIVE" --overwrite --no-same-owner "${EXTRACT_ARGS[@]}" >>"$LOG" 2>&1
RC=$?
if [ $RC -gt 1 ]; then log "❌ 解壓失敗 rc=$RC，中止（可至備份中心查看）"; exit 4; fi
log "📦 檔案還原完成"

# 5) 還原 crontab
if [ -s "$MOK/backup_meta/crontab.txt" ]; then
  if crontab "$MOK/backup_meta/crontab.txt" >>"$LOG" 2>&1; then
    log "🗓 crontab 已還原"
  else
    log "⚠️ crontab 還原失敗（檔案仍保留於 backup_meta/crontab.txt）"
  fi
else
  log "⚠️ 備份中無 crontab.txt，略過 cron 還原"
fi

# 6) 還原並重啟所有 pm2 服務
DUMP="$MOK/backup_meta/pm2_dump.pm2"
[ -s "$DUMP" ] || DUMP=/tmp/pm2_dump_pre_restore.pm2
if [ -s "$DUMP" ]; then
  cp -f "$DUMP" "$PM2D"
  pm2 delete all >>"$LOG" 2>&1 || true
  log "♻️ 依 dump 還原並重啟 pm2 服務…"
  pm2 resurrect >>"$LOG" 2>&1 || true
else
  log "♻️ 無 pm2 dump，改依清單重啟 .py 服務…"
  python3 - "$MOK/backup_meta/pm2_jlist.json" <<'PY' >>"$LOG" 2>&1
import json, subprocess, sys
try: d = json.load(open(sys.argv[1], encoding="utf-8"))
except Exception: d = []
for p in d:
    e = p.get("pm2_env", {})
    name, script, cwd = p.get("name"), e.get("pm_exec_path"), e.get("pm_cwd")
    if name and script and str(script).endswith(".py"):
        subprocess.run(["pm2", "start", script, "--name", name], cwd=cwd or None)
PY
fi
pm2 save >>"$LOG" 2>&1 || true

# 7) 保險：確認本體 mok_agi 有起來
if ! pm2 describe mok_agi >/dev/null 2>&1; then
  log "⚠️ mok_agi 未在 pm2 中，嘗試手動拉起…"
  pm2 start "$MOK/core/launcher.py" --name mok_agi --interpreter python3 --cwd "$MOK/core" >>"$LOG" 2>&1 || true
  pm2 save >>"$LOG" 2>&1 || true
fi

log "✅ 還原完成，所有服務已重啟"
ADM_DATE "+%F %T" > "$BK/last_restore.txt"
echo "===== $(ADM_DATE '+%F %T') restore done $BASE =====" >> "$LOG"
