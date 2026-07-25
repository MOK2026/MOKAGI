#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
autofix2.py — 通用重試與自動修復包裝器（避免與 mokagi.py 循環導入）

功能：
- 對任何異步函數嘗試指定次數（預設 2 次），若全部失敗則自動呼叫 autofix 工具進行修復。
- 若 autofix 成功（返回 {"fixed": true, "args":...}），使用修正後的參數再重試一次。
- 若 autofix 亦失敗，可通過 `stream_error_callback` 輸出經過 LLM 精煉的錯誤報告（原因 + 解決建議）。
- 若未提供 `stream_error_callback`，仍拋出最終異常（向後兼容）。

使用方式（在 mokagi.py 中）：
    from autofix2 import retry_with_autofix
    result = await retry_with_autofix(
        action_func=handler,
        action_args=(),
        action_kwargs={"args": tool_args, "chat_id": user_id, "agent_config": agent_config},
        autofix_handler=find_tool_handler("autofix"),
        autofix_extra_args={"user_id": user_id, "agent_config": agent_config},
        stream_error_callback=_send,          # 用於輸出錯誤報告
        llm_func=mokagi.call_llm              # 用於生成報告的 LLM 函數
    )
"""

import json
import logging
import asyncio
from typing import Callable, Optional, Dict, Any, Awaitable

async def _generate_error_report(
    original_prompt: str,
    error_message: str,
    tool_name: str,
    tool_args: dict,
    llm_func: Optional[Callable] = None,
    agent_config: dict = None,
    user_id: str = ""
) -> str:
    """
    使用 LLM 生成友好的錯誤報告：原因分析 + 解決建議。
    若 llm_func 為 None，則返回簡單的格式化文本。
    """
    if llm_func is not None and agent_config:
        # 構建 prompt
        prompt = (
            f"工具調用失敗。工具名稱：{tool_name}\n"
            f"參數：{json.dumps(tool_args, ensure_ascii=False)}\n"
            f"錯誤訊息：{error_message}\n"
            f"原始上下文：{original_prompt}\n\n"
            f"詳細總結：1. 錯誤原因 2. 解決建議。"
        )
        try:
            response = await llm_func(
                prompt=prompt,
                user_id=user_id,
                agent_config=agent_config,
                stream=False,
                temperature=0.3,
                #max_tokens=300
            )
            if isinstance(response, dict):
                response = response.get("content", "")
            return response.strip()
        except Exception as e:
            logging.warning(f"錯誤報告生成失敗：{e}")
            # 回退到模板
            pass

    # 無 LLM 時的靜態模板
    return (
        f"❌ 工具 **{tool_name}** 執行失敗（已嘗試自動修復）\n"
        f"錯誤原因：{error_message}\n"
        f"建議：檢查參數是否正確，或聯繫管理員。"
    )


async def retry_with_autofix(
    action_func: Callable[..., Awaitable[Any]],
    action_args: tuple = (),
    action_kwargs: dict = None,
    max_retries_before_autofix: int = 2,
    error_info_builder: Optional[Callable[[Exception, dict], dict]] = None,
    autofix_handler: Optional[Callable] = None,
    autofix_extra_args: dict = None,
    # ===== 新增參數 =====
    stream_error_callback: Optional[Callable[[dict], Awaitable[None]]] = None,
    llm_func: Optional[Callable] = None,       # 用於生成錯誤報告的 LLM 函數
    original_user_text: str = "",              # 用戶原始輸入文本（用於報告上下文）
) -> Any:
    """
    通用重試 + 自動修復包裝器。

    參數：
        action_func           : 要執行的異步函數
        action_args           : 位置參數
        action_kwargs         : 關鍵字參數
        max_retries_before_autofix : 在呼叫 autofix 前的重試次數（預設 2）
        error_info_builder    : 函數 (exception, action_kwargs) -> dict，用來建構傳給 autofix 的錯誤資訊
        autofix_handler       : 自動修復處理函數（例如 handle_autofix）
        autofix_extra_args    : 額外傳給 autofix_handler 的參數（如 user_id, agent_config 等）
        stream_error_callback : 最終失敗時的流式回調，接收 {"type": "error_report", "content": ...}
        llm_func              : 用於生成錯誤報告的 LLM 函數，簽名如 mokagi.call_llm(prompt=.., stream=False, ...)
        original_user_text    : 用戶原始輸入（可選）

    回傳：
        action_func 成功後的回傳值。
        若最終失敗且提供了 stream_error_callback，則返回 "__ERROR_REPORTED__" 並傳送錯誤報告；
        否則拋出最後一次異常（向後兼容）。
    """
    if action_kwargs is None:
        action_kwargs = {}
    if autofix_extra_args is None:
        autofix_extra_args = {}

    last_exception = None
    # 提取工具名稱（用於錯誤報告）
    tool_name = action_kwargs.get("args", {}).get("tool_name", "未知工具")  # 可根據實際結構調整
    # 也可從 action_kwargs 中獲取 agent_config、user_id
    agent_config = autofix_extra_args.get("agent_config", {})
    user_id = autofix_extra_args.get("user_id", "")

    # ---------- 第一階段：嘗試執行 ----------
    for attempt in range(1, max_retries_before_autofix + 1):
        try:
            return await action_func(*action_args, **action_kwargs)
        except Exception as e:
            last_exception = e
            logging.warning(
                f"[retry_with_autofix] 第 {attempt} 次嘗試失敗 ({type(e).__name__}): {str(e)[:100]}"
            )
            await asyncio.sleep(0.3)
            continue

    # ---------- 第二階段：呼叫 autofix 修復 ----------
    if autofix_handler is not None:
        # 建構錯誤資訊
        if error_info_builder:
            error_info = error_info_builder(last_exception, action_kwargs)
        else:
            error_info = {
                "error": f"{type(last_exception).__name__}: {str(last_exception)}",
                "original_args": json.dumps(action_kwargs, ensure_ascii=False)
            }
        error_info.update(autofix_extra_args)

        try:
            fix_result = await autofix_handler(error_info, **autofix_extra_args)
        except Exception as autofix_e:
            logging.error(f"[retry_with_autofix] autofix_handler 本身出錯: {autofix_e}")
            # 若 autofix 本身出錯，視為修復失敗，繼續進入最終錯誤報告
            fix_result = None

        # 解析 autofix 結果
        if fix_result:
            if isinstance(fix_result, str):
                try:
                    fix_data = json.loads(fix_result)
                except json.JSONDecodeError:
                    fix_data = {"fixed": False, "error": "autofix 回傳非 JSON"}
            elif isinstance(fix_result, dict):
                fix_data = fix_result
            else:
                fix_data = {"fixed": False, "error": "autofix 回傳格式異常"}

            # 若修復成功，用修正後參數重試
            if fix_data.get("fixed"):
                fixed_args = fix_data.get("args")
                if fixed_args is not None:
                    if isinstance(fixed_args, dict):
                        action_kwargs.update(fixed_args)
                    else:
                        action_kwargs["args"] = fixed_args
                    logging.info("[retry_with_autofix] autofix 修復成功，使用修正後參數重試...")
                    try:
                        return await action_func(*action_args, **action_kwargs)
                    except Exception as e:
                        last_exception = e
                        logging.error(f"[retry_with_autofix] 修復後重試仍然失敗: {e}")
                        # 繼續進入最終錯誤報告

    # ---------- 最終階段：生成並輸出錯誤報告 ----------
    error_message = f"{type(last_exception).__name__}: {str(last_exception)}"
    # 使用 LLM 生成友好報告
    report = await _generate_error_report(
        original_prompt=original_user_text,
        error_message=error_message,
        tool_name=tool_name,
        tool_args=action_kwargs.get("args", {}),
        llm_func=llm_func,
        agent_config=agent_config,
        user_id=user_id
    )

    if stream_error_callback:
        # 流式輸出錯誤報告
        await stream_error_callback({"type": "error_report", "content": report})
        # 返回標記，表示錯誤已報告
        return "__ERROR_REPORTED__"
    else:
        # 無回調則拋出帶有報告的異常
        raise Exception(report)
    








# ===== 新增：通用異常處理循環 =====
async def autofix_run(
    func: Callable[..., Awaitable[Any]],
    func_args: tuple = (),
    func_kwargs: dict = None,
    max_attempts: int = 5,
    autofix_handler: Optional[Callable] = None,
    autofix_extra_args: dict = None,
    stream_callback: Optional[Callable[[dict], Awaitable[None]]] = None,
    llm_func: Optional[Callable] = None,
    agent_config: dict = None,
    user_id: str = "",
    original_text: str = ""
) -> Any:
    """
    自動修復執行循環。
    - 嘗試執行 func(*func_args, **func_kwargs)。
    - 若失敗，收集完整錯誤上下文（堆棧、代碼片段、變量），調用 LLM 分析並生成修復指令。
    - 通過 autofix_handler（或 admin 工具）執行修復（如修改代碼、安裝依賴、調整參數）。
    - 修復後重新嘗試，最多 max_attempts 次。
    - 若成功，返回結果；若最終失敗，拋出異常或通過 stream_callback 發送錯誤報告。
    """
    if func_kwargs is None:
        func_kwargs = {}
    if autofix_extra_args is None:
        autofix_extra_args = {}
    if agent_config is None:
        agent_config = {}

    attempt = 0
    last_exception = None

    while attempt < max_attempts:
        attempt += 1
        try:
            return await func(*func_args, **func_kwargs)
        except Exception as e:
            last_exception = e
            logging.error(f"[autofix_run] 第 {attempt} 次執行失敗: {type(e).__name__}: {str(e)}")
            if attempt == max_attempts:
                break

            # ---- 收集錯誤上下文 ----
            import traceback
            import sys
            tb = traceback.format_exc()
            # 嘗試獲取相關變量（簡化：只取局部變量）
            local_vars = {}
            try:
                # 獲取調用棧最後一幀的局部變量（謹慎，可能很大）
                tb_frame = sys.exc_info()[2].tb_frame
                local_vars = tb_frame.f_locals
            except:
                pass


            # ---- 構建修復提示 ----
            import os, glob
            import mokagi
            
            # ----- 讀取工作流日誌（當前 agent 的 logs 目錄）-----
            workflow_log_content = ""
            try:
                agent_name = agent_config.get("MOK_AGENT_NAME", "default")
                logs_dir = os.path.expanduser(f"~/.{mokagi.MOKAGI_home}/{agent_name}/logs")
                if os.path.exists(logs_dir):
                    # 獲取最近修改的 .md 文件（最多 5 個，按修改時間倒序）
                    all_files = glob.glob(os.path.join(logs_dir, "*.md"))
                    if all_files:
                        # 按修改時間排序，取最新的 3 個（避免過多）
                        sorted_files = sorted(all_files, key=os.path.getmtime, reverse=True)[:3]
                        # 嘗試匹配包含當前 user_id 的日誌
                        matched_log = None
                        for fpath in sorted_files:
                            try:
                                with open(fpath, 'r', encoding='utf-8') as f:
                                    content = f.read()
                                    # 查找文件頭中的 NoneID（通常格式為 **NoneID**: <user_id>）
                                    import re
                                    match = re.search(r'\*\*NoneID\*\*:\s*(\S+)', content)
                                    if match and match.group(1) == user_id:
                                        matched_log = fpath
                                        break
                            except Exception:
                                continue
                        # 如果沒找到匹配 user_id 的，就用最新的
                        if not matched_log and sorted_files:
                            matched_log = sorted_files[0]
                        
                        if matched_log:
                            with open(matched_log, 'r', encoding='utf-8') as f:
                                raw = f.read()
                                # 截斷至 2500 字符
                                workflow_log_content = raw[:2500]
                                if len(raw) > 2500:
                                    workflow_log_content += "\n... (日誌過長，已截斷)"
            except Exception as e:
                logging.warning(f"[autofix_run] 讀取工作流日誌失敗: {e}")

            # ✅ 將日誌部分單獨構建，避免 f-string 中的反斜槓
            log_section = ""
            if workflow_log_content:
                log_section = "\n【工作流執行日誌（摘要）】\n" + workflow_log_content

            prompt = f"""
工具/函數執行失敗，需要分析並修復。

錯誤類型：{type(e).__name__}
錯誤信息：{str(e)}
完整堆棧：
{tb}

相關局部變量（摘要）：
{json.dumps(local_vars, ensure_ascii=False, default=str, indent=2)[:1000]}

用戶原始輸入：{original_text}
當前 Agent 配置：{json.dumps(agent_config, ensure_ascii=False, default=str)}
{log_section}

請分析錯誤原因，並提供修復方案（需返回可執行的工具調用指令）。
如果需要修改代碼，請使用 `/admin exec` 或 `/admin read_file` 等工具。
若需要修改參數，請返回修正後的參數 JSON。
"""
            # ---- 調用 LLM 生成修復方案 ----
            if llm_func is not None:
                try:
                    llm_response = await llm_func(
                        prompt=prompt,
                        user_id=user_id,
                        agent_config=agent_config,
                        stream=False,
                        temperature=0.3,
                        # max_tokens=2000
                    )
                    if isinstance(llm_response, dict):
                        response_text = llm_response.get("content", "")
                    else:
                        response_text = str(llm_response)
                except Exception as llm_e:
                    logging.error(f"[autofix_run] LLM 調用失敗: {llm_e}")
                    response_text = ""
            else:
                response_text = ""

            # ---- 執行修復 ----
            if autofix_handler is not None and response_text:
                # 嘗試從 LLM 響應中提取修復指令（JSON 格式）
                try:
                    # 提取第一個 JSON 對象
                    import re
                    json_match = re.search(r'\{.*\}', response_text, re.DOTALL)
                    if json_match:
                        fix_data = json.loads(json_match.group())
                        if "action" in fix_data:
                            # 調用 autofix_handler 執行修復
                            await autofix_handler(fix_data, **autofix_extra_args)
                            # 等待一小段時間讓修復生效
                            await asyncio.sleep(1)
                            continue  # 重試
                except Exception as fix_e:
                    logging.error(f"[autofix_run] 執行修復失敗: {fix_e}")
                    # 繼續循環，下次可能再嘗試

            # 若沒有修復或修復失敗，則嘗試簡單重試（可能臨時性問題）
            await asyncio.sleep(0.5)
            continue

    # 所有嘗試失敗
    error_msg = f"自動修復循環失敗（{max_attempts}次嘗試），最後錯誤: {type(last_exception).__name__}: {str(last_exception)}"
    if stream_callback:
        await stream_callback({"type": "error_report", "content": error_msg})
        return "__ERROR_REPORTED__"
    else:
        raise Exception(error_msg)

# ===== 從 autofix.py 遷移：LLM 驅動的參數/代碼修正 =====
async def generate_fix(
    original_code: str,
    error_msg: str,
    context: str = "",
    tool_name: str = "",
    original_args: str = "",
    llm_func=None,
    agent_config: dict = None,
    mokagi_home: str = "MokAgi",
    MOK_ADMIN_NAME: str = "用戶",
    agent_name: str = "助手",
) -> tuple:
    """
    讓 LLM 產生修正後的程式碼或修正後的工具調用參數。
    
    參數：
        original_code: 原始程式碼
        error_msg: 錯誤訊息
        context: 額外上下文
        tool_name: 工具名稱（工具調用修正場景）
        original_args: 原始參數
        llm_func: LLM 調用函數，簽名如 mokagi.call_llm(prompt=..., stream=False, ...)
        agent_config: Agent 配置
        mokagi_home: MOKAGI 主目錄名（用於構建 prompt 中路徑）
        MOK_ADMIN_NAME: 管理員名稱
        agent_name: Agent 名稱
    
    返回：
        (修正結果, 說明) 或 (None, 錯誤訊息)
    """
    if llm_func is None:
        return None, "未提供 LLM 調用函數"

    if tool_name:
        # 工具調用參數修正場景
        prompt = f"""你是一個專業的 AI 助手。{MOK_ADMIN_NAME} 調用工具 `{tool_name}` 時發生錯誤。
原始參數：{original_args}
錯誤訊息：{error_msg}
{f"額外上下文：{context}" if context else ""}

工具目錄： ~/.{mokagi_home}/tools

用指令 /admin read_file 查看工具目錄，並分析錯誤原因，再輸出修正後的參數（僅輸出 JSON 對象，格式與該工具的參數定義一致）。
如果無法修正，輸出 {{"fixed": false}}。
如果能夠修正，輸出 {{"fixed": true, "args": 修正後的參數對象}}。
只輸出 JSON，不要有其他文字。"""
    else:
        # 代碼修正場景
        prompt = f"""
原始程式碼：
```python
{original_code}

錯誤訊息：
{error_msg}
```
{f"額外上下文：{context}" if context else ""}

請輸出 JSON 格式，包含以下欄位：

"fixed_code": 修正後的完整程式碼（字串）

"explanation": 簡短的修正說明（一句話）

只輸出 JSON，不要有其他文字。"""

    try:
        response = await llm_func(
            prompt=prompt,
            stream=False,
            temperature=0.2,
            num_predict=1500,
            agent_config=agent_config
        )
        # 處理字典類型的回應
        if isinstance(response, dict):
            response = response.get("content", "")
        if isinstance(response, str):
            response = response.strip()
        else:
            response = str(response).strip()
        start = response.find('{')
        end = response.rfind('}') + 1
        if start == -1 or end <= start:
            return None, "無法解析 LLM 回應：找不到 JSON"
        data = json.loads(response[start:end])
        
        if tool_name:
            # 工具參數修正場景：返回 fixed + args
            fixed = data.get("fixed", False)
            if not fixed:
                return None, data.get("error", "LLM 無法修正此錯誤")
            return data.get("args"), data.get("explanation", "已產生修正版本")
        else:
            # 代碼修正場景：返回 fixed_code
            fixed = data.get("fixed_code")
            explanation = data.get("explanation", "已產生修正版本")
            if not fixed:
                return None, "LLM 未提供修正程式碼"
            return fixed, explanation
    except Exception as e:
        logging.exception("generate_fix 失敗")
        return None, f"內部錯誤：{str(e)}"
