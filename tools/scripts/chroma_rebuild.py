#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chroma_rebuild.py — 原地重建 ChromaDB collection（修復向量段失效）

症狀：col.count() > 0 但 query 回 0 命中、get(include=['embeddings']) 報
      InternalError: Error finding id —— 資料列在 metadata 段，向量段卻沒有對應 id。
      舊版 chroma 建立的 collection 特別容易這樣（向量段檔案停在很久以前）。
      後果：/memory recall、對話語義搜索、記憶衝突消解（相似度）全部失效。

做法：讀出所有 (id, document, metadata) → 刪掉 collection → 用同一個 embedding
      函式重新建立同名 collection → 重新 add。向量段與 metadata 段即恢復一致。

用法：
  python3 ~/.mok/tools/chroma_rebuild.py --name <collection> [...可多個]
  python3 ~/.mok/tools/chroma_rebuild.py --all-broken [--dry-run]
  python3 ~/.mok/tools/chroma_rebuild.py --agent mokagi說明
"""
import os, sys, time, json, importlib.util, traceback

CHROMA_PATH = "/home/ubuntu/.mok/.chroma_data"
SUFFIXES = ("_user_memory", "_conversation")


def load_memory():
    spec = importlib.util.spec_from_file_location("memory", "/home/ubuntu/.mok/tools/memory.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    m._init_embedding()
    return m


def broken(col):
    try:
        if col.count() == 0:
            return False
        r = col.query(query_texts=["記憶"], n_results=1)
        return len(r.get("ids", [[]])[0]) == 0
    except Exception:
        return True


def rebuild(cl, ef, name, dry=False):
    col = cl.get_collection(name)
    meta = dict(col.metadata or {})
    got = col.get(limit=200000, include=["documents", "metadatas"])
    ids, docs, metas = got.get("ids") or [], got.get("documents") or [], got.get("metadatas") or []
    pairs = [(i, d, m or {}) for i, d, m in zip(ids, docs, metas) if d]
    info = {"name": name, "rows": len(ids), "usable": len(pairs), "skipped": len(ids) - len(pairs)}
    if dry:
        return info
    cl.delete_collection(name)
    col2 = cl.create_collection(name, embedding_function=ef, metadata=meta or None)
    for i in range(0, len(pairs), 100):
        b = pairs[i:i + 100]
        col2.add(ids=[x[0] for x in b], documents=[x[1] for x in b], metadatas=[x[2] for x in b])
    try:
        hits = col2.query(query_texts=["記憶"], n_results=1).get("ids", [[]])[0]
        info["query_ok"] = len(hits) > 0
    except Exception as e:
        info["query_ok"] = False
        info["err"] = str(e)[:100]
    info["count_after"] = col2.count()
    return info


def main():
    args = sys.argv[1:]
    dry = "--dry-run" in args
    memory = load_memory()
    import chromadb
    from chromadb.config import Settings
    cl = chromadb.PersistentClient(path=CHROMA_PATH, settings=Settings(anonymized_telemetry=False))
    ef = memory._embed_fn
    names = []
    for a in args:
        if a.startswith("--name="):
            names.append(a.split("=", 1)[1])
    if "--name" in args:
        for i, a in enumerate(args):
            if a == "--name" and i + 1 < len(args):
                names.append(args[i + 1])
    if "--agent" in args:
        key = memory.sanitize_name_for_chromadb(args[args.index("--agent") + 1])
        names += [f"agent_{key}_user_memory", f"agent_{key}_conversation"]
    if "--all-broken" in args:
        for c in cl.list_collections():
            if c.name.endswith(SUFFIXES) and c.name not in names and broken(c):
                names.append(c.name)
    names = [n for n in dict.fromkeys(names) if n]
    if not names:
        print("用法: --name <col> | --agent <名> | --all-broken [--dry-run]")
        return 1
    print(f"待處理 {len(names)} 個 collection" + ("（dry-run）" if dry else ""))
    ok = 0
    for n in names:
        try:
            info = rebuild(cl, ef, n, dry=dry)
            print("  " + json.dumps(info, ensure_ascii=False))
            if info.get("query_ok"):
                ok += 1
        except Exception as e:
            print(f"  {n}: 失敗 {type(e).__name__} {str(e)[:120]}")
    print(f"完成：{ok}/{len(names)} 修復成功")
    return 0


if __name__ == "__main__":
    sys.exit(main())
