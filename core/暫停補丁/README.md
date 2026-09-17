# 暫停補丁（pause_patch）

仿 ChatGPT／DeepSeek 的「暫停 / 繼續」輸出功能。**不修改 `mokagi.py`**。

## 檔案
- `pause_patch.py`：後端暫停狀態 + Flask 路由（`/api/chat/pause`）。
- `__init__.py`：空套件標記。

## 接線（僅動 `frontends/mok_web.py`，兩處）
1. 在 `@app.context_processor` 之前載入並註冊路由：
   ```python
   try:
       import sys as _sys, os as _os
       _pp_dir = _os.path.join(_os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))), 'core', '暫停補丁')
       if _os.path.isdir(_pp_dir) and _pp_dir not in _sys.path:
           _sys.path.insert(0, _pp_dir)
       import pause_patch
       pause_patch.register_routes(app)
   except Exception as _e:
       print('[pause_patch] load failed:', _e)
   ```
2. 在 `async_stream_cb(event)` 最前面：
   ```python
   try:
       await pause_patch.wait_if_paused(agent_name)
   except Exception:
       pass
   ```

## 前端
- `html/index.html`：輸入區按鍵列新增 `<button id="pauseBtn">`。
- `html/static/main.js`：`_onChatStream` 開頭緩衝事件、按鈕切換、重放緩衝。
- `html/static/style.css`：按鈕樣式。

## 行為
- 生成中按「⏸ 暫停」→ 後端回調阻塞、前端停止渲染，按鈕變「▶ 繼續」。
- 按「▶ 繼續」→ 先重放前端緩衝事件，再解除後端阻塞，內容續行、不重送、不重複計費。
- 最長暫停 300 秒後自動解除，避免請求永久卡死。
