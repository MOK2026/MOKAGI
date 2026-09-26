# -*- coding: utf-8 -*-
"""
機密閘補丁 v1（止血版） 2026-09-25
====================================
載入：由 mok_web/保丁.py 載入器自動掃描載入（目錄名 ｚ 開頭，排序最後、優先級最高）。
機制：只加一道 app.before_request，不改任何核心函數、不覆蓋任何路由。

【背景】2026-09-24 診斷：檔案類 API 完全沒有身分檢查，任何通過 Cloudflare 的人
（含會員/訪客）都能：
    GET  /api/room_tree?agent=領妹&path=videos/h3_live_20260924/server_h3x  → 列機密檔名
    GET  /api/file/.mok/env.env                                            → 讀光 API Key
    POST /api/repair/exec {"command":"id"}                                 → 遠端執行命令(RCE)

【本版規則】
  1. admin（session['member_user'] in ADMIN_USERNAMES）→ 全部放行。
  2. 非 admin 且路徑命中「受保護端點」→ 403，不回任何資料。
  3. 非 admin 且路徑命中「機密黑名單」→ 403（不分端點，第二層保險）。

【已知副作用（v1 止血妥協）】
  會員在自己工作區的檔案瀏覽（/api/tree、/api/room_tree、/api/file、
  /api/repair/*）會被擋；聊天、列表、扣款等既有流程不受影響。
  v2 會改為「按方案白名單：只可讀自己方案內 agent 的房間，且永久排除
  soul/ logs/ videos/ *.db/ env.env」。

【停用】目錄改名加底線開頭（例：_ｚｚ機密閘_20260925）→ 重啟即恢復原狀。
"""
import os
import sys

main = sys.modules.get('__main__')
app = getattr(main, 'app', None)

_raw = (os.environ.get('ADMIN_USERNAMES') or getattr(main, 'ADMIN_USERNAMES', '') or 'admin')
ADMIN_USERS = set(x.strip() for x in str(_raw).split(',') if x.strip())

# 1) 受保護端點（精確、或後接 / 或 _）
PROTECTED = (
    '/api/file', '/api/raw', '/api/tree', '/api/room_tree',
    '/api/repair', '/api/save_file', '/api/create_file', '/api/delete_file',
    '/api/mkdir', '/api/upload', '/api/backup',
)

# 2) 機密黑名單（URL 內出現即拒絕；不分端點）
SECRET_HINTS = (
    'env.env', '.env', '/soul/', '/logs/', '/videos/', 'browser_profile',
    'id_rsa', '/.git/', 'member.db', '.db',
)


def _is_admin():
    try:
        from flask import session
        who = session.get('member_user')
        return bool(who) and str(who) in ADMIN_USERS
    except Exception:
        return False


def _hit(name, path):
    return path == name or path.startswith(name + '/') or path.startswith(name + '_')


def _looks_secret(path):
    p = (path or '').lower()
    for s in SECRET_HINTS:
        if s in p:
            return True
    return False


def _guard():
    if app is None:
        return None
    try:
        from flask import request, jsonify
    except Exception:
        return None
    try:
        path = request.path or ''
    except Exception:
        return None
    if not path.startswith('/api'):
        return None
    if _is_admin():
        return None
    if any(_hit(p, path) for p in PROTECTED) or _looks_secret(path):
        return jsonify({'success': False,
                        'error': 'forbidden: admin only（機密資源已封鎖）'}), 403
    return None


try:
    if app is not None:
        app.before_request(_guard)
        print('[機密閘v1] 掛載完成 protected=%d secret=%d admin=%s'
              % (len(PROTECTED), len(SECRET_HINTS), sorted(ADMIN_USERS)), flush=True)
    else:
        print('[機密閘v1] 載入失敗：找不到 main.app', flush=True)
except Exception as _e:
    print('[機密閘v1] 掛載錯誤: %r' % (_e,), flush=True)
