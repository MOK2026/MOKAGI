# -*- coding: utf-8 -*-
"""
comic.py v2 - 動態漫畫式短劇生成工具（Ken Burns 推拉鏡頭 + 多分鏡 + 配音字幕）

用法:
    /comic 主題                       -> 生成動態漫畫短劇 MP4（預設 mode=video）
    /comic 主題 mode=comic            -> 舊版 4格靜態拼圖 PNG
    /comic subject=武俠 style=水墨 panels=6 aspect=9:16 seconds=4 voice=1

管線:
    1. story_engine : LLM 寫短劇分鏡劇本（多場景/角色/台詞/旁白/鏡頭/秒數）
    2. image_engine : 每場景一張全幅精繪圖（Gemini 主，zhipu/comfyui 備援，含重試）
    3. video_engine : ffmpeg zoompan 每格 Ken Burns 推拉 + drawtext 字幕 + xfade 轉場
    4. audio_engine : edge-tts 多角色配音（旁白+台詞）-> adelay/amix 混音

輸出: ~/.mok/agent/影片女/design/comics/<標題>_<時間戳>.mp4
"""
import os
import re
import io
import json
import time
import base64
import asyncio
import shutil
import subprocess
import logging
from typing import Optional, Dict, List
from datetime import datetime

logger = logging.getLogger(__name__)

PLUGIN_INFO = {
    "command": "/comic",
    "icon": "🎬",
    "handler": "handle_comic",
    "description": "動態漫畫式短劇生成：LLM寫多分鏡劇本 + AI每場景精繪圖 + ffmpeg Ken Burns推拉鏡頭 + 多角色配音字幕合成MP4；mode=comic 可輸出舊版4格拼圖",
    "intent_keywords": [
        ("漫畫", "/comic"),
        ("短劇", "/comic"),
        ("動態漫畫", "/comic"),
        ("分鏡", "/comic"),
        ("連環畫", "/comic"),
    ],
    "tool_schema": {
        "name": "comic_4panel",
        "description": "生成動態漫畫式短劇影片（預設）：LLM 寫多分鏡劇本、AI 每場景一張精繪圖、ffmpeg 做 Ken Burns 推拉鏡頭與轉場、可加多角色配音與字幕，輸出 9:16/16:9 MP4。也可 mode=comic 輸出舊版 2x2 四格漫畫圖。",
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "短劇主題或關鍵詞，例如：貓咪的報恩、雨夜竹林俠客"},
                "style": {"type": "string", "description": "畫風：日系Q版/水墨國風/美式卡通/寫實/賽璐璐（可自訂）", "default": "日系Q版"},
                "backend": {"type": "string", "description": "出圖後端：auto/gemini/zhipu/comfyui/placeholder", "default": "auto"},
                "stop_at": {"type": "string", "description": "生成到哪一步：script（只寫分鏡劇本）/image（含每幕精繪圖）/video（完整影片）", "default": "video"},
                "panels": {"type": "integer", "description": "分鏡幕數（2~10，預設6），越多越有短劇感", "default": 6},
                "aspect": {"type": "string", "description": "畫面比例：9:16（直式短劇）/16:9（橫式）", "default": "9:16"},
                "seconds": {"type": "number", "description": "每幕秒數（2~8，預設4）", "default": 4},
                "voice": {"type": "string", "description": "是否多角色配音：1/0", "default": "1"},
                "mode": {"type": "string", "description": "video（動態短劇，預設）/comic（舊版4格拼圖）", "default": "video"}
            },
            "required": ["subject"]
        }
    },
}

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/wqy/wqy-zenhei.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc",
    "/usr/share/fonts/truetype/droid/DroidSansFallbackFull.ttf",
]

OUTPUT_DIR = os.path.expanduser("~/.mok/agent/影片女/design/comics")

STYLE_EN_MAP = {
    "日系Q版": "Japanese anime style, beautiful detailed illustration, vibrant",
    "水墨國風": "Chinese ink wash painting style, elegant, cinematic",
    "美式卡通": "American cartoon style, bold outlines, expressive",
    "寫實": "photorealistic, detailed, cinematic lighting",
    "賽璐璐": "cel-shaded anime style, clean lines, cinematic",
}

CAMERA_EN_MAP = {
    "推": "push", "拉": "pull", "左移": "panleft", "右移": "panright",
    "上移": "panup", "下移": "pandown", "靜止": "static", "特寫": "push",
}

def _parse_args(args) -> Dict:
    if isinstance(args, dict):
        return args
    s = str(args).strip()
    if not s:
        return {}
    kv = {}
    rest = []
    for token in re.split(r"\s+", s):
        if "=" in token:
            k, _, v = token.partition("=")
            kv[k.strip()] = v.strip()
        else:
            rest.append(token)
    if "subject" not in kv and rest:
        kv["subject"] = " ".join(rest)
    return kv


def _load_config(agent_config):
    if agent_config and isinstance(agent_config, dict):
        return agent_config
    cfg = {}
    try:
        mok_home = os.environ.get("MOKAGI_HOME", "mok")
        agent_name = os.environ.get("MOK_AGENT_NAME") or "影片女"
        cfg_path = os.path.expanduser(f"~/.{mok_home}/agent/{agent_name}/.{agent_name}")
        if os.path.isfile(cfg_path):
            with open(cfg_path, encoding="utf-8", errors="ignore") as f:
                for line in f:
                    line = line.strip()
                    if "=" in line and not line.startswith("#"):
                        k, _, v = line.partition("=")
                        cfg[k.strip()] = v.strip()
    except Exception as e:
        logger.warning(f"載入設定失敗: {e}")
    return cfg


def _get_llm_config(cfg):
    current = cfg.get("MOK_CURRENT_MODEL", "deepseek-v4-flash")
    if cfg.get("MOK_MODEL_NAME") == current:
        return current, cfg.get("MOK_MODEL_url", ""), cfg.get("MOK_MODEL_token", "")
    for key, val in cfg.items():
        if key.startswith("MOK_MODEL_NAME") and val == current:
            idx = key.replace("MOK_MODEL_NAME", "")
            return val, cfg.get(f"MOK_MODEL_url{idx}", ""), cfg.get(f"MOK_MODEL_token{idx}", "")
    return (cfg.get("MOK_MODEL_NAME6", "deepseek-v4-flash"),
            cfg.get("MOK_MODEL_url6", "https://api.deepseek.com"),
            cfg.get("MOK_MODEL_token6", ""))


def _find_font():
    for f in FONT_CANDIDATES:
        if os.path.isfile(f):
            return f
    return None

# ==================== 1. story_engine: LLM 寫短劇分鏡劇本 ====================
DRAMA_SYSTEM_PROMPT = """你是專業的短劇分鏡編劇。根據主題寫一部「動態漫畫短劇」劇本，只輸出 JSON（不要任何其他文字）。
JSON 格式：
{
  "title": "劇名(2-8字)",
  "genre": "類型",
  "style": "畫風",
  "characters": [{"name": "角色名", "role": "身份"}],
  "scenes": [
    {
      "no": 1,
      "scene": "場景地點(3-8字)",
      "action": "畫面動作描述",
      "dialogue": "台詞(<=18字，無則填空字串)",
      "narration": "旁白(<=16字，無則填空字串)",
      "speaker": "說台詞的角色名(旁白則填「旁白」)",
      "emotion": "情緒氛圍(如：冷峻/溫馨/緊張)",
      "camera": "鏡頭運動(推/拉/左移/右移/上移/下移/靜止)",
      "duration": 4
    }
  ]
}
要求：分鏡要有起承轉合與戲劇張力、台詞簡短有力、旁白有文學感、camera 多樣不要連續重複、每幕 duration 3~6 秒。"""


def _call_llm(cfg, user_prompt, system_prompt=None, timeout=120):
    model, url, token = _get_llm_config(cfg)
    if not token:
        raise RuntimeError(f"模型 {model} 未設定 token")
    import requests
    base = url.rstrip("/")
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    body = {"model": model, "messages": [{"role": "system", "content": system_prompt or ""},
                                          {"role": "user", "content": user_prompt}],
            "temperature": 0.8, "max_tokens": 3000}
    try:
        r = requests.post(f"{base}/chat/completions", headers=headers, json=body, timeout=timeout)
    except Exception as e:
        raise RuntimeError(f"LLM 連線失敗: {e}")
    if r.status_code != 200:
        raise RuntimeError(f"LLM {r.status_code}: {r.text[:300]}")
    data = r.json()
    try:
        return data["choices"][0]["message"]["content"]
    except Exception:
        raise RuntimeError(f"LLM 回傳格式錯誤: {str(data)[:300]}")


def _extract_json(text):
    text = str(text).strip()
    m = re.search(r"\{.*\}", text, re.S)
    if m:
        text = m.group(0)
    try:
        return json.loads(text)
    except Exception:
        text = re.sub(r"[\x00-\x1f]", " ", text)
        text = re.sub(r",\s*([}\]])", r"\1", text)
        try:
            return json.loads(text)
        except Exception as e:
            raise RuntimeError(f"JSON 解析失敗: {e} | 內容: {text[:300]}")


def _gen_drama_script(cfg, subject, style, panels=6):
    user_prompt = (f"主題：{subject}\n指定畫風：{style}\n分鏡數：{panels} 幕。\n"
                   "請寫出動態漫畫短劇劇本（嚴格輸出 JSON）。")
    content = _call_llm(cfg, user_prompt, DRAMA_SYSTEM_PROMPT)
    script = _extract_json(content)
    if "scenes" not in script or not script.get("scenes"):
        if "panels" in script:
            script["scenes"] = script["panels"]
        else:
            raise RuntimeError(f"劇本 JSON 格式不符: {content[:300]}")
    for i, sc in enumerate(script["scenes"]):
        sc.setdefault("no", i + 1)
        sc.setdefault("scene", "")
        sc.setdefault("action", "")
        sc.setdefault("dialogue", "")
        sc.setdefault("narration", "")
        sc.setdefault("speaker", "")
        sc.setdefault("emotion", "")
        sc.setdefault("camera", "推")
        try:
            sc["duration"] = max(2, min(int(sc.get("duration", 4)), 8))
        except Exception:
            sc["duration"] = 4
    return script

# ==================== 2. image_engine: 每場景一張精繪圖 ====================
def _build_image_prompt(script, scene, style_zh, aspect="9:16"):
    style_en = STYLE_EN_MAP.get(style_zh, "detailed cinematic illustration, high quality")
    chars = "; ".join(f"{c.get('name','')}({c.get('role','')})" for c in script.get("characters", []))
    parts = [
        f"{style_zh}畫風的電影感精繪插畫（{aspect} 構圖），單張完整場景，非漫畫分格。",
        f"角色設定：{chars}。",
        f"場景：{scene.get('scene','')}。",
        f"動作：{scene.get('action','')}。",
        f"情緒氛圍：{scene.get('emotion','')}。",
        "要求：高細節、電影打光、景深、精緻構圖；角色與其他幕同一造型、同一畫風、同一色調；畫面不要任何文字、浮水印、對話框、邊框。",
        f"{style_en}, single full illustration, cinematic lighting, high detail, consistent character design, no text, no watermark, no speech bubble, no panel border",
    ]
    return " ".join(parts)


def _gen_gemini(prompt, key, aspect="9:16", timeout=90, retries=2):
    import requests
    model = "gemini-2.5-flash-image"
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    ar = aspect if aspect in ("9:16", "16:9", "1:1") else "9:16"
    last = None
    for attempt in range(retries + 1):
        body = {"contents": [{"parts": [{"text": prompt}]}],
                "generationConfig": {"responseModalities": ["IMAGE"],
                                     "imageConfig": {"aspectRatio": ar}}}
        try:
            r = requests.post(url, headers={"x-goog-api-key": key, "Content-Type": "application/json"},
                              json=body, timeout=timeout)
            if r.status_code != 200:
                last = RuntimeError(f"Gemini {r.status_code}: {r.text[:200]}")
                time.sleep(3)
                continue
            data = r.json()
            img_b64 = None
            for part in data.get("candidates", [{}])[0].get("content", {}).get("parts", []):
                if "inlineData" in part and part["inlineData"].get("data"):
                    img_b64 = part["inlineData"]["data"]
                    break
            if not img_b64:
                last = RuntimeError("Gemini 回傳 200 但無圖片內容")
                time.sleep(3)
                continue
            return base64.b64decode(img_b64)
        except Exception as e:
            last = e
            time.sleep(3)
    raise last or RuntimeError("Gemini 生成失敗")


def _gen_zhipu(prompt, key, timeout=120):
    import requests
    url = "https://open.bigmodel.cn/api/paas/v4/images/generations"
    body = {"model": "cogview-3-flash", "prompt": prompt, "size": "1024x1024"}
    r = requests.post(url, headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                      json=body, timeout=timeout)
    if r.status_code != 200:
        raise RuntimeError(f"智譜 {r.status_code}: {r.text[:200]}")
    data = r.json()
    img_url = data.get("data", [{}])[0].get("url", "")
    if not img_url:
        raise RuntimeError(f"智譜未回傳圖片URL: {data}")
    ir = requests.get(img_url, timeout=60)
    if ir.status_code != 200:
        raise RuntimeError(f"下載智譜圖片失敗 {ir.status_code}")
    return ir.content


def _gen_comfyui(prompt, timeout=300):
    import requests
    base = "http://127.0.0.1:8188"
    try:
        objs = requests.get(f"{base}/object_info", timeout=5).json()
    except Exception as e:
        raise RuntimeError(f"ComfyUI 未啟動: {e}")
    ckpt_list = objs.get("CheckpointLoaderSimple", {}).get("input", {}).get("required", {}).get("ckpt_name", [[]])[0] or []
    if not ckpt_list:
        raise RuntimeError("ComfyUI 沒有可用的 checkpoint")
    ckpt = ckpt_list[0]
    wf = {
        "3": {"class_type": "KSampler", "inputs": {"seed": int(time.time() * 1000) % 999999, "steps": 20, "cfg": 7.0, "sampler_name": "euler", "scheduler": "normal", "denoise": 1.0, "model": ["4", 0], "positive": ["6", 0], "negative": ["7", 0], "latent_image": ["5", 0]}},
        "4": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": ckpt}},
        "5": {"class_type": "EmptyLatentImage", "inputs": {"width": 768, "height": 768, "batch_size": 1}},
        "6": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["4", 1]}},
        "7": {"class_type": "CLIPTextEncode", "inputs": {"text": "text, watermark, low quality, blurry", "clip": ["4", 1]}},
        "8": {"class_type": "VAEDecode", "inputs": {"samples": ["3", 0], "vae": ["4", 2]}},
        "9": {"class_type": "SaveImage", "inputs": {"filename_prefix": "comic", "images": ["8", 0]}},
    }
    pid = requests.post(f"{base}/prompt", json={"prompt": wf}, timeout=30).json()
    if "prompt_id" not in pid:
        raise RuntimeError(f"ComfyUI 提交失敗: {pid}")
    pid = pid["prompt_id"]
    for _ in range(60):
        time.sleep(2)
        hist = requests.get(f"{base}/history/{pid}", timeout=10).json()
        if pid in hist:
            outputs = hist[pid].get("outputs", {})
            for node in outputs.values():
                for img in node.get("images", []):
                    fn = img.get("filename")
                    sub = img.get("subfolder", "")
                    p = requests.get(f"{base}/view", params={"filename": fn, "subfolder": sub}, timeout=30)
                    if p.status_code == 200:
                        return p.content
            break
    raise RuntimeError("ComfyUI 出圖逾時")


def _cover_resize(img, W, H):
    from PIL import Image
    img = img.convert("RGB")
    r = max(W / img.width, H / img.height)
    nw, nh = max(int(img.width * r), 1), max(int(img.height * r), 1)
    img = img.resize((nw, nh), Image.LANCZOS)
    x = (nw - W) // 2
    y = (nh - H) // 2
    return img.crop((x, y, x + W, y + H))

# ==================== 3. video_engine: Ken Burns 推拉鏡頭 ====================
def _esc_drawtext(t):
    t = str(t)
    t = t.replace("\\", "\\\\").replace("'", "\\'").replace(":", "\\:")
    t = t.replace(",", "\\,").replace("%", "\\%").replace("[", "\\[").replace("]", "\\]")
    return t


def _cam_expr(cam, Dm1):
    c = f"on/{Dm1}"
    if cam == "push":
        return f"1+0.25*{c}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if cam == "pull":
        return f"1.25-0.25*{c}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    if cam == "panleft":
        return "1.15", f"(iw-iw/zoom)*(1-{c})", "ih/2-(ih/zoom/2)"
    if cam == "panright":
        return "1.15", f"(iw-iw/zoom)*{c}", "ih/2-(ih/zoom/2)"
    if cam == "panup":
        return "1.15", "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*(1-{c})"
    if cam == "pandown":
        return "1.15", "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*{c}"
    return f"1+0.06*{c}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"


def _render_clip(img_path, scene, idx, cam, W, H, fps, seconds, out_mp4, font_path, title=None, is_first=False):
    D = max(int(seconds * fps), fps)
    Dm1 = max(D - 1, 1)
    cam = CAMERA_EN_MAP.get(cam, "push")
    zexpr, xexpr, yexpr = _cam_expr(cam, Dm1)
    up_w, up_h = W * 3, H * 3
    vf = (f"scale={up_w}:{up_h}:force_original_aspect_ratio=increase,"
          f"crop={up_w}:{up_h},"
          f"zoompan=z='{zexpr}':x='{xexpr}':y='{yexpr}':d={D}:s={W}x{H}:fps={fps}")
    dialogue = scene.get("dialogue", "")
    speaker = scene.get("speaker", "")
    sub_text = f"{speaker}：{dialogue}" if speaker and dialogue else dialogue
    if sub_text:
        fs = max(28, int(H * 0.052))
        y_pos = int(H * 0.86)
        vf += (f",drawtext=fontfile={font_path}:text='{_esc_drawtext(sub_text)}'"
               f":x=(w-text_w)/2:y={y_pos}:fontsize={fs}:fontcolor=white"
               f":borderw={max(2, int(H * 0.004))}:bordercolor=black@0.85:shadowx=2:shadowy=2")
    scene_name = scene.get("scene", "")
    if scene_name:
        vf += (f",drawtext=fontfile={font_path}:text='{_esc_drawtext(scene_name)}'"
               f":x=26:y=26:fontsize={max(20, int(H * 0.032))}:fontcolor=white@0.9"
               f":borderw=2:bordercolor=black@0.7")
    if is_first and title:
        vf += (f",drawtext=fontfile={font_path}:text='{_esc_drawtext(title)}'"
               f":x=(w-text_w)/2:y={int(H * 0.28)}:fontsize={int(H * 0.075)}:fontcolor=white"
               f":borderw=5:bordercolor=black@0.9:shadowx=3:shadowy=3:enable='between(t,0,2.2)'")
    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", img_path, "-vf", vf,
           "-frames:v", str(D), "-r", str(fps), "-c:v", "libx264",
           "-preset", "medium", "-pix_fmt", "yuv420p", "-an", out_mp4]
    subprocess.run(cmd, check=True, capture_output=True, timeout=180)


def _xfade_concat(clip_paths, durations, trans_dur, out_mp4, fps=25):
    n = len(clip_paths)
    if n == 1:
        shutil.copy(clip_paths[0], out_mp4)
        return
    inputs = []
    for p in clip_paths:
        inputs += ["-i", p]
    fc = [f"[{i}:v]null[v{i}]" for i in range(n)]
    trans = ["fade", "wipeleft", "fade", "slideleft", "fade", "circleopen", "fade", "smoothup"]
    last = "[v0]"
    for i in range(1, n):
        outl = f"[vx{i}]" if i < n - 1 else "[vout]"
        off = sum(durations[:i]) - i * trans_dur
        t = trans[i % len(trans)]
        fc.append(f"{last}[v{i}]xfade=transition={t}:duration={trans_dur}:offset={off:.2f}{outl}")
        last = outl
    cmd = ["ffmpeg", "-y"] + inputs + ["-filter_complex", ";".join(fc),
           "-map", "[vout]", "-c:v", "libx264", "-preset", "medium",
           "-crf", "20", "-pix_fmt", "yuv420p", "-r", str(fps), out_mp4]
    subprocess.run(cmd, check=True, capture_output=True, timeout=300)

# ==================== 4. audio_engine: edge-tts 多角色配音 ====================
def _tts_sync(text, voice, out_path):
    import threading
    result = {}
    def _worker():
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        try:
            import edge_tts
            c = edge_tts.Communicate(text, voice)
            loop.run_until_complete(c.save(out_path))
            result['ok'] = True
        except Exception as e:
            result['err'] = str(e)
        finally:
            loop.close()
    t = threading.Thread(target=_worker, daemon=True)
    t.start()
    t.join(timeout=60)
    if result.get('err'):
        raise RuntimeError(result['err'])
    if not result.get('ok'):
        raise RuntimeError('edge-tts 逾時')


def _voice_for_speaker(name, role="", emotion=""):
    s = f"{name}{role}{emotion}"
    if "旁白" in s or "說書" in s or "敘述" in s:
        return "zh-CN-YunyangNeural"
    if any(k in s for k in ["小女孩", "少女", "妹妹", "小妹妹", "女兒", "萝莉", "蘿莉"]):
        return "zh-CN-XiaoyiNeural"
    if any(k in s for k in ["男", "哥哥", "少年", "父親", "老爺", "師傅", "老", "俠客", "劍客"]):
        return "zh-CN-YunxiNeural"
    return "zh-CN-XiaoxiaoNeural"


def _probe_dur(path):
    try:
        r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                            "-of", "default=nw=1:nk=1", path], capture_output=True, text=True, timeout=20)
        return float(r.stdout.strip())
    except Exception:
        return 3.0


def _add_audio(video_mp4, audio_items, out_mp4):
    if not audio_items:
        shutil.copy(video_mp4, out_mp4)
        return
    inputs = ["-i", video_mp4]
    fc = []
    labels = []
    for i, (apath, start_ms) in enumerate(audio_items):
        inputs += ["-i", apath]
        lab = f"[a{i}]"
        fc.append(f"[{i + 1}:a]adelay={int(start_ms)}|{int(start_ms)}{lab}")
        labels.append(lab)
    mix = "".join(labels)
    fc.append(f"{mix}amix=inputs={len(labels)}:duration=longest:normalize=0:dropout_transition=0[outa]")
    vdur = _probe_dur(video_mp4)
    cmd = ["ffmpeg", "-y"] + inputs + ["-filter_complex", ";".join(fc),
           "-map", "0:v", "-map", "[outa]", "-c:v", "copy",
           "-c:a", "aac", "-b:a", "192k", "-t", f"{vdur:.3f}", out_mp4]
    subprocess.run(cmd, check=True, capture_output=True, timeout=300)

# ==================== 主處理 ====================
async def handle_comic(args, user_id: str = None, agent_config: Optional[Dict] = None) -> str:
    try:
        p = _parse_args(args)
        subject = (p.get("subject") or "").strip()
        if not subject:
            return json.dumps({"success": False, "error": "請提供主題，例如：/comic 貓咪偷吃魚"}, ensure_ascii=False)
        style = p.get("style", "日系Q版")
        backend = p.get("backend", "auto")
        stop_at = p.get("stop_at", "video")
        mode = p.get("mode", "video")
        aspect = p.get("aspect", "9:16")
        fps = 25
        try:
            panels = max(2, min(int(p.get("panels", 6)), 10))
        except Exception:
            panels = 6
        try:
            seconds = max(2, min(float(p.get("seconds", 4)), 8))
        except Exception:
            seconds = 4
        voice = str(p.get("voice", "1")).lower() not in ("0", "false", "no", "off")
        if backend not in ("auto", "gemini", "zhipu", "comfyui", "placeholder"):
            backend = "auto"
        if stop_at not in ("script", "image", "video"):
            stop_at = "video"
        if aspect not in ("9:16", "16:9", "1:1"):
            aspect = "9:16"
        if mode not in ("video", "comic"):
            mode = "video"
        if mode == "comic":
            return await _handle_comic_static(p, subject, style, backend, stop_at, agent_config)
        cfg = _load_config(agent_config)
        font_path = _find_font()
        if not font_path:
            return json.dumps({"success": False, "error": "找不到中文字型"}, ensure_ascii=False)
        if aspect == "9:16":
            W, H = 720, 1280
        elif aspect == "16:9":
            W, H = 1280, 720
        else:
            W = H = 720
        trans_dur = 0.5
        os.makedirs(OUTPUT_DIR, exist_ok=True)
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        work_dir = os.path.join(OUTPUT_DIR, f"_work_{ts}")
        os.makedirs(work_dir, exist_ok=True)
        safe_title = re.sub(r'[\\/:*?"<>|]', "_", subject)[:30]
        out_path = os.path.join(OUTPUT_DIR, f"{safe_title}_{ts}.mp4")
        script_json_path = os.path.join(OUTPUT_DIR, f"{safe_title}_{ts}_script.json")

        # 1. LLM 寫短劇分鏡劇本
        script = _gen_drama_script(cfg, subject, style, panels)
        with open(script_json_path, "w", encoding="utf-8") as f:
            json.dump(script, f, ensure_ascii=False, indent=2)
        title = script.get("title", subject)
        scenes = script["scenes"]
        if stop_at == "script":
            return json.dumps({"success": True, "stage": "script", "title": title,
                               "script": script, "script_path": script_json_path}, ensure_ascii=False)

        # 2. 每場景出圖（Gemini 主，備援鏈，含重試）
        gemini_key = cfg.get("MOK_Gemini", "") or os.environ.get("MOK_Gemini", "")
        zhipu_key = cfg.get("ZHIPU_API_KEY", "") or os.environ.get("ZHIPU_API_KEY", "")
        from PIL import Image as PILImage
        panel_paths = []
        used_backend = backend
        img_log = []
        for i, sc in enumerate(scenes):
            prompt = _build_image_prompt(script, sc, style, aspect)
            img = None
            last_err = None
            if backend == "auto":
                order = [("gemini", gemini_key), ("zhipu", zhipu_key), ("comfyui", None), ("placeholder", None)]
            elif backend == "gemini":
                order = [("gemini", gemini_key), ("placeholder", None)]
            elif backend == "zhipu":
                order = [("zhipu", zhipu_key), ("placeholder", None)]
            elif backend == "comfyui":
                order = [("comfyui", None), ("placeholder", None)]
            else:
                order = [("placeholder", None)]
            for bname, key in order:
                try:
                    if bname == "gemini":
                        if not key:
                            raise RuntimeError("未設定 MOK_Gemini")
                        img = _gen_gemini(prompt, key, aspect)
                    elif bname == "zhipu":
                        if not key:
                            raise RuntimeError("未設定 ZHIPU_API_KEY")
                        img = _gen_zhipu(prompt, key)
                    elif bname == "comfyui":
                        img = _gen_comfyui(prompt)
                    else:
                        img = _make_placeholder(prompt, i + 1)
                    used_backend = bname
                    break
                except Exception as e:
                    last_err = str(e)
                    time.sleep(1)
                    continue
            if img is None:
                raise RuntimeError(f"第{i + 1}幕出圖全部失敗: {last_err}")
            if isinstance(img, bytes):
                img = PILImage.open(io.BytesIO(img))
            img = _cover_resize(img, W, H)
            pp = os.path.join(work_dir, f"panel_{i + 1:02d}.png")
            img.save(pp, "PNG")
            panel_paths.append(pp)
            img_log.append({"no": i + 1, "backend": used_backend})
            logger.info(f"第{i + 1}幕出圖完成: {used_backend}")
        if stop_at == "image":
            return json.dumps({"success": True, "stage": "image", "backend": used_backend,
                               "panels": len(panel_paths), "images": panel_paths,
                               "script_path": script_json_path}, ensure_ascii=False)

        # 3. 每幕渲染 Ken Burns 推拉鏡頭影片（含字幕）
        clip_paths = []
        durations = []
        for i, sc in enumerate(scenes):
            dur = float(sc.get("duration", seconds))
            durations.append(dur)
            cp = os.path.join(work_dir, f"clip_{i + 1:02d}.mp4")
            _render_clip(panel_paths[i], sc, i, sc.get("camera", "推"), W, H, fps, dur, cp, font_path, title, is_first=(i == 0))
            clip_paths.append(cp)

        # 4. xfade 轉場串接
        silent_path = os.path.join(work_dir, "silent.mp4")
        _xfade_concat(clip_paths, durations, trans_dur, silent_path, fps)

        # 5. edge-tts 多角色配音
        audio_items = []
        audio_err = ""
        if voice:
            try:
                import edge_tts  # noqa
                total_before = 0.0
                for i, sc in enumerate(scenes):
                    start_ms = int((sum(durations[:i]) - i * trans_dur) * 1000)
                    speaker = sc.get("speaker", "")
                    vname = _voice_for_speaker(speaker, emotion=sc.get("emotion", ""))
                    nar = sc.get("narration", "").strip()
                    dia = sc.get("dialogue", "").strip()
                    if nar:
                        np_ = os.path.join(work_dir, f"nar_{i + 1:02d}.mp3")
                        _tts_sync(nar, "zh-CN-YunyangNeural", np_)
                        nd = _probe_dur(np_)
                        audio_items.append((np_, start_ms))
                        start_ms += int(nd * 1000)
                    if dia:
                        dp_ = os.path.join(work_dir, f"dia_{i + 1:02d}.mp3")
                        _tts_sync(dia, vname, dp_)
                        audio_items.append((dp_, start_ms))
            except Exception as e:
                audio_err = str(e)
                logger.warning(f"配音失敗，輸出無聲版: {e}")

        # 6. 合成最終影片
        if audio_items and not audio_err:
            _add_audio(silent_path, audio_items, out_path)
        else:
            shutil.copy(silent_path, out_path)

        # 清理暫存（保留面板圖與劇本）
        try:
            for f in os.listdir(work_dir):
                if f.endswith((".mp4", ".mp3")):
                    os.remove(os.path.join(work_dir, f))
        except Exception:
            pass

        return json.dumps({
            "success": True,
            "stage": "video",
            "title": title,
            "mode": "video",
            "backend": used_backend,
            "panels": len(scenes),
            "aspect": aspect,
            "video_path": out_path,
            "script_path": script_json_path,
            "audio": ("配音完成" if (audio_items and not audio_err) else f"無配音（{audio_err or 'voice=0'}）"),
            "提示": "動態漫畫短劇：每幕精繪圖 + Ken Burns推拉鏡頭 + 轉場 + 字幕。"
        }, ensure_ascii=False)

    except Exception as e:
        logger.exception("comic 生成失敗")
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)

def _make_placeholder(prompt, panel_no):
    from PIL import Image, ImageDraw, ImageFont
    w = h = 640
    img = Image.new("RGB", (w, h), (250, 248, 243))
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, w - 1, h - 1], outline=(80, 80, 80), width=3)
    font = _find_font()
    f_big = ImageFont.truetype(font, 36) if font else ImageFont.load_default()
    f_small = ImageFont.truetype(font, 20) if font else ImageFont.load_default()
    d.rectangle([20, 20, w - 20, h - 20], outline=(200, 195, 185), width=2)
    d.text((w // 2, h // 2 - 70), f"第 {panel_no} 幕", font=f_big, fill=(60, 60, 60), anchor="mm")
    summary = prompt[:90]
    y = h // 2
    for i in range(0, len(summary), 15):
        d.text((w // 2, y), summary[i:i + 15], font=f_small, fill=(120, 120, 120), anchor="mm")
        y += 26
    d.text((w // 2, h - 30), "⚠ 佔位圖（出圖後端失敗）", font=f_small, fill=(180, 60, 60), anchor="mm")
    return img

async def _handle_comic_static(p, subject, style, backend, stop_at, agent_config):
    cfg = _load_config(agent_config)
    script = _gen_drama_script(cfg, subject, style, 4)
    scenes = script["scenes"][:4]
    gemini_key = cfg.get("MOK_Gemini", "") or os.environ.get("MOK_Gemini", "")
    zhipu_key = cfg.get("ZHIPU_API_KEY", "") or os.environ.get("ZHIPU_API_KEY", "")
    from PIL import Image as PILImage
    panel_imgs = []
    used_backend = backend
    for i, sc in enumerate(scenes):
        prompt = _build_image_prompt(script, sc, style, "1:1")
        img = None
        last_err = None
        if backend == "auto":
            order = [("gemini", gemini_key), ("zhipu", zhipu_key), ("comfyui", None), ("placeholder", None)]
        elif backend == "gemini":
            order = [("gemini", gemini_key), ("placeholder", None)]
        elif backend == "zhipu":
            order = [("zhipu", zhipu_key), ("placeholder", None)]
        elif backend == "comfyui":
            order = [("comfyui", None), ("placeholder", None)]
        else:
            order = [("placeholder", None)]
        for bname, key in order:
            try:
                if bname == "gemini":
                    if not key:
                        raise RuntimeError("未設定 MOK_Gemini")
                    img = _gen_gemini(prompt, key, "1:1")
                elif bname == "zhipu":
                    if not key:
                        raise RuntimeError("未設定 ZHIPU_API_KEY")
                    img = _gen_zhipu(prompt, key)
                elif bname == "comfyui":
                    img = _gen_comfyui(prompt)
                else:
                    img = _make_placeholder(prompt, i + 1)
                used_backend = bname
                break
            except Exception as e:
                last_err = str(e)
                continue
        if img is None:
            raise RuntimeError(f"第{i + 1}格出圖失敗: {last_err}")
        if isinstance(img, bytes):
            img = PILImage.open(io.BytesIO(img))
        panel_imgs.append(img)
    if stop_at == "image":
        return json.dumps({"success": True, "stage": "image", "backend": used_backend,
                           "panels": len(panel_imgs)}, ensure_ascii=False)
    os.makedirs(OUTPUT_DIR, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = re.sub(r'[\\/:*?"<>|]', "_", script.get("title", subject))[:30]
    out_path = os.path.join(OUTPUT_DIR, f"{safe_title}_{ts}.png")
    _stitch_static(panel_imgs, script, out_path)
    return json.dumps({"success": True, "stage": "video", "title": script.get("title", ""),
                       "backend": used_backend, "image_path": out_path}, ensure_ascii=False)

def _stitch_static(panel_imgs, script, output_path):
    from PIL import Image, ImageDraw, ImageFont
    font_path = _find_font()
    if not font_path:
        raise RuntimeError("找不到中文字型")
    f_title = ImageFont.truetype(font_path, 34)
    f_sub = ImageFont.truetype(font_path, 20)
    f_dialog = ImageFont.truetype(font_path, 26)
    margin = 24
    cell_w = cell_h = 640
    dialog_h = 96
    title_h = 110
    gap = 16
    canvas_w = margin * 2 + cell_w * 2 + gap
    canvas_h = margin * 2 + title_h + (cell_h + dialog_h) * 2 + gap
    canvas = Image.new("RGB", (canvas_w, canvas_h), (255, 255, 255))
    d = ImageDraw.Draw(canvas)
    title = script.get("title", "短劇漫畫")
    genre = script.get("genre", "")
    style = script.get("style", "")
    d.text((canvas_w // 2, margin + 22), title, font=f_title, fill=(30, 30, 30), anchor="mm")
    d.text((canvas_w // 2, margin + 62), f"{genre} · {style}", font=f_sub, fill=(120, 120, 120), anchor="mm")
    scenes = script.get("scenes", [])
    for i, img in enumerate(panel_imgs[:4]):
        img = img.resize((cell_w, cell_h), Image.LANCZOS)
        col = i % 2
        row = i // 2
        x = margin + col * (cell_w + gap)
        y = margin + title_h + row * (cell_h + dialog_h + gap)
        canvas.paste(img, (x, y))
        sc = scenes[i] if i < len(scenes) else {}
        dia = sc.get("dialogue", "")
        d.text((x + cell_w // 2, y + cell_h + dialog_h // 2 - 8), dia,
               font=f_dialog, fill=(40, 40, 40), anchor="mm")
    canvas.save(output_path, "PNG")
