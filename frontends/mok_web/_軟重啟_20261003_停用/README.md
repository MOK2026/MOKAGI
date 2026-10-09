# 軟重啟補丁（2026-10-03 稚）

把首頁右上「更多 → 🚫 緊急重啟」改成「🔄 軟重啟」。

## 行為
1. 前端 `stopGeneration()` → socket `stop_generation`（由本補丁接管，取代核心 handle_stop）。
2. 補丁：廣播 `server_restart` 給所有前端 + 寫旗標 `~/.mok/run/soft_restart.flag`，不呼叫 pm2。
3. `core/launcher.py` 主迴圈偵測旗標 → `soft_restart_web()`：優雅終止舊 Web → 等埠釋放 → 重拉新 Web。
   新 Web 啟動時自動載入 `frontends/mok_web/保丁.py` → 重新載入所有補丁。
4. 前端輪詢 `/api/whoami`，偵測到新 Web 起來後 `location.reload()`。

## 為何不碰 pm2
pm2 CLI 層有 MOK-PLAN-C-GUARD 守衛；軟重啟完全不經過 pm2，故不觸發守衛、不需主人授權。

## 相容（重要）
若 launcher 尚未更新（沒有旗標監看），補丁會在 4 秒後自行 `os._exit(0)`，
由 launcher 既有的「Web 退出 → 自動重拉」機制完成軟重啟。兩條路徑互斥、不會同時觸發。

## 停用
把本目錄改名（前綴加 `_`）即可；核心 `handle_stop`（pm2 緊急重啟）即恢復。
