# ------------------------------------------------------------------------------------ #
# 工具名稱: speech2text (語音識別 / Speech-to-Text)
# 用途: 將語音/音頻檔案轉換為文字。支援多種格式（OGG/MP3/WAV/FLAC/M4A/WEBM）。
#
# 引擎策略（自動降級）:
#   1. faster-whisper  (首選，輕量快速，CTranslate2 推理)
#   2. openai-whisper  (備用，若已安裝)
#   3. Google Speech   (無需模型，免費線上識別)
#
# 主要函數:
#   handle_speech2text(args, chat_id, agent_config)
#
# 依賴（依優先級）:
#   faster-whisper (pip install faster-whisper)  ← 推薦
#   openai-whisper (pip install openai-whisper)
#   SpeechRecognition + pydub (pip install SpeechRecognition pydub)
#
# 更新記錄:
#   202607171340 - 泠製作 🥰 初版，三引擎自動降級架構
# ------------------------------------------------------------------------------------ #

import os
import sys
import json
import logging
import subprocess
import tempfile
from pathlib import Path

logger = logging.getLogger(__name__)

# ================== PLUGIN_INFO ==================
PLUGIN_INFO = {
    "command": "speech2text",
    "handler": "handle_speech2text",
    "description": "語音識別工具：將音頻轉為文字。支援多引擎自動降級（faster-whisper → openai-whisper → Google）。用法: /speech2text <檔案路徑|URL> [語言]",
    "keywords": ["語音識別", "speech to text", "語音轉文字", "辨識語音", "轉寫", "transcribe"],
    "params": {
        "file_path": "本地音頻檔案路徑",
        "url": "音頻 URL",
        "model": "模型大小: tiny/base/small/medium/large (預設 tiny)",
        "language": "語言代碼如 zh/en/ja (可選)"
    }
}

# ================== 依賴檢測 ==================
HAS_FASTER_WHISPER = False
HAS_OPENAI_WHISPER = False
HAS_SPEECHRECOG = False

try:
    from faster_whisper import WhisperModel
    HAS_FASTER_WHISPER = True
    logger.info("[speech2text] ✅ faster-whisper 可用")
except ImportError:
    logger.info("[speech2text] ⚠️ faster-whisper 未安裝")

try:
    import whisper
    HAS_OPENAI_WHISPER = True
    logger.info("[speech2text] ✅ openai-whisper 可用")
except ImportError:
    logger.info("[speech2text] ⚠️ openai-whisper 未安裝")

try:
    import speech_recognition as sr
    from pydub import AudioSegment
    HAS_SPEECHRECOG = True
    logger.info("[speech2text] ✅ SpeechRecognition 可用")
except ImportError:
    logger.info("[speech2text] ⚠️ SpeechRecognition 未安裝")

# 模型快取目錄
MODEL_DIR = os.path.expanduser("~/.mok/.speech2text_models")
os.makedirs(MODEL_DIR, exist_ok=True)


# ================== 音頻轉換 ==================

def _to_wav(input_path: str) -> str:
    """將任意音頻轉為 16kHz mono WAV（用 pydub 或 ffmpeg）"""
    wav_path = input_path.rsplit(".", 1)[0] + "_conv.wav"

    # 已係 WAV 就直接用
    if input_path.lower().endswith(".wav"):
        return input_path

    # 方法 1: pydub
    try:
        from pydub import AudioSegment
        audio = AudioSegment.from_file(input_path)
        audio = audio.set_frame_rate(16000).set_channels(1)
        audio.export(wav_path, format="wav")
        logger.info(f"[speech2text] pydub 轉換完成: {wav_path}")
        return wav_path
    except Exception as e:
        logger.warning(f"[speech2text] pydub 轉換失敗: {e}")

    # 方法 2: ffmpeg
    try:
        subprocess.run([
            "ffmpeg", "-y", "-i", input_path,
            "-ar", "16000", "-ac", "1", "-sample_fmt", "s16",
            wav_path
        ], capture_output=True, timeout=60, check=True)
        logger.info(f"[speech2text] ffmpeg 轉換完成: {wav_path}")
        return wav_path
    except Exception as e:
        logger.error(f"[speech2text] ffmpeg 轉換失敗: {e}")
        raise RuntimeError(f"無法轉換音頻格式: {e}")


# ================== 引擎1: faster-whisper ==================

def _transcribe_faster_whisper(audio_path: str, model_name="tiny", language=None):
    """使用 faster-whisper (CTranslate2)，比 openai-whisper 快 4x、記憶體少 4x"""
    if not HAS_FASTER_WHISPER:
        return None

    try:
        compute_type = "int8"  # CPU 友善
        model = WhisperModel(
            model_name or "tiny",
            device="cpu",
            compute_type=compute_type,
            download_root=MODEL_DIR
        )

        opts = {}
        if language:
            opts["language"] = language

        segments, info = model.transcribe(audio_path, **opts)

        text = " ".join(seg.text.strip() for seg in segments)

        return {
            "text": text.strip(),
            "language": info.language,
            "model": f"faster-whisper-{model_name or 'tiny'}",
            "engine": "faster-whisper"
        }
    except Exception as e:
        logger.error(f"[speech2text] faster-whisper 失敗: {e}")
        return None


# ================== 引擎2: openai-whisper ==================

def _transcribe_openai_whisper(audio_path: str, model_name="tiny", language=None):
    """使用 openai-whisper 作為備用"""
    if not HAS_OPENAI_WHISPER:
        return None

    try:
        model = whisper.load_model(model_name or "tiny", download_root=MODEL_DIR)
        opts = {}
        if language:
            opts["language"] = language

        result = model.transcribe(audio_path, **opts)
        return {
            "text": result.get("text", "").strip(),
            "language": result.get("language", "unknown"),
            "model": f"openai-whisper-{model_name or 'tiny'}",
            "engine": "openai-whisper"
        }
    except Exception as e:
        logger.error(f"[speech2text] openai-whisper 失敗: {e}")
        return None


# ================== 引擎3: Google Speech ==================

def _transcribe_google(audio_path: str, language=None):
    """使用 Google Speech Recognition（免費線上，無需模型）"""
    if not HAS_SPEECHRECOG:
        return None

    try:
        recognizer = sr.Recognizer()

        # 確保係 WAV
        wav_path = _to_wav(audio_path)

        with sr.AudioFile(wav_path) as source:
            audio = recognizer.record(source)

        lang = language or "zh-TW"
        text = recognizer.recognize_google(audio, language=lang)

        return {
            "text": text.strip(),
            "language": lang,
            "model": "google-speech",
            "engine": "google-speech"
        }
    except Exception as e:
        logger.error(f"[speech2text] Google Speech 失敗: {e}")
        return None


# ================== URL 下載 ==================

def _download_url(url: str) -> str:
    """下載音頻 URL 到暫存檔"""
    import urllib.request
    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".audio")
    try:
        urllib.request.urlretrieve(url, tmp.name)
        return tmp.name
    except Exception as e:
        os.unlink(tmp.name)
        raise RuntimeError(f"下載失敗: {e}")


# ================== 主入口 ==================

def handle_speech2text(args: str, chat_id: str = "", agent_config: dict = None) -> str:
    """
    語音識別主函數
    參數格式: file_path=<path> | url=<url> | 直接給路徑/URL
    可選: model=tiny|base|small|medium, language=zh|en|ja|...
    """
    if agent_config is None:
        agent_config = {}

    # --- 解析參數 ---
    file_path = None
    url = None
    model_name = agent_config.get("STT_MODEL", "tiny")
    language = agent_config.get("STT_LANGUAGE", None)

    s = args.strip() if args else ""

    # 嘗試 JSON 格式
    if s.startswith("{"):
        try:
            p = json.loads(s)
            file_path = p.get("file_path", None)
            url = p.get("url", None)
            model_name = p.get("model", model_name)
            language = p.get("language", language)
        except json.JSONDecodeError:
            file_path = s
    elif s.startswith("http://") or s.startswith("https://"):
        url = s
    elif s:
        file_path = s

    if not file_path and not url:
        return json.dumps({
            "success": False,
            "error": "請提供 file_path 或 url。用法: /speech2text <路徑|URL> [語言]",
            "help": "支援格式: ogg, mp3, wav, flac, m4a, webm"
        }, ensure_ascii=False)

    # --- 下載 URL ---
    if url and not file_path:
        try:
            file_path = _download_url(url)
            logger.info(f"[speech2text] 已下載: {url} → {file_path}")
        except Exception as e:
            return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    # --- 檢查檔案 ---
    if not os.path.exists(file_path):
        return json.dumps({"success": False, "error": f"檔案不存在: {file_path}"}, ensure_ascii=False)

    # --- 依賴檢查 ---
    if not HAS_FASTER_WHISPER and not HAS_OPENAI_WHISPER and not HAS_SPEECHRECOG:
        return json.dumps({
            "success": False,
            "error": "沒有任何語音識別引擎可用！請安裝依賴:",
            "install_options": [
                "pip install faster-whisper  ← 推薦（輕量快速）",
                "pip install openai-whisper  ← 備用（較大）",
                "pip install SpeechRecognition pydub  ← 輕量線上"
            ]
        }, ensure_ascii=False)

    # --- 轉換音頻格式 ---
    original_path = file_path
    try:
        file_path = _to_wav(file_path)
    except RuntimeError as e:
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

    # --- 依序嘗試各引擎 ---
    result = None

    # 1. faster-whisper
    result = _transcribe_faster_whisper(file_path, model_name, language)
    if result:
        logger.info(f"[speech2text] ✅ faster-whisper 成功: {result['text'][:80]}...")
        return json.dumps({"success": True, **result}, ensure_ascii=False)

    # 2. openai-whisper
    result = _transcribe_openai_whisper(file_path, model_name, language)
    if result:
        logger.info(f"[speech2text] ✅ openai-whisper 成功: {result['text'][:80]}...")
        return json.dumps({"success": True, **result}, ensure_ascii=False)

    # 3. Google Speech（用原始檔案，讓 pydub 自己處理）
    result = _transcribe_google(original_path, language)
    if result:
        logger.info(f"[speech2text] ✅ Google Speech 成功: {result['text'][:80]}...")
        return json.dumps({"success": True, **result}, ensure_ascii=False)

    # --- 全部失敗 ---
    return json.dumps({
        "success": False,
        "error": "所有語音識別引擎皆失敗。請檢查音頻檔案是否有效，或嘗試安裝 faster-whisper。"
    }, ensure_ascii=False)


# ================== 直接測試 ==================
if __name__ == "__main__":
    import sys
    test_args = sys.argv[1] if len(sys.argv) > 1 else ""
    print(handle_speech2text(test_args))
