# -*- coding: utf-8 -*-
"""
插話補丁 (interject_patch)
------------------------------------------------------------
讓 MOKAGI 網頁介面能像 VS Code / DeepSeek 的 AI 那樣：
「AI 工作中時，輸入框仍可正常輸入並送出；送出的訊息會被併入
 當前的工作輪當作額外參考，繼續原本的工作。」
且【不直接修改 mokagi.py】。

原理
----
mokagi.process_message 的工具循環長這樣：
    messages = [system, user]
    for iteration in range(max_iterations):
        stream_gen = await call_llm(messages=messages, ...)
        ...
        messages.append(assistant_msg)
        ...（有 tool_calls 就繼續下一輪）

messages 是「同一個 list 物件」，以參照傳給 call_llm。
本補丁用 monkey-patch 包住 mokagi.call_llm：每次 LLM 呼叫前，
先檢查該 agent 的「插話佇列」，把使用者中途送出的訊息 append 進
messages，於是【下一輪】LLM 就會把這些補充當成最新使用者指示來參考，
自然地繼續原本工作 —— 不需重新發送、不重複計費、不打斷當前輪。

Flask 路由
----------
  POST api 路徑 chat 插話端點   body: {"agent","message","user_id"}
       - 該 agent 正在工作 → 排入插話佇列，回 {"ok":true,"mode":"injected","pending":n}
       - 沒有在跑       → 回 {"ok":true,"mode":"new"}（前端改走正常啟動端點）
  GET  api 路徑 chat 插話狀態端點?agent=...
       - 回傳待消費插話數與累計已注入次數

安全
----
  - 全程例外保護，載入或呼叫失敗都不影響原本功能。
  - 插話佇列有上限（_MAX_QUEUE）與 TTL，避免無限累積。
  - 單次 LLM 呼叫最多注入 _MAX_INJECT_PER_CALL 則，避免一次塞爆。
"""
import time
import threading

_LOCK = threading.Lock()
_QUEUE = {}        # {agent: [ {"text":..., "ts":..., "id":..., "user_id":...} ]}
_CONSUMED = {}     # {agent: int} 累計已注入則數（統計）
_MAX_QUEUE = 20
_TTL_SEC = 600
_MAX_INJECT_PER_CALL = 5


def _norm(a):
    return a or ""


def _prune_locked(agent):
    """清掉過期插話（需在持有 _LOCK 時呼叫）。"""
    now = time.time()
    q = _QUEUE.get(agent)
    if not q:
        return
    keep = [it for it in q if (now - it.get("ts", now)) <= _TTL_SEC]
    if keep:
        _QUEUE[agent] = keep
    else:
        _QUEUE.pop(agent, None)


def enqueue(agent, text, user_id=""):
    """把一則插話排入佇列，回傳目前待消費數量。"""
    a = _norm(agent)
    t = (text or "").strip()
    if not t:
        return 0
    with _LOCK:
        _prune_locked(a)
        q = _QUEUE.setdefault(a, [])
        q.append({"text": t, "ts": time.time(),
                  "id": int(time.time() * 1000), "user_id": user_id or ""})
        if len(q) > _MAX_QUEUE:
            del q[:len(q) - _MAX_QUEUE]
        return len(q)


def peek_count(agent):
    a = _norm(agent)
    with _LOCK:
        _prune_locked(a)
        return len(_QUEUE.get(a, []))


def drain(agent, limit=_MAX_INJECT_PER_CALL):
    """取出並移除最多 limit 則插話，回傳文字列表。"""
    a = _norm(agent)
    with _LOCK:
        _prune_locked(a)
        q = _QUEUE.get(a)
        if not q:
            return []
        take = q[:limit]
        rest = q[limit:]
        if rest:
            _QUEUE[a] = rest
        else:
            _QUEUE.pop(a, None)
        _CONSUMED[a] = _CONSUMED.get(a, 0) + len(take)
    return [it.get("text", "") for it in take]


def pop_leftover(agent):
    """取出該 agent 全部殘留插話（用於本輪結束後自動續開一輪）。"""
    a = _norm(agent)
    with _LOCK:
        _prune_locked(a)
        q = _QUEUE.pop(a, None) or []
    return [it.get("text", "") for it in q]


def has_pending(agent):
    return peek_count(agent) > 0


def _agent_of(agent_config):
    try:
        if isinstance(agent_config, dict):
            return agent_config.get("MOK_AGENT_NAME") or ""
    except Exception:
        pass
    return ""


def _inject_into_messages(messages, agent):
    """把插話 append 進 messages（role=user），回傳實際注入的文字列表。"""
    if not agent or not isinstance(messages, list):
        return []
    texts = drain(agent)
    for t in texts:
        try:
            messages.append({
                "role": "user",
                "content": ("【主人中途補充，請納入參考並繼續原本的工作；"
                            "已完成的部分不要重做】\n" + t),
            })
        except Exception:
            pass
    return texts


_INSTALLED = [False]


def install_call_llm_hook():
    """monkey-patch mokagi.call_llm：每次呼叫前把插話 append 進 messages。"""
    if _INSTALLED[0]:
        return True
    try:
        import mokagi
        orig = getattr(mokagi, "call_llm", None)
        if orig is None:
            print("[interject_patch] mokagi.call_llm 不存在，略過 hook")
            _INSTALLED[0] = True
            return False
        if getattr(orig, "_interject_wrapped", False):
            _INSTALLED[0] = True
            return True

        async def wrapped(*args, **kwargs):
            try:
                msgs = kwargs.get("messages")
                if msgs is None and len(args) >= 6:
                    # call_llm(prompt,user_id,system_prompt,stream,tools_def,messages,...)
                    msgs = args[5]
                agent = _agent_of(kwargs.get("agent_config"))
                if not agent:
                    agent = _agent_of(getattr(mokagi, "_agent_config", None))
                if agent and isinstance(msgs, list):
                    injected = _inject_into_messages(msgs, agent)
                    if injected:
                        print("[interject_patch] injected %d msg(s) into agent=%s"
                              % (len(injected), agent))
            except Exception as _e:
                print("[interject_patch] hook error:", _e)
            return await orig(*args, **kwargs)

        wrapped._interject_wrapped = True
        mokagi.call_llm = wrapped
        _INSTALLED[0] = True
        print("[interject_patch] mokagi.call_llm hooked")
        return True
    except Exception as e:
        print("[interject_patch] install_call_llm_hook failed:", e)
        return False


def register_routes(app, is_running_fn=None):
    """把插話端點註冊到 Flask app。is_running_fn(agent)->bool 判斷是否工作中。"""
    from flask import request, jsonify

    def _running(a):
        try:
            return bool(is_running_fn(a)) if callable(is_running_fn) else False
        except Exception:
            return False

    @app.route("/api/chat/interject", methods=["POST"])
    def _mok_interject():
        data = request.get_json(force=True, silent=True) or {}
        agent = _norm(data.get("agent"))
        msg = (data.get("message") or "").strip()
        user_id = data.get("user_id") or ""
        if not agent or not msg:
            return jsonify({"ok": False, "error": "empty agent or message"}), 400
        if _running(agent):
            n = enqueue(agent, msg, user_id=user_id)
            print("[interject_patch] queued agent=%s pending=%d msg=%s"
                  % (agent, n, msg[:40]))
            return jsonify({"ok": True, "mode": "injected", "pending": n, "active": True})
        return jsonify({"ok": True, "mode": "new", "pending": 0, "active": False})

    @app.route("/api/chat/interject/status", methods=["GET"])
    def _mok_interject_status():
        agent = request.args.get("agent")
        return jsonify({"ok": True,
                        "pending": peek_count(agent),
                        "consumed": _CONSUMED.get(_norm(agent), 0)})

    print("[interject_patch] routes registered: /api/chat/interject")
    return app
