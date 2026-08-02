#!/usr/bin/env bash
# ============================================
# noVNC 斷線診斷腳本（在宿主機執行）
# ============================================
echo "===== 1. 桌面服務進程 ====="
ps aux | grep -E "Xvfb|fluxbox|x11vnc|websockify" | grep -v grep || echo "(無桌面進程！)"
echo ""
echo "===== 2. 端口監聽 (5000/5900/6080) ====="
(sudo netstat -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null || ss -tlnp 2>/dev/null) | grep -E ':(5000|5900|6080)\s' || echo "(端口未監聽！)"
echo ""
echo "===== 3. mok_web 進程 ====="
ps aux | grep mok_web | grep -v grep || echo "(mok_web 未運行！)"
echo ""
echo "===== 4. websockify 本地測試 (期望 400/405 而非連線失敗) ====="
curl -s -o /dev/null -w "HTTP code: %{http_code}\n" --max-time 3 http://127.0.0.1:6080/ || echo "❌ 6080 連線失敗 → websockify 沒跑"
echo ""
echo "===== 5. Flask /novnc-ws 握手測試 (期望 HTTP/1.1 101) ====="
curl -s -i -N --max-time 4 \
  -H "Connection: Upgrade" \
  -H "Upgrade: websocket" \
  -H "Sec-WebSocket-Key: SGVsbG8sIHdvcmxkIQ==" \
  -H "Sec-WebSocket-Version: 13" \
  http://127.0.0.1:5000/novnc-ws 2>&1 | head -12
echo ""
echo "===== 6. nginx 反代配置 (查 /novnc-ws 轉發) ====="
grep -rn "novnc" /etc/nginx/ 2>/dev/null | head -20 || echo "(nginx 無 novnc 配置 → /novnc-ws 可能 404！)"
echo ""
echo "===== 7. mok_web 啟動日誌 (看 VNC Proxy 掛載訊息) ====="
tail -30 /tmp/mok_web.log 2>/dev/null || echo "(無 /tmp/mok_web.log)"
