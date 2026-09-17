# -*- coding: utf-8 -*-
"""
[保丁] 老人陪伴房 公網代理 (2026-09-08 05:33)
背景: 老人照顧/老人陪伴房 web 經 mok_web /report 對外公網開放時, 頁面 fetch 的 /api/*
      只存在於房後台 127.0.0.1:8940, mok_web 原本冇代理 => 公網開出嚟「冇數據」。
做法 (純新增, 唔郁核心):
  1) 長者房 overlay / family 監護台用到嘅 /api/* 資料端點 同 /tts/* 音檔 -> 反向代理 8940;
     (唔代理 /api/chat: 嗰個係 mok_web 核心對話端點, 會撞。)
  2) 覆蓋 /api/llm_bridge/<agent>: agent=老人照顧 -> 轉發 8941 (小暖橋); 其他照舊。
"""
import sys, json, urllib.request, urllib.error, urllib.parse

_main = sys.modules.get("__main__")
app = getattr(_main, "app", None)

ELDER_UP        = "http://127.0.0.1:8940"
ELDER_BRIDGE_UP = "http://127.0.0.1:8941"
ELDER_AGENT     = "老人照顧"


def _proxy_upstream(upstream, timeout=150):
    from flask import request, Response
    url = upstream
    if request.query_string:
        url += "?" + request.query_string.decode("utf-8")
    body = request.get_data() if request.method in ("POST", "PUT", "DELETE") else None
    headers = {"Content-Type": request.content_type or "application/json"}
    req = urllib.request.Request(url, data=body, method=request.method, headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read()
        resp = Response(raw, status=r.status,
                        content_type=r.headers.get("Content-Type") or "application/json; charset=utf-8")
    except urllib.error.HTTPError as e:
        resp = Response(e.read(), status=e.code,
                        content_type=e.headers.get("Content-Type") or "text/plain; charset=utf-8")
    except Exception as e:
        resp = Response(json.dumps({"error": "elder proxy error: " + str(e)}, ensure_ascii=False),
                        status=502, content_type="application/json; charset=utf-8")
    resp.headers["Access-Control-Allow-Origin"] = "*"
    resp.headers["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    resp.headers["Access-Control-Allow-Headers"] = "Content-Type"
    return resp


def _reg(path, methods):
    if app is None:
        return
    def _mk():
        def _view():
            return _proxy_upstream(ELDER_UP + path)
        _view.__name__ = "elder_proxy_" + str(abs(hash(path + "".join(methods))))
        return _view
    view = _mk()
    try:
        app.add_url_rule(path, view.__name__, view, methods=methods)
    except Exception as e:
        print("[老人陪伴房補丁] skip route", path, "->", e, flush=True)


for _p in ["/api/checkin/status", "/api/checkin/status_all", "/api/alerts",
           "/api/incidents", "/api/outbox", "/api/room/latest", "/api/orders",
           "/api/tts", "/api/items"]:
    _reg(_p, ["GET"])

for _p in ["/api/checkin", "/api/heartbeat", "/api/incident", "/api/outbox/ack",
           "/api/proactive", "/api/room", "/api/order"]:
    _reg(_p, ["POST"])

if app is not None:
    def _tts_view(fname):
        return _proxy_upstream(ELDER_UP + "/tts/" + urllib.parse.quote(fname), timeout=60)
    _tts_view.__name__ = "elder_proxy_tts_audio"
    try:
        app.add_url_rule("/tts/<path:fname>", _tts_view.__name__, _tts_view, methods=["GET"])
    except Exception as e:
        print("[老人陪伴房補丁] skip /tts/<f> ->", e, flush=True)


_orig_llm = (app.view_functions.get("api_llm_bridge_proxy") if app else None)

def _llm_bridge_proxy_with_elder(agent):
    from flask import request, Response, jsonify
    if agent == ELDER_AGENT:
        if request.method == "OPTIONS":
            _r = Response("")
            _r.headers["Access-Control-Allow-Origin"] = "*"
            _r.headers["Access-Control-Allow-Methods"] = "POST, OPTIONS"
            _r.headers["Access-Control-Allow-Headers"] = "Content-Type"
            return _r
        return _proxy_upstream(ELDER_BRIDGE_UP + "/?agent=" + urllib.parse.quote(ELDER_AGENT))
    if _orig_llm:
        return _orig_llm(agent)
    return jsonify({"text": "", "error": "bridge 未初始化"}), 503

if app is not None:
    try:
        app.view_functions["api_llm_bridge_proxy"] = _llm_bridge_proxy_with_elder
        print("[老人陪伴房補丁] /api/llm_bridge 已加入 agent=老人照顧 -> 8941", flush=True)
    except Exception as e:
        print("[老人陪伴房補丁] override llm_bridge 失敗 ->", e, flush=True)

# -*- coding: utf-8 -*-
"""test patch"""
