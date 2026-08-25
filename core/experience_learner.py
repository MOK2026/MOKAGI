"""experience_learner.py - 經驗學習模組"""
import json
import logging
import os
import re
import sqlite3
import time
from contextlib import closing
from typing import Dict, List, Optional

from shared import MOKAGI_home, EXPERIENCE_DB_PATH

def _init_experience_db():
    """初始化經驗記錄表與 FTS5 虛擬表"""
    with closing(sqlite3.connect(EXPERIENCE_DB_PATH, timeout=10.0)) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS experience_log (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_key TEXT,
                agent_name TEXT,
                goal TEXT,
                outcome TEXT,
                tool_sequence TEXT,
                error_message TEXT,
                summary TEXT,
                keywords TEXT,
                timestamp REAL
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_exp_agent ON experience_log (agent_name)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_exp_outcome ON experience_log (outcome)')
        conn.execute('''
            CREATE VIRTUAL TABLE IF NOT EXISTS experience_fts USING fts5(
                goal, summary, keywords, error_message,
                content='experience_log', content_rowid='id'
            )
        ''')

def log_experience(
    user_id: str,
    agent_name: str,
    goal: str,
    outcome: str,
    messages: list,
    error_message: str = "",
    keywords: str = "",
    agent_config: Dict = None
):
    """記錄一次經驗"""
    _init_experience_db()
    user_key = f"{agent_name}:{user_id}"
    tool_sequence = json.dumps([m for m in messages if isinstance(m, dict) and 'tool' in m], ensure_ascii=False)
    try:
        with closing(sqlite3.connect(EXPERIENCE_DB_PATH)) as conn:
            conn.execute('''
                INSERT INTO experience_log (user_key, agent_name, goal, outcome, tool_sequence, error_message, summary, keywords, timestamp)
                VALUES (?,?,?,?,?,?,?,?,?)
            ''', (user_key, agent_name, goal, outcome, tool_sequence, error_message[:500], goal[:200], keywords, time.time()))
            conn.commit()
            last_id = conn.execute('SELECT last_insert_rowid()').fetchone()[0]
            conn.execute('''
                INSERT INTO experience_fts (rowid, goal, summary, keywords, error_message)
                VALUES (?,?,?,?,?)
            ''', (last_id, goal[:200], goal[:200], keywords[:200], error_message[:200]))
            conn.commit()
    except Exception as e:
        logging.warning(f"記錄經驗失敗: {e}")

def recall_experience(
    goal: str,
    agent_name: str = None,
    limit: int = 5
) -> List[Dict]:
    """回憶相關經驗"""
    # FTS5 安全處理：避免語法錯誤（如 fts5: syntax error near "."）
    _safe = re.sub(r'[^\w\u4e00-\u9fff\s]', ' ', goal or '', flags=re.UNICODE)
    _tokens = [t for t in _safe.split() if t]
    goal = ' '.join(f'"{t}"' for t in _tokens) if _tokens else ''
    if not goal:
        return []
    _init_experience_db()
    try:
        with closing(sqlite3.connect(EXPERIENCE_DB_PATH)) as conn:
            conn.row_factory = sqlite3.Row
            if agent_name:
                rows = conn.execute('''
                    SELECT e.* FROM experience_log e
                    JOIN experience_fts f ON e.id = f.rowid
                    WHERE experience_fts MATCH ? AND e.agent_name = ?
                    ORDER BY e.timestamp DESC LIMIT ?
                ''', (goal, agent_name, limit)).fetchall()
            else:
                rows = conn.execute('''
                    SELECT e.* FROM experience_log e
                    JOIN experience_fts f ON e.id = f.rowid
                    WHERE experience_fts MATCH ?
                    ORDER BY e.timestamp DESC LIMIT ?
                ''', (goal, limit)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logging.warning(f"回憶經驗失敗: {e}")
        return []
