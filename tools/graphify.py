# ------------------------------------------------------------------------------------ #
# 工具名稱: graphify (知識圖譜構建與查詢)
# 用途: 使用 Graphify (graphifyy) 將任意目錄的程式碼/文件映射為知識圖譜，支援查詢、路徑追蹤。
#
# 主要函數:
#   handle_graphify(args, chat_id, agent_config)
#       - 入口函數，處理 /graphify 命令或 LLM 工具調用。
#       - 支援 action: build, query, path, explain
#
# 設計原則:
#   - 作為通用工具，directory 參數可指定任意目錄
#   - build: 對指定目錄建圖 → 輸出 graphify-out/graph.json + graph.html + GRAPH_REPORT.md
#   - query/path/explain: 對已建好的 graph.json 進行查詢
#
# 依賴: graphifyy (pip install graphifyy)
#
# 更新記錄:
#   20260730 - 初版，支援 build/query/path/explain
# ------------------------------------------------------------------------------------ #

import os
import sys
import json
import logging
import subprocess
from pathlib import Path
from typing import Dict, Any, Optional

logger = logging.getLogger(__name__)

# ---------- 可選依賴 ----------
try:
    import graphify
    HAS_GRAPHIFY = True
except ImportError:
    HAS_GRAPHIFY = False
    logger.warning("graphifyy 未安裝，請執行: pip install graphifyy")

# ---------- 配置 ----------
PLUGIN_INFO = {
    "command": "/graphify",
    "icon": "🕸️",
    "handler": "handle_graphify",
    "description": "知識圖譜工具：將任意目錄的程式碼/文件映射為互動式知識圖譜，支援查詢、路徑追蹤、概念解釋。",
    "intent_keywords": [
        ("建圖", "/graphify build"),
        ("知識圖譜", "/graphify build"),
        ("圖譜查詢", "/graphify query"),
        ("圖譜路徑", "/graphify path"),
        ("圖譜解釋", "/graphify explain"),
    ],
    "tool_schema": {
        "name": "graphify",
        "description": "知識圖譜構建與查詢工具。使用 Graphify 將任意目錄的程式碼/文件映射為互動式知識圖譜（graph.json + graph.html + GRAPH_REPORT.md）。支援 build（建圖）、query（自然語言查詢）、path（兩節點間最短路徑）、explain（解釋特定概念）。",
        "parameters": {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["build", "query", "path", "explain"],
                    "description": "操作類型：build(對指定目錄建立知識圖譜), query(對已建圖譜進行自然語言查詢), path(查詢兩個概念之間的路徑), explain(解釋特定概念)"
                },
                "directory": {
                    "type": "string",
                    "description": "目標目錄的絕對路徑（build 時必需，其他操作可選）。可指定任意目錄。例如 /home/ubuntu/my-project"
                },
                "args": {
                    "type": "string",
                    "description": "操作參數。query 時為自然語言問題；explain 時為概念名稱；path 時格式為 '節點A 節點B'（兩個名稱以空格分隔）"
                },
                "output_dir": {
                    "type": "string",
                    "description": "圖譜輸出的目標目錄（build 時可選）。預設為目標目錄下的 graphify-out/。"
                }
            },
            "required": ["action"]
        }
    },
    "update": "202607300147"
}


def _run_graphify(cmd: list, cwd: str = None, timeout: int = 300) -> Dict[str, Any]:
    """執行 graphify CLI 命令並返回結果"""
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout
        )
        return {
            "success": result.returncode == 0,
            "stdout": result.stdout.strip(),
            "stderr": result.stderr.strip(),
            "returncode": result.returncode
        }
    except subprocess.TimeoutExpired:
        return {
            "success": False,
            "stdout": "",
            "stderr": "執行超時（超過 {} 秒）".format(timeout),
            "returncode": -1
        }
    except FileNotFoundError:
        return {
            "success": False,
            "stdout": "",
            "stderr": "找不到 graphify 命令。請確認已安裝: pip install graphifyy",
            "returncode": -1
        }
    except Exception as e:
        return {
            "success": False,
            "stdout": "",
            "stderr": str(e),
            "returncode": -1
        }


def _find_graph_json(directory: str) -> Optional[str]:
    """在目錄中尋找 graph.json"""
    # 優先找 graphify-out/graph.json
    candidate = os.path.join(directory, "graphify-out", "graph.json")
    if os.path.exists(candidate):
        return candidate
    # 再找目錄下的 graph.json
    candidate = os.path.join(directory, "graph.json")
    if os.path.exists(candidate):
        return candidate
    return None


def handle_graphify(args, chat_id=None, agent_config=None) -> str:
    """
    處理 /graphify 命令或 LLM 工具調用。
    
    參數格式（字典或 JSON 字符串）:
        {
            "action": "build" | "query" | "path" | "explain",
            "directory": "/path/to/dir",    # build 必需，其他可選
            "args": "查詢內容或節點名稱",    # query/path/explain 需要
            "output_dir": "/output/path"     # build 可選
        }
    """
    if not HAS_GRAPHIFY:
        return json.dumps({
            "success": False,
            "error": "graphifyy 未安裝。請執行: pip install graphifyy",
            "tool": "graphify"
        }, ensure_ascii=False)
    
    # 解析參數
    if isinstance(args, str):
        # 空參數 → 顯示使用說明
        if not args.strip():
            return json.dumps({
                "success": False,
                "error": "請提供參數。使用方法：\n"
                         "  /graphify build <目錄路徑>        - 為目錄建立知識圖譜\n"
                         "  /graphify query <問題>             - 查詢已建立的圖譜\n"
                         "  /graphify path <節點A> <節點B>     - 查兩個概念之間的路徑\n"
                         "  /graphify explain <概念名稱>       - 解釋特定概念",
                "tool": "graphify"
            }, ensure_ascii=False)
        try:
            args = json.loads(args)
        except json.JSONDecodeError:
            return json.dumps({
                "success": False,
                "error": f"無法解析參數: {args}",
                "tool": "graphify"
            }, ensure_ascii=False)
    
    action = args.get("action", "")
    directory = args.get("directory", os.getcwd())
    extra_args = args.get("args", "")
    output_dir = args.get("output_dir", "")
    
    # 展開 ~ 和相對路徑
    directory = os.path.abspath(os.path.expanduser(directory))
    
    if action == "build":
        return _handle_build(directory, output_dir)
    elif action == "query":
        graph_json = _find_graph_json(directory)
        if not graph_json:
            return json.dumps({
                "success": False,
                "error": f"在目錄 {directory} 中找不到 graph.json。請先執行 build。",
                "tool": "graphify"
            }, ensure_ascii=False)
        return _handle_query(graph_json, extra_args)
    elif action == "path":
        graph_json = _find_graph_json(directory)
        if not graph_json:
            return json.dumps({
                "success": False,
                "error": f"在目錄 {directory} 中找不到 graph.json。請先執行 build。",
                "tool": "graphify"
            }, ensure_ascii=False)
        return _handle_path(graph_json, extra_args)
    elif action == "explain":
        graph_json = _find_graph_json(directory)
        if not graph_json:
            return json.dumps({
                "success": False,
                "error": f"在目錄 {directory} 中找不到 graph.json。請先執行 build。",
                "tool": "graphify"
            }, ensure_ascii=False)
        return _handle_explain(graph_json, extra_args)
    else:
        return json.dumps({
            "success": False,
            "error": f"未知的 action: {action}。支援: build, query, path, explain",
            "tool": "graphify"
        }, ensure_ascii=False)


def _handle_build(directory: str, output_dir: str = "") -> str:
    """對指定目錄建立知識圖譜"""
    if not os.path.isdir(directory):
        return json.dumps({
            "success": False,
            "error": f"目錄不存在: {directory}",
            "tool": "graphify"
        }, ensure_ascii=False)
    
    logger.info(f"開始為目錄建圖: {directory}")
    
    cmd = ["graphify", "build", directory]
    
    if output_dir:
        output_dir = os.path.abspath(os.path.expanduser(output_dir))
        cmd.extend(["--output", output_dir])
    
    result = _run_graphify(cmd, cwd=directory, timeout=600)
    
    if result["success"]:
        # 檢查輸出檔案
        out_dir = output_dir or os.path.join(directory, "graphify-out")
        files_found = []
        for f in ["graph.json", "graph.html", "GRAPH_REPORT.md"]:
            fp = os.path.join(out_dir, f)
            if os.path.exists(fp):
                size = os.path.getsize(fp)
                files_found.append(f"{f} ({_format_size(size)})")
        
        return json.dumps({
            "success": True,
            "action": "build",
            "directory": directory,
            "output_dir": out_dir,
            "files": files_found,
            "stdout": result["stdout"][-2000:] if result["stdout"] else "",
            "message": f"知識圖譜已建立於 {out_dir}，包含: {', '.join(files_found)}"
        }, ensure_ascii=False)
    else:
        return json.dumps({
            "success": False,
            "action": "build",
            "directory": directory,
            "error": result["stderr"] or result["stdout"] or "未知錯誤",
            "tool": "graphify"
        }, ensure_ascii=False)


def _handle_query(graph_json: str, question: str) -> str:
    """對已建圖譜進行自然語言查詢"""
    if not question:
        return json.dumps({
            "success": False,
            "error": "請提供查詢問題（args 參數）",
            "tool": "graphify"
        }, ensure_ascii=False)
    
    logger.info(f"圖譜查詢: {question}")
    
    result = _run_graphify(["graphify", "query", question, "--graph", graph_json], timeout=120)
    
    if result["success"]:
        return json.dumps({
            "success": True,
            "action": "query",
            "question": question,
            "graph": graph_json,
            "result": result["stdout"] if result["stdout"] else "查詢完成，無匹配結果。",
        }, ensure_ascii=False)
    else:
        return json.dumps({
            "success": False,
            "action": "query",
            "question": question,
            "error": result["stderr"] or result["stdout"] or "查詢失敗",
            "tool": "graphify"
        }, ensure_ascii=False)


def _handle_path(graph_json: str, nodes: str) -> str:
    """查詢兩個節點之間的最短路徑"""
    parts = nodes.split()
    if len(parts) < 2:
        return json.dumps({
            "success": False,
            "error": "請提供兩個節點名稱，以空格分隔（args 參數）。例如: 'FastAPI ModelField'",
            "tool": "graphify"
        }, ensure_ascii=False)
    
    node_a, node_b = parts[0], parts[1]
    logger.info(f"查詢路徑: {node_a} → {node_b}")
    
    result = _run_graphify(["graphify", "path", node_a, node_b, "--graph", graph_json], timeout=120)
    
    if result["success"]:
        return json.dumps({
            "success": True,
            "action": "path",
            "from": node_a,
            "to": node_b,
            "graph": graph_json,
            "result": result["stdout"] if result["stdout"] else "找不到路徑。",
        }, ensure_ascii=False)
    else:
        return json.dumps({
            "success": False,
            "action": "path",
            "from": node_a,
            "to": node_b,
            "error": result["stderr"] or result["stdout"] or "路徑查詢失敗",
            "tool": "graphify"
        }, ensure_ascii=False)


def _handle_explain(graph_json: str, concept: str) -> str:
    """解釋特定概念節點"""
    if not concept:
        return json.dumps({
            "success": False,
            "error": "請提供要解釋的概念名稱（args 參數）",
            "tool": "graphify"
        }, ensure_ascii=False)
    
    logger.info(f"解釋概念: {concept}")
    
    result = _run_graphify(["graphify", "explain", concept, "--graph", graph_json], timeout=120)
    
    if result["success"]:
        return json.dumps({
            "success": True,
            "action": "explain",
            "concept": concept,
            "graph": graph_json,
            "result": result["stdout"] if result["stdout"] else f"找不到 '{concept}' 的相關資訊。",
        }, ensure_ascii=False)
    else:
        return json.dumps({
            "success": False,
            "action": "explain",
            "concept": concept,
            "error": result["stderr"] or result["stdout"] or "解釋失敗",
            "tool": "graphify"
        }, ensure_ascii=False)


def _format_size(size_bytes: int) -> str:
    """將位元組轉為人類可讀格式"""
    for unit in ["B", "KB", "MB", "GB"]:
        if size_bytes < 1024:
            return f"{size_bytes:.1f} {unit}"
        size_bytes /= 1024
    return f"{size_bytes:.1f} TB"


# ================== 直接測試 ==================
if __name__ == "__main__":
    # 測試 build
    test_args = {
        "action": "build",
        "directory": os.path.expanduser("~/.mok/core")
    }
    print(handle_graphify(test_args))
