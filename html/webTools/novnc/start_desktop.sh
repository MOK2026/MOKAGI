#!/usr/bin/env bash
# ==============================================
# MOKAGI 虛擬桌面啟動腳本
# Xvfb :1 → fluxbox → x11vnc :5900 → websockify :6080
# ==============================================

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
        echo "啟動 Xvfb :1 (1280x1280, 預設橫式 1280x800)..."
        Xvfb :1 -screen 0 1280x1280x24 -ac &
        sleep 1
    fi
        DISPLAY=:1 xrandr --fb 1280x800 2>/dev/null || true

    # fluxbox
    if ! pgrep -f fluxbox > /dev/null; then
        echo "啟動 fluxbox..."
        DISPLAY=:1 fluxbox &
        sleep 0.5
    fi

    # x11vnc
    if ! pgrep -f "x11vnc.*5900" > /dev/null; then
        echo "啟動 x11vnc (port 5900, localhost only)..."
        x11vnc -display :1 -forever -shared -rfbport 5900 -localhost -nopw -quiet -xrandr &
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
        echo "Xvfb: $(pgrep -c 'Xvfb.*:1' 2>/dev/null || echo 0)"
        echo "fluxbox: $(pgrep -c fluxbox 2>/dev/null || echo 0)"
        echo "x11vnc: $(pgrep -c 'x11vnc.*5900' 2>/dev/null || echo 0)"
        echo "websockify: $(pgrep -c 'websockify.*6080' 2>/dev/null || echo 0)"
        ;;
    *)
        echo "用法: $0 {start|stop|restart|status}"
        ;;
esac
