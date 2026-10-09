# -*- coding: utf-8 -*-
"""
建立 Agent 方案限額補丁 v1  (2026-10-05)  作者：mokagi說明
=========================================================
載入：mok_web/保丁.py 載入器自動掃描（本目錄未列於「載入順序.txt」→ 接在最後載入，
      故為 create_agent 的最外層包裝，可看到內層 [身分核心] 的 owner 記錄結果）。

【主人規格 2026-10-05】
  - pro / vip 皆可用「第 1 個 agent」免費。
  - pro  ：只可建 1 個（第 2 個起擋下）。
  - vip  ：可建無限個；第 2 個起，每建一個扣 150000 tokens（餘額不足則擋下）。
  - free ：不可建立。
  - admin：不受限、不扣費。

【做法】不改核心、不覆蓋既有補丁，只做一件事：
      包裝 main.create_agent / app.view_functions['create_agent']：
        1) 建立前：查 member.db 的身分（plan / is_admin）＋ agent_owners 已擁有數
           → 依上表決定「放行 / 擋下（回 403 JSON，前端口徑 data.message）」。
        2) 建立成功（HTTP 200 且 status=='ok'）後，才把 150000 記入 ledger 並扣
           users.balance_tokens（ledger 可讓會員在「我的帳本」看到扣費流水）。

【停用】目錄改名前面加底線（_）→ 下次 mok_web 載入即失效。
"""
import os
import sys
import time
import glob
import sqlite3

main = sys.modules.get('__main__')
app = getattr(main, 'app', None)

_PATCH_DIR = os.path.dirname(os.path.abspath(__file__))

# ==== 方案限額設定（改這裡即可調參，不必動核心） ======================
FREE_MAX_AGENTS = 0            # free：不可建立
PRO_MAX_AGENTS = 1             # pro：最多 1 個（首個免費）
VIP_MAX_AGENTS = -1            # vip：-1 = 無限
VIP_EXTRA_COST = 150000        # vip：第 2 個起，每個扣 150000 tokens
# =====================================================================


def _find_member_db():
    parent = os.path.dirname(_PATCH_DIR)
    for pat in (os.path.join(parent, '*', 'member.db'),
                os.path.join(parent, 'member.db')):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return os.path.join(parent, 'member.db')


MEMBER_DB = _find_member_db()


def _conn():
    c = sqlite3.connect(MEMBER_DB, timeout=10)
    c.row_factory = sqlite3.Row
    return c


def _session_user():
    try:
        from flask import session
        return session.get('member_user')
    except Exception:
        return None


def _user_row(username):
    if not username:
        return None
    c = None
    try:
        c = _conn()
        r = c.execute('SELECT username, plan, is_admin, balance_tokens '
                      'FROM users WHERE username=?', (username,)).fetchone()
        return dict(r) if r else None
    except Exception as e:
        print('[建agent限額] 讀 users 失敗: %r' % (e,), flush=True)
        return None
    finally:
        if c is not None:
            c.close()


def _owned_count(username):
    c = None
    try:
        c = _conn()
        r = c.execute('SELECT COUNT(*) AS n FROM agent_owners WHERE owner=?',
                      (str(username),)).fetchone()
        return int(r['n'] or 0)
    except Exception as e:
        print('[建agent限額] 讀 agent_owners 失敗: %r' % (e,), flush=True)
        return 0
    finally:
        if c is not None:
            c.close()


def _charge(username, tokens, meta=''):
    """扣 balance_tokens 並記 ledger（負向流水）。回傳 (ok, why, balance_after)。"""
    tokens = int(tokens or 0)
    if not username or tokens <= 0:
        return True, 'skip', None
    c = None
    try:
        c = _conn()
        row = c.execute('SELECT balance_tokens FROM users WHERE username=?',
                        (username,)).fetchone()
        if row is None:
            return False, 'no_such_user', None
        bal = int(row['balance_tokens'] or 0)
        if bal < tokens:
            return False, 'insufficient', bal
        c.execute('UPDATE users SET balance_tokens = balance_tokens - ? WHERE username=?',
                  (tokens, username))
        newbal = int(c.execute('SELECT balance_tokens FROM users WHERE username=?',
                               (username,)).fetchone()['balance_tokens'])
        try:
            c.execute('INSERT INTO ledger (username, delta_tokens, balance_after, '
                      'reason, order_no, idem_key, meta, ts) VALUES (?,?,?,?,?,?,?,?)',
                      (username, -tokens, newbal, 'create_agent', '',
                       None, str(meta or ''), time.time()))
        except Exception as le:
            print('[建agent限額] 寫 ledger 失敗（仍已扣款）: %r' % (le,), flush=True)
        c.commit()
        return True, 'ok', newbal
    except Exception as e:
        print('[建agent限額] 扣款失敗: %r' % (e,), flush=True)
        return False, 'error', None
    finally:
        if c is not None:
            c.close()


def _block(msg, code=403):
    return ({'status': 'error', 'message': msg}, code)


def _decide():
    """回傳 (allow, need_tokens, msg, who)。"""
    uname = _session_user()
    if not uname:
        return False, 0, '請先登入會員後再建立 Agent。', None
    u = _user_row(uname)
    if u is None:
        return False, 0, '找不到會員資料，請重新登入。', uname
    if u.get('is_admin'):
        return True, 0, '', uname
    plan = str(u.get('plan') or 'free').lower()
    owned = _owned_count(uname)
    if plan == 'free':
        return False, 0, ('免費方案（free）不可建立 Agent，請升級為 Pro 或 VIP。'
                          '（目前可建立上限 0 個）'), uname
    if plan == 'pro':
        if owned >= PRO_MAX_AGENTS:
            return False, 0, ('Pro 方案最多只能建立 %d 個 Agent（已建立 %d 個）。'
                              '如需更多，請升級為 VIP。'
                              % (PRO_MAX_AGENTS, owned)), uname
        return True, 0, '', uname
    if plan == 'vip':
        if owned < 1:
            return True, 0, '', uname          # 首個免費
        bal = int(u.get('balance_tokens') or 0)
        if bal < VIP_EXTRA_COST:
            return False, 0, ('VIP 方案第 2 個起的 Agent 每個需 %s tokens，'
                              '目前餘額 %s，不足請先充值。'
                              % (format(VIP_EXTRA_COST, ','), format(bal, ','))), uname
        return True, VIP_EXTRA_COST, '', uname
    return False, 0, '你的方案（%s）不可建立 Agent，請升級方案。' % plan, uname


# ---------------- 包裝 create_agent ----------------
_orig_create_agent = getattr(main, 'create_agent', None)


def _limited_create_agent(*a, **k):
    allow, need, msg, who = _decide()
    if not allow:
        print('[建agent限額] 擋下 user=%s：%s' % (who, msg), flush=True)
        return _block(msg)
    resp = _orig_create_agent(*a, **k) if _orig_create_agent else (
        {'status': 'error', 'message': 'unavailable'}, 500)
    body, code = (resp if isinstance(resp, tuple) else (resp, 200))
    if need > 0 and code == 200 and isinstance(body, dict) and body.get('status') == 'ok':
        name = ''
        try:
            from flask import request
            name = ((request.get_json(silent=True) or {}).get('name') or '').strip()
        except Exception:
            pass
        ok, why, bal = _charge(who, need, meta=('agent=' + name if name else ''))
        if ok:
            if isinstance(body, dict):
                body = dict(body)
                body['charged_tokens'] = need
                body['balance_after'] = bal
                body['message'] = (str(body.get('message') or '')
                                   + '（VIP 第 2 個起已扣 %s tokens，餘額 %s）'
                                   % (format(need, ','), format(bal, ',')))
            print('[建agent限額] %s 建立 agent 扣 %d tokens，餘額 %s'
                  % (who, need, bal), flush=True)
        else:
            print('[建agent限額] ⚠️ %s 扣款失敗(%s)，agent 已建立但未扣到款'
                  % (who, why), flush=True)
    return body if not isinstance(resp, tuple) else (body, code)


if _orig_create_agent is not None:
    try:
        main.create_agent = _limited_create_agent
        if app and 'create_agent' in getattr(app, 'view_functions', {}):
            app.view_functions['create_agent'] = _limited_create_agent
        print('[建agent限額] create_agent 已包裝（pro/vip/free 限額 + vip 加購扣費）',
              flush=True)
    except Exception as e:
        print('[建agent限額] 包裝失敗: %r' % (e,), flush=True)
else:
    print('[建agent限額] 載入失敗：找不到 main.create_agent', flush=True)
