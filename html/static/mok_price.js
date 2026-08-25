/* ═══════════════════════════════════════════════════════════
 * mok_price.js - MOKAGI 統一計費（前端唯一讀取器）
 *
 * 唯一價格源：.mok/core/mok_price.py
 * 所有前端頁面透過本檔案讀取價格，修改 mok_price.py 即全局生效。
 *
 * 兩種讀取方式（自動偵測）：
 *   1. 模板注入：{{ MOKAGI_PRICE | tojson }} → window.MOKAGI_PRICE
 *   2. API 讀取：fetch('/api/price')（無注入時自動使用）
 *
 * 用法：
 *   await MokAgiPrice.ready()            // 等待價格就緒
 *   MokAgiPrice.perMillion               // 68（每百萬 token 價格）
 *   MokAgiPrice.perToken                 // 0.000068
 *   MokAgiPrice.setupFee                 // 5000
 *   MokAgiPrice.formatHKD(tokens)        // 計算並格式化費用
 *   MokAgiPrice.cost(tokens)             // 返回費用數值
 * ═══════════════════════════════════════════════════════════ */
window.MokAgiPrice = (function () {
    const _default = {
        currency: "HKD",
        price_per_million: 68,
        price_per_token: 0.000068,
        setup_fee: 5000,
        github: "https://github.com/MOK2026/MOKAGI",
        display: {
            per_million: "HK$68 / 百萬 token",
            setup_fee: "HK$5,000"
        }
    };

    let _price = null;          // 當前價格
    let _promise = null;        // 讀取 Promise（緩存）

    // ── 取得價格（優先模板注入，其次 API）──
    function load() {
        if (_price) return Promise.resolve(_price);
        if (window.MOKAGI_PRICE && window.MOKAGI_PRICE.price_per_million) {
            _price = window.MOKAGI_PRICE;
            return Promise.resolve(_price);
        }
        if (!_promise) {
            _promise = fetch('/api/price')
                .then(r => r.json())
                .then(data => { _price = data; return data; })
                .catch(() => { _price = _default; return _default; });
        }
        return _promise;
    }

    return {
        ready: load,
        get perMillion() { return _price ? _price.price_per_million : _default.price_per_million; },
        get perToken() { return _price ? _price.price_per_token : _default.price_per_token; },
        get setupFee() { return _price ? _price.setup_fee : _default.setup_fee; },
        get currency() { return (_price || _default).currency; },
        get displayPerMillion() { return (_price || _default).display.per_million; },
        get displaySetupFee() { return (_price || _default).display.setup_fee; },

        /** 計算 tokens 的費用（HKD 數值） */
        cost(tokens) {
            const p = _price ? _price.price_per_token : _default.price_per_token;
            return tokens * p;
        },

        /** 格式化費用為 HK$ 字串 */
        formatHKD(tokens) {
            const c = this.cost(tokens);
            if (c < 0.01) return 'HK$' + c.toFixed(4);
            if (c < 1) return 'HK$' + c.toFixed(3);
            return 'HK$' + c.toFixed(2);
        },

        /** 格式化費用（可選貨幣符號） */
        format(tokens) {
            return this.formatHKD(tokens);
        }
    };
})();
