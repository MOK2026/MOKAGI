#!/bin/bash
# ============================================================
# 莫氏村莊遊戲 - 資源時間戳自動版本化（cache busting）
# 用 sed 實現，僅依賴 coreutils（宿主機/容器皆可執行）。
# 當 game.js / set.js / style.css 異動，把最新 mtime 寫入
# index.html 的 ?v= 查詢參數，讓瀏覽器每次開啟都抓最新版。
# 冪等：版本沒變就不改寫。
# ============================================================
DIR="/home/ubuntu/.mok/html/game"
INDEX="$DIR/index.html"
[ -f "$INDEX" ] || exit 0
cd "$DIR" || exit 0

VER=$(stat -c '%Y' game.js set.js style.css 2>/dev/null | sort -n | tail -1)
[ -z "$VER" ] && exit 0

# 已是最新版本則直接結束
if grep -q "/game/game.js?v=${VER}" "$INDEX"; then
  exit 0
fi

sed -i -E \
  "s#(/game/style\.css)(\?v=[0-9]+)?#\1?v=${VER}#g; \
   s#(/game/set\.js)(\?v=[0-9]+)?#\1?v=${VER}#g; \
   s#(/game/game\.js)(\?v=[0-9]+)?#\1?v=${VER}#g" "$INDEX"

echo "game assets versioned: v=$VER"
