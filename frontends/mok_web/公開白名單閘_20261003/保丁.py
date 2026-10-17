# -*- coding: utf-8 -*-
"""
公開白名單閘 v1  (2026-10-03)  作者：API平台工程師
v2 patch (2026-10-11 SEO侍女)：未登入訪客的「未知路徑」改回 404（不再 302/403）。
====================================================
載入：mok_web/保丁.py 載入器自動掃描（目錄名 8 個 ｚ 開頭，載入順序最後）。

【目的】
未登入訪客「只可瀏覽公開白名單頁面」，其餘任何頁面一律要求登入（導向 /login）。
＝首頁與右側「📡 頻道 ▾」(index.html → id=channelBtn) 內的貼文頁可公開；
  其餘（例：/skill/進化/…、/report/影片女/短劇王/…、/backup、/ASCII …）一律要登入。

【設計】
  1) 只註冊一道 app.before_request（最後註冊＝最後執行）。
  2) 前面的守門若已放行，本閘在最後把「不在公開白名單」的訪客請求擋下：
       HTML 導覽 → 302 /login?next=<原路徑>
       其餘(XHR/API/非html) → 403 JSON
  3) 已登入會員/管理員：不受本閘影響（交由既有各閘依方案/身分處理）。
  4) 白名單來源（優先序）：
       甲) member.db settings 表 key=public_pages（JSON 陣列字串）
       乙) 內建 DEFAULT_PUBLIC（首頁 + 目前 channelBtn 的 4 個頻道頁）
  5) 白名單語法：以半形斜線結尾＝目錄前綴；其餘＝精確路徑。

停用：把目錄改名加底線開頭後重啟即恢復原狀。
"""
import os
import json
import time
import sqlite3
import sys

main = sys.modules.get('__main__')
app = getattr(main, 'app', None)

_PATCH_DIR = os.path.dirname(os.path.abspath(__file__))


def _find_member_db():
    parent = os.path.dirname(_PATCH_DIR)
    try:
        for d in sorted(os.listdir(parent)):
            p = os.path.join(parent, d, 'member.db')
            if os.path.exists(p):
                return p
    except Exception:
        pass
    return os.path.join(parent, 'member.db')


MEMBER_DB = _find_member_db()

DEFAULT_PUBLIC = [
    '/',
    '/index.html',
    '/會議模式',
    '/game/',
    '/report/春/mokagi社交平台2/',
    '/report/病毒引擎/直播房3/',
]

ALWAYS_PREFIX = (
    '/api',
    '/static',
    '/socket.io',
    '/login',
    '/register',
    '/logout',
    '/plans',
    '/member',
    '/favicon',
    '/admin',
    '/_test',
)


# ===== v2 (2026-10-11 SEO侍女)：未知路徑 -> 404 =====
_HTML_ROOT = os.path.expanduser('~/.mok/html')
_PUBLIC_ROOT = '/var/www/mok-public'

SAFE_FILES = (
    '/robots.txt', '/sitemap.xml', '/llms.txt', '/llms-full.txt',
    '/humans.txt', '/feed.xml', '/og.png', '/.well-known/security.txt',
)


def _exists(np):
    try:
        return _exists_inner(np)
    except Exception:
        return True


def _exists_inner(np):
    if np in ('/', ''):
        return True
    if np in SAFE_FILES:
        return True
    page = np.strip('/')
    if not page:
        return True
    try:
        from flask import request as _rq
        adapter = app.url_map.bind(_rq.host or 'mokagi.com')
        rule, args = adapter.match(np, method=_rq.method)
        if 'page' not in args:
            return True
    except Exception:
        pass
    roots = [r for r in (_HTML_ROOT, _PUBLIC_ROOT) if r]
    for r in roots:
        for c in (os.path.join(r, page),
                  os.path.join(r, page + '.html'),
                  os.path.join(r, page, 'index.html')):
            try:
                if os.path.exists(c):
                    return True
            except Exception:
                pass
    first = page.split('/')[0]
    if first:
        try:
            if os.path.exists(os.path.join(_HTML_ROOT, first)):
                return True
        except Exception:
            pass
    return False



def _norm(p):
    from urllib.parse import unquote as _uq
    p = _uq(p or '').strip().split('?')[0].split('#')[0]
    return ('/' + p.lstrip('/')) if p else ''


def _always(path):
    for pre in ALWAYS_PREFIX:
        if path == pre or path.startswith(pre + '/'):
            return True
    return False


_cache = {'t': 0.0, 'v': None}
_TTL = 20.0


def _entries():
    now = time.time()
    if _cache['v'] is not None and (now - _cache['t']) < _TTL:
        return _cache['v']
    entries = None
    try:
        c = sqlite3.connect(MEMBER_DB, timeout=3)
        try:
            row = c.execute("SELECT value FROM settings WHERE key='public_pages'").fetchone()
        finally:
            c.close()
        if row and row[0]:
            v = json.loads(row[0])
            if isinstance(v, list) and v:
                entries = [str(x) for x in v if str(x).strip()]
    except Exception:
        entries = None
    if not entries:
        entries = list(DEFAULT_PUBLIC)
    _cache['t'] = now
    _cache['v'] = entries
    return entries


def _is_public(np, entries):
    for raw in entries:
        e = _norm(raw)
        if not e:
            continue
        if e == '/':
            if np in ('/', '/index.html'):
                return True
            continue
        if e.endswith('/'):
            base = e.rstrip('/')
            if np == base or np.startswith(e):
                return True
        else:
            if np == e:
                return True
            parent = e.rsplit('/', 1)[0] + '/'
            if parent != '/' and np.startswith(parent):
                return True
    return False


_NOTFOUND_HTML = """<!doctype html>
<html lang="zh-HK">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex,follow">
<title>404 &middot; 找不到頁面 | MOKAGI</title>
<style>
:root{color-scheme:dark}*{box-sizing:border-box}
body{margin:0;min-height:100vh;display:flex;align-items:center;justify-content:center;
background:radial-gradient(1200px 600px at 50% -10%,#1b2440 0%,#0b0f1a 60%,#05070d 100%);
color:#e8ecf8;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI","PingFang HK","Noto Sans CJK TC",sans-serif}
.wrap{max-width:640px;padding:48px 28px;text-align:center}
.code{font-size:96px;font-weight:800;letter-spacing:4px;line-height:1;
background:linear-gradient(90deg,#5ea0ff,#a86bff);-webkit-background-clip:text;background-clip:text;color:transparent}
h1{font-size:22px;margin:18px 0 8px}p{color:#9aa6c4;line-height:1.7;margin:8px 0}
a.btn{display:inline-block;margin:18px 8px 0;padding:12px 22px;border-radius:999px;text-decoration:none;
background:linear-gradient(90deg,#3b6ff0,#8b5cf6);color:#fff;font-weight:600}
a.link{color:#9ec1ff;text-decoration:none;margin:0 8px}.small{margin-top:26px;font-size:12px;color:#65708f}
</style>
</head>
<body>
<div class="wrap">
  <div class="code">404</div>
  <h1>找不到這個頁面 &middot; Page not found</h1>
  <p>您要找的頁面不存在、已被移動，或網址有誤。</p>
  <p>The page you requested does not exist or has been moved.</p>
  <a class="btn" href="https://mokagi.com/">回到首頁 &middot; Home</a>
  <div class="small">
    <a class="link" href="https://mokagi.com/faq/">FAQ</a>
    <a class="link" href="https://mokagi.com/ai-agent/">AI Agent</a>
    <a class="link" href="https://mokagi.com/about/">關於 MOKAGI</a>
  </div>
  <div class="small">MOKAGI &middot; 自託管多 Agent AI 作業系統 &middot; <a class="link" href="https://mokagi.com/">mokagi.com</a></div>
</div>
</body>
</html>"""

def _guard():
    try:
        from flask import request, session, redirect, jsonify
    except Exception:
        return None
    if request.method not in ('GET', 'HEAD'):
        return None
    try:
        if session.get('member_user'):
            return None
    except Exception:
        pass
    path = request.path or '/'
    np = _norm(path)
    if _always(np):
        return None
    if _is_public(np, _entries()):
        return None
    acc = (request.headers.get('Accept') or '').lower()
    mode = (request.headers.get('Sec-Fetch-Mode') or '').lower()
    is_nav = ('text/html' in acc) or (mode == 'navigate') or (acc == '')
    if not _exists(np):
        if is_nav:
            return _NOTFOUND_HTML, 404, {'Content-Type': 'text/html; charset=utf-8'}
        return jsonify({'success': False, 'error': 'not found'}), 404
    if is_nav:
        from urllib.parse import quote
        return redirect('/login?next=' + quote(path))
    return jsonify({'success': False, 'error': 'forbidden: 請先登入'}), 403


if app is not None:
    try:
        app.before_request(_guard)
        print('[公開白名單閘] 已掛載 db=%s public=%d 條' % (MEMBER_DB, len(_entries())), flush=True)
    except Exception as _e:
        print('[公開白名單閘] 掛載失敗: %r' % (_e,), flush=True)
else:
    print('[公開白名單閘] 載入失敗：找不到 main.app', flush=True)
