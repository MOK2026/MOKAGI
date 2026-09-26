# Plans 層級後台（方案三欄權限編輯）

> 掛載方式：保丁（補丁）— 完全不修改 `mok_web.py` / `mokagi.py` 核心。
> 由 `mok_web/保丁.py` 載入器自動掃描載入（目錄名排序最先，僅新增路由，不覆蓋他人）。

## 功能

- **plans 表擴充三欄權限**：在既有 `agents`（可用 Agent）之外，新增
  `pages`（可瀏覽頁面）與 `pets`（可用桌面寵物款式）兩個權限欄位，
  每個層級方案共「三欄權限」。
- **後台編輯頁 `/admin/plans`**：三欄卡片（free / pro / vip 並排），
  每張卡片可編輯該層級的月額度、描述與三欄權限；管理員限定。
- **前台方案展示 `/plans`**：公開的三欄對照卡片（三種方案並排），
  顯示每月額度與權限摘要。
- **頁面權限檢查**：登入會員瀏覽受保護 HTML 頁面前，若其層級的
  `pages` 白名單不含該路徑（且非 `["*"]`）→ 403。
  預設三層級 `pages=["*"]`、`pets=["*"]`（不限制），主人於後台設定後才生效。

## 權限語義（三欄）

| 欄位 | 內容 | 格式 |
|------|------|------|
| agents | 可用 Agent（聊天白名單） | JSON 陣列，`["*"]`=全部 |
| pages | 可瀏覽頁面（路徑白名單） | JSON 陣列，`["*"]`=全部 |
| pets | 可用桌面寵物款式 | JSON 陣列，`["*"]`=全部 |

## 頁面

- `/admin/plans` — 後台三欄編輯（僅 admin）
- `/admin/plans/<plan>` — 單一層級儲存（POST，CSRF 防護）
- `/plans` — 前台三欄展示

## 安全

- 後台僅 ADMIN_USERNAMES（預設 admin）可進入
- 管理 POST 皆需 CSRF token
- 頁面權限檢查預設全放行，避免影響現況

---

## v2.0 擴充（2026-09-23）公開層 / 訪客池

### 新增資料
- `plans.requires_login`：1=需登入（預設）、0=公開層（免登入）
- `plans.guest_quota`：訪客每日可用 mokagi 次數（0=不限）
- `guest_usage(guest_id, day, plan, used, first_ts, last_ts)`：訪客每日用量

### 新增行為
1. `/admin/plans` 頁面底部自動多出「第四欄：公開層 / 訪客池」（勾選 + 額度輸入 + 今日訪客用量）
2. 新路由 `POST /admin/plans/public/<plan>`：儲存公開層設定（管理員 + CSRF）
3. 未登入訪客瀏覽頁面：只放行公開層方案的 `pages` 白名單（可在程式內 `_BLOCK_NONPUBLIC_GUEST=False` 關閉）
4. `/api/chat`、`/api/chat/start`：訪客超過 `guest_quota` 回傳 429 並附中文提示

### 安全設計
- 未設定任何公開層（全部 requires_login=1）時，行為與 v1 完全相同 → 零影響
- 訪客身分沿用「ｚ訪客身分綁定」補丁頒發的 `guest:<uuid>`（session / 簽章 cookie），前端無法偽造
- 所有資料庫操作皆為冪等 ALTER / CREATE IF NOT EXISTS

### 回滾
把本目錄改名加底線開頭（停用），並把 `_Plans後台_v1_backup` 改回 `Plans後台_202609051030`，重啟即可。
