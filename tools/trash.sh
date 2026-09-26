#!/bin/bash
# trash.sh — 把檔案/目錄安全移入 ~/.mok/trash（全系統回收站）
# 用法: trash.sh <路徑1> [路徑2 ...]
TRASH="$HOME/.mok/trash"
STAMP=$(date +%s)
mkdir -p "$TRASH"
[ $# -eq 0 ] && echo "用法: $0 路徑..." && exit 1
for SRC in "$@"; do
  [ -e "$SRC" ] || { echo "不存在: $SRC"; continue; }
  A=$(realpath "$SRC")
  DEST="$TRASH/$(basename "$A")_$STAMP"
  mv "$A" "$DEST" && echo "已移入回收站: $A  ->  $DEST"
  echo "$(date)  $A  ->  $DEST" >> "$TRASH/.origin.log"
done
