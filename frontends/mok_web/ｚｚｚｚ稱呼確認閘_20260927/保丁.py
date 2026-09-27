# -*- coding: utf-8 -*-
# 稱呼確認閘 v1  (2026-09-27 by 稚)
# 目的：1) 稱呼三層制：未登入(FREE)=「用戶」；已登入(PRO/VIP)=會員名；admin=「主人」。
#       2) 未登入不得使用確認碼流程（需管理員權限的操作）。
# 手法：記憶體級包裝，不改核心檔、不覆寫路由。
import os
import sys
import sqlite3
import contextvars

main = sys.modules.get('__main__')
app = getattr(main, 'app', None)

import mokagi as _mokagi

_PATCH_DIR = os.path.dirname(os.path.abspath(__file__))
_WEB_DIR = os.path.dirname(_PATCH_DIR)
MEMBER_DB = os.path.join(_WEB_DIR, '會員系統_202608311340', 'member.db')

ADMIN_USERS = set()   # 2026-09-27：不再用帳號名白名單；admin 只認 member.db users.is_admin=1
GUEST_NAME = '用戶'

_TIER = contextvars.ContextVar('mok_caller_tier', default=None)


def _lookup_member(username):
    try:
        con = sqlite3.connect(MEMBER_DB)
        try:
            r = con.execute('SELECT display_name, plan, is_admin FROM users WHERE username=?', (username,)).fetchone()
        finally:
            con.close()
        if r:
            return {'display_name': r[0] or '', 'plan': r[1] or 'free', 'is_admin': bool(r[2])}
    except Exception:
        pass
    return None


def _classify_caller(user_id):
    uid = str(user_id or '').strip()
    if not uid or uid.isdigit():
        return None
    if uid in ADMIN_USERS:
        return {'tier': 'admin', 'name': '主人', 'username': uid}
    if uid.startswith('web_guest_') or uid.startswith('guest:'):
        return {'tier': 'guest', 'name': GUEST_NAME, 'username': None}
    info = _lookup_member(uid)
    if info is None:
        return {'tier': 'guest', 'name': GUEST_NAME, 'username': None}
    if info.get('is_admin'):
        return {'tier': 'admin', 'name': (info.get('display_name') or '主人'), 'username': uid}
    return {'tier': 'member', 'name': (info.get('display_name') or uid), 'username': uid, 'plan': info.get('plan')}



def _apply_admin_gate():
    try:
        import tool_handler
        admin_mod = tool_handler.get_tools().get('admin')
    except Exception:
        admin_mod = None
    if admin_mod is None or getattr(admin_mod, '_tier_gated', False):
        return
    _orig_is_admin = getattr(admin_mod, 'is_admin', None)
    if _orig_is_admin is None:
        return

    def _gated_is_admin(chat_id, agent_config=None):
        if agent_config is None:
            agent_config = getattr(_mokagi, '_agent_config', {}) or {}
        flag = agent_config.get('MOK_CALLER_IS_ADMIN') if isinstance(agent_config, dict) else None
        if flag is not None and str(flag) != '':
            return str(flag) == '1'
        return _orig_is_admin(chat_id, agent_config)

    admin_mod.is_admin = _gated_is_admin

    _orig_confirm = getattr(admin_mod, 'confirm_command', None)
    if _orig_confirm is not None:
        async def _gated_confirm_command(chat_id, token, agent_config=None):
            if not _gated_is_admin(chat_id, agent_config):
                return (False, '權限不足：未登入或非管理員無法使用確認碼流程。')
            return await _orig_confirm(chat_id, token, agent_config)
        admin_mod.confirm_command = _gated_confirm_command

    admin_mod._tier_gated = True
    print('[稱呼確認閘] tools admin gate applied')


_apply_admin_gate()


_orig_pm = getattr(_mokagi, 'process_message', None)
if _orig_pm is not None and not getattr(_orig_pm, '_tier_aware', False):

    async def _tier_aware_process_message(*args, **kwargs):
        try:
            _apply_admin_gate()
            uid = kwargs.get('user_id', args[0] if args else None)
            ident = _classify_caller(uid)
            if ident is not None:
                _TIER.set(ident)
                cfg = kwargs.get('agent_config', args[4] if len(args) > 4 else None)
                if isinstance(cfg, dict):
                    cfg2 = dict(cfg)
                    cfg2['MOK_ADMIN_NAME'] = ident['name']
                    cfg2['MOK_CALLER_IS_ADMIN'] = '1' if ident['tier'] == 'admin' else '0'
                    cfg2['MOK_CALLER_TIER'] = ident['tier']
                    if ident.get('username'):
                        cfg2['MOK_CALLER_USERNAME'] = ident['username']
                    if 'agent_config' in kwargs:
                        kwargs['agent_config'] = cfg2
                    elif len(args) > 4:
                        _a = list(args)
                        _a[4] = cfg2
                        args = tuple(_a)
        except Exception as _e:
            print('[稱呼確認閘] process_message 包裝警告:', _e)
        return await _orig_pm(*args, **kwargs)

    _tier_aware_process_message._tier_aware = True
    _mokagi.process_message = _tier_aware_process_message
    print('[稱呼確認閘] process_message wrapped')


_orig_gsc = getattr(_mokagi, 'get_system_context', None)
if _orig_gsc is not None and not getattr(_orig_gsc, '_addr_aware', False):

    def _addr_aware_get_system_context(agent_name, owner, owner_time=0, context_files=None):
        try:
            body = _orig_gsc(agent_name, owner, owner_time, context_files=context_files)
        except TypeError:
            body = _orig_gsc(agent_name, owner, owner_time)
        try:
            if owner:
                body = (body or '') + (
                    '\n\n【稱呼】本次對話對象的稱呼為「' + str(owner) + '」。'
                    '請一律以「' + str(owner) + '」稱呼對方，不得自行改用其他稱謂；'
                    '尤其對未登入訪客一律稱「' + GUEST_NAME + '」，不可稱「主人」。')
            ident = _TIER.get()
            if ident and ident.get('tier') == 'guest':
                body = (body or '') + (
                    '\n\n【訪客權限】對方為未登入訪客（FREE）。僅可：一般聊天、搜尋網頁、讀取文件、'
                    '生成一頁式網頁、生成語音、生成圖片/影片（限免費 skill 類）。'
                    '不得增減主機內容或修改系統；任何需要管理員權限的操作一律婉拒，'
                    '並提示登入會員或聯絡管理員。')
        except Exception:
            pass
        return body

    _addr_aware_get_system_context._addr_aware = True
    _mokagi.get_system_context = _addr_aware_get_system_context
    print('[稱呼確認閘] get_system_context wrapped')
