# -*- coding: utf-8 -*-
# 聊天入口 v1  (2026-10-08)  作者：API平台工程師
# ====================================================
# 載入：mok_web/保丁.py 載入器自動掃描（本目錄未列於 載入順序.txt，依目錄名接在最後）。
#
# 目的：把聊天頁收斂成單一正式入口 /chat。
#     路徑 /chat       ：200，渲染 index.html（正式入口，URL 不變）
#     路徑 /index.html ：301 永久轉址到 /chat
#     路徑 /           ：301 永久轉址到 /chat
#
# 手法：最小侵入、不改核心 mok_web.py。
#     兩條靜態 URL 規則（/chat、/index.html）優先於 catch-all，子路徑不受影響；
#     根路徑沿用本專案既有慣例，改寫 app 既有的 index 檢視函式。
#
# 影響：改 Python 路由，須由主人重啟 mok_web 才生效。
# 回滾：目錄名前加底線（_聊天入口_20261008）後重啟即恢復原狀。
import sys

from flask import redirect, render_template

main = sys.modules.get('__main__')
app = getattr(main, 'app', None)

CHAT_PATH = '/chat'


def _chat_page():
    return render_template('index.html')


def _redirect_to_chat():
    return redirect(CHAT_PATH, code=301)


if app is not None:
    try:
        app.add_url_rule(CHAT_PATH, 'chat_entry', _chat_page, methods=['GET', 'HEAD'])
        app.add_url_rule('/index.html', 'chat_entry_index_html',
                         _redirect_to_chat, methods=['GET', 'HEAD'])
        if 'index' in app.view_functions:
            app.view_functions['index'] = _redirect_to_chat
            _root = 'index 檢視函式已改寫'
        else:
            _root = '找不到 endpoint index，根路徑維持原樣'
        print('[聊天入口] OK：/chat=200、/index.html 轉址、/ 轉址 (%s)' % _root, flush=True)
    except Exception as _e:
        import traceback
        traceback.print_exc()
        print('[聊天入口] 掛載失敗: %r' % (_e,), flush=True)
else:
    print('[聊天入口] 載入失敗：找不到 main.app', flush=True)
