#!/usr/bin/env python3
# tool_console.py – 獨立工具調用服務（無 LLM 入口）

import os, sys, json
from flask import Flask, request, jsonify, render_template
import tool_handler
from config import _agent_config

# 確保工具目錄在 Python 路徑
TOOLS_DIR = os.path.expanduser("~/.mok/tools")
if TOOLS_DIR not in sys.path:
    sys.path.insert(0, TOOLS_DIR)

# 加載所有工具（複用 mokagi 的工具加載邏輯）
tool_handler.load_tools()

app = Flask(__name__, template_folder='~/.mok/html')
app.config['SECRET_KEY'] = 'dev_key'

# 默認用戶 ID（可從環境變量或配置讀取）
DEFAULT_USER_ID = _agent_config.get("ADMIN_CHAT_ID") or os.environ.get("ADMIN_CHAT_ID", "web_default")

@app.route('/')
def index():
    """返回工具控制檯頁面"""
    return render_template('tool_console.html')

@app.route('/api/tools', methods=['GET'])
def list_tools():
    """返回所有可用工具及其元數據"""
    tools_info = []
    for name, mod in tool_handler.get_tools().items():
        if not hasattr(mod, 'PLUGIN_INFO'):
            continue
        info = mod.PLUGIN_INFO
        schema = info.get('tool_schema', {})
        sub_tools = info.get('sub_tools', [])
        # 主工具
        tools_info.append({
            'name': schema.get('name', name),
            'command': info.get('command', ''),
            'description': info.get('description', ''),
            'icon': info.get('icon', '🔧'),
            'parameters': schema.get('parameters', {}),
            'handler': info.get('handler', '')
        })
        # 子工具
        for sub in sub_tools:
            tools_info.append({
                'name': sub.get('name', ''),
                'command': '',  # 子工具通常沒有獨立命令
                'description': sub.get('description', ''),
                'icon': '🔹',
                'parameters': sub.get('parameters', {}),
                'handler': info.get('handler', '')  # 共用父 handler
            })
    return jsonify({'tools': tools_info})

@app.route('/api/execute', methods=['POST'])
def execute_tool():
    """執行指定工具，參數為 JSON"""
    data = request.get_json()
    tool_name = data.get('name')
    arguments = data.get('arguments', {})
    user_id = data.get('user_id', DEFAULT_USER_ID)
    if not tool_name:
        return jsonify({'error': 'Missing tool name'}), 400

    # 查找 handler
    handler = None
    for mod in tool_handler.get_tools().values():
        if not hasattr(mod, 'PLUGIN_INFO'):
            continue
        info = mod.PLUGIN_INFO
        # 檢查主工具
        if info.get('tool_schema', {}).get('name') == tool_name:
            handler_name = info.get('handler')
            if handler_name:
                handler = getattr(mod, handler_name, None)
            break
        # 檢查子工具
        for sub in info.get('sub_tools', []):
            if sub.get('name') == tool_name:
                handler_name = info.get('handler')
                if handler_name:
                    handler = getattr(mod, handler_name, None)
                break
        if handler:
            break

    if not handler:
        return jsonify({'error': f'Tool "{tool_name}" not found'}), 404

    try:
        # 調用工具
        result = handler(arguments, user_id, agent_config=_agent_config)
        # 如果結果是協程，需要同步等待（這裡假設都是同步函數，但可能有些是異步，簡化處理）
        if hasattr(result, '__await__'):
            import asyncio
            result = asyncio.run(result)
        return jsonify({'success': True, 'result': str(result)})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

if __name__ == '__main__':
    app.run(host='127.0.0.1', port=5001, debug=False)