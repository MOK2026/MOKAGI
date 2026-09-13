#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
mok_price.py - MOKAGI 統一計費配置（唯一真源）

═══════════════════════════════════════════════════
⚠️  整個系統的收費標準都在此文件定義！
   修改此文件即可全局生效（後端計費 + 前端所有頁面顯示）。
═══════════════════════════════════════════════════

使用方式：
    後端 Python: from mok_price import price_per_token, cost_for_tokens, to_dict
    前端 JS:     mok_web.py 的 context_processor 自動注入
                 window.MOKAGI_PRICE（所有 Jinja2 模板可用）
                 或 fetch('/api/price') 動態獲取

計費規則：
    - 所有 AI 功能統一收費：HK$68 / 百萬 token
    - ai agent（人工智能助手）另收一次性安裝費：HK$5,000
    - 開發者（開源版）：完全免費
"""

# ========== 收費標準（唯一修改點）==========
MOKAGI_CURRENCY = "HKD"                 # 貨幣
MOKAGI_PRICE_HKD_PER_MILLION = 68       # 使用費：HK$68 / 百萬 token（所有 AI 功能統一收費）
MOKAGI_SETUP_FEE_HKD = 5000             # 一次性安裝費：HK$5,000（ai agent 版）
MOKAGI_GITHUB = "https://github.com/MOK2026/MOKAGI"

# ========== GPU（vast.ai 租機）計費 —— 侍女 GPU 用量帳單 ==========
# 真實成本 = 租機單價(US$/hr) × 租用時數（由 vast.ai API 取得）。
# 以下為「要給用戶看的費率」，尚未定案，先留空：
#   MOKAGI_GPU_MARKUP     成本倍率（例：2.0 = 收成本兩倍）; None = 未定（頁面顯示「費率待定」）
#   MOKAGI_GPU_USD_TO_HKD 美元兌港幣匯率，用於把成本換算成 HK$
MOKAGI_GPU_MARKUP = None
MOKAGI_GPU_USD_TO_HKD = 7.8  # 開源倉庫（開發者免費）
# =========================================


def price_per_token() -> float:
    """每 token 單價（HKD）"""
    return MOKAGI_PRICE_HKD_PER_MILLION / 1_000_000


def cost_for_tokens(tokens: float) -> float:
    """計算指定 token 數的費用（HKD）"""
    return tokens * price_per_token()


def display_per_million() -> str:
    """顯示用：如 'HK$68 / 百萬 token'"""
    return f"{MOKAGI_CURRENCY}${MOKAGI_PRICE_HKD_PER_MILLION} / 百萬 token"


def display_setup_fee() -> str:
    """顯示用：如 'HK$5,000'"""
    return f"{MOKAGI_CURRENCY}${MOKAGI_SETUP_FEE_HKD:,}"


def to_dict() -> dict:
    """返回完整價格信息（供 /api/price 與模板注入）"""
    return {
        "currency": MOKAGI_CURRENCY,
        "price_per_million": MOKAGI_PRICE_HKD_PER_MILLION,
        "price_per_token": price_per_token(),
        "setup_fee": MOKAGI_SETUP_FEE_HKD,
        "github": MOKAGI_GITHUB,
        "display": {
            "per_million": display_per_million(),
            "setup_fee": display_setup_fee(),
        },
        "gpu": {
            "markup": MOKAGI_GPU_MARKUP,
            "usd_to_hkd": MOKAGI_GPU_USD_TO_HKD,
            "display": ("成本 × %.2f（US$1=HK$%.2f）" % (MOKAGI_GPU_MARKUP, MOKAGI_GPU_USD_TO_HKD))
                       if MOKAGI_GPU_MARKUP else "費率待定",
        },
    }


if __name__ == "__main__":
    import json
    print(json.dumps(to_dict(), ensure_ascii=False, indent=2))
