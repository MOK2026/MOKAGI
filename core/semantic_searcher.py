"""semantic_searcher.py - 語義搜索與上下文檢索"""
import logging
from typing import Dict, List, Optional, Callable, Awaitable

async def auto_semantic_search_context(
    user_id: str,
    query: str,
    stream_callback: Optional[Callable] = None,
    n_results: int = 3,
    agent_config: Optional[Dict] = None
) -> str:
    """
    自動執行語義搜索，返回格式化的上下文文本。
    使用 tools 中的 semantic_search_conversation、extract_keywords_from_sentence、_generate_associations。
    """
    from tools.memory import semantic_search_conversation
    from tools.associate import extract_keywords_from_sentence, _generate_associations

    ASSOC_COUNT = 5
    KEYWORD_LIMIT = 15

    async def _t(msg):
        if stream_callback:
            if isinstance(msg, dict):
                await stream_callback(msg)
            else:
                await stream_callback({"type": "think", "content": msg})

    # 第一次搜索
    direct_result = await semantic_search_conversation(
        user_id, query, n_results=n_results, keywords=None, agent_config=agent_config,
        stream_callback=_t
    )
    if "沒有找到" not in direct_result and "搜索出錯" not in direct_result:
        return f"\n【相關歷史對話（語義搜索）】\n{direct_result}\n\n"

    # 直接搜索無結果，嘗試聯想詞
    await _t("⚠️ 直接搜索未找到，嘗試聯想詞擴充...\n")
    core_keywords = await extract_keywords_from_sentence(query, agent_config) or [query]
    await _t(f"📌 核心關鍵詞：{', '.join(core_keywords)}\n")

    all_keywords = set()
    for kw in core_keywords:
        assoc_words = await _generate_associations(kw, count=ASSOC_COUNT, context="", agent_config=agent_config)
        all_keywords.update([kw] + assoc_words)
    final_keywords = list(all_keywords)[:KEYWORD_LIMIT]
    await _t(f"🔗 聯想詞總數：{len(final_keywords)} 個\n")

    result = await semantic_search_conversation(
        user_id, query, n_results=n_results, keywords=final_keywords,
        agent_config=agent_config, stream_callback=_t, assoc_count=0
    )
    if "沒有找到" in result or "搜索出錯" in result:
        return ""
    return f"\n【相關歷史對話（語義搜索）】\n{result}\n\n"
