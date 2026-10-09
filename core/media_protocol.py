# -*- coding: utf-8 -*-
"""
media_protocol.py - MOKAGI L3 結構化媒體協議（跨前端共用）

目的：讓「工具回傳 / 助手回覆」能以結構化方式攜帶媒體（圖 / 影 / 音），
      由各前端（Web main.js、Telegram mok_tg.py、未來 App）自行渲染，
      不必把 HTML 塞進 Markdown，亦避免 XSS。

協議（每個媒體項為 dict）：
    {"type": "image" | "video" | "audio",
     "url": "/static/..." 或 "https://...",
     "alt": "可選說明",
     "poster": "可選；影片封面圖 url"}

工具可直接回傳（dict 或 JSON 字串）：
    {"media": [ {...}, {...} ]}
或採隱含欄位（url / image / images / video / audio / path / src ...），
本模組的 extract_media() 會自動辨識、正規化、去重並做白名單過濾。

indexPage|L3結構化媒體|建立媒體協議與 extract_media 抽取器|20261008032605(HK)
"""

from __future__ import annotations

import json
import re
from typing import Any, Dict, List, Optional

MEDIA_TYPES = ("image", "video", "audio")

_EXT2TYPE: Dict[str, str] = {
    ".png": "image", ".jpg": "image", ".jpeg": "image", ".gif": "image",
    ".webp": "image", ".bmp": "image", ".svg": "image", ".avif": "image",
    ".mp4": "video", ".webm": "video", ".mov": "video", ".m4v": "video", ".mkv": "video",
    ".mp3": "audio", ".wav": "audio", ".ogg": "audio", ".m4a": "audio",
    ".aac": "audio", ".opus": "audio", ".flac": "audio",
}

# 站內相對路徑白名單前綴：只允許這些根，避免路徑穿越 / 任意檔案外洩
_ALLOWED_PREFIXES = (
    "/static/", "/media/", "/report/", "/files/", "/uploads/", "/tts/",
)

_URL_RE = re.compile(
    r"""(?:https?://[^\s"'<>()\[\]]+|/(?:static|media|report|files|uploads|tts)/[^\s"'<>()\[\]]+)""",
    re.IGNORECASE,
)

# 結構化 dict 中，代表「媒體網址」的鍵（前者優先）
_URL_KEYS = ("url", "image_url", "video_url", "audio_url", "image", "img",
             "video", "audio", "src", "path", "file", "output", "output_path")
# 值為「網址清單」的鍵
_LIST_KEYS = ("images", "videos", "audios", "urls", "media_urls", "items")
# 型別提示鍵
_TYPE_KEYS = ("type", "kind", "media_type", "mediatype", "content_type")


def _clean_url(u: Any) -> Optional[str]:
    """正規化並白名單過濾 URL；不合法者回 None。"""
    if not isinstance(u, str):
        return None
    u = u.strip().strip('"\'').replace("&amp;", "&")
    if not u:
        return None
    low = u.lower()
    if low.startswith("http://") or low.startswith("https://"):
        return u
    if u.startswith("//"):
        return "https:" + u
    if u.startswith("/"):
        for p in _ALLOWED_PREFIXES:
            if u.startswith(p):
                return u
    return None


def _has_media_ext(url: str) -> bool:
    path = url.split("?", 1)[0].split("#", 1)[0].lower()
    return any(path.endswith(ext) for ext in _EXT2TYPE)


def _guess_type(url: str, hint: Any = None) -> str:
    if isinstance(hint, str) and hint in MEDIA_TYPES:
        return hint
    path = url.split("?", 1)[0].split("#", 1)[0].lower()
    for ext, t in _EXT2TYPE.items():
        if path.endswith(ext):
            return t
    return "image"


def _make(url: str, type_: Any = None, alt: Any = None, poster: Any = None) -> Dict[str, Any]:
    item: Dict[str, Any] = {"type": _guess_type(url, type_), "url": url}
    if isinstance(alt, str) and alt.strip():
        item["alt"] = alt.strip()[:300]
    p = _clean_url(poster)
    if p:
        item["poster"] = p
    return item


def _from_dict(d: Dict[str, Any]) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []

    # 1) 明確協議：{"media": [...]} 或 {"media": {...}}
    if "media" in d:
        out.extend(_from_obj(d.get("media")))

    hint = None
    for k in _TYPE_KEYS:
        if isinstance(d.get(k), str):
            hint = d.get(k)
            break
    poster = d.get("poster") or d.get("thumbnail") or d.get("cover")

    # 2) 單一媒體網址欄位
    for k in _URL_KEYS:
        v = d.get(k)
        if isinstance(v, str):
            url = _clean_url(v)
            if url and (k not in ("path", "file", "output", "output_path", "src") or _has_media_ext(url)):
                out.append(_make(url, hint, d.get("alt") or d.get("caption") or d.get("title"), poster))
        elif isinstance(v, list):
            for one in v:
                url = _clean_url(one)
                if url:
                    out.append(_make(url, hint, None, poster))

    # 3) 網址清單欄位
    for k in _LIST_KEYS:
        v = d.get(k)
        if isinstance(v, list):
            out.extend(_from_obj(v))

    return out


def _from_obj(obj: Any) -> List[Dict[str, Any]]:
    if obj is None:
        return []
    if isinstance(obj, dict):
        return _from_dict(obj)
    if isinstance(obj, (list, tuple)):
        out: List[Dict[str, Any]] = []
        for x in obj:
            out.extend(_from_obj(x))
        return out
    if isinstance(obj, str):
        return _from_text(obj)
    return []


def _from_text(text: str) -> List[Dict[str, Any]]:
    """純文字：只抓「有明確媒體副檔名」的 URL，避免誤抓一般網頁連結。"""
    if not text or not isinstance(text, str):
        return []
    out: List[Dict[str, Any]] = []
    for m in _URL_RE.finditer(text):
        raw = m.group(0)
        url = _clean_url(raw)
        if url and _has_media_ext(url):
            out.append(_make(url, None, None, None))
    return out


def _from_any(src: Any) -> List[Dict[str, Any]]:
    """來源可為 dict / list / JSON 字串 / 純文字。"""
    if isinstance(src, str):
        s = src.strip()
        if s[:1] in ("{", "["):
            try:
                parsed = json.loads(s)
                got = _from_obj(parsed)
                if got:
                    return got
            except Exception:
                pass
        return _from_text(src)
    return _from_obj(src)


def extract_media(*sources: Any) -> List[Dict[str, Any]]:
    """從多個來源（工具原始結果、自然化文字…）抽取正規化後的媒體清單（已去重）。"""
    out: List[Dict[str, Any]] = []
    seen = set()
    for s in sources:
        try:
            for it in _from_any(s):
                u = it.get("url")
                if u and u not in seen:
                    seen.add(u)
                    out.append(it)
        except Exception:
            continue
    return out


def normalize_item(raw: Any) -> Optional[Dict[str, Any]]:
    """把單一 dict 正規化為協議格式；失敗回 None。"""
    if not isinstance(raw, dict):
        return None
    url = _clean_url(raw.get("url"))
    if not url:
        return None
    return _make(url, raw.get("type"), raw.get("alt"), raw.get("poster"))


if __name__ == "__main__":
    import sys
    demo = sys.argv[1:] if len(sys.argv) > 1 else [
        '{"success": true, "mode": "web", "url": "/static/tts/demo.mp3"}',
        {"media": [{"type": "image", "url": "/static/media/202610/a.png", "alt": "圖"}]},
        "看圖 https://x.com/a.jpg 與說明文字 https://example.com/page 不算",
    ]
    print(json.dumps(extract_media(*demo), ensure_ascii=False, indent=2))
