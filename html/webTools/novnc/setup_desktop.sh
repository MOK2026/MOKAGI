#!/usr/bin/env bash
# ==============================================
# MOKAGI 主機桌面安裝腳本
# 安裝 Xvfb + fluxbox + x11vnc + websockify + noVNC
# 在 Ubuntu 主機上執行: bash setup_desktop.sh
# ==============================================
set -e

GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m'

echo -e "${GREEN}=============================================="
echo -e " 🖥️  MOKAGI 主機桌面安裝"
echo -e "==============================================${NC}"

# 1. 安裝系統套件
echo -e "${YELLOW}[1/3] 安裝系統套件...${NC}"
sudo apt-get update -qq
sudo apt-get install -y xvfb x11vnc fluxbox firefox websockify

echo -e "${GREEN}✅ 系統套件安裝完成${NC}"

# 2. 下載 noVNC（從 CDN 版已在 vnc.html 中引用，此步可選）
echo -e "${YELLOW}[2/3] 檢查 noVNC 前端...${NC}"
NOVNC_DIR="$HOME/.mok/html/static/novnc"
if [ -f "$NOVNC_DIR/vnc.html" ]; then
    echo -e "${GREEN}✅ noVNC 前端已就緒${NC}"
else
    echo -e "${YELLOW}⚠️ vnc.html 不存在，將在啟動時自動建立${NC}"
fi

# 啟動腳本檢查（不再重建）
#   2026-09-20：本安裝腳本不再以 heredoc 重建 start_desktop.sh，
#   也不再指向舊路徑 ~/.mok/start_desktop.sh。
#   novnc/start_desktop.sh 為獨立維護檔（由 cron 驅動），請直接編輯它。
if [ -x "$HOME/.mok/html/webTools/novnc/start_desktop.sh" ]; then
    echo -e "${GREEN}✅ 啟動腳本已就緒（不重建）${NC}"
else
    echo -e "${RED}⚠️ 找不到 novnc/start_desktop.sh，請先還原後再執行安裝${NC}"
fi

# 3. 設定開機自動啟動（可選）
echo -e "${YELLOW}[3/3] 設定開機自動啟動...${NC}"
CRON_JOB="@reboot sleep 30 && bash $HOME/.mok/html/webTools/novnc/start_desktop.sh start"
if crontab -l 2>/dev/null | grep -q "start_desktop.sh"; then
    echo -e "${YELLOW}⚠️ 開機啟動已存在，跳過${NC}"
else
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    echo -e "${GREEN}✅ 已加入開機自動啟動${NC}"
fi

echo -e "${GREEN}=============================================="
echo -e " ✅ 安裝完成！"
echo -e "=============================================="
echo -e " 立即啟動: bash ~/.mok/html/webTools/novnc/start_desktop.sh start"
echo -e " 查看狀態: bash ~/.mok/html/webTools/novnc/start_desktop.sh status"
echo -e " 停止服務: bash ~/.mok/html/webTools/novnc/start_desktop.sh stop"
echo -e ""
echo -e " 然後重啟 Web 服務: pm2 restart mok_web"
echo -e "==============================================${NC}"
