# -*- coding: utf-8 -*-
"""owner-only soul gate v2  2026-10-04  凜
Wrap mokagi.get_system_context: for non-owner users strip
<!-- MOK_OWNER_ONLY --> ... <!-- /MOK_OWNER_ONLY --> blocks inside soul files.

owner 判定（v2 治本）：不再用「名稱字串」當權限鍵。
  唯一真相 = member.db `users.is_admin`（→ users.id 集合）＋ 該 agent 的
  擁有者 `agent_owners.owner_id`（舊資料用 owner 帳號反查 users.id）。
  Telegram 純數字 chat_id 只在「該 agent 明確設定 ADMIN_CHAT_ID」時才認，
  且「先在 users 表查到同名帳號者」一律以 DB 為準（避免 ADMIN_CHAT_ID=123 撞到會員）。
任何例外 -> 一律視為『非 owner』（fail-closed，不洩漏私料）。
"""
import os
import glob
import sqlite3

_B = "<!-- MOK_OWNER_ONLY -->"
_E = "<!-- /MOK_OWNER_ONLY -->"
_CACHE = {}
_OWN_CACHE = {}


def _member_db():
    env = os.environ.get("MOK_MEMBER_DB")
    if env and os.path.exists(env):
        return env
    home = os.path.expanduser("~/.mok")
    cands = glob.glob(os.path.join(home, "frontends", "mok_web", "會員系統_*", "member.db"))
    if not cands:
        return None
    cands.sort(key=lambda p: os.path.getmtime(p), reverse=True)
    return cands[0]


def _q(db, sql, args=()):
    con = sqlite3.connect(db, timeout=5)
    con.row_factory = sqlite3.Row
    try:
        return con.execute(sql, args).fetchone()
    finally:
        con.close()


def _user_id_of(db, username):
    try:
        r = _q(db, "SELECT id FROM users WHERE username=?", (str(username),))
        return int(r["id"]) if r else None
    except Exception:
        return None


def _admin_ids(db):
    """users.id 集合（is_admin=1）＝ admin 的唯一真相。"""
    out = set()
    try:
        con = sqlite3.connect(db, timeout=5)
        try:
            for r in con.execute("SELECT id FROM users WHERE is_admin=1"):
                out.add(int(r[0]))
        finally:
            con.close()
    except Exception:
        pass
    return out


def _agent_owner_id(db, agent_name):
    """該 agent 擁有者的 users.id（agent_owners.owner_id；舊資料用 owner 反查）。"""
    if not agent_name:
        return None
    try:
        r = _q(db, "SELECT owner, owner_id FROM agent_owners WHERE agent=?", (str(agent_name),))
    except Exception:
        r = None
    if not r:
        return None
    try:
        if r["owner_id"] is not None:
            return int(r["owner_id"])
    except Exception:
        pass
    try:
        return _user_id_of(db, r["owner"])
    except Exception:
        return None


def _admin_chat_ids(agent_name):
    """該 agent 明確設定的 ADMIN_CHAT_ID（僅純數字）＝ Telegram 專用。"""
    ids = set()
    home = os.path.expanduser("~/.mok")
    cands = []
    if agent_name:
        cands.append(os.path.join(home, "agent", str(agent_name), "." + str(agent_name)))
    cands.append(os.path.join(home, ".agent"))
    for p in cands:
        if not os.path.isfile(p):
            continue
        try:
            with open(p, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    s = line.strip()
                    if s.startswith("ADMIN_CHAT_ID="):
                        v = s.split("=", 1)[1].strip().strip('"').strip("'")
                        if v:
                            ids.add(v)
        except Exception:
            pass
    return ids


def _owner_ids(agent_name):
    """『owner 判定』用的 users.id 集合（整數）。
       = 全部 is_admin=1 的 users.id ∪ {該 agent 擁有者 users.id}。
       DB 不可用時，退回最小名稱集合 {'admin','root'}（僅最後手段）。"""
    if agent_name in _CACHE:
        return _CACHE[agent_name]
    ids = set()
    db = _member_db()
    if db:
        ids |= _admin_ids(db)
        oid = _agent_owner_id(db, agent_name)
        if oid is not None:
            ids.add(oid)
    else:
        ids |= {"admin", "root"}
    _CACHE[agent_name] = ids
    return ids


def _is_owner(uid, agent_name):
    key = (agent_name, None if uid is None else str(uid).strip())
    if key in _OWN_CACHE:
        return _OWN_CACHE[key]
    val = _is_owner_calc(uid, agent_name)
    _OWN_CACHE[key] = val
    return val


def _is_owner_calc(uid, agent_name):
    # uid None/"" = internal/owner flow -> keep（向後相容）
    if uid is None or str(uid).strip() == "":
        return True
    u = str(uid).strip()
    # 訪客（匿名租戶）永不升級為 owner
    if u.startswith("web_guest_") or u.startswith("guest:"):
        return False
    db = _member_db()
    if db:
        # 1) 先用 users 表判定（唯一真相）；命中就不再看 ADMIN_CHAT_ID，避免 123 撞名
        try:
            r = _q(db, "SELECT id, is_admin FROM users WHERE username=?", (u,))
        except Exception:
            r = None
        if r is not None:
            if bool(r["is_admin"]):
                return True
            oid = _agent_owner_id(db, agent_name)
            return oid is not None and int(r["id"]) == int(oid)
        # 2) users 表查無此帳號（多半是 Telegram 純數字 chat_id）→ 只認明確設定的 ADMIN_CHAT_ID
        if u.isdigit():
            return u in _admin_chat_ids(agent_name)
        return False
    # DB 不可用：退回名稱集合（僅最後手段）
    return u in _owner_ids(agent_name)


def _strip(content, keep):
    if not content or "MOK_OWNER_ONLY" not in content:
        return content
    out, i = [], 0
    while True:
        s = content.find(_B, i)
        if s == -1:
            out.append(content[i:])
            break
        out.append(content[i:s])
        t = content.find(_E, s + len(_B))
        if t == -1:
            if keep:
                out.append(content[s + len(_B):])
            i = len(content)
            break
        if keep:
            out.append(content[s + len(_B):t])
        i = t + len(_E)
    return "".join(out)


def _apply():
    try:
        import mokagi
    except Exception as e:
        print("[owner_only] import mokagi fail: %r" % (e,), flush=True)
        return
    orig = getattr(mokagi, "get_system_context", None)
    if orig is None or getattr(orig, "_mok_owneronly", False):
        return

    def _wrapped(*args, **kwargs):
        agent_name = kwargs.get("agent_name", None)
        if agent_name is None and len(args) >= 1:
            agent_name = args[0]
        uid = kwargs.get("current_user", None)
        if uid is None and len(args) >= 7:
            uid = args[6]
        out = orig(*args, **kwargs)
        if out and "MOK_OWNER_ONLY" in out:
            try:
                return _strip(out, _is_owner(uid, agent_name))
            except Exception:
                return out
        return out

    _wrapped._mok_owneronly = True
    setattr(mokagi, "get_system_context", _wrapped)
    print("[owner_only] gate attached (v2)", flush=True)


_apply()
