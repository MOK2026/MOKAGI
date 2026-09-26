#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chroma_gc.py — ChromaDB 向量庫瘦身 / 陳舊 WAL 清理工具

問題：ChromaDB 的 embeddings_queue（WAL）會被「未 checkpoint」的寫入撐爆。
      mokagi 的 chroma.sqlite3 曾因此膨脹到 1.8~2.8GB，實際向量只有約 1 萬筆。

做法：
  1) 對「仍有 collection 存在」的 topic，把其 segments 的 max_seq_id 前移
     （等同 checkpoint：這些 WAL 已經落盤到 embeddings/segment 檔），
  2) 刪除已 checkpoint 的 embeddings_queue 列，
  3) 刪除孤兒 topic（collection 已不存在）的殘留列，
  4) 可選 VACUUM 把檔案實際縮小。

用法：
  python3 ~/.mok/tools/chroma_gc.py                 # dry-run，只報告
  python3 ~/.mok/tools/chroma_gc.py --purge         # 實際清理（不 VACUUM）
  python3 ~/.mok/tools/chroma_gc.py --purge --vacuum # 清理並 VACUUM（需要約 2 倍磁碟空間）
  python3 ~/.mok/tools/chroma_gc.py --backup        # 清理前先線上備份到 ~/.mok/backups/_tmp_backups/
"""
import os, sys, time, sqlite3, json

DB = os.environ.get("MOK_CHROMA_DB", "/home/ubuntu/.mok/.chroma_data/chroma.sqlite3")
BK_DIR = "/home/ubuntu/.mok/backups/_tmp_backups"


def human(n):
    n = float(n)
    for u in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024 or u == "TB":
            return f"{n:.1f}{u}"
        n /= 1024


def online_backup():
    os.makedirs(BK_DIR, exist_ok=True)
    dst = os.path.join(BK_DIR, "chroma_" + time.strftime("%Y%m%d_%H%M%S") + ".sqlite3")
    s = sqlite3.connect(DB, timeout=300)
    d = sqlite3.connect(dst)
    s.backup(d)
    d.close()
    s.close()
    return dst


def analyse(c):
    q = c.execute
    size = os.path.getsize(DB) if os.path.exists(DB) else 0
    rows = q("select count(*) from embeddings_queue").fetchone()[0]
    maxseq = q("select max(seq_id) from embeddings_queue").fetchone()[0] or 0
    cols = {r[0]: r[1] for r in q("select id,name from collections")}
    segs = {}
    for sid, cid in q("select id,collection from segments"):
        segs.setdefault(cid, []).append(sid)
    maxmap = {sid: sq for sid, sq in q("select segment_id,seq_id from max_seq_id")}
    topics = q("select topic,count(*) from embeddings_queue group by topic").fetchall()
    orphan_rows = 0
    purgeable = 0
    for topic, cnt in topics:
        cid = topic.rsplit("/", 1)[-1]
        if cid not in cols:
            orphan_rows += cnt
            continue
        sids = segs.get(cid, [])
        mn = min([maxmap.get(s, -1) for s in sids]) if sids else -1
        # checkpoint 前移後，可刪除 <= max_seq_id（現況） 的列
        purgeable += q(
            "select count(*) from embeddings_queue where topic=? and seq_id<=?",
            (topic, maxseq),
        ).fetchone()[0]
    return {
        "db": DB,
        "size": human(size),
        "size_bytes": size,
        "queue_rows": rows,
        "queue_topics": len(topics),
        "collections": len(cols),
        "orphan_collections": sum(1 for t, _ in topics if t.rsplit("/", 1)[-1] not in cols),
        "orphan_rows": orphan_rows,
        "purgeable_rows": purgeable,
    }


def main():
    do = set(a for a in sys.argv[1:] if a.startswith("--"))
    dry = "--purge" not in do
    if "--backup" in do:
        p = online_backup()
        print("備份完成:", p, human(os.path.getsize(p)))
    if not os.path.exists(DB):
        print(json.dumps({"ok": False, "msg": "找不到 " + DB}, ensure_ascii=False))
        return 1
    c = sqlite3.connect(DB, timeout=600)
    c.execute("pragma busy_timeout=600000")
    before = analyse(c)
    print("清理前:", json.dumps(before, ensure_ascii=False))
    if dry:
        print("（dry-run，未做任何修改；加上 --purge 才會實際清理）")
        c.close()
        return 0
    cur = c.cursor()
    maxseq = cur.execute("select max(seq_id) from embeddings_queue").fetchone()[0] or 0
    cols = {r[0]: r[1] for r in cur.execute("select id,name from collections")}
    segs = {}
    for sid, cid in cur.execute("select id,collection from segments"):
        segs.setdefault(cid, []).append(sid)
    advanced = 0
    deleted = 0
    for (topic,) in cur.execute("select distinct topic from embeddings_queue").fetchall():
        cid = topic.rsplit("/", 1)[-1]
        if cid not in cols:
            cur.execute("delete from embeddings_queue where topic=?", (topic,))
            deleted += cur.rowcount
            continue
        for sid in segs.get(cid, []):
            row = cur.execute("select seq_id from max_seq_id where segment_id=?", (sid,)).fetchone()
            if row is None:
                cur.execute("insert into max_seq_id(segment_id,seq_id) values(?,?)", (sid, maxseq))
                advanced += 1
            elif row[0] < maxseq:
                cur.execute("update max_seq_id set seq_id=? where segment_id=?", (maxseq, sid))
                advanced += 1
        cur.execute("delete from embeddings_queue where topic=? and seq_id<=?", (topic, maxseq))
        deleted += cur.rowcount
    c.commit()
    print(f"checkpoint 前移 segments: {advanced}，刪除 WAL 列: {deleted}")
    if "--vacuum" in do:
        t = time.time()
        c.execute("vacuum")
        c.commit()
        print(f"VACUUM 完成，耗時 {time.time()-t:.1f}s")
    after = analyse(c)
    print("清理後:", json.dumps(after, ensure_ascii=False))
    c.close()
    try:
        with open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "logs", "chroma_gc.log"), "a") as f:
            f.write(time.strftime("%F %T") + " " + json.dumps({"before": before, "after": after}, ensure_ascii=False) + "\n")
    except Exception:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
