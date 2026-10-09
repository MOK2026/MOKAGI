# -*- coding: utf-8 -*-
"""
產物三層落點 補丁 v1.0  (2026-09-28)
=====================================
目的：讓「依身分決定輸出目錄」的權威來源（core/output_router.py）真正接到前端——
      在不改核心 mok_web.py 的前提下，把 output_dir 傳進 mokagi.process_message。

背景：
  第 1 層（未登入）需要 anon 沙盒 sid（cookie `mok_anon`），但 process_message
  是在 socketio 的背景執行緒被呼叫，那裡拿不到 flask request／cookie。
  故本補丁在「請求執行緒」先攔 resolve_tenant，把該使用者的 anon sid 記下來，
  再於 process_message 包裝層取用；取不到時退回 guest uuid（仍落在 _tmp/anon/）。
"""
import os
import sys

main = sys.modules['__main__']

try:
    import mokagi
except Exception:
    mokagi = None

try:
    import output_router
except Exception:
    output_router = None

# --- 1) 在請求執行緒攔 sid -------------------------------------------------
_SID_BY_USER = {}
_ORIG_RESOLVE = getattr(main, 'resolve_tenant', None)


def resolve_tenant(data=None):
    u = None
    try:
        u = _ORIG_RESOLVE(data) if _ORIG_RESOLVE else None
    except Exception:
        u = None
    try:
        from flask import request as _rq
        _sid = _rq.cookies.get('mok_anon')
        if u and _sid:
            _SID_BY_USER[str(u)] = _sid
    except Exception:
        pass
    return u


if _ORIG_RESOLVE is not None:
    main.resolve_tenant = resolve_tenant

# --- 2) 包裝 process_message 注入 output_dir -------------------------------
_ORIG_PM = getattr(mokagi, 'process_message', None) if mokagi else None



def _anon_sync(path):
    """回合結束後：把落進匿名沙盒的檔案登記進該 sid 的 meta。"""
    try:
        m = getattr(main, 'mok_anon', None)
        if not m or not path:
            return
        root = m.get('root')
        sync = m.get('sync_disk_files')
        if not root or not sync:
            return
        rp = os.path.realpath(str(path))
        rr = os.path.realpath(root)
        if not rp.startswith(rr + os.sep):
            return
        sync(os.path.basename(rp))
    except Exception:
        pass


async def process_message(*args, **kwargs):
    if output_router is not None and _ORIG_PM is not None and not kwargs.get('output_dir'):
        try:
            user_id = kwargs.get('user_id')
            if user_id is None and args:
                user_id = args[0]
            agent_name = kwargs.get('agent_name')
            sid = _SID_BY_USER.get(str(user_id)) if user_id is not None else None
            path, role = output_router.output_dir_for_request(user_id, agent_name, sid=sid)
            output_router.ensure_dir(path)
            kwargs['output_dir'] = path
            if sid:
                kwargs.setdefault('anon_sid', sid)
        except Exception:
            pass
    _res = await _ORIG_PM(*args, **kwargs)
    _anon_sync(kwargs.get('output_dir'))
    return _res


if _ORIG_PM is not None:
    mokagi.process_message = process_message

print('[產物三層落點] ✅ 補丁已載入（依身分決定輸出目錄 -> mokagi.process_message）')
