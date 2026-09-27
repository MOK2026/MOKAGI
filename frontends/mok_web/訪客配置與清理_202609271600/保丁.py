# -*- coding: utf-8 -*-
"""
訪客配置放寬 + 訪客歷史清理策略（2026-09-27 by 凜）
=====================================================
【1】/api/mok_config 訪客放寬
    背景：2026-09-26 泠 的資安加固以 admin 閘擋掉所有非特權 session（含未登入訪客），
          訪客端 fetch('/api/mok_config') 得 403 → configData={} →
          前端 agenticon = mokConfig.MOK_AGENT_ICON || '🌸' 全走預設值。
    修正：不再 403，一律回傳 _safe_mok_config()（白名單 + DENY 子字串過濾後的非機密 UI 設定）。
          機密（token/secret/key/users/chat_id…）已在 _safe_mok_config 擋除，無外洩風險。

【2】訪客歷史清理策略
    chat_history / conversation_history 中 tenant 為 guest:* 或 web_guest_* 的列，
    超過「匿名 TTL 窗」（＝匿名沙盒 TTL_MAX，預設 6 小時）即刪除，避免訪客對話無限累積。
    2026-09-27：保留窗由 10 天統一為與匿名產物/cookie 同一個 6 小時窗（單一來源）。
    啟動後每小時檢查一次（背景 daemon 執行緒）。
"""
import os, sys, time, sqlite3, threading
from contextlib import closing

main = sys.modules['__main__']

# 【統一 TTL】2026-09-27：匿名對話保留窗統一為「匿名沙盒硬上限」(main.mok_anon['ttl_max'])，
# 與產物 / cookie 同一個 6 小時窗，不再自成一格用 10 天；取不到才回落此值。
GUEST_RETENTION_SEC_FALLBACK = 6 * 3600
_CLEANUP_INTERVAL_SEC = 3600
_cleanup_last = 0.0


# ---------- 1) 放寬 /api/mok_config ----------
def get_mok_config():
    """回傳白名單過濾後的非機密設定；訪客/未登入亦可讀（不再 403）。"""
    return main.jsonify(main._safe_mok_config())


try:
    main.app.view_functions['get_mok_config'] = get_mok_config
    main.get_mok_config = get_mok_config
    print('[訪客配置放寬] ✅ /api/mok_config 已改為白名單輸出（訪客可讀，不再 403）')
except Exception as _e:
    print('[訪客配置放寬] ❌ 掛載失敗:', _e)


# ---------- 2) 訪客歷史清理 ----------
def _db_targets():
    home = getattr(main, 'MOKAGI_home', 'mok')
    return [
        (getattr(main, 'DB_PATH', None), 'chat_history'),
        (os.path.expanduser('~/.{}/.memory/conversation_history.db'.format(home)), 'conversation_history'),
    ]


def _anon_ttl_sec():
    """統一 TTL：匿名對話保留窗＝匿名沙盒硬上限（main.mok_anon['ttl_max']，預設 6h）。"""
    try:
        t = (getattr(main, 'mok_anon', None) or {}).get('ttl_max')
        if t and int(t) > 0:
            return int(t)
    except Exception:
        pass
    return GUEST_RETENTION_SEC_FALLBACK


def cleanup_old_guest_rows(days=None, force=True):
    """刪除超過「匿名 TTL 窗」的訪客對話列（tenant 為 guest:* / web_guest_*）。回傳刪除筆數。
    2026-09-27：保留窗由 10 天統一為匿名沙盒 TTL_MAX（預設 6 小時）。"""
    global _cleanup_last
    _now = time.time()
    if not force and (_now - _cleanup_last) < _CLEANUP_INTERVAL_SEC:
        return 0
    _cleanup_last = _now
    _ttl = _anon_ttl_sec()
    _cutoff = _now - _ttl
    _deleted = 0
    for _path, _table in _db_targets():
        if not _path or not os.path.exists(_path):
            continue
        try:
            with closing(sqlite3.connect(_path, timeout=30)) as conn:
                cur = conn.execute(
                    "DELETE FROM {} WHERE timestamp < ? "
                    "AND (tenant LIKE 'guest:%' OR tenant LIKE 'web_guest_%')".format(_table),
                    (_cutoff,),
                )
                _deleted += cur.rowcount or 0
                conn.commit()
        except Exception as _e:
            print('[訪客清理] ❌ {}: {}'.format(_table, _e))
    if _deleted:
        print('[訪客清理] 🧹 已清除 {} 筆（保留窗 {} 小時）外的訪客對話列'.format(_deleted, round(_ttl / 3600.0, 2)))
    return _deleted


main.cleanup_old_guest_rows = cleanup_old_guest_rows


def _guest_cleanup_worker():
    time.sleep(90)  # 等服務就緒
    while True:
        try:
            cleanup_old_guest_rows(force=True)
        except Exception as _e:
            print('[訪客清理] worker 錯誤:', _e)
        time.sleep(_CLEANUP_INTERVAL_SEC)


try:
    threading.Thread(target=_guest_cleanup_worker, daemon=True).start()
    print('[訪客清理] ✅ 匿名對話清理策略已啟動（保留窗 {} 小時，每小時檢查）'.format(round(_anon_ttl_sec() / 3600.0, 2)))
except Exception as _e:
    print('[訪客清理] ❌ 背景執行緒啟動失敗:', _e)
