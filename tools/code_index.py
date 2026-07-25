#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
code_index.py - 程式碼庫索引工具（基於 RAG）
功能：自動索引系統中的所有 Python/HTML 檔案，供 AI 在修改程式碼時檢索。
每次重啟時自動重建索引，確保程式碼修改後能被正確檢索。
"""







# ===== PLUGIN_INFO（供 mokagi 工具系統註冊）=====
PLUGIN_INFO = {
    "command": "/code",
    "icon": "📚",
    "handler": "handle_code_index",
    "description": "程式碼庫索引工具：搜尋程式碼(search)、重建索引(rebuild)、查看檔案內容(read_file)、查看程式碼區塊(get_chunk)。",



    "tool_schema": {
        "name": "code_index",
        "description": (
            "搜尋、查看和索引系統中的 Python/HTML 程式碼檔案。\n\n"
            "支援的操作：\n"
            "- **search**：搜尋程式碼，query 為關鍵詞，可選 n_results（預設 10）。\n"
            "  範例：{\"action\":\"search\",\"query\":\"get_system_context\"}\n\n"
            "- **rebuild**：重建索引，無需其他參數。\n"
            "  範例：{\"action\":\"rebuild\"}\n\n"
            "- **read_file**：讀取完整檔案，query 為檔案路徑。\n"
            "  範例：{\"action\":\"read_file\",\"query\":\"/home/ubuntu/.mok/core/mokagi.py\"}\n\n"
            "- **get_chunk**：查看指定行範圍，query 為檔案路徑，start_line 和 end_line 為起止行號（行號從 1 開始）。\n"
            "  範例：{\"action\":\"get_chunk\",\"query\":\"/home/ubuntu/.mok/core/mokagi.py\",\"start_line\":329,\"end_line\":507}\n\n"
            "- **debug_context**：為修 bug 自動收集相關上下文。\n"
            "  參數：query 為錯誤訊息或函數名稱，depth 為遞迴深度（預設 1），n_results 為每層搜索結果數（預設 5）。\n"
            "  範例：{\"action\":\"debug_context\",\"query\":\"save_pending_task 失敗\",\"depth\":1,\"n_results\":5}\n\n"
            "【重要】debug_context 會自動搜索相關程式碼、讀取完整片段、並遞迴找出呼叫關係，產生一份結構化報告，"
            "包含所有相關檔案路徑、行號和程式碼片段。非常適合用於定位和修復 bug。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["search", "rebuild", "read_file", "get_chunk"],
                    "description": "操作類型：search、rebuild、read_file、get_chunk"
                },
                "query": {
                    "type": "string",
                    "description": "search 時：搜尋關鍵詞；read_file 時：檔案路徑；get_chunk 時：檔案路徑"
                },
                "n_results": {
                    "type": "integer",
                    "description": "search 時：返回結果數量，預設 10"
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
                },
                "n_results": {
                    "type": "integer",
                    "description": "debug_context 時：每層搜索結果數（預設 5）"
                }
            },
            "required": ["action"]
        }
    }
}





import os
import re
import time
import hashlib
import json
import logging
from typing import Dict, List, Optional, Tuple
from pathlib import Path

from mok_token import count_tokens, MOK_max_tokens, truncate_by_token

import chromadb
from chromadb.config import Settings
from chromadb.utils import embedding_functions

# ===== 配置 =====
MOKAGI_HOME = os.environ.get("MOKAGI_HOME", "mok")
INDEX_DIRS = [
    os.path.expanduser(f"~/.{MOKAGI_HOME}/core"),
    os.path.expanduser(f"~/.{MOKAGI_HOME}/tools"),
    os.path.expanduser(f"~/.{MOKAGI_HOME}/frontends"),
    os.path.expanduser(f"~/.{MOKAGI_HOME}/html"),
]
CHROMA_PATH = os.path.expanduser(f"~/.{MOKAGI_HOME}/.chroma_data")
COLLECTION_NAME = "code_index"
# ==============

# 全域變量
_client = None
_collection = None
_embed_fn = None
import mokagi
agent_config = mokagi._agent_config
owner = agent_config.get("MOK_ADMIN_NAME")
agent_name = agent_config.get("MOK_AGENT_NAME")


def _init_embedding():
    """初始化 embedding 函數"""
    global _embed_fn
    if _embed_fn is not None:
        return _embed_fn
    try:
        _embed_fn = embedding_functions.SentenceTransformerEmbeddingFunction(
            model_name="all-MiniLM-L6-v2"
        )
        return _embed_fn
    except ImportError:
        logging.error("sentence-transformers 未安裝，請執行: pip install sentence-transformers")
        return None


def _get_client():
    """獲取 ChromaDB 客戶端"""
    global _client
    if _client is None:
        _client = chromadb.PersistentClient(
            path=CHROMA_PATH,
            settings=Settings(anonymized_telemetry=False)
        )
    return _client


def _get_collection():
    """獲取程式碼索引 collection"""
    global _collection
    if _collection is not None:
        return _collection
    embed_fn = _init_embedding()
    if embed_fn is None:
        return None
    client = _get_client()
    try:
        _collection = client.get_collection(COLLECTION_NAME)
    except:
        _collection = client.create_collection(
            name=COLLECTION_NAME,
            embedding_function=embed_fn
        )
    return _collection


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
    """掃描目錄下的所有 Python 和 HTML 檔案"""
    files = []
    for dir_path in dirs:
        if not os.path.exists(dir_path):
            continue
        for root, _, filenames in os.walk(dir_path):
            for filename in filenames:
                # 跳過 __pycache__ 和 .pyc
                if '__pycache__' in root or filename.endswith('.pyc'):
                    continue
                if filename.endswith('.py') or filename.endswith('.html'):
                    files.append(os.path.join(root, filename))
    return files


def rebuild_index(force: bool = False) -> str:
    """
    重建程式碼索引（刪除舊索引，重新建立）
    返回操作結果訊息
    """
    embed_fn = _init_embedding()
    if embed_fn is None:
        return "❌ 缺少 sentence-transformers，請執行: pip install sentence-transformers"
    
    col = _get_collection()
    if col is None:
        return "❌ 無法建立 collection"
    
    # 掃描所有檔案
    files = _scan_files(INDEX_DIRS)
    if not files:
        return f"⚠️ 未在任何目錄下找到 Python/HTML 檔案: {', '.join(INDEX_DIRS)}"
    
    # 收集所有區塊
    all_chunks = []
    for filepath in files:
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            
            # 根據副檔名選擇切分方法
            if filepath.endswith('.py'):
                chunks = _chunk_python_code(content, filepath)
            else:  # .html
                chunks = _chunk_html_code(content, filepath)
            all_chunks.extend(chunks)
        except Exception as e:
            logging.warning(f"讀取 {filepath} 失敗: {e}")
    
    if not all_chunks:
        return "⚠️ 未找到任何可索引的程式碼區塊"
    
    # 刪除舊索引
    try:
        col.delete(where={})
    except:
        pass
    
    # 批量新增
    batch_size = 100
    total = len(all_chunks)
    for i in range(0, total, batch_size):
        batch = all_chunks[i:i+batch_size]
        col.add(
            documents=[c["text"] for c in batch],
            metadatas=[c["metadata"] for c in batch],
            ids=[c["id"] for c in batch]
        )
    
    return f"✅ 程式碼索引已重建：{total} 個區塊，來自 {len(files)} 個檔案"


def search_code(query: str, n_results: int = 10, file_filter: str = None) -> List[Dict]:
    """
    搜尋程式碼
    返回 [{text, file, type, name, line_start, line_end}, ...]
    """
    col = _get_collection()
    if col is None:
        return []
    
    where = {}
    if file_filter:
        where["file"] = {"$contains": file_filter}
    
    try:
        results = col.query(
            query_texts=[query],
            n_results=n_results,
            where=where if where else None
        )
    except Exception as e:
        logging.error(f"搜尋失敗: {e}")
        return []
    
    docs = results.get("documents", [[]])[0]
    metas = results.get("metadatas", [[]])[0]
    
    output = []
    for doc, meta in zip(docs, metas):
        output.append({
            "text": doc,
            "file": meta.get("file", ""),
            "type": meta.get("type", "unknown"),
            "name": meta.get("name", ""),
            "line_start": meta.get("line_start", 0),
            "line_end": meta.get("line_end", 0),
            "ext": meta.get("ext", "")
        })
    
    return output


def get_full_file_content(filepath: str) -> Optional[str]:
    """讀取指定檔案的完整內容（繞過截斷限制）"""
    try:
        with open(filepath, 'r', encoding='utf-8') as f:
            return f.read()
    except:
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
    except:
        return None








def _build_debug_context(query: str, depth: int = 1, n_results: int = 5) -> str:
    """
    自動收集修 bug 所需的所有相關程式碼上下文。
    返回結構化報告（純文字）。
    """
    import re
    from collections import deque

    visited = set()          # 已處理的 (file, name)
    results = []             # 收集到的片段資訊
    queue = deque()          # (query, current_depth)
    queue.append((query, 0))

    # 正則：比對 Python import/from ... import
    import_pattern = re.compile(r'^\s*(?:from\s+(\S+)\s+import\s+(\S+)|import\s+(\S+))')
    # 比對函數/類別呼叫（簡化版：抓取 foo.bar 或 bar( 等）
    call_pattern = re.compile(r'\b([a-zA-Z_]\w*)\s*\(')

    while queue and len(results) < 30:  # 避免無限擴張
        current_query, cur_depth = queue.popleft()
        if cur_depth > depth:
            continue

        # 1. 搜索相關程式碼
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

            # 2. 讀取該區塊的前後 30 行（確保上下文）
            start = max(1, item["line_start"] - 30)
            end = item["line_end"] + 30
            section = get_file_section(filepath, start, end)
            if section is None:
                continue

            # 3. 存入結果
            results.append({
                "file": filepath,
                "name": name,
                "type": item["type"],
                "start": start,
                "end": end,
                "code": section
            })

            # 4. 分析該片段中的 import 和呼叫，加入下一層隊列
            if cur_depth < depth:
                # 只分析前 200 行，避免太慢
                lines = section.split('\n')[:200]
                for line in lines:
                    # 檢查 import
                    imp_match = import_pattern.search(line)
                    if imp_match:
                        # 提取模組名（可能含路徑）
                        mod = imp_match.group(1) or imp_match.group(3)
                        if mod and not mod.startswith('.'):
                            # 轉成可能的路徑或關鍵詞
                            queue.append((mod.split('.')[-1], cur_depth + 1))
                    # 檢查函數呼叫（只抓取與當前 query 相關的）
                    calls = call_pattern.findall(line)
                    for call in calls:
                        if call != name and len(call) > 2:
                            queue.append((call, cur_depth + 1))

    # 生成報告
    if not results:
        return f"⚠️ 未找到與「{query}」相關的任何程式碼片段。"

    report = f"📚 **修 Bug 上下文報告**（關鍵詞：{query}，深度：{depth}）\n\n"
    report += f"共找到 {len(results)} 個相關程式碼區塊：\n\n"

    for idx, r in enumerate(results, 1):
        report += f"【區塊 {idx}】{r['type']} `{r['name']}`\n"
        report += f"📁 {r['file']} (第 {r['start']}~{r['end']} 行)\n"
        # 截斷過長片段（每段最多 500 行，但前面已限制）
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
            return f"沒有找到與「{query}」相關的程式碼"
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
        
        # ===== 新增：檢查檔案行數 =====
        lines = content.split('\n')
        total_lines = len(lines)
        
        # 設定安全門檻（例如 200 行，可自行調整）
        SAFE_LINE_LIMIT = 200
        
        if total_lines > SAFE_LINE_LIMIT:
            # 不給內容，只給「操作方法」，讓 AI 自己決定下一步
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
        # ===== 結束檢查 =====
        
        ext = "python" if query.endswith('.py') else "html"
        result = f"📄 {query}\n```{ext}\n{content}\n```"
        return truncate_by_token(result)
    
    if action == "get_chunk":
        # 💦 兼容兩種參數格式：
        # 格式1: query 包含 "路徑 起始行 結束行"
        # 格式2: 使用 start_line 和 end_line 參數
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
        
        # 如果格式1解析失敗，嘗試從 start_line/end_line 獲取
        if not filepath or start == 0 or end == 0:
            # 從參數中獲取
            if isinstance(args, dict):
                filepath = args.get("query") or args.get("filepath")
                start = args.get("start_line", 0) or args.get("start", 0)
                end = args.get("end_line", 0) or args.get("end", 0)
            # 如果還是沒有，返回幫助信息
            if not filepath:
                return "用法: /code get_chunk <檔案路徑> <起始行> <結束行>\n範例: /code get_chunk /home/ubuntu/.mok/core/mokagi.py 329 507"
            if start == 0 or end == 0:
                return "請提供起始行和結束行（數字），例如：/code get_chunk /path/to/file.py 100 200"
        
        # 確保 start <= end
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
        # 解析深度和 n_results
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
        # 如果報告太長，截斷（但一般不會）
        # 改用統一的 token 截斷
        return truncate_by_token(report)

    return f"未知操作: {action}。支援: rebuild, search, read_file, get_chunk"