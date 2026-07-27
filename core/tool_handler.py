"""
202607271012_暫時可用版
tool_handler.py - 統一工具調用中間層（適配 mokagi 架構）
負責加載 tools/ 下的所有插件，提供意圖識別、命令執行、結果自然化等功能
"""

import os
import sys
import json
import logging
import importlib.util
from typing import Dict, Any, Optional, Tuple

# 設置 logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================== 動態路徑配置（跟隨 MOKAGI_HOME）==================
# 優先從環境變量獲取，若未設置則使用默認值 "MokAgi"（兼容舊版）
MOKAGI_HOME = os.environ.get("MOKAGI_HOME", "MokAgi")
TOOLS_DIR = os.path.expanduser(f"~/.{MOKAGI_HOME}/tools")

if not os.path.exists(TOOLS_DIR):
    os.makedirs(TOOLS_DIR, exist_ok=True)
    logger.warning(f"工具目錄不存在，已創建: {TOOLS_DIR}")

# 確保 tools 目錄在 sys.path 中，以便模塊間相互引用
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

# 設置環境變量（供工具模塊使用，模擬 agent 環境）
# AD_AgiName 使用 MOKAGI_HOME 的值
os.environ.setdefault("AD_AgiName", MOKAGI_HOME)
os.environ.setdefault("AD_AGENT_NAME", "default")
os.environ.setdefault("ADMIN_CHAT_ID", "")   # 網頁版暫不需要管理員校驗

# 全局變量，存儲加載的工具和命令映射
_tools = {}       # {模塊名: 模塊對象}
_cmd_map = {}     # {命令字符串: handler函數}

def load_tools() -> Tuple[Dict[str, Any], Dict[str, Any]]:
    """
    加載 tools/ 目錄下的所有插件，返回 (tools, cmd_map)
    """
    global _tools, _cmd_map
    _tools = {}
    _cmd_map = {}
    
    if not os.path.isdir(TOOLS_DIR):
        logger.warning(f"工具目錄不存在: {TOOLS_DIR}")
        return _tools, _cmd_map
    
    for filename in os.listdir(TOOLS_DIR):
        if not filename.endswith(".py") or filename == "__init__.py":
            continue
        module_name = filename[:-3]
        try:
            spec = importlib.util.spec_from_file_location(
                module_name, os.path.join(TOOLS_DIR, filename)
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            _tools[module_name] = module
            
            if hasattr(module, "PLUGIN_INFO"):
                info = module.PLUGIN_INFO
                cmd = info.get("command")
                handler_name = info.get("handler")
                if cmd and handler_name:
                    handler = getattr(module, handler_name, None)
                    if handler:
                        _cmd_map[cmd] = handler
                        #logger.info(f"加載工具: {module_name} -> {cmd}")
                    else:
                        logger.warning(f"工具 {module_name} 缺少 handler: {handler_name}")
        except Exception as e:
            logger.error(f"加載工具 {module_name} 失敗: {e}", exc_info=True)
    print(f"\n========= 已轉換AGENT =========\n")
    logger.info(f"共加載 {len(_tools)} 個工具, 命令: {list(_cmd_map.keys())}")
    return _tools, _cmd_map

def get_tools() -> Dict[str, Any]:
    """返回已加載的工具字典"""
    return _tools

def get_cmd_map() -> Dict[str, Any]:
    """返回命令映射字典"""
    return _cmd_map

def get_intent_module():
    """獲取 intent 模塊（如果已加載）"""
    return _tools.get("intent")



async def recognize_intent(
    user_text: str,
    ollama_api: str,
    model_name: str,
    cmd_map: Dict[str, Any] = None,
    tools: Dict[str, Any] = None
) -> Tuple[Optional[str], Optional[str]]:
    """
    意圖識別，返回 (完整命令, 參數字符串) 或 (None, None)
    """
    if cmd_map is None:
        cmd_map = _cmd_map
    if tools is None:
        tools = _tools
    intent_mod = get_intent_module()
    if not intent_mod:
        return None, None
    
    # 1. 規則匹配
    try:
        kw_map = intent_mod.build_keyword_map(cmd_map, tools)
        cmd, args = await intent_mod.rule_based_intent(user_text, kw_map)
        if cmd:
            return cmd, args
    except Exception as e:
        logger.error(f"規則匹配意圖失敗: {e}")
    
    # 2. LLM 意圖分類
    try:
        cmd, args = await intent_mod.llm_intent(
            user_text, cmd_map, tools, ollama_api, model_name
        )
        if cmd:
            return cmd, args
    except Exception as e:
        logger.error(f"LLM 意圖識別失敗: {e}")
    
    return None, None






async def execute_command(cmd: str, args: str, chat_id: str = "web", agent_config: Optional[Dict] = None) -> str:
    """
    執行命令，返回原始結果字符串
    """
    handler = _cmd_map.get(cmd)
    if not handler:
        return json.dumps({"success": False, "error": f"未知命令: {cmd}"}, ensure_ascii=False)
    try:
        # 調用 handler，如果 handler 接受 agent_config 參數則傳遞
        import inspect
        sig = inspect.signature(handler)
        if 'agent_config' in sig.parameters:
            result = await handler(args, chat_id, agent_config=agent_config)
        else:
            result = await handler(args, chat_id)
        # 如果 result 不是字符串，轉為字符串
        if not isinstance(result, str):
            result = json.dumps(result, ensure_ascii=False)
        return result
    except Exception as e:
        logger.exception(f"執行命令 {cmd} 失敗")
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

async def naturalize_result(
    user_text: str,
    cmd: str,
    raw_result: str,
    ollama_api: str,
    model_name: str,
    tools: Dict[str, Any] = None
) -> str:
    """
    對工具返回的原始結果進行自然化處理
    """
    if tools is None:
        tools = _tools
    
    # 查找對應工具模塊
    target_mod = None
    for mod in tools.values():
        if hasattr(mod, "PLUGIN_INFO") and mod.PLUGIN_INFO.get("command") == cmd:
            target_mod = mod
            break
    
    # 如果工具定義了 naturalize_func，則調用
    if target_mod and hasattr(target_mod, "PLUGIN_INFO"):
        func_name = target_mod.PLUGIN_INFO.get("naturalize_func")
        if func_name:
            naturalize_func = getattr(target_mod, func_name, None)
            if naturalize_func:
                try:
                    # 注意簽名: async def func(user_text, raw_result, ollama_api, model_name, temp_msg=None, context=None)
                    naturalized = await naturalize_func(
                        user_text=user_text,
                        raw_result=raw_result,
                        ollama_api=ollama_api,
                        model_name=model_name,
                        temp_msg=None,
                        context=None
                    )
                    if naturalized:
                        return naturalized
                except Exception as e:
                    logger.warning(f"自然化函數調用失敗: {e}")
    
    # 默認自然化：嘗試將 JSON 轉為易讀文本
    try:
        data = json.loads(raw_result)
        if isinstance(data, dict):
            if data.get("success") and "results" in data:
                lines = []
                for idx, item in enumerate(data["results"][:5], 1):
                    title = item.get("title", "無標題")
                    link = item.get("href", "#")
                    snippet = item.get("body", "")[:150]
                    lines.append(f"{idx}. {title}\n   {snippet}\n   {link}")
                if lines:
                    return "\n\n".join(lines)
            elif data.get("success") and "reply" in data:
                return data["reply"]
            elif "error" in data:
                return f"❌ 錯誤: {data['error']}"
    except:
        pass
    
    # 直接返回原始結果（截斷過長內容）
    # if len(raw_result) > 2000:
    #    raw_result = raw_result[:2000] + "\n...(內容過長，已截斷)"
    return raw_result

async def process_message(
    user_text: str,
    chat_id: str,
    ollama_api: str,
    model_name: str,
    cmd_map: Dict[str, Any] = None,
    tools: Dict[str, Any] = None,
    agent_config: Optional[Dict] = None
) -> Optional[str]:
    """
    高層函數：處理一條用戶消息。
    如果消息匹配直接命令或意圖識別出命令，則執行並返回自然化後的回覆字符串；
    如果沒有匹配任何命令，返回 None，以便上層回退到普通聊天流程。
    """
    if cmd_map is None:
        cmd_map = _cmd_map
    if tools is None:
        tools = _tools
    
    # 1. 直接命令（以 / 開頭）
    if user_text.startswith('/'):
        parts = user_text.split(maxsplit=1)
        cmd = parts[0]
        args = parts[1] if len(parts) > 1 else ""
        if cmd in cmd_map:
            raw_result = await execute_command(cmd, args, chat_id, agent_config=agent_config)
            naturalized = await naturalize_result(
                user_text, cmd, raw_result, ollama_api, model_name, tools
            )
            return naturalized
    
    # 2. 意圖識別
    cmd, args = await recognize_intent(user_text, ollama_api, model_name, cmd_map, tools)
    if cmd:
        # 拆分可能包含子命令的 cmd (如 "/admin htop")
        if ' ' in cmd.lstrip('/'):   # 根命令後帶空格
            parts = cmd.split(maxsplit=1)
            root_cmd = parts[0]
            sub_args = parts[1] if len(parts) > 1 else ""
            # 合併原有的 args（如果有）
            final_args = f"{sub_args} {args}".strip() if args else sub_args
        else:
            root_cmd = cmd
            final_args = args
        # 檢查根命令是否存在
        if root_cmd in cmd_map:
            raw_result = await execute_command(root_cmd, final_args, chat_id, agent_config=agent_config)
            naturalized = await naturalize_result(
                user_text, root_cmd, raw_result, ollama_api, model_name, tools
            )
            return naturalized
    
    # 未匹配任何命令
    return None









































# 公共時間提取函數
def extract_timelimit(text: str) -> Optional[str]:
    """從文本中提取時間範圍關鍵詞，返回 'd','w','m','y' 或 None"""
    if not text:
        return None
    text_lower = text.lower()
    if any(kw in text_lower for kw in ['今日', '今天', '日', '天', 'day', 'today', 'd']):
        return 'd'
    if any(kw in text_lower for kw in ['本週', '這周', '星期', '周', 'week', 'w']):
        return 'w'
    if any(kw in text_lower for kw in ['本月', '這個月', '月份', '月', 'month', 'm']):
        return 'm'
    if any(kw in text_lower for kw in ['本年', '今年', '年', 'year', 'y']):
        return 'y'
    return None