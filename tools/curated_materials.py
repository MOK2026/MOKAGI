#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""curated_materials.py — 「逐句拆 beat + CLIP 視覺閘門選材」引擎。

供 money.py 的 video_source=curated 使用，以獨立行程執行，
避免把 CLIP/torch 載入主服務行程。

輸入（argv[1]=JSON 檔路徑；否則讀 stdin）：
{
  "beats": [{"caption": "<EN 視覺描述>", "queries": ["...", "..."]}, ...],
  "positive": "<EN 正確畫面總描述>",
  "negatives": ["<EN 錯誤/垃圾畫面描述>", ...],
  "orient": "portrait" | "landscape",
  "dest": "/abs/path/storage/local_videos",
  "prefix": "curated",
  "workdir": "/abs/path/work",
  "config": "/abs/path/config.toml",
  "per_query": 20,
  "min_gate": 0.0
}

輸出（stdout 最後一行）：{"success": true, "materials": ["curated_00.mp4", ...], "picks": [...]}
進度訊息一律走 stderr。
"""
from __future__ import annotations

import json
import os
import re
import sys
import time

import requests
import subprocess

CLIP_MODEL = "openai/clip-vit-base-patch32"
TIMEOUT = 40
UA = {"User-Agent": "Mozilla/5.0 (curated-materials)"}


# --------------------------------------------------------------------------- #
# 工具
# --------------------------------------------------------------------------- #
def log(*a):
    print(*a, file=sys.stderr, flush=True)


def _arr(txt: str, name: str):
    m = re.search(name + r"\s*=\s*\[([^\]]*)\]", txt)
    if not m:
        return []
    return [s for s in re.findall(r'"([^"]+)"', m.group(1)) if s.strip()]


def load_keys(cfg_path: str):
    try:
        txt = open(cfg_path, encoding="utf-8").read()
    except Exception:
        txt = ""
    px = _arr(txt, "pexels_api_keys")
    pb = _arr(txt, "pixabay_api_keys")
    if not px:
        px = ["IVZoxqedCuonquZN0OYRdiiGKwUksnzyPcigl3CGfi5pgMCAudy6bHxu"]
    if not pb:
        pb = ["57837897-1832387fee43b83d85cbb9f56"]
    return px, pb


# --------------------------------------------------------------------------- #
# 搜尋
# --------------------------------------------------------------------------- #
def pexels(query, orient, key, per_query):
    try:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers={"Authorization": key, **UA},
            params={"query": query, "per_page": per_query, "orientation": orient},
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return []
        out = []
        for v in r.json().get("videos", []):
            files = [f for f in (v.get("video_files") or []) if f.get("width") and f.get("link")]
            if not files:
                continue
            files.sort(key=lambda f: -f["width"])
            small = [f for f in files if f["width"] <= 1920]
            pick = small[0] if small else files[-1]
            out.append({
                "source": "pexels",
                "id": "px%d" % v["id"],
                "tags": " ".join(t.get("title", "") for t in (v.get("tags") or []) if isinstance(t, dict)),
                "page": v.get("url"),
                "dur": v.get("duration"),
                "w": pick.get("width"),
                "h": pick.get("height"),
                "thumb": v.get("image"),
                "video_url": pick["link"],
                "orientation": orient,
                "query": query,
            })
        return out
    except Exception as e:
        log("pexels err", query, orient, e)
        return []


def pixabay(query, key, per_query):
    try:
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={"q": query, "per_page": min(per_query * 3, 60), "key": key},
            headers=UA,
            timeout=TIMEOUT,
        )
        if r.status_code != 200:
            return []
        out = []
        for h in r.json().get("hits", []):
            v = h.get("videos", {})
            lar = v.get("large") or v.get("medium")
            med = v.get("medium") or v.get("small")
            if not (lar and med):
                continue
            out.append({
                "source": "pixabay",
                "id": "pb%d" % h["id"],
                "tags": h.get("tags"),
                "page": h.get("pageURL"),
                "dur": h.get("duration"),
                "w": lar.get("width"),
                "h": lar.get("height"),
                "thumb": med.get("thumbnail"),
                "video_url": lar.get("url"),
                "orientation": "portrait" if (lar.get("height") or 0) > (lar.get("width") or 0) else "landscape",
                "query": query,
            })
        return out
    except Exception as e:
        log("pixabay err", query, e)
        return []


def fetch_candidates(beats, per_query, px_keys, pb_keys, orient):
    pool = {}
    for bi, b in enumerate(beats):
        queries = b.get("queries") or []
        for q in queries:
            got = []
            for k in px_keys[:2]:
                got += pexels(q, "portrait", k, per_query)
                got += pexels(q, "landscape", k, per_query)
            for k in pb_keys[:2]:
                got += pixabay(q, k, per_query)
            for c in got:
                if c["id"] not in pool:
                    c["beat_hint"] = bi
                    pool[c["id"]] = c
    return list(pool.values())


def get_thumb(c, thumbs_dir):
    p = os.path.join(thumbs_dir, c["id"] + ".jpg")
    if os.path.exists(p) and os.path.getsize(p) > 1000:
        return p
    try:
        r = requests.get(c["thumb"], timeout=TIMEOUT, headers=UA)
        if r.status_code == 200 and len(r.content) > 1000:
            with open(p, "wb") as f:
                f.write(r.content)
            return p
    except Exception:
        pass
    return None


# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
def _find_cjk_font(bold=True):
    """回傳 (字型檔, 字面索引)。TTC 實測：0=JP 1=KR 2=SC 3=TC 4=HK，繁中優先 TC→HK。"""
    name = "NotoSansCJK-Bold.ttc" if bold else "NotoSansCJK-Regular.ttc"
    path = os.path.join("/usr/share/fonts/opentype/noto", name)
    for idx in (3, 4, 0, 2, 1):
        try:
            from PIL import ImageFont
            f = ImageFont.truetype(path, 60, index=idx)
            if f.getbbox("繁體中文養寵物"):
                return path, idx
        except Exception:
            continue
    return path, 0


def _wrap_cjk(text, font, max_w, draw):
    lines, cur = [], ""
    for ch in text:
        if ch == "\n":
            lines.append(cur)
            cur = ""
            continue
        t = cur + ch
        try:
            w = draw.textlength(t, font=font)
        except Exception:
            w = len(t) * 40
        if w > max_w and cur:
            lines.append(cur)
            cur = ch
        else:
            cur = t
    if cur:
        lines.append(cur)
    return lines


def _render_card(text, out_path, orient, seconds=6.0):
    """抽象句字卡：深色漸層底 + Noto CJK 居中排版 → 無聲 h264 mp4。"""
    text = (text or "").strip()
    if not text:
        return False
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception as e:
        log("card PIL missing", e)
        return False
    W, H = (1080, 1920) if orient == "portrait" else (1920, 1080)
    img = Image.new("RGB", (W, H), (10, 14, 26))
    dr = ImageDraw.Draw(img)
    top, bot = (20, 26, 48), (6, 8, 16)
    for y in range(H):
        t = y / max(1, H - 1)
        dr.line([(0, y), (W, y)],
                fill=(int(top[0] + (bot[0] - top[0]) * t),
                      int(top[1] + (bot[1] - top[1]) * t),
                      int(top[2] + (bot[2] - top[2]) * t)))
    n = len(text)
    size = 104 if n <= 12 else 92 if n <= 18 else 78 if n <= 28 else 64 if n <= 44 else 54
    fpath, fidx = _find_cjk_font(True)
    font = ImageFont.truetype(fpath, size, index=fidx)
    lines = _wrap_cjk(text, font, int(W * 0.78), dr)
    line_h = int(size * 1.42)
    y = (H - line_h * len(lines)) / 2
    for ln in lines:
        try:
            w = dr.textlength(ln, font=font)
        except Exception:
            w = len(ln) * size
        x = (W - w) / 2
        dr.text((x + 3, y + 3), ln, font=font, fill=(0, 0, 0))
        dr.text((x, y), ln, font=font, fill=(240, 244, 255))
        y += line_h
    png = out_path + ".src.png"
    img.save(png)
    cmd = ["ffmpeg", "-y", "-loop", "1", "-i", png, "-t", "%.2f" % seconds,
           "-r", "30", "-vf", "format=yuv420p", "-c:v", "libx264",
           "-preset", "veryfast", "-an", out_path]
    ok = False
    try:
        r = subprocess.run(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, timeout=180)
        ok = (r.returncode == 0 and os.path.exists(out_path) and os.path.getsize(out_path) > 5000)
        if not ok:
            log("  card ffmpeg rc=%s %s" % (r.returncode, r.stderr.decode("utf-8", "ignore")[-300:]))
    except Exception as e:
        log("  card ffmpeg err", e)
    try:
        os.remove(png)
    except OSError:
        pass
    return ok


def main():
    if len(sys.argv) > 1 and os.path.isfile(sys.argv[1]):
        spec = json.load(open(sys.argv[1], encoding="utf-8"))
    else:
        spec = json.load(sys.stdin)

    def _is_visual(b):
        v = b.get("visual")
        if v is None:
            return True
        if isinstance(v, str):
            return v.strip().lower() not in ("false", "0", "no", "none", "abstract", "抽象")
        return bool(v)

    raw = [b for b in spec.get("beats", []) if (b.get("caption") or b.get("zh") or "").strip()]
    if not raw:
        print(json.dumps({"success": False, "error": "no beats provided"}, ensure_ascii=False))
        return

    beats = [{
        "visual": _is_visual(b),
        "zh": (b.get("zh") or "").strip(),
        "caption": (b.get("caption") or "").strip(),
        "queries": [str(q).strip() for q in (b.get("queries") or []) if str(q).strip()],
    } for b in raw]

    orient = spec.get("orient", "portrait")
    dest = spec["dest"]
    prefix = spec.get("prefix", "curated")
    workdir = spec.get("workdir") or os.path.join(os.path.dirname(dest), "curated_work")
    thumbs_dir = os.path.join(workdir, "thumbs")
    os.makedirs(dest, exist_ok=True)
    os.makedirs(thumbs_dir, exist_ok=True)

    try:
        cutoff = time.time() - 14 * 86400
        for fn in os.listdir(thumbs_dir):
            fp = os.path.join(thumbs_dir, fn)
            if os.path.isfile(fp) and os.path.getmtime(fp) < cutoff:
                os.remove(fp)
    except OSError:
        pass

    min_gate = float(spec.get("min_gate", 0.02))
    per_query = int(spec.get("per_query", 20))
    card_seconds = float(spec.get("card_seconds", 6.0))
    subject_keywords = [str(k).strip().lower() for k in (spec.get("subject_keywords") or []) if str(k).strip()]
    miss_penalty = float(spec.get("keyword_miss_penalty", 0.03))

    positive = (spec.get("positive") or "").strip() or \
        "professional real-world stock footage matching the described scene"
    negatives = [n for n in (spec.get("negatives") or []) if (n or "").strip()] or [
        "a text slide with big words on a plain background",
        "a chart, graph or diagram",
        "a screenshot of a computer interface",
        "a plain solid colour background",
    ]

    cfg = spec.get("config") or "/home/ubuntu/.mok/mpt/MoneyPrinterTurbo/config.toml"
    px_keys, pb_keys = load_keys(cfg)

    vis_idx = [i for i, b in enumerate(beats) if b["visual"] and b["caption"]]
    log("beats: %d (visual=%d / card=%d) min_gate=%.3f card_sec=%.1f"
        % (len(beats), len(vis_idx), len(beats) - len(vis_idx), min_gate, card_seconds))

    # ---------------- 只為具象 beat 抓候選 ----------------
    keep = []
    if vis_idx:
        log("== fetch candidates ==")
        pool = fetch_candidates([beats[i] for i in vis_idx], per_query, px_keys, pb_keys, orient)
        for c in pool:
            bh = c.get("beat_hint", 0)
            c["beat_all"] = vis_idx[bh] if isinstance(bh, int) and 0 <= bh < len(vis_idx) else vis_idx[0]
        log("candidates:", len(pool))
        log("== thumbnails ==")
        for c in pool:
            p = get_thumb(c, thumbs_dir)
            if p:
                c["thumb_path"] = p
                keep.append(c)
        log("thumbs ok:", len(keep))

    # ---------------- CLIP 打分 ----------------
    scored = []
    if keep:
        log("== CLIP scoring ==")
        import torch
        from PIL import Image
        from transformers import CLIPModel, CLIPProcessor

        dev = "cuda" if torch.cuda.is_available() else "cpu"
        model = CLIPModel.from_pretrained(CLIP_MODEL).to(dev).eval()
        proc = CLIPProcessor.from_pretrained(CLIP_MODEL)

        nb = len(vis_idx)
        texts = [beats[i]["caption"] for i in vis_idx] + [positive] + negatives
        with torch.no_grad():
            T = model.get_text_features(**proc(text=texts, return_tensors="pt", padding=True, truncation=True))
            T = T / T.norm(dim=-1, keepdim=True)
            imgs = []
            for c in keep:
                try:
                    imgs.append(Image.open(c["thumb_path"]).convert("RGB"))
                except Exception:
                    imgs.append(None)
            valid = [i for i, im in enumerate(imgs) if im is not None]
            keep = [keep[i] for i in valid]
            imgs = [imgs[i] for i in valid]
            embs = []
            for i in range(0, len(imgs), 32):
                I = model.get_image_features(**proc(images=imgs[i:i + 32], return_tensors="pt"))
                embs.append(I / I.norm(dim=-1, keepdim=True))
            I = torch.cat(embs, 0)
            sim_beat = I @ T[:nb].T
            pos_sim = I @ T[nb]
            neg_sim = (I @ T[nb + 1:].T).max(dim=1).values if negatives else torch.zeros_like(pos_sim)
            gate = pos_sim - neg_sim
        for i, c in enumerate(keep):
            c["beat_sims"] = [round(float(sim_beat[i][b]), 4) for b in range(nb)]
            c["gate"] = round(float(gate[i]), 4)
            c["argmax_beat"] = int(sim_beat[i].argmax())
            scored.append(c)

    def _kw_ok(c):
        if not subject_keywords:
            return True
        blob = (str(c.get("page") or "") + " " + str(c.get("tags") or "") + " "
                + str(c.get("query") or "")).lower().replace("-", " ").replace("_", " ")
        return any(k.replace("-", " ").replace("_", " ") in blob for k in subject_keywords)

    def _rank(c, sub_b):
        sc = c["beat_sims"][sub_b] + 0.5 * c["gate"] + (0.02 if c["argmax_beat"] == sub_b else 0.0)
        if not _kw_ok(c):
            sc -= miss_penalty
        return sc

    # ---------------- 逐 beat 決策：實拍 or 字卡（刀1+刀2） ----------------
    used = set()
    plan = []
    for b, bt in enumerate(beats):
        if not bt["visual"]:
            plan.append((b, "card", None))
            log("b%d -> CARD(標記抽象)" % b)
            continue
        if b not in vis_idx or not scored:
            plan.append((b, "card", None))
            log("b%d -> CARD(無候選/縮圖失敗)" % b)
            continue
        sub_b = vis_idx.index(b)
        pool = [(i, c) for i, c in enumerate(scored) if c["id"] not in used]
        ranked = sorted(pool, key=lambda ic: -_rank(ic[1], sub_b))
        ok = [(i, c) for i, c in ranked if c["gate"] >= min_gate]
        if not ok:
            plan.append((b, "card", None))
            log("b%d -> CARD(全候選 gate < %.3f)" % (b, min_gate))
            continue
        orient_ok = [(i, c) for i, c in ok if c["orientation"] == orient] or ok
        i, c = orient_ok[0]
        used.add(c["id"])
        plan.append((b, "video", c))
        log("b%d -> VIDEO %s sim=%.3f gate=%+.3f" % (b, c["id"], c["beat_sims"][sub_b], c["gate"]))

    # ---------------- 落盤（嚴格依旁白順序） ----------------
    log("== render ==")
    materials, picks = [], []
    for b, kind, c in plan:
        out = os.path.join(dest, "%s_%02d.mp4" % (prefix, len(materials)))
        ok = False
        if kind == "video":
            sub_b = vis_idx.index(b)
            others = sorted([x for x in scored if x["id"] not in used], key=lambda x: -_rank(x, sub_b))[:3]
            for cand in [c] + others:
                try:
                    if not (os.path.exists(out) and os.path.getsize(out) > 200000):
                        r = requests.get(cand["video_url"], stream=True, timeout=180, headers=UA)
                        r.raise_for_status()
                        with open(out, "wb") as f:
                            for ch in r.iter_content(1 << 20):
                                f.write(ch)
                    if os.path.getsize(out) > 50000:
                        log("  ok b%d <- %s %s (%.1fMB)" % (b, cand["id"], cand["source"], os.path.getsize(out) / 1e6))
                        used.add(cand["id"])
                        picks.append({"beat": b, "kind": "video", "id": cand["id"], "source": cand["source"],
                                      "orientation": cand["orientation"], "w": cand.get("w"), "h": cand.get("h"),
                                      "tags": cand.get("tags"), "page": cand.get("page"), "gate": cand.get("gate"),
                                      "beat_sims": cand.get("beat_sims"), "beat_text": beats[b]["caption"]})
                        ok = True
                        break
                except Exception as e:
                    log("  dl fail", cand["id"], e)
        if not ok:
            txt = beats[b]["zh"] or beats[b]["caption"]
            if _render_card(txt, out, orient, card_seconds):
                log("  ok b%d <- CARD %s" % (b, txt[:24]))
                picks.append({"beat": b, "kind": "card", "id": "card%d" % b, "source": "card",
                              "orientation": orient, "w": None, "h": None, "tags": txt[:40], "page": None,
                              "gate": None, "beat_sims": None, "beat_text": txt})
                ok = True
            else:
                log("  !! b%d card render failed" % b)
        if ok and os.path.exists(out) and os.path.getsize(out) > 1000:
            materials.append(os.path.basename(out))

    for fn in os.listdir(dest):
        if fn.startswith(prefix + "_") and fn.endswith(".mp4") and fn not in materials:
            try:
                os.remove(os.path.join(dest, fn))
            except OSError:
                pass

    result = {
        "success": bool(materials),
        "materials": materials,
        "picks": picks,
        "beats": [b["caption"] or b["zh"] for b in beats],
    }
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
