#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chroma_reindex.py — 修復「資料列在、但向量索引是空的」的 ChromaDB collection。

症狀：col.count() > 0，但 col.query() 回傳 0 命中（或 where 查詢報
      InternalError: Error finding id）。原因是舊資料的 HNSW 段沒了/沒寫進去，
      導致 /memory recall、對話語義搜索、記憶衝突消解全部失效。

做法：把既有 (id, document, metadata) 讀出來後原 id upsert 回去，
      讓 embedding 重新計算並寫入向量索引（metadata 不變）。

用法：
  python3 ~/.mok/tools/chroma_reindex.py --list                # 列出可疑 collection 與命中數
  python3 ~/.mok/tools/chroma_reindex.py --agent mokagi說明     # 只修這個 agent
  python3 ~/.mok/tools/chroma_reindex.py --all                 # 修所有 *_user_memory / *_conversation
  python3 ~/.mok/tools/chroma_reindex.py --all --dry-run
"""
import os, sys, time, json, sqlite3, logging

CHROMA_PATH = "/home/ubuntu/.mok/.chroma_data"
SUFFIXES = ("_user_memory", "_conversation")


def get_client():
    import chromadb
    from chromadb.config import Settings
    return chromadb.PersistentClient(path=CHROMA_PATH, settings=Settings(anonymized_telemetry=False))


def needs_fix(col, probe="記憶 偏好"):
    try:
        if col.count() == 0:
            return False
        r = col.query(query_texts=[probe], n_results=1)
        return len(r.get("ids", [[]])[0]) == 0
    except Exception:
        return True


def fix_collection(col, batch=200):
    got = col.get(limit=100000, include=["documents", "metadatas"])
    ids, docs, metas = got.get("ids") or [], got.get("documents") or [], got.get("metadatas") or []
    ok = miss = 0
    for i in range(0, len(ids), batch):
        bi = ids[i:i + batch]
        bd = [d if d else "" for d in docs[i:i + batch]]
        bm = [m or {} for m in metas[i:i + batch]]
        keep = [(a, b, c) for a, b, c in zip(bi, bd, bm) if b]
        if not keep:
            miss += len(bi)
            continue
        try:
            col.upsert(ids=[k[0] for k in keep], documents=[k[1] for k in keep], metadatas=[k[2] for k in keep])
            ok += len(keep)
        except Exception as e:
            logging.error(f"[reindex] upsert 失敗 {col.name}: {e}")
            miss += len(keep)
    return ok, miss


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    cl = get_client()
    names = [c.name for c in cl.list_collections()]
    want_agent = None
    if "--agent" in args:
        want_agent = args[args.index("--agent") + 1]
    if "--agent" in args or "--resolve" in args:
        # 把 agent 名對應到 collection 名（用 memory.py 的 sanitize 規則）
        import importlib.util
        spec = importlib.util.spec_from_file_location("memory", os.path.join(os.path.dirname(os.path.abspath(__file__)), "memory.py"))
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
        key = m.sanitize_name_for_chromadb(want_agent)
        names = [n for n in names if n in (f"agent_{key}_user_memory", f"agent_{key}_conversation")]
    elif "--all" not in args and "--list" not in args:
        print("用法: --list | --agent <名> | --all [--dry-run]")
        return 1
    else:
        names = [n for n in names if n.endswith(SUFFIXES)]
    print(f"檢查 {len(names)} 個 collection…")
    todo = []
    for n in names:
        try:
            col = cl.get_collection(n)
            if needs_fix(col):
                todo.append((n, col.count()))
        except Exception as e:
            print(f"  {n}: 開啟失敗 {e}")
    print(json.dumps({"need_fix": todo}, ensure_ascii=False))
    if dry:
        return 0
    fixed = 0
    for n, cnt in todo:
        col = cl.get_collection(n)
        t = time.time()
        ok, miss = fix_collection(col)
        good = len(col.query(query_texts=["記憶"], n_results=1).get("ids", [[]])[0]) > 0
        print(f"  {n}: 重建 {ok} 筆（略過 {miss}）耗時 {time.time()-t:.1f}s 檢索={'OK' if good else '仍失敗'}")
        if good:
            fixed += 1
    print(f"完成：修好 {fixed}/{len(todo)} 個 collection")
    return 0


if __name__ == "__main__":
    sys.exit(main())
