"""
file_cache.py - 檔案讀取快取模組
用途：避免同一 session 中重複讀取相同檔案，減少 I/O 和 LLM context 膨脹。
"""
import os
import time
import hashlib
import logging

# ===== 全域快取 =====
# 格式: {real_path: {"content": str, "mtime": float, "cached_at": float}}
_file_cache = {}
_cache_max_age = 300  # 快取有效期 5 分鐘
_cache_max_size = 50  # 最多快取 50 個檔案


def get_cached_file(real_path: str, max_lines: int = 0) -> dict:
    """
    獲取快取中的檔案內容。
    返回: {"cached": bool, "content": str, "source": "cache"|"fresh"}
    """
    real_path = os.path.realpath(real_path)
    
    # 檢查檔案是否存在
    if not os.path.exists(real_path):
        return {"cached": False, "content": "", "error": "file_not_found"}
    
    current_mtime = os.path.getmtime(real_path)
    cache_key = f"{real_path}:{max_lines}"
    
    # 快取命中且未過期
    if cache_key in _file_cache:
        entry = _file_cache[cache_key]
        if entry["mtime"] == current_mtime and (time.time() - entry["cached_at"]) < _cache_max_age:
            logging.info(f"[file_cache] ✅ 快取命中: {real_path} (max_lines={max_lines})")
            return {"cached": True, "content": entry["content"], "source": "cache"}
    
    return {"cached": False}


def set_cached_file(real_path: str, content: str, max_lines: int = 0):
    """
    設定檔案快取。
    """
    real_path = os.path.realpath(real_path)
    if not os.path.exists(real_path):
        return
    
    cache_key = f"{real_path}:{max_lines}"
    
    # 清理過期快取
    if len(_file_cache) >= _cache_max_size:
        oldest_key = min(_file_cache.keys(), key=lambda k: _file_cache[k]["cached_at"])
        del _file_cache[oldest_key]
    
    _file_cache[cache_key] = {
        "content": content,
        "mtime": os.path.getmtime(real_path),
        "cached_at": time.time()
    }
    logging.info(f"[file_cache] 📦 已快取: {real_path} ({len(content)}B)")


def clear_cache():
    """清空快取"""
    _file_cache.clear()
    logging.info("[file_cache] 🧹 快取已清空")


def get_cache_stats() -> dict:
    """獲取快取統計"""
    return {
        "size": len(_file_cache),
        "keys": list(_file_cache.keys()),
        "max_age": _cache_max_age,
        "max_size": _cache_max_size
    }
