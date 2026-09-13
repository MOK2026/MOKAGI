# ------------------------------------------------------------------------------------ #
# 工具名稱: speech (輕量語音識別 / Speech-to-Text Lite)
# 用途: 使用 speech_recognition 庫進行語音識別，支援 Google / Sphinx 後端。
#       無需下載大型模型，輕量快速，適合短語音。
#
# 主要函數:
#   handle_speech(args, chat_id, agent_config)
#       - 入口函數，接收音頻檔案路徑或 URL，返回 JSON 轉寫結果。
#
# 依賴:
#   SpeechRecognition (pip install SpeechRecognition)
#   pocketsphinx (可選，離線識別: pip install pocketsphinx)
#
# 更新記錄:
#   20260717 - 初版，使用 speech_recognition + Google / Sphinx
# ------------------------------------------------------------------------------------ #

import os
import sys
import json
import logging
import tempfile
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)

# ================== 依賴檢查 ==================

HAS_SPEECH_RECOGNITION = False
HAS_POCKETSPHINX = False
HAS_REQUESTS = False

try:
    import speech_recognition as sr
    HAS_SPEECH_RECOGNITION = True
except ImportError:
    pass

try:
    import pocketsphinx
    HAS_POCKETSPHINX = True
except ImportError:
    pass

try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    pass


def _has_ffmpeg():
    try:
        subprocess.run(["ffmpeg", "-version"], capture_output=True, timeout=5)
        return True
    except Exception:
        return False


HAS_FFMPEG = _has_ffmpeg()

# ================== 支援的音頻格式轉換 ==================

# speech_recognition 原生支援 WAV，其他格式先用 ffmpeg 轉換
SUPPORTED_FORMATS_RAW = {"wav", "aiff", "aif", "flac"}
SUPPORTED_FORMATS_FFMPEG = {"ogg", "mp3", "m4a", "aac", "wma", "opus", "webm", "mov", "avi", "mkv"}


def _convert_to_wav(input_path, output_path=None):
    """使用 ffmpeg 將音頻轉為 16kHz mono WAV"""
    if not HAS_FFMPEG:
        return None
    if output_path is None:
        output_path = input_path + "_converted.wav"
    try:
        result = subprocess.run([
            "ffmpeg", "-y", "-i", input_path,
            "-ar", "16000", "-ac", "1",
            "-sample_fmt", "s16",
            output_path
        ], capture_output=True, text=True, timeout=60)
        if os.path.exists(output_path) and os.path.getsize(output_path) > 0:
            return output_path
        else:
            logger.error(f"[speech] ffmpeg 轉換失敗: {result.stderr[:300]}")
            return None
    except Exception as e:
        logger.error(f"[speech] ffmpeg 異常: {e}")
        return None


def _download_file(url, dest_path):
    """下載遠端音頻檔案"""
    if not HAS_REQUESTS:
        return False
    try:
        r = requests.get(url, timeout=30, stream=True)
        r.raise_for_status()
        with open(dest_path, 'wb') as f:
            for chunk in r.iter_content(chunk_size=8192):
                f.write(chunk)
        return True
    except Exception as e:
        logger.error(f"[speech] 下載失敗: {e}")
        return False


# ================== 語音識別核心 ==================

def _recognize_google(audio_data, language=None):
    """使用 Google Speech Recognition（免費，需聯網）"""
    if not HAS_SPEECH_RECOGNITION:
        return None
    try:
        recognizer = sr.Recognizer()
        lang = language if language else "zh-TW"
        text = recognizer.recognize_google(audio_data, language=lang)
        return {
            "text": text.strip(),
            "language": lang,
            "model": "google-free"
        }
    except sr.UnknownValueError:
        return {"text": "", "language": language or "unknown", "model": "google-free",
                "note": "無法識別語音內容"}
    except sr.RequestError as e:
        logger.error(f"[speech] Google API 錯誤: {e}")
        return None
    except Exception as e:
        logger.error(f"[speech] Google 識別異常: {e}")
        return None


def _recognize_sphinx(audio_data, language=None):
    """使用 CMU Sphinx 離線識別"""
    if not HAS_SPEECH_RECOGNITION or not HAS_POCKETSPHINX:
        return None
    try:
        recognizer = sr.Recognizer()
        text = recognizer.recognize_sphinx(audio_data)
        return {
            "text": text.strip(),
            "language": language or "en-US",
            "model": "sphinx-offline"
        }
    except sr.UnknownValueError:
        return {"text": "", "language": "unknown", "model": "sphinx-offline",
                "note": "無法識別語音內容"}
    except Exception as e:
        logger.error(f"[speech] Sphinx 識別異常: {e}")
        return None


def _process_audio_file(file_path, backend="google", language=None):
    """
    處理音頻檔案並返回識別結果。
    backend: "google" 或 "sphinx" 或 "auto"
    """
    if not HAS_SPEECH_RECOGNITION:
        return {"success": False, "error": "speech_recognition 庫未安裝。請執行: pip install SpeechRecognition"}

    recognizer = sr.Recognizer()
    tmp_files = []
    wav_path = file_path

    try:
        ext = Path(file_path).suffix.lower().lstrip(".")

        # 如果不是原生支援格式，先用 ffmpeg 轉換
        if ext not in SUPPORTED_FORMATS_RAW:
            if not HAS_FFMPEG:
                return {"success": False,
                        "error": f"不支援的格式 '{ext}'，且 ffmpeg 未安裝，無法轉換。支援格式: {', '.join(sorted(SUPPORTED_FORMATS_RAW))}"}
            converted = _convert_to_wav(file_path)
            if converted is None:
                return {"success": False, "error": f"ffmpeg 轉換 '{ext}' 格式失敗"}
            tmp_files.append(converted)
            wav_path = converted

        # 讀取音頻
        with sr.AudioFile(wav_path) as source:
            audio_data = recognizer.record(source)

        # 嘗試不同後端
        result = None

        if backend in ("google", "auto"):
            result = _recognize_google(audio_data, language)
            if result and result.get("text"):
                result["success"] = True
                return result

        if backend in ("sphinx", "auto"):
            if backend == "sphinx" and not HAS_POCKETSPHINX:
                return {"success": False,
                        "error": "pocketsphinx 未安裝，無法使用離線模式。請執行: pip install pocketsphinx"}
            result = _recognize_sphinx(audio_data, language)
            if result:
                result["success"] = True
                return result

        if result and result.get("text") == "":
            result["success"] = True
            return result

        return {"success": False, "error": "所有識別後端均失敗或未安裝"}

    except FileNotFoundError:
        return {"success": False, "error": f"音頻檔案不存在: {file_path}"}
    except Exception as e:
        logger.error(f"[speech] 處理音頻異常: {e}")
        return {"success": False, "error": f"處理音頻時發生錯誤: {str(e)}"}
    finally:
        for tmp in tmp_files:
            try:
                os.remove(tmp)
            except Exception:
                pass


# ================== PLUGIN_INFO ==================

PLUGIN_INFO = {
    "command": "/speech",
    "icon": "🗣️",
    "handler": "handle_speech",
    "description": "輕量語音識別：使用 Google/Sphinx 將語音轉文字，無需大型模型。",
    "intent_keywords": [
        ("/輕量語音識別", "/speech"),
        ("/輕量轉寫", "/speech"),
        ("/speech", "/speech"),
    ],
    "tool_schema": {
        "name": "speech",
        "description": "輕量語音識別工具（Speech-to-Text Lite）：使用 Google Speech Recognition 或 CMU Sphinx 將音頻轉文字，無需下載大型模型。\n\n【功能】給定音頻檔案路徑或 URL，返回轉寫文字。\n\n【與 /stt 的區別】\n- /stt：基於 OpenAI Whisper，準確度高，需下載模型（tiny ~72MB）\n- /speech：基於 Google Speech API（免費）或 Sphinx（離線），無需模型下載，適合短語音\n\n【後端】\n- google（預設）：免費 Google Web Speech API，需聯網，支援多語言\n- sphinx：CMU PocketSphinx 離線識別，需安裝 pocketsphinx\n- auto：自動依序嘗試 google → sphinx\n\n【支援格式】\n- 原生：WAV, AIFF, FLAC\n- 需 ffmpeg 轉換：OGG, MP3, M4A, AAC, WMA, OPUS, WEBM\n\n【注意】\n- Google 後端為免費 API，不保證長期可用，單次最長約 10 秒\n- Sphinx 離線識別準確度較低，僅建議用於英文",
        "parameters": {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "本地音頻檔案路徑。例如 /tmp/voice.ogg"
                },
                "url": {
                    "type": "string",
                    "description": "音頻檔案 URL（與 file_path 二選一）。例如 https://example.com/audio.mp3"
                },
                "backend": {
                    "type": "string",
                    "enum": ["google", "sphinx", "auto"],
                    "description": "識別後端：google（需聯網）、sphinx（離線）、auto（自動）。預設 google。"
                },
                "language": {
                    "type": "string",
                    "description": "語言代碼（可選）。如 zh-TW（繁體中文）、zh-CN（簡體中文）、en-US（英文）、ja-JP（日文）。預設 zh-TW。"
                }
            }
        }
    }
}


# ================== 入口處理函數 ==================

def handle_speech(args, chat_id, agent_config):
    """
    處理 /speech 命令或 LLM 工具調用。

    參數:
        args: dict，可能包含 file_path, url, backend, language
        chat_id: 聊天 ID
        agent_config: Agent 配置字典

    返回:
        str: JSON 格式的結果
    """
    file_path = args.get("file_path", "").strip() if isinstance(args, dict) else ""
    url = args.get("url", "").strip() if isinstance(args, dict) else ""
    backend = args.get("backend", "google").strip() if isinstance(args, dict) else "google"
    language = args.get("language", "").strip() if isinstance(args, dict) else ""

    # 參數驗證
    if not file_path and not url:
        return json.dumps({
            "success": False,
            "error": "請提供 file_path（本地檔案路徑）或 url（音頻檔案網址）",
            "tool": "speech"
        }, ensure_ascii=False)

    if not HAS_SPEECH_RECOGNITION:
        return json.dumps({
            "success": False,
            "error": "speech_recognition 庫未安裝。請執行: pip install SpeechRecognition",
            "tool": "speech"
        }, ensure_ascii=False)

    tmp_files = []

    try:
        # 如果是 URL，先下載
        if url and not file_path:
            if not HAS_REQUESTS:
                return json.dumps({
                    "success": False,
                    "error": "requests 庫未安裝，無法下載 URL。請執行: pip install requests",
                    "tool": "speech"
                }, ensure_ascii=False)

            ext = Path(url.split("?")[0]).suffix or ".ogg"
            tmp_fd, tmp_path = tempfile.mkstemp(suffix=ext)
            os.close(tmp_fd)
            tmp_files.append(tmp_path)

            if not _download_file(url, tmp_path):
                return json.dumps({
                    "success": False,
                    "error": f"下載音頻檔案失敗: {url}",
                    "tool": "speech"
                }, ensure_ascii=False)

            file_path = tmp_path

        # 檢查檔案是否存在
        if not os.path.exists(file_path):
            return json.dumps({
                "success": False,
                "error": f"音頻檔案不存在: {file_path}",
                "tool": "speech"
            }, ensure_ascii=False)

        # 執行識別
        result = _process_audio_file(file_path, backend=backend, language=language or None)

        return json.dumps(result, ensure_ascii=False)

    except Exception as e:
        logger.error(f"[speech] handle_speech 異常: {e}")
        return json.dumps({
            "success": False,
            "error": f"執行錯誤: {str(e)}",
            "tool": "speech"
        }, ensure_ascii=False)

    finally:
        for tmp in tmp_files:
            try:
                os.remove(tmp)
            except Exception:
                pass


# ================== 獨立測試 ==================

if __name__ == "__main__":
    # 簡單測試：需要有實際音頻檔案
    import argparse

    ap = argparse.ArgumentParser(description="輕量語音識別測試")
    ap.add_argument("file", nargs="?", help="音頻檔案路徑")
    ap.add_argument("--backend", choices=["google", "sphinx", "auto"], default="google")
    ap.add_argument("--lang", default="zh-TW", help="語言代碼")
    args_parsed = ap.parse_args()

    if not args_parsed.file:
        print("用法: python speech.py <音頻檔案> [--backend google|sphinx|auto] [--lang zh-TW]")
        sys.exit(1)

    test_args = {
        "file_path": args_parsed.file,
        "backend": args_parsed.backend,
        "language": args_parsed.lang
    }
    result = handle_speech(test_args, "test_chat", {})
    print(result)
