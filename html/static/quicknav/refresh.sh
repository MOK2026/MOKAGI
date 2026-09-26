#!/usr/bin/env bash
# ============================================================
# QuickNav - 重新生成 .mok 目錄列表 JSON   by indexPage
# 用途：當 ~/.mok 下新增/刪除第一層資料夾後，執行此腳本即可
#       更新 mok_dirs.json（前端提示的資料來源）。
# 用法：bash ~/.mok/html/static/quicknav/refresh.sh
# ============================================================
set -e
# 由腳本所在位置推導 .mok 根目錄（避免 HOME 環境差異）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
MOK_HOME="$(cd "$SCRIPT_DIR/../../.." && pwd)"
OUT="$SCRIPT_DIR/mok_dirs.json"
UPDATED=$(date '+%Y-%m-%dT%H:%M:%S')

ALL=$(find "$MOK_HOME" -maxdepth 1 -mindepth 1 -type d 2>/dev/null \
      | sed "s|^$MOK_HOME/||" | sort)

NORMAL=$(printf '%s\n' "$ALL" | grep -v '^\.' || true)
HIDDEN=$(printf '%s\n' "$ALL" | grep '^\.' || true)

{
  echo '{'
  echo '  "generator": "indexPage",'
  echo '  "root": ".mok",'
  echo "  \"updated_at\": \"$UPDATED\","
  echo '  "dirs": ['
  first=1
  emit() {
    local name="$1"
    if [ "$first" -eq 0 ]; then printf ',\n'; fi
    first=0
    printf '    { "name": "%s", "path": ".mok/%s", "display": ".mok/%s/" }' "$name" "$name" "$name"
  }
  while IFS= read -r d; do [ -n "$d" ] && emit "$d"; done <<< "$NORMAL"
  while IFS= read -r d; do [ -n "$d" ] && emit "$d"; done <<< "$HIDDEN"
  echo ''
  echo '  ]'
  echo '}'
} > "$OUT"

echo "✅ 已生成 $OUT"
