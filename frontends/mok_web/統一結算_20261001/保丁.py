# -*- coding: utf-8 -*-
"""
統一結算補丁 v1.0 (2026-10-01, author: 凜)
==========================================
目標：讓「整個 .mok 的所有對話前端」共用同一套會員 token 即時結算。

原理（為什麼掛在 process_message 就全覆蓋）：
  實測所有對話入口最終都匯流到 mokagi.process_message(user_id=...)
    · 主網頁 index.html      → POST /api/chat(SSE) → _start_sse_chat_session → _sse_bg_worker
    · /api/chat/stream/<id>  → 同一條 SSE 佇列
    · 會議模式 / game        → Socket.IO 'chat_message' → handle_chat_message → _bg_worker
    · 直播房3 / 老人陪伴房   → 其實也是 POST /api/chat（bridge 打 127.0.0.1:5000/api/chat）
  而 process_message 是在「背景執行緒」被呼叫的 —— 那裡沒有 Flask request context，
  所以靠 request.path 判斷 is_web 的舊寫法永遠 False（扣款從未發生）。
  本補丁改用「process_message 收到的 user_id」認身分 → 不依賴 request context、一次覆蓋全部前端。

規則（依 2026-10-01 主人確認的計費規格）：
  1. 身分 = user_id 對應到 member.db 的會員帳號；admin/root/system 不計費。
  2. 開場先檢查餘額：balance_tokens <= 0 → 本回合擋下（不執行）。
  3. 可為負：本回合照跑完，跑完才量差額扣款（balance 可變負）。
  4. 跨月重置沿用會員系統補丁的 _ensure_month（每月回補方案額度，此為正確機制，不改）。
  5. 扣款沿用會員系統補丁的 _deduct_tokens（同步更新 monthly_used 與 usage 表）。

停用：目錄改名前面加底線（_）後重啟 mok_web 即可。
"""
import os
import sys
import time
import glob
import sqlite3
import functools

main = sys.modules.get("__main__")
import mokagi as _mokagi

_HOME = os.path.expanduser("~/.mok")
TOKEN_DB = os.path.join(_HOME, ".memory", "chat_history.db")
_UNLIMITED = ("admin", "root")
_SKIP_USERS = ("system", "admin", "root")


def _find_member_db():
    for pat in (
        os.path.join(_HOME, "frontends", "mok_web", "會員系統_*", "member.db"),
        os.path.join(_HOME, "frontends", "mok_web", "*", "member.db"),
    ):
        hits = sorted(glob.glob(pat))
        if hits:
            return hits[-1]
    return None


MEMBER_DB = _find_member_db()
print("[統一結算] member.db =", MEMBER_DB)


def _ro(path):
    return sqlite3.connect("file:%s?mode=ro" % path, uri=True, timeout=10)


def _member(username):
    if not username or not MEMBER_DB:
        return None
    try:
        with _ro(MEMBER_DB) as c:
            c.row_factory = sqlite3.Row
            r = c.execute("SELECT * FROM users WHERE username=?", (str(username),)).fetchone()
            return dict(r) if r else None
    except Exception:
        return None


def billing_user(user_id):
    """回傳「應計費的會員帳號」；不需計費（訪客/機器人/admin）回 None。"""
    if not user_id:
        return None
    u = str(user_id)
    if u in _SKIP_USERS:
        return None
    rec = _member(u)
    if not rec:
        return None
    if str(rec.get("plan") or "").lower() in _UNLIMITED:
        return None
    if int(rec.get("disabled") or 0):
        return None
    return u


def _sum_since(user_id, ts):
    """本回合真實消耗＝token_usage 表在 ts 之後、該 user_id 的 total_tokens 總和。"""
    try:
        with _ro(TOKEN_DB) as c:
            row = c.execute(
                "SELECT COALESCE(SUM(total_tokens),0) FROM token_usage WHERE user_id=? AND timestamp>=?",
                (str(user_id), ts)).fetchone()
            return int(row[0] or 0)
    except Exception:
        return 0


def _mem_mod():
    for name, m in list(sys.modules.items()):
        if name.startswith("mokweb_patch_") and hasattr(m, "_check_balance") and hasattr(m, "_deduct_tokens"):
            return m
    return None


_MEM = _mem_mod()
print("[統一結算] 會員系統模組 =", getattr(_MEM, "__name__", None))


def _check_balance(username):
    if _MEM is not None:
        return _MEM._check_balance(username)
    rec = _member(username)
    if not rec:
        return False, "用戶不存在"
    if int(rec.get("balance_tokens") or 0) <= 0:
        return False, "餘額不足（剩 %s tokens）。請充值或升級方案。" % rec.get("balance_tokens")
    return True, ""


def _sync_plan(username):
    """扣款後校正等級（唯一真相在會員系統補丁；找不到時退化為等效 SQL）。"""
    if not username:
        return
    if _MEM is not None and hasattr(_MEM, 'sync_plan'):
        try:
            return _MEM.sync_plan(username)
        except Exception as e:
            print("[統一結算] sync_plan 失敗:", e)
            return
    try:
        with sqlite3.connect(MEMBER_DB, timeout=15) as c:
            c.execute("UPDATE users SET vip_eligible=0 WHERE balance_tokens<=0 "
                      "AND plan IN ('pro','vip') AND username=? AND username NOT IN "
                      "(SELECT username FROM member_vip_grandfather)", (username,))
            c.execute("UPDATE users SET plan=CASE WHEN EXISTS (SELECT 1 FROM member_vip_grandfather g "
                      "WHERE g.username=users.username) THEN 'vip' "
                      "WHEN vip_eligible=1 AND balance_tokens>0 THEN 'vip' ELSE 'pro' END "
                      "WHERE plan IN ('pro','vip') AND username=?", (username,))
            c.commit()
    except Exception as e:
        print("[統一結算] 等級同步失敗:", e)


def _deduct(username, tokens):
    if tokens <= 0:
        return
    if _MEM is not None:
        _MEM._deduct_tokens(username, tokens)
        _sync_plan(username)          # ★ 2026-10-07 凜：扣款後餘額 <= 0 → 降回 pro
        return
    try:
        with sqlite3.connect(MEMBER_DB, timeout=15) as c:
            c.execute(
                "UPDATE users SET balance_tokens=balance_tokens-?, monthly_used=monthly_used+? WHERE username=?",
                (tokens, tokens, username))
            c.commit()
    except Exception as e:
        print("[統一結算] 扣款失敗:", e)
    _sync_plan(username)              # ★ 2026-10-07 凜：退化路徑也同步等級


async def _emit_block(stream_callback, agent_name, msg):
    if not stream_callback:
        return
    import inspect
    for ev in ({"type": "reply", "content": msg, "agent": agent_name},
               {"type": "done", "final_reply": msg, "agent": agent_name}):
        try:
            r = stream_callback(ev)
            if inspect.isawaitable(r):
                await r
        except Exception as e:
            print("[統一結算] emit 失敗:", e)


def _wrap(mod):
    if mod is None:
        return False
    orig = getattr(mod, "process_message", None)
    if orig is None or getattr(orig, "_mok_unified_settle", False):
        return False

    @functools.wraps(orig)
    async def _settled_process_message(user_id, text, stream_callback=None, agent_name=None,
                                       agent_config=None, **kw):
        who = billing_user(user_id)
        if who:
            ok, err = _check_balance(who)
            if not ok:
                msg = "⛔ " + str(err)
                print("[統一結算] 擋下 %s：%s" % (who, err))
                if stream_callback:
                    await _emit_block(stream_callback, agent_name, msg)
                    return msg
                raise PermissionError(msg)
        t0 = time.time()
        try:
            return await orig(user_id, text, stream_callback=stream_callback,
                              agent_name=agent_name, agent_config=agent_config, **kw)
        finally:
            if who:
                used = _sum_since(user_id, t0)
                if used > 0:
                    _deduct(who, used)
                    print("[統一結算] %s 本回合 -%d tokens" % (who, used))

    _settled_process_message._mok_unified_settle = True
    setattr(mod, "process_message", _settled_process_message)
    return True


_wrap(_mokagi)
try:
    if hasattr(main, "process_message"):
        _wrap(main)
except Exception:
    pass
print("[統一結算] 掛載完成（user_id 為準的即時結算）")
