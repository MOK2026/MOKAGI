"""db_conn.py - SQLite 統一連線層（L0）

所有核心模組的 SQLite 連線一律經由 connect() 建立，確保：
  - timeout=30.0         : Python 層等待寫鎖（取代 sqlite3 預設 5 秒）
  - PRAGMA busy_timeout  : SQLite 層等待寫鎖 30000ms（取代預設 0）
  - PRAGMA journal_mode  : WAL，讀寫並行互不阻塞
  - PRAGMA synchronous   : NORMAL（WAL 模式下安全且快）

目的：解掉多 agent / 多侍女並行時的 "database is locked"。
本層純參數調校，不改任何 schema、不改任何資料表、不改對外 API。

用法：
    from db_conn import connect
    with closing(connect(HISTORY_DB_PATH)) as conn:
        ...
"""

import sqlite3

DEFAULT_TIMEOUT = 30.0
DEFAULT_BUSY_TIMEOUT_MS = 30000


def connect(db_path, timeout=DEFAULT_TIMEOUT, readonly=False, **kwargs):
    """建立已調校好的 SQLite 連線。

    db_path  : 資料庫路徑（readonly=True 時傳純路徑即可，內部轉成 URI）
    timeout  : Python 層等待寫鎖的秒數
    readonly : 以唯讀模式開啟；不設 journal_mode（避免對唯讀庫寫 PRAGMA）
    """
    if readonly:
        conn = sqlite3.connect("file:%s?mode=ro" % db_path, uri=True,
                               timeout=timeout, **kwargs)
    else:
        conn = sqlite3.connect(db_path, timeout=timeout, **kwargs)

    try:
        conn.execute("PRAGMA busy_timeout = %d" % DEFAULT_BUSY_TIMEOUT_MS)
        if not readonly:
            conn.execute("PRAGMA journal_mode=WAL")
            conn.execute("PRAGMA synchronous=NORMAL")
    except sqlite3.Error:
        pass
    return conn
