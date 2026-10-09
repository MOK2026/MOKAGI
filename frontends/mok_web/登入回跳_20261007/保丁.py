# -*- coding: utf-8 -*-
"""
登入回跳補丁 v1（2026-10-07 賺錢王）
=====================================
【問題】
  權限閘擋下訪客時會導向 /login?next=<原路徑>（公開白名單閘 2026-10-03 已寫入 next），
  但 /login 登入成功後一律 redirect('/member')，next 被丟棄
  → 訪客登入後卡在會員中心，回不到本來要去的頁（例：/report/賺錢王/傳單王/index.html）。
  會員中心頁本身也沒有任何「返回／回主頁」連結。

【做法（完全不動既有補丁，全部用 after_request 收尾）】
  1) /login：登入成功（3xx 且 Location 指向 /member）→ 改寫成 next（僅接受站內相對路徑，
     並轉成 ASCII-safe 百分號編碼，避免中文標頭出錯）。
     仍是登入頁（密碼錯／兩步驗證）→ 表單注入 hidden next + 提示，重試仍帶得住 next。
  2) /member：頂層瀏覽（非 iframe）時，在會員卡最前面注入「🏠 回主頁（對話）／⬅ 返回上一頁」。
  3) next 白名單化：'/' 開頭、非 '//'、不含反斜線與換行、長度 <= 512。

【停用】目錄名前加底線（_登入回跳_20261007）後重啟 mok_web。
【生效】需重啟 mok_web（屬主人手動動作）。
"""
import re
import sys
import html as _html
from urllib.parse import quote as _quote

main = sys.modules.get('__main__')
app = getattr(main, 'app', None)

if app is not None:
    from flask import request

    _MAXLEN = 512
    _SAFE = "/%:?=&#@!$'()*+,;~_-."

    def _clean_next(v):
        if not v:
            return None
        try:
            v = str(v).strip()
        except Exception:
            return None
        if not v.startswith('/') or v.startswith('//'):
            return None
        if '\\' in v or '\r' in v or '\n' in v or '\t' in v:
            return None
        if len(v) > _MAXLEN:
            return None
        return v

    def _ascii_path(p):
        """已編碼的 %XX 原樣保留，只把中文等非 ASCII 轉成百分號編碼。"""
        try:
            return _quote(p, safe=_SAFE)
        except Exception:
            return p

    def _inject_form_next(body, nxt):
        """第一個表單前加提示、表單內加 hidden next。"""
        m = re.search(r'<form\b[^>]*>', body)
        if not m:
            return body
        hint = ('<p style="color:#8b93b5;font-size:12px;line-height:1.6;margin:0 0 4px">'
                '登入成功後會自動返回：<b>%s</b></p>' % _html.escape(nxt))
        hidden = '<input type="hidden" name="next" value="%s">' % _html.escape(nxt, quote=True)
        return body[:m.start()] + hint + m.group(0) + hidden + body[m.end():]

    _BACKBAR = (
        '<div id="mokBackBar" style="display:flex;gap:8px;flex-wrap:wrap;margin:0 0 2px">'
        '<a href="/" style="display:inline-block;padding:7px 14px;border-radius:9px;background:#7aa2ff;'
        'color:#0f1220;font-size:13px;font-weight:700;text-decoration:none">🏠 回主頁（對話）</a>'
        '<a href="#" onclick="history.back();return false" style="display:inline-block;padding:7px 14px;'
        'border-radius:9px;background:transparent;border:1px solid #2b3357;color:#aab2d6;font-size:13px;'
        'text-decoration:none">⬅ 返回上一頁</a>'
        '</div>')

    @app.after_request
    def _mok_login_next(resp):
        try:
            path = request.path or '/'
            code = getattr(resp, 'status_code', 200)
            ctype = resp.headers.get('Content-Type') or ''

            if path == '/login':
                nxt = _clean_next(request.values.get('next'))
                if nxt:
                    loc = resp.headers.get('Location') or ''
                    if 300 <= code < 400 and loc.rstrip('/').endswith('/member'):
                        resp.headers['Location'] = _ascii_path(nxt)
                    elif code == 200 and 'text/html' in ctype:
                        body = resp.get_data(as_text=True)
                        if body and 'name="next"' not in body and '</form>' in body:
                            resp.set_data(_inject_form_next(body, nxt))
                return resp

            if path == '/member':
                dest = (request.headers.get('Sec-Fetch-Dest') or '').lower()
                if dest == 'document' and code == 200 and 'text/html' in ctype:
                    body = resp.get_data(as_text=True)
                    if body and 'mokBackBar' not in body and '<div class="mc">' in body:
                        resp.set_data(body.replace('<div class="mc">',
                                                   '<div class="mc">' + _BACKBAR, 1))
                return resp
        except Exception as e:
            try:
                print('[登入回跳] 注入失敗（不影響登入流程）:', e, flush=True)
            except Exception:
                pass
        return resp

    print('[登入回跳] OK：/login 支援 next 回跳；/member 已加返回鍵', flush=True)
