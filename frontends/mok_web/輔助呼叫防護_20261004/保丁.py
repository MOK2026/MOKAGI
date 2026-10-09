# -*- coding: utf-8 -*-
"""
輔助呼叫防護補丁 v3  (2026-10-04 by 凜；2026-10-04 修正：撤除 32768 墊高，改由 core disable_thinking 關推理)
========================================
對應故障：推理模型（如 deepseek-v4-flash/pro）把「輸出額度」全用在思考，
          正文回空（finish_reason='length'、content=''、reasoning 滿），
          導致：前端看不到回答、且反覆燒 token。治本＝輔助呼叫關推理（core disable_thinking）；本補丁保留最後一道「空正文自癒」。

本補丁「不改核心」，於 mokagi.call_llm 外層只留一道「空正文自癒」防護：

  A.（已撤除 2026-10-04 凜）不再墊高輔助呼叫的 num_predict。
     改由 core.call_llm 的 disable_thinking=True（送 thinking.type=disabled）從源頭關推理，
     小額度（4096／1024／256）就夠用，無需墊到 32768。

  B. 空正文自癒（僅自癒時提高額度）
     B1 非流式回空 → 額度 ×2（下限 _SELFHEAL_FLOOR）後「原樣」重呼一次。
     B2 流式整條只吐思考、既無正文也無工具呼叫 → 額度 ×2 後「原樣」續串一次，工具保留。

停用：把本目錄改名（前面加 _）即停用（載入器會跳過）。
調整：環境變數 MOK_AUX_SELFHEAL_FLOOR 可改自癒下限（預設 2048）。
"""
import os
import sys
import logging

_SELFHEAL_FLOOR = int(os.environ.get("MOK_AUX_SELFHEAL_FLOOR", "2048"))

_orig_call_llm = None
_ENABLED = True

try:
    import mokagi as _mokagi
except Exception as e:                      # noqa
    print("[輔助防護] import mokagi 失敗，補丁停用: %r" % (e,), flush=True)
    _ENABLED = False

if _ENABLED:
    _orig_call_llm = getattr(_mokagi, "call_llm", None)
    if _orig_call_llm is None:
        print("[輔助防護] 找不到 mokagi.call_llm，補丁停用", flush=True)
        _ENABLED = False

_EMPTY_NUDGE = ("（系統）上一輪的輸出在寫完正文前就被截斷了。請接續完成，"
                "並輸出最終要給使用者看的完整答案。")


# _bump_aux 已於 2026-10-04 撤除（不再墊高輔助呼叫額度）。


def _reinvoke(args, kwargs, nudge):
    """提高輸出上限後「原樣」重呼一次（不關思考、不關工具）。"""
    kw2 = dict(kwargs)
    kw2["num_predict"] = max(int(kw2.get("num_predict") or 0) * 2, _SELFHEAL_FLOOR)
    msgs = kw2.get("messages")
    if msgs:
        kw2["messages"] = list(msgs) + [{"role": "user", "content": nudge}]
    else:
        kw2["prompt"] = (kw2.get("prompt") or "") + "\n\n" + nudge
    return _orig_call_llm(*args, **kw2)


if _ENABLED:

    async def _call_llm_guarded(*args, **kwargs):
        _stream = kwargs.get("stream")

        # ---- A. 已撤除（2026-10-04 凜）：不再墊高輔助呼叫額度 ----

        res = await _orig_call_llm(*args, **kwargs)

        # 測試模式等會直接回字串，原樣放行
        if isinstance(res, str):
            if (_stream in (False, None)) and (not res.strip()):
                logging.warning("[輔助防護] 輔助呼叫回空內容 → 提高額度後原樣重試一次")
                try:
                    res2 = await _reinvoke(args, kwargs, _EMPTY_NUDGE)
                    if isinstance(res2, str) and res2.strip():
                        return res2
                    if isinstance(res2, dict) and (res2.get("content") or "").strip():
                        return res2
                except Exception as e:       # noqa
                    logging.warning("[輔助防護] 重試失敗: %s", e)
            return res

        # ---- B2. 流式：只有思考、無正文且無工具呼叫 → 提高額度後原樣續串一次（工具保留） ----
        if _stream and hasattr(res, "__aiter__"):

            async def _gen():
                _saw_reply = False
                _saw_tools = False
                _started = False
                try:
                    async for ev in res:
                        _started = True
                        try:
                            _t = ev.get("type") if isinstance(ev, dict) else None
                        except Exception:
                            _t = None
                        if _t == "reply":
                            _saw_reply = True
                        elif _t == "tool_calls":
                            _saw_tools = True
                        yield ev
                except Exception:
                    raise
                if (not _started) or (not _saw_reply and not _saw_tools):
                    logging.warning("[輔助防護] 流式回合只有思考、正文為空 → 提高額度後原樣續串一次")
                    try:
                        _res2 = await _reinvoke(args, kwargs, _EMPTY_NUDGE)
                        if hasattr(_res2, "__aiter__"):
                            async for ev2 in _res2:
                                yield ev2
                    except Exception as e:   # noqa
                        logging.warning("[輔助防護] 流式降級失敗: %s", e)

            return _gen()

        return res

    _mokagi.call_llm = _call_llm_guarded
    print("[輔助防護] 已掛載：空正文自癒（×2，下限 %d）；已撤除 32768 墊高" % _SELFHEAL_FLOOR,
          flush=True)
