# -*- coding: utf-8 -*-
"""
傳單王 API 反向代理補丁 v1  (2026-10-03)
==========================================
由 mok_web/保丁.py 載入器自動掃描載入（目錄名 9 個 ｚ 開頭 → 最後載入）。

【目的】
1. 讓「傳單王」前端（/report/賺錢王/傳單王/index.html，工作區同源）能用
   /skill/傳單王/api/* 呼叫本機後端 127.0.0.1:8788，避開 iframe 跨埠 / 混合內容問題。
2. 把「已登入會員身分」以受信任標頭 X-Mok-User 注入後端。
   → 後端據此做「會員產物隔離」（每位會員的傳單／上傳圖／名單／發信紀錄完全獨立）。
   → 前端無法偽造：此標頭由伺服器端依 session 蓋上，且後端只綁 127.0.0.1。

【停用】把目錄改名前面加底線（_）後重啟 mok_web 即可。
"""
import sys

main = sys.modules.get('__main__')
app = getattr(main, 'app', None)

if app is not None:
    from flask import request, Response, jsonify, session
    import urllib.request
    import urllib.error

    UPSTREAM = 'http://127.0.0.1:8788'

    def _session_user():
        try:
            u = session.get('member_user')
            if u:
                return str(u)
        except Exception:
            pass
        try:
            f = getattr(main, '_session_member_user', None)
            if callable(f):
                u = f()
                if u:
                    return str(u)
        except Exception:
            pass
        return None

    @app.route('/skill/傳單王/api/<path:sub>', methods=['GET', 'POST', 'OPTIONS'])
    def flyerwang_api_proxy(sub):
        if request.method == 'OPTIONS':
            r = Response('', status=204)
            r.headers['Access-Control-Allow-Origin'] = '*'
            r.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
            r.headers['Access-Control-Allow-Headers'] = 'Content-Type'
            return r

        user = _session_user()
        if not user:
            return jsonify({'success': False, 'error': '請先登入會員再使用傳單王。'}), 401

        target = UPSTREAM + '/api/' + sub
        if request.query_string:
            target += '?' + request.query_string.decode('utf-8')
        data = request.get_data() if request.method == 'POST' else None
        req = urllib.request.Request(target, data=data, method=request.method)
        req.add_header('Content-Type', request.headers.get('Content-Type') or 'application/json')
        req.add_header('X-Mok-User', user)
        try:
            with urllib.request.urlopen(req, timeout=300) as resp:
                body = resp.read()
                return Response(body, status=resp.status,
                                content_type=resp.headers.get('Content-Type', 'application/json'))
        except urllib.error.HTTPError as e:
            return Response(e.read(), status=e.code, content_type='application/json')
        except Exception as e:
            return jsonify({'success': False,
                            'error': '傳單王服務未啟動或連線失敗：%s（請執行 jobs/傳單王/run.sh start）' % e}), 502

    print('[傳單王代理] OK /skill/傳單王/api -> 127.0.0.1:8788, member header injected')
