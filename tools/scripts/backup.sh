#!/bin/bash
# ============================================================
# 備份中心 - MOK 系統備份腳本
# 更新：2026-09-19（打包格式統一：逐項列頂層項，成員不帶 ./ 前綴，與 mok_web.py 一致）
# 更新：2026-09-20（補排除：browser_profile* / live2d* / .trash / _restore_bak_* / _fix_bak_* / desktop_setup.log）
# 更新：2026-09-17（加入系統還原點）
#   每次備份都會匯出「所有 cron」與「執行中的 pm2 .py」到 backup_meta/，
#   隨備份一起打包；還原時即可回到該時點，並自動重啟所有服務。
#   fix1: RC<=1 視為成功（tar 因 db 運行中變動返回 1，非致命）
#   fix2: exclude 可重建大目錄（mpt / browser_profile / browser_profiles ...）
#   fix3: --warning=no-file-changed 抑制警告；nice -n 19 降優先級
#   fix6: 改用 tar -C 取代 cd，相容編輯登記鎖
#   fix7: 打包改為「逐項列出頂層項目」→ 成員不帶 ./ 前綴，與 mok_web.py（arcname 統一）一致
# ============================================================
MOK=/home/ubuntu/.mok
BK=$MOK/backups
META=$MOK/backup_meta
LOG=$BK/backup_cron.log
PM2D=/home/ubuntu/.pm2/dump.pm2
TZ_OFF=$(grep -E "^MOK_ADMIN_TIME_ZONE=" "$MOK/env.env" 2>/dev/null | tail -1 | cut -d= -f2 | tr -d "[:space:]")
TZ_OFF=${TZ_OFF:-8}
ADM_DATE() { date -u -d "+${TZ_OFF} hours" "$@"; }
mkdir -p "$BK" "$META"
FN="mok_backup_$(ADM_DATE +%Y%m%d_%H%M%S).tar.gz"
FP="$BK/$FN"
echo "===== $(ADM_DATE "+%F %T") start $FN =====" >> "$LOG"

# ---- 0) 匯出還原點資訊：所有 cron + 執行中的 pm2 ----
crontab -l > "$META/crontab.txt" 2>/dev/null || true
pm2 save >/dev/null 2>&1 || true
[ -s "$PM2D" ] && cp -f "$PM2D" "$META/pm2_dump.pm2" 2>/dev/null || true
pm2 jlist > "$META/pm2_jlist.json" 2>/dev/null || echo '[]' > "$META/pm2_jlist.json"
pm2 list > "$META/pm2_list.txt" 2>/dev/null || true
ADM_DATE "+%F %T" > "$META/backup_time.txt"

python3 - "$META" "$FN" <<'PY' >> "$LOG" 2>&1
import json, os, sys, socket
meta, fn = sys.argv[1], sys.argv[2]
try:
    cron_txt = open(os.path.join(meta, "crontab.txt"), encoding="utf-8", errors="replace").read()
except Exception:
    cron_txt = ""
cron_count = sum(1 for l in cron_txt.splitlines() if l.strip() and not l.strip().startswith("#"))
pm2_py = []
try:
    d = json.load(open(os.path.join(meta, "pm2_jlist.json"), encoding="utf-8"))
    for p in d:
        e = p.get("pm2_env", {}) or {}
        sp = str(e.get("pm_exec_path", ""))
        if sp.endswith(".py"):
            pm2_py.append(p.get("name"))
except Exception:
    pass
try:
    created = open(os.path.join(meta, "backup_time.txt"), encoding="utf-8").read().strip()
except Exception:
    created = ""
man = {"file": fn, "created": created, "host": socket.gethostname(),
       "cron_count": cron_count, "pm2_count": len(pm2_py), "pm2_py": pm2_py}
json.dump(man, open(os.path.join(meta, "manifest.json"), "w", encoding="utf-8"),
          ensure_ascii=False, indent=2)
print("meta-exported cron=%d pm2_py=%d" % (cron_count, len(pm2_py)))
PY

# ---- 1) 打包整個系統資料夾（含 backup_meta，還原點資訊隨備份保存）----
# 統一打包格式：與 mok_web.py（tarfile arcname=頂層項名）一致，
#   逐項列出頂層項目打包 → 成員一律不帶 ./ 前綴（舊版 tar ... . 會產生 ./core）。
#   restore.sh 已能自動偵測前綴，兩種格式皆可還原。
shopt -s dotglob nullglob
TOP=()
for p in "$MOK"/*; do
  b=${p##*/}
  case "$b" in chat_history.db|conversation_history.db) continue ;; esac
  TOP+=("$b")
done
shopt -u dotglob nullglob
nice -n 19 tar -C "$MOK" \
  --exclude="backups" \
  --exclude="__pycache__" \
  --exclude=".git" \
  --exclude="node_modules" \
  --exclude="playwright-browsers" \
  --exclude=".chroma_data" \
  --exclude=".speech2text_models" \
  --exclude="whisper_models" \
  --exclude=".pending_cron_confirm" \
  --exclude="*.bak" \
  --exclude="*.bak*" \
  --exclude="_restore_bak_*" \
  --exclude="_fix_bak_*" \
  --exclude="CPU_上傳.bat" \
  --exclude="CPU_備份.bat" \
  --exclude="trash" \
  --exclude=".trash" \
  --exclude="logs" \
  --exclude="desktop_setup.log" \
  --exclude="mpt" \
  --exclude="browser_profile*" \
  --exclude="_tmp" \
  --exclude="*.mp4" \
  --exclude="*/videos" \
  --exclude="*/live2d*" \
  --exclude="*/jobs/聲音工作" \
  --warning=no-file-changed \
  -czf "$FP" "${TOP[@]}" 2>>"$LOG"

RC=$?
# tar 退出碼：0=成功；1=僅檔案變動警告（對話 db 持續寫入，備份仍有效）
if [ $RC -le 1 ] && [ -s "$FP" ]; then
  echo "done: $FP ($(du -h "$FP" | cut -f1)) rc=$RC" >> "$LOG"
  cp -f "$META/manifest.json" "$FP.meta.json" 2>/dev/null || true
else
  echo "fail rc=$RC" >> "$LOG"
  rm -f "$FP"
  exit 1
fi

# 只保留最近 7 份（連同 .meta.json 一起清理）
ls -1t "$BK"/mok_backup_*.tar.gz 2>/dev/null | tail -n +8 | while read -r old; do
  rm -f "$old" "$old.meta.json"
done
echo "count: $(ls -1 "$BK"/mok_backup_*.tar.gz 2>/dev/null | wc -l)" >> "$LOG"
