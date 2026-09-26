# -*- coding: utf-8 -*-
"""
[保丁] 酒倉貨位記錄 存檔 API（POST /report_save）
位置: .mok/frontends/mok_web/酒倉存檔_202609232100/保丁.py
日期: 2026-09-23

背景:
  酒倉貨位記錄頁 (agent/春/jobs/酒倉/index.html) 經
    https://64071181.xyz/report/春/酒倉/index.html
  開啟時，頁面 POST save.json 會打到 mok_web 的 /report/<agent>/<path> 路由，
  該路由只支援 GET → 405 Method Not Allowed，
  記錄永遠寫唔入 save.json；刷新時又用舊 save.json 覆蓋本機記錄 → 「記錄消失」。

做法（純新增，唔改 mok_web.py）:
  新增 POST /report_save/<agent>/<path:filename>
    1. 只准寫入 agent jobs/ 目錄內、副檔名 .json、大小 <= 1MB 的檔案
    2. 內容必須為合法 JSON
    3. tmp + os.replace 原子寫入，避免半寫入壞檔
  前端 index.html 偵測到位於 /report/ 時，會自動改打 /report_save/。
"""
import json
import os
import sys

from flask import jsonify, request

_main = sys.modules.get("__main__")
app = getattr(_main, "app", None)
ENV_DIR = getattr(_main, "ENV_DIR", None) or os.path.expanduser("~/.mok/agent")

MAX_BYTES = 1024 * 1024  # 1MB


def _report_save(agent=None, filename=""):
    if not agent or "/" in agent or "\\" in agent or ".." in agent:
        return jsonify({"success": False, "error": "invalid agent"}), 400
    if not (filename or "").lower().endswith(".json"):
        return jsonify({"success": False, "error": "only .json allowed"}), 403

    jobs_dir = os.path.join(ENV_DIR, agent, "jobs")
    jobs_real = os.path.realpath(jobs_dir)
    full = os.path.realpath(os.path.join(jobs_dir, filename))
    if not full.startswith(jobs_real + os.sep):
        return jsonify({"success": False, "error": "forbidden"}), 403

    raw = request.get_data() or b""
    if len(raw) > MAX_BYTES:
        return jsonify({"success": False, "error": "too large"}), 413
    try:
        json.loads(raw.decode("utf-8"))
    except Exception as exc:
        return jsonify({"success": False, "error": "bad json: %s" % exc}), 400

    os.makedirs(os.path.dirname(full), exist_ok=True)
    tmp = full + ".tmp"
    with open(tmp, "wb") as fp:
        fp.write(raw)
    os.replace(tmp, full)
    return jsonify({"success": True, "path": filename, "bytes": len(raw)})


if app is not None:
    app.add_url_rule(
        "/report_save/<agent>/<path:filename>",
        "report_save_json",
        _report_save,
        methods=["POST"],
    )
    print("[保丁] 酒倉存檔 API 已註冊：POST /report_save/<agent>/<path:filename>")
else:
    print("[保丁] 載入失敗：找不到 app")

