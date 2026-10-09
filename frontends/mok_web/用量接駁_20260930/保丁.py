# -*- coding: utf-8 -*-
"""
用量接駁補丁 v1.2 (2026-10-03, author: indexPage)
=================================================
症狀：頂部 Token 與會員中心「本月已用」對 admin 永遠顯示 0。
      （admin 不計費 → member.db monthly_used 不會更新；plans.admin.monthly_tokens=1 也是假值。）

修正（只補 admin/root，其餘會員行為完全不動，避免影響既有計費顯示）：
      真實來源 = ~/.mok/.memory/chat_history.db 的 token_usage 表（本月、依 user_id 加總）。
      1) /api/member/header  → admin/root 的 used 改為真實本月用量
      2) /member 會員中心     → admin/root 的 monthly_used 改為真實本月用量

為什麼 v1.1 沒生效：
      保丁載入器（frontends/mok_web/保丁.py）以 importlib.util.module_from_spec + exec_module
      載入補丁，**並未把補丁模組註冊進 sys.modules**；v1.1 的 _find_patch_module 走 sys.modules
      掃描，因此永遠回 None，hook 從未掛上（後端 used 一直是 0）。
v1.2 改由 app.view_functions[<endpoint>].__globals__ 直達補丁模組命名空間，穩定可達。

停用：目錄改名前面加底線（_）後重啟即可。
"""
import os
import sys
import sqlite3
import time

main = sys.modules.get("__main__")
app = getattr(main, "app", None)

_HOME = os.path.expanduser("~/.mok")
TOKEN_DB = os.path.join(_HOME, ".memory", "chat_history.db")
_UNLIMITED_PLANS = ("admin", "root")


def _month():
    return time.strftime("%Y-%m")


def month_used(user_id):
    """本月真實 token 用量（token_usage 表；user_id＝會員帳號 / 訪客 id）。"""
    if not user_id:
        return 0
    try:
        with sqlite3.connect("file:%s?mode=ro" % TOKEN_DB, uri=True, timeout=10) as c:
            row = c.execute(
                "SELECT COALESCE(SUM(total_tokens),0) FROM token_usage "
                "WHERE user_id=? AND strftime('%Y-%m', datetime(timestamp,'unixepoch','localtime'))=?",
                (str(user_id), _month())).fetchone()
        return int(row[0] or 0)
    except Exception as e:
        try:
            print("[usage-hook] month_used(%s) error: %s" % (user_id, e))
        except Exception:
            pass
        return 0


def _patch_ns_of(endpoint):
    """由已註冊的 Flask view 函式取得它所屬補丁模組的 globals（v1.2 唯一可靠取得法）。"""
    try:
        fn = app.view_functions.get(endpoint)
    except Exception:
        fn = None
    return getattr(fn, "__globals__", None)


# ---------- 1) /api/member/header：admin/root 的 used 補上真實用量 ----------
_ns = _patch_ns_of("api_member_header")

if _ns is not None and callable(_ns.get("_payload")):
    _orig_payload = _ns["_payload"]

    def _payload_admin_used():
        try:
            d = dict(_orig_payload() or {})
        except Exception as e:
            print("[usage-hook] orig payload error: %s" % e)
            return {}
        try:
            plan = str(d.get("plan") or "").lower()
            who = d.get("identity") or d.get("username") or d.get("temp_id") or ""
            if d.get("logged_in") and plan in _UNLIMITED_PLANS and who:
                used = month_used(who)
                d["used"] = used
                d["unlimited"] = True
                d["monthly_quota"] = None
                d["remaining"] = None
                d["usage_source"] = "token_usage@" + _month()
        except Exception as e:
            print("[usage-hook] header hook error: %s" % e)
        return d

    _ns["_payload"] = _payload_admin_used
    print("[usage-hook] OK header (admin used) hooked")
else:
    print("[usage-hook] WARN member header patch not found")


# ---------- 2) /member 會員中心：admin/root 的 monthly_used 補上真實用量 ----------
_ns2 = _patch_ns_of("member_center")

if _ns2 is not None and callable(_ns2.get("_get_user")):
    _orig_get_user = _ns2["_get_user"]

    def _get_user_admin_used(username):
        u = _orig_get_user(username)
        try:
            if isinstance(u, dict) and str(u.get("plan") or "").lower() in _UNLIMITED_PLANS:
                u["monthly_used"] = month_used(u.get("username") or username)
        except Exception as e:
            print("[usage-hook] member hook error: %s" % e)
        return u

    _ns2["_get_user"] = _get_user_admin_used
    print("[usage-hook] OK member center (admin used) hooked")
else:
    print("[usage-hook] WARN member patch not found")
