# 保丁：酒倉貨位記錄 存檔 API

日期：2026-09-23
目的：修復「酒倉貨位記錄，網頁刷新後紀錄消失」

## 問題根因
1. 網頁以 https://64071181.xyz/report/春/酒倉/index.html 開啟。
2. 該路徑由 mok_web.py 的 /report/<agent>/<path:filename> 提供，只支援 GET（唯讀靜態服務）。
3. 前端 syncToFile() 用 POST 寫 save.json，伺服器回 405 Method Not Allowed，永遠寫唔入檔。
4. 前端載入邏輯原本是「save.json 有內容就以檔案為準，忽略 localStorage」，
   所以每次刷新都被舊的 save.json 覆蓋，新紀錄消失。

## 修法
- 後端：本保丁新增 POST /report_save/<agent>/<path:filename>，把 jobs/ 內的 .json 寫入磁碟
  （限制：限 .json、<=1MB、必須合法 JSON、防路徑穿越、原子寫入）。
- 前端：index.html 偵測到自己在 /report/ 之下時，自動改用 /report_save/ 上傳；
  載入時改為 save.json 與 localStorage 去重合併，兩邊都唔會漏。

## 停用
把本目錄改名（前面加底線）即可停用，無需改核心。

