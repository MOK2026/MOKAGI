#!/usr/bin/env python3
"""
補全 chat_history 表中舊消息的 conv_id 字段。
匹配依據：agent 名稱 + 用戶消息內容 + 時間戳（相差 <= 3 秒）
用法：直接運行腳本


"""

import sqlite3
import os
import sys
from contextlib import closing

# 配置 —— 如果您的 MOKAGI_home 不是 "mok"，請修改此處
MOKAGI_home = "mok"

# 數據庫路徑
CONV_HISTORY_DB = os.path.expanduser(f"~/.{MOKAGI_home}/.memory/conversation_history.db")
CHAT_HISTORY_DB = os.path.expanduser(f"~/.{MOKAGI_home}/.memory/chat_history.db")


def get_all_agents_from_chat():
    """從 chat_history 中獲取所有不同的 agent 名稱"""
    if not os.path.exists(CHAT_HISTORY_DB):
        print("❌ chat_history.db 不存在，請檢查路徑。")
        sys.exit(1)

    with closing(sqlite3.connect(CHAT_HISTORY_DB)) as conn:
        cursor = conn.execute("SELECT DISTINCT agent FROM chat_history")
        agents = [row[0] for row in cursor.fetchall()]
    return agents


def get_conversation_user_records():
    """從 conversation_history 中獲取所有 role='user' 的記錄"""
    if not os.path.exists(CONV_HISTORY_DB):
        print("❌ conversation_history.db 不存在，請檢查路徑。")
        sys.exit(1)

    with closing(sqlite3.connect(CONV_HISTORY_DB)) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT id, user_key, content, timestamp FROM conversation_history WHERE role='user' ORDER BY timestamp"
        ).fetchall()
        return [dict(row) for row in rows]


def update_conv_ids():
    """主更新函數"""
    agents = get_all_agents_from_chat()
    if not agents:
        print("⚠️ chat_history 表中沒有記錄，無需處理。")
        return

    conv_users = get_conversation_user_records()
    if not conv_users:
        print("⚠️ conversation_history 表中沒有 user 記錄，無法匹配。")
        return

    print(f"📋 找到 {len(agents)} 個 Agent，{len(conv_users)} 條用戶對話記錄。")

    # 建立連接並開啟事務
    chat_conn = sqlite3.connect(CHAT_HISTORY_DB)
    chat_conn.row_factory = sqlite3.Row
    chat_cursor = chat_conn.cursor()

    # 為了加速匹配，按 agent 對 conv_users 分組
    conv_by_agent = {}
    for rec in conv_users:
        # user_key 格式為 f"{user_id}_{agent_name}"，取最後一部分作為 agent
        key_parts = rec['user_key'].split('_')
        if len(key_parts) >= 2:
            agent_candidate = key_parts[-1]
            conv_by_agent.setdefault(agent_candidate, []).append(rec)
        else:
            # 兼容舊格式，跳過
            continue

    total_updated = 0
    for agent in agents:
        print(f"\n🔍 處理 Agent: {agent}")
        if agent not in conv_by_agent:
            print(f"   ⚠️ 在 conversation_history 中未找到 agent '{agent}' 的記錄，跳過。")
            continue

        conv_list = conv_by_agent[agent]
        # 按時間排序（已有序）
        # 獲取該 agent 的 chat 消息，按時間升序
        chat_cursor.execute(
            "SELECT id, role, content, timestamp FROM chat_history WHERE agent=? ORDER BY timestamp",
            (agent,)
        )
        chat_msgs = [dict(row) for row in chat_cursor.fetchall()]
        if not chat_msgs:
            continue

        i = 0
        while i < len(chat_msgs):
            msg = chat_msgs[i]
            if msg['role'] != 'user':
                # 只處理 user 消息，assistant 會被配對處理
                i += 1
                continue

            # 嘗試匹配 conv_list 中的記錄
            matched = None
            for conv in conv_list:
                # 條件：內容完全一致，時間差 <= 3 秒
                if conv['content'] == msg['content'] and abs(conv['timestamp'] - msg['timestamp']) <= 3.0:
                    matched = conv
                    break

            if matched:
                conv_id = matched['id']
                # 更新當前 user 消息
                chat_cursor.execute(
                    "UPDATE chat_history SET conv_id=? WHERE id=?",
                    (conv_id, msg['id'])
                )
                total_updated += 1

                # 檢查下一條是否為 assistant
                if i + 1 < len(chat_msgs) and chat_msgs[i + 1]['role'] == 'assistant':
                    assistant_msg = chat_msgs[i + 1]
                    chat_cursor.execute(
                        "UPDATE chat_history SET conv_id=? WHERE id=?",
                        (conv_id, assistant_msg['id'])
                    )
                    total_updated += 1
                    i += 2  # 跳過 assistant
                    continue
                else:
                    i += 1
            else:
                # 未匹配，跳過此 user，繼續下一輪
                i += 1

        chat_conn.commit()
        print(f"   ✅ 完成，共更新 {total_updated} 條消息")

    chat_conn.close()
    print(f"\n🎉 全部處理完成，總共更新 {total_updated} 條消息。")


if __name__ == "__main__":
    update_conv_ids()