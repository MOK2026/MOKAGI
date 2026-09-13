#!/usr/bin/env bash
# =============================================================
# MOKAGI 備份包 — 一鍵推送到 GitHub
#   遠端： https://github.com/MOK2026/MOKAGI
#   用法： bash PUSH.sh [branch] [--force]
#          bash PUSH.sh              # 推到 backup 分支（最安全，不覆蓋 main）
#          bash PUSH.sh main --force # 強制覆蓋 main（請自行確認）
# =============================================================
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REMOTE="https://github.com/MOK2026/MOKAGI"
BRANCH="backup"
FORCE=""
for a in "$@"; do
    case "$a" in
        --force) FORCE="--force" ;;
        *) BRANCH="$a" ;;
    esac
done

cd "$HERE"

# 1) 首次：初始化並設定遠端
if [ ! -d .git ]; then
    git init -b "$BRANCH"
    git remote add origin "$REMOTE"
fi

# 2) 提交所有內容（.gitignore 已排除密鑰/備份/日誌）
git add -A
git -c user.name="moksurky" -c user.email="mok20260316ci@gmail.com" \
    commit -m "backup: MOKAGI 備份包 $(date '+%Y-%m-%d %H:%M')" \
    || echo "（無新變動，略過 commit）"

# 3) 推送
echo "→ 推送至 $REMOTE  ($BRANCH) $FORCE"
git push $FORCE -u origin "HEAD:$BRANCH"

echo "✅ 完成"

