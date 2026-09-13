# -*- coding: utf-8 -*-
"""
[保丁] 直播房3 管理後台 公網代理  (2026-09-12)
位置: .mok/frontends/mok_web/直播房3後台公網代理_202609121400/保丁.py

背景:
  直播房3 後台 admin.html 由 jobs/直播房3/_admin.py 服務, 只綁 127.0.0.1:8945。
  公網 (https://64071181.xyz -> mok_web:5000) 開 admin.html 時:
    (1) 頁面相對 fetch 的 /api/* 打到 mok_web  => "Page not found" 404;
    (2) 127.0.0.1:8945 係「瀏覽器自己部機」 => net::ERR_CONNECTION_REFUSED。
  結果後台「冇數據」, 連加主播都 TypeError (reading 'allowed_agents')。

做法 (純新增, 唔郁 mok_web.py):
  將 /liveadmin/<path> 同 /直播房3/<path> 反向代理去 http://127.0.0.1:8945/<path>;
  /liveadmin/ 同 /直播房3/admin.html 都開得到後台。
  admin.html 的 detectAPI 已加自動偵測呢兩個前綴。
"""
import sys, json, urllib.request, urllib.error
from flask import request, Response

_main = sys.modules.get("__main__")
app = getattr(_main, "app", None)

ADMIN_UP = "http://127.0.0.1:8945"
PREFIXES = ["/liveadmin", "/直播房3"]


def _proxy(sub):
    path = "/" + sub if sub else "/"
    url = ADMIN_UP + path
    if request.query_string:
        url += "?" + request.query_string.decode("utf-8", "replace")
    data = request.get_data() if request.method in ("POST", "PUT", "DELETE", "PATCH") else None
    req = urllib.request.Request(url, data=data, method=request.method)
    ct = request.headers.get("Content-Type")
    if ct:
        req.add_header("Content-Type", ct)
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            raw = r.read()
            ctype = r.headers.get("Content-Type") or "application/octet-stream"
            resp = Response(raw, status=200, content_type=ctype)
    except urllib.error.HTTPError as e:
        raw = e.read()
        ctype = e.headers.get("Content-Type") or "text/plain; charset=utf-8"
        resp = Response(raw, status=e.code, content_type=ctype)
    except Exception as e:
        resp = Response(json.dumps({"error": "liveadmin proxy error: " + str(e)}, ensure_ascii=False),
                        status=502, content_type="application/json; charset=utf-8")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["Access-Control-Allow-Origin"] = "*"
    return resp


def _mk_view(pfx):
    def _view(sub=""):
        return _proxy(sub)
    _view.__name__ = "liveadmin_view_" + str(abs(hash(pfx)))
    return _view


if app is not None:
    for _pfx in PREFIXES:
        _tag = str(abs(hash(_pfx)))
        try:
            app.add_url_rule(_pfx + "/<path:sub>", "liveadmin_sub_" + _tag,
                             _mk_view(_pfx),
                             methods=["GET", "POST", "PUT", "DELETE", "PATCH", "OPTIONS"])
            app.add_url_rule(_pfx + "/", "liveadmin_root_" + _tag,
                             _mk_view(_pfx), methods=["GET"])
            print("[直播房3公網代理補丁] 已掛載 " + _pfx + " -> " + ADMIN_UP, flush=True)
        except Exception as e:
            print("[直播房3公網代理補丁] 掛載失敗 " + _pfx + " : " + str(e), flush=True)
