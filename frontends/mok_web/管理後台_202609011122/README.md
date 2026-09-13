# Admin 會員管理後台（方案 B 補丁）

> 掛載方式：保丁（補丁）— 完全不修改核心。
> 由 mok_web/保丁.py 載入器自動掃描載入（目錄名排序於「會員系統」之後）。

## 功能

- 會員列表（含搜尋、統計）：/admin/member
- 新增會員：/admin/member/add
- 改方案：/admin/member/<u>/plan
- 加減餘額（+1000 / -500）：/admin/member/<u>/balance
- 改密碼：/admin/member/<u>/password
- 停用 / 啟用會員：/admin/member/<u>/toggle
- 刪除會員：/admin/member/<u>/delete
- JSON API：/api/admin/members

## 安全

- 僅 ADMIN_USERNAMES（預設 admin）可進入後台
- 停用會員：無法登入、無法使用任何 agent
- admin 帳號不可被停用 / 刪除；不可刪除自己
- 管理 POST 皆需 CSRF token
- 所有操作寫入 admin_log 稽核表

## 環境變數

- ADMIN_USERNAMES：可管理後台的帳號（逗號分隔），預設 admin
