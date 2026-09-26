#!/bin/sh
# ══════════════════════════════════════════════════════════════
#  Mok_AI_Advisor_news.sh
#  莫氏集團 · AI 應用落地顧問 — 最近新聞自動更新腳本
#
#  用法：
#    bash MoksAv/Mok_AI_Advisor_news.sh
#    或加入 crontab 每日自動更新：
#    0 9 * * * bash /home/ubuntu/.mok/html/project/MoksAv/Mok_AI_Advisor_news.sh
# ══════════════════════════════════════════════════════════════

set -e

BASE_DIR="$(cd "$(dirname "$0")/.." && pwd)"
INDEX="$BASE_DIR/index.html"
NEWS_TMP="$(mktemp)"
OUT_TMP="$(mktemp)"

echo "📰 莫氏集團 · AI 新聞更新"
echo "   目標檔案: $INDEX"

cat > "$NEWS_TMP" << 'NEWSEOF'
<div class="news-item">
    <div class="news-title">🇹🇼 <a href="https://www.meta-intelligence.tech/insight-genai-trends-2026" target="_blank" rel="noopener">台灣 2026 生成式 AI 六大趨勢：72% 企業已導入，但僅 15% 實現規模化</a></div>
    <div class="news-desc">超智諮詢引 McKinsey 數據指出，72% 企業已在核心業務流程導入生成式 AI，但能實現規模化價值者僅 15%。2026 年六大趨勢包括：多模態原生模型、推理模型崛起、AI Agent 標準化、小型語言模型（SLM）、合成資料與 AI 治理法規。</div>
    <div class="news-meta"><span class="news-flag">📍 台灣</span> · 出處：meta-intelligence.tech · 2026</div>
</div>

<div class="news-item">
    <div class="news-title">🇯🇵 <a href="https://www.ai-souken.com/article/ai-adoption-japan-status" target="_blank" rel="noopener">日本生成 AI 導入率達 57.7%，3 年成長約 1.7 倍，但仍落後中美約 35–40 百分點</a></div>
    <div class="news-desc">NRI 2025 年調查顯示日本大企業生成 AI 導入率由 2023 年 33.8% 升至 57.7%，含「檢討中」達 73%。總務省數據顯示日本企業整體利用率僅 55.2%，遠低於中國 95.8% 與美國 90.6%。最大障礙是 31.8% 企業「方針未策定」。</div>
    <div class="news-meta"><span class="news-flag">📍 日本</span> · 出處：AI総合研究所（ai-souken.com）引 NRI / 總務省 · 2026-06</div>
</div>

<div class="news-item">
    <div class="news-title">🇸🇬 <a href="https://traineticsacademy.com.sg/ai-strategy-for-smes-in-singapore-2026-trend-analysis-and-implementation-guide" target="_blank" rel="noopener">新加坡政府目標 3 年支援 10,000 家企業導入 AI，大企業採用率 62.5% 但中小企僅 14.5%</a></div>
    <div class="news-desc">新加坡國家 AI 策略 2.0 推動下，政府透過 2026 企業創新計劃提供 400% 稅務扣減及 SkillsFuture 資助培訓。零售業 AI 庫存預測準確率已達 98%。關鍵趨勢包括行業專屬 AI Agent、超本地化模型與低程式碼普及化。</div>
    <div class="news-meta"><span class="news-flag">📍 新加坡</span> · 出處：traineticsacademy.com.sg · 2026</div>
</div>

<div class="news-item">
    <div class="news-title">🇰🇷 <a href="https://www.dongascience.com/en/news/76937" target="_blank" rel="noopener">韓國投入 625 億韓元「AI 整合券」計劃，支援中小企業 AI 落地</a></div>
    <div class="news-desc">韓國科學技術情報通信部（MSIT）2026 年推出「AI 整合券」計劃，總規模 625 億韓元。涵蓋 AI Voucher（252 億）、Data Voucher（72 億）、Cloud Voucher（41 億）及 AX 一站式 Voucher（260 億），自 2019 年起持續協助中小企業進行 AI 方案開發與部署。</div>
    <div class="news-meta"><span class="news-flag">📍 韓國</span> · 出處：DongA Science（dongascience.com） · 2026</div>
</div>

<div class="news-item">
    <div class="news-title">🇪🇺 <a href="https://www.hklaw.com/en/insights/publications/2026/04/us-companies-face-eu-ai-acts-possible-august-2026-compliance-deadline" target="_blank" rel="noopener">EU AI Act 高風險系統合規期限 2026 年 8 月生效，全球企業面臨關鍵抉擇</a></div>
    <div class="news-desc">歐盟 AI 法案高風險 AI 系統合規要求將於 2026 年 8 月 2 日生效，涵蓋生物辨識、關鍵基礎設施、教育、就業、信用評分及保險等領域。非歐盟企業只要其 AI 輸出影響歐盟居民即受管轄，違規罰款最高 1,500 萬歐元或全球年營收 3%。</div>
    <div class="news-meta"><span class="news-flag">📍 歐洲</span> · 出處：Holland & Knight（hklaw.com） · 2026-04</div>
</div>
NEWSEOF

awk -v newsfile="$NEWS_TMP" '
    /<!-- MOK_NEWS_START -->/ {
        print
        while ((getline line < newsfile) > 0) print line
        close(newsfile)
        in_news = 1
        next
    }
    /<!-- MOK_NEWS_END -->/ { in_news = 0 }
    !in_news { print }
' "$INDEX" > "$OUT_TMP" && cat "$OUT_TMP" > "$INDEX"

rm -f "$NEWS_TMP" "$OUT_TMP"

echo "✅ 已更新最近新聞（5 條）→ $INDEX"
echo "   🇹🇼 台灣 / 🇯🇵 日本 / 🇸🇬 新加坡 / 🇰🇷 韓國 / 🇪🇺 歐洲"
