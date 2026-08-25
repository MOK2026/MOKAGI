# WhatsApp Web 自動化技能（whatsappWeb）

> 適用 agent：ws客服
> 實測驗證：2026-08-24（春 agent 實測跑通：登入檢查 → 掃描 /信息 → 開聊天 → 發訊息 → 驗證 ✅）
> 本技能目錄：/home/ubuntu/.mok/skill/whatsappWeb/（產生檔放這）

## 📌 用途
1. **自動發訊息到指定號碼**（WhatsApp Web 網頁版）
2. **收到 /開頭 的信息自動回答**（客服自動應答）
3. **減少錯誤操作 browser 工具的 token 與時間浪費**

## ⚙️ 前置條件（一次性）
- browser 工具已安裝（Chromium + Playwright，`/browser install`）
- 持久化設定檔：`/home/ubuntu/.mok/browser_profile2`（browser.py 內 `_persistent_dir`）
- 已掃過一次 QR 登入 → 登入狀態保存在設定檔，之後免掃碼
- 若由 cron/pm2 常駐輪詢，用本目錄 `wa_auto.py`（見 D 節）

---

## 🚀 標準流程

### A. 啟動與登入檢查（每次操作前必做，~2 秒）
1. `/browser status`
   - 「瀏覽器運行中」→ 跳第 2 步
   - 「頁面已失效」→ `/browser launch`（持久化模式）
   - launch 報 `profile already in use` → **先清理殘留進程**（見錯誤處理表）
2. `/browser goto https://web.whatsapp.com`
3. `/browser wait 4000`
4. 用 **JS 判斷登入狀態**（JS 片段庫 #1）
   - `hasChatList: true` → 已登入 ✅ 直接繼續
   - `hasQR: true` → 未登入 → `/browser screenshot /tmp/wa_qr.png` → **把 QR 圖發給用戶掃**（60 秒內掃完，過期重截）

### B. 發訊息到指定號碼（核心流程，已實測 ✅）
1. 搜尋號碼：
   `/browser type input[aria-label="搜尋或開始新對話"] <號碼>`
   - 號碼格式：`+852 9867 2794`（含空格）或 `85298672794` 皆可
2. `/browser wait 1500`
3. 點擊搜尋結果（用完整號碼避免誤點群組）：
   `/browser click div[role="row"]:has-text("+852 9867 2794")`
   - ⚠️ 號碼在列表顯示為 `+852 9867 2794` 格式，click 用此格式
4. `/browser wait 1500`（等聊天視窗載入）
5. 輸入訊息：
   `/browser type div[contenteditable="true"][data-tab="10"] <訊息內容>`
6. `/browser press Enter`
7. **驗證發送**（JS 片段庫 #5）：`inputBoxCleared: true` = 發送成功 ✅
   - 輸入框清空即代表送出，**不需截圖確認**（省 token）
   - 進階驗證：讀最後一條訊息是否為自己發的（JS #3 的 📤 標記）

### C. 檢查 /信息 並自動回答（核心流程，已實測 ✅）
> 🎯 **重點：不要逐個聊天點擊掃描！** 實測教訓：JS 內 `rows[i].click()` 程式化點擊在 WhatsApp Web **無效**（需真實使用者事件），逐個點 68 個聊天要 136+ 次調用，浪費大量 token。

1. **一次 JS 掃描全部聊天預覽**（JS 片段庫 #6）→ 直接找出含 `/` 的聊天
   - 返回 `slashChats` 陣列：`[{title, text}]`
2. 對每個 `slashChats`：
   - `/browser click div[role="row"]:has-text("<title>")`（title 用聊天名稱/號碼）
   - `/browser wait 1200`
   - 讀取該聊天最後訊息（JS 片段庫 #3）確認 `/xxx` 內容與是否已回覆
3. **判斷是否已回覆**：最後一條訊息若是 📤（自己發的）→ 已回覆，跳過
4. 未回覆 → LLM 生成回覆 → 走 **B 流程**（5→6→7）發送
5. 發完驗證後，繼續處理下一個 slashChats

### D. 常駐自動回覆（cron / pm2，可選）
- 本目錄提供 `wa_auto.py`：用 Playwright 直接操作（同 browser_profile2 設定檔）
- 輪詢每 60 秒：登入檢查 → 掃描 /信息（JS #6）→ 對未回覆者回覆
- ⚠️ **勿與 browser 工具同時執行**（同設定檔會被 lock），cron 排程要避開 agent 操作時段
- 回覆內容由 LLM 產生（本檔 C 節是 agent 手動流程；wa_auto.py 是無 LLM 的規則回覆範本）

---

## 🛠 錯誤處理表（省時間關鍵！先查表，別反覆試錯）
| 錯誤訊息 / 情況 | 原因 | 解法 |
|---|---|---|
| `Opening in existing browser session... profile already in use` | 殘留 Chromium 進程佔用設定檔 | `ps aux \| grep chrome-linux/chrome` 找 PID → `kill <PID>` → 重新 `/browser launch` |
| `瀏覽器頁面已失效` / `瀏覽器尚未啟動` | 瀏覽器被關閉/context 過期 | `/browser launch` 重新啟動（持久化設定檔自動恢復登入） |
| 出現 QR code | 登入狀態失效（少見） | 截圖發用戶掃碼（60 秒內）；掃完自動進主頁 |
| 搜尋不到號碼 | 號碼格式錯 / 未註冊 WA | 改用 `+852` 完整格式；確認號碼有效 |
| **點擊無效 / click 沒反應** | WhatsApp Web 需真實使用者事件 | 用 browser 工具真實 click；**不要**在 execute JS 裡 `el.click()` |
| **網頁碼更新、selector 失效** | WhatsApp 改版 DOM 變動 | 用 JS 片段庫 #4 重新探測元素，更新 selector；仍不行才叫用戶或 LLM 修正 |
| 找不到 /信息 | 訊息在「已封存」 | 點 `已封存`（`/browser click text=已封存`）再掃描（JS #7） |
| 發送後沒反應 | 輸入框 selector 失效 | JS #4 探測 `div[contenteditable="true"][data-tab="10"]` |
| 聊天太多掃描慢 | 逐個點擊太浪費 | 一律用 JS #6 預覽掃描，**禁止**逐個 click 掃描 |

---

## 💡 節省 token / 時間的鐵律
1. **登入檢查用 JS**（一次 execute 返回 JSON），不要用 content/screenshot 判斷
2. **發送驗證用 JS**（輸入框清空），不要截圖
3. **批量讀取用 JS**：一次 execute 返回多項資訊，避免多次往返
4. **掃描 /信息 用 JS #6 預覽法**：一次搞定，不要逐個點擊（實測 68 聊天只需 1 次調用）
5. **wait 用短秒數**（1200~1500ms 足夠），不要無腦 5000ms
6. **已登入就跳過 QR 流程**，別重複截圖
7. 操作失敗先查錯誤處理表，**不要反覆試錯**浪費 token
8. 只有遇到「要刷 QR」或「網頁碼更新點不到」這種**無法自己解決**的才叫用戶 / 用 LLM 修正

---

## 📜 JS 片段庫（browser execute 直接貼）

**#1 登入狀態檢查**（實測）
```js
(() => {
  const hasQR = !!document.querySelector('canvas');
  const hasChatList = !!document.querySelector('#side');
  const bodyText = document.body.innerText.slice(0, 200);
  return JSON.stringify({ hasQR, hasChatList, bodyText: bodyText.slice(0,100), url: location.href });
})()
```

**#2 未讀訊息數 + 聊天預覽**（實測）
```js
(() => {
  const badges = document.querySelectorAll('span[data-icon="unread-count"]');
  const chats = Array.from(document.querySelectorAll('#pane-side div[role="row"]')).slice(0, 30).map(row => {
    const title = row.querySelector('div[title]') ? row.querySelector('div[title]').getAttribute('title') : '';
    const text = row.innerText.replace(/\n/g, ' | ').slice(0, 120);
    return { title, text };
  });
  return JSON.stringify({ unreadCount: badges.length, totalChats: chats.length, chats });
})()
```

**#3 讀取當前聊天最後訊息（含 📤/📥 方向）**（實測）
```js
(() => {
  const msgs = Array.from(document.querySelectorAll('div[data-id^="false_"], div[data-id^="true_"]')).slice(-15).map(el => {
    const text = el.innerText.replace(/\n/g, ' | ').slice(0, 150);
    const isOut = el.dataset.id.startsWith('true');
    return (isOut ? '📤' : '📥') + ' ' + text;
  });
  return msgs.join('\n');
})()
```

**#4 探測元素（DOM 變動 / 點不到時用）**（實測）
```js
(() => {
  const search = document.querySelector('input[aria-label*="搜尋"]');
  const msgBox = document.querySelector('div[contenteditable="true"][data-tab="10"]');
  const rows = document.querySelectorAll('div[role="row"]').length;
  return JSON.stringify({ hasSearch: !!search, hasMsgBox: !!msgBox, rows });
})()
```

**#5 發送驗證**
```js
(() => {
  const box = document.querySelector('div[contenteditable="true"][data-tab="10"]');
  return JSON.stringify({ inputBoxCleared: box ? box.innerText.trim() === '' : false });
})()
```

**#6 掃描全部聊天預覽，找出 / 開頭訊息**（實測 ✅ 最重要！）
```js
(() => {
  const chats = Array.from(document.querySelectorAll('#pane-side div[role="row"]')).map(row => {
    const titleEl = row.querySelector('div[title]') ? row.querySelector('div[title]').getAttribute('title') : '';
    const text = row.innerText.replace(/\n/g, ' ⏎ ').slice(0, 200);
    return { title, text };
  });
  const slashChats = chats.filter(c => c.text.includes(' /') || c.text.startsWith('/') || c.text.includes('⏎ /'));
  return JSON.stringify({ total: chats.length, slashChats });
})()
```

**#7 檢查「已封存」聊天**（實測）
```js
(() => {
  const bodyText = document.body.innerText;
  const idx = bodyText.indexOf('封存');
  return JSON.stringify({ found: idx > -1, context: idx > -1 ? bodyText.slice(Math.max(0, idx-30), idx+30) : 'not found' });
})()
```
- 找到後：`/browser click text=已封存` → 再跑 JS #6

**#8 聊天列表標題清單**（實測）
```js
(() => {
  const chats = Array.from(document.querySelectorAll('#pane-side div[role="row"]')).map(row => {
    const titleEl = row.querySelector('div[title]') || row.querySelector('span[dir="auto"]');
    const title = titleEl ? (titleEl.getAttribute('title') || titleEl.innerText) : '(未知)';
    const text = row.innerText.replace(/\n/g, ' | ').slice(0, 60);
    return `[${i}] ${title} :: ${text}`;
  });
  return chats.join('\n');
})()
```

---

## 📌 實測紀錄（2026-08-24，春 agent）
- 設定檔 `browser_profile2` 保存登入狀態有效，重啟後免 QR ✅
- 搜尋框：`input[aria-label="搜尋或開始新對話"]` ✅
- 訊息輸入框：`div[contenteditable="true"][data-tab="10"]` ✅
- 實例：掃描到 `+852 9867 2794` 有 `/早晨` 未回覆 → 回覆早餐建議 → 驗證輸入框清空 ✅
- ⚠️ **教訓：JS 程式化 `rows[i].click()` 對 WA 無效**，一律用 browser 工具真實 click
- 全部 67 個主聊天 + 已封存都掃過，無其他 / 訊息待回覆
