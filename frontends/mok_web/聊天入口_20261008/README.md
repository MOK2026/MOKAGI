# 聊天入口_20261008（/chat 正式入口）

作者：API平台工程師｜建立：2026-10-08

## 做什麼
把聊天頁收斂成單一正式入口 /chat。

| 路徑 | 行為 |
|---|---|
| /chat | 200，渲染 index.html（正式入口） |
| /index.html | 301 永久轉址到 /chat |
| / | 301 永久轉址到 /chat |

## 怎麼做（不改核心 mok_web.py）
- 以 app.add_url_rule 註冊兩條靜態規則：/chat 與 /index.html。
  靜態規則優先於 catch-all，子路徑（例 /report/x/index.html）完全不受影響。
- 根路徑 / 已由核心註冊 endpoint index，同路徑再加規則無效（先註冊者勝），
  故改寫 app 既有的 index 檢視函式。

## 影響面
- 只影響根層三條路徑；其他頁面與子路徑不受影響。
- 未登入訪客要能看 /chat，需 settings.public_pages 內含 /chat（已於 2026-10-08 加入）。
- 改 Python 路由需由主人手動重啟 mok_web 才生效。

## 回滾
目錄改名為 _聊天入口_20261008 後重啟 mok_web，即恢復原狀。
注意：301 會被瀏覽器與中繼快取，回滾後部分用戶需清快取。
