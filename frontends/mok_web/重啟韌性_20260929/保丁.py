# -*- coding: utf-8 -*-
"""
重啟韌性補丁 v1（2026-09-29 架）
================================
對應主人交辦的 item 1、2：
  item1 優雅重啟：關閉前向所有「進行中」的 SSE 前端廣播「服務即將重啟」，
        前端顯示提示；不再讓服務被靜默硬砍。
  item2 續流持久化：SSE 事件緩衝同步落盤（~/.mok/logs/sse_stream/），
        服務重啟後自動還原「未完成」的 session，前端重連即可把答案接回來。

做法：monkey patch（不改核心 mok_web.py）
  - 以落盤版 dict 取代 main._sse_buffers / _sse_agents / _sse_users / _sse_done
  - 新增 POST /api/restart-notice（launcher 於關閉前呼叫）
  - 安裝 SIGTERM/SIGINT handler：廣播 + 收尾 + 退出
  - 啟動時還原 3 小時內未完成的 session
停用：把本目錄改名（前綴加 _）即可，無需改核心。
"""
import sys
import os
import json
import glob
import time
import queue as _queue
import threading
import signal as _signal

main = sys.modules.get('__main__')
if main is None or not hasattr(main, '_sse_queues'):
    raise RuntimeError('重啟韌性補丁需在 mok_web 主模組下載入')

_LOG_DIR = os.path.expanduser('~/.mok/logs/sse_stream')
try:
    os.makedirs(_LOG_DIR, exist_ok=True)
except Exception:
    pass
_RESTORE_MAX_AGE = 3 * 3600


def _meta_path(sid):
    return os.path.join(_LOG_DIR, sid + '.meta.json')


def _ev_path(sid):
    return os.path.join(_LOG_DIR, sid + '.jsonl')


def _write_meta(sid, field, value):
    try:
        p = _meta_path(sid)
        d = {}
        if os.path.exists(p):
            try:
                with open(p, encoding='utf-8') as f:
                    d = json.load(f)
            except Exception:
                d = {}
        d[field] = value
        d['ts'] = time.time()
        tmp = p + '.tmp'
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(d, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception as e:
        print('[重啟韌性] meta 寫入失敗', e)


class _PersistList(list):
    """事件緩衝：append 時同步落盤，供重啟後續流重播。"""

    def __init__(self, sid):
        super(_PersistList, self).__init__()
        self._sid = sid

    def append(self, ev):
        super(_PersistList, self).append(ev)
        try:
            with open(_ev_path(self._sid), 'a', encoding='utf-8') as f:
                f.write(json.dumps(ev, ensure_ascii=False) + '\n')
        except Exception:
            pass


class _BufDict(dict):
    def __setitem__(self, k, v):
        if isinstance(v, list) and not isinstance(v, _PersistList):
            v = _PersistList(k)
        super(_BufDict, self).__setitem__(k, v)


class _MetaDict(dict):
    def __init__(self, field):
        super(_MetaDict, self).__init__()
        self._field = field

    def __setitem__(self, k, v):
        super(_MetaDict, self).__setitem__(k, v)
        _write_meta(k, self._field, v)


main._sse_buffers = _BufDict()
main._sse_agents = _MetaDict('agent')
main._sse_users = _MetaDict('user')
main._sse_done = _MetaDict('done')


def _broadcast(event):
    """把事件推進所有『進行中』的 SSE 佇列；SSE generator 讀到即送達前端。"""
    n = 0
    try:
        with main._sse_lock:
            for sid, q in list(main._sse_queues.items()):
                if main._sse_done.get(sid, False):
                    continue
                try:
                    q.put(dict(event))
                    n += 1
                except Exception:
                    pass
    except Exception as e:
        print('[重啟韌性] 廣播失敗', e)
    return n


def _notify_restart():
    txt = '服務即將重啟，本輪回覆會自動接續，請稍候…'
    n = _broadcast({'type': 'server_restart', 'content': txt})
    try:
        main.socketio.emit('server_restart', {'content': txt})
    except Exception:
        pass
    print('[重啟韌性] 已廣播重啟通知，涵蓋 %d 條進行中連線' % n)
    return n


try:
    @main.app.route('/api/restart-notice', methods=['POST'])
    def _mok_restart_notice():
        n = _notify_restart()
        try:
            return main.jsonify({'ok': True, 'notified': n})
        except Exception:
            return 'ok'
except Exception as e:
    print('[重啟韌性] 註冊 /api/restart-notice 失敗', e)


# 2026-10-03 稚：「無縫接回」接手窗口 —— 還原後先不宣告結束，
# 給接回補丁 28 秒認領；無人認領才誠實收尾（維持舊行為的提示）。
_pending_continuation = set()


def _claim_continuation(sid):
    try:
        _pending_continuation.discard(sid)
    except Exception:
        pass
    return True


def _finalize_continuation(sid, text=None):
    try:
        _pending_continuation.discard(sid)
    except Exception:
        pass
    try:
        with main._sse_lock:
            buf = main._sse_buffers.get(sid)
            q = main._sse_queues.get(sid)
        try:
            ag = main._sse_agents.get(sid) or ''
        except Exception:
            ag = ''
        if text:
            ev = {'type': 'reply', 'content': text, 'agent': ag}
            if buf is not None:
                buf.append(ev)
            if q is not None:
                q.put(dict(ev))
        dn = {'type': 'done', 'sse_session_id': sid, 'restarted': True}
        if buf is not None:
            buf.append(dn)
        if q is not None:
            q.put(dict(dn))
        _write_meta(sid, 'done', True)
        return True
    except Exception as e:
        print('[重啟韌性] 收尾失敗', e)
        return False


def _continuation_watchdog():
    time.sleep(28)
    try:
        left = list(_pending_continuation)
    except Exception:
        left = []
    for sid in left:
        print('[重啟韌性] session %s 無人接回 -> 誠實收尾' % sid)
        _finalize_continuation(
            sid, '\n\n> ⚠️ 服務在產生回覆時重啟，這一輪沒能自動接回（重啟當下可能正在跑工具、或還沒開始寫正文）。以上為已完成的部分；請回覆「繼續」讓我接著處理。')


try:
    main._claim_continuation = _claim_continuation
    main._finalize_continuation = _finalize_continuation
    # 2026-10-03 稚：暴露同一個 set 物件（非複本），讓「無縫接回」能精準知道
    # 這次開機到底還原了哪幾條中斷 session，避免它自己掃 meta 時誤撈到新開的回合。
    main._pending_continuation = _pending_continuation
except Exception:
    pass


def _restore_sessions():
    now = time.time()
    restored = 0
    try:
        metas = glob.glob(os.path.join(_LOG_DIR, '*.meta.json'))
    except Exception:
        metas = []
    for mp in metas:
        try:
            with open(mp, encoding='utf-8') as f:
                d = json.load(f)
        except Exception:
            continue
        if d.get('done'):
            continue
        try:
            if now - float(d.get('ts') or 0) > _RESTORE_MAX_AGE:
                continue
        except Exception:
            continue
        sid = os.path.basename(mp)[:-len('.meta.json')]
        if dict.__contains__(main._sse_done, sid):
            continue
        evs = []
        try:
            with open(_ev_path(sid), encoding='utf-8') as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        evs.append(json.loads(line))
                    except Exception:
                        pass
        except Exception:
            evs = []
        if not evs:
            continue
        bl = _PersistList(sid)
        list.extend(bl, evs)
        dict.__setitem__(main._sse_buffers, sid, bl)
        dict.__setitem__(main._sse_agents, sid, d.get('agent') or '')
        dict.__setitem__(main._sse_users, sid, d.get('user'))
        dict.__setitem__(main._sse_done, sid, False)
        q = _queue.Queue()
        main._sse_queues[sid] = q
        # 2026-10-03 稚：不在此宣告結束 —— 讓「無縫接回」補丁有機會把剩下的答案寫完；
        # 若 28 秒內無人認領，watchdog 會補上提示 + done（與舊行為一致）。
        try:
            _pending_continuation.add(sid)
        except Exception:
            pass
        restored += 1
    if restored:
        print('[重啟韌性] 已還原 %d 條未完成的 SSE 續流 session' % restored)


_shutting_down = {'flag': False}


def _do_shutdown():
    threading.Timer(10.0, lambda: os._exit(0)).start()
    try:
        _notify_restart()
    except Exception as e:
        print('[重啟韌性] 關閉廣播失敗', e)
    deadline = time.time() + 6
    while time.time() < deadline:
        try:
            with main._sse_lock:
                inflight = [s for s, dn in list(main._sse_done.items()) if not dn]
        except Exception:
            inflight = []
        if not inflight:
            break
        time.sleep(0.3)
    time.sleep(0.6)
    os._exit(0)


def _graceful_shutdown(signum, frame):
    if _shutting_down['flag']:
        return
    _shutting_down['flag'] = True
    print('[重啟韌性] Web 收到信號 %s，廣播重啟通知並收尾…' % signum)
    # 另開執行緒收尾：signal handler 會在主執行緒中斷執行，
    # 直接在 handler 內取 _sse_lock 可能與主執行緒互卡（死鎖）。
    threading.Thread(target=_do_shutdown, daemon=True).start()


try:
    _signal.signal(_signal.SIGTERM, _graceful_shutdown)
    _signal.signal(_signal.SIGINT, _graceful_shutdown)
    print('[重啟韌性] SIGTERM/SIGINT 優雅關閉已安裝')
except Exception as e:
    print('[重啟韌性] 安裝 signal 失敗', e)


def _prune_old():
    """把超過 7 天的續流暫存搬進 _old/（不硬刪），避免無限成長。"""
    try:
        cutoff = time.time() - 7 * 86400
        old_dir = os.path.join(_LOG_DIR, '_old')
        moved = 0
        for f in glob.glob(os.path.join(_LOG_DIR, '*')):
            if os.path.isdir(f):
                continue
            try:
                if os.path.getmtime(f) < cutoff:
                    if not os.path.isdir(old_dir):
                        os.makedirs(old_dir, exist_ok=True)
                    os.replace(f, os.path.join(old_dir, os.path.basename(f)))
                    moved += 1
            except Exception:
                pass
        if moved:
            print('[重啟韌性] 已歸檔 %d 個逾 7 天的續流暫存' % moved)
    except Exception:
        pass


try:
    _prune_old()
except Exception as _e:
    print('[重啟韌性] 歸檔失敗', _e)

try:
    _restore_sessions()
except Exception as e:
    print('[重啟韌性] 還原 session 失敗', e)

try:
    threading.Thread(target=_continuation_watchdog, daemon=True).start()
except Exception as _wd_e:
    print('[重啟韌性] watchdog 啟動失敗', _wd_e)

print('[重啟韌性] 補丁已載入（item1 重啟廣播 + item2 續流持久化）')
