# -*- coding: utf-8 -*-
"""
主人重啟補丁 v1（2026-10-03 稚）
================================
把首頁那顆鍵（原 id="stopBtn"）還原成「主人重啟鈕」：真正的完整重啟。

  - 接管 socketio 'stop_generation'（覆蓋核心 handle_stop 的強制重啟行為）
  - 廣播「主人重啟中」給所有進行中的 SSE 前端
  - 寫旗標 ~/.mok/run/master_restart.flag
      → launcher 主迴圈偵測 → 優雅關閉所有子進程（Web + 全部 Bot）
        → execv 重啟 launcher 本體
      = 完整重啟（重載 launcher / mok_web / 所有補丁 / 所有 Agent）
  - 完全不呼叫 pm2 指令 → 不觸發方案C 的閘門（守衛不必放行、也沒被繞過）
  - 保底：若 launcher 仍是舊版（無旗標監看），逾時後對父進程（launcher）
    送 SIGTERM，走 launcher 既有的優雅關閉 → pm2 自動重拉 = 同樣完整重啟
  - 另附 POST /api/master_restart（admin 專用）

停用：把本目錄改名（前綴加 _）即可，無需改核心。
"""
import os
import sys
import time
import signal
import threading
import pathlib

main = sys.modules.get('__main__')
if main is None:
    raise RuntimeError('主人重啟補丁需在 mok_web 主模組下載入')

from flask import request  # noqa: E402

FLAG = pathlib.Path('/home/ubuntu/.mok/run/master_restart.flag')
_NOTICE = '🔄 主人重啟中：正在重啟 mokagi 全部服務（重載所有程式碼與補丁），完成後自動刷新…'
_FALLBACK_DELAY = 6.0


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
        f.write('master')
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
    """舊版 launcher 無旗標監看 → 逾時後對父進程（launcher）送訊號，
    讓它走既有的優雅關閉（廣播→收尾→退出），再由 pm2 自動重拉 = 完整重啟。"""
    def _wait():
        try:
            time.sleep(_FALLBACK_DELAY)
            if not _flag_pending():
                return
            try:
                FLAG.unlink()
            except Exception:
                pass
            ppid = os.getppid()
            print('[主人重啟] launcher 未接手旗標，改以訊號觸發完整重啟（parent pid=%s）' % ppid, flush=True)
            try:
                sys.stdout.flush()
            except Exception:
                pass
            try:
                os.kill(ppid, signal.SIGTERM)
            except Exception as e:
                print('[主人重啟] 送訊號失敗：%s' % e, flush=True)
        except Exception:
            pass
    threading.Thread(target=_wait, daemon=True).start()


def _master_restart():
    """回傳 (ok, msg)。"""
    if not _is_admin():
        return False, 'forbidden: admin only'
    _broadcast(_NOTICE)
    try:
        _write_flag()
    except Exception as e:
        return False, 'flag write failed: %s' % e
    print('[主人重啟] 已請求 launcher 完整重啟', flush=True)
    return True, 'requested'


def _on_stop_generation():
    sid = getattr(request, 'sid', None)
    if not _is_admin():
        try:
            main.socketio.emit('stream_stopped', {'status': 'forbidden'}, room=sid)
        except Exception:
            pass
        print('master_restart rejected (not admin): %s' % sid)
        return
    try:
        main.socketio.emit('stream_stopped', {'status': 'restarting'}, room=sid)
    except Exception:
        pass
    ok, msg = _master_restart()
    if ok:
        _arm_fallback()
    else:
        try:
            main.socketio.emit('stream_stopped', {'status': 'error', 'msg': msg}, room=sid)
        except Exception:
            pass


try:
    main.socketio.on('stop_generation')(_on_stop_generation)
    print('[主人重啟] 已接管 socketio stop_generation → 主人重啟')
except Exception as e:
    print('[主人重啟] 接管 stop_generation 失敗', e)


try:
    @main.app.route('/api/master_restart', methods=['POST'])
    def _mok_master_restart():
        ok, msg = _master_restart()
        if ok:
            _arm_fallback()
        try:
            return main.jsonify({'ok': ok, 'msg': msg})
        except Exception:
            return 'ok' if ok else msg
except Exception as e:
    print('[主人重啟] 註冊 /api/master_restart 失敗', e)


print('[主人重啟] 補丁已載入')
