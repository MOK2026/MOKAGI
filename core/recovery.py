"""
202608110022_出街版
recovery.py - 統一處理意圖模糊、異常恢復等需要 LLM 介入的場景


===

# 調用時機與流程

1. LLM 調用失敗時的錯誤處理
文件：mokagi.py

函數：call_llm

觸發條件：當使用 OpenAI API（或 OpenRouter）調用 LLM 時發生異常（網絡錯誤、API 超時、模型不可用等）。

調用代碼：

python
except Exception as e:
    logging.exception("OpenAI 調用失敗")
    return await recovery.handle_llm_error(e)
作用：recovery.handle_llm_error 根據錯誤類型返回友好的錯誤提示，並可能觸發重試或降級策略。

2. 用戶回覆待澄清問題（主動提問後的二次理解）
文件：mokagi.py

函數：process_message

觸發條件：上一輪對話中 AI 主動提出了澄清問題（例如“請補充說明：…”），用戶在當前消息中做出了回答。

調用代碼：

python
pending = _pending_clarification.pop(user_id, None)
if pending and (time.time() - pending["timestamp"]) < 300:
    from recovery import merge_and_reunderstand
    result = await merge_and_reunderstand(user_id, pending["original"], pending["question"], text)
作用：merge_and_reunderstand 將用戶的新回答與原始模糊問題合併，重新理解意圖，返回 (cmd, args) 元組供後續處理。

3. 工具結果自然化失敗時的降級處理
文件：mokagi.py

函數：naturalize_tool_result

觸發條件：某工具定義了自然化函數，但調用時發生異常。

調用代碼：

python
except Exception as e:
    logging.warning(f"自然化函數調用失敗: {e}")
    return await recovery.naturalize_tool_result_fallback(user_text, tool_name, raw_result)
作用：recovery.naturalize_tool_result_fallback 返回簡單的 JSON 轉文本，確保用戶仍能看到工具結果。

4. 主動生成澄清問題（在普通聊天中提示用戶補充信息）
文件：mokagi.py

函數：process_message → 普通聊天分支（OpenAI 或 Ollama 模式）

觸發條件：當 LLM 返回的回覆過短、包含“抱歉”、“沒有理解”等關鍵詞時。

調用代碼：

python
if "沒有理解" in final_reply or "抱歉" in final_reply or len(final_reply.strip()) < 10:
    from recovery import ask_clarification
    clarification = await ask_clarification(text)
    final_reply = clarification
作用：ask_clarification 生成專業的引導問題列表，幫助用戶補充必要信息。

"""

import json
import logging
import httpx
from typing import Optional, Tuple, Dict
import tool_handler





























async def ask_clarification(user_text: str, agent_config: Optional[Dict] = None) -> str:
    """當無法識別意圖時，讓 LLM 分析{owner}輸入，指出模糊之處並生成提問"""

    import mokagi
    if agent_config is None:
        agent_config = mokagi._agent_config
    agent_name = agent_config.get("MOK_AGENT_NAME", "助手")
    owner = agent_config.get("MOK_ADMIN_NAME", "用户")
    owner_time = agent_config.get("MOK_ADMIN_TIME_ZONE", 0)



    prompt = f"""{owner}說：「{user_text}」
妳剛剛無法確定{owner}意圖。請：
1. 分析模糊之處（缺少關鍵詞、動作不明確、對象缺失等）。
2. 生成一個專業的列表清單，引導{owner}補充必要信息。
輸出格式：
請{owner}補充:\n\n
---
1
2
"""

    # 子Agent不需要系統環境信息
    #agent_body = mokagi.get_system_context(agent_name, owner, owner_time)

    default_question = f"抱歉{owner}，{agent_name}無法理解「{user_text[:50]}...」。請補充說明：{owner}是想讓{agent_name}記住某件事、搜索信息、管理工作流，還是執行其他操作？"
    try:
        agent_body = mokagi.get_system_context(agent_name, owner, owner_time)
        reply = await mokagi.call_llm(
            prompt,
            system_prompt=agent_body,
            stream=False,
            temperature=0.5,
            num_predict=2000,
            agent_config=agent_config
        )


        if reply.startswith("❌"):
            # 失敗時嘗試簡化 prompt
            simple_prompt = f"{owner}說：「{user_text}」。請用一句話直接問{owner}需要什麼幫助。"
            reply2 = await mokagi.call_llm(simple_prompt, stream=False, temperature=0.3, num_predict=50, agent_config=agent_config)
            if reply2.startswith("❌"):
                return default_question
            reply = reply2.strip()
        if not reply or len(reply) < 5:
            return default_question
        return reply.strip()
    except Exception as e:
        logging.warning(f"ask_clarification 異常: {e}")
        return default_question

























async def merge_and_reunderstand(user_id: str, original: str, question: str, answer: str, agent_config: Optional[Dict] = None) -> Optional[Tuple[str, str]]:

    """將原始輸入、AI提問、{owner}回答合併，讓LLM重新理解意圖，返回 (cmd, args) 或 None"""

    import mokagi
    if agent_config is None:
        agent_config = mokagi._agent_config
    agent_name = agent_config.get("MOK_AGENT_NAME", "助手")
    owner = agent_config.get("MOK_ADMIN_NAME", "用户")

    prompt = f"""對話歷史：
- {owner}原話：{original}
- 我({agent_name})提問：{question}
- {owner}回答：{answer}

根據上述對話，重新判斷{owner}意圖。你必須只輸出以下三種 JSON 之一，不要輸出任何其他文字：

1. 如果需要執行多步複雜任務（例如搜索、抓取、翻譯、保存等），輸出：
   {{"command": "/workflow create", "args": "重新生成的完整目標"}}
2. 如果是普通聊天，輸出：
   {{"command": "chat"}}
3. 如果仍然無法確定，輸出：
   {{"command": "none"}}

只輸出 JSON，不要有任何解釋。"""

    try:
        response = await mokagi.call_llm(prompt, stream=False, temperature=0.2, num_predict=2000, agent_config=agent_config)
        response = response.strip()
        start = response.find('{')
        end = response.rfind('}') + 1
        if start != -1 and end > start:
            data = json.loads(response[start:end])
            cmd = data.get("command")
            args = data.get("args", "")
            if cmd == "chat":
                return "chat", ""
            if cmd and cmd != "none" and cmd in tool_handler.get_cmd_map():
                return cmd, args
    except Exception as e:
        logging.warning(f"重新理解意圖失敗: {e}")
    return None





























# 通用錯誤處理函數
async def handle_llm_error(error: Exception, context: dict = None, agent_config: Optional[Dict] = None) -> str:
    """處理 LLM 調用中的錯誤，返回{owner}友好的消息"""
    import mokagi
    if agent_config is None:
        agent_config = mokagi._agent_config
    error_type = type(error).__name__
    owner = agent_config.get("MOK_ADMIN_NAME", "用户")


    if isinstance(error, httpx.TimeoutException):
        return "⏰ 模型響應超時，請稍後重試或檢查模型服務是否正常。"
    elif isinstance(error, httpx.HTTPStatusError):
        status = error.response.status_code
        if status == 404:
            return "❌ 模型端點不存在，請檢查配置文件中的 MOK_MODEL_url 是否正確。"
        else:
            return f"❌ HTTP 錯誤 {status}，請檢查網絡或模型服務。"
    else:
        # 讓 LLM 分析錯誤並給出建議
        prompt = f"妳在調用語言模型時遇到錯誤：{error_type}: {str(error)}。請生成一句簡短、專業的提示。告訴{owner}，並建議可能的原因（如網絡問題、配置錯誤等）。只輸出提示內容。"
        try:


            # ===== 可選：注入系統環境信息 =====
            #agent_body = mokagi.get_system_context(agent_name, owner, owner_time)
            reply = await mokagi.call_llm(
                prompt,
                #system_prompt=agent_body,
                stream=False,
                temperature=0.3,
                num_predict=100, agent_config=agent_config
            )

            return reply.strip() if reply else f"❌ 生成失敗：{error_type}"
        except:
            return f"❌ 系統錯誤：{error_type}，請聯繫管理員。"






























async def naturalize_tool_result_fallback(user_text: str, tool_name: str, raw_result: str, agent_config: Optional[Dict] = None) -> str:
    """當工具的自然化函數失敗時，讓 LLM 生成一個友好的摘要"""

    import mokagi
    if agent_config is None:
        agent_config = mokagi._agent_config
    owner = agent_config.get("MOK_ADMIN_NAME", "用户")


    prompt = f"""{owner}請求：{user_text}
工具 {tool_name} 返回了原始結果：
{raw_result[:800]}

請用一句簡短、專業的告訴{owner}這個結果的核心信息。不要提及“根據結果”，直接說結論。"""
    try:
        reply = await mokagi.call_llm(prompt, stream=False, temperature=0.3, num_predict=1000, agent_config=agent_config, include_soul=False)
        if reply and not reply.startswith("❌"):
            return reply.strip()
    except Exception:
        pass
    # 降級：返回截斷的原始結果
    if len(raw_result) > 500:
        raw_result = raw_result[:500] + "..."
    return f"工具返回：{raw_result}"