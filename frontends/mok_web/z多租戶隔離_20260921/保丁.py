# -*- coding: utf-8 -*-
"""
多租戶即時隔離補丁 v1.0  (2026-09-21)
=====================================
現象：A 用戶送出訊息，B 用戶會看到同一 agent「工作中」，甚至直接接續 A 的串流、
      看到 A 的思考與回答；B 的補充輸入還可能被注入 A 的對話。

根因：即時層（SSE session 狀態）是全體共用，讀取端未按「用戶」過濾：
  1. GET /api/chat/active?agent=X   只依 agent 過濾 -> B 開頁就 resume A 的 session
  2. GET /api/chat/stream/<sid>     完全不驗身分
  3. GET /api/env_files 的 is_running 用全域 _running_agents -> 徽章洩漏
  4. POST /api/chat/interject 佇列只按 agent 排，user_id 由前端自填

本補丁不改核心，載入時以 monkey patch 包裝上述視圖與插話佇列，
一律改以 tenant（登入會員帳號 / 訪客 id）為隔離鍵。
"""
import sys, json, time, contextvars

main = sys.modules['__main__']

# 目前執行中的 tenant（由 process_message 包裝設定，供插話佇列取用）
_TENANT_CTX = contextvars.ContextVar('mok_tenant_ctx', default=None)


def _log(m):
    try:
        print('[多租戶隔離] ' + m)
    except Exception:
        pass


def _tenant():
    try:
        return main.resolve_tenant_arg()
    except Exception:
        return None


def _tenant_body(data):
    try:
        return main.resolve_tenant(data)
    except Exception:
        return None


def _running_agents_for(tenant):
    """該 tenant 目前真正在跑的 agent 集合（以 SSE session 為權威來源）。"""
    out = set()
    if tenant is None:
        return out
    try:
        with main._sse_lock:
            for sid, ag in main._sse_agents.items():
                if main._sse_done.get(sid, False):
                    continue
                if main._sse_users.get(sid) == tenant:
                    out.add(ag)
    except Exception:
        pass
    return out


# ============ 1) /api/chat/active：只回「自己」的進行中 session ============
def api_chat_active():
    agent = main.request.args.get('agent', '').strip()
    tenant = _tenant()
    if tenant is None:
        return main.jsonify({'ok': True, 'sessions': []})
    with main._sse_lock:
        sessions = []
        for sid, ag in main._sse_agents.items():
            if agent and ag != agent:
                continue
            if main._sse_done.get(sid, False):
                continue
            if main._sse_users.get(sid) != tenant:
                continue                      # ★ 關鍵：跳過別人的 session
            buf = main._sse_buffers.get(sid, [])
            _agg = main._sse_agg.get(sid)
            item = {'session_id': sid, 'agent': ag, 'buffered': len(buf)}
            if _agg:
                try:
                    item['state'] = {
                        'rounds': _agg.get('rounds') or [],
                        'think': _agg.get('think', ''),
                        'reply': _agg.get('reply', ''),
                        'n': _agg.get('n', len(buf)),
                    }
                except Exception:
                    pass
            sessions.append(item)
    sessions.sort(key=lambda s: s['buffered'])
    return main.jsonify({'ok': True, 'sessions': sessions})


# ============ 2) /api/chat/stream/<sid>：驗身分，不是自己的就不給接 ============
_orig_stream = None
_orig_active = None
_orig_env_api = None
try:
    _orig_stream = main.app.view_functions.get('api_chat_stream_sse')
    _orig_active = main.app.view_functions.get('api_chat_active')
    _orig_env_api = main.app.view_functions.get('get_env_files_api')
except Exception as _e:
    _log('取得原始視圖失敗: %s' % _e)


def api_chat_stream_sse(session_id):
    tenant = _tenant()
    owner = None
    try:
        with main._sse_lock:
            owner = main._sse_users.get(session_id)
    except Exception:
        owner = None
    if owner is not None and tenant != owner:
        def _deny():
            yield ': stream-open\n\n'
            yield 'data: ' + json.dumps({'type': 'stream_meta', 'sse_session_id': session_id,
                                         'expired': True, 'denied': True}, ensure_ascii=False) + '\n\n'
            yield 'data: ' + json.dumps({'type': 'done', 'sse_session_id': session_id,
                                         'expired': True}, ensure_ascii=False) + '\n\n'
        return main.Response(_deny(), mimetype='text/event-stream', headers={
            'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no', 'Connection': 'keep-alive'})
    if _orig_stream is None:
        return main.Response('', mimetype='text/event-stream')
    return _orig_stream(session_id)


# ============ 3) /api/env_files：is_running 改成只看自己的 session ============
def get_env_files_api():
    data = _orig_env_api() if _orig_env_api is not None else {'agents': [], 'current': ''}
    try:
        running = _running_agents_for(_tenant())
        if isinstance(data, dict):
            for a in data.get('agents', []):
                a['is_running'] = a.get('name') in running
    except Exception as e:
        _log('is_running 過濾失敗: %s' % e)
    return data


def _install_view(name, fn):
    try:
        if name in main.app.view_functions:
            main.app.view_functions[name] = fn
            return True
    except Exception as e:
        _log('安裝視圖 %s 失敗: %s' % (name, e))
    return False


_install_view('api_chat_active', api_chat_active)
_install_view('api_chat_stream_sse', api_chat_stream_sse)
_install_view('get_env_files_api', get_env_files_api)


# ============ 4) 插話佇列：改以 (agent, 用戶) 為鍵 ============
try:
    import interject_patch as _ij
except Exception:
    _ij = None


def _ukey(agent, user_id):
    return (agent or '') + '\x00' + (user_id or '')


if _ij is not None:
    def _enqueue(agent, text, user_id=''):
        a = _ij._norm(agent)
        t = (text or '').strip()
        if not t:
            return 0
        k = _ukey(a, user_id)
        with _ij._LOCK:
            _ij._prune_locked(k)
            q = _ij._QUEUE.setdefault(k, [])
            q.append({'text': t, 'ts': time.time(), 'id': int(time.time() * 1000),
                      'user_id': user_id or ''})
            if len(q) > _ij._MAX_QUEUE:
                del q[:len(q) - _ij._MAX_QUEUE]
            return len(q)

    def _drain(agent, limit=None):
        if limit is None:
            limit = _ij._MAX_INJECT_PER_CALL
        k = _ukey(_ij._norm(agent), _TENANT_CTX.get())
        with _ij._LOCK:
            _ij._prune_locked(k)
            q = _ij._QUEUE.get(k)
            if not q:
                return []
            take = q[:limit]
            rest = q[limit:]
            if rest:
                _ij._QUEUE[k] = rest
            else:
                _ij._QUEUE.pop(k, None)
            _ij._CONSUMED[k] = _ij._CONSUMED.get(k, 0) + len(take)
        return [it.get('text', '') for it in take]

    def _pop_leftover(agent):
        k = _ukey(_ij._norm(agent), _TENANT_CTX.get())
        with _ij._LOCK:
            _ij._prune_locked(k)
            q = _ij._QUEUE.pop(k, None) or []
        return [it.get('text', '') for it in q]

    def _peek_count(agent):
        a = _ij._norm(agent)
        uid = _TENANT_CTX.get()
        with _ij._LOCK:
            if uid is None:
                total = 0
                for k in list(_ij._QUEUE.keys()):
                    if k == a or k.startswith(a + '\x00'):
                        _ij._prune_locked(k)
                        total += len(_ij._QUEUE.get(k, []))
                return total
            k = _ukey(a, uid)
            _ij._prune_locked(k)
            return len(_ij._QUEUE.get(k, []))

    _ij.enqueue = _enqueue
    _ij.drain = _drain
    _ij.pop_leftover = _pop_leftover
    _ij.peek_count = _peek_count
    _ij.has_pending = lambda agent: _peek_count(agent) > 0

    # 插話端點：身分一律取 session（不信任前端自填的 user_id），且只允許「自己」的進行中會話
    def _mok_interject():
        try:
            data = main.request.get_json(force=True, silent=True) or {}
        except Exception:
            data = {}
        agent = (data.get('agent') or '').strip()
        msg = (data.get('message') or '').strip()
        if not agent or not msg:
            return main.jsonify({'ok': False, 'error': 'empty agent or message'}), 400
        tenant = _tenant_body(data)
        if tenant is None:
            return main.jsonify({'ok': False, 'error': 'unauthorized'}), 401
        if agent not in _running_agents_for(tenant):
            return main.jsonify({'ok': False, 'error': '該 agent 未在你的會話中執行'}), 409
        n = _enqueue(agent, msg, tenant)
        return main.jsonify({'ok': True, 'queued': n})

    _install_view('_mok_interject', _mok_interject)

    # 讓 call_llm 注入時知道「現在是哪個用戶」：包裝 process_message 設定 contextvar
    try:
        import mokagi as _mk
        _orig_pm = _mk.process_message

        async def _pm_ctx(*args, **kwargs):
            uid = kwargs.get('user_id')
            if uid is None and len(args) > 0:
                uid = args[0]
            tok = _TENANT_CTX.set(uid)
            try:
                return await _orig_pm(*args, **kwargs)
            finally:
                try:
                    _TENANT_CTX.reset(tok)
                except Exception:
                    pass

        _mk.process_message = _pm_ctx
        if hasattr(main, 'process_message'):
            main.process_message = _pm_ctx
    except Exception as e:
        _log('process_message 包裝失敗（插話隔離降級）: %s' % e)

_log('✅ 即時層隔離補丁已載入（chat/active、chat/stream、env_files.is_running、interject）')
