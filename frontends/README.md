# frontends ── MOKAGI 前端目錄

此目錄存放 MOKAGI 對外前端程式，由 core/launcher.py 啟動。

## 現行檔案（勿隨意移動/刪除）
- mok_web.py          網頁前端主程式（gunicorn + eventlet）
- mok_tg.py           Telegram Bot 前端
- gunicorn_config.py  gunicorn 設定
- mok_web/            進化功能模組（各功能一個子資料夾，內含 保丁.py）
- static/             靜態資源（如 voice_msg.mp3）

## _archive/（歷史歸檔，可安全忽略）
2026-09-13 整理：將根目錄 58 個歷史備份/暫存檔移入 _archive/
- _archive/mok_web_bak/   mok_web.py 的 43 個歷史備份
- _archive/mok_tg_bak/    mok_tg.py  的 7 個歷史備份
- _archive/logs/          _mok_web_restart.log、scp_error_*.txt
- _archive/misc/          0.md、測試檔、CPU_*.bat 等雜項

## 備份還原
*.bak 檔為還原用歷史快照（見 skill/進化/編輯登記.json 記錄）。
還原前請先登記編輯鎖：python3 skill/進化/editlock.py start 檔案 Agent 目的
