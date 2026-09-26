#!/usr/bin/env bash
# MOKAGI 主機桌面 安裝+啟動 (idempotent, 可由 cron 每分鐘觸發)
export PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
LOG=/home/ubuntu/.mok/desktop_setup.log
LOCK=/home/ubuntu/.mok/.desktop_setup.lock

# 日誌輪替：僅保留最近 1000 行，避免 desktop_setup.log 無限長大（20260913）
LOG_KEEP=1000
if [ -f "$LOG" ]; then
    _n=$(wc -l < "$LOG" 2>/dev/null || echo 0)
    if [ "$_n" -gt "$LOG_KEEP" ]; then
        sed -i "1,$((_n - LOG_KEEP))d" "$LOG" 2>/dev/null || true
    fi
fi

exec >>"$LOG" 2>&1
echo "===== $(date '+%F %T') 桌面 setup 觸發 ====="

if [ -d "$LOCK" ]; then
    if find "$LOCK" -maxdepth 0 -mmin +10 2>/dev/null | grep -q .; then
        rm -rf "$LOCK"
    else
        echo "已有 setup 在執行中，跳過"
        exit 0
    fi
fi
mkdir -p "$LOCK"

SUDO=""
if [ "$(id -u)" != "0" ]; then
    SUDO="sudo -n"
fi

# 必需套件（xterm 為可選，不在此列）
missing=""
for c in Xvfb x11vnc websockify fluxbox; do
    command -v "$c" >/dev/null 2>&1 || missing="$missing $c"
done

if [ -n "$missing" ]; then
    echo "缺少必需套件:$missing 開始安裝..."
    $SUDO apt-get update -y >/dev/null 2>&1
    if $SUDO apt-get install -y xvfb x11vnc websockify fluxbox >/dev/null 2>&1; then
        echo "套件安裝完成"
    else
        echo "⚠️ APT 安裝失敗（繼續嘗試啟動已存在的部分）"
    fi
fi

started=""
# 解析度自愈：若 Xvfb 已存在但非雙向解析度 1280x1280，重啟整個桌面服務（1280x1280 才能同時支援橫版 1280x800 與直版 800x1280）
if pgrep -f "Xvfb :1" >/dev/null 2>&1 && ! pgrep -af "Xvfb :1" | grep -q "1280x1280"; then
    echo "⚠️ 偵測到 Xvfb 非雙向(1280x1280)，重啟桌面服務..."
    pkill -f "websockify.*6080" 2>/dev/null
    pkill -f "x11vnc.*5900" 2>/dev/null
    pkill -f fluxbox 2>/dev/null
    pkill -f "Xvfb :1" 2>/dev/null
    sleep 1
    perl -e 'unlink qw(/tmp/novnc_rotate_state)' 2>/dev/null
    perl -e 'unlink qw(/tmp/.X1-lock /tmp/.X11-unix/X1)' 2>/dev/null
fi
if ! pgrep -f "Xvfb :1" >/dev/null 2>&1; then
    if command -v Xvfb >/dev/null 2>&1; then
        echo "啟動 Xvfb :1"
        # 清除可能殘留的鎖檔，避免 Xvfb 無法重啟
        perl -e 'unlink qw(/tmp/.X1-lock /tmp/.X11-unix/X1)' 2>/dev/null
        nohup Xvfb :1 -screen 0 1280x1280x24 -ac +extension RANDR >/tmp/xvfb_1.log 2>&1 &
        started="$started Xvfb"
        sleep 1
        # 預設橫式 1280x800（1280x1280 螢幕內可自由切換橫/直版）
        DISPLAY=:1 xrandr --fb 1280x800 2>/dev/null || true
    fi
fi
if ! pgrep -f "fluxbox" >/dev/null 2>&1; then
    if command -v fluxbox >/dev/null 2>&1; then
        echo "啟動 fluxbox"
        DISPLAY=:1 nohup fluxbox >/dev/null 2>&1 &
        started="$started fluxbox"
        sleep 1
    fi
fi
# 設定桌面背景色（避免全黑誤以為沒畫面）
        if command -v xsetroot >/dev/null 2>&1; then
            DISPLAY=:1 xsetroot -solid "#2d4a7a" 2>/dev/null
        fi
        if ! pgrep -f "x11vnc.*5900" >/dev/null 2>&1; then
    if command -v x11vnc >/dev/null 2>&1; then
        echo "啟動 x11vnc (5900)"
        nohup x11vnc -display :1 -forever -shared -rfbport 5900 -localhost -nopw -xrandr -o /home/ubuntu/.mok/x11vnc_1.log >/home/ubuntu/.mok/x11vnc_1.log 2>&1 &
        started="$started x11vnc"
        sleep 1
    fi
fi
if ! pgrep -f "websockify.*6080" >/dev/null 2>&1; then
    if command -v websockify >/dev/null 2>&1; then
        echo "啟動 websockify (6080->5900)"
        nohup websockify 127.0.0.1:6080 127.0.0.1:5900 >/dev/null 2>&1 &
        started="$started websockify"
        sleep 1
    fi
fi

# 可選 xterm 監控視窗
if command -v xterm >/dev/null 2>&1; then
    if ! pgrep -f "xterm.*MOK" >/dev/null 2>&1; then
        echo "開啟 xterm 監控視窗"
        DISPLAY=:1 nohup xterm -T "MOK 主機監控" -geometry 120x40 -e "top -d 2" >/dev/null 2>&1 &
    fi
fi

echo "本次啟動:$started"
_cnt() { pgrep -f "$1" 2>/dev/null | wc -l; }
    echo "狀態 Xvfb:$(_cnt 'Xvfb :1') fluxbox:$(_cnt fluxbox) x11vnc:$(_cnt 'x11vnc.*5900') websockify:$(_cnt 'websockify.*6080')"
rm -rf "$LOCK"
echo "===== $(date '+%F %T') 桌面 setup 完成 ====="
