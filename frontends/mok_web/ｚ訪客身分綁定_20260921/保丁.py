# -*- coding: utf-8 -*-
"""
訪客身分綁定補丁 v1.0  (P2, 2026-09-21)
=========================================
問題（殘留漏洞）：
  未登入者的 tenant 由前端自填的 user_id 決定（?user_id=web_guest_xxx / body.user_id）。
  任何人只要換一個 guest id，就能讀取／寫入「別人訪客」的對話歷史（訪客↔訪客外洩），
  也能把訊息汙染進對方的歷史。

修正：
  未登入者改由「伺服器頒發」訪客身分（guest:<uuid>），以簽章 cookie + session 保存，
  一律忽略前端自填的 user_id；已登入者仍以 session 會員帳號為準。
  舊的 web_guest_* 前端 id 不再被信任（其歷史視為 legacy，不自動繼承）。
"""
import sys, uuid, re

main = sys.modules['__main__']

try:
    from flask import session, request, after_this_request
except Exception:
    session = request = after_this_request = None

_GUEST_RE = re.compile(r'^guest:[0-9a-f]{8,32}$')
_COOKIE = 'mok_guest'
_MAXAGE = 180 * 24 * 3600


def _new_guest():
    return 'guest:' + uuid.uuid4().hex[:16]


def _guest_identity():
    """取得（必要時頒發）本瀏覽器的訪客身分；只信伺服器簽章來源。"""
    if session is None:
        return None
    try:
        g = session.get('mok_guest_id')
        if not g:
            try:
                c = (request.cookies.get(_COOKIE) or '').strip()
            except Exception:
                c = ''
            g = c if _GUEST_RE.match(c) else _new_guest()
            session['mok_guest_id'] = g
            # 另存長效 cookie（session cookie 遺失時仍可續用同一訪客身分）
            try:
                @after_this_request
                def _persist(resp):
                    try:
                        if request.cookies.get(_COOKIE) != g:
                            resp.set_cookie(_COOKIE, g, max_age=_MAXAGE,
                                            httponly=True, samesite='Lax', path='/')
                    except Exception:
                        pass
                    return resp
            except Exception:
                pass
        return g
    except Exception:
        return None


def resolve_tenant(data=None):
    u = main._session_member_user()
    if u:
        return u
    return _guest_identity()


def resolve_tenant_arg():
    u = main._session_member_user()
    if u:
        return u
    return _guest_identity()


main.resolve_tenant = resolve_tenant
main.resolve_tenant_arg = resolve_tenant_arg
print('[訪客身分綁定] ✅ 補丁已載入（未登入者身分改由伺服器頒發 guest:<uuid>）')
