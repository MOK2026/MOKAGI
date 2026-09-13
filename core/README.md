# core 目錄結構說明

MOKAGI 核心程式所在。以下為 **執行中模組，請勿隨意搬移**（多被主引擎、啟動器、前端 import 載入）。

## 主要模組
| 模組 | 用途 |
|---|---|
| mokagi | 主引擎（LLM 對話、工具調度） |
| launcher | pm2 入口 mok_agi，啟動各 agent |
| config | 全域設定（被引用最多） |
| shared | 共用狀態 |
| tool_handler | 工具處理 |
| recovery | 錯誤恢復 |
| logger | 日誌 |
| app_loop | asyncio 事件迴圈相容層 |
| vnc_proxy | noVNC WebSocket 代理（前端以 core 模組引用） |
| girl_engine, girl_gate, girl_switch | 角色(女聲)相關 |
| gpu_billing, mok_price, mok_token | 計費與 token |
| editlock_hook, autofix2 | 編輯鎖 hook、自動修錯 |
| 其餘 | 輔助模組 |

## 子目錄
- backups — 歷史備份（bak）與退役檔
- logs — 離散日誌檔
- docs — 說明文件
- scripts — 輔助腳本
- 502補丁、插話補丁、暫停補丁 — 功能補丁包（Python package）
- warden — 看門狗（pm2 mok_warden 入口）

## 維護約定
1. 新增檔案請放對應子目錄。
2. 備份命名以 bak 為後綴並歸入 backups。
3. 移動任何模組前，先確認無 import 引用（避免破壞系統）。
