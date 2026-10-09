# -*- coding: utf-8 -*-
"""20260923 凜：Agent 配置 contextvar。

用於在單一協程 / 任務內安全傳遞「當前處理中的 agent_config」，
避免多 Agent / 多請求並發時互相汙染全局 _agent_config。

用法：
    from context import _agent_config_ctx
    _agent_config_ctx.set(cfg)      # 於 process_message 入口設定
    cfg = _agent_config_ctx.get()   # 讀取；未設定時為 None
"""

import contextvars

# 當前協程上下文中的 agent_config；None 表示尚未設定
_agent_config_ctx = contextvars.ContextVar("agent_config", default=None)

# ── 來源平台旗標（2026-10-08 indexPage：修法B）────────────────
# 由前端入口經 process_message(platform=...) 傳入（mok_web 傳 "web"）。
# 目的：同一 chat_id 值在不同平台語義不同（網頁會員帳號是數字，易與 TG chat_id 混淆），
#       讓工具層（如 tts）能依平台正確分流，且不寫死單一平台。
_platform_ctx = contextvars.ContextVar("mok_platform", default=None)


def set_platform(platform):
    """設定當前協程的來源平台；None／空字串視為清除。"""
    return _platform_ctx.set(platform or None)


def get_platform(default=None):
    """讀取當前協程的來源平台；未設定時回傳 default。"""
    return _platform_ctx.get() or default


__all__ = ["_agent_config_ctx", "_platform_ctx", "set_platform", "get_platform"]
