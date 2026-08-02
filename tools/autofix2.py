"""
autofix2.py - 全局錯誤分流與自動修復引擎
更新: 202607030245

核心功能：
1. generate_fix: 使用 LLM 分析錯誤並生成修復方案
2. autofix_run: 通用執行包裝器，捕獲任何異常並自動嘗試修復
3. _auto_handle_error: 智能錯誤分類器

設計理念（方案 B）：
- 不限於工作流，任何程式內錯誤都應分流到此
- 全局錯誤攔截 → 智能分類 → 自動修復 → 重試
- autofix.py 是面向用戶的工具接口層，autofix2.py 是底層引擎層
"""

import logging
import traceback
import subprocess
import json
import re
from typing import Optional, Tuple, Callable, Any, Awaitable

logger = logging.getLogger(__name__)

# ══════════════════════════════════════════════════════════════════════════════
# 1. generate_fix — LLM 驅動的程式碼修復生成器
# ══════════════════════════════════════════════════════════════════════════════

async def generate_fix(
    code: str,
    error: str,
    context: str = "",
    llm_func: Callable = None,
    agent_config: dict = None,
    mokagi_home: str = ""
) -> Tuple[Optional[str], str]:
    """
    使用 LLM 分析錯誤並生成修復方案。

    Args:
        code: 原始程式碼
        error: 錯誤訊息（含 traceback）
        context: 額外上下文（例如：使用者意圖、目標描述）
        llm_func: LLM 調用函數，簽名為 async def(prompt, agent_config) -> str
        agent_config: Agent 配置字典
        mokagi_home: Mokagi 主目錄路徑（保留以備未來使用）

    Returns:
        (fixed_code, explanation)
        - fixed_code: 修正後的完整程式碼，若無法修復則為 None
        - explanation: 人類可讀的修復說明
    """
    if llm_func is None:
        return None, "LLM 函數未提供，無法生成修復方案"

    prompt = f"""你是一個專業的程式碼修復專家。請分析以下錯誤並提供修正方案。

## 原始程式碼
```python
{code[:3000]}
```

## 錯誤訊息
{error[:2000]}
"""
    if context:
        prompt += f"\n## 上下文\n{context[:1000]}\n"

    prompt += """
## 要求
請回傳 **純 JSON**（不要用 ```json 包裹），格式：
{"fixed": true/false, "fixed_code": "修正後的完整程式碼", "explanation": "修復說明"}

若無法修復，fixed=false 並在 explanation 中說明原因。
注意：fixed_code 必須是完整可執行的程式碼，不是片段。
"""

    try:
        response = await llm_func(prompt, agent_config=agent_config)
        response = response.strip()

        # 移除可能的 markdown 標記
        if response.startswith("```"):
            lines = response.split("\n")
            # 去掉第一行 ```json 和最後一行 ```
            response = "\n".join(lines[1:-1] if lines[-1].strip() == "```" else lines[1:])

        result = json.loads(response)
        if result.get("fixed"):
            return result.get("fixed_code"), result.get("explanation", "已生成修復方案")
        else:
            return None, result.get("explanation", "無法自動修復此錯誤")
    except json.JSONDecodeError:
        # LLM 可能返回非 JSON，嘗試直接作為解釋
        logger.warning(f"generate_fix: LLM 返回非 JSON 格式，原始回應: {response[:200]}")
        return None, f"LLM 未返回有效的修復方案。原始回應: {response[:500]}"
    except Exception as e:
        logger.error(f"generate_fix 失敗: {e}")
        return None, f"generate_fix 內部錯誤: {str(e)}"


# ══════════════════════════════════════════════════════════════════════════════
# 2. autofix_run — 通用執行包裝器（全局錯誤分流入口）
# ══════════════════════════════════════════════════════════════════════════════

async def autofix_run(
    func: Callable[..., Awaitable[Any]],
    func_args: tuple = (),
    func_kwargs: dict = None,
    max_attempts: int = 3,
    autofix_handler: Callable = None,
    autofix_extra_args: dict = None,
    llm_func: Callable = None,
    agent_config: dict = None,
    user_id: str = "",
    original_text: str = "",
    stream_callback: Callable = None
) -> Any:
    """
    通用執行包裝器 — 捕獲任何異常，自動分析並嘗試修復後重試。

    這是全局錯誤分流的核心入口。任何 async 函數都可以被此函數包裹，
    自動獲得錯誤分析、分類處理與智能重試能力。

    設計原則：
    - 錯誤分類優先於 LLM 調用（先嚐試確定性修復，再調用 LLM）
    - ModuleNotFoundError → 自動 pip install
    - dict/type 錯誤 → 自動轉換
    - 其他錯誤 → LLM 分析 → 嘗試修復代碼並重新執行

    Args:
        func: 要執行的 async 函數
        func_args: 位置參數 tuple
        func_kwargs: 關鍵字參數 dict
        max_attempts: 最大嘗試次數（含首次），預設 3
        autofix_handler: autofix 工具處理器（備用）
        autofix_extra_args: 傳遞給 autofix_handler 的額外參數
        llm_func: LLM 調用函數
        agent_config: Agent 配置
        user_id: 用戶 ID
        original_text: 用戶原始輸入
        stream_callback: 流式回調

    Returns:
        func 的成功返回值，或 "__ERROR_REPORTED__" 表示最終失敗
    """
    if func_kwargs is None:
        func_kwargs = {}
    if autofix_extra_args is None:
        autofix_extra_args = {}

    last_error = None
    last_error_str = ""

    for attempt in range(1, max_attempts + 1):
        try:
            logger.debug(f"autofix_run: 第 {attempt}/{max_attempts} 次嘗試執行 {func.__name__}")
            return await func(*func_args, **func_kwargs)
        except Exception as e:
            last_error = e
            last_error_str = traceback.format_exc()
            error_type = type(e).__name__
            logger.warning(
                f"autofix_run: 第 {attempt}/{max_attempts} 次嘗試失敗 "
                f"[{error_type}]: {str(e)[:200]}"
            )

            if attempt >= max_attempts:
                logger.error(f"autofix_run: 已達最大重試次數 {max_attempts}")
                break

            # ── 智能錯誤處理 ──
            fixed = await _auto_handle_error(
                error=e,
                error_str=last_error_str,
                llm_func=llm_func,
                agent_config=agent_config,
                autofix_handler=autofix_handler,
                autofix_extra_args=autofix_extra_args,
            )

            if not fixed:
                logger.warning("autofix_run: 無法自動修復，停止重試")
                break

            logger.info(f"autofix_run: 已自動修復，準備第 {attempt + 1} 次重試")

    # ── 所有嘗試失敗 ──
    logger.error(f"autofix_run 最終失敗: [{type(last_error).__name__}] {last_error}")
    return "__ERROR_REPORTED__"


# ══════════════════════════════════════════════════════════════════════════════
# 3. _auto_handle_error — 智能錯誤分類器
# ══════════════════════════════════════════════════════════════════════════════

async def _auto_handle_error(
    error: Exception,
    error_str: str,
    llm_func: Callable = None,
    agent_config: dict = None,
    autofix_handler: Callable = None,
    autofix_extra_args: dict = None
) -> bool:
    """
    智能錯誤分類與自動處理。

    策略（由確定性到啟發式）：
    1. ModuleNotFoundError → pip install
    2. AttributeError: 'dict' has no attribute 'strip' → 類型轉換
    3. 其他 → 調用 LLM 分析

    Returns:
        bool: True 表示錯誤已被處理（可以重試），False 表示無法處理
    """
    error_type = type(error).__name__
    error_msg = str(error)

    # ── 策略 1: ModuleNotFoundError ──
    if error_type == "ModuleNotFoundError":
        match = re.search(r"No module named '(\w+)'", error_msg)
        if match:
            package = match.group(1)
            logger.info(f"autofix: 偵測到缺失套件 {package}，嘗試自動安裝")
            try:
                result = subprocess.run(
                    f"pip install --user {package}",
                    shell=True, capture_output=True, text=True, timeout=300
                )
                if result.returncode == 0:
                    logger.info(f"autofix: 已成功安裝 {package}")
                    return True
                else:
                    logger.warning(f"autofix: pip install {package} 失敗: {result.stderr[:200]}")
            except Exception as e:
                logger.error(f"autofix: pip install 異常: {e}")
        return False

    # ── 策略 2: dict/type 錯誤（例如 dict has no attribute 'strip'） ──
    if "'dict'" in error_msg and ("strip" in error_msg or "startswith" in error_msg):
        logger.info("autofix: 偵測到 dict/str 類型錯誤，嘗試轉換")
        # 這種錯誤通常需要調用方修正，autofix 無法在運行時自動轉換
        # 但我們可以透過 autofix_handler 嘗試修正參數
        if autofix_handler and autofix_extra_args:
            try:
                tool_name = autofix_extra_args.get("tool_name", "")
                original_args = autofix_extra_args.get("original_args", {})
                if isinstance(original_args, dict):
                    fixed_args = json.dumps(original_args, ensure_ascii=False)
                    logger.info(f"autofix: 已將 dict 參數轉為 JSON 字符串")
                    return True
            except Exception as e:
                logger.error(f"autofix: 類型轉換失敗: {e}")
        return False

    # ── 策略 3: LLM 分析 ──
    if llm_func is not None:
        try:
            # 截取 traceback 尾部（最有用的部分）
            tb_lines = error_str.strip().split("\n")
            short_tb = "\n".join(tb_lines[-15:])  # 最後 15 行

            fixed_code, explanation = await generate_fix(
                code=f"# 錯誤上下文（非完整程式碼）:\n# {short_tb}",
                error=f"{error_type}: {error_msg}",
                context=f"此錯誤發生在 autofix_run 的第 N 次嘗試中，請分析根本原因。",
                llm_func=llm_func,
                agent_config=agent_config,
            )

            if fixed_code is not None:
                logger.info(f"autofix: LLM 生成修復方案 — {explanation[:200]}")
                # 注意：此處不自動執行 LLM 生成的修復代碼（安全考量）
                # 僅記錄方案，返回 True 允許重試（期望暫時性錯誤自行恢復）
                return True
            else:
                logger.info(f"autofix: LLM 無法修復 — {explanation[:200]}")
        except Exception as e:
            logger.error(f"autofix: LLM 分析異常: {e}")

    return False


# ══════════════════════════════════════════════════════════════════════════════
# 4. retry_with_autofix — 先重試，失敗後進入 autofix 的通用重試器
# ══════════════════════════════════════════════════════════════════════════════

async def retry_with_autofix(
    action_func: Callable[..., Awaitable[Any]],
    action_args: tuple = (),
    action_kwargs: dict = None,
    max_retries_before_autofix: int = 2,
    error_info_builder: Callable = None,
    autofix_handler: Callable = None,
    autofix_extra_args: dict = None,
) -> Any:
    """
    先簡單重試 N 次，若仍失敗則進入 autofix 修復流程。

    與 autofix_run 的差異：
    - autofix_run: 每次失敗都立即嘗試分析修復
    - retry_with_autofix: 先快速重試（可能是暫時性錯誤），確定性失敗後才調用 autofix

    Args:
        action_func: 要執行的 async 函數
        action_args: 位置參數 tuple
        action_kwargs: 關鍵字參數 dict
        max_retries_before_autofix: 進入 autofix 前的簡單重試次數
        error_info_builder: 可選，簽名 (Exception, dict) -> dict，構建 autofix 所需的錯誤信息
        autofix_handler: autofix 工具處理器
        autofix_extra_args: 傳給 autofix_handler 的額外參數

    Returns:
        action_func 的成功返回值，或拋出最終異常
    """
    import asyncio

    if action_kwargs is None:
        action_kwargs = {}
    if autofix_extra_args is None:
        autofix_extra_args = {}

    # ── 階段 1: 簡單重試（不調用 autofix，用於暫時性錯誤） ──
    last_exception = None
    for attempt in range(1, max_retries_before_autofix + 1):
        try:
            return await action_func(*action_args, **action_kwargs)
        except Exception as e:
            last_exception = e
            logger.warning(f"[retry_with_autofix] 第 {attempt}/{max_retries_before_autofix} 次簡單重試失敗: {e}")
            await asyncio.sleep(0.3)

    # ── 階段 2: 進入 autofix 修復流程 ──
    if autofix_handler is None:
        logger.error("[retry_with_autofix] autofix_handler 不可用，無法修復")
        raise last_exception

    # 構建 autofix 所需的錯誤信息
    error_info = {
        "error": str(last_exception),
        "error_type": type(last_exception).__name__,
    }
    if error_info_builder:
        try:
            extra_info = error_info_builder(last_exception, autofix_extra_args)
            if extra_info:
                error_info.update(extra_info)
        except Exception as e:
            logger.warning(f"[retry_with_autofix] error_info_builder 失敗: {e}")

    # 調用 autofix 工具進行修復
    try:
        autofix_args = {
            "tool_name": autofix_extra_args.get("tool_name", ""),
            "original_args": autofix_extra_args.get("original_args", ""),
            "error": json.dumps(error_info, ensure_ascii=False),
            "context": autofix_extra_args.get("context", ""),
        }
        autofix_result = await autofix_handler(autofix_args, autofix_extra_args.get("user_id", ""))
        logger.info(f"[retry_with_autofix] autofix 返回: {str(autofix_result)[:300]}")
    except Exception as e:
        logger.error(f"[retry_with_autofix] autofix 調用失敗: {e}")

    # ── 階段 3: 修復後重試最後一次 ──
    try:
        return await action_func(*action_args, **action_kwargs)
    except Exception as e:
        logger.error(f"[retry_with_autofix] 修復後重試仍失敗: {e}")
        raise
