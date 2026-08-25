# 🤖 賺錢王 - 香港商家 AI 客服機會掃描器

## 這是什麼？
自動掃描**香港商家**，找出 2 類潛在客戶：
- **A. 沒網頁的商家** → 可推銷「建網站 + AI 客服」方案
- **B. 有網頁但沒 AI 客服的商家** → 可推銷「加裝 AI 客服」方案

每個商家自動提取 **WhatsApp / Email** 聯絡方式（**不取電話、不取地址**），
供業務人員直接透過 WhatsApp 或 Email 開發客戶。

## 檔案
| 檔案 | 說明 |
|------|------|
| `scanner.py` | 主程式（Google Maps 掃描 + AI客服檢測 + 提取 WhatsApp/Email + 去重 + 報告） |
| `build_mokcs.py` | 克隆商家首頁到 MokCs（**只複製有 WhatsApp 或 Email 的商家**，注入 AI 客服 Agent 做 Demo） |
| `run.sh` | 一鍵執行腳本 |
| `leads_7days.jsonl` | **7 天去重記錄檔**（自動清除 7 天前舊資料；**只記錄有 WhatsApp 或 Email 的潛在客**） |
| `build_mokcs.py --nosite-only` | 為「沒網站但有聯絡」的商家快速建立一頁式自適應網頁（含 MOKAGI 客服） |
| `報告.html` | 最新掃描報告（顯示 WhatsApp / Email，無電話無地址） |
| `scanner.log` | 執行日誌 |

## 手動執行
```bash
# 預設掃描 12 個行業（餐廳/美容/髮型屋/裝修/寵物店…）
cd ~/.mok/skill/賺錢王 && ./run.sh

# 只掃指定行業
python3 scanner.py 寵物店
python3 scanner.py 餐廳 美容 裝修

# 每行業最多處理 N 間
python3 scanner.py 餐廳 --limit 30

# 掃完後克隆有 WhatsApp/Email 的商家首頁到 MokCs
python3 build_mokcs.py
```

## 潛在客戶記錄規則（重要）
- **leads_7days.jsonl 只記錄「有 WhatsApp 或 Email」的潛在客**（A 沒網頁 / B 有網頁無AI客服）
- 沒聯絡方式的商家**完全不記錄**、**不列入報告**、**不複製**（沒聯絡＝無法開發，記錄無價值）
- 報告由全量 leads 產生，反映所有累積潛在客，不是只列當次掃描

## 網頁製作規則（重要）
- **有網站的** → 只複製「有 WhatsApp 或 Email」的商家首頁到 `~/.mok/html/project/MokCs/`
- **沒網站的** → 若該商家有 WhatsApp 或 Email，快速做一頁式自適應網頁（格式同複製頁，放同一目錄）
- **所有頁面都要加 MOKAGI api 客服**（右下角 AI 客服氣泡），供業務直接拿來開發客戶

## 聯絡方式規則（重要）
- **報告只顯示 WhatsApp 與 Email**，不要電話、不要地址
- 從商家網站 HTML 自動提取：
  - WhatsApp：`wa.me/號碼`、`api.whatsapp.com/send?phone=`、`whatsapp.com/send?phone=` 等
  - Email：`mailto:` 或網頁上的 email 字串（自動排除圖片/資源檔）
- **MokCs 克隆時，沒有 WhatsApp 或 Email 的商家一律不複製**

## 去重邏輯
- 所有檢查過的商家寫入 `leads_7days.jsonl`（含名稱+電話 hash key）
- 7 天內再次遇到 → 自動跳過，不會重複打擾
- 超過 7 天的舊資料自動滾動清除

## AI 客服檢測
抓取商家網站 HTML，偵測 20+ 種客服平台：
Tawk.to、Intercom、Crisp、Tidio、Chatwoot、Zendesk、LiveChat、Freshchat、
HubSpot、Drift、ManyChat、LivePerson、Salesforce、智齒、美洽、七魚、環信、53客服、LINE OA…
另標記即時通訊渠道（WhatsApp/WeChat/FB Messenger/Telegram/LINE）供參考。

## 依賴
requests、beautifulsoup4、playwright（build_mokcs 抓取需要）
