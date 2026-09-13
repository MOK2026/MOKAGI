# ------------------------------------------------------------------------------------ #
# 工具名稱: stt (語音識別 / Speech-to-Text)
# 用途: 將語音/音頻檔案轉換為文字。支援 Telegram 語音消息、OGG、MP3、WAV 等格式。
#
# 主要函數:
#   handle_stt(args, chat_id, agent_config)
#       - 入口函數，處理 /stt 命令或 LLM 工具調用。
#       - 接收音頻檔案路徑或 URL，調用 Whisper 進行語音識別。
#       - 返回 JSON 格式轉寫結果。
#
# 依賴:
#   openai-whisper (pip install openai-whisper)
#   librosa + soundfile (pip install librosa soundfile) - 音頻讀取與轉換（無需 ffmpeg）
#   ffmpeg（可選，作為備用轉換方案）
#
# 更新記錄:
#   202607170520 - 初版，使用 whisper + ffmpeg
#   202607170523 - v2，改用 librosa 為主力（無需 ffmpeg），ffmpeg 作為備用
# ------------------------------------------------------------------------------------ #

import os
import sys
import json
import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------- 依賴檢查 ----------
try:
    import whisper
    HAS_WHISPER = True
except ImportError:
    HAS_WHISPER = False

try:
    import librosa
    import soundfile
    HAS_LIBROSA = True
except ImportError:
    HAS_LIBROSA = False

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    HAS_REQUESTS = False

try:
    import numpy as np
    HAS_NUMPY = True
except ImportError:
    HAS_NUMPY = False


def _has_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return True
    except Exception:
        return False

HAS_FFMPEG = _has_ffmpeg()

# qqq whisper_models 轉為用 .agent內選
WHISPER_MODEL_DIR = os.path.expanduser("~/.mok/whisper_models")

PLUGIN_INFO = {
    "command": "/stt",
    "icon": "🎤",
    "handler": "handle_stt",
    "description": "語音識別：將語音/音頻檔案轉為文字。支援 Telegram 語音消息、OGG、MP3、WAV 等。",
    "intent_keywords": [
        ("/語音識別", "/stt"),
        ("/轉寫", "/stt"),
        ("/語音轉文字", "/stt"),
        ("/聽寫", "/stt"),
    ],
    "tool_schema": {
        "name": "stt",
        "description": (
            "語音識別工具（Speech-to-Text）：將音頻檔案轉換為文字。\n"
            "支援多種音頻格式（OGG、MP3、WAV、FLAC、M4A 等），使用 librosa 或 ffmpeg 自動轉換。\n"
            "使用 OpenAI Whisper 本地模型進行轉寫。\n\n"
            "【功能】給定音頻檔案路徑或 URL，返回轉寫後的文字內容。\n\n"
            "【返回格式】\n"
            "- 成功時返回 JSON：{\"success\": true, \"text\": \"轉寫文字\", \"language\": \"偵測語言\", \"model\": \"使用的模型\"}\n"
            "- 失敗時返回 JSON：{\"success\": false, \"error\": \"錯誤訊息\"}\n\n"
            "【支援格式】ogg, mp3, wav, flac, m4a, aac, wma, opus, webm\n\n"
            "【參數】\n"
            "- file_path：本地音頻檔案路徑\n"
            "- url：音頻檔案 URL（會自動下載）\n"
            "- model：Whisper 模型大小（tiny/base/small/medium/large，預設 tiny）\n"
            "- language：指定語言代碼（如 zh, en, ja），留空則自動偵測\n\n"
            "【注意】\n"
            "- 首次使用會自動下載 Whisper 模型（tiny ~72MB）\n"
            "- 優先使用 librosa 處理音頻（無需 ffmpeg），ffmpeg 作為備用\n"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "本地音頻檔案的完整路徑。例如 /tmp/voice.ogg"
                },
                "url": {
                    "type": "string",
                    "description": "音頻檔案的 URL 地址（與 file_path 二選一）。例如 https://example.com/audio.mp3"
                },
                "model": {
                    "type": "string",
                    "description": "Whisper 模型大小（可選）。tiny(最快)/base/small/medium/large(最準)。預設 tiny。",
                    "enum": ["tiny", "base", "small", "medium", "large"]
                },
                "language": {
                    "type": "string",
                    "description": "指定語言代碼（可選）。如 zh（中文）、en（英文）、ja（日文）。留空自動偵測。"
                }
            }
        }
    }
}


# ================== 音頻轉換：librosa（主力，無需 ffmpeg）==================

def _convert_with_librosa(input_path):
    """
    使用 librosa 載入音頻，重採樣至 16kHz mono，輸出為臨時 WAV。
    返回 wav 路徑，失敗返回 None。
    """
    if not HAS_LIBROSA or not HAS_NUMPY:
        return None
    try:
        # librosa 自動處理多種格式（ogg, mp3, flac 等）
        audio, sr = librosa.load(input_path, sr=16000, mono=True)
        if audio is None or len(audio) == 0:
            return None

        fd, wav_path = tempfile.mkstemp(suffix=".wav")
        os.close(fd)
        soundfile.write(wav_path, audio, 16000, subtype="PCM_16")
        return wav_path
    except Exception as e:
        logger.warning(f"[STT] librosa 轉換失敗: {e}")
        return None


# ================== 音頻轉換：ffmpeg（備用）==================

def _convert_with_ffmpeg(input_path):
    """使用 ffmpeg 轉換為 16kHz mono WAV。"""
    if not HAS_FFMPEG:
        return None

    fd, output_path = tempfile.mkstemp(suffix=".wav")
    os.close(fd)

    try:
        subprocess.run(
            ["ffmpeg", "-y", "-i", input_path,
             "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
             output_path],
            capture_output=True, timeout=60
        )
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return output_path
    except Exception as e:
        logger.warning(f"[STT] ffmpeg 轉換失敗: {e}")

    # 清理
    try:
        os.remove(output_path)
    except Exception:
        pass
    return None


def _convert_audio(input_path):
    """
    將音頻轉換為 16kHz mono WAV。
    優先使用 librosa，備用 ffmpeg。
    返回 (wav_path, is_temp) — is_temp 表示是否需要清理。
    """
    # 若已是 16kHz mono WAV，直接返回
    ext = Path(input_path).suffix.lower()
    if ext == ".wav" and HAS_LIBROSA:
        try:
            info = soundfile.info(input_path)
            if info.samplerate == 16000 and info.channels == 1:
                return input_path, False
        except Exception:
            pass

    # 嘗試 librosa
    result = _convert_with_librosa(input_path)
    if result:
        return result, True

    # 備用 ffmpeg
    result = _convert_with_ffmpeg(input_path)
    if result:
        return result, True

    return None, False


# ================== 下載音頻 ==================

def _download_audio(url, output_dir=None):
    if not HAS_REQUESTS:
        return None
    if output_dir is None:
        output_dir = tempfile.gettempdir()

    url_path = url.split("?")[0]
    ext = Path(url_path).suffix.lower()
    if not ext or len(ext) > 5:
        ext = ".ogg"

    fd, local_path = tempfile.mkstemp(suffix=ext, dir=output_dir)
    os.close(fd)

    try:
        resp = requests.get(url, timeout=120, stream=True)
        resp.raise_for_status()
        with open(local_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                f.write(chunk)
        return local_path
    except Exception as e:
        logger.error(f"[STT] 下載失敗: {e}")
        try:
            os.remove(local_path)
        except Exception:
            pass
        return None


# ================== Whisper 轉寫 ==================

def _transcribe_whisper(audio_path, model_name="tiny", language=None):
    if not HAS_WHISPER:
        return None
    os.makedirs(WHISPER_MODEL_DIR, exist_ok=True)

    try:
        model = whisper.load_model(model_name or "tiny", download_root=WHISPER_MODEL_DIR)
        opts = {}
        if language:
            opts["language"] = language

        result = model.transcribe(audio_path, **opts)
        return {
            "text": result.get("text", "").strip(),
            "language": result.get("language", "unknown"),
            "model": f"whisper-{model_name or 'tiny'}",
            "segments": len(result.get("segments", []))
        }
    except Exception as e:
        logger.error(f"[STT] Whisper 轉寫失敗: {e}")
        return None


# ================== OpenAI API 備用 ==================

def _transcribe_openai_api(audio_path, agent_config=None, language=None):
    api_key = None
    if agent_config:
        api_key = agent_config.get("OPENAI_API_KEY", "")
    if not api_key:
        api_key = os.environ.get("OPENAI_API_KEY", "")
    if not api_key or not HAS_REQUESTS:
        return None

    try:
        url = "https://api.openai.com/v1/audio/transcriptions"
        headers = {"Authorization": f"Bearer {api_key}"}
        with open(audio_path, "rb") as f:
            files = {"file": (os.path.basename(audio_path), f)}
            data = {"model": "whisper-1"}
            if language:
                data["language"] = language
            resp = requests.post(url, headers=headers, files=files, data=data, timeout=120)
            resp.raise_for_status()
            result = resp.json()

        return {
            "text": result.get("text", "").strip(),
            "language": language or "auto",
            "model": "whisper-1 (API)",
            "segments": 1
        }
    except Exception as e:
        logger.error(f"[STT] OpenAI API 失敗: {e}")
        return None


# ================== 主入口 ==================

def handle_stt(args, chat_id="web", agent_config=None):
    # 參數解析
    file_path = None
    url = None
    model_name = "tiny"
    language = None

    if isinstance(args, dict):
        file_path = args.get("file_path", "") or args.get("args", "")
        url = args.get("url", "")
        model_name = args.get("model", "tiny")
        language = args.get("language", None)
    elif isinstance(args, str):
        s = args.strip()
        if s.startswith("{"):
            try:
                p = json.loads(s)
                file_path = p.get("file_path", "")
                url = p.get("url", "")
                model_name = p.get("model", "tiny")
                language = p.get("language", None)
            except json.JSONDecodeError:
                file_path = s
        elif s.startswith("http://") or s.startswith("https://"):
            url = s
        else:
            file_path = s

    if agent_config:
        if model_name == "tiny":
            model_name = agent_config.get("STT_MODEL", model_name)
        if not language:
            language = agent_config.get("STT_LANGUAGE", None)

    if not file_path and not url:
        return json.dumps({"success": False, "error": "請提供 file_path 或 url。"}, ensure_ascii=False)

    # 依賴檢查
    if not HAS_WHISPER and not (agent_config and agent_config.get("OPENAI_API_KEY")):
        return json.dumps({
            "success": False,
            "error": "缺少 openai-whisper，請執行 /admin pip install openai-whisper",
            "error_type": "missing_dependency",
            "dependency": "openai-whisper"
        }, ensure_ascii=False)

    if not HAS_LIBROSA and not HAS_FFMPEG:
        return json.dumps({
            "success": False,
            "error": "缺少音頻處理庫。請執行 /admin pip install librosa soundfile，或安裝 ffmpeg。",
            "error_type": "missing_dependency",
            "dependency": "librosa 或 ffmpeg"
        }, ensure_ascii=False)

    # 取得音頻
    local_audio = None
    tmp_files = []

    try:
        if url:
            local_audio = _download_audio(url)
            if not local_audio:
                return json.dumps({"success": False, "error": f"無法下載: {url}"}, ensure_ascii=False)
            tmp_files.append(local_audio)
        else:
            local_audio = file_path
            if not os.path.exists(local_audio):
                return json.dumps({"success": False, "error": f"檔案不存在: {local_audio}"}, ensure_ascii=False)

        # 轉換音頻
        wav_path, is_temp = _convert_audio(local_audio)
        if wav_path is None:
            return json.dumps({
                "success": False,
                "error": "無法轉換音頻格式。請確認檔案有效，並已安裝 librosa 或 ffmpeg。"
            }, ensure_ascii=False)
        if is_temp:
            tmp_files.append(wav_path)

        # 轉寫
        result = None
        if HAS_WHISPER:
            result = _transcribe_whisper(wav_path, model_name, language)
        if result is None:
            result = _transcribe_openai_api(wav_path, agent_config, language)

        if result is None:
            return json.dumps({"success": False, "error": "語音轉寫失敗，請檢查檔案與模型。"}, ensure_ascii=False)

        return json.dumps({
            "success": True,
            "text": result["text"],
            "language": result["language"],
            "model": result["model"],
            "segments": result.get("segments", 1)
        }, ensure_ascii=False)

    except Exception as e:
        logger.error(f"[STT] 錯誤: {e}")
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    finally:
        for f in tmp_files:
            try:
                if os.path.exists(f):
                    os.remove(f)
            except Exception:
                pass
