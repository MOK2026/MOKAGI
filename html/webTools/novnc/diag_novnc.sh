#!/usr/bin/env bash
# ============================================
# MOKAGI 沙箱桌面 - 完整診斷腳本 v2
# 在宿主機執行：bash ~/.mok/html/webTools/novnc/diag_novnc.sh
# ============================================
echo "===== 1. 桌面服務進程 ====="
ps aux | grep -E "Xvfb|fluxbox|chromium|x11vnc|websockify" | grep -v grep || echo "(無桌面進程！)"
echo ""
echo "===== 2. 端口監聽 ====="
(ss -tlnp 2>/dev/null || netstat -tlnp 2>/dev/null) | grep -E ':(80|5000|5900|6080|9222)\s' || echo "(端口未監聽！)"
echo ""
echo "===== 3. nginx 狀態 ====="
systemctl status nginx 2>/dev/null | head -5 || nginx -t 2>&1
echo ""
echo "===== 4. WebSocket 測試 (6080) ====="
curl -s -o /dev/null -w "HTTP: %{http_code}\n" --max-time 3 http://127.0.0.1:6080/ || echo "❌ 6080 無法連線"
echo ""
echo "===== 5. nginx /novnc-ws 測試 ====="
curl -s -o /dev/null -w "HTTP: %{http_code}\n" --max-time 3 http://127.0.0.1/novnc-ws || echo "❌ /novnc-ws 無法連線"
echo ""
echo "===== 6. Chromium CDP 測試 ====="
curl -s --max-time 3 http://127.0.0.1:9222/json/version 2>/dev/null | head -5 || echo "❌ Chromium CDP 未就緒"
echo ""
echo "===== 7. nginx 配置檢查 ====="
grep -rn "novnc\|mokagi\|proxy_pass" /etc/nginx/sites-enabled/ 2>/dev/null | head -20 || echo "(無 nginx 配置)"
echo ""
echo "===== 8. 磁碟空間 ====="
df -h /home/ubuntu/.mok | tail -1
echo ""
echo "===== 9. mok_web 日誌 ====="
tail -20 /tmp/mok_web.log 2>/dev/null || echo "(無日誌)"
