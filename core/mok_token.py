#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
token.py - Token 用量統計模塊

功能：
- 記錄每次 LLM 調用的 token 用量到 SQLite 數據庫
- 提供統一的記錄函數 log_token_usage

依賴：
- config.py 中的 MOKAGI_home
- 標準庫 sqlite3, json, time, os

使用方式：
    from token import log_token_usage
    log_token_usage(user_id, agent_name, model_name, prompt_tokens, completion_tokens, total_tokens, ...)
"""

import os
import json
import time
import sqlite3
from contextlib import closing
from config import MOKAGI_home,MOK_max_tokens
# ===== 新增：Token 計數工具（供其他模組重用）=====
import importlib.util

# token 統計數據庫路徑（與 chat_history 共用數據庫）
TOKEN_DB_PATH = os.path.expanduser(f"~/.{MOKAGI_home}/.memory/chat_history.db")

def _ensure_token_table():
    """確保 token_usage 表存在"""
    with closing(sqlite3.connect(TOKEN_DB_PATH)) as conn:
        conn.execute('''
            CREATE TABLE IF NOT EXISTS token_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id TEXT,
                agent_name TEXT,
                model_name TEXT,
                conversation_id TEXT,
                workflow_id TEXT,
                prompt_tokens INTEGER,
                completion_tokens INTEGER,
                total_tokens INTEGER,
                timestamp REAL,
                extra TEXT
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_token_agent ON token_usage (agent_name)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_token_model ON token_usage (model_name)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_token_user ON token_usage (user_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_token_conversation ON token_usage (conversation_id)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_token_workflow ON token_usage (workflow_id)')
        conn.commit()

def log_token_usage(
    user_id: str,
    agent_name: str,
    model_name: str,
    prompt_tokens: int,
    completion_tokens: int,
    total_tokens: int,
    conversation_id: str = None,
    workflow_id: str = None,
    extra: dict = None
):
    """記錄單次 LLM 調用的 token 用量"""
    _ensure_token_table()
    with closing(sqlite3.connect(TOKEN_DB_PATH)) as conn:
        conn.execute(
            '''INSERT INTO token_usage
               (user_id, agent_name, model_name, conversation_id, workflow_id,
                prompt_tokens, completion_tokens, total_tokens, timestamp, extra)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
            (user_id, agent_name, model_name, conversation_id, workflow_id,
             prompt_tokens, completion_tokens, total_tokens, time.time(),
             json.dumps(extra, ensure_ascii=False) if extra else None)
        )
        conn.commit()






_TIKTOKEN_AVAILABLE = importlib.util.find_spec("tiktoken") is not None

def count_tokens(text: str, model: str = "gpt-4") -> int:
    """
    計算給定文字在指定模型下的 token 數量。
    若 tiktoken 未安裝，則使用粗略估算（1 token ≈ 4 字元）。
    """
    if not text:
        return 0
    if _TIKTOKEN_AVAILABLE:
        import tiktoken
        try:
            encoding = tiktoken.encoding_for_model(model)
        except KeyError:
            encoding = tiktoken.get_encoding("cl100k_base")  # 通用 fallback
        return len(encoding.encode(text))
    else:
        # 粗略估算：中英文混合約 1 token = 4 字元
        return len(text) // 4




def truncate_by_token(text: str, max_tokens: int = MOK_max_tokens) -> str:
    """
    若 text 的 token 數超過 max_tokens，則自動截斷並加提示。
    截斷方式：以字元數估算（4 字元 ≈ 1 token）來截斷，保證不超過上限。
    """
    if count_tokens(text) > max_tokens:
        cutoff = int(max_tokens * 4)  # 粗略估算，確保不超過
        return text[:cutoff] + "\n\n... (輸出過長，已截斷以節省 Token)"
    return text


# ═══════════════════════════════════════════════════════════
# 統一計費（唯一價格源：mok_price.py）
# ⚠️  修改收費標準只需編輯 mok_price.py，本文件無需改動
# ═══════════════════════════════════════════════════════════
def get_price() -> dict:
    """取得 MOKAGI 統一收費標準（HK$68 / 百萬 token 等）"""
    try:
        from mok_price import to_dict
        return to_dict()
    except Exception:
        return {
            "currency": "HKD",
            "price_per_million": 68,
            "price_per_token": 0.000068,
            "setup_fee": 5000,
        }


def price_per_token() -> float:
    """每 token 單價（HKD），由 mok_price.py 統一管理"""
    try:
        from mok_price import price_per_token as _ppt
        return _ppt()
    except Exception:
        return 0.000068


def cost_of_tokens(tokens: float) -> float:
    """計算指定 token 數的費用（HKD）"""
    try:
        from mok_price import cost_for_tokens
        return cost_for_tokens(tokens)
    except Exception:
        return tokens * 0.000068
