# -*- coding: utf-8 -*-
"""[保丁] 進化面板：把 skill/進化/ 整個目錄用靜態檔服務（僅限本機）

2026-09-12 由凜建立。原因：首頁右下「進化」按鈕的 iframe 需要以相對路徑
載入面板資料，但核心 catch-all 只 render html，其他副檔名一律 404，
故在此補上一條靜態路由（不改核心）。
停用：把本目錄改名（前面加 _）即可。
"""
import os
import sys

main = sys.modules.get('__main__')
app = getattr(main, 'app', None)
if app is None:
    raise RuntimeError('進化面板補丁：找不到核心 app')

from flask import request, send_from_directory

D = chr(46)
IDX = 'index' + D + 'html'
_home = getattr(main, 'MOKAGI_home', 'mok') or 'mok'
EVO_DIR = os.path.realpath(os.path.expanduser('~/.%s/skill/進化' % _home))
_LOCAL = ('127.0.0.1', '::1', 'localhost')
_R1 = '/skill/進化/'
_R2 = _R1 + 'PTH'


# 2026-09-13 放寬（方案A）：外網瀏覽器也可開啟進化面板。
# 要改回僅限本機：把 ALLOW_REMOTE 設成 False（或把本補丁目錄改名，前面加 _ 停用）。
ALLOW_REMOTE = True


def _is_local():
    if ALLOW_REMOTE:
        return True
    ra = (getattr(request, 'remote_addr', '') or '').strip()
    if not (ra in _LOCAL or ra.startswith('127.')):
        return False
    h = (getattr(request, 'host', '') or '').lower()
    if h.startswith('['):
        h = h[1:].split(']')[0]
    else:
        h = h.split(':')[0]
    return h in ('127.0.0.1', 'localhost', '::1')


def _serve_evo(filename=IDX):
    if not _is_local():
        return '進化面板僅限本機瀏覽器存取', 403
    fn = (filename or IDX).lstrip('/')
    full = os.path.realpath(os.path.join(EVO_DIR, fn))
    if not full.startswith(EVO_DIR + os.sep) or not os.path.isfile(full):
        return 'Not found', 404
    return send_from_directory(EVO_DIR, fn)


app.add_url_rule(_R1, 'evo_panel_index', _serve_evo)
app.add_url_rule(_R2.replace('PTH', '<' + 'path:filename' + '>'), 'evo_panel_file', _serve_evo)
print('[進化面板] 已掛載 /skill/進化/ （ALLOW_REMOTE=%s）' % ALLOW_REMOTE)
