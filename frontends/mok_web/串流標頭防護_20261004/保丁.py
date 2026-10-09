# -*- coding: utf-8 -*-
"""
串流標頭防護補丁 v1  (2026-10-04 by 凜)
========================================
對應故障：日誌中反覆出現
    werkzeug/serving.py  write() before start_response
    AssertionError: write() before start_response
成因：某些請求的 WSGI 應用「回傳了 iterable，卻沒有呼叫 start_response」，
      werkzeug 開發伺服器（socketio.run → run_simple）在 execute() 收尾時
      呼叫 write(b"") 就會踩到這個斷言，整條連線以例外收場（日誌被洗版）。

本補丁「不改核心」：在 Flask app 外層包一層最小 WSGI 防護——
  保證 start_response 一定會被呼叫；若應用真的沒呼叫，就補一個明確的 500，
  並（節流）印出觸發的請求方法/路徑，方便日後定位真凶。

停用：把本目錄改名（前面加 _）即停用（載入器會跳過）。
"""
import sys
import time

main = sys.modules.get('__main__')
app = getattr(main, 'app', None)

_ENABLED = app is not None and not getattr(app, '_stream_hdr_guard', False)

_last = {"t": 0.0, "n": 0}


def _note(environ):
    _last["n"] += 1
    now = time.time()
    if now - _last["t"] > 60:
        _last["t"] = now
        try:
            print("[串流標頭防護] 偵測到未呼叫 start_response 的請求：%s %s（累計 %d，已補 500）"
                  % (environ.get("REQUEST_METHOD"), environ.get("PATH_INFO"), _last["n"]), flush=True)
        except Exception:
            pass


if _ENABLED:
    _orig_wsgi = app.wsgi_app
    _BODY = b"Internal Server Error (no start_response)\n"
    _HDR = [("Content-Type", "text/plain; charset=utf-8")]

    def _guarded_wsgi(environ, start_response):
        _called = [False]

        def _sr(status, headers, exc_info=None):
            _called[0] = True
            return start_response(status, headers, exc_info)

        it = _orig_wsgi(environ, _sr)

        if _called[0]:
            return it

        # 應用尚未呼叫 start_response。Flask 一律「先呼叫再回傳」，
        # 走到這裡代表是邊界情況（engineio / werkzeug 等）→ 包一層，保證一定補上。
        def _gen():
            for chunk in it:
                if not _called[0]:
                    _note(environ)
                    _sr("500 Internal Server Error", _HDR)
                    yield _BODY
                yield chunk
            if not _called[0]:
                _note(environ)
                _sr("500 Internal Server Error", _HDR)
                yield _BODY

        return _gen()

    app.wsgi_app = _guarded_wsgi
    try:
        app._stream_hdr_guard = True
    except Exception:
        pass
    print("[串流標頭防護] 已掛載：保證 start_response 一定被呼叫", flush=True)
