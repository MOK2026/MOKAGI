# 三級權限閘補丁 v1.0（2026-09-13）

> 掛載方式：保丁（補丁）— 完全不修改 `mok_web.py` / `mokagi.py` 核心。
> 由 `mok_web/保丁.py` 載入器自動掃描載入（目錄名 z 開頭，排序在 ASCII 命名的補丁之後）。

## 目的

落地「三類身份 × 可見頁面 / 可用 agent」：

| 身份 | 可見頁面 | 可用 agent |
|------|----------|-----------|
| admin | 全部 | 全部 |
| member（已登入） | 依方案 `pages` 白名單（由 Plans 補丁把關） | 依方案 `agents` 白名單 |
| 未登入（guest） | 僅 /login /register /plans /logout | 僅 free 方案 agents |

## 作法（不改核心，只兩件事）

1. 包裝 `main.get_env_files` → `/api/env_files` 只吐該身份可用的 agent（列表層過濾）
2. `app.before_request(_page_guard)` → 未登入者瀏覽受限 HTML 頁面時導向 `/login?next=<原路徑>`

## 與其他補丁分工

| 補丁 | 負責 |
|------|------|
| 會員系統_202608311340 | /login /register /member，聊天攔截（agent 權限、扣款） |
| Plans後台_202609051030 | /admin/plans /plans，已登入會員的 pages 白名單 |
| 管理後台_202609011122 | /admin 系列頁面自身權限 |
| **本補丁** | 未登入訪客的頁面閘門 + agent 列表過濾 |

## 可調參數（檔頭）

- `GUEST_PAGES`：未登入仍可看的頁面前綴
- `ALWAYS_OPEN`：完全放行前綴（API / 靜態資源 / 各後台自行判斷）
- `ADMIN_USERS`：管理員帳號，讀環境變數 `ADMIN_USERNAMES`（逗號分隔），預設 `admin`

## 停用

把目錄名前面加底線（如 `_z三級權限閘_202609131430`），重啟 mok_web 即停用。
