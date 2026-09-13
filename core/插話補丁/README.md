# 插話補丁（interject_patch）

仿 VS Code 的 AI 與 DeepSeek：**AI 工作中時，輸入框仍可正常輸入並送出；
送出的訊息會被併入當前工作輪當作額外參考，繼續原本的工作。**
**不修改 `mokagi.py`**（比照 暫停補丁、502補丁 的加掛式作法）。

## 要解決的問題
原本 `main.js` 的 `showWorkingIndicator()` 會在 agent 工作中時把整個輸入框
`chatInputWrapper` 隱藏，使用者只能乾等，也無法中途補充資訊。

## 原理（關鍵：messages 以參照傳遞）
`mokagi.process_message` 的工具循環：

```
messages = [system, user]
for iteration in range(max_iterations):
    stream_gen = await call_llm(messages=messages, ...)   # 同一個 list 物件
    ... messages.append(assistant_msg)
    ...（有 tool_calls 就繼續下一輪）
```

本補丁用 monkey-patch 包住 `mokagi.call_llm`：**每次 LLM 呼叫前**先看該 agent
的插話佇列，把使用者中途送出的訊息 `append` 進 `messages`（role=user）。
因此下一輪 LLM 就會看到「主人中途補充」並納入參考，自然繼續原工作
—— 不重新發送、不重複計費、不打斷當前輪。

## 檔案
| 檔案 | 作用 |
|---|---|
| `interject_patch.py` | 後端：插話佇列 + `call_llm` hook + Flask 路由 |
| `__init__.py` | 套件標記 |
| `README.md` | 本文件 |
| `backup` 目錄 | 動工前原檔備份（mok_web.py.orig、main.js.orig、style.css.orig） |

## 端點
| Method | Path | 作用 |
|---|---|---|
| POST | /api/chat/interject | `{agent,message,user_id}`；工作中→`mode:"injected"`，否則→`mode:"new"` |
| GET | /api/chat/interject/status?agent= | 待消費插話數與累計已注入數 |

## 接線（僅動 `frontends/mok_web.py` 兩處，皆 try 保護）
1. `_running_agents = set()` 之後：
   - import `interject_patch`、`install_call_llm_hook()`、
     `register_routes(app, is_running_fn=lambda a: a in _running_agents)`。
2. `_sse_bg_worker` 的 `finally`：本輪結束時若仍有殘留插話
   （`pop_leftover`），自動以該文字續開一輪（呼叫既有 `_start_sse_chat_session`）。

## 前端（直接改 `html/static/main.js`、`html/static/style.css`）
- `showWorkingIndicator()`：不再隱藏 `chatInputWrapper`，輸入框保持可用；
  顯示精簡「工作中」橫幅（`.working-indicator.compact`，隱藏 3D 場景）。
- `sendUserMessage()`：若該 agent 正在工作 → 走插話端點；
  回 `mode:"new"` 才 fallback 走正常新一輪。
- `done` 事件：若本輪有插話，延遲呼叫 `resumeActiveSessionForAgent()` 續接
  自動續開的新一輪。

## 調校
- `_MAX_QUEUE`、`_TTL_SEC`、`_MAX_INJECT_PER_CALL`：佇列上限、過期秒數、單次注入則數。
- 想恢復原本「工作中隱藏輸入框」：把 `main.js` 的 `showWorkingIndicator()` 裡
  `inputWrapper` 那行改回 `display:none` 即可。
