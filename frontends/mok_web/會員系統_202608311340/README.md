# 會員系統補丁（方案 B）

> 掛載方式：保丁（補丁）— **完全不修改 `mok_web.py` / `mokagi.py` 核心**。
> 由 `mok_web/保丁.py` 載入器自動掃描載入。

## 功能

| 功能 | 說明 |
|------|------|
| 👤 多用戶登入 | 註冊 / 登入 / 登出，取代匿名 UUID 隔離 |
| 💳 付費分級 | free / pro / vip 三方案，不同方案可用不同 agent |
| 🔢 Token 計費 | 攔截 `process_message`，按實際用量扣款 |
| 🛑 餘額控制 | 餘額不足自動拒絕服務 |

## 檔案結構

```
會員系統_202608311340/
├── 保丁.py        ← 核心邏輯（會員 + 分級 + 計費 + 攔截）
├── member.db      ← SQLite（首次載入自動建立）
└── README.md      ← 本說明
```

## 頁面

- `/login` — 登入
- `/register` — 註冊（免費方案）
- `/logout` — 登出
- `/member` — 會員中心（方案 / 餘額 / 用量）
- `/api/member/me` — JSON API（前端整合用）

## 預設帳號

| 帳號 | 密碼 | 方案 |
|------|------|------|
| admin | admin123 | vip（餘額 1 兆，請盡快改密碼） |

改密碼：登入後於伺服器執行
```sql
sqlite3 member.db "UPDATE users SET password_hash='<新hash>' WHERE username='admin';"
```
Hash 算法：`sha256('mok_member_v1' + 密碼)`

## 方案管理

預設方案（存於 `member.db` 的 `plans` 表）：

| 方案 | 可用 agent | 月額度 |
|------|-----------|--------|
| free | 凜、客服、稚、春、備、卓、現 | 50,000 |
| pro | 全部 | 500,000 |
| vip | 全部 | 2,000,000 |

調整方案：
```sql
-- 改 free 可用 agent
UPDATE plans SET agents='["凜","客服"]' WHERE plan='free';
-- 改用戶方案
UPDATE users SET plan='pro' WHERE username='某人';
```

## 設定（保丁.py 頂部）

- `ALLOW_GUEST_CHAT`：False=未登入不能聊天（嚴格多用戶）；True=訪客可用預設額度
- `GUEST_DAILY_TOKENS`：訪客每日額度
- `DEFAULT_PLANS`：方案定義

## 運作原理

1. 保丁載入器 `exec` 執行本檔 → 取得 `__main__`（即 mok_web 模組）與 `mokagi` 模組
2. `mokagi.process_message` 被包裝（monkey patch）：
   - 判斷是否網頁 `/api` 請求 → 檢查登入 → 檢查 agent 權限 → 檢查餘額
   - 呼叫原始 `process_message`（強制 `user_id=會員帳號`，讓 token 歸戶）
   - 完成後比對 `token_usage` 表差額，扣除會員餘額
3. 非網頁調用（Telegram / 後台 job）不受影響，照常運作

## 停用

把本目錄改名（前面加 `_`）→ `_會員系統_202608311340`，重啟服務即停用。
