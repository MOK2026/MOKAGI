# -*- coding: utf-8 -*-
"""
無縫接回補丁 v1（2026-10-03 稚）
==================================
目標：服務（launcher / web / bot）重新啟動後，把「正在生成中」的那一輪回覆
      自動從斷點寫完 —— 前端不必重送、使用者不必按「繼續」。

前置（配套）：
  1) core 端 mok_web.py 兩個生成 worker 已植入 mok_resume_hook 呼叫點。
  2) ｚｚ重啟韌性_20260929 補丁提供事件落盤、session 還原與認領介面
     （main._claim_continuation / main._finalize_continuation）。

原理：
  1) 生成中的每一輪，核心把（session_id / agent / user / user_msg /
     assistant_msg_id / 已生成正文）交給 mok_resume_hook → 本補丁節流落盤到
     ~/.mok/logs/sse_stream/<sid>.resume.json（每次最多 1 秒寫一次）。
  2) 本補丁載入後 8 秒掃描未完成的記錄：
      - 中斷在「工具已發出、還沒拿到結果」（真的懸空）-> 自動代主人送一則「繼續」進主迴圈
        （process_message，帶完整工具）把工作跑完；主迴圈會自行確認現況，不盲目重做已完成動作。
      - 中斷在工具之間（本輪用過工具、但最後一個工具已有結果）-> 安全純文字續寫。
      - 2026-10-03 晚修（B 修）：舊版把「本輪用過工具」一律擋掉，實測導致主人必須人手打「繼續」，
        體驗比修改前更差，已廢除；現在只在「最後一個工具呼叫真的沒結果」時走主迴圈自動續跑。
     - 其他情況：以（系統提示 + 本輪提問 + 已寫好的部分 + 續寫指令）重呼 LLM，
       把新吐出的 token 追加進同一條 SSE 緩衝與隊列 → 前端續流即刻接上，
       最後補一個 done；DB 同一筆 assistant 記錄就地更新（不新增訊息）。
  3) 2026-10-04（方案A｜零漏網）：懸空的工具呼叫若命中「副作用白名單」
     （admin_exec／發文／寄信／付款／刪除／租機／燒錢出片…），不自動重跑，
     改誠實收尾請主人確認 —— 見 _is_side_effect / _SIDE_EFFECT_* 常數。
停用：目錄前綴加 _ 即停用（核心的 hook 找不到補丁，自動失效、零副作用）。
"""
import sys
import os
import json
import glob
import time
import queue as _queue
import threading
import asyncio
import sqlite3

main = sys.modules.get('__main__')
if main is None or not hasattr(main, '_sse_queues'):
    raise RuntimeError('無縫接回補丁需在 mok_web 主模組下載入')

_LOG_DIR = os.path.expanduser('~/.mok/logs/sse_stream')
try:
    os.makedirs(_LOG_DIR, exist_ok=True)
except Exception:
    pass

MAX_AGE = 1800.0      # 只接回 30 分鐘內中斷的回合
START_DELAY = 8.0     # 開機後等待秒數（等 web 就緒、前端重連）
_IDLE_WAIT = 45.0     # 逾時未看到任何上游事件就放棄（避免卡死）
# 2026-10-08 三修（稚）：
_TOTAL_WAIT = 240.0   # 止血：續寫總時限（wall-clock 秒），逾時即停、誠實收尾
_FAKE_TOOL_HOLD = 400 # 治本：疑似工具 JSON/DSML 的最長緩衝字數（超過即放行）
CONTINUE_HINT = (
    '（系統訊息）你上一則回覆寫到一半時，服務因為重啟而中斷了。'
    '請直接從斷點「續寫」：接著上面最後一個字繼續寫下去，'
    '不要重複任何已經寫過的內容、不要重新開頭、不要加任何前言、'
    '也不要說「繼續」「接續」之類的話。'
    '如果上面的內容其實已經語意完整，請只輸出 <END> 四個字元。'
)
FALLBACK_TOOL = ('\n\n> ⚠️ 服務在工具執行途中重啟，為避免同一動作被重做，'
                 '這裡不自動續跑。以上為已完成的部分；請回覆「繼續」讓我接著處理。')
FALLBACK_NO_TEXT = ('\n\n> ⚠️ 服務在產生回覆時重啟（當時還沒有正文）。'
                    '請重送一次，或回覆「繼續」讓我接著處理。')
FALLBACK_FAIL = ('\n\n> ⚠️ 自動續寫沒有成功（上游沒有回應）。'
                 '以上為已完成的部分；請回覆「繼續」讓我接著寫。')
# 2026-10-03 晚（稚）：工具懸空中斷時，自動代主人送給主迴圈的「繼續」指令。
AUTO_CONTINUE_NOTE = (
    '（自動續跑）服務剛剛重啟，你上一輪的工作在「工具執行途中」被中斷了。'
    '請接著把原本的工作做完：先確認剛才那個工具是否已經跑完、產物檔案是否已經產生，'
    '再決定下一步。有副作用的動作（發文、寄信、付款、刪除）請先確認是否已完成，不要重做；'
    '沒跑完的就繼續跑。完成後直接給出最終結果。'
)

# ================= 2026-10-04 by 稚：副作用工具白名單（方案A｜零漏網） =================
# 背景（conv 257039 拍板）：工具懸空中斷時，本補丁原本「一律」代主人送繼續、帶完整工具重跑。
#   若該工具具副作用（發文／寄信／付款／刪除／租機／燒錢出片），重跑就等於「重複副作用」。
# 方案A：命中的工具不自動重跑，改誠實收尾（FALLBACK_TOOL），等主人回「繼續」再處理。
# 原則：寧可多停一次，也不要重複副作用；參數讀不出來就當成有副作用。
_SIDE_EFFECT_UNKNOWN = False   # 未列名的工具是否一律視為有副作用（True = 更嚴、也會更吵）
_ADMIN_SAFE_ACTIONS = {'htop', 'cpu', 'mode', 'logs', 'read_file'}
_ADMIN_SAFE_TOOLS = {'admin_read_file', 'admin_read_room', 'admin_htop',
                     'admin_cpu', 'admin_mode', 'admin_logs'}
# 第一層：工具名命中即攔（這類工具一被呼叫就對外／改檔）
_SIDE_EFFECT_TOOLS = {
    'tts', 'money_video', 'comic', 'mpt_llm',   # 對外送語音／vast 出片＝燒錢
    'replace_in_file', 'graphify',              # 改檔／寫檔
    'gui_agent',                                # 桌面點擊，可開 App、跑指令
}
# 第二層：看動作參數才算（其餘動作如 list / status / 讀取 → 放行，不吵主人）
_SIDE_EFFECT_ACTIONS = {
    'instagram': {'post', 'schedule', 'logout'},
    'linkedin': {'post', 'post_video', 'like', 'follow', 'comment', 'discover'},
    'browser': {'click', 'type', 'press', 'execute', 'install'},
    'memory': {'forgetall', 'delete', 'update'},
    'job': {'new', 'update', 'delete', 'run', 'handover', 'takeover', 'share'},
    'task': {'new', 'update', 'delete'},
    'skill': {'create', 'delete'},
    'cron': {'add', 'delete'},
    'backup': {'run', 'restore', 'cleanup'},
}


def _is_side_effect(name, args):
    """懸空的工具呼叫是否具副作用（True = 不自動重跑，改誠實收尾）。"""
    if not name:
        return True                     # 資訊不明 → 當成有副作用
    n = str(name)
    if isinstance(args, str):           # 有些上游把 arguments 存成 JSON 字串
        try:
            args = json.loads(args)
        except Exception:
            args = {}
    if not isinstance(args, dict):
        args = {}
    # admin 家族：admin_exec 是萬能的（發文／寄信／付款／刪除／租機／發片全在裡面）→ 一律攔。
    if n == 'admin':
        return args.get('action') not in _ADMIN_SAFE_ACTIONS
    if n.startswith('admin_'):
        return n not in _ADMIN_SAFE_TOOLS
    if n in _SIDE_EFFECT_TOOLS:
        return True
    acts = _SIDE_EFFECT_ACTIONS.get(n)
    if acts is not None:
        return args.get('action') in acts
    return bool(_SIDE_EFFECT_UNKNOWN)

_LAST_WRITE = {}
_MEM = {}


# ---------------- 一、核心回報 → 節流落盤 ----------------
def _rec_path(sid):
    return os.path.join(_LOG_DIR, sid + '.resume.json')


def _write_rec(rec):
    try:
        sid = rec.get('sid')
        if not sid:
            return
        p = _rec_path(sid)
        tmp = p + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(rec, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception as e:
        print('[接回] resume 記錄寫入失敗', e)


def mok_resume_hook(ev=None, **kw):
    """核心生成迴圈每個事件都會呼叫這裡（節流寫檔，成本可忽略）。"""
    try:
        sid = kw.get('session_id')
        if not sid:
            return
        et = (ev or {}).get('type') or ''
        rec = _MEM.get(sid)
        if rec is None:
            rec = {'sid': sid}
            _MEM[sid] = rec
        for k in ('agent', 'user_id', 'user_msg', 'user_msg_id',
                  'assistant_msg_id', 'accumulated_reply', 'accumulated_think'):
            if k in kw and kw[k] is not None:
                rec[k] = kw[k]
        rec['ts'] = time.time()
        if et == 'done':
            rec['done'] = True
        elif et == 'tool_calls':
            rec['tool_pending'] = True
        elif et == 'tool_result':
            rec['tool_pending'] = False
        force = (et in ('done', 'tool_calls', 'tool_result')) or et == ''
        now = time.time()
        if force or (now - _LAST_WRITE.get(sid, 0.0)) >= 1.0:
            _LAST_WRITE[sid] = now
            _write_rec(rec)
    except Exception:
        pass


try:
    main.mok_resume_hook = mok_resume_hook
    print('[接回] mok_resume_hook 已註冊（重啟後自動續寫已啟用）')
except Exception as e:
    print('[接回] 註冊 hook 失敗', e)


# ---------------- 二、SSE 緩衝/隊列 操作 ----------------
def _push(sid, agent, ev, agg=True):
    ev = dict(ev)
    if agent:
        ev['agent'] = agent
    buf = None
    q = None
    try:
        with main._sse_lock:
            buf = main._sse_buffers.get(sid)
            if buf is not None:
                buf.append(ev)
            q = main._sse_queues.get(sid)
    except Exception:
        q = None
    if q is not None:
        try:
            q.put(ev)
        except Exception:
            pass
    if agg:
        try:
            _sse_agg[sid] = _agg_ensure(sid)
            a = _sse_agg[sid]
            t = ev.get('type')
            if t == 'think':
                a['think'] = (a.get('think') or '') + (ev.get('content') or '')
                if a.get('rounds'):
                    a['rounds'][-1]['think'] = (a['rounds'][-1].get('think') or '') + (ev.get('content') or '')
            elif t == 'reply':
                a['reply'] = (a.get('reply') or '') + (ev.get('content') or '')
                if a.get('rounds'):
                    a['rounds'][-1]['reply'] = (a['rounds'][-1].get('reply') or '') + (ev.get('content') or '')
            try:
                a['n'] = len(main._sse_buffers.get(sid, []) or [])
                main._sse_agg_last[sid] = time.time()
            except Exception:
                pass
        except Exception:
            pass
    return ev


_sse_agg = {}


def _agg_rounds_from_buffer(sid):
    """依緩衝事件重建前端要的聚合快照（rounds/think/reply/n）。"""
    rounds = []
    think = ''
    reply = ''
    try:
        with main._sse_lock:
            buf = list(main._sse_buffers.get(sid, []) or [])
    except Exception:
        buf = []

    def _cur():
        if not rounds:
            rounds.append({'think': '', 'tool_calls': [], 'tool_results': [],
                           'reply': '', 'iteration': 1})
        return rounds[-1]

    for ev in buf:
        et = ev.get('type')
        c = ev.get('content') or ''
        if et == 'iteration_start':
            rounds.append({'think': '', 'tool_calls': [], 'tool_results': [],
                           'reply': '', 'iteration': ev.get('iteration') or (len(rounds) + 1)})
        elif et == 'think':
            _cur()['think'] += c
            think += c
        elif et == 'tool_calls':
            _cur()['tool_calls'] = ev.get('calls', [])
        elif et == 'tool_result':
            _cur()['tool_results'].append({'name': ev.get('tool_name', '未知工具'), 'content': c})
        elif et == 'reply':
            sub = ev.get('subtype', 'normal')
            if sub == 'tool_result':
                _cur()['tool_results'].append({'name': ev.get('tool_name', '工具'), 'content': c})
            elif sub == 'pending_list':
                pass
            elif sub in ('tool_process', 'semantic_search', 'experience'):
                key = {'tool_process': 'tool_process',
                       'semantic_search': 'semantic',
                       'experience': 'experience'}[sub]
                _cur()[key] = _cur().get(key, '') + c + '\n\n'
            else:
                _cur()['reply'] += c
                reply += c
    return rounds, think, reply, len(buf)


def _agg_ensure(sid):
    a = None
    try:
        a = _sse_agg.get(sid)
    except Exception:
        a = None
    if a is None:
        try:
            rounds, think, reply, n = _agg_rounds_from_buffer(sid)
        except Exception:
            rounds, think, reply, n = [], '', '', 0
        a = {'rounds': rounds, 'think': think, 'reply': reply, 'n': n}
        try:
            _sse_agg[sid] = a
        except Exception:
            pass
    return a


def _push(sid, agent, ev, agg=True):
    """把事件寫進 SSE 緩衝 + 隊列（前端續流即時收到），並同步聚合快照。"""
    ev = dict(ev)
    if agent:
        ev['agent'] = agent
    a = None
    if agg:
        try:
            a = _agg_ensure(sid)
        except Exception:
            a = None
    buf = None
    q = None
    try:
        with main._sse_lock:
            buf = main._sse_buffers.get(sid)
            if buf is not None:
                buf.append(ev)
            q = main._sse_queues.get(sid)
    except Exception:
        q = None
    if q is not None:
        try:
            q.put(ev)
        except Exception:
            pass
    if a is not None:
        try:
            t = ev.get('type')
            c = ev.get('content') or ''
            rs = a.get('rounds') or []
            if t == 'think':
                a['think'] = (a.get('think') or '') + c
                if rs:
                    rs[-1]['think'] = (rs[-1].get('think') or '') + c
            elif t == 'reply' and (ev.get('subtype', 'normal') == 'normal'):
                a['reply'] = (a.get('reply') or '') + c
                if rs:
                    rs[-1]['reply'] = (rs[-1].get('reply') or '') + c
            elif t == 'tool_calls':
                if not rs:
                    rs.append({'think': '', 'tool_calls': [], 'tool_results': [],
                               'reply': '', 'iteration': 1})
                    a['rounds'] = rs
                rs[-1]['tool_calls'] = ev.get('calls', [])
            try:
                a['n'] = len(main._sse_buffers.get(sid, []) or [])
                main._sse_agg_last[sid] = time.time()
            except Exception:
                pass
        except Exception:
            pass
    return ev


def _ensure_session(sid, rec):
    """確保本輪的 SSE 結構存在（重啟韌性已還原者不重建，只補缺）。"""
    with main._sse_lock:
        try:
            if main._sse_buffers.get(sid) is None:
                main._sse_buffers[sid] = []
        except Exception:
            pass
        for d, v in ((main._sse_agents, rec.get('agent') or ''),
                     (main._sse_users, rec.get('user_id')),
                     (main._sse_done, False)):
            try:
                d[sid] = v
            except Exception:
                pass
        try:
            if main._sse_queues.get(sid) is None:
                main._sse_queues[sid] = _queue.Queue()
        except Exception:
            pass
    try:
        _agg_ensure(sid)
        a = _sse_agg.get(sid)
        if a is not None:
            a['n'] = len(main._sse_buffers.get(sid, []) or [])
    except Exception as e:
        print('[接回] 聚合快照重建失敗', e)


# 統一使用核心那份聚合快照（/api/chat/active 讀的是同一個物件）
try:
    _sse_agg = main._sse_agg
except Exception:
    _sse_agg = {}
    try:
        main._sse_agg = _sse_agg
    except Exception:
        pass


# ---------------- 三、認領 / 收尾 / 掃描 ----------------
def _claim(sid):
    try:
        f = getattr(main, '_claim_continuation', None)
        if f:
            f(sid)
    except Exception:
        pass
    return True


def _finalize(sid, agent, text=None):
    try:
        f = getattr(main, '_finalize_continuation', None)
        if f and f(sid, text):
            return
    except Exception:
        pass
    if text:
        _push(sid, agent, {'type': 'reply', 'content': text})
    _push(sid, agent, {'type': 'done'}, agg=False)
    try:
        main._sse_done[sid] = True
    except Exception:
        pass


def _scan_records():
    out = []
    for p in glob.glob(os.path.join(_LOG_DIR, '*.resume.json')):
        try:
            with open(p, encoding='utf-8') as f:
                d = json.load(f)
        except Exception:
            continue
        if isinstance(d, dict) and d.get('sid'):
            out.append(d)
    return out


def _buffer_has_done(sid):
    try:
        with main._sse_lock:
            buf = list(main._sse_buffers.get(sid, []) or [])
        for ev in reversed(buf):
            t = ev.get('type')
            if t == 'done':
                return True
            if t in ('reply', 'think', 'tool_calls', 'tool_result'):
                return False
    except Exception:
        pass
    return False


def _superseded(sid, rec):
    """同一 (agent, user) 已有更新的回合 → 放棄接回（別污染新對話）。"""
    try:
        if main._sse_done.get(sid, False):
            return True
    except Exception:
        pass
    try:
        newest = 0.0
        for d in _scan_records():
            if d.get('sid') == sid:
                continue
            if d.get('agent') == rec.get('agent') and d.get('user_id') == rec.get('user_id'):
                newest = max(newest, float(d.get('ts') or 0))
        if newest > float(rec.get('ts') or 0):
            return True
    except Exception:
        pass
    return False


def _update_db(rec, full):
    db = getattr(main, 'DB_PATH', None)
    if not db:
        return
    try:
        conn = sqlite3.connect(db, timeout=30)
        try:
            mid = rec.get('assistant_msg_id')
            if mid:
                conn.execute('UPDATE chat_history SET content = ? WHERE id = ?', (full, mid))
            else:
                cur = conn.execute(
                    'INSERT INTO chat_history (agent, role, content, think_content, timestamp, tenant) '
                    'VALUES (?, ?, ?, ?, ?, ?)',
                    (rec.get('agent'), 'assistant', full, rec.get('accumulated_think') or '',
                     time.time(), rec.get('user_id')))
                rec['assistant_msg_id'] = cur.lastrowid
            conn.commit()
        finally:
            conn.close()
    except Exception as e:
        print('[接回] DB 更新失敗', e)


# ---------------- 四、續寫本體 ----------------
def _looks_like_fake_tool_call(t):
    '''2026-10-08 三修（治本）：續寫呼叫不帶工具（tools_def=[]），模型想用工具時
    只能把 tool call 當成文字吐出來（JSON 或 DSML 標記）。偵測到這種假工具呼叫，
    立刻中止、丟棄該段，改走主迴圈（帶完整工具）接手（等同主人親手打「繼續」）。'''
    if not t:
        return False
    if '<|DSML|' in t or '<|tool_calls' in t or '｜DSML｜' in t:
        return True
    _q = chr(34)
    if (_q + 'tool_calls' + _q) in t:
        return True
    if (_q + 'arguments' + _q) in t and ((_q + 'name' + _q) in t or (_q + 'tool' + _q) in t):
        return True
    return False


async def _gen(sid, rec):
    core = os.path.expanduser('~/.mok/core')
    if core not in sys.path:
        sys.path.insert(0, core)
    import mokagi
    agent = rec.get('agent') or ''
    uid = rec.get('user_id') or ''
    cfg = await mokagi.get_agent_config(agent)
    owner = cfg.get('MOK_ADMIN_NAME') or uid
    try:
        owner_time = int(cfg.get('MOK_ADMIN_TIME_ZONE') or 0)
    except Exception:
        owner_time = 0
    try:
        sysctx = mokagi.get_system_context(agent, owner, owner_time)
    except Exception as e:
        print('[接回] 取系統提示失敗（改用空提示）', e)
        sysctx = ''
    partial = rec.get('accumulated_reply') or ''
    prompt = '\n%s:%s\n%s:' % (owner, rec.get('user_msg') or '', agent)
    msgs = [
        {'role': 'system', 'content': sysctx},
        {'role': 'user', 'content': prompt},
        {'role': 'assistant', 'content': partial},
        {'role': 'user', 'content': CONTINUE_HINT},
    ]
    try:
        temperature = float(cfg.get('MOK_temperature') or 0.8)
    except Exception:
        temperature = 0.8
    agen = await mokagi.call_llm(messages=msgs, stream=True, tools_def=[],
                                 agent_config=cfg, user_id=uid, temperature=temperature)
    if isinstance(agen, str):
        print('[接回] 上游回傳非串流內容，放棄續寫')
        return ''
    got = ''
    _hold = ''
    _t0 = time.time()
    _abort = None
    while True:
        _left = _TOTAL_WAIT - (time.time() - _t0)
        if _left <= 0:
            print('[接回] 續寫超過總時限 %.0fs，停止（保留座標）' % _TOTAL_WAIT)
            _abort = 'timeout'
            break
        try:
            ev = await asyncio.wait_for(agen.__anext__(), timeout=min(_IDLE_WAIT, max(1.0, _left)))
        except StopAsyncIteration:
            break
        except asyncio.TimeoutError:
            if _left <= 1.5:
                print('[接回] 續寫超過總時限 %.0fs，停止（保留座標）' % _TOTAL_WAIT)
                _abort = 'timeout'
            else:
                print('[接回] 上游 %.0fs 無回應，放棄續寫' % _IDLE_WAIT)
                _abort = 'idle'
            break
        except Exception as e:
            print('[接回] 續寫中斷', e)
            _abort = 'error'
            break
        if not isinstance(ev, dict):
            continue
        if _superseded(sid, rec):
            print('[接回] 本輪已被新回合取代，停止續寫')
            break
        et = ev.get('type')
        c = ev.get('content') or ''
        if et == 'think':
            if c:
                _push(sid, agent, {'type': 'think', 'content': c})
        elif et == 'reply':
            if c.strip() in ('<END>', 'END', '＜END＞'):
                continue
            if c:
                _hold += c
                if _looks_like_fake_tool_call(_hold):
                    print('[接回] %s 偵測到假工具呼叫（續寫不帶工具）→ 丟棄該段、改走主迴圈' % sid)
                    _abort = 'fake_tool'
                    _hold = ''
                    break
                if _hold and any(_ch in _hold for _ch in ('{', '`', '<', '＜')):
                    if len(_hold) < _FAKE_TOOL_HOLD:
                        continue
                got += _hold
                _push(sid, agent, {'type': 'reply', 'content': _hold})
                _hold = ''
        elif et == 'done':
            break
    if _hold:
        got += _hold
        _push(sid, agent, {'type': 'reply', 'content': _hold})
        _hold = ''
    rec['resume_abort'] = _abort
    if _abort == 'fake_tool':
        rec['resume_fake_tool'] = True
    elif _abort:
        rec['resume_incomplete'] = True
    return got


# ================= 2026-10-03 by 稚：工具輪安全閘（治本 A） =================
# 事故：重啟當下該輪是「工具輪」（.jsonl 有 tool_calls 事件），本補丁卻仍以
#       「純文字續寫」重呼 LLM —— 續寫呼叫不帶工具定義（tools_def=[]），模型只能
#       把 tool call 寫成 JSON 文字；接著本補丁又補一顆 done → 前端看到「一段假
#       JSON + 完成」，任務其實斷在半路（2026-10-03 凜 / 993fee30 事故）。
# 修法：只要本輪出現過真正的工具呼叫，就不自動續寫、不認領；改由「重啟韌性」的
#       28 秒 watchdog 送誠實收尾（請使用者回覆「繼續」，由主迴圈帶完整工具重跑）。
def _round_had_tools(sid):
    """本輪（單一 .jsonl = 單一回合）是否出現過真正的工具呼叫／結果事件。"""
    if not sid:
        return False
    try:
        with open(os.path.join(_LOG_DIR, sid + '.jsonl'), encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    ev = json.loads(line)
                except Exception:
                    continue
                if ev.get('type') in ('tool_calls', 'tool_result'):
                    return True
    except Exception:
        return False
    return False


# ================= 2026-10-03 晚 by 稚：工具懸空 -> 自動續跑（B 修） =================
# 問題：A 修把「用過工具的回合」一律擋掉，結果重啟後三條中斷全都不續跑，
#       主人得自己打「繼續」，體驗比改前更差。
# 修法：
#   1) 改為只看「最後一個工具事件」是 tool_calls 還是 tool_result：
#        最後是 tool_calls -> 真的懸空，需要帶工具續跑
#        最後是 tool_result（或本輪沒有工具）-> 純文字續寫就安全
#   2) 真的懸空時不再停手：自動代主人送一則「繼續」進主迴圈（process_message，
#      帶完整工具），等同主人手動回「繼續」，只是不用人手輸入。
def _tail_tool_hang(sid, rec=None):
    """本輪最後一個工具事件是否為「未回覆的工具呼叫」。
    回傳 (是否懸空, 工具名列表, 最後一組呼叫明細)。
    呼叫明細 = [{'name': ..., 'arguments': {...}}, ...]（供副作用白名單判斷）。
    jsonl 不存在時退回 rec['tool_pending']。"""
    last = None
    last_calls = []
    if sid:
        try:
            p = os.path.join(_LOG_DIR, sid + '.jsonl')
            if os.path.exists(p):
                with open(p, encoding='utf-8') as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            ev = json.loads(line)
                        except Exception:
                            continue
                        t = ev.get('type')
                        if t == 'tool_calls':
                            names = []
                            calls = []
                            for c in (ev.get('calls') or []):
                                try:
                                    if not isinstance(c, dict):
                                        continue
                                    if c.get('name'):
                                        names.append(c.get('name'))
                                    calls.append({'name': c.get('name'),
                                                  'arguments': c.get('arguments')})
                                except Exception:
                                    pass
                            last = ('tool_calls', names)
                            last_calls = calls
                        elif t == 'tool_result':
                            last = ('tool_result', [ev.get('tool_name') or ''])
        except Exception:
            last = None
    if last is None:
        return (bool((rec or {}).get('tool_pending')), [], [])
    return (last[0] == 'tool_calls', last[1], last_calls)


def _continue_via_main_loop(sid, rec, agent):
    """工具懸空：自動代主人送一則「繼續」給主迴圈（帶完整工具）把工作跑完。
    等同主人手動回覆「繼續」，只是由系統自動送出、不用人手輸入。"""
    uid = rec.get('user_id') or ''
    try:
        main._running_agents.add(agent)
    except Exception:
        pass
    _push(sid, agent, {'type': 'server_restart',
                       'content': '服務剛剛重啟：偵測到上一輪卡在工具執行途中，已自動接手繼續（帶完整工具）…'},
          agg=False)

    async def _cb(ev):
        try:
            if not isinstance(ev, dict):
                return
            if ev.get('type') in ('reply', 'think'):
                _push(sid, agent, ev)
        except Exception:
            pass

    async def _work():
        core = os.path.expanduser('~/.mok/core')
        if core not in sys.path:
            sys.path.insert(0, core)
        import mokagi
        cfg = await mokagi.get_agent_config(agent)
        _out_dir = _anon = None
        try:
            _out_dir, _anon = main.resolve_output_ctx(uid, agent)
        except Exception as _e:
            print('[接回] 取產物落點失敗（照常續跑）', _e)
        kw = dict(user_id=uid, text=AUTO_CONTINUE_NOTE, stream_callback=_cb,
                  agent_name=agent, agent_config=cfg,
                  context_files=None, output_dir=_out_dir, anon_sid=_anon)
        gate = getattr(main, 'gate_call', None)
        if gate is not None:
            await gate(mokagi.process_message, **kw)
        else:
            await mokagi.process_message(**kw)

    ok = False
    try:
        asyncio.run(_work())
        ok = True
    except Exception as e:
        print('[接回] 自動續跑失敗', e)
    try:
        main._running_agents.discard(agent)
    except Exception:
        pass
    if ok:
        print('[接回] %s 自動續跑結束（主迴圈，帶完整工具）' % sid)
        _finalize(sid, agent, None)
    else:
        print('[接回] %s 自動續跑失敗 -> 誠實收尾' % sid)
        _finalize(sid, agent, FALLBACK_FAIL)


def _defer_honest_close(sid, agent, rec, delay=45.0):
    """工具輪中斷：不假裝完成。交由 watchdog 收尾；另備保險計時器避免前端卡死。"""
    # 1) 把已寫好的部分正文留在 DB，讓使用者回「繼續」時主迴圈能接著做
    try:
        _p = (rec or {}).get('accumulated_reply') or ''
        if _p.strip() and (rec or {}).get('assistant_msg_id'):
            _update_db(rec, _p)
    except Exception:
        pass
    # 2) 讓「重啟韌性」的 watchdog 認得這條 session
    try:
        _pend = getattr(main, '_pending_continuation', None)
        if _pend is not None:
            _pend.add(sid)
    except Exception:
        pass

    # 3) 保險計時器（watchdog 未涵蓋時的最後一道；一樣是誠實提示）
    def _later():
        try:
            time.sleep(delay)
            if _buffer_has_done(sid):
                return
            print('[接回] %s 保險收尾（誠實提示，非假完成）' % sid)
            _finalize(sid, agent, FALLBACK_TOOL)
            try:
                main._sse_done[sid] = True
            except Exception:
                pass
        except Exception:
            pass
    try:
        threading.Thread(target=_later, daemon=True).start()
    except Exception:
        pass


def _run_one(rec):
    sid = rec.get('sid')
    agent = rec.get('agent') or ''
    # 2026-10-03 晚修（稚 B 修）：主人指示「重啟就要自動繼續，不要人手打『繼續』」。
    # 舊版（A 修）把「本輪用過工具」一律擋掉，實測導致三條中斷全都不續跑、主人得手動補「繼續」。
    # 新版只針對「最後一個工具呼叫沒有結果（真的懸空）」處理，而且不是擋住，
    # 是改走「自動代主人送一則『繼續』進主迴圈（process_message，帶完整工具）」把工作跑完。
    _hang, _tnames, _calls = _tail_tool_hang(sid, rec)
    # 2026-10-04（方案A）：先判這組懸空的呼叫有沒有副作用；有 -> 不自動重跑。
    _side = []
    if _hang:
        for _c in (_calls or []):
            try:
                if _is_side_effect(_c.get('name'), _c.get('arguments')):
                    _side.append(_c.get('name'))
            except Exception:
                _side.append(_c.get('name'))
    if _hang:
        print('[接回] %s 中斷在「未回覆的工具呼叫」%s -> %s' % (
            sid, _tnames or '',
            ('偵測到副作用工具 %s，不自動重跑' % _side) if _side else '自動帶完整工具續跑'))
    elif _round_had_tools(sid):
        print('[接回] %s 本輪用過工具，但中斷點不在工具之間 -> 安全純文字續寫' % sid)
    _claim(sid)
    if _buffer_has_done(sid):
        print('[接回] %s 已有 done，略過' % sid)
        return
    _ensure_session(sid, rec)
    if _superseded(sid, rec):
        print('[接回] %s 已被新回合取代，略過' % sid)
        return
    # 2026-10-04（方案A｜零漏網）：懸空的是副作用工具 -> 絕不自動重跑，
    # 保留已完成的正文、貼誠實提示請主人確認（避免重複發文／寄信／付款／刪除／燒錢）。
    if _hang and _side:
        print('[接回] %s 副作用工具 %s 懸空 -> 誠實收尾（不自動重跑）' % (sid, _side))
        _finalize(sid, agent, FALLBACK_TOOL)
        try:
            main._sse_done[sid] = True
        except Exception:
            pass
        return
    # 重試上限：同一輪最多自動續寫 2 次（避免反覆重啟時無限接回）
    try:
        tries = int(rec.get('resume_tries') or 0)
    except Exception:
        tries = 0
    if tries >= 2:
        print('[接回] %s 已續寫失敗 %d 次 -> 誠實收尾' % (sid, tries))
        _finalize(sid, agent, FALLBACK_FAIL)
        return
    rec['resume_tries'] = tries + 1
    _write_rec(rec)

    # 分支 1：工具懸空 -> 走主迴圈（帶完整工具）自動續跑，等同主人手動回「繼續」
    if _hang:
        _continue_via_main_loop(sid, rec, agent)
        return

    # 分支 2：安全純文字續寫
    # 以「SSE 緩衝實際收到的內容」為準（可能已含前一次續寫吐出的字），
    # 避免反覆重啟時把同一段文字接兩次。
    partial = (rec.get('accumulated_reply') or '')
    try:
        _r2, _t2, _br2, _n2 = _agg_rounds_from_buffer(sid)
        if len(_br2) > len(partial):
            print('[接回] %s 以緩衝內容為準（%d -> %d 字）' % (sid, len(partial), len(_br2)))
            partial = _br2
    except Exception:
        pass
    rec['accumulated_reply'] = partial
    _write_rec(rec)
    partial = partial.strip()
    if not partial:
        print('[接回] %s 尚無正文 -> 不自動續寫' % sid)
        _finalize(sid, agent, FALLBACK_NO_TEXT)
        return
    print('[接回] %s 開始自動續寫（已有 %d 字）' % (sid, len(partial)))
    try:
        main._running_agents.add(agent)
    except Exception:
        pass
    _push(sid, agent, {'type': 'server_restart',
                       'content': '服務剛剛重啟，本輪回覆正在自動從斷點續寫…'}, agg=False)
    got = ''
    try:
        got = asyncio.run(_gen(sid, rec))
    except Exception as e:
        print('[接回] 續寫失敗', e)
    full = (rec.get('accumulated_reply') or '') + got
    _update_db(rec, full)
    try:
        main._running_agents.discard(agent)
    except Exception:
        pass
    # 2026-10-08 三修（治本）：續寫（不帶工具）偵測到假工具呼叫 ->
    # 丟棄假 JSON、改走主迴圈（帶完整工具）接手，等同主人親手打「繼續」。
    if rec.get('resume_fake_tool'):
        rec['accumulated_reply'] = full
        _write_rec(rec)
        print('[接回] %s 續寫偵測到假工具呼叫 -> 改走主迴圈（帶完整工具）接手' % sid)
        _continue_via_main_loop(sid, rec, agent)
        return
    # 2026-10-08 三修（止血＋收尾）：逾時／中斷不標 done、不上 _sse_done，
    # 保留 resume 座標等下次重啟再續（避免「假的完成」）。
    if rec.get('resume_incomplete'):
        print('[接回] %s 續寫逾時／中斷（補了 %d 字）-> 不標 done、保留座標' % (sid, len(got)))
        _push(sid, agent, {'type': 'reply', 'content': FALLBACK_FAIL}, agg=False)
        rec['resume_incomplete'] = False
        _write_rec(rec)
        return
    if got:
        print('[接回] %s 續寫完成（補了 %d 字）' % (sid, len(got)))
        _push(sid, agent, {'type': 'done', 'final_reply': full}, agg=False)
        try:
            main._sse_done[sid] = True
        except Exception:
            pass
    else:
        print('[接回] %s 無新內容 -> 誠實收尾' % sid)
        _finalize(sid, agent, FALLBACK_FAIL)




#（根因修補） =================
# 事故：按「主人重啟」時，那一輪的 .resume.json 不存在（該輪開始時本補丁還沒載入），
#       開機掃描就報「無中斷的回合需要接回」，28 秒後由「重啟韌性」貼舊訊息收尾。
# 根因：接回看 *.resume.json；重啟韌性看 *.meta.json + *.jsonl —— 兩者各看各的檔。
# 修法：開機掃描時，若某個中斷 session 缺 .resume.json，改用 .meta.json + .jsonl
#       的磁盤緩衝合成一份續寫座標；安全閘門（工具待決／無正文／已 done／已被取代）照走。
def _last_user_msg(agent, uid):
    """取該 (agent, tenant) 最後一則 user 訊息，供續寫 prompt 用。"""
    try:
        db = getattr(main, 'DB_PATH', None)
        if not db:
            return ''
        conn = sqlite3.connect(db, timeout=30)
        try:
            row = conn.execute(
                "SELECT content FROM chat_history WHERE agent = ? AND role = 'user' "
                "AND (tenant IS ? OR tenant = ?) ORDER BY id DESC LIMIT 1",
                (agent or '', uid, uid)).fetchone()
            return (row[0] if row and row[0] else '') or ''
        finally:
            conn.close()
    except Exception:
        return ''


def _last_assistant_id(agent, uid):
    try:
        db = getattr(main, 'DB_PATH', None)
        if not db:
            return None
        conn = sqlite3.connect(db, timeout=30)
        try:
            row = conn.execute(
                "SELECT id FROM chat_history WHERE agent = ? AND role = 'assistant' "
                "AND (tenant IS ? OR tenant = ?) ORDER BY id DESC LIMIT 1",
                (agent or '', uid, uid)).fetchone()
            return row[0] if row else None
        finally:
            conn.close()
    except Exception:
        return None


def _synth_from_buffer(sid, agent, uid, ts):
    """沒有 .resume.json 時，用磁盤緩衝 .jsonl 還原出續寫座標。"""
    evs = []
    try:
        with open(os.path.join(_LOG_DIR, sid + '.jsonl'), encoding='utf-8') as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    evs.append(json.loads(line))
                except Exception:
                    pass
    except Exception:
        return None
    if not evs:
        return None
    reply = ''
    think = ''
    pending = False
    seen_done = False
    for ev in evs:
        et = ev.get('type')
        if et == 'think':
            think += ev.get('content') or ''
        elif et == 'reply':
            sub = ev.get('subtype', 'normal')
            if sub in ('tool_result', 'tool_process', 'semantic_search', 'experience', 'pending_list'):
                continue
            reply += ev.get('content') or ''
        elif et == 'tool_calls':
            pending = True
        elif et == 'tool_result':
            pending = False
        elif et == 'done':
            seen_done = True
    if seen_done or not (reply or think):
        return None
    return {'sid': sid, 'agent': agent or '', 'user_id': uid or '',
            'user_msg': _last_user_msg(agent, uid),
            'assistant_msg_id': _last_assistant_id(agent, uid),
            'accumulated_reply': reply, 'accumulated_think': think,
            'tool_pending': pending, 'done': False, 'ts': ts, 'from_buffer': True}


def _synth_records():
    """掃 .meta.json（重啟韌性已判定為中斷的 session），為缺 resume 記錄者補一份。"""
    out = []
    have = set()
    try:
        for r in _scan_records():
            have.add(r.get('sid'))
    except Exception:
        pass
    now = time.time()
    # 2026-10-03 稚：安全鎖 1 —— 只認「重啟韌性」本次開機實際還原的那批 session。
    _pend = getattr(main, '_pending_continuation', None)
    # 安全鎖 2 —— 某 agent 此刻正在跑，就不要去補錄它的回合（避免重複生成）。
    _running = getattr(main, '_running_agents', None)
    for mp in glob.glob(os.path.join(_LOG_DIR, '*.meta.json')):
        sid = os.path.basename(mp)[:-len('.meta.json')]
        if sid in have:
            continue
        if _pend is not None and sid not in _pend:
            continue
        try:
            with open(mp, encoding='utf-8') as f:
                d = json.load(f)
        except Exception:
            continue
        if d.get('done'):
            continue
        try:
            mts = float(d.get('ts') or 0)
        except Exception:
            mts = 0.0
        try:
            mts = max(mts, os.path.getmtime(mp))
        except Exception:
            pass
        # 2026-10-03 修：再取 .jsonl 的 mtime 當「最後活動時間」（每次 append 都會更新），
        # 避免長回合因 meta 時間戳停在起點而被誤判過期。
        try:
            mts = max(mts, os.path.getmtime(os.path.join(_LOG_DIR, sid + '.jsonl')))
        except Exception:
            pass
        if mts and (now - mts) > MAX_AGE:
            continue
        if _running is not None and (d.get('agent') in _running):
            print('[接回] %s 的 %s 正在跑，跳過補錄' % (sid, d.get('agent')))
            continue
        r = _synth_from_buffer(sid, d.get('agent'), d.get('user'), mts or now)
        if r:
            out.append(r)
    return out


def _boot():
    time.sleep(START_DELAY)
    now = time.time()
    try:
        recs = _scan_records()
    except Exception as e:
        print('[接回] 掃描失敗', e)
        recs = []
    # 2026-10-03 修（稚）：補上「該輪開始時本補丁還沒載入 → 沒有 .resume.json」的缺口，
    # 以重啟韌性已落盤的 .meta.json + .jsonl 磁盤緩衝合成續寫座標；安全閘門照走。
    try:
        _syn = _synth_records()
        if _syn:
            print('[接回] 由磁盤緩衝補出 %d 條可續寫回合' % len(_syn))
            recs = list(recs) + _syn
    except Exception as _se:
        print('[接回] 磁盤緩衝補錄失敗', _se)
    todo = []
    for r in recs:
        if r.get('done'):
            continue
        try:
            age = now - float(r.get('ts') or 0)
        except Exception:
            continue
        if age <= MAX_AGE:
            todo.append(r)
    todo.sort(key=lambda r: float(r.get('ts') or 0))
    if not todo:
        print('[接回] 無中斷的回合需要接回')
        return
    print('[接回] 發現 %d 條中斷回合，開始自動續寫' % len(todo))
    # 2026-10-03 晚修（稚）：工具續跑可能長達數分鐘（跑腳本／爬資料），
    # 舊版序列執行會讓後面的中斷回合枯等。改為每條中斷各自一條執行緒並行續跑。
    for r in todo:
        try:
            threading.Thread(target=_run_one, args=(r,), daemon=True).start()
        except Exception as e:
            print('[接回] 接回執行緒啟動失敗', r.get('sid'), e)


try:
    threading.Thread(target=_boot, daemon=True).start()
except Exception as _bt_e:
    print('[接回] 啟動續寫執行緒失敗', _bt_e)


# ---------------- 五、狀態回報檔（驗收用；不開 API、不碰認證） ----------------
_STATUS_FILE = os.path.join(_LOG_DIR, '_seamless_status.json')


def _status_snapshot():
    rows = []
    for r in _scan_records():
        rows.append({'sid': r.get('sid'), 'agent': r.get('agent'), 'user': r.get('user_id'),
                     'done': bool(r.get('done')), 'tool_pending': bool(r.get('tool_pending')),
                     'reply_len': len(r.get('accumulated_reply') or ''), 'ts': r.get('ts')})
    rows.sort(key=lambda x: x.get('ts') or 0)
    pend = []
    try:
        pend = sorted(list(getattr(main, '_pending_continuation', set()) or set()))
    except Exception:
        pend = []
    inflight = []
    try:
        with main._sse_lock:
            for sid, doneflag in list(main._sse_done.items()):
                if not doneflag:
                    inflight.append(sid)
    except Exception:
        inflight = []
    return {'pid': os.getpid(), 'ts': time.time(), 'max_age_sec': MAX_AGE,
            'pending_continuation': pend, 'inflight_sessions': inflight, 'records': rows}


def _status_reporter():
    while True:
        try:
            tmp = _STATUS_FILE + '.tmp'
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump(_status_snapshot(), f, ensure_ascii=False, indent=1)
            os.replace(tmp, _STATUS_FILE)
        except Exception:
            pass
        time.sleep(15)


try:
    threading.Thread(target=_status_reporter, daemon=True).start()
    print('[接回] 狀態回報檔已啟動：', _STATUS_FILE)
except Exception as _sr_e:
    print('[接回] 狀態回報啟動失敗', _sr_e)
