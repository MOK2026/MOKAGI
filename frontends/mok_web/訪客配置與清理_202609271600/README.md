# 訪客配置放寬 + 訪客歷史清理策略（2026-09-27 by 凜）

## 補丁內容
1. **`/api/mok_config` 訪客放寬**
   - 原（2026-09-26 泠 資安加固）：`if not _is_privileged_session(): return 403`
     → 未登入訪客 fetch 得 403、`configData = {}` → 前端 agent 圖示/配置全走預設值（`🌸`）。
   - 改：不再 403，一律回傳 `_safe_mok_config()`（白名單前綴 + DENY 子字串 `token/secret/key/users/chat_id` 過濾）。
   - 機密設定仍在 `_safe_mok_config` 內被擋除，無外洩風險；訪客取得的是非機密 UI 設定（含 `MOK_AGENT_ICON`）。
   - 以 `app.view_functions['get_mok_config']` 覆蓋（monkey patch 函數物件不足以換掉已註冊的 view）。

2. **訪客歷史清理策略（10 天）**
   - `chat_history` / `conversation_history` 中 `tenant` 為 `guest:*` 或 `web_guest_*` 的列，
     凡 `timestamp < now - 10*86400` 即刪除。
   - 啟動後 90 秒起跑一次，之後每小時檢查一次（daemon 執行緒）。
   - 可呼叫 `main.cleanup_old_guest_rows(days=10, force=True)` 手動執行，回傳刪除筆數。

## 掛載方式
核心 `mok_web.py` 於 `__main__` 以 `exec` 載入 `mok_web/保丁.py` 載入器，
載入器掃描本目錄下所有子目錄（`_` 前綴視為停用）依序載入。
本補丁目錄名 `訪客配置與清理_202609271600`（非 `_` 開頭）→ 啟用。

## 停用
把目錄改名加 `_` 前綴即可，無需改核心。

## 驗證
- 訪客：`curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:5000/api/mok_config` → 200
- 清理：見 `pm2 logs mok_agi` 的 `[訪客清理] 🧹 ...`
