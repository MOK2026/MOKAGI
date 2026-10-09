#!/usr/bin/env bash
# trash.sh — 把檔案/目錄安全移入 ~/.mok/trash（全系統回收站）
# 用法:
#   trash.sh <路徑1> [路徑2 ...]   移入回收站
#   trash.sh list                  列出回收筒
#   trash.sh stats                 回收筒大小
#   trash.sh restore <關鍵字>      還原（關鍵字需唯一命中）
#   trash.sh empty                 清空回收筒（永久刪除）
#
# 2026-09-28 嚴格化（衍）— 修「逐 token 誤搬」：
#   1) 兩階段：先把全部目標驗證完，任一項不合格就整批中止，不搬任何檔案。
#   2) 參數若長得像 shell 運算子（&& || ; | & > < ( ) { }）或空字串，直接拒絕。
#   3) 拒絕搬移受保護路徑：/ 、家目錄、~/.mok、~/.mok/core、~/.mok/tools。
set -u

TRASH="$HOME/.mok/trash"
ORIGIN_LOG="$TRASH/.origin.log"
STAMP=$(date +%s)

mkdir -p "$TRASH"

if [ $# -eq 0 ]; then
  echo "用法: $0 <路徑...> | list | stats | restore <關鍵字> | empty" >&2
  exit 1
fi

case "$1" in
  list)
    ls -lhA "$TRASH" 2>/dev/null | grep -v "^total"
    exit 0 ;;
  stats)
    echo "回收筒: $TRASH"
    du -sh "$TRASH" 2>/dev/null
    printf "項目數: %s\n" "$(find "$TRASH" -mindepth 1 -maxdepth 1 ! -name ".*" | wc -l)"
    exit 0 ;;
  restore)
    KW="${2:-}"
    if [ -z "$KW" ]; then echo "用法: $0 restore <名稱關鍵字>" >&2; exit 1; fi
    mapfile -t HITS < <(find "$TRASH" -mindepth 1 -maxdepth 1 ! -name ".*" -printf "%f\n" 2>/dev/null | grep -F -- "$KW")
    if [ "${#HITS[@]}" -eq 0 ]; then echo "❌ 找不到符合 $KW 的項目" >&2; exit 1; fi
    if [ "${#HITS[@]}" -gt 1 ]; then
      echo "⚠️ 有多個符合，請給更精確的關鍵字:" >&2
      printf "  %s\n" "${HITS[@]}" >&2
      exit 1
    fi
    SRC="$TRASH/${HITS[0]}"
    TARGET=$(grep -F -- "  ->  $SRC" "$ORIGIN_LOG" 2>/dev/null | tail -1 | sed "s/  ->  .*$//" | sed -E "s/^[A-Z][a-z]{2} [A-Z][a-z]{2} +[0-9]+ [0-9:]+ [A-Z]+ [0-9]+  //; s/^[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9:]{8}  //")
    if [ -z "$TARGET" ]; then echo "❌ 找不到原始路徑紀錄，無法還原（請查 $ORIGIN_LOG）" >&2; exit 1; fi
    mkdir -p -- "$(dirname -- "$TARGET")"
    if mv -- "$SRC" "$TARGET"; then
      echo "♻️ 已還原: $SRC  ->  $TARGET"
      printf "%s  RESTORE  %s  ->  %s\n" "$(date "+%Y-%m-%d %H:%M:%S")" "$SRC" "$TARGET" >> "$ORIGIN_LOG"
    else
      echo "❌ 還原失敗: $SRC" >&2; exit 1
    fi
    exit 0 ;;
  empty)
    CNT=$(find "$TRASH" -mindepth 1 -maxdepth 1 ! -name ".*" | wc -l)
    find "$TRASH" -mindepth 1 -maxdepth 1 ! -name ".*" -exec rm -rf -- {} +
    echo "🧹 已清空回收筒（$CNT 項）"
    printf "%s  EMPTY  %s 項\n" "$(date "+%Y-%m-%d %H:%M:%S")" "$CNT" >> "$ORIGIN_LOG"
    exit 0 ;;
esac

# ---- 第一階段：全部驗證，先不動任何檔案 ----
SRCS=()
for SRC in "$@"; do
  case "$SRC" in
    "") echo "❌ 拒絕: 空字串不是合法路徑（整批中止）" >&2; exit 1 ;;
    -*) echo "❌ 拒絕: $SRC 是選項不是路徑（整批中止）" >&2; exit 1 ;;
    "&&"|"||"|";"|"|"|"&"|">"|"<"|"("|")"|"{"|"}") echo "❌ 拒絕: $SRC 是 shell 運算子，疑似命令被拆成 token（整批中止）" >&2; exit 1 ;;
  esac
  if [ ! -e "$SRC" ] && [ ! -L "$SRC" ]; then
    echo "❌ 拒絕: $SRC 不存在（整批中止，未搬移任何檔案）" >&2
    exit 1
  fi
  A=$(realpath -- "$SRC")
  case "$A" in
    "/"|"$HOME"|"$HOME/.mok"|"$HOME/.mok/core"|"$HOME/.mok/core/"*|"$HOME/.mok/tools"|"$HOME/.mok/tools/"*|/etc|/etc/*|/usr|/usr/*|/bin|/bin/*|/sbin|/sbin/*|/boot|/boot/*|/var|/var/*|/dev|/dev/*|/proc|/proc/*|/sys|/sys/*|/lib|/lib/*|/lib64|/lib64/*|/opt|/opt/*|/root|/root/*|/srv|/srv/*|/run|/run/*)
      echo "❌ 拒絕: $A 是受保護路徑（整批中止）" >&2; exit 1 ;;
  esac
  SRCS+=("$A")
done

# ---- 第二階段：全部合格，才開始搬 ----
i=0
for A in "${SRCS[@]}"; do
  i=$((i+1))
  DEST="$TRASH/$(basename -- "$A")_$STAMP"
  while [ -e "$DEST" ]; do DEST="${DEST}_$i"; i=$((i+1)); done
  if mv -- "$A" "$DEST"; then
    echo "已移入回收站: $A  ->  $DEST"
    printf "%s  %s  ->  %s\n" "$(date "+%Y-%m-%d %H:%M:%S")" "$A" "$DEST" >> "$ORIGIN_LOG"
  else
    echo "❌ 搬移失敗: $A" >&2
  fi
done
exit 0
