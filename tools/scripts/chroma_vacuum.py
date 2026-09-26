#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""離線壓縮向量庫（chroma.sqlite3）。
向量庫 ~1.8G 但實際向量僅約 1 萬條，其餘為 SQLite 膨脹（embeddings_queue / FTS）。
VACUUM 可回收空間；**只有沒有任何進程開啟此 DB 時才安全**。
請勿在 mokagi 服務運行時執行：需主人先停服務 → 執行 → 重啟。
用法：
  python3 chroma_vacuum.py --check   # 只看大小
  python3 chroma_vacuum.py           # VACUUM + 報告
"""
import os, sqlite3, sys

DB = os.path.expanduser("~/.mok/.chroma_data/chroma.sqlite3")


def size(p):
    try:
        return os.path.getsize(p)
    except Exception:
        return 0


def human(n):
    n = float(n)
    for u in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f}{u}"
        n /= 1024
    return f"{n:.1f}TB"


def main():
    if "--check" in sys.argv:
        print(f"{DB}: {human(size(DB))}")
        return
    before = size(DB)
    print(f"before: {human(before)}")
    con = sqlite3.connect(DB, timeout=5)
    try:
        con.execute("PRAGMA busy_timeout=5000")
        con.execute("VACUUM")
        con.commit()
    finally:
        con.close()
    after = size(DB)
    print(f"after : {human(after)}  (freed {human(before - after)})")


if __name__ == "__main__":
    main()
