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
echo -e "${YELLOW}[1/4] 安裝系統套件...${NC}"
sudo apt-get update -qq
sudo apt-get install -y xvfb x11vnc fluxbox firefox websockify

echo -e "${GREEN}✅ 系統套件安裝完成${NC}"

# 2. 下載 noVNC（從 CDN 版已在 vnc.html 中引用，此步可選）
echo -e "${YELLOW}[2/4] 檢查 noVNC 前端...${NC}"
NOVNC_DIR="$HOME/.mok/html/static/novnc"
if [ -f "$NOVNC_DIR/vnc.html" ]; then
    echo -e "${GREEN}✅ noVNC 前端已就緒${NC}"
else
    echo -e "${YELLOW}⚠️ vnc.html 不存在，將在啟動時自動建立${NC}"
fi

# 3. 建立啟動服務
echo -e "${YELLOW}[3/4] 建立 VNC 啟動腳本...${NC}"
cat > "$HOME/.mok/start_desktop.sh" << 'STARTEOF'
#!/usr/bin/env bash
# 啟動虛擬桌面環境
# Xvfb :1 → fluxbox → x11vnc :5900 → websockify :6080

stop_desktop() {
    echo "停止現有桌面服務..."
    pkill -f "x11vnc.*5900" 2>/dev/null || true
    pkill -f "websockify.*6080" 2>/dev/null || true
    pkill -f "fluxbox" 2>/dev/null || true
    pkill -f "Xvfb.*:1" 2>/dev/null || true
    sleep 1
}

start_desktop() {
    # Xvfb
    if ! pgrep -f "Xvfb.*:1" > /dev/null; then
        echo "啟動 Xvfb :1 (1280x800)..."
        Xvfb :1 -screen 0 1280x800x24 -ac &
        sleep 1
    fi
    
    # fluxbox
    if ! pgrep -f fluxbox > /dev/null; then
        echo "啟動 fluxbox..."
        DISPLAY=:1 fluxbox &
        sleep 0.5
    fi
    
    # firefox (optional)
    if ! pgrep -f "firefox" > /dev/null; then
        echo "啟動 Firefox..."
        DISPLAY=:1 firefox &
        sleep 2
    fi
    
    # x11vnc
    if ! pgrep -f "x11vnc.*5900" > /dev/null; then
        echo "啟動 x11vnc (port 5900, localhost only)..."
        x11vnc -display :1 -forever -shared -rfbport 5900 -localhost -nopw -quiet &
        sleep 1
    fi
    
    # websockify
    if ! pgrep -f "websockify.*6080" > /dev/null; then
        echo "啟動 websockify (6080 → 5900)..."
        websockify 127.0.0.1:6080 127.0.0.1:5900 &
        sleep 0.5
    fi
    
    echo "✅ 桌面服務已啟動"
    echo "   VNC: localhost:5900"
    echo "   WebSocket: localhost:6080"
}

case "${1:-start}" in
    start)
        start_desktop
        ;;
    stop)
        stop_desktop
        ;;
    restart)
        stop_desktop
        start_desktop
        ;;
    status)
        echo "Xvfb: $(pgrep -c 'Xvfb.*:1' || echo 0)"
        echo "fluxbox: $(pgrep -c fluxbox || echo 0)"
        echo "x11vnc: $(pgrep -c 'x11vnc.*5900' || echo 0)"
        echo "websockify: $(pgrep -c 'websockify.*6080' || echo 0)"
        ;;
    *)
        echo "用法: $0 {start|stop|restart|status}"
        ;;
esac
STARTEOF
chmod +x "$HOME/.mok/start_desktop.sh"

# 4. 設定開機自動啟動（可選）
echo -e "${YELLOW}[4/4] 設定開機自動啟動...${NC}"
CRON_JOB="@reboot sleep 30 && bash $HOME/.mok/start_desktop.sh start"
if crontab -l 2>/dev/null | grep -q "start_desktop.sh"; then
    echo -e "${YELLOW}⚠️ 開機啟動已存在，跳過${NC}"
else
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    echo -e "${GREEN}✅ 已加入開機自動啟動${NC}"
fi

echo -e "${GREEN}=============================================="
echo -e " ✅ 安裝完成！"
echo -e "=============================================="
echo -e " 立即啟動: bash ~/.mok/start_desktop.sh start"
echo -e " 查看狀態: bash ~/.mok/start_desktop.sh status"
echo -e " 停止服務: bash ~/.mok/start_desktop.sh stop"
echo -e ""
echo -e " 然後重啟 Web 服務: pm2 restart mok_web"
echo -e "==============================================${NC}"
