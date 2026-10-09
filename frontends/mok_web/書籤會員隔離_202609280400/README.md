# 書籤會員隔離（202609280400 by indexPage）

## 目的
`/api/bookmark/*` 依身分各自隔離：會員只看到自己的書籤；訪客各自隔離，並套用「匿名沙盒」機制（限時刪、限時可拉回會員房）。

## 對應主人指定 1-8 點
1. 每筆書籤加 `user_id` + uuid `id`
2. `add`：tenant 取不到 -> 401；寫入 `user_id`
3. `list`：只回自己的（admin/root 可讀全部）
4. `delete` / `rename`：改用 `id` 並驗 owner（廢除 index / conv_id 定址）
5. 前端 `書籤.html` 改送 `id`、未登入顯示提示（該檔另改）
6. 舊資料（無 `user_id`）一次性歸 `admin`
7. 重啟 mok_web 生效
8. 訪客各自隔離 + 限時刪 + 限時可拉回會員房

## 訪客機制（第 8 點，與匿名沙盒一致）
- 訪客 tenant = `guest:<mok_anon sid>`（**與匿名沙盒同一把 cookie** `mok_anon`）。
- 限時刪：janitor 每 10 分鐘掃一次；某訪客 tenant 的書籤若「閒置 > TTL_IDLE」或「最早一筆 > TTL_MAX」即整批清除。
  TTL 直接讀取匿名沙盒常數（`main.mok_anon['ttl_idle']` / `['ttl_max']`，預設 65 分 / 6 小時），保持一致。
- 限時可拉回會員房：登入後，`before_request` 會把 `guest:<sid>` / `web_guest_<sid>` 的書籤自動轉給該會員（冪等）。

## 掛載方式
核心 `mok_web.py` 以 `exec` 載入 `mok_web/保丁.py` 載入器，載入器掃描本目錄子目錄（`_` 前綴＝停用）依序載入。
本目錄名 `ｚｚｚｚｚ書籤會員隔離_202609280400`（5 個 ｚ）→ 最後載入，確保覆蓋核心與其他補丁。
透過 `app.view_functions[...]` 覆蓋 view（monkey patch 函數物件不足以換掉已註冊的 view）。

## 停用
目錄改名加 `_` 前綴後重啟。

## 驗證
- 會員 A 看不到會員 B 的書籤。
- 訪客加書籤 → 只有同 cookie 訪客看得到。
- 訪客登入 → 原本訪客書籤自動出現於會員帳號。
- `pm2 logs mok_agi` 可見 `[bookmark-iso] ...`。
