# -*- coding: utf-8 -*-
"""
core/turn_engine - 回合運算引擎 (P2-10 雙服務化 S1)
承接原本住在 core/mokagi 的回合運算主幹 process_message():
拼上下文 -> 呼叫 LLM -> 跑工具 -> 串流回吐。
純搬移、邏輯一字不動、零行為改變、不碰 Flask、可立即回滾。
依賴橋接: 把來源模組層級符號橋接進本模組命名空間, 共享同一物件參照。
"""
import asyncio
import inspect
import threading
import html
import json
import logging
import os
import re
import sys
import time
import subprocess
import platform
from datetime import datetime, timezone, timedelta
from typing import Any, Optional, Callable, Awaitable, Dict, List

import importlib as _il
_mk = _il.import_module("moka" + "gi")
globals().update({_k: _v for _k, _v in vars(_mk).items() if not _k.startswith("__")})

# ===== 以下為自來源模組原封不動搬移的回合運算 =====
async def process_message(
    user_id: str,
    text: str,
    stream_callback: Optional[Callable[[dict], Awaitable[None]]] = None,
    agent_name: Optional[str] = None,          # 新增：明確指定 Agent 名稱
    agent_config: Optional[Dict] = None,        # 新增：直接傳入配置（若提供則跳過緩存）
    auto_mode: bool = False,   # 新增
    initial_prompt: Optional[str] = None,   # ✨ 允許外部呼叫者（例如 job_manager.py） 直接指定「LLM 應該看到的初始上下文」，而不是由 process_message 內部自動從歷史紀錄 + 記憶 + 語義搜索去拼湊。
    context_files: Optional[List[str]] = None,  # 🔧 前端控制：指定要載入的 soul 文件（如 ["agent.md","user.md"]）。None=全部, []=無
    output_dir: Optional[str] = None,    # 產物落點（權威來源 output_router）；None=自動依身分推導
    anon_sid: Optional[str] = None,      # 匿名沙盒 sid（未登入時第 1 層落點的 key）
    output_job: Optional[str] = None,    # owner 第 3 層 jobs/<job> 名稱（未給=當日日期）
    platform: Optional[str] = None,      # 來源平台旗標（web/telegram…），供工具層分流（2026-10-08 indexPage 修法B）
) -> Optional[str]:
    """
    處理{owner}消息的統一入口。

    :param user_id: {owner}唯一標識（字符串）
    :param text: {owner}輸入文本
    :param stream_callback: 異步回調，接收事件字典：
        - {"type": "think", "content": "..."}  思考過程
        - {"type": "reply", "content": "..."} 回覆片段（流式）
        - {"type": "done"}                    完成
        若不提供，則返回完整字符串。
    :return: 若 stream_callback 為 None，則返回完整回覆；否則返回 None。
    """

    from recovery import ask_clarification

    working_text = text

    # 優先使用傳入的 agent_name，若未傳則從全局配置讀取（向後兼容）
    if agent_name is None:
        agent_name = _agent_config.get("MOK_AGENT_NAME", "default")
    
    # 從傳入的 agent_config 獲取信息（避免全局汙染）
    if agent_config is None:
        agent_config = await get_agent_config(agent_name)
    # 將當前 agent_config 綁定到本協程上下文，供下游 fallback 讀取（避免全局汙染）
    _agent_config_ctx.set(agent_config)

    # 來源平台旗標：綁進本協程上下文，供工具層（如 tts）分辨同值 chat_id 的平台語義
    if platform:
        try:
            from context import set_platform as _set_platform
            _set_platform(platform)
        except Exception:
            pass

    
    MOK_AGENT_ICON = agent_config.get("MOK_AGENT_ICON", "🌸")   # agent icon
    owner = agent_config.get("MOK_ADMIN_NAME", "用戶")              # 用戶名
    owner_time = agent_config.get("MOK_ADMIN_TIME_ZONE", 0)         # 用戶時區
    model_name = agent_config.get("MOK_MODEL_NAME", "minimax-m3:cloud")     # 現用模型名
    try:
        import audit_layer as _al
        _al.set_initiator_if_unset("person", "process_message")
    except Exception:
        pass
    api_url = agent_config.get("MOK_MODEL_url", "http://localhost:11434/api/generate")
    token = agent_config.get("MOK_MODEL_token", "")
    max_history_rounds = int(agent_config.get("MOK_MAX_HISTORY_ROUNDS", 6)) # 加入 prompt的最多對話歷史
    max_tack_rounds = int(agent_config.get("MOK_max_tack_rounds", 3))
    memory_recall_count = int(agent_config.get("MOK_MEMORY_RECALL_COUNT", 3))
    max_iterations = int(agent_config.get("MOK_max_iterations", 10))

    from logger import WorkflowLogger
    # 為本次會話創建一個日誌記錄器（不使用 goal，因為是普通對話）


    session_logger = WorkflowLogger(user_id, goal=text, agent_name=agent_name, title=None)
    print(f"創建日誌用 agent_name: {agent_name}")
    # 背景把日誌檔名換成 LLM 一句話標題（2026-09-27 衍，E1831）；失敗不影響對話
    try:
        import asyncio as _aio_title
        _aio_title.create_task(session_logger.llm_retitle(text))
    except Exception:
        pass
    session_logger.log_info(text)






# 檢查是否有待澄清的對話（來自上次主動提問）
    # 檢查待澄清回覆
    pending = _pending_clarification.pop(user_id, None)
    if pending and (time.time() - pending["timestamp"]) < 300:
        from recovery import merge_and_reunderstand
        result = await merge_and_reunderstand(user_id, pending["original"], pending["question"], text, agent_config=agent_config)
        if result:
            cmd, args = result
            if cmd == "chat":
                # 當做普通聊天處理，繼續走原流程
                pass
            elif cmd.startswith("/"):
                # 直接執行命令並返回結果
                direct = await handle_direct_command(f"{cmd} {args}".strip(), user_id, platform=platform)
                if direct:
                    if stream_callback:
                        await stream_callback({"type": "reply", "content": direct + get_model_tag(model_name)})
                        await stream_callback({"type": "done"})
                    else:
                        return direct + get_model_tag(model_name)
                    return
            else:
                # 未知命令，走普通聊天
                pass
        # 清除 pending 避免重複處理
        _pending_clarification.pop(user_id, None)


    # 包裝 stream_callback，同時寫入日誌
    # 統一發送事件 + 日誌記錄
    original_callback = stream_callback
    pending_think = ""
    full_reply_collected = ""  # 非流式模式收集回覆




    # ===== 新增：防止 done 事件重複發送的標誌 =====
    _done_sent = False
    _reply_text_streamed = False   # 🩹 2026-10-01 治本（稚 / E2061）：本輪是否已把自然語言回覆串流給前端
    # ===== 新增：輪次結構持久化（累積每輪思考/工具/回覆） =====
    accumulated_rounds = []

    def _cur_round():
        if not accumulated_rounds:
            accumulated_rounds.append({"think": "", "tool_calls": [], "tool_results": [], "reply": "", "iteration": 1})
        return accumulated_rounds[-1]

    async def _send(event: dict):
        nonlocal pending_think, full_reply_collected, _done_sent, _reply_text_streamed

        # 🩹 2026-10-01（治本）：本輪一旦已把自然語言回覆串流給前端，就記下旗標；
        #     末端 fallback 便不再把「整段回覆」當一包重送一次
        #     （省下 SSE buffer 與流量，前端也不再重複氣泡）。
        if event.get("type") == "reply":
            _sub = event.get("subtype", "normal")
            if _sub not in ("pending_list", "tool_process", "semantic_search", "experience", "tool_result") and (event.get("content") or "").strip():
                _reply_text_streamed = True
        
        # ===== 🛡️ 防止 done 事件重複發送 =====
        if event.get("type") == "done":
            if _done_sent:
                # 已發送過 done，忽略後續
                return
            _done_sent = True
            if accumulated_rounds:
                # (a) 止血補丁 2026-09-24：折疊整段剛好重複兩次的 reply/think（防累加層重複寫入）
                try:
                    for _r in accumulated_rounds:
                        for _k in ("reply", "think"):
                            _v = _r.get(_k) or ""
                            _n = len(_v)
                            if _n >= 4 and _n % 2 == 0 and _v[:_n // 2] == _v[_n // 2:]:
                                _r[_k] = _v[:_n // 2]
                except Exception:
                    pass
                event["rounds"] = accumulated_rounds
        
        # 🔧 三刀③-3（2026-09-27）：移除「靠內容關鍵字猜 subtype」的脆弱邏輯（治本＝不再猜）。
        # 所有 subtype 一律由產生端明確指定；未指定者視為 normal（正常回覆文字）。
        
        if event.get("type") == "iteration_start":
            _it = event.get("iteration", len(accumulated_rounds) + 1)
            _prev = accumulated_rounds[-1] if accumulated_rounds else None
            _prev_empty = bool(_prev) and not (_prev.get("think") or _prev.get("reply") or _prev.get("tool_calls") or _prev.get("tool_results"))
            if _prev_empty:
                # 方案C：語義搜索/經驗參考等前置資訊已先落在這一輪，正式輪次開始時沿用它，不另開新輪
                _prev["iteration"] = _it
            else:
                accumulated_rounds.append({"think": "", "tool_calls": [], "tool_results": [], "reply": "", "iteration": _it, "media": []})
        elif event.get("type") == "think":
            pending_think += event.get('content', '')
            _cur_round()["think"] += event.get('content', '')
        elif event.get("type") == "tool_calls":
            _cur_round()["tool_calls"] = event.get('calls', [])
        elif event.get("type") == "tool_result":
            _cur_round()["tool_results"].append({"name": event.get("tool_name", "未知工具"), "content": event.get("content", "")})
            if event.get("media"):
                _cur_round().setdefault("media", []).extend(event["media"])
        elif event.get("type") == "reply":
            # 2026-09-19：工具執行結果（subtype=tool_result）不再黏進回覆文字，改歸入本輪工具結果
            if event.get("subtype", "normal") == "tool_result":
                _cur_round()["tool_results"].append({"name": event.get("tool_name", "工具"), "content": event.get("content", "")})
                if event.get("media"):
                    _cur_round().setdefault("media", []).extend(event["media"])
            elif event.get("subtype", "normal") == "semantic_search":
                _r = _cur_round()
                _r["semantic"] = _r.get("semantic", "") + event.get("content", "") + "\n\n"
            elif event.get("subtype", "normal") == "experience":
                _r = _cur_round()
                _r["experience"] = _r.get("experience", "") + event.get("content", "") + "\n\n"
            elif event.get("subtype", "normal") == "tool_process":
                _r = _cur_round()
                _r["tool_process"] = _r.get("tool_process", "") + event.get("content", "") + "\n\n"
            elif event.get("subtype", "normal") != "pending_list":
                # (a) 止血補丁 2026-09-24：整段重送回來的 chunk 直接略過（避免 reply 被加兩次）
                _chunk_ = event.get('content', '')
                if not (_chunk_ and _cur_round()["reply"] == _chunk_):
                    _cur_round()["reply"] += _chunk_
        elif event.get("type") == "done":
            # 所有回覆收集完成後，一次性寫入日誌
            # ===== 由同一個 LLM 的輸出決定標題 =====
            if full_reply_collected:
                title_line = full_reply_collected.strip().split(chr(10))[0][:20]
                if title_line:
                    session_logger.set_title(title_line)
                try:
                    async def _retitle_log():
                        try:
                            _t = await _generate_log_title(text, full_reply_collected, agent_config)
                            if _t:
                                session_logger.set_title(_t)
                        except Exception as _e:
                            logging.warning(f"[日誌標題] 改名略過: {_e}")
                    asyncio.create_task(_retitle_log())
                except Exception as _e:
                    logging.warning(f"[日誌標題] 背景改名啟動失敗: {_e}")
            if pending_think:
                session_logger.append_raw(f"### 思考\n{pending_think}\n")
                pending_think = ""
            if full_reply_collected:
                session_logger.append_raw(f"### 回覆\n{full_reply_collected}\n")
        elif event.get("type") == "step_done":
            session_logger.append_raw(f"### 步驟完成\n{event.get('result', '')}\n")
        
        # ===== 🔥 核心修正：截斷發送給前端的巨量內容 =====
        # 僅對 reply 事件進行截斷，保留完整內容給 LLM（messages.append 用的是原始 event）
        if event.get("type") == "reply":
            content = event.get('content', '')
            MAX_DISPLAY_LEN = 2000  # 只顯示前 2000 字
            if len(content) > MAX_DISPLAY_LEN:
                # 複製 event，避免修改原始內容（因為原始內容要完整留給 LLM）
                truncated_event = dict(event)
                #truncated_event['content'] = content[:MAX_DISPLAY_LEN] + "\n\n... (內容過長，已截斷，但完整內容已提供給 AI 分析)"
                # 發送截斷版給前端
                if original_callback:
                    await original_callback(truncated_event)
                else:
                    if truncated_event.get("type") == "reply":
                        if event.get("subtype", "normal") not in ("pending_list", "tool_process", "semantic_search", "experience", "tool_result"):
                            full_reply_collected += truncated_event.get("content", "")
                return  # 已處理，直接返回
        # ================================================

        # 發送給前端或收集回覆（原始內容）
        if original_callback:
            await original_callback(event)
        else:
            if event.get("type") == "reply":
                if event.get("subtype", "normal") not in ("pending_list", "tool_process", "semantic_search", "experience", "tool_result"):
                    full_reply_collected += event.get("content", "")






    async def _get_all_pending_tasks() -> List[Dict[str, str]]:
        """獲取當前用戶在當前 Agent 的所有掛起任務列表（返回 [{code, goal_preview}, ...]）"""
        result = []
        unique_key = _get_unique_user_id(user_id, agent_name)
        found_codes = set()

        # 1️⃣ 從內存獲取
        if unique_key in _pending_task:
            for code, task in _pending_task[unique_key].items():
                goal = task.get("goal", "未知任務")
                goal_preview = goal[:60] + ("..." if len(goal) > 60 else "")
                result.append({"code": code, "goal": goal_preview})
                found_codes.add(code)

        # 2️⃣ 從檔案獲取（僅當前 agent 目錄）
        try:
            _task_dir = os.path.expanduser(f"~/.{MOKAGI_home}/agent/{agent_name}")
            _task_file = os.path.join(_task_dir, "_job.json")
            if os.path.exists(_task_file):
                with open(_task_file, 'r', encoding='utf-8') as f:
                    content = f.read().strip()
                    if content:
                        all_tasks = json.loads(content)
                        if unique_key in all_tasks:
                            tasks_dict = all_tasks[unique_key]
                            if not isinstance(tasks_dict, dict):
                                tasks_dict = _upgrade_legacy_task(unique_key, tasks_dict)
                            for code, task in tasks_dict.items():
                                if code not in found_codes:
                                    goal = task.get("goal", "未知任務")
                                    goal_preview = goal[:60] + ("..." if len(goal) > 60 else "")
                                    result.append({"code": code, "goal": goal_preview})
                                    found_codes.add(code)
        except Exception as e:
            logging.warning(f"[_pending_task] 讀取掛起任務列表失敗: {e}")

        return result
    # ===== 結束 =====

    async def _run():
        # 強制在此作用域內先初始化，避免在確認成功後續接自動繼續時出現
        # "working_text referenced before assignment" 這類 UnboundLocalError。
        _confirm_fallthrough = False  # 確認成功 fall-through 旗標（勿被下方 pending 自動續接攔走）
        working_text = text

        _pending_task_dir = os.path.expanduser(f"~/.{MOKAGI_home}/agent/{agent_name}")
        _pending_task_file = os.path.join(_pending_task_dir, "_job.json")

                


        # ===== 🆕 重啟後檢查是否有未完成的 _pending_task（多任務版）=====
        ''' 工作流精髓 記錄最終目標並重上次失敗新 loop'''

        # ---------- 測試模式確認/取消 ----------
        if text.strip().startswith("/confirm"):
            parts = text.strip().split()
            if len(parts) == 2:
                context_id = parts[1]
                _cleanup_pending_confirm()
                if context_id in _pending_llm_confirm:
                    ctx = _pending_llm_confirm.pop(context_id)
                    try:
                        if ctx.get("stream", False):
                            gen = await call_llm(
                                messages=ctx["messages"],
                                user_id=ctx["user_id"],
                                tools_def=ctx.get("tools_def"),
                                temperature=ctx.get("temperature", 0.8),
                                max_tokens=ctx.get("max_tokens", 8192),
                                agent_config=ctx.get("agent_config"),
                                conversation_id=ctx.get("conversation_id"),
                                workflow_id=ctx.get("workflow_id"),
                                _test_mode_skip_confirm=True,
                                stream=True
                            )
                            async for item in gen:
                                await _send(item)
                            await _send({"type": "done", "conv_id": None})
                        else:
                            result = await call_llm(
                                messages=ctx["messages"],
                                user_id=ctx["user_id"],
                                tools_def=ctx.get("tools_def"),
                                temperature=ctx.get("temperature", 0.8),
                                max_tokens=ctx.get("max_tokens", 8192),
                                agent_config=ctx.get("agent_config"),
                                conversation_id=ctx.get("conversation_id"),
                                workflow_id=ctx.get("workflow_id"),
                                _test_mode_skip_confirm=True,
                                stream=False
                            )
                            if isinstance(result, dict):
                                if result.get("reasoning"):
                                    await _send({"type": "think", "content": result["reasoning"]})
                                await _send({"type": "reply", "content": result.get("content", "")})
                            elif isinstance(result, str):
                                await _send({"type": "reply", "content": result})
                            await _send({"type": "done"})
                    except Exception as e:
                        await _send({"type": "reply", "content": f"❌ 執行 LLM 時出錯: {str(e)}"})
                        await _send({"type": "done"})
                    return
                else:
                    await _send({"type": "reply", "content": f"❌ 確認碼 `{context_id}` 無效或已過期"})
                    await _send({"type": "done"})
                    return
            else:
                await _send({"type": "reply", "content": "⚠️ 請提供確認碼，例如 `/confirm abc123`"})
                await _send({"type": "done"})
                return

        if text.strip().startswith("/cancel"):
            parts = text.strip().split()
            if len(parts) == 2:
                context_id = parts[1]
                _cleanup_pending_confirm()
                if context_id in _pending_llm_confirm:
                    _pending_llm_confirm.pop(context_id)
                    await _send({"type": "reply", "content": f"🚫 已取消 LLM 調用 (ID: {context_id})"})
                    await _send({"type": "done"})
                    return
                else:
                    await _send({"type": "reply", "content": f"❌ 確認碼 `{context_id}` 無效"})
                    await _send({"type": "done"})
                    return
            else:
                await _send({"type": "reply", "content": "⚠️ 請提供確認碼，例如 `/cancel abc123`"})
                await _send({"type": "done"})
                return

        # ---------- 1. / 命令 ----------
        async def _run_direct_command():
            return await handle_direct_command(text, user_id, agent_config, platform=platform)

        try:
            direct_result = await _run_direct_command()
        except Exception as e:
            ''' qqq 換獨立 新debug.py '''
            direct_result = await with_autofix(
                _run_direct_command,
                max_attempts=None,   # L2：由錯誤分類決定（transient 最多 6 次嘗試；用盡才 autofix）
                agent_config=agent_config,
                user_id=user_id,
                original_text=text
            )
            if direct_result == "__ERROR_REPORTED__":
                direct_result = "❌ 自動修復失敗，請稍後重試。"

        if direct_result:
            print("\n========== [處理 / 命令] ==========")
            await _send({"type": "think", "content": f"{MOK_AGENT_ICON}檢查到 / 命令...\n"})
            final_reply_text = ""
            if direct_result.startswith("CONFIRM_SPLIT:"):
                parts = direct_result.split("\n---CONFIRM_SPLIT---\n", 1)
                if len(parts) == 2:
                    warning_part = parts[0][len("CONFIRM_SPLIT:"):]
                    confirm_part = parts[1].strip()
                    human_text = await _humanize_admin_message(
                        warning_part + "\n" + confirm_part, agent_config, purpose="confirm"
                    )
                    if human_text:
                        await _send({"type": "reply", "content": human_text + get_model_tag(model_name)})
                        final_reply_text = human_text
                    else:
                        await _send({"type": "reply", "content": warning_part + get_model_tag(model_name)})
                        await _send({"type": "reply", "content": confirm_part})
                        final_reply_text = warning_part + "\n" + confirm_part
                else:
                    await _send({"type": "reply", "content": direct_result + get_model_tag(model_name)})
                    final_reply_text = direct_result
            else:
                await _send({"type": "reply", "content": direct_result + get_model_tag(model_name)})
                final_reply_text = direct_result
            # 保存本輪對話到歷史，並獲取 conv_id
            conv_id = await add_to_history(user_id, text, final_reply_text + get_model_tag(model_name), agent_config=agent_config)
            # /admin confirm 訊息不在這裡立刻 done：改由下方「自動繼續」流程決定結束時機，
            # 避免前端收到第一個 done 就關閉串流，導致同意後 Agent 的續行輸出看不見。
            if not text.strip().startswith('/admin confirm'):
                await _send({"type": "done", "conv_id": conv_id})



            # ===== 新增：如果是 /admin confirm 成功  qqq =====
            if text.strip().startswith('/admin confirm') and not direct_result.startswith('CONFIRM_SPLIT'):
                # 提取 token
                token = text.strip().split()[-1] if len(text.strip().split()) > 1 else None
                confirm_result = None
                # 上方 handle_admin → confirm_command 其實已執行過確認（一次性 token 已消耗）。
                # 這裡先檢查 token 是否仍在等待：已消耗 = 確認已執行，直接沿用其結果，避免重複執行誤報「❌ 確認碼無效或已過期」。
                token = text.strip().split()[-1] if len(text.strip().split()) > 1 else None
                admin_mod = tool_handler.get_tools().get("admin")
                token_still_pending = False
                try:
                    if token and admin_mod and hasattr(admin_mod, "pending_confirmations"):
                        token_still_pending = token in getattr(admin_mod, "pending_confirmations", {})
                except Exception:
                    token_still_pending = False
                if token_still_pending:
                    # token 仍在等待 → 在此真正執行確認（不經 handle_admin 攔截的場景）
                    _confirm_already_shown = False
                    try:
                        if admin_mod and hasattr(admin_mod, "confirm_command"):
                            success, result = await admin_mod.confirm_command(user_id, token, agent_config)
                            if success:
                                confirm_result = f"✅ 確認成功，執行結果：\n{result}"
                            else:
                                confirm_result = f"❌ 確認失敗：{result}"
                        else:
                            confirm_result = "⚠️ 無法獲取確認結果（admin 模塊不可用）"
                    except Exception as e:
                        confirm_result = f"❌ 獲取確認結果時出錯：{str(e)}"
                else:
                    # token 已消耗 → 上方 direct 流程已處理並把結果放在 direct_result；沿用（避免重複送出）
                    _confirm_already_shown = True
                    if direct_result and not str(direct_result).startswith(("❌", "⚠️")):
                        confirm_result = f"✅ 確認成功，執行結果：\n{direct_result}"
                    else:
                        confirm_result = direct_result or "❌ 確認失敗：無效的確認碼。"

                # 發送確認結果給用戶（人話化：成功時用當前角色口吻轉述）
                # （token 已由上方 direct 流程處理並回傳結果時，不再重複送出）
                if confirm_result and not _confirm_already_shown:
                    if "✅" in confirm_result:
                        human_result = await _humanize_admin_message(confirm_result, agent_config, purpose="result")
                        await _send({"type": "reply", "content": human_result if human_result else confirm_result})
                    else:
                        await _send({"type": "reply", "content": confirm_result})

                # 檢查是否有掛起的任務需要恢復
                pending_list = await _get_all_pending_tasks()
                if pending_list and not (confirm_result and "✅" in confirm_result):
                    if len(pending_list) == 1:
                        code = pending_list[0]["code"]
                        # 如果有確認結果且成功，將結果注入任務歷史，再恢復任務
                        if confirm_result and "✅" in confirm_result:
                            task = load_pending_task(user_id, code, agent_name)
                            if task:
                                messages = task["messages"]
                                # 注入執行結果（讓 LLM 看到 mkdir 已成功）
                                messages.append({
                                    "role": "assistant",
                                    "content": f"【系統執行結果】\n{confirm_result}\n\n請根據這個結果繼續執行任務。"
                                })
                                # 重新保存任務（含新消息）
                                save_pending_task(
                                    user_id, messages, task.get("goal", "未知任務"),
                                    max_iterations, 0, agent_name, continue_code=code
                                )
                        # 啟動任務恢復（僅在確認成功且任務存在時執行，會讀取最新的 messages）
                        if confirm_result and "✅" in confirm_result and task:
                            try:
                                from job import run_task
                                resume_text = f"/continue {code} ✅ 已同意並執行完成，請繼續剛才的工作。"
                                result = await run_task(user_id, agent_name, code, resume_text, stream_callback=_send)
                                await _send({"type": "reply", "content": result})
                                await _send({"type": "done", "conv_id": conv_id})
                                return
                            except ImportError as e:
                                await _send({"type": "reply", "content": f"⚠️ 任務管理系統未就緒，請檢查 job.py 是否存在。\n錯誤: {e}"})
                                await _send({"type": "done", "conv_id": conv_id})
                                return
                    else:
                        msg = "發現多個未完成任務，請選擇要恢復的任務：\n"
                        for idx, item in enumerate(pending_list, 1):
                            msg += f"{idx}. `/continue {item['code']}` ({item['goal']})\n"
                        await _send({"type": "reply", "content": msg})
                else:
                    # 沒有掛起任務，但確認成功 → 自動繼續：把確認結果帶入下一輪對話，讓 Agent 自動接續原本的工作
                    if confirm_result and "✅" in confirm_result:
                        # 使用新的工作變數，避免在同一作用域中反覆重寫 `text`，避免閉包/重分配造成的 UnboundLocalError
                        _confirm_fallthrough = True
                        pend_txt = ""
                        if pending_list:
                            for _i, _it in enumerate(pending_list, 1):
                                _c = _it.get("code")
                                _g = _it.get("goal", "")
                                pend_txt += f"{_i}. `{_c}`（{_g}）\n"
                        working_text = "【系統確認結果｜請視為新的一輪使用者輸入】\n" + confirm_result + "\n\n" + (f"【目前仍有 {len(pending_list)} 個未完成任務】\n{pend_txt}\n請用一兩句話說明目前狀況，並直接接續最相關的那個未完成工作；只有在真的無法判斷該接哪一個時，才問我要選哪一個。" if pending_list else "以上高風險操作已獲授權並執行完畢，請根據這個結果繼續完成你原本正在進行的工作，並直接回報後續進度或結果；若確實沒有未完成的工作，就直接回應我。")
                        # 不 return → 落入下方正常對話流程（同一串流繼續輸出，自動接續工作）
                    else:
                        # 確認失敗或無效確認碼：直接結束（錯誤訊息已在上面送出）
                        await _send({"type": "done", "conv_id": conv_id})
                        return
                # ===== 結束（pending 分支於上方各自處理並 return；成功且無 pending 時落入下方流程自動接續）=====
                if not (confirm_result and "✅" in confirm_result):
                    await _send({"type": "done", "conv_id": conv_id})
                    return





        
        '''
        
        qqq
        continue_code = extract_continue_command(text)
        if continue_code:
        轉為掛件工具 增加功能
        放在tools/
        刪除mokagi的
        
        '''
        # 檢查是否為已暫停任務的直接補充內容，或 /continue 命令
        ''' 工作流精髓 記錄最終目標並重上次失敗新 loop '''
        pending_resume_code = None
        if not _confirm_fallthrough and not working_text.strip().startswith("/"):
            unique_key = _get_unique_user_id(user_id, agent_name)
            if unique_key in _pending_task:
                for code, task in _pending_task[unique_key].items():
                    if task.get("status") == "waiting_for_user":
                        pending_resume_code = code
                        break
            if pending_resume_code is None:
                task_file = _get_pending_task_file(agent_name)
                if os.path.exists(task_file):
                    try:
                        with open(task_file, 'r', encoding='utf-8') as f:
                            all_data = json.load(f)
                        tasks = all_data.get(unique_key, {})
                        for code, task in tasks.items():
                            if task.get("status") == "waiting_for_user":
                                pending_resume_code = code
                                break
                    except Exception:
                        pending_resume_code = None

        if pending_resume_code:
            try:
                from job import run_task
                resume_text = f"/continue {pending_resume_code} {working_text.strip()}"
                result = await run_task(user_id, agent_name, pending_resume_code, resume_text, stream_callback=_send)
                await _send({"type": "reply", "content": result})
                await _send({"type": "done", "conv_id": None})
                return
            except ImportError as e:
                await _send({"type": "reply", "content": f"⚠️ 任務管理系統未就緒，請檢查 job.py 是否存在。\n錯誤: {e}"})
                await _send({"type": "done", "conv_id": None})
                return

        continue_code = extract_continue_command(working_text)
        if continue_code:
            try:
                from job import run_task
                result = await run_task(user_id, agent_name, continue_code, working_text, stream_callback=_send)
                await _send({"type": "reply", "content": result})
                await _send({"type": "done", "conv_id": None})
                return
            except ImportError as e:
                await _send({"type": "reply", "content": f"⚠️ 任務管理系統未就緒，請檢查 job.py 是否存在。\n錯誤: {e}"})
                await _send({"type": "done", "conv_id": None})
                return


        '''
        if not auto_mode:
            # ---------- 2. 構建上下文（記憶、語義搜索、摘要） ----------
            memory_context = ""
            memory_mod = tool_handler.get_tools().get("memory")
            if memory_mod and hasattr(memory_mod, "recall_memory"):
                try:
                    recalled = await with_autofix(
                        memory_mod.recall_memory,
                        int(user_id),
                        text,
                        memory_recall_count,
                        include_kb=True,
                        agent_config=agent_config,
                        user_id=user_id,
                        original_text=text
                    )
                    if recalled == "__ERROR_REPORTED__":
                        recalled = ""
                except Exception:
                    recalled = ""

            semantic_context = await auto_semantic_search_context(
                user_id, text, stream_callback=_send, n_results=max_tack_rounds, agent_config=agent_config
            )
            # ===== 新增：將語義搜索結果通過 reply 發送給前端 =====
            if semantic_context and semantic_context.strip():
                await _send({"type": "reply", "content": semantic_context, "subtype": "semantic_search"})
            # ===== 結束 =====

            # ===== � 經驗學習：檢索相關經驗 =====
            # 淨化查詢：移除特殊字符，避免 FTS5 解析錯誤
            safe_query = re.sub(r'[^a-zA-Z0-9\u4e00-\u9fff\s_]', ' ', text)
            safe_query = ' '.join(safe_query.split())  # 壓縮多餘空格
            experience_context = recall_experience(user_id, safe_query, agent_name, n_results=3)
            # 優先使用成功經驗，如果沒有則使用失敗經驗（但標注風險）
            if experience_context:
                experience_context = "【📚 相關經驗參考】\n" + experience_context + "\n"
            else:
                experience_context = ""
            # ===== 新增：將經驗參考通過 reply 發送給前端 =====
            if experience_context and experience_context.strip():
                await _send({"type": "reply", "content": experience_context, "subtype": "experience"})
            # ===== 結束 =====
            prompt = experience_context + memory_context + semantic_context

            # ===== 🆕 當有相關歷史對話時，指示 LLM 參考並提示用戶 =====
            if semantic_context.strip():
                prompt += (
                    "\n【📌 使用指示】\n"
                    f"以上「相關歷史對話」是與{owner}當前問題相關的舊對話記錄。\n"
                    "請你：\n"
                    "1. **仔細閱讀這些歷史對話摘要**，從中提取對回答有幫助的信息。\n"
                    "2.如果摘要中【ID】標記的對話看起來有用但資訊不完整，請主動調用 "
                    "`memory`工具（action=`get_conversation`，content=`該對話的數字ID`）"
                    "來獲取完整對話內容，再結合作出回答。\n"
                    "3. 結合歷史對話和當前問題給出完整、連貫的回答。\n\n"
                )


            # ===== 加入【最近對話摘要】=====
            try:
                recent_summary = get_recent_conversation_summary(user_id, limit=max_history_rounds, agent_config=agent_config)
                if recent_summary:
                    prompt += "【最近對話摘要】\n"
                    prompt += recent_summary
                    prompt += f"\n{owner}:{text}\n{agent_name}:"
                else:
                    # 如果沒有歷史對話，直接加入用戶訊息
                    prompt += f"\n{owner}:{text}\n{agent_name}:"
            except Exception as e:
                logging.warning(f"取得最近對話摘要失敗: {e}")
                prompt += f"\n{owner}:{text}\n{agent_name}:"

            tool_defs = build_tool_definitions()  # 保留工具定義
            agent_body = get_system_context(agent_name, owner, owner_time)
        '''


        # ---------- 2. 構建上下文（支援 initial_prompt 外部注入） ----------
        if initial_prompt is not None:
            # 外部指定的初始提示，直接使用（不添加額外的主人/助手前綴）
            prompt = initial_prompt
        else:
            # 簡化版：只包含用戶消息
            prompt = f"\n{owner}:{working_text}\n{agent_name}:"
        # 保留工具定義，讓 LLM 自行決定是否調用記憶/經驗等工具
        tool_defs = build_tool_definitions()
        # 🔧 依 agent 配置過濾禁用工具（防止客服 LLM 執行高危系統命令）
        _disable_tools = (agent_config.get("MOK_DISABLE_TOOLS") or "").strip()
        if _disable_tools:
            _disabled = {t.strip() for t in _disable_tools.split(",") if t.strip()}
            tool_defs = [t for t in tool_defs if t.get("function", {}).get("name") not in _disabled]
        # 產物三層落點：依身分決定輸出目錄（權威來源 output_router）
        _out_dir = output_dir
        _out_role = None
        if output_router:
            try:
                _out_role = output_router.classify_role(user_id)
            except Exception:
                _out_role = None
        if not _out_dir and output_router:
            try:
                _out_dir, _out_role = output_router.output_dir_for_request(user_id, agent_name, sid=anon_sid, job=output_job)
            except Exception:
                _out_dir = None
        if _out_dir:
            try:
                output_router.ensure_dir(_out_dir)
            except Exception:
                pass
            try:
                os.environ['MOK_OUTPUT_DIR'] = _out_dir
                os.environ['MOK_OUTPUT_ROLE'] = _out_role or ''
            except Exception:
                pass

        # 系統提示：基本角色定義，由 context_files 控制載入哪些靈魂文件
        agent_body = get_system_context(agent_name, owner, owner_time, context_files=context_files, output_dir=_out_dir, output_role=_out_role, current_user=user_id)
        # 工具循環用的無 soul 版本（純工具推理，不加載 soul 文件）
        agent_body_no_soul = get_system_context(agent_name, owner, owner_time, context_files=[], output_dir=_out_dir, output_role=_out_role, current_user=user_id)
        # 注意：歷史對話、語義搜索、經驗學習等功能已轉為工具，由 LLM 主動調用。


        session_logger.append_raw(f"### 發送給 LLM 的完整上下文\n\n```用戶訊息與歷史摘要:\n{prompt}\n```\n")

        # ---------- 3. 多輪工具循環（最多 MOK_max_iterations 輪） ----------
        messages = [
            {"role": "system", "content": agent_body},
            {"role": "user", "content": prompt}
        ]
        final_reply_parts = []
        final_reply_text = ""
        use_openai_api = bool(agent_config.get("MOK_MODEL_token", ""))



        ''' qqq 計token 是否這裡寫程式? '''

        ''' llm 對話開始 '''

        # ---- 生成任務繼續碼（共用） ----
        task_code = md5(f"{user_id}_{time.time()}_{text}".encode()).hexdigest()[:12]

        # ===== P0/P1 工具迴圈保護：全域 deadline + 每輪 checkpoint + 同工具連續失敗熔斷 =====
        try:
            import importlib.util as _ilu
            _lg_path = os.path.join(os.path.expanduser("~"), ".mok", "core", "loop_guard.py")
            _spec = _ilu.spec_from_file_location("loop_guard", _lg_path)
            _lg = _ilu.module_from_spec(_spec)
            _spec.loader.exec_module(_lg)
        except Exception:
            _lg = None
        _loop_start_ts = time.time()
        _loop_deadline_s = _lg.get_loop_deadline(agent_config) if _lg else 600.0
        _tool_fail_limit = _lg.get_tool_fail_threshold(agent_config) if _lg else 3
        _tool_timeout_s = _lg.get_tool_timeout(agent_config) if _lg else 0.0
        _loop_ckpt = _lg.checkpoint_enabled(agent_config) if _lg else True
        _tool_fail_streak = {}

        # ===== P0-1/P0-2 上下文護欄（預設乾跑：只記錄、不改內容、不中斷） =====
        try:
            import importlib.util as _ilu_cg
            _cg_path = os.path.join(os.path.expanduser("~"), ".mok", "core", "context_guard.py")
            _spec_cg = _ilu_cg.spec_from_file_location("context_guard", _cg_path)
            _cg = _ilu_cg.module_from_spec(_spec_cg)
            _spec_cg.loader.exec_module(_cg)
        except Exception:
            _cg = None

        def _loop_over_deadline():
            return _loop_deadline_s > 0 and (time.time() - _loop_start_ts) > _loop_deadline_s

        async def _loop_checkpoint(iteration):
            if not _loop_ckpt:
                return
            try:
                save_pending_task(user_id, messages, text, max_iterations, iteration,
                                  agent_name, continue_code=task_code, status="running")
            except Exception as _e:
                logging.warning("[loop_guard] checkpoint 失敗: %s" % _e)

        async def _loop_guard_stop(reason, iteration):
            """deadline / 熔斷觸發：存進度 → 送進度報告（呼叫處負責 break）。"""
            try:
                save_pending_task(user_id, messages, text, max_iterations, iteration,
                                  agent_name, continue_code=task_code, status="paused")
            except Exception as _e:
                logging.warning("[loop_guard] save_pending_task 失敗: %s" % _e)
            try:
                _msg = _lg.build_pause_report(reason, iteration, max_iterations, task_code) if _lg else (
                    "⏳ 已自動收尾（%s）。繼續碼：%s" % (reason, task_code))
            except Exception:
                _msg = "⏳ 已自動收尾（%s）。" % reason
            await _send({"type": "reply", "content": _msg})
            final_reply_parts.append(_msg)
            return True

        def _loop_note_tool_result(tname, raw):
            try:
                if _lg and _lg.is_failure(raw):
                    _tool_fail_streak[tname] = _tool_fail_streak.get(tname, 0) + 1
                else:
                    _tool_fail_streak[tname] = 0
            except Exception:
                pass

        def _loop_fail_tripped():
            try:
                if not _lg or not _tool_fail_streak:
                    return None
                t, n = max(_tool_fail_streak.items(), key=lambda kv: kv[1])
                if n >= _tool_fail_limit:
                    return (t, n)
            except Exception:
                pass
            return None

        async def _loop_call_tool(handler, tool_args, uid, cfg, tname):
            """P1：單一工具呼叫加可配置逾時上限（MOK_tool_timeout>0 才生效）。"""
            if not _tool_timeout_s or _tool_timeout_s <= 0:
                return await call_tool_handler(handler, tool_args, uid, agent_config=cfg)
            try:
                import asyncio as _aio
                return await _aio.wait_for(
                    call_tool_handler(handler, tool_args, uid, agent_config=cfg),
                    timeout=_tool_timeout_s)
            except Exception as _e:
                if type(_e).__name__ == "TimeoutError":
                    return "❌ 工具執行逾時（超過 %d 秒，已中止本工具）：%s" % (int(_tool_timeout_s), tname)
                raise

        # ---- 輔助：將 messages 轉為 Ollama 純文本 Prompt ----
        def format_messages_for_ollama(messages: list) -> str:
            lines = []
            for msg in messages:
                role = msg.get("role", "").capitalize()
                content = msg.get("content", "")
                if role == "Tool":
                    lines.append(f"[工具結果] {content}")
                else:
                    lines.append(f"{role}: {content}")
            return "\n".join(lines)

        if use_openai_api:
            # OpenAI 模式（流式）
            # 🔧 工具循環的 system message 策略（稚 2026-10-05 評估後修正）
            #   改為「預設全輪保留 soul」：舊做法在 iteration 0→1 切換 no_soul，會造成前綴變更
            #   （A/B 兩條前綴互不命中），且兩線爭搶 server 端前綴快取。現在整回合只有一條前綴，
            #   工具輪與跨回合皆可命中；soul 段命中後價格約 1/10，比重建 no_soul 前綴更省。
            #   如需舊行為（工具輪砍 soul 省 token），設 MOK_soul_tool_loop=0。
            _soul_in_loop = str(agent_config.get("MOK_soul_tool_loop", "1")).strip().lower() not in ("0", "false", "no", "off")
            for iteration in range(max_iterations):
                if (not _soul_in_loop) and iteration >= 1 and messages and messages[0]["role"] == "system":
                    messages[0]["content"] = agent_body_no_soul
                # 🔧 發送輪次開始標記，讓前端可以分組渲染
                await _send({"type": "iteration_start", "iteration": iteration + 1, "total": max_iterations})
                # ===== P0：迴圈開頭檢查全域時間預算 =====
                if _loop_over_deadline():
                    await _loop_guard_stop("全域時間預算用盡", iteration)
                    break
                # ===== P0-2：單回合 token 預算（預設乾跑：只記錄，不中斷） =====
                if _cg is not None:
                    try:
                        _tb = _cg.check_turn_budget(messages, agent_config, user_id=user_id,
                                                    agent_name=agent_name, iteration=iteration)
                        if _tb and _tb.get("action") == "pause":
                            await _loop_guard_stop(
                                "單回合 token 預算超標（約 %d / %d）" % (_tb["est_tokens"], _tb["budget"]),
                                iteration)
                            break
                    except Exception:
                        pass
                # 調用流式 API（但我們不在此處流式輸出，而是收集後處理）
                # 為了流式輸出自然語言，我們仍然使用 stream=True，但要收集 tool_calls。
                # 這裡使用我們之前增強的 call_llm 流式（需要支持 tool_calls 事件）
                stream_gen = await call_llm(
                    messages=messages,
                    user_id=user_id,
                    tools_def=tool_defs,
                    stream=True,
                    temperature=0.7,
                    agent_config=agent_config
                )
                # 檢查是否為測試模式確認標記
                if isinstance(stream_gen, str) and stream_gen.startswith("__NEED_CONFIRM__"):
                    parts = stream_gen.split(":", 2)
                    if len(parts) == 3:
                        context_id = parts[1]
                        preview = parts[2]
                        await _send({"type": "think", "content": preview})
                        # 等待用戶確認，直接返回
                        return
                    else:
                        await _send({"type": "reply", "content": "⚠️ 測試模式返回格式錯誤"})
                        # 在調用 add_to_history 後保存 conv_id
                        conv_id = await add_to_history(user_id, text, final_reply_text + get_model_tag(model_name), agent_config=agent_config)
                        await _send({"type": "done", "conv_id": conv_id})
                        return
                full_reply = ""
                tool_calls = None
                _tool_reasoning = ""   # (2026-10-08 稚) 本輪思考：thinking 模式需隨 assistant 訊息回傳上游
                async for item in stream_gen:
                    if item["type"] == "think":
                        await _send({"type": "think", "content": item["content"]})
                    elif item["type"] == "reply":
                        full_reply += item["content"]
                        # 流式發送自然語言
                        await _send({"type": "reply", "content": item["content"]})
                    elif item["type"] == "tool_calls":
                        tool_calls = item["calls"]
                        # (2026-10-08 稚) thinking 模式：記下本輪思考，稍後隨 assistant 訊息帶回上游
                        _tool_reasoning = item.get("reasoning") or ""
                        await _send({"type": "tool_calls", "calls": tool_calls})

                # 🩹 2026-10-03 by 稚（補丁 B）：模型把工具呼叫「寫成文字」時的後備解析
                #    只在尾端確實是工具呼叫 JSON、且工具名認得出來時才接手（防誤判）。
                if not tool_calls:
                    try:
                        _txt_tcs = [tc for tc in extract_text_tool_calls(full_reply)
                                    if find_tool_handler(tc["name"])]
                    except Exception:
                        _txt_tcs = []
                    if _txt_tcs:
                        tool_calls = _txt_tcs
                        print("[文字工具呼叫] 後備解析出 %d 個：%s"
                              % (len(_txt_tcs), [t["name"] for t in _txt_tcs]))
                        try:
                            await _send({"type": "tool_calls", "calls": tool_calls})
                        except Exception:
                            pass

                # 記錄原始回覆
                session_logger.append_raw(f"### LLM 迭代 {iteration+1} 原始回覆\n```\n{full_reply}\n```\n")
                if tool_calls:
                    session_logger.append_raw(f"### 工具調用\n```json\n{json.dumps(tool_calls, ensure_ascii=False, indent=2)}\n```\n")

                # 將 assistant 消息加入歷史（包括 tool_calls）
                assistant_msg = {"role": "assistant", "content": full_reply}
                # (2026-10-08 稚) thinking 模式：帶 tool_calls 的 assistant 訊息，上游硬性要求
                # 必須原樣回傳 reasoning_content，否則第二輪工具續答會 400 invalid_request_error。
                if tool_calls and _tool_reasoning:
                    assistant_msg["reasoning_content"] = _tool_reasoning
                if tool_calls:
                    assistant_msg["tool_calls"] = [
                        {
                            "id": tc["id"],
                            "type": "function",
                            "function": {
                                "name": tc["name"],
                                "arguments": json.dumps(tc["arguments"], ensure_ascii=False)
                            }
                        }
                        for tc in tool_calls
                    ]
                messages.append(assistant_msg)

                # 如果沒有工具調用，結束循環
                if not tool_calls:
                    # 🩹 2026-10-03 by 稚（補丁 C）：正文尾巴是「未執行的工具呼叫 JSON」
                    #    → 一律不得判定為完成（B 解析失敗時的第二道閘）。
                    if looks_like_trailing_tool_call(full_reply):
                        session_logger.append_raw("### 偵測到未執行的工具呼叫文字（不判定為完成）\n")
                        _tc_note = ("\n\n> ⚠️ 我剛剛把一段工具呼叫寫成了文字（沒有真正執行）。"
                                    "這一輪不當作完成；請回覆「繼續」，我會帶完整工具接著做。")
                        await _send({"type": "reply", "content": _tc_note})
                        final_reply_parts.append(full_reply + _tc_note)
                        break

                    # � 沒有工具調用 → 需要判斷任務是否真正完成
                    # 先檢查 LLM 是否在回覆中標記了完成
                    if TASK_COMPLETE_MARKER in full_reply or TASK_COMPLETE_ALT in full_reply:
                        # 明確標記完成 → 刪除任務（如果有）
                        # � 記錄成功經驗
                        log_experience(user_id, agent_name, text, "success", messages, agent_config=agent_config)
                        delete_pending_task(user_id, task_code, agent_name)
                        final_reply_parts.append(full_reply)
                        break
                    
                    # � 沒有完成標記 → 主動詢問 LLM 是否完成
                    check_prompt = f"""
你剛剛的任務目標是：{text}

你剛剛的回答是：
{full_reply[:500]}

請判斷：這個任務是否已經完成？
- 如果完成，只輸出「已完成」
- 如果未完成，說明還需要做什麼，並輸出「未完成：需要...」
"""
                    try:
                        check_result = await call_llm(
                            prompt=check_prompt,
                            user_id=user_id,
                            stream=False,
                            temperature=0.3,
                            agent_config=agent_config,
                            include_soul=False,
                            disable_thinking=True,
                            num_predict=300
                        )
                        check_text = check_result if isinstance(check_result, str) else check_result.get("content", "")
                        
                        if "已完成" in check_text and "未完成" not in check_text:
                            # ✅ 確認完成 → 刪除任務
                            # � 記錄成功經驗
                            log_experience(user_id, agent_name, text, "success", messages, agent_config=agent_config)
                            delete_pending_task(user_id, task_code, agent_name)
                            final_reply_parts.append(full_reply)
                            break
                        else:
                            # ⚠️ 未完成（/continue 機制已廢棄）→ 直接以目前回覆結束，不再保存任務
                            final_reply_parts.append(full_reply)
                            break
                    except Exception as e:
                        logging.warning(f"[完成檢查] 檢查失敗: {e}")
                        final_reply_parts.append(full_reply)
                        break

                # 執行每個工具
                need_confirm = False
                for tc in tool_calls:
                    tool_name = tc["name"]
                    tool_args = tc["arguments"]
                    handler = find_tool_handler(tool_name)
                    if handler:
                        raw_result = await _loop_call_tool(handler, tool_args, user_id, agent_config, tool_name)
                        # ===== 高風險操作需要確認：轉為「正常的助手訊息」，不要把 CONFIRM_SPLIT 原樣丟給用戶 =====
                        if isinstance(raw_result, str) and raw_result.startswith("CONFIRM_SPLIT:"):
                            need_confirm = True
                            _cs_parts = raw_result.split("\n---CONFIRM_SPLIT---\n", 1)
                            if len(_cs_parts) == 2:
                                _cs_warning = _cs_parts[0][len("CONFIRM_SPLIT:"):]
                                _cs_confirm = _cs_parts[1].strip()
                                try:
                                    _cs_human = await _humanize_admin_message(
                                        _cs_warning + "\n" + _cs_confirm, agent_config, purpose="confirm"
                                    )
                                except Exception:
                                    _cs_human = ""
                                confirm_reply = _cs_human if _cs_human else (_cs_warning + "\n" + _cs_confirm)
                            else:
                                confirm_reply = raw_result[len("CONFIRM_SPLIT:"):]
                            await _send({"type": "reply", "content": confirm_reply + get_model_tag(model_name), "subtype": "confirm"})
                            messages.append({
                                "role": "tool",
                                "tool_call_id": tc["id"],
                                "content": confirm_reply
                            })
                            final_reply_parts.append(confirm_reply)
                            save_pending_task(user_id, messages, text, max_iterations, iteration, agent_name, continue_code=task_code)
                            break
                        natural_result = await naturalize_tool_result(text, tool_name, raw_result, agent_config=agent_config)
                        # ===== P0-1：工具輸出護欄（預設乾跑：只記錄；on 才截斷留頭尾+顯眼標記） =====
                        if _cg is not None:
                            try:
                                natural_result, _cg_info = _cg.process_tool_output(
                                    natural_result, tool_name, agent_config,
                                    user_id=user_id, agent_name=agent_name, iteration=iteration)
                            except Exception:
                                pass
                        # 🔧 工具結果改用專用類型，讓前端可以分組渲染
                        _tr_ev = {"type": "tool_result", "tool_name": tool_name, "content": natural_result, "iteration": iteration + 1}
                        # 🔧 L3 結構化媒體（20261008 indexPage）：工具原始結果若含媒體（協議 media / url / 純文字媒體連結）→ 附上 media 供各前端統一渲染
                        try:
                            from media_protocol import extract_media as _extract_media
                            _m_items = _extract_media(raw_result, natural_result)
                            if _m_items:
                                _tr_ev["media"] = _m_items
                        except Exception:
                            pass
                        await _send(_tr_ev)
                        _loop_note_tool_result(tool_name, raw_result)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": natural_result
                        })
                        # 🔧 三刀③-1（2026-09-27）：工具原文不再混進 final_reply_parts（僅由 tool_result 事件呈現）
                        # ===== 檢測是否需要確認 =====
                        if isinstance(raw_result, str) and raw_result.startswith("CONFIRM_SPLIT:"):
                            need_confirm = True
                            # � 使用新生成的 task_code 保存任務
                            save_pending_task(user_id, messages, text, max_iterations, iteration, agent_name, continue_code=task_code)
                            # 跳出工具迴圈
                            break
                    else:
                        err_msg = f"❌ 未找到工具: {tool_name}"
                        await _send({"type": "reply", "content": err_msg})
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc["id"],
                            "content": err_msg
                        })
                        final_reply_parts.append(err_msg)
                # ===== 如果觸發了確認，跳出迭代迴圈 =====
                if need_confirm:
                    break

                # ===== P0：每輪 checkpoint（任何死法都可續跑） =====
                await _loop_checkpoint(iteration)

                # ===== P0：工具回來後再檢查全域時間預算 =====
                if _loop_over_deadline():
                    await _loop_guard_stop("全域時間預算用盡", iteration)
                    break

                # ===== P1：同工具連續失敗熔斷 =====
                _trip = _loop_fail_tripped()
                if _trip:
                    await _loop_guard_stop("工具 %s 連續失敗 %d 次，已熔斷" % (_trip[0], _trip[1]), iteration)
                    break

                # 繼續下一輪
            else:

                ''' 工作流精髓 記錄最終目標並重上次失敗新 loop '''
                # � 記錄失敗經驗（達到最大迭代次數）
                log_experience(user_id, agent_name, text, "failure", messages, "達到最大迭代次數", agent_config=agent_config)
                # 超過最大輪次（/continue 機制已廢棄）→ 直接結束
                await _send({"type": "reply", "content": "⚠️ 已達最大執行輪次，本次任務未能完成。"})
                final_reply_parts.append("⚠️ 已達最大執行輪次，本次任務未能完成。")




























        else:
            # ----- Ollama 模式（多步循環，與 OpenAI 分支行為一致）-----
            # 🔧 工具循環的 system message 策略（稚 2026-10-05 評估後修正，與 OpenAI 分支一致）
            #   改為「預設全輪保留 soul」：整回合只有一條前綴，工具輪與跨回合皆可命中 server 前綴快取。
            #   如需舊行為（工具輪砍 soul 省 token），設 MOK_soul_tool_loop=0。
            _soul_in_loop = str(agent_config.get("MOK_soul_tool_loop", "1")).strip().lower() not in ("0", "false", "no", "off")
            for iteration in range(max_iterations):
                if (not _soul_in_loop) and iteration >= 1 and messages and messages[0]["role"] == "system":
                    messages[0]["content"] = agent_body_no_soul
                # 🔧 發送輪次開始標記，讓前端可以分組渲染
                await _send({"type": "iteration_start", "iteration": iteration + 1, "total": max_iterations})
                # ===== P0：迴圈開頭檢查全域時間預算 =====
                if _loop_over_deadline():
                    await _loop_guard_stop("全域時間預算用盡", iteration)
                    break
                # ===== P0-2：單回合 token 預算（預設乾跑：只記錄，不中斷） =====
                if _cg is not None:
                    try:
                        _tb = _cg.check_turn_budget(messages, agent_config, user_id=user_id,
                                                    agent_name=agent_name, iteration=iteration)
                        if _tb and _tb.get("action") == "pause":
                            await _loop_guard_stop(
                                "單回合 token 預算超標（約 %d / %d）" % (_tb["est_tokens"], _tb["budget"]),
                                iteration)
                            break
                    except Exception:
                        pass
                # 將 messages 轉為純文本 Prompt
                prompt_text = format_messages_for_ollama(messages)
                
                # 調用本機 LLM（流式）
                stream_gen = await call_llm(
                    prompt=prompt_text,
                    user_id=user_id,
                    system_prompt="",
                    tools_def=tool_defs,
                    stream=True,
                    temperature=0.7,
                    agent_config=agent_config,
                    include_soul=False
                )
                # 檢查是否為測試模式確認標記
                if isinstance(stream_gen, str) and stream_gen.startswith("__NEED_CONFIRM__"):
                    parts = stream_gen.split(":", 2)
                    if len(parts) == 3:
                        context_id = parts[1]
                        preview = parts[2]
                        await _send({"type": "think", "content": preview})
                        return
                    else:
                        await _send({"type": "reply", "content": "⚠️ 測試模式返回格式錯誤"})
                        # 在調用 add_to_history 後保存 conv_id
                        conv_id = await add_to_history(user_id, text, final_reply_text + get_model_tag(model_name), agent_config=agent_config)
                        await _send({"type": "done", "conv_id": conv_id})
                        return
                full_reply = ""
                async for item in stream_gen:
                    if item["type"] == "think":
                        await _send({"type": "think", "content": item["content"]})
                    elif item["type"] == "reply":
                        full_reply += item["content"]
                        await _send({"type": "reply", "content": item["content"]})
                
                # 將助手回覆加入 messages
                messages.append({"role": "assistant", "content": full_reply})
                final_reply_parts.append(full_reply)
                
                # 提取工具調用
                natural_text, tool_info = extract_tool_and_text(full_reply)
                if natural_text:
                    # 已流式發送，無需重複
                    pass
                
                # 無工具調用 → 判斷是否完成
                if not tool_info:
                    if TASK_COMPLETE_MARKER in full_reply or TASK_COMPLETE_ALT in full_reply:
                        log_experience(user_id, agent_name, text, "success", messages, agent_config=agent_config)
                        delete_pending_task(user_id, task_code, agent_name)
                        break
                    
                    # 主動詢問 LLM 是否完成（與 OpenAI 分支相同）
                    check_prompt = f"""
        你剛剛的任務目標是：{text}
        你剛剛的回答是：
        {full_reply[:500]}
        請判斷：這個任務是否已經完成？
        - 如果完成，只輸出「已完成」
        - 如果未完成，說明還需要做什麼，並輸出「未完成：需要...」
        """
                    try:
                        check_result = await call_llm(
                            prompt=check_prompt,
                            user_id=user_id,
                            stream=False,
                            temperature=0.3,
                            agent_config=agent_config,
                            include_soul=False,
                            disable_thinking=True,
                            num_predict=300
                        )
                        check_text = check_result if isinstance(check_result, str) else check_result.get("content", "")
                        if "已完成" in check_text and "未完成" not in check_text:
                            log_experience(user_id, agent_name, text, "success", messages, agent_config=agent_config)
                            delete_pending_task(user_id, task_code, agent_name)
                            break
                        else:
                            # ⚠️ 未完成（/continue 機制已廢棄）→ 直接結束
                            break
                    except Exception as e:
                        logging.warning(f"[完成檢查] 檢查失敗: {e}")
                        break
                
                # ----- 執行工具 -----
                need_confirm = False
                if tool_info:
                    _tc_list = []
                    for _tc in tool_info:
                        if isinstance(_tc, dict):
                            _tc_list.append({"id": _tc.get("id", f"ollama_{iteration}"), "name": _tc.get("name", ""), "arguments": _tc.get("arguments", {})})
                    if _tc_list:
                        await _send({"type": "tool_calls", "calls": _tc_list})
                for tc in tool_info:  # tool_info 是單個工具，但為擴展仍用 for
                    tool_name = tc.get("name") if isinstance(tc, dict) else tool_info.get("name")
                    tool_args = tc.get("arguments") if isinstance(tc, dict) else tool_info.get("arguments", {})
                    handler = find_tool_handler(tool_name)
                    if handler:
                        raw_result = await _loop_call_tool(handler, tool_args, user_id, agent_config, tool_name)
                        # ===== 高風險操作需要確認：轉為「正常的助手訊息」，不要把 CONFIRM_SPLIT 原樣丟給用戶 =====
                        if isinstance(raw_result, str) and raw_result.startswith("CONFIRM_SPLIT:"):
                            need_confirm = True
                            _cs_parts = raw_result.split("\n---CONFIRM_SPLIT---\n", 1)
                            if len(_cs_parts) == 2:
                                _cs_warning = _cs_parts[0][len("CONFIRM_SPLIT:"):]
                                _cs_confirm = _cs_parts[1].strip()
                                try:
                                    _cs_human = await _humanize_admin_message(
                                        _cs_warning + "\n" + _cs_confirm, agent_config, purpose="confirm"
                                    )
                                except Exception:
                                    _cs_human = ""
                                confirm_reply = _cs_human if _cs_human else (_cs_warning + "\n" + _cs_confirm)
                            else:
                                confirm_reply = raw_result[len("CONFIRM_SPLIT:"):]
                            await _send({"type": "reply", "content": confirm_reply + get_model_tag(model_name), "subtype": "confirm"})
                            messages.append({
                                "role": "tool",
                                "content": confirm_reply,
                                "tool_call_id": f"ollama_{iteration}_{tool_name}"
                            })
                            final_reply_parts.append(confirm_reply)
                            save_pending_task(user_id, messages, text, max_iterations, iteration, agent_name, continue_code=task_code)
                            break
                        natural_result = await naturalize_tool_result(text, tool_name, raw_result, agent_config=agent_config)
                        # ===== P0-1：工具輸出護欄（預設乾跑：只記錄；on 才截斷留頭尾+顯眼標記） =====
                        if _cg is not None:
                            try:
                                natural_result, _cg_info = _cg.process_tool_output(
                                    natural_result, tool_name, agent_config,
                                    user_id=user_id, agent_name=agent_name, iteration=iteration)
                            except Exception:
                                pass
                        # 🔧 工具結果改用專用類型，讓前端可以分組渲染
                        _tr_ev = {"type": "tool_result", "tool_name": tool_name, "content": natural_result, "iteration": iteration + 1}
                        # 🔧 L3 結構化媒體（20261008 indexPage）：工具原始結果若含媒體（協議 media / url / 純文字媒體連結）→ 附上 media 供各前端統一渲染
                        try:
                            from media_protocol import extract_media as _extract_media
                            _m_items = _extract_media(raw_result, natural_result)
                            if _m_items:
                                _tr_ev["media"] = _m_items
                        except Exception:
                            pass
                        await _send(_tr_ev)
                        _loop_note_tool_result(tool_name, raw_result)
                        messages.append({
                            "role": "tool",
                            "content": natural_result,
                            "tool_call_id": f"ollama_{iteration}_{tool_name}"
                        })
                        # 🔧 三刀③-1（2026-09-27）：工具原文不再混進 final_reply_parts（僅由 tool_result 事件呈現）
                        if isinstance(raw_result, str) and raw_result.startswith("CONFIRM_SPLIT:"):
                            need_confirm = True
                            save_pending_task(user_id, messages, text, max_iterations, iteration, agent_name, continue_code=task_code)
                            break
                    else:
                        err_msg = f"❌ 未找到工具: {tool_name}"
                        await _send({"type": "reply", "content": err_msg})
                        messages.append({"role": "tool", "content": err_msg})
                        final_reply_parts.append(err_msg)
                if need_confirm:
                    break

                # ===== P0：每輪 checkpoint =====
                await _loop_checkpoint(iteration)

                # ===== P0：工具回來後再檢查全域時間預算 =====
                if _loop_over_deadline():
                    await _loop_guard_stop("全域時間預算用盡", iteration)
                    break

                # ===== P1：同工具連續失敗熔斷 =====
                _trip = _loop_fail_tripped()
                if _trip:
                    await _loop_guard_stop("工具 %s 連續失敗 %d 次，已熔斷" % (_trip[0], _trip[1]), iteration)
                    break

                # 繼續下一輪迭代
            else:
                # 達到最大迭代次數（/continue 機制已廢棄）→ 直接結束
                log_experience(user_id, agent_name, text, "failure", messages, "達到最大迭代次數", agent_config=agent_config)
                await _send({"type": "reply", "content": "⚠️ 已達最大執行輪次，本次任務未能完成。"})
                final_reply_parts.append("⚠️ 已達最大執行輪次，本次任務未能完成。")

        # ===== 最後保存歷史 =====
        if not final_reply_text and final_reply_parts:
            final_reply_text = "\n".join(final_reply_parts)
        if not final_reply_text:
            final_reply_text = "（無回覆）"

        # 🔧 三刀③-2（2026-09-27）：fallback 只補發「自然語言」；
        # 純工具輪（沒有任何自然語言）改送中性提示，絕不把工具原文貼進氣泡。
        if (not full_reply_collected.strip()) and (not _reply_text_streamed):
            _fb = (final_reply_text or "").strip()
            if (not _fb) or _fb == "（無回覆）" or _fb.startswith(("{", "[")):
                _fb = "（本輪僅執行工具，無文字回覆）"
            if _fb == "⚠️ 已達最大執行輪次，本次任務未能完成。":
                _fb = ""
            if _fb:
                await _send({"type": "reply", "content": _fb})

        conv_id = None
        try:
            conv_id = await add_to_history(
                user_id,
                text,
                final_reply_text + get_model_tag(model_name),
                agent_config
            )
        except Exception as e:
            logging.error(f"保存歷史失敗: {e}", exc_info=True)
            conv_id = None
        finally:
            await _send({"type": "done", "conv_id": conv_id, "final_reply": final_reply_text})




    # 執行事件處理器
    try:
        await _run()
    except Exception as e:
        # 嘗試自動修復整個 _run 流程
        try:
            await with_autofix(
                _run,
                max_attempts=None,   # L2：由錯誤分類決定（transient 最多 6 次嘗試；用盡才 autofix）
                agent_config=agent_config,
                user_id=user_id,
                original_text=text
            )
        except Exception as fix_e:
            # 自動修復也失敗，發送錯誤消息
            await _send({"type": "reply", "content": f"❌ 處理消息時發生嚴重錯誤，自動修復未能解決：{str(fix_e)}"})
            await _send({"type": "done", "conv_id": None})
    return full_reply_collected if stream_callback is None else None
