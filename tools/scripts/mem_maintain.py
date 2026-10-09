#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""記憶定期維護（供 cron 呼叫）
  - 衰減遺忘：刪除分數過低的「動態」記憶（static/manual/舊資料一律不動）
  - 可選：刷新各 agent 的 soul/user.md 雙段（--profiles）
用法：
  python3 mem_maintain.py                       # dry-run（只報告）
  python3 mem_maintain.py --apply               # 真的刪除
  python3 mem_maintain.py --apply --profiles    # 同時刷新所有 agent profile
  python3 mem_maintain.py --apply --wal         # 同時修剪向量庫 WAL（embeddings_queue）
"""
import sys, os
import io

MOK = os.path.expanduser("~/.mok")
sys.path.insert(0, os.path.join(MOK, "tools"))
sys.path.insert(0, os.path.join(MOK, "core"))


def main():
    apply_now = "--apply" in sys.argv
    do_profiles = ("--profiles" in sys.argv) or ("--all-profiles" in sys.argv)
    from config import load_agent_config
    import memory as M

    res = M.gc_all_agents(dry_run=not apply_now, agent_config={})
    scan = sum(v.get("scanned", 0) for v in res.values() if isinstance(v, dict))
    dele = sum(v.get("deleted", 0) for v in res.values() if isinstance(v, dict))
    print(f"[mem] gc agents={len(res)} scanned={scan} deleted={dele} apply={apply_now}")

    # 預設：--apply 時一併修剪 WAL，避免 embeddings_queue 再次膨脹（--no-wal 可關）
    do_wal = ("--wal" in sys.argv) or (apply_now and ("--no-wal" not in sys.argv))
    if do_wal:
        wal_trim(apply_now)

    if do_profiles:
        # 記憶分庫（2026-10-03 by 凜）：profile 已按對話者(uid)分庫，
        # 改為刷新「已存在分庫檔」的每個對話者；不再依賴 soul/user.md 的舊動態標記。
        n = 0
        skipped = 0
        refs = 0
        users_root = os.path.join(MOK, "user")
        for ag in M._all_agent_names():
            p = os.path.join(MOK, "agent", ag, "soul", "user.md")
            if not os.path.exists(p):
                continue
            uids = []
            try:
                if os.path.isdir(users_root):
                    for uid in sorted(os.listdir(users_root)):
                        if os.path.exists(os.path.join(users_root, uid, "profile", ag + ".md")):
                            uids.append(uid)
            except Exception:
                pass
            if not uids:
                skipped += 1
                continue
            for uid in uids:
                try:
                    M.update_user_profile(ag, uid=uid, agent_config=load_agent_config(ag), dry_run=not apply_now)
                    n += 1
                    refs += 1
                except Exception as e:
                    print(f"[mem] profile {ag}/{uid} ERR {e}")
        print(f"[mem] profiles refreshed: {n} skipped(no per-user profile): {skipped} apply={apply_now}")


def wal_trim(apply_now):
    """修剪 ChromaDB 的 embeddings_queue（WAL）中「各 collection 已消費」的舊紀錄。
    規則與 chromadb 內建 purge_log 一致：刪除 seq_id < min(該 collection 各 segment 的 max_seq_id) 的列；
    max_seq_id<=0（尚未消費）的 collection 一律保留，確保不會弄丟向量。
    """
    import sqlite3
    db = os.path.join(MOK, ".chroma_data", "chroma.sqlite3")
    if not os.path.exists(db):
        print("[wal] 找不到 chroma.sqlite3")
        return
    size_before = os.path.getsize(db) / 1048576.0
    con = sqlite3.connect(db, timeout=120)
    cur = con.cursor()
    cur.execute("PRAGMA busy_timeout=120000")
    seg = {}
    for sid, cid in cur.execute("SELECT id, collection FROM segments"):
        seg.setdefault(cid, []).append(sid)
    mx = dict(cur.execute("SELECT segment_id, seq_id FROM max_seq_id"))
    topics = cur.execute("SELECT topic, count(*) FROM embeddings_queue GROUP BY topic").fetchall()
    pending = 0
    for topic, _cnt in topics:
        cid = topic.rsplit("/", 1)[-1]
        sns = seg.get(cid, [])
        if not sns:
            continue
        mn = min(mx.get(s, -1) for s in sns)
        if mn <= 0:
            continue
        pending += cur.execute(
            "SELECT count(*) FROM embeddings_queue WHERE topic=? AND seq_id<?", (topic, mn)
        ).fetchone()[0]
    if apply_now and pending:
        for topic, _cnt in topics:
            cid = topic.rsplit("/", 1)[-1]
            sns = seg.get(cid, [])
            if not sns:
                continue
            mn = min(mx.get(s, -1) for s in sns)
            if mn <= 0:
                continue
            cur.execute("DELETE FROM embeddings_queue WHERE topic=? AND seq_id<?", (topic, mn))
        con.commit()
    con.close()
    size_after = os.path.getsize(db) / 1048576.0
    print(f"[wal] topics={len(topics)} 可清={pending} 已清={pending if apply_now else 0} "
          f"db={size_before:.0f}MB->{size_after:.0f}MB apply={apply_now}")


if __name__ == "__main__":
    main()
