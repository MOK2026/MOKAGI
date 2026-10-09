# -*- coding: utf-8 -*-
"""owner_auth.py - owner / admin 判定的唯一真相  (2026-10-03 by Ling)

規則 (fail-closed):
  1) 空 / 訪客 id (web_guest_* / guest:*)  -> False
  2) member.db users 查得到該帳號           -> users.is_admin (唯一真相)
  3) 純數字 (Telegram chat_id)             -> 只認該 agent 的 ADMIN_CHAT_ID
  4) 其餘                                  -> False
任何例外 -> False。
"""
import os
import glob
import sqlite3


def _home():
    return os.path.expanduser("~/.mok")


def member_db_path():
    env = os.environ.get("MOK_MEMBER_DB")
    if env and os.path.exists(env):
        return env
    cands = glob.glob(os.path.join(_home(), "frontends", "mok_web", "會員系統_*", "member.db"))
    if not cands:
        return None
    cands.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return cands[0]


def is_guest_id(v):
    s = str(v or "")
    return s.startswith("web_guest_") or s.startswith("guest:")


def _connect(path):
    con = sqlite3.connect(path, timeout=5)
    con.row_factory = sqlite3.Row
    return con


def is_admin_identity(user_id, agent_config=None):
    u = str(user_id or "").strip()
    if not u:
        return False
    if is_guest_id(u):
        return False
    db = member_db_path()
    if db:
        try:
            con = _connect(db)
            try:
                r = con.execute("SELECT is_admin FROM users WHERE username=?", (u,)).fetchone()
                if r is not None:
                    return bool(r["is_admin"])
            finally:
                con.close()
        except Exception:
            pass
    if u.isdigit():
        cfg = agent_config or {}
        try:
            admin_chat = str(cfg.get("ADMIN_CHAT_ID", "") or "").strip()
        except Exception:
            admin_chat = ""
        if admin_chat and admin_chat.isdigit() and admin_chat != "0":
            return u == admin_chat
    return False


def admin_ids(db=None):
    out = set()
    path = db or member_db_path()
    if not path:
        return out
    try:
        con = _connect(path)
        try:
            for r in con.execute("SELECT id FROM users WHERE is_admin=1"):
                out.add(int(r[0]))
        finally:
            con.close()
    except Exception:
        pass
    return out


def owner_id_of(username, db=None):
    if not username:
        return None
    path = db or member_db_path()
    if not path:
        return None
    try:
        con = _connect(path)
        try:
            r = con.execute("SELECT id FROM users WHERE username=?", (str(username),)).fetchone()
            return int(r[0]) if r else None
        finally:
            con.close()
    except Exception:
        return None
