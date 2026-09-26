# -*- coding: utf-8 -*-
"""
[保丁] 侍女 GPU 用量帳單 API 補回   (2026-09-24 by 稚)
位置: .mok/frontends/mok_web/GPU帳單API_202609241330/保丁.py

症狀:
  /webTools/gpu_status 頁面打得開（走 mok_web.py 的 @app.route('/<path:page>') 萬用路由），
  但頁面內 fetch('/api/gpu/status') 在核心已無註冊，被萬用路由吃掉，
  回純文字 404 "Page not found"，前端 r.json() 拋:
      Unexpected token 'P', "Page not found" is not valid JSON

原因:
  mok_web.py 內的 api_gpu_status / api_gpu_sync 路由在後續版本被移除
  （最後一次出現於 core/插話補丁/backup/mok_web.py.orig:3425）。
  core/gpu_billing.py 本身仍在且可用，只是沒接上路由。

做法 (純新增, 不動核心):
  重新註冊 /api/gpu/status 與 /api/gpu/sync，直接呼叫 core/gpu_billing.py。

停用: 把本目錄改名加前置 _ 即可。
"""
import os
import sys

from flask import request, jsonify

_main = sys.modules.get("__main__")
app = getattr(_main, "app", None)

try:
    _web_file = os.path.abspath(getattr(_main, "__file__", __file__))
    _core_dir = os.path.join(os.path.dirname(os.path.dirname(_web_file)), "core")
    if os.path.isdir(_core_dir) and _core_dir not in sys.path:
        sys.path.insert(0, _core_dir)
except Exception:
    pass


def _gpu_billing():
    import importlib
    if "gpu_billing" in sys.modules:
        return sys.modules["gpu_billing"]
    return importlib.import_module("gpu_billing")


if app is not None:
    @app.route("/api/gpu/status", methods=["GET"])
    def api_gpu_status():
        try:
            sync_first = request.args.get("sync", "1") != "0"
            return jsonify(_gpu_billing().status_payload(sync_first=sync_first))
        except Exception as e:
            return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500

    @app.route("/api/gpu/sync", methods=["GET", "POST"])
    def api_gpu_sync():
        try:
            return jsonify({"ok": True, "summary": _gpu_billing().sync()})
        except Exception as e:
            return jsonify({"ok": False, "error": "%s: %s" % (type(e).__name__, e)}), 500

    print("[保丁][GPU帳單] 已註冊 /api/gpu/status, /api/gpu/sync", flush=True)
else:
    print("[保丁][GPU帳單] 找不到 __main__.app，略過", flush=True)
