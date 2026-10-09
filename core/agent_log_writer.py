# -*- coding: utf-8 -*-
"""agent_log_writer.py — launcher 的 agent log 批次寫入器（P0-3 2026-10-04 by 稚）

把 launcher.stream_reader「每行一次 flush」的寫法，改成：
  * 記憶體緩衝：累積 >= MOK_AGENT_LOG_BUF_KB（預設 8KB）或 >=64 行 或 距上次滿 0.5s 才落盤
  * 背景 flusher 每 0.5s 掃一次，確保低流量時也會落盤（不丟資料）
  * 單檔超過 MOK_AGENT_LOG_MAX_MB（預設 20MB）自動輪替為 <name>.log.1（保留 1 份）
  * 進程結束（atexit）強制 flush

用法（launcher.py）：
    from agent_log_writer import write_record, flush_all
    ...
    write_record(fn, fh, line)      # 取代 fh.write(...) + fh.flush()

停用（回舊行為）：MOK_AGENT_LOG_SYNC=1
"""
import os
import time
import threading
import atexit

_LOCK = threading.Lock()
_BUF = {}      # fn -> [str]
_BYTES = {}    # fn -> 目前緩衝位元組數
_FHS = {}      # fn -> 目前開啟的 handle
_LAST = {}     # fn -> 上次 flush 時間
_STARTED = False


def _env_int(k, d):
    try:
        return int(os.environ.get(k, "") or d)
    except Exception:
        return d


def _env_float(k, d):
    try:
        return float(os.environ.get(k, "") or d)
    except Exception:
        return d


_MAX_BYTES = _env_int("MOK_AGENT_LOG_MAX_MB", 20) * 1024 * 1024
_BUF_BYTES = max(1024, _env_int("MOK_AGENT_LOG_BUF_KB", 8) * 1024)
_BUF_LINES = max(8, _env_int("MOK_AGENT_LOG_BUF_LINES", 64))
_INTERVAL = max(0.1, _env_float("MOK_AGENT_LOG_FLUSH_SEC", 0.5))
_SYNC = os.environ.get("MOK_AGENT_LOG_SYNC", "0").strip().lower() in ("1", "true", "yes", "on")


def _rotate_locked(fn, fh):
    """單檔超過上限 -> 關檔、覆蓋式輪替為 <path>.1、重開。"""
    try:
        path = getattr(fh, "name", None)
        if not path:
            return fh
        try:
            fh.close()
        except Exception:
            pass
        bak = path + ".1"
        try:
            if os.path.exists(bak):
                os.remove(bak)
            os.replace(path, bak)
        except Exception:
            pass
        new_fh = open(path, "a", encoding="utf-8")
        _FHS[fn] = new_fh
        return new_fh
    except Exception:
        return _FHS.get(fn, fh)


def _flush_one_locked(fn):
    buf = _BUF.get(fn)
    fh = _FHS.get(fn)
    if not buf or fh is None:
        _BUF[fn] = []
        _BYTES[fn] = 0
        return
    try:
        try:
            if fh.tell() >= _MAX_BYTES:
                fh = _rotate_locked(fn, fh)
        except Exception:
            pass
        fh.write("".join(buf))
        fh.flush()
    except Exception:
        pass
    _BUF[fn] = []
    _BYTES[fn] = 0
    _LAST[fn] = time.time()


def _flusher_loop():
    while True:
        time.sleep(_INTERVAL)
        try:
            with _LOCK:
                for fn in list(_BUF.keys()):
                    _flush_one_locked(fn)
        except Exception:
            pass


def _ensure_flusher():
    global _STARTED
    if _STARTED or _SYNC:
        return
    _STARTED = True
    threading.Thread(target=_flusher_loop, name="agent-log-flusher", daemon=True).start()


def write_record(fn, fh, rec):
    """緩衝一筆記錄；必要時批次落盤。"""
    if _SYNC:
        try:
            fh.write(rec)
            fh.flush()
        except Exception:
            pass
        return
    try:
        with _LOCK:
            _FHS[fn] = fh
            buf = _BUF.get(fn)
            if buf is None:
                buf = []
                _BUF[fn] = buf
                _BYTES[fn] = 0
                _LAST[fn] = time.time()
            buf.append(rec)
            _BYTES[fn] = _BYTES.get(fn, 0) + len(rec)
            _ensure_flusher()
            if (len(buf) >= _BUF_LINES or _BYTES.get(fn, 0) >= _BUF_BYTES
                    or (time.time() - _LAST.get(fn, 0)) >= _INTERVAL):
                _flush_one_locked(fn)
    except Exception:
        pass


def flush_all():
    try:
        with _LOCK:
            for fn in list(_BUF.keys()):
                _flush_one_locked(fn)
    except Exception:
        pass


atexit.register(flush_all)
