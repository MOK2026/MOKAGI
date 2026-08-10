"""history_manager.py - 對話歷史記錄管理"""
import json
import logging
import os
import sqlite3
import time
from contextlib import closing
from datetime import datetime, timezone, timedelta
from typing import Dict, List, Optional, Tuple

from shared import MOKAGI_home, HISTORY_DB_PATH

def _init_history_db():
    """創建對話歷史表，啟用 WAL 模式"""
    with closing(sqlite3.connect(HISTORY_DB_PATH, timeout=10.0)) as conn:
        conn.execute('PRAGMA journal_mode=WAL')
        conn.execute('PRAGMA busy_timeout = 5000')
        conn.execute('''
            CREATE TABLE IF NOT EXISTS conversation_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_key TEXT NOT NULL,
                role TEXT NOT NULL,
                content TEXT,
                timestamp REAL NOT NULL
            )
        ''')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_user_key ON conversation_history (user_key)')
        conn.execute('CREATE INDEX IF NOT EXISTS idx_timestamp ON conversation_history (timestamp)')
        try:
            conn.execute('ALTER TABLE conversation_history ADD COLUMN summary TEXT')
        except sqlite3.OperationalError:
            pass
        try:
            conn.execute('ALTER TABLE conversation_history ADD COLUMN keywords TEXT')
        except sqlite3.OperationalError:
            pass

def get_user_history(user_id: str, limit: int = None, agent_name: str = None) -> List[Dict]:
    """獲取用戶對話歷史"""
    _init_history_db()
    user_key = f"{agent_name or 'default'}:{user_id}"
    with closing(sqlite3.connect(HISTORY_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        if limit:
            rows = conn.execute(
                'SELECT * FROM conversation_history WHERE user_key=? ORDER BY timestamp DESC LIMIT ?',
                (user_key, limit)
            ).fetchall()
        else:
            rows = conn.execute(
                'SELECT * FROM conversation_history WHERE user_key=? ORDER BY timestamp DESC',
                (user_key,)
            ).fetchall()
    return [dict(r) for r in rows]

async def add_to_history(user_id: str, user_msg: str, assistant_reply: str, agent_config: Dict = None):
    """添加一輪對話到歷史記錄"""
    _init_history_db()
    user_key = f"{(agent_config or {}).get('MOK_AGENT_NAME', 'default')}:{user_id}"
    now = time.time()
    with closing(sqlite3.connect(HISTORY_DB_PATH)) as conn:
        conn.execute(
            'INSERT INTO conversation_history (user_key, role, content, timestamp) VALUES (?,?,?,?)',
            (user_key, "user", user_msg, now)
        )
        conn.execute(
            'INSERT INTO conversation_history (user_key, role, content, timestamp) VALUES (?,?,?,?)',
            (user_key, "assistant", assistant_reply, now)
        )
        conn.commit()

def clear_history(user_id: str, agent_name: str = None):
    """清除用戶對話歷史"""
    _init_history_db()
    user_key = f"{agent_name or 'default'}:{user_id}"
    with closing(sqlite3.connect(HISTORY_DB_PATH)) as conn:
        conn.execute('DELETE FROM conversation_history WHERE user_key=?', (user_key,))
        conn.commit()

def get_all_conversation_summary(user_id: str, agent_config: Dict = None) -> str:
    """獲取所有對話的摘要"""
    _init_history_db()
    user_key = f"{(agent_config or {}).get('MOK_AGENT_NAME', 'default')}:{user_id}"
    with closing(sqlite3.connect(HISTORY_DB_PATH)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            'SELECT * FROM conversation_history WHERE user_key=? ORDER BY timestamp ASC',
            (user_key,)
        ).fetchall()
    if not rows:
        return "沒有找到任何對話記錄。"

    pairs_with_id = []
    i = 0
    while i < len(rows):
        if rows[i]['role'] == 'user' and i+1 < len(rows) and rows[i+1]['role'] == 'assistant':
            pairs_with_id.append({
                'user_rowid': rows[i]['id'],
                'user': rows[i]['content'],
                'assistant': rows[i+1]['content'],
                'summary': rows[i].get('summary'),
                'keywords': rows[i].get('keywords')
            })
            i += 2
        else:
            i += 1
    if not pairs_with_id:
        return "沒有找到任何對話記錄。"
    lines = []
    for idx, pair in enumerate(pairs_with_id, 1):
        user_preview = pair['user'][:80].replace('\n', ' ')
        assistant_preview = pair['assistant'][:80].replace('\n', ' ')
        if len(pair['user']) > 80:
            user_preview += "..."
        if len(pair['assistant']) > 80:
            assistant_preview += "..."
        lines.append(f"【{idx}】 (ID:{pair['user_rowid']}) 用戶: {user_preview}\n   回應: {assistant_preview}")
    return "\n\n".join(lines)
