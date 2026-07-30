# ------------------------------------------------------------------------------------ #
# 工具名稱: vision (視覺分析)
# 用途: 接收圖片/影片路徑，使用多模態 LLM 分析內容。
#       支援圖片描述、文字提取(OCR)、影片關鍵幀分析。
#
# 主要函數:
#   handle_vision(args, chat_id, agent_config)
#       - 入口函數，處理 /vision 命令或 LLM 工具調用。
#       - 接收 file_path / url / base64 圖片或影片。
#       - 調用多模態模型分析，返回文字描述。
#
# 依賴:
#   httpx（通常已安裝）
#   ffmpeg（可選，用於影片關鍵幀提取）
#
# 支援的多模態模型:
#   - gemini-3.5-flash / gemini-2.0-flash（Google，原生支援圖片）
#   - 任何 OpenAI 兼容的 vision 模型
#
# 更新記錄:
#   202607170315 - 初版，支援圖片分析 + 影片關鍵幀分析
# ------------------------------------------------------------------------------------ #

import os
import sys
import json
import base64
import asyncio
import logging
import subprocess
import tempfile
import mimetypes
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------- 依賴 ----------
try:
    import httpx
    HAS_HTTPX = True
except ImportError:
    HAS_HTTPX = False

# ---------- 支援的圖片格式 ----------
IMAGE_EXTENSIONS = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.bmp', '.tiff', '.tif'}
VIDEO_EXTENSIONS = {'.mp4', '.mov', '.avi', '.mkv', '.webm', '.flv', '.wmv', '.m4v'}

# ---------- 多模態模型配置 ----------
# 優先使用支援 vision 的模型
VISION_MODEL_PREFERENCE = [
    "gemini-3.5-flash",     # Google，原生支援
    "gemini-2.0-flash",     # Google
    "deepseek-v4-flash",    # DeepSeek（可能支援）
    "minimax-m3:cloud",     # MiniMax（可能支援）
]

PLUGIN_INFO = {
    "command": "/vision",
    "icon": "👁️",
    "handler": "handle_vision",
    "description": "視覺分析：分析圖片或影片內容，支援圖片描述、文字提取(OCR)、影片關鍵幀分析。",

    "intent_keywords": [
        ("/視覺", "/vision"),
        ("/看", "/vision"),
        ("/vision", "/vision"),
        ("/圖片", "/vision"),
        ("/分析圖片", "/vision"),
    ],

    "tool_schema": {
        "name": "vision",
        "description": (
            "視覺分析工具：使用多模態 AI 模型分析圖片或影片內容。\n\n"
            "【功能】給定圖片/影片的檔案路徑、URL 或 base64 編碼，返回 AI 對內容的描述分析。\n"
            "支援：圖片描述、文字提取(OCR)、影片關鍵幀分析。\n\n"
            "【返回格式】\n"
            "- 成功時返回 JSON：{\"success\": true, \"analysis\": \"分析結果文字\", \"model\": \"使用的模型\"}\n"
            "- 失敗時返回 JSON：{\"success\": false, \"error\": \"錯誤訊息\"}\n\n"
            "【支援格式】\n"
            "- 圖片：jpg, jpeg, png, gif, webp, bmp\n"
            "- 影片：mp4, mov, avi, mkv, webm（自動提取關鍵幀）\n\n"
            "【使用方式】\n"
            "- 檔案路徑：/vision /tmp/photo.jpg\n"
            "- 或 LLM 調用：{\"file_path\": \"/tmp/photo.jpg\", \"question\": \"圖片中有什麼？\"}"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "圖片或影片的本地檔案路徑。例如 /tmp/photo.jpg"
                },
                "url": {
                    "type": "string",
                    "description": "圖片的 URL 地址（可選，與 file_path 二選一）"
                },
                "question": {
                    "type": "string",
                    "description": "對圖片/影片的提問（可選）。預設：'請詳細描述這張圖片/影片的內容，包括場景、人物、動作、文字等。'"
                },
                "model": {
                    "type": "string",
                    "description": "指定使用的模型（可選）。支援 vision 的模型如 gemini-3.5-flash。若不指定則自動選擇。"
                },
                "max_keyframes": {
                    "type": "integer",
                    "description": "影片分析時提取的最大關鍵幀數（可選，預設 3）",
                    "default": 3
                }
            },
            "required": []
        }
    }
}


# ========== 工具函數 ==========

def _get_image_base64(file_path: str) -> tuple:
    """讀取圖片檔案，返回 (base64_string, mime_type)"""
    mime_type, _ = mimetypes.guess_type(file_path)
    if not mime_type or not mime_type.startswith('image/'):
        mime_type = 'image/jpeg'  # fallback
    
    with open(file_path, 'rb') as f:
        data = base64.b64encode(f.read()).decode('utf-8')
    
    return data, mime_type


def _extract_video_keyframes(video_path: str, max_frames: int = 3) -> list:
    """使用 ffmpeg 提取影片關鍵幀，返回 [(frame_path, timestamp), ...]"""
    temp_dir = tempfile.mkdtemp(prefix="vision_frames_")
    frames = []
    
    try:
        # 取得影片時長
        probe_cmd = [
            'ffprobe', '-v', 'quiet', '-print_format', 'json',
            '-show_format', video_path
        ]
        result = subprocess.run(probe_cmd, capture_output=True, text=True, timeout=15)
        duration = 5.0  # 預設
        if result.returncode == 0:
            probe_data = json.loads(result.stdout)
            duration = float(probe_data.get('format', {}).get('duration', 5.0))
        
        # 計算截圖時間點（均勻分佈）
        interval = duration / (max_frames + 1)
        timestamps = [interval * (i + 1) for i in range(max_frames)]
        
        for i, ts in enumerate(timestamps):
            frame_path = os.path.join(temp_dir, f"frame_{i:02d}_{ts:.1f}s.jpg")
            extract_cmd = [
                'ffmpeg', '-y', '-ss', str(ts), '-i', video_path,
                '-vframes', '1', '-q:v', '5', frame_path
            ]
            result = subprocess.run(extract_cmd, capture_output=True, text=True, timeout=30)
            if result.returncode == 0 and os.path.exists(frame_path):
                frames.append((frame_path, ts))
        
    except FileNotFoundError:
        logger.warning("ffmpeg/ffprobe 未安裝，無法提取影片關鍵幀")
    except Exception as e:
        logger.error(f"提取關鍵幀失敗: {e}")
    
    if not frames:
        # fallback: 嘗試用單幀
        try:
            frame_path = os.path.join(temp_dir, "frame_00.jpg")
            subprocess.run([
                'ffmpeg', '-y', '-i', video_path,
                '-vframes', '1', '-q:v', '5', frame_path
            ], capture_output=True, timeout=30)
            if os.path.exists(frame_path):
                frames.append((frame_path, 0.0))
        except Exception:
            pass
    
    return frames


def _get_vision_model_config(agent_config: dict, preferred_model: str = None) -> dict:
    """獲取支援 vision 的模型配置（API URL, token, model name）"""
    # 先檢查 agent_config 中有哪些模型配置
    models_available = {}
    for key, value in agent_config.items():
        if key.startswith("MOK_MODEL_NAME") and not key.endswith(('0', '11', '12')):
            # 排除本地 Ollama 模型（通常不支援 vision）
            idx = key.replace("MOK_MODEL_NAME", "")
            url_key = f"MOK_MODEL_url{idx}"
            token_key = f"MOK_MODEL_token{idx}"
            if url_key in agent_config:
                models_available[value] = {
                    "url": agent_config[url_key],
                    "token": agent_config.get(token_key, ""),
                    "index": idx
                }
    
    # 按優先序找支援 vision 的模型
    search_order = [preferred_model] if preferred_model else VISION_MODEL_PREFERENCE
    for model_name in search_order:
        if model_name in models_available:
            cfg = models_available[model_name]
            return {
                "model": model_name,
                "api_url": cfg["url"],
                "api_token": cfg["token"],
            }
    
    # fallback：使用當前活躍模型
    current_model = agent_config.get("MOK_MODEL_NAME", "")
    current_url = agent_config.get("MOK_MODEL_url", "http://localhost:11434/v1")
    current_token = agent_config.get("MOK_MODEL_token", "")
    return {
        "model": current_model,
        "api_url": current_url,
        "api_token": current_token,
    }


async def _call_vision_model(
    image_base64: str,
    mime_type: str,
    question: str,
    model_config: dict,
    extra_images: list = None
) -> str:
    """調用多模態模型分析圖片"""
    if not HAS_HTTPX:
        return json.dumps({"success": False, "error": "缺少依賴 httpx"})
    
    # 構建訊息
    content_parts = [
        {"type": "text", "text": question},
        {
            "type": "image_url",
            "image_url": {
                "url": f"data:{mime_type};base64,{image_base64}",
                "detail": "high"
            }
        }
    ]
    
    # 添加額外圖片（影片關鍵幀）
    if extra_images:
        for img_b64, img_mime in extra_images:
            content_parts.append({
                "type": "image_url",
                "image_url": {
                    "url": f"data:{img_mime};base64,{img_b64}",
                    "detail": "high"
                }
            })
    
    messages = [
        {"role": "user", "content": content_parts}
    ]
    
    api_url = model_config["api_url"]
    api_token = model_config["api_token"]
    model_name = model_config["model"]
    
    # 確保 URL 以 /v1 結尾（OpenAI 兼容格式）
    if not api_url.rstrip('/').endswith('/v1'):
        if '/v1beta/' in api_url:
            pass  # Google API 的特殊路徑
        elif '/openai/' in api_url:
            pass  # 已經正確
        else:
            api_url = api_url.rstrip('/') + '/v1'
    
    # 確保有 /chat/completions
    if not api_url.endswith('/chat/completions'):
        api_url = api_url.rstrip('/') + '/chat/completions'
    
    headers = {"Content-Type": "application/json"}
    if api_token:
        headers["Authorization"] = f"Bearer {api_token}"
    
    payload = {
        "model": model_name,
        "messages": messages,
        "max_tokens": 2048,
        "temperature": 0.7,
    }
    
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(api_url, json=payload, headers=headers)
            if response.status_code != 200:
                error_detail = response.text[:500]
                logger.error(f"Vision API 調用失敗 ({response.status_code}): {error_detail}")
                return json.dumps({
                    "success": False,
                    "error": f"API 調用失敗 (HTTP {response.status_code}): {error_detail}"
                }, ensure_ascii=False)
            
            data = response.json()
            if "choices" in data and len(data["choices"]) > 0:
                content = data["choices"][0].get("message", {}).get("content", "")
                return content
            else:
                return json.dumps({
                    "success": False,
                    "error": f"API 返回異常: {json.dumps(data)[:500]}"
                }, ensure_ascii=False)
                
    except httpx.TimeoutException:
        return json.dumps({"success": False, "error": "API 調用超時（120秒）"})
    except Exception as e:
        return json.dumps({"success": False, "error": f"調用異常: {str(e)}"})


async def handle_vision(args, chat_id="web", agent_config=None):
    """
    處理 vision 命令或 LLM 工具調用。
    
    參數格式（支援兩種）:
    1. 命令行: "/vision /tmp/photo.jpg" 或 "/vision /tmp/photo.jpg 這張圖有什麼？"
    2. JSON: {"file_path": "/tmp/photo.jpg", "question": "..."} 或 dict 物件
    """
    if agent_config is None:
        agent_config = {}
    
    # --- 解析參數 ---
    file_path = None
    url = None
    question = "請詳細描述這張圖片/影片的內容，包括場景、人物、動作、文字、顏色、構圖等所有細節。如果是影片，請描述整體內容。"
    preferred_model = None
    max_keyframes = 3
    
    if isinstance(args, str):
        # 字串格式：可能是路徑 或 路徑+問題
        # 先嘗試解析 JSON
        try:
            parsed = json.loads(args)
            if isinstance(parsed, dict):
                file_path = parsed.get("file_path")
                url = parsed.get("url")
                question = parsed.get("question", question)
                preferred_model = parsed.get("model")
                max_keyframes = parsed.get("max_keyframes", 3)
        except (json.JSONDecodeError, TypeError):
            # 非 JSON，當作路徑處理
            parts = args.strip().split(maxsplit=1)
            file_path = parts[0] if parts else None
            if len(parts) > 1:
                question = parts[1]
    
    elif isinstance(args, dict):
        file_path = args.get("file_path")
        url = args.get("url")
        question = args.get("question", question)
        preferred_model = args.get("model")
        max_keyframes = args.get("max_keyframes", 3)
    
    # --- 驗證輸入 ---
    if not file_path and not url:
        return json.dumps({
            "success": False,
            "error": "請提供圖片/影片的檔案路徑或 URL。用法：/vision <路徑> [問題]",
            "usage": "/vision /tmp/photo.jpg 或 {\"file_path\": \"/tmp/photo.jpg\"}"
        }, ensure_ascii=False)
    
    # --- 處理 URL ---
    if url and not file_path:
        # 下載 URL 到暫存檔
        if not HAS_HTTPX:
            return json.dumps({"success": False, "error": "缺少依賴 httpx，無法下載 URL"})
        try:
            async with httpx.AsyncClient(timeout=60.0) as client:
                resp = await client.get(url, follow_redirects=True)
                if resp.status_code != 200:
                    return json.dumps({"success": False, "error": f"下載 URL 失敗: HTTP {resp.status_code}"})
                
                content_type = resp.headers.get("content-type", "image/jpeg")
                ext = mimetypes.guess_extension(content_type.split(";")[0]) or ".jpg"
                tmp = tempfile.NamedTemporaryFile(suffix=ext, delete=False)
                tmp.write(resp.content)
                tmp.close()
                file_path = tmp.name
        except Exception as e:
            return json.dumps({"success": False, "error": f"下載 URL 失敗: {str(e)}"})
    
    # --- 檢查檔案存在 ---
    if file_path and not os.path.exists(file_path):
        return json.dumps({"success": False, "error": f"檔案不存在: {file_path}"})
    
    # --- 判斷檔案類型 ---
    ext = Path(file_path).suffix.lower()
    is_video = ext in VIDEO_EXTENSIONS
    is_image = ext in IMAGE_EXTENSIONS
    
    if not is_image and not is_video:
        return json.dumps({
            "success": False,
            "error": f"不支援的檔案格式: {ext}。支援的圖片格式: {', '.join(IMAGE_EXTENSIONS)}，影片格式: {', '.join(VIDEO_EXTENSIONS)}"
        })
    
    # --- 獲取模型配置 ---
    model_config = _get_vision_model_config(agent_config, preferred_model)
    
    # --- 分析 ---
    try:
        if is_image:
            # 圖片分析
            img_b64, mime_type = _get_image_base64(file_path)
            result = await _call_vision_model(img_b64, mime_type, question, model_config)
            
            return json.dumps({
                "success": True,
                "analysis": result,
                "model": model_config["model"],
                "type": "image",
                "file": file_path
            }, ensure_ascii=False)
        
        elif is_video:
            # 影片分析：提取關鍵幀
            frames = _extract_video_keyframes(file_path, max_keyframes)
            
            if not frames:
                return json.dumps({
                    "success": False,
                    "error": "無法提取影片關鍵幀。請確認 ffmpeg 已安裝。"
                })
            
            # 讀取所有幀
            frame_data = []
            for frame_path, ts in frames:
                try:
                    img_b64, mime_type = _get_image_base64(frame_path)
                    frame_data.append((img_b64, mime_type, ts))
                except Exception as e:
                    logger.error(f"讀取關鍵幀失敗 {frame_path}: {e}")
            
            if not frame_data:
                return json.dumps({"success": False, "error": "無法讀取影片關鍵幀"})
            
            # 分析第一幀 + 附加其他幀
            first = frame_data[0]
            extra = [(b64, mime) for b64, mime, _ in frame_data[1:]]
            
            video_question = f"{question}\n\n（這是從影片中提取的 {len(frame_data)} 個關鍵幀，請綜合分析影片內容。）"
            result = await _call_vision_model(first[0], first[1], video_question, model_config, extra)
            
            # 清理暫存幀
            if frames:
                tmp_dir = os.path.dirname(frames[0][0])
                for f in os.listdir(tmp_dir):
                    try:
                        os.remove(os.path.join(tmp_dir, f))
                    except Exception:
                        pass
                try:
                    os.rmdir(tmp_dir)
                except Exception:
                    pass
            
            return json.dumps({
                "success": True,
                "analysis": result,
                "model": model_config["model"],
                "type": "video",
                "file": file_path,
                "keyframes": len(frame_data)
            }, ensure_ascii=False)
    
    except Exception as e:
        logger.exception("Vision 分析失敗")
        return json.dumps({
            "success": False,
            "error": f"分析失敗: {str(e)}"
        })


# ========== 測試用 ==========
if __name__ == "__main__":
    async def test():
        result = await handle_vision(
            {"file_path": "/tmp/test.jpg", "question": "這張圖片有什麼？"},
            chat_id="test",
            agent_config={"MOK_MODEL_NAME": "gemini-3.5-flash"}
        )
        print(result)
    
    asyncio.run(test())
