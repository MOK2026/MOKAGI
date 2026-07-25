import os, sqlite3, hashlib, time
from contextlib import closing
import sys
sys.path.insert(0, '/home/ubuntu/.mok')
sys.path.insert(0, '/home/ubuntu/.mok/tools')

import mokagi
from tools.memory import _get_conversation_collection, index_conversation

MOK_HOME = mokagi.MOKAGI_home
agent_base = os.path.expanduser(f'/home/ubuntu/.{MOK_HOME}/agent')

def rebuild_one(agent_name):
    col = _get_conversation_collection(agent_name)
    if not col:
        return f"⚠️  {agent_name} 向量索引未啟用"
    try:
        col.delete(where={"user_key": {"$exists": True}})
    except:
        pass
    history_db = mokagi.HISTORY_DB_PATH
    count = 0
    with closing(sqlite3.connect(history_db)) as conn:
        rows = conn.execute(
            "SELECT id, content, user_key FROM conversation_history WHERE role = ? ORDER BY id",
            ("user",)
        ).fetchall()
        for row in rows:
            user_key = row[2]
            if not user_key.endswith(f"_{agent_name}"):
                continue
            assistant = conn.execute(
                "SELECT content FROM conversation_history WHERE user_key = ? AND role = ? AND id > ? ORDER BY id LIMIT 1",
                (user_key, "assistant", row[0])
            ).fetchone()
            index_conversation(
                user_key, row[1], assistant[0] if assistant else "",
                row[0], row[0]+1, {"MOK_AGENT_NAME": agent_name}
            )
            count += 1
    return f"✅ 已重建 {count} 條索引"

results = []
for name in os.listdir(agent_base):
    if os.path.isdir(os.path.join(agent_base, name)):
        try:
            results.append(rebuild_one(name))
        except Exception as e:
            results.append(f"❌ {name}: {e}")
print("\n".join(results))
