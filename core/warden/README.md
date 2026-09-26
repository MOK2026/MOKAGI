# Warden — MOKAGI 緊急守衛（全新獨立系統）

> 目的：主人要的「緊急鍵」。**不修改** 現有 `緊急重啟`(index.html stopBtn)、
> **不使用** `pm2 restart mok_agi`。完全獨立，日後可作為所有 mokagi 服務的總控入口。

## 檔案
| 檔案 | 作用 |
|---|---|
| `warden.py` | 主程式（純 stdlib，無第三方依賴） |
| `warden.json` | 服務清單 / 擊殺名單 / watchdog 設定 |
| `warden.html` | 操作面板（大顆緊急停止鈕） |
| `LOCK` | panic 後產生的鎖檔（watchdog 見鎖不復活） |
| `warden.log` | 執行日誌 |

## 啟動
```bash
python3 /home/ubuntu/.mok/core/warden/warden.py
# 或交給 pm2：
pm2 start /home/ubuntu/.mok/core/warden/warden.py --name mok_warden --interpreter python3
```

## 端點（僅 127.0.0.1:5599）
| Method | Path | 作用 |
|---|---|---|
| GET | `/` | 操作面板 |
| GET | `/api/status` | 各服務 PID / 狀態 / 是否上鎖 |
| POST | `/api/panic` | **緊急停止**：pm2 stop + 擊殺殘留 + 上鎖 |
| POST | `/api/resume` | 解鎖並 `pm2 start`（非 restart） |
| POST | `/api/restart?name=mok_agi` | 單一服務安全重啟（stop + start） |
| GET | `/api/log?n=200` | 最近日誌 |

## 安全設計
1. warden 自身 / 祖先 / 子進程 永不擊殺。
2. 先 SIGTERM，逾時才 SIGKILL。
3. 只綁 127.0.0.1；可於 `warden.json` 設 `token` 做簡易驗證。
4. panic 寫 `LOCK`；`watchdog` 遇鎖不動作，避免「停了又自己活過來」。

## 未完成 / 待主人決定
- **對外曝露方式**：目前僅監聽 127.0.0.1。要能用手機按，需決定：
  (a) 走現有 web 反向代理掛一個 `/warden`；或 (b) 另開 tunnel；或 (c) 綁 0.0.0.0 + token。
- 是否啟用 `watchdog.enabled=true` 自動拉回關鍵進程。

## agent 專用的安全重啟通道（#2 方案 A）
agent **不需要**再自己 `kill`，只要寫一個旗標檔，warden 每 3 秒掃一次並代為安全重啟：
```bash
touch ~/.mok/core/warden/requests/mok_web.req     # 想重啟哪個服務就寫哪個名字
```
（可用 `curl -X POST 'http://127.0.0.1:5599/api/restart?name=mok_web'` 等效。）

## 落地狀態（2026-09-10）
| 項目 | 狀態 |
|---|---|
| #2 安全重啟機制（新建 warden） | ✅ 已上線（pm2 `mok_warden`，127.0.0.1:5599） |
| #2 方案 A 旗標檔通道 | ✅ 已啟用（`requests/*.req`） |
| #1 自殺防護 | ✅ 代碼已加（`tools/admin.py::_guard_self_kill`），**待 mok_web 下次重啟生效** |
| #3 mok_web SIGTERM 優雅關機 | ✅ 代碼已加，**待 mok_web 下次重啟生效** |
| #4 kill 黑名單（`\bkill\b`） | ✅ 代碼已加，**待 mok_web 下次重啟生效**（目前 `exec` 於 web 進程仍會放行 kill） |


