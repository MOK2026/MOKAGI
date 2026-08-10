"""
Gunicorn 配置 - mok_web.py
使用 eventlet worker 實現並行 I/O，支援多用戶同時訪問與 Ollama 多工並行

【架構說明】
- worker_class = eventlet：利用協程（green thread）實現非阻塞 I/O 並行
- workers = 1：SocketIO 需要 sticky session，暫用單 worker
  但 eventlet 的協程模型讓單 worker 可以同時處理數百個並行連線
- 每個連線等待 Ollama 回應時，eventlet 會自動切換到其他連線
- timeout = 600s：給 Ollama 長回應足夠時間
"""
import os

# === Worker 配置 ===
worker_class = 'eventlet'
workers = 1                # SocketIO sticky session，單 worker + eventlet 協程並行
worker_connections = 1000  # 最大並行連線數
threads = 1                # eventlet 不需要多線程

# === 綁定 ===
bind = '127.0.0.1:5000'

# === 超時 ===
timeout = 600              # 10 分鐘，Ollama 大型模型回應可能很慢
graceful_timeout = 30
keepalive = 5

# === 日誌 ===
accesslog = '-'
errorlog = '-'
loglevel = 'info'

# === 進程 ===
proc_name = 'mok_web_gunicorn'

# === 環境變數 ===
os.environ.setdefault("MOKAGI_HOME", "mok")
