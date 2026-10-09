# -*- coding: utf-8 -*-
"""
履歷生成網 × mokagi 會員系統 接駁補丁 v1（2026-10-07 靜）
=========================================================
由 mok_web 的補丁載入器自動掃描載入（未列於 載入順序.txt → 接在清單最後，
晚於「會員系統_202608311340」載入，故可取得會員記帳函式）。

【目的】
1. 讓「履歷生成網」（/report/履歷之神/履歷生成網/index.html）在 mok_web 同源下運作，
   前端 fetch 改打相對前綴，不需跨埠 iframe、不依賴 cloudflared 隧道。
2. 接回 mokagi 會員系統：
   - 需登入會員（session.member_user）才能生成。
   - 「生成精美履歷」每次扣 150000 token（走會員系統補丁的 _deduct_tokens 記帳）。
   - 餘額不足直接拒絕，不呼叫上游（不浪費算力）。
3. 純新增路由 + 反向代理（127.0.0.1:8080）；不改 core、不改前端服務主程式、
   不改會員系統補丁。前端只加一段 API 前綴與餘額顯示。

【路由】（全部掛在 mok_web 下）
  · GET（/resume/api/themes）        → 主題 / 字型 / 社交平台（免登入，公開瀏覽用）
  · POST（/resume/api/parse_upload） → 上傳履歷解析（需登入；不扣費）
  · POST（/resume/api/generate）     → 生成履歷（需登入；扣 150000 token）
  · GET（/resume/workspace/<path>）  → 產物下載（pdf / html / md / png）
  · GET（/resume/api/account）       → 目前登入者 / 餘額 / 本次費用（前端顯示用）

【停用】目錄改名前面加底線（_）後重啟 mok_web 即可。
"""
import sys
import json
import urllib.request
import urllib.error; from urllib.parse import quote

main = sys.modules.get('__main__')
app = getattr(main, 'app', None)

UPSTREAM = 'http://127.0.0.1:8080'
COST = 150000
COST_LABEL = '生成精美履歷'

if app is not None:
    from flask import request, Response, jsonify, session

    # ---------- 取得會員系統補丁的記帳函式 ----------
    # 載入器以 importlib 載入補丁且未註冊 sys.modules，故由已註冊 view 的 __globals__
    # 直達該補丁模組命名空間（同「用量接駁」補丁的可靠取法），並在必要時延後重試。
    _CACHE = {}

    def _member_ns():
        if _CACHE.get('ns') is not None:
            return _CACHE['ns']
        ns = None
        try:
            fn = app.view_functions.get('member_center')
            ns = getattr(fn, '__globals__', None)
        except Exception:
            ns = None
        if ns is not None:
            _CACHE['ns'] = ns
        return ns

    def _mfn(name):
        ns = _member_ns() or {}
        fn = ns.get(name)
        return fn if callable(fn) else None

    if not (_mfn('_get_user') and _mfn('_deduct_tokens')):
        print('[履歷生成網代理] 注意：尚未取得會員記帳函式，將於請求時再試', flush=True)

    def _current_user():
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

    def _balance_of(username):
        """回傳目前餘額（int）或 None（查不到）。"""
        try:
            get_user = _mfn('_get_user')
            if not get_user:
                return None
            u = get_user(username)
            if not u:
                return None
            ensure = _mfn('_ensure_month')
            if ensure:
                u = ensure(u)
            return int(u.get('balance_tokens') or 0)
        except Exception as e:
            print('[履歷生成網代理] 讀餘額失敗: %r' % (e,), flush=True)
            return None

    def _plan_of(username):
        try:
            get_user = _mfn('_get_user')
            u = get_user(username) if get_user else None
            return (u or {}).get('plan')
        except Exception:
            return None

    # ---------- 反向代理 ----------
    def _proxy(path, timeout=600):
        url = UPSTREAM + quote(path, safe="/%")
        if request.query_string:
            url += '?' + request.query_string.decode('utf-8', 'replace')
        body = request.get_data() if request.method in ('POST', 'PUT', 'DELETE', 'PATCH') else None
        headers = {}
        ct = request.headers.get('Content-Type')
        if ct:
            headers['Content-Type'] = ct
        req = urllib.request.Request(url, data=body, method=request.method, headers=headers)
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                return Response(raw, status=r.status,
                                content_type=r.headers.get('Content-Type') or 'application/octet-stream')
        except urllib.error.HTTPError as e:
            return Response(e.read(), status=e.code,
                            content_type=e.headers.get('Content-Type') or 'text/plain; charset=utf-8')
        except Exception as e:
            msg = json.dumps({'success': False,
                              'error': '履歷生成服務未啟動或連線失敗：%s'
                                       '（請確認 pm2 的 resume-web 在線）' % e}, ensure_ascii=False)
            return Response(msg, status=502, content_type='application/json; charset=utf-8')

    def _opt204():
        r = Response('', status=204)
        r.headers['Access-Control-Allow-Origin'] = '*'
        r.headers['Access-Control-Allow-Methods'] = 'GET, POST, OPTIONS'
        r.headers['Access-Control-Allow-Headers'] = 'Content-Type'
        return r

    app.add_url_rule("/resume/index.html", "resume_page", lambda: _proxy("/", timeout=30), methods=["GET"])
    @app.route('/resume/api/themes', methods=['GET', 'OPTIONS'])
    def resume_themes():
        if request.method == 'OPTIONS':
            return _opt204()
        return _proxy('/api/themes', timeout=30)

    @app.route('/resume/api/parse_upload', methods=['POST', 'OPTIONS'])
    def resume_parse_upload():
        if request.method == 'OPTIONS':
            return _opt204()
        if not _current_user():
            return jsonify({'success': False, 'need_login': True,
                            'error': '請先登入 mokagi 會員再上傳履歷。'}), 401
        return _proxy('/api/parse_upload', timeout=180)

    @app.route('/resume/api/generate', methods=['POST', 'OPTIONS'])
    def resume_generate():
        if request.method == 'OPTIONS':
            return _opt204()
        user = _current_user()
        if not user:
            return jsonify({'success': False, 'need_login': True,
                            'error': '請先登入 mokagi 會員，%s 需 %d tokens。'
                                     % (COST_LABEL, COST)}), 401
        before = _balance_of(user)
        if before is None:
            return jsonify({'success': False, 'need_login': True,
                            'error': '找不到會員資料，請重新登入。'}), 401
        if before < COST:
            return jsonify({'success': False, 'insufficient': True,
                            'cost': COST, 'balance_tokens': before,
                            'error': 'Token 不足：%s 需 %d，目前剩 %d。請先充值或升級方案。'
                                     % (COST_LABEL, COST, before)}), 402

        resp = _proxy('/api/generate', timeout=600)
        if getattr(resp, 'status_code', 500) >= 400:
            return resp

        # 生成成功 → 記帳（一次性）
        charged = False
        try:
            deduct = _mfn('_deduct_tokens')
            if deduct:
                deduct(user, COST)
                charged = True
            else:
                print('[履歷生成網代理] 注意：無 _deduct_tokens，本次未扣費', flush=True)
        except Exception as e:
            print('[履歷生成網代理] 扣費失敗: %r' % (e,), flush=True)

        after = _balance_of(user)
        try:
            data = json.loads(resp.get_data(as_text=True))
            if isinstance(data, dict):
                data['charged_tokens'] = COST if charged else 0
                data['balance_tokens'] = after
                data['cost'] = COST
            out = jsonify(data)
            out.status_code = resp.status_code
            return out
        except Exception:
            return resp

    @app.route('/resume/api/account', methods=['GET', 'OPTIONS'])
    def resume_account():
        if request.method == 'OPTIONS':
            return _opt204()
        user = _current_user()
        if not user:
            return jsonify({'logged_in': False, 'cost': COST, 'plan': None,
                            'balance_tokens': None, 'affordable': False})
        bal = _balance_of(user)
        return jsonify({'logged_in': True, 'username': user, 'plan': _plan_of(user),
                        'balance_tokens': bal, 'cost': COST,
                        'affordable': (bal is not None and bal >= COST)})

    @app.route('/resume/workspace/<path:sub>', methods=['GET'])
    def resume_workspace(sub):
        return _proxy('/workspace/' + sub, timeout=300)

    print('[履歷生成網代理] 已啟用：登入 + 每次生成扣 %d tokens；反向代理至 127.0.0.1:8080' % COST,
          flush=True)
else:
    print('[履歷生成網代理] 找不到 app，略過', flush=True)
