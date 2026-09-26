#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gpu_snapshot.py — GPU 帳單儀表板快照產生器   (2026-09-24 by 稚)
================================================================
背景
    mok_web.py 的 /api/gpu/status 路由在 2026-09-19 ~ 09-20 的版本更迭中被移除
    （最後一次出現於 core/插話補丁/backup/mok_web.py.orig:3425），
    導致 /webTools/gpu_status 頁面 fetch 到萬用路由的純文字 404：
        Unexpected token 'P', "Page not found" is not valid JSON

    正式修法已放在 frontends/mok_web/GPU帳單API_202609241330/保丁.py，
    但需 mok_web 重啟才會生效。在重啟之前，本腳本提供「免重啟」的資料來源：
    把 gpu_billing.status_payload() 寫成靜態 JSON，
    網頁改讀 ~/.mok/html/static/gpu_status.json（靜態檔每次請求都重讀，立即生效）。

用法
    python3 ~/.mok/tools/gpu_snapshot.py
    （建議由 cron 每 5 分鐘執行一次）

輸出
    ~/.mok/html/static/gpu_status.json
"""
import json
import os
import sys

CORE = os.path.expanduser("~/.mok/core")
if os.path.isdir(CORE) and CORE not in sys.path:
    sys.path.insert(0, CORE)

OUT = os.path.expanduser("~/.mok/html/static/gpu_status.json")


def main():
    import gpu_billing

    payload = gpu_billing.status_payload(sync_first=True)
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(payload, f, ensure_ascii=False)
    os.replace(tmp, OUT)

    totals = payload.get("totals", {})
    acct = payload.get("vast_account") or {}
    print("[gpu_snapshot] ok  maids=%d running=%s cost=%.4f balance=%s -> %s"
          % (len(payload.get("maids", [])), totals.get("running"),
             float(totals.get("cost_usd") or 0), acct.get("balance"), OUT))
    return 0


if __name__ == "__main__":
    sys.exit(main())
