# -*- coding: utf-8 -*-
"""
軟重啟補丁 v1（2026-10-03 稚）
================================
把首頁「緊急重啟」鍵改成「軟重啟」的後端實作（純補丁，不改核心 mok_web.py）：
  - 接管 socketio 'stop_generation'（覆蓋核心 handle_stop）
  - 廣播「服務軟重啟中」+ 寫旗標 ~/.mok/run/soft_restart.flag
  - launcher 偵測旗標 → 只重拉 Web 子進程（= 重新載入所有補丁）
停用：把本目錄改名（前綴加 _）即可，無需改核心。
"""
import os
import sys
import time
import threading
import pathlib

main = sys.modules.get('__main__')
if main is None:
    raise RuntimeError('軟重啟補丁需在 mok_web 主模組下載入')

from flask import request  # noqa: E402

FLAG = pathlib.Path('/home/ubuntu/.mok/run/soft_restart.flag')
_NOTICE = '🔄 服務軟重啟中：launcher 正在重拉 Web 子進程並重新載入所有補丁，完成後自動刷新…'
_FALLBACK_DELAY = 4.0


def _is_admin():
    try:
        from flask import session as _fs
        u = _fs.get('member_user')
    except Exception:
        u = None
    try:
        return bool(u and str(u) in main._PRIVILEGED_TENANTS)
    except Exception:
        return bool(u)


def _flag_pending():
    try:
        return FLAG.exists()
    except Exception:
        return False


def _write_flag():
    FLAG.parent.mkdir(parents=True, exist_ok=True)
    tmp = str(FLAG) + '.tmp'
    with open(tmp, 'w', encoding='utf-8') as f:
        f.write('soft-restart')
    os.replace(tmp, str(FLAG))


def _broadcast(txt):
    n = 0
    try:
        with main._sse_lock:
            for sid, q in list(main._sse_queues.items()):
                try:
                    q.put({'type': 'server_restart', 'content': txt})
                    n += 1
                except Exception:
                    pass
    except Exception:
        pass
    try:
        main.socketio.emit('server_restart', {'content': txt})
    except Exception:
        pass
    return n


def _arm_fallback():
    """舊版 launcher 無旗標監看 → 逾時仍無人接手就自行退出，
    交給 launcher 既有的「Web 退出 → 自動重拉」機制完成軟重啟。"""
    def _wait():
        try:
            time.sleep(_FALLBACK_DELAY)
            if _flag_pending():
                try:
                    FLAG.unlink()
                except Exception:
                    pass
                print('[軟重啟] launcher 未接手旗標，改以退出→自動重拉完成軟重啟', flush=True)
                try:
                    sys.stdout.flush()
                except Exception:
                    pass
                os._exit(0)
        except Exception:
            pass
    threading.Thread(target=_wait, daemon=True).start()


def _soft_restart():
    """回傳 (ok, msg)。"""
    if not _is_admin():
        return False, 'forbidden: admin only'
    _broadcast(_NOTICE)
    try:
        _write_flag()
    except Exception as e:
        return False, 'flag write failed: %s' % e
    print('[軟重啟] 已請求 launcher 重拉 Web 子進程', flush=True)
    return True, 'soft-restart requested'


def _on_stop_generation():
    sid = getattr(request, 'sid', None)
    if not _is_admin():
        try:
            main.socketio.emit('stream_stopped', {'status': 'forbidden'}, room=sid)
        except Exception:
            pass
        print('soft_restart rejected (not admin): %s' % sid)
        return
    try:
        main.socketio.emit('stream_stopped', {'status': 'restarting'}, room=sid)
    except Exception:
        pass
    ok, msg = _soft_restart()
    if ok:
        _arm_fallback()
    else:
        try:
            main.socketio.emit('stream_stopped', {'status': 'error', 'msg': msg}, room=sid)
        except Exception:
            pass


try:
    main.socketio.on('stop_generation')(_on_stop_generation)
    print('[軟重啟] 已接管 socketio stop_generation → 軟重啟')
except Exception as e:
    print('[軟重啟] 接管 stop_generation 失敗', e)


try:
    @main.app.route('/api/soft-restart', methods=['POST'])
    def _mok_soft_restart():
        ok, msg = _soft_restart()
        if ok:
            _arm_fallback()
        try:
            return main.jsonify({'ok': ok, 'msg': msg})
        except Exception:
            return 'ok' if ok else msg
except Exception as e:
    print('[軟重啟] 註冊 /api/soft-restart 失敗', e)


print('[軟重啟] 補丁已載入')
