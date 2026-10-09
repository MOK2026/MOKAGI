#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
code_index.py - 程式碼庫索引工具（SQLite FTS5 純關鍵字索引）

【為何棄用 ChromaDB / embedding】
MOK 同時跑多個 Python 進程（mok_agi / mok_warden / mokagi-social / mok_repair …），
它們共用同一個 ChromaDB 目錄。多進程搶寫會造成架構性問題：
  * SQLITE_READONLY_DBMOVED（code 1032 attempt to write a readonly database）
  * 子進程在原生層崩潰（exit=-11）或卡到逾時
  * 寫入被靜默丟棄 → 索引「假裝重建成功」卻毫無效果
而對「找函數名、找關鍵字」這類用途，純關鍵字（BM25）其實比語義 embedding 更精準，
且不需下載模型、不怕多進程、毫秒級又穩。

【本版架構】
單一 SQLite FTS5 資料庫（~/.mok/.code_index/code_index.db）：
  * unicode61 分詞器 + 自訂「索引正規化」（camelCase 拆詞、符號轉空白、中文逐字拆），
    讓 2 字中文（「逾時」）、識別字子詞（pending → save_pending_task）、
    camelCase（getSystemContext）都能命中，查詢端做同樣正規化。
  * WAL 模式：多進程可同時讀、單一寫入；整次重建包在一個 transaction 內完成，
    期間其他進程仍讀得到舊索引 → 不再有鎖競爭與崩潰。
對外介面完全不變：search / rebuild / read_file / get_chunk / debug_context。
"""

# ===== PLUGIN_INFO（供 mokagi 工具系統註冊）=====
PLUGIN_INFO = {
    "command": "/code",
    "icon": "📚",
    "handler": "handle_code_index",
    "description": "程式碼庫索引工具：搜尋程式碼(search)、重建索引(rebuild)、查看檔案內容(read_file)、查看程式碼區塊(get_chunk)。",
    "intent_keywords": ["/code", "程式碼", "代碼", "程式碼索引", "程式碼庫", "搜尋程式碼", "重建索引", "索引程式碼"],
    "tool_schema": {
        "name": "code_index",
        "description": (
            "搜尋、查看和索引系統中的 Python/HTML 程式碼檔案。\n\n"
            "支援的操作：\n"
            "- **search**：搜尋程式碼，query 為關鍵詞，可選 n_results（預設 10）。\n"
            "  範例：{\"action\":\"search\",\"query\":\"get_system_context\"}\n\n"
            "- **rebuild**：重建索引，無需其他參數。\n"
            "  範例：{\"action\":\"rebuild\"}\n\n"
            "- **read_file**：讀取完整檔案，query 為檔案的絕對路徑。\n"
            "  範例：{\"action\":\"read_file\",\"query\":\"/path/to/file.py\"}\n\n"
            "- **get_chunk**：查看指定行範圍，query 為檔案路徑，start_line 和 end_line 為起止行號（行號從 1 開始）。\n"
            "  範例：{\"action\":\"get_chunk\",\"query\":\"/path/to/file.py\",\"start_line\":329,\"end_line\":507}\n\n"
            "- **debug_context**：為修 bug 自動收集相關上下文。\n"
            "  參數：query 為錯誤訊息或函數名稱，depth 為遞迴深度（預設 1），n_results 為每層搜索結果數（預設 5）。\n"
            "  範例：{\"action\":\"debug_context\",\"query\":\"save_pending_task failed\",\"depth\":1,\"n_results\":5}\n\n"
            "【重要】debug_context 會自動搜索相關程式碼、讀取完整片段、並遞迴找出呼叫關係，產生一份結構化報告，"
            "包含所有相關檔案路徑、行號和程式碼片段。非常適合用於定位和修復 bug。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search", "rebuild", "read_file", "get_chunk", "debug_context"],
                    "description": "操作類型：search、rebuild、read_file、get_chunk、debug_context"
                },
                "query": {
                    "type": "string",
                    "description": "search 時：搜尋關鍵詞；read_file 時：檔案路徑；get_chunk 時：檔案路徑"
                },
                "n_results": {
                    "type": "integer",
                    "description": "search / debug_context 時：返回結果數量（預設 10 / 5）"
                },
                "start_line": {
                    "type": "integer",
                    "description": "get_chunk 時：起始行號（必填）"
                },
                "end_line": {
                    "type": "integer",
                    "description": "get_chunk 時：結束行號（必填）"
                },
                "depth": {
                    "type": "integer",
                    "description": "debug_context 時：遞迴深度（預設 1）"
                }
            },
            "required": ["action"]
        }
    }
}


import os
import re
import time
import json
import hashlib
import logging
import threading
import sqlite3
import fcntl
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from mok_token import count_tokens, MOK_max_tokens, truncate_by_token


# ===== 配置 =====
MOKAGI_HOME = os.environ.get("MOKAGI_HOME", "mok")
INDEX_DIRS = [
    os.path.expanduser(f"~/.{MOKAGI_HOME}/core"),
    os.path.expanduser(f"~/.{MOKAGI_HOME}/tools"),
    os.path.expanduser(f"~/.{MOKAGI_HOME}/frontends"),
    os.path.expanduser(f"~/.{MOKAGI_HOME}/html"),
]
# 索引改用單一 SQLite FTS5 資料庫（多進程安全、毫秒級）
INDEX_ROOT = os.path.expanduser(f"~/.{MOKAGI_HOME}/.code_index")
INDEX_DB_PATH = os.path.join(INDEX_ROOT, "code_index.db")
# 重建序列化鎖：避免開機時多個進程同時重建（雖然 FTS5 已安全，仍省重複工）
REBUILD_LOCK_PATH = os.path.join(INDEX_ROOT, ".rebuild.lock")
INDEX_MARKER_PATH = os.path.join(INDEX_ROOT, ".index_built_at")
# 掃描範圍控制（可用環境變數覆寫）：
#  MOK_CODE_INDEX_SKIP   要跳過的「路徑片段」，逗號分隔（預設排除爬蟲行銷頁）
#  MOK_CODE_INDEX_MAX_KB 單檔大小上限（KB），0=不限（預設 300）
_CODE_INDEX_SKIP = [x.strip() for x in os.environ.get(
    "MOK_CODE_INDEX_SKIP", "/html/project/,/trash/,/_dev_archive/").split(",") if x.strip()]
try:
    _CODE_INDEX_MAX_KB = int(os.environ.get("MOK_CODE_INDEX_MAX_KB", "300") or "0")
except ValueError:
    _CODE_INDEX_MAX_KB = 300
# ==============

# 全域變量
_conn = None
_db_lock = threading.RLock()
_index_operation_lock = threading.RLock()
owner = os.environ.get("MOK_ADMIN_NAME", "用戶")
agent_name = os.environ.get("MOK_AGENT_NAME", "Agent")


# ===== 文字正規化（索引端與查詢端共用，確保兩邊一致）=====
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff]")


def _normalize_text(text: str) -> str:
    """把任意程式碼/查詢字串正規化成適合 unicode61 分詞的形式。

    處理步驟：
      1. camelCase / PascalCase 拆詞（savePendingTask -> save Pending Task）
      2. 底線、點、連字號、括號等符號 -> 空白（save_pending_task -> save pending task）
      3. 轉小寫
      4. 中日韓漢字逐字用空白隔開（逾時 -> 逾 時），讓 2 字中文可被精確比對

    索引端與查詢端都呼叫本函式，故「查什麼、索引什麼」永遠對齊。
    """
    if not text:
        return ""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    s = re.sub(r"(?<=[A-Z])(?=[A-Z][a-z])", " ", s)
    s = re.sub(r"[^0-9A-Za-z\u3400-\u4dbf\u4e00-\u9fff]+", " ", s)
    s = s.lower()
    out = []
    for ch in s:
        if _CJK_RE.match(ch):
            out.append(" ")
            out.append(ch)
            out.append(" ")
        else:
            out.append(ch)
    s = "".join(out)
    return re.sub(r"\s+", " ", s).strip()


_INSERT_SQL = (
    "INSERT INTO chunks (norm, cid, file, ctype, name, line_start, line_end, ext, raw) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)"
)


def _get_conn():
    """取得（並初始化）本進程的 SQLite FTS5 連線（WAL，多進程安全）。"""
    global _conn
    if _conn is not None:
        return _conn
    with _db_lock:
        if _conn is not None:
            return _conn
        os.makedirs(INDEX_ROOT, exist_ok=True)
        conn = sqlite3.connect(INDEX_DB_PATH, timeout=30.0, check_same_thread=False)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA busy_timeout=30000")
        conn.execute(
            "CREATE VIRTUAL TABLE IF NOT EXISTS chunks USING fts5("
            "norm, cid UNINDEXED, file UNINDEXED, ctype UNINDEXED, name UNINDEXED, "
            "line_start UNINDEXED, line_end UNINDEXED, ext UNINDEXED, raw UNINDEXED, "
            "tokenize='unicode61 remove_diacritics 2')"
        )
        conn.commit()
        _conn = conn
        return _conn


def _chunk_python_code(content: str, filepath: str) -> List[Dict]:
    """
    將 Python 程式碼按函數/類別切分為區塊
    返回 [{id, text, metadata}, ...]
    """
    chunks = []
    lines = content.split('\n')

    # 正則：匹配函數定義、類別定義、頂層程式碼
    func_pattern = re.compile(r'^(async\s+)?def\s+(\w+)\s*\(')
    class_pattern = re.compile(r'^class\s+(\w+)\s*[:\(]')

    current_chunk = []
    current_type = "header"
    current_name = "header"
    line_num = 0

    for line in lines:
        line_num += 1
        stripped = line.strip()

        # 檢測新函數或類別
        func_match = func_pattern.match(stripped)
        class_match = class_pattern.match(stripped)

        if func_match or class_match:
            # 儲存之前的區塊
            if current_chunk and not (len(current_chunk) == 1 and current_chunk[0].strip() == ''):
                chunk_text = '\n'.join(current_chunk)
                if len(chunk_text) > 20:
                    chunk_id = hashlib.md5(f"{filepath}_{current_name}_{line_num}".encode()).hexdigest()[:16]
                    chunks.append({
                        "id": chunk_id,
                        "text": chunk_text,
                        "metadata": {
                            "file": filepath,
                            "type": current_type,
                            "name": current_name,
                            "line_start": line_num - len(current_chunk),
                            "line_end": line_num - 1,
                            "ext": "py"
                        }
                    })

            # 開始新區塊
            if func_match:
                current_type = "function"
                current_name = func_match.group(2)
            else:
                current_type = "class"
                current_name = class_match.group(1)
            current_chunk = [line]
        else:
            current_chunk.append(line)

    # 儲存最後一個區塊
    if current_chunk and len(current_chunk) > 1:
        chunk_text = '\n'.join(current_chunk)
        if len(chunk_text) > 20:
            chunk_id = hashlib.md5(f"{filepath}_{current_name}_{line_num}".encode()).hexdigest()[:16]
            chunks.append({
                "id": chunk_id,
                "text": chunk_text,
                "metadata": {
                    "file": filepath,
                    "type": current_type,
                    "name": current_name,
                    "line_start": line_num - len(current_chunk),
                    "line_end": line_num,
                    "ext": "py"
                }
            })

    return chunks


def _chunk_html_code(content: str, filepath: str) -> List[Dict]:
    """
    將 HTML 程式碼按區塊切分（按註解、標籤、樣式、腳本）
    返回 [{id, text, metadata}, ...]
    """
    chunks = []
    lines = content.split('\n')

    current_chunk = []
    current_type = "html"
    current_name = "html"
    line_num = 0

    # 偵測 HTML 區塊標記
    section_patterns = [
        (r'<!--.*-->', 'comment'),
        (r'<style[^>]*>', 'style'),
        (r'<script[^>]*>', 'script'),
        (r'<[^>]+>', 'tag'),
    ]

    in_special_block = False
    special_type = ""

    for line in lines:
        line_num += 1
        stripped = line.strip()

        # 檢測是否進入特殊區塊（style, script）
        if '<style' in stripped or '<script' in stripped:
            if current_chunk:
                chunk_text = '\n'.join(current_chunk)
                if len(chunk_text) > 20:
                    chunk_id = hashlib.md5(f"{filepath}_{current_name}_{line_num}".encode()).hexdigest()[:16]
                    chunks.append({
                        "id": chunk_id,
                        "text": chunk_text,
                        "metadata": {
                            "file": filepath,
                            "type": current_type,
                            "name": current_name,
                            "line_start": line_num - len(current_chunk),
                            "line_end": line_num - 1,
                            "ext": "html"
                        }
                    })
            current_chunk = [line]
            if '<style' in stripped:
                current_type = "style"
                current_name = "style"
            else:
                current_type = "script"
                current_name = "script"
            in_special_block = True
            continue

        # 檢測離開特殊區塊
        if in_special_block and ('</style>' in stripped or '</script>' in stripped):
            current_chunk.append(line)
            chunk_text = '\n'.join(current_chunk)
            if len(chunk_text) > 20:
                chunk_id = hashlib.md5(f"{filepath}_{current_name}_{line_num}".encode()).hexdigest()[:16]
                chunks.append({
                    "id": chunk_id,
                    "text": chunk_text,
                    "metadata": {
                        "file": filepath,
                        "type": current_type,
                        "name": current_name,
                        "line_start": line_num - len(current_chunk) + 1,
                        "line_end": line_num,
                        "ext": "html"
                    }
                })
            current_chunk = []
            current_type = "html"
            current_name = "html"
            in_special_block = False
            continue

        current_chunk.append(line)

    # 儲存最後一個區塊
    if current_chunk and len(current_chunk) > 1:
        chunk_text = '\n'.join(current_chunk)
        if len(chunk_text) > 20:
            chunk_id = hashlib.md5(f"{filepath}_{current_name}_{line_num}".encode()).hexdigest()[:16]
            chunks.append({
                "id": chunk_id,
                "text": chunk_text,
                "metadata": {
                    "file": filepath,
                    "type": current_type,
                    "name": current_name,
                    "line_start": line_num - len(current_chunk) + 1,
                    "line_end": line_num,
                    "ext": "html"
                }
            })

    return chunks


def _scan_files(dirs: List[str]) -> List[str]:
    """掃描目錄下的所有 Python 和 HTML 檔案。

    依 _CODE_INDEX_SKIP（路徑片段黑名單）與 _CODE_INDEX_MAX_KB（單檔大小上限）過濾，
    避免把爬蟲抓來的大量行銷頁／巨型檔案全部切塊，導致索引暴增、重建過慢。
    """
    files = []
    for dir_path in dirs:
        if not os.path.exists(dir_path):
            continue
        for root, _, filenames in os.walk(dir_path):
            if any(sk in (root + "/") for sk in _CODE_INDEX_SKIP):
                continue
            for filename in filenames:
                # 跳過 __pycache__ 和 .pyc
                if '__pycache__' in root or filename.endswith('.pyc'):
                    continue
                if not (filename.endswith('.py') or filename.endswith('.html')):
                    continue
                fp = os.path.join(root, filename)
                if any(sk in fp for sk in _CODE_INDEX_SKIP):
                    continue
                if _CODE_INDEX_MAX_KB > 0:
                    try:
                        if os.path.getsize(fp) > _CODE_INDEX_MAX_KB * 1024:
                            continue
                    except OSError:
                        continue
                files.append(fp)
    return files


def _newest_source_mtime() -> float:
    """回傳索引目錄中最新的程式碼檔案修改時間"""
    newest = 0.0
    try:
        for fp in _scan_files(INDEX_DIRS):
            try:
                newest = max(newest, os.path.getmtime(fp))
            except OSError:
                continue
    except Exception:
        return 0.0
    return newest


def _index_is_fresh() -> bool:
    """索引是否已是最新（沒有程式碼檔案比上次成功重建時間更新）"""
    try:
        built_at = os.path.getmtime(INDEX_MARKER_PATH)
    except OSError:
        return False
    return _newest_source_mtime() <= built_at


def _touch_marker():
    try:
        with open(INDEX_MARKER_PATH, "w") as mf:
            mf.write(str(time.time()))
    except OSError:
        pass


def rebuild_index(force: bool = False) -> str:
    """
    重建程式碼索引（FTS5）。

    多個 agent 進程共用同一個 SQLite 資料庫：WAL 模式下讀寫互不阻塞，整次重建
    包在單一 transaction 內完成，commit 前其他進程仍讀得到舊索引，因此不再有
    過去 ChromaDB 的 SQLITE_READONLY_DBMOVED / SIGSEGV / 寫入被丟棄 等問題。
    仍以跨進程檔案鎖序列化重建，避免開機時多個進程同時做白工。
    force=False 時若索引已是最新則直接跳過。
    """
    try:
        os.makedirs(INDEX_ROOT, exist_ok=True)
    except OSError:
        pass

    lock_fd = os.open(REBUILD_LOCK_PATH, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        deadline = time.time() + 90  # 最多等 90 秒取得跨進程鎖
        while True:
            try:
                fcntl.flock(lock_fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError:
                if time.time() >= deadline:
                    return "⏳ 另一個進程正在重建索引，等待 90 秒仍未取得鎖，請稍後再試"
                time.sleep(1)

        if not force and _index_is_fresh():
            return "✅ 程式碼索引已是最新，跳過重建（其他進程剛重建過）"

        with _index_operation_lock:
            result = _rebuild_index_locked(force)

        # FTS5 寫入失敗會直接拋例外，不會「靜默成功」；成功才更新新鮮度標記。
        if isinstance(result, str) and result.startswith("✅"):
            _touch_marker()
        return result
    finally:
        try:
            fcntl.flock(lock_fd, fcntl.LOCK_UN)
        finally:
            os.close(lock_fd)


def _rebuild_index_locked(force: bool = False) -> str:
    files = _scan_files(INDEX_DIRS)
    if not files:
        return f"⚠️ 未在任何目錄下找到 Python/HTML 檔案: {', '.join(INDEX_DIRS)}"

    # 收集所有區塊
    all_chunks = []
    for filepath in files:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            if filepath.endswith('.py'):
                chunks = _chunk_python_code(content, filepath)
            else:  # .html
                chunks = _chunk_html_code(content, filepath)
            all_chunks.extend(chunks)
        except Exception as e:
            logging.warning(f"讀取 {filepath} 失敗: {e}")

    if not all_chunks:
        return "⚠️ 未找到任何可索引的程式碼區塊"

    conn = _get_conn()
    started = time.time()
    inserted = 0
    try:
        with _db_lock:
            # 單一交易：清空 + 重寫；commit 前其他進程讀到的是舊索引（WAL）
            conn.execute("BEGIN IMMEDIATE")
            conn.execute("DELETE FROM chunks")
            batch = []
            for c in all_chunks:
                md = c["metadata"]
                norm = _normalize_text(md.get("name", "") + " " + c["text"])
                batch.append((
                    norm,
                    c["id"],
                    md.get("file", ""),
                    md.get("type", ""),
                    md.get("name", ""),
                    int(md.get("line_start", 0)),
                    int(md.get("line_end", 0)),
                    md.get("ext", ""),
                    c["text"],
                ))
                if len(batch) >= 500:
                    conn.executemany(_INSERT_SQL, batch)
                    inserted += len(batch)
                    batch = []
            if batch:
                conn.executemany(_INSERT_SQL, batch)
                inserted += len(batch)
            conn.execute("COMMIT")
    except Exception as e:
        try:
            conn.execute("ROLLBACK")
        except Exception:
            pass
        logging.error(f"[code_index] FTS5 重建失敗: {e}")
        return f"❌ 索引重建失敗：{type(e).__name__}: {e}"

    elapsed = time.time() - started
    logging.info(
        f"[code_index] FTS5 重建完成：索引共 {inserted} 個區塊 / {len(files)} 個檔案（耗時 {elapsed:.2f}s）"
    )
    return (
        f"✅ 程式碼索引已重建：共 {inserted} 個區塊，來自 {len(files)} 個檔案"
        f"（SQLite FTS5，耗時 {elapsed:.2f} 秒）"
    )


def _query_keyword_tokens(query: str) -> List[str]:
    """從查詢抽出可精確比對的關鍵字（英文識別字 + 中文詞，供子字串後備檢索）。"""
    tokens: List[str] = []
    seen = set()
    for t in re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", query or ""):
        if t.lower() not in seen:
            seen.add(t.lower())
            tokens.append(t)
    for t in re.findall(r"[\u3400-\u4dbf\u4e00-\u9fff]{2,}", query or ""):
        if t not in seen:
            seen.add(t)
            tokens.append(t)
    return tokens


def _build_match_query(query: str) -> str:
    """把查詢正規化後，組成 FTS5 MATCH 表達式（每個 token 加引號、以 AND 串接）。"""
    norm = _normalize_text(query)
    toks = [t for t in norm.split() if t]
    if not toks:
        return ""
    return " ".join('"%s"' % t.replace('"', '') for t in toks)


def search_code(query: str, n_results: int = 10, file_filter: str = None) -> List[Dict]:
    """搜尋程式碼（FTS5 / BM25 排序，含 LIKE 與原文關鍵字兩層後備）。"""
    with _index_operation_lock:
        return _search_code_locked(query, n_results, file_filter)


def _search_code_locked(query: str, n_results: int = 10, file_filter: str = None) -> List[Dict]:
    if not query:
        return []
    try:
        conn = _get_conn()
    except Exception as e:
        logging.error(f"[code_index] 取得 FTS5 連線失敗: {e}")
        return []
    if conn is None:
        return []

    out: List[Dict] = []
    seen = set()

    def _absorb(rows, keyword=False):
        for row in rows:
            key = (row["file"], row["line_start"], row["line_end"])
            if key in seen:
                continue
            seen.add(key)
            d = {
                "text": row["raw"],
                "file": row["file"],
                "type": row["ctype"] or "unknown",
                "name": row["name"],
                "line_start": row["line_start"],
                "line_end": row["line_end"],
                "ext": row["ext"],
            }
            if keyword:
                d["keyword"] = True
            out.append(d)

    conn.row_factory = sqlite3.Row
    file_clause = ""
    file_params: List = []
    if file_filter:
        file_clause = " AND file LIKE ?"
        file_params = [f"%{file_filter}%"]

    match_q = _build_match_query(query)
    if match_q:
        try:
            sql = (
                "SELECT file, ctype, name, line_start, line_end, ext, raw, bm25(chunks) AS rank "
                "FROM chunks WHERE chunks MATCH ?" + file_clause + " ORDER BY rank LIMIT ?"
            )
            rows = conn.execute(sql, [match_q] + file_params + [max(n_results * 3, 20)]).fetchall()
            _absorb(rows)
        except Exception as e:
            logging.warning(f"[code_index] FTS5 MATCH 檢索失敗（改用後備）: {e}")

    if len(out) < n_results:
        norm_q = _normalize_text(query).replace(" ", "")
        if norm_q:
            try:
                sql = (
                    "SELECT file, ctype, name, line_start, line_end, ext, raw "
                    "FROM chunks WHERE replace(norm, ' ', '') LIKE ?" + file_clause + " LIMIT ?"
                )
                rows = conn.execute(sql, [f"%{norm_q}%"] + file_params + [n_results]).fetchall()
                _absorb(rows, keyword=True)
            except Exception as e:
                logging.warning(f"[code_index] LIKE 後備檢索失敗: {e}")

    if len(out) < n_results:
        for tok in _query_keyword_tokens(query)[:3]:
            if len(out) >= n_results:
                break
            try:
                sql = (
                    "SELECT file, ctype, name, line_start, line_end, ext, raw "
                    "FROM chunks WHERE raw LIKE ?" + file_clause + " LIMIT ?"
                )
                rows = conn.execute(sql, [f"%{tok}%"] + file_params + [n_results]).fetchall()
                _absorb(rows, keyword=True)
            except Exception as e:
                logging.warning(f"[code_index] 原文關鍵字後備檢索失敗({tok}): {e}")

    return out[:n_results]


def get_full_file_content(filepath: str) -> Optional[str]:
    """讀取指定檔案的完整內容（繞過截斷限制）"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except Exception:
        return None


def get_file_section(filepath: str, start_line: int, end_line: int) -> Optional[str]:
    """讀取檔案的指定行範圍"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        if start_line < 1:
            start_line = 1
        if end_line > len(lines):
            end_line = len(lines)
        return ''.join(lines[start_line-1:end_line])
    except Exception:
        return None


def _build_debug_context(query: str, depth: int = 1, n_results: int = 5) -> str:
    """自動收集修 bug 所需的所有相關程式碼上下文，返回結構化報告。"""
    import re as _re
    from collections import deque

    visited = set()
    results = []
    queue = deque()
    queue.append((query, 0))

    import_pattern = _re.compile(r'^\s*(?:from\s+(\S+)\s+import\s+(\S+)|import\s+(\S+))')
    call_pattern = _re.compile(r'\b([a-zA-Z_]\w*)\s*\(')

    while queue and len(results) < 30:
        current_query, cur_depth = queue.popleft()
        if cur_depth > depth:
            continue

        search_results = search_code(current_query, n_results)
        if not search_results:
            continue

        for item in search_results:
            filepath = item["file"]
            name = item["name"]
            key = (filepath, name)
            if key in visited:
                continue
            visited.add(key)

            start = max(1, item["line_start"] - 30)
            end = item["line_end"] + 30
            section = get_file_section(filepath, start, end)
            if section is None:
                continue

            results.append({
                "file": filepath,
                "name": name,
                "type": item["type"],
                "start": start,
                "end": end,
                "code": section
            })

            if cur_depth < depth:
                lines = section.split('\n')[:200]
                for line in lines:
                    imp_match = import_pattern.search(line)
                    if imp_match:
                        mod = imp_match.group(1) or imp_match.group(3)
                        if mod and not mod.startswith('.'):
                            queue.append((mod.split('.')[-1], cur_depth + 1))
                    calls = call_pattern.findall(line)
                    for call in calls:
                        if call != name and len(call) > 2:
                            queue.append((call, cur_depth + 1))

    if not results:
        return f"⚠️ 未找到與「{query}」相關的任何程式碼片段。"

    report = f"📚 **修 Bug 上下文報告**（關鍵詞：{query}，深度：{depth}）\n\n"
    report += f"共找到 {len(results)} 個相關程式碼區塊：\n\n"

    for idx, r in enumerate(results, 1):
        report += f"【區塊 {idx}】{r['type']} `{r['name']}`\n"
        report += f"📁 {r['file']} (第 {r['start']}~{r['end']} 行)\n"
        code = r['code']
        if len(code.splitlines()) > 200:
            code = '\n'.join(code.splitlines()[:200]) + "\n... (片段過長，已截斷)"
        report += f"```python\n{code}\n```\n\n"

    return report


async def handle_code_index(args, chat_id: str = None, agent_config: Dict = None) -> str:
    """處理 /code 命令"""
    if isinstance(args, dict):
        action = args.get("action", "")
        query = args.get("query", "")
        n_results = args.get("n_results", 10)
        start_line = args.get("start_line", 0)
        end_line = args.get("end_line", 0)
    else:
        parts = str(args).strip().split(maxsplit=1)
        action = parts[0] if parts else ""
        query = parts[1] if len(parts) > 1 else ""
        n_results = 10
        start_line = 0
        end_line = 0

    if action == "rebuild":
        return rebuild_index(force=True)

    if action == "search":
        if not query:
            return "用法: /code search 關鍵詞 [數量]"
        if len(query.split()) > 1 and query.split()[-1].isdigit():
            parts = query.rsplit(maxsplit=1)
            query = parts[0]
            n_results = int(parts[1])
        results = search_code(query, n_results)
        if not results:
            return f"⚠️ code_index 沒有找到與「{query}」相關的程式碼；請改用更具體的函數名稱、英文關鍵詞，或先執行 rebuild。"
        output = f"📚 找到 {len(results)} 個相關程式碼區塊：\n\n"
        for i, r in enumerate(results, 1):
            ext_icon = "🐍" if r['ext'] == 'py' else "🌐"
            output += f"【{i}】{ext_icon} {r['name']} ({r['type']})\n"
            output += f"📁 {r['file']} (第 {r['line_start']}~{r['line_end']} 行)\n"
            preview = r['text'][:200].replace('\n', ' ').strip()
            if len(r['text']) > 200:
                preview += "..."
            output += f"📝 {preview}\n\n"
        return truncate_by_token(output)

    if action == "read_file":
        if not query:
            return "用法: /code read_file 檔案路徑"
        content = get_full_file_content(query)
        if content is None:
            return f"❌ 無法讀取檔案: {query}"
        lines = content.split('\n')
        total_lines = len(lines)
        SAFE_LINE_LIMIT = 200
        if total_lines > SAFE_LINE_LIMIT:
            return (
                f"⚠️ 檔案 `{query}` 共有 **{total_lines} 行**（超過 {SAFE_LINE_LIMIT} 行安全限制）。\n\n"
                f"直接讀取全文會觸發訊息長度限制，請改用以下方式分段查看：\n\n"
                f"1. **搜尋關鍵函數**：\n"
                f"   `/code search <關鍵詞>`\n"
                f"   例如：`/code search save_pending_task`\n\n"
                f"2. **分段讀取特定行範圍**：\n"
                f"   `/code get_chunk {query} <起始行> <結束行>`\n"
                f"   例如：`/code get_chunk {query} 1 200`\n\n"
                f"---\n"
                f"💡 **給 {agent_name} 的指示**：請根據{owner}命令，自動選擇使用 `search` 找出相關區塊，\n"
                f"或逐段使用 `get_chunk` 讀取完整內容。"
            )
        ext = "python" if query.endswith('.py') else "html"
        result = f"📄 {query}\n```{ext}\n{content}\n```"
        return truncate_by_token(result)

    if action == "get_chunk":
        filepath = None
        start = 0
        end = 0
        if query:
            parts = query.split()
            if len(parts) >= 3:
                filepath = parts[0]
                try:
                    start = int(parts[1])
                    end = int(parts[2])
                except ValueError:
                    pass
        if not filepath or start == 0 or end == 0:
            if isinstance(args, dict):
                filepath = args.get("query") or args.get("filepath")
                start = args.get("start_line", 0) or args.get("start", 0)
                end = args.get("end_line", 0) or args.get("end", 0)
            if not filepath:
                return "用法: /code get_chunk <檔案路徑> <起始行> <結束行>\n範例: /code get_chunk /home/ubuntu/.mok/core/mokagi.py 329 507"
            if start == 0 or end == 0:
                return "請提供起始行和結束行（數字），例如：/code get_chunk /path/to/file.py 100 200"
        if start > end:
            start, end = end, start
        content = get_file_section(filepath, start, end)
        if content is None:
            return f"❌ 無法讀取檔案: {filepath}"
        ext = "python" if filepath.endswith('.py') else "html"
        result = f"📄 {filepath} (第 {start}~{end} 行)\n```{ext}\n{content}\n```"
        return truncate_by_token(result)

    if action == "debug_context":
        if not query:
            return "用法: /code debug_context <錯誤訊息或函數名稱> [深度] [每層結果數]"
        parts = query.split()
        base_query = parts[0]
        depth = 1
        n_res = 5
        if len(parts) > 1 and parts[1].isdigit():
            depth = int(parts[1])
        if len(parts) > 2 and parts[2].isdigit():
            n_res = int(parts[2])
        if depth < 0:
            depth = 0
        if n_res < 1:
            n_res = 1
        report = _build_debug_context(base_query, depth, n_res)
        return truncate_by_token(report)

    return (
        "用法: /code <action> [參數]\n"
        "  search <關鍵詞> [數量]     搜尋程式碼\n"
        "  rebuild                    重建索引\n"
        "  read_file <路徑>           讀取檔案\n"
        "  get_chunk <路徑> <起> <迄> 讀取指定行範圍\n"
        "  debug_context <關鍵詞> [深度] [數量]  收集修 bug 上下文"
    )
