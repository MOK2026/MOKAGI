# -*- coding: utf-8 -*-
"""mok_logging_setup.py — 全系統日誌分流（P0-3 2026-10-04 by 稚）

單一真相：把 root logger 的輸出依「等級」分流到不同 fd——
    INFO / DEBUG  -> sys.stdout   （launcher stream_reader 接到 -> <agent>.log）
    WARNING 以上  -> sys.stderr   （launcher stream_reader 接到 -> <agent>_ERR.log）

目的：<agent>_ERR.log 只保留真正的錯誤（WARNING/ERROR/CRITICAL + traceback），
     不再被 INFO（job 提醒、admin exec 回顯、載入訊息…）灌爆到數十 MB。

特性：
  * 幂等：重複呼叫只設定一次（以 root._mok_split 標記）。
  * 只清掉 basicConfig 預設的 StreamHandler，不動 FileHandler 等自訂 handler。
  * MOK_LOG_LEVEL 可調 root 等級（預設 INFO）。
  * MOK_LOG_UNIFIED=1 → 全部照舊走 stderr（一鍵回舊行為，便於對照）。
"""
import os
import sys
import logging


def setup():
    root = logging.getLogger()
    if getattr(root, "_mok_split", False):
        return
    level = getattr(logging, os.environ.get("MOK_LOG_LEVEL", "INFO").upper(), logging.INFO)

    if os.environ.get("MOK_LOG_UNIFIED", "0").strip().lower() in ("1", "true", "yes", "on"):
        root.setLevel(level)
        for h in list(root.handlers):
            if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
                h.setLevel(level)
        root._mok_split = True
        return

    fmt = logging.Formatter("%(levelname)s:%(name)s:%(message)s")
    # 清掉 basicConfig 預設的 std StreamHandler（保留 FileHandler 等）
    for h in list(root.handlers):
        if isinstance(h, logging.StreamHandler) and not isinstance(h, logging.FileHandler):
            root.removeHandler(h)

    out_h = logging.StreamHandler(sys.stdout)
    out_h.setLevel(level)
    out_h.addFilter(lambda r: r.levelno < logging.WARNING)
    out_h.setFormatter(fmt)

    err_h = logging.StreamHandler(sys.stderr)
    err_h.setLevel(logging.WARNING)
    err_h.setFormatter(fmt)

    root.setLevel(level)
    root.addHandler(out_h)
    root.addHandler(err_h)
    root._mok_split = True


setup()
