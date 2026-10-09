#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mok_cache_metrics.py - LLM 快取命中量測（P1）

建立：2026-10-01 市場調查侍女
目的：把「前綴快取命中」資訊抽出，塞進 token_usage.extra，讓省錢成效可被量測。

支援：
  - OpenAI 兼容：usage.prompt_tokens_details.cached_tokens
  - DeepSeek    ：usage.prompt_cache_hit_tokens / usage.prompt_cache_miss_tokens
任何異常一律吞掉，絕不影響主流程與計費。
"""

# 上游是否支援 stream_options={"include_usage": True}
# 一旦失敗即永久關閉（本進程），避免每輪都撞一次 400。
_USE_STREAM_OPTIONS = True


def _g(obj, key):
    """安全取值：同時支援 dict 與物件屬性。"""
    try:
        if obj is None:
            return None
        if isinstance(obj, dict):
            return obj.get(key)
        return getattr(obj, key, None)
    except Exception:
        return None


def cache_extra(usage, purpose: str = "openai_api") -> dict:
    """回傳可直接餵給 log_token_usage(extra=...) 的 dict。"""
    try:
        details = _g(usage, "prompt_tokens_details")
        hit = _g(details, "cached_tokens")
        source = "openai_details" if hit is not None else None
        if hit is None:
            hit = _g(usage, "prompt_cache_hit_tokens")
            if hit is not None:
                source = "deepseek"
        miss = _g(usage, "prompt_cache_miss_tokens")
        return {
            "purpose": purpose,
            "cached_tokens": int(hit) if hit is not None else 0,
            "cache_miss_tokens": int(miss) if miss is not None else 0,
            "cache_source": source,
        }
    except Exception:
        return {"purpose": purpose, "cached_tokens": 0,
                "cache_miss_tokens": 0, "cache_source": None}


def stream_options_kwargs() -> dict:
    """串流請求要附加的 kwargs（未支援時回空 dict）。"""
    if _USE_STREAM_OPTIONS:
        return {"stream_options": {"include_usage": True}}
    return {}


def disable_stream_options(reason: str = "") -> None:
    """永久關閉 stream_options（本進程）。"""
    global _USE_STREAM_OPTIONS
    _USE_STREAM_OPTIONS = False


async def stream_with_usage(stream, log_fn, ctx: dict):
    """包住 async 串流：收集尾端 chunk 的 usage，串流結束（含提早關閉）時寫用量。

    log_fn : 通常是 mokagi.log_token_usage
    ctx    : {"user_id","agent_name","model_name","conversation_id","workflow_id"}
    任何異常都吞掉，絕不影響主流程。
    """
    usage = None
    try:
        async for ch in stream:
            u = _g(ch, "usage")
            if u is not None:
                usage = u
            yield ch
    finally:
        try:
            if usage is not None:
                log_fn(
                    user_id=ctx.get("user_id", ""),
                    agent_name=ctx.get("agent_name", "unknown"),
                    model_name=ctx.get("model_name", ""),
                    prompt_tokens=int(_g(usage, "prompt_tokens") or 0),
                    completion_tokens=int(_g(usage, "completion_tokens") or 0),
                    total_tokens=int(_g(usage, "total_tokens") or 0),
                    conversation_id=ctx.get("conversation_id"),
                    workflow_id=ctx.get("workflow_id"),
                    extra=cache_extra(usage, "openai_api_stream"),
                )
        except Exception:
            pass
