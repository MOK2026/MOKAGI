# -*- coding: utf-8 -*-
"""socketio輪詢_20261004 保丁（P1-9 2026-10-04 by 稚）
====================================================
目的：前端 Socket.IO 只走 polling，後端也把「每次 websocket 升級都觸發一次
      假 500 + werkzeug traceback」堵死（雙保險）。

做法（不改核心）：
  1) 伺服端把 engineio 可用的 transport 收斂成僅 polling。
  2) 在 Flask app 外層墊一層最小 WSGI 防護：凡是帶 socket.io 前綴、且帶 websocket
     升級標頭的請求，直接回一個「已呼叫 start_response」的乾淨 200，不進
     engineio（那些 socket-hijack 寫法正是 werkzeug 假 500 的來源）。

停用：把本目錄改名（前面加 _）即停用；載入器會跳過。
"""
import sys

main = sys.modules.get('__main__')
app = getattr(main, 'app', None)
_ENABLED = app is not None and not getattr(app, '_socketio_polling_only', False)

if _ENABLED:
    # ---- 1) 伺服端只允許 polling ----
    try:
        _sio = getattr(main, 'socketio', None)
        _srv = getattr(_sio, 'server', None)
        if _srv is not None and hasattr(_srv, 'transports'):
            _srv.transports = ['polling']
    except Exception as _e:
        print("[socketio輪詢] 設定伺服端 transports 失敗（略過）：%s" % _e, flush=True)

    # ---- 2) WSGI 層攔 websocket 升級，回乾淨 200 ----
    _orig_wsgi = app.wsgi_app

    def _guarded_wsgi(environ, start_response):
        path = environ.get('PATH_INFO', '') or ''
        upgrade = (environ.get('HTTP_UPGRADE') or '').lower()
        if path.startswith('/socket.io/') and upgrade == 'websocket':
            start_response(
                '200 OK',
                [('Content-Type', 'text/plain; charset=utf-8'),
                 ('Content-Length', '0'),
                 ('Connection', 'close')],
            )
            return [b'']
        return _orig_wsgi(environ, start_response)

    app.wsgi_app = _guarded_wsgi
    try:
        app._socketio_polling_only = True
    except Exception:
        pass
    print("[socketio輪詢] 已掛載：socket.io websocket 升級 -> 乾淨 200（只走 polling）", flush=True)
