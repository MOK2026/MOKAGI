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

__all__ = ["_agent_config_ctx"]
