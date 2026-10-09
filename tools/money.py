# -*- coding: utf-8 -*-
"""
money.py - MoneyPrinterTurbo 短影音生成工具

用法:
    /money 主題                     → 生成 9:16 直式短影音
    /money subject=主題 aspect=16:9 paragraphs=3
    LLM tool call: money_video(subject=..., aspect=..., paragraphs=..., language=...)

底層: /home/ubuntu/.mok/mpt/MoneyPrinterTurbo/cli.py (uv venv)
"""
import os
import re
import asyncio
import json
import logging
import shutil
from typing import Optional, Dict, List

logger = logging.getLogger(__name__)

PLUGIN_INFO = {
    "command": "/money",
    "icon": "🎬",
    "handler": "handle_money",
    "description": "AI 短影音生成：輸入主題，自動寫腳本、配音、配素材、加字幕合成影片（MoneyPrinterTurbo）。",
    "intent_keywords": [
        ("做影片", "/money"),
        ("生成影片", "/money"),
        ("短影音", "/money"),
        ("AI影片", "/money"),
    ],
    "tool_schema": {
        "name": "money_video",
        "description": (
            "生成 AI 短影音。給定一個主題或關鍵詞，自動生成腳本、配音、素材、字幕並合成短影音。"
            "可用 stop_at 控制只生成到中間步驟（script=只寫腳本，audio=含配音，materials=含素材，video=完整影片）。"
            "video_source=curated 時，會把旁白逐句拆成視覺 beat、以 CLIP 視覺閘門自動選材並依旁白順序拼接，避免畫面與旁白錯配。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "subject": {"type": "string", "description": "影片主題或關鍵詞，例如：深海裡的微光"},
                "language": {"type": "string", "description": "語言代碼：zh-CN（簡中）/ zh-TW（繁中）/ en-US（英文）", "default": "zh-CN"},
                "aspect": {"type": "string", "description": "畫面比例：9:16（直式抖音）/ 16:9（橫式）", "default": "9:16"},
                "paragraphs": {"type": "integer", "description": "段落數，每段約10秒旁白，1-5", "default": 2},
                "stop_at": {"type": "string", "description": "生成到哪一步：script/audio/subtitle/materials/video", "default": "video"},
                "video_source": {"type": "string", "description": "素材來源：pexels/pixabay/coverr/local/curated。curated=逐句拆 beat + CLIP 視覺閘門自動選材（依旁白順序拼接，避免畫面與旁白錯配）；留空則用 config.toml 設定", "default": ""},
                "video_terms": {"type": "string", "description": "自訂素材搜尋詞，逗號分隔（留空則由 LLM 生成）", "default": ""},
            },
            "required": ["subject"],
        },
    },
    "update": "202608220715",
    "naturalize_func": "naturalize_money_result",
}

MPT_DIR = "/home/ubuntu/.mok/mpt/MoneyPrinterTurbo"
PYTHON = os.path.join(MPT_DIR, ".venv", "bin", "python")
CLI = os.path.join(MPT_DIR, "cli.py")
STORAGE = os.path.join(MPT_DIR, "storage", "tasks")
LOCAL_VIDEOS = os.path.join(MPT_DIR, "storage", "local_videos")


def _parse_args(args) -> Dict:
    """統一解析 str 或 dict 參數。"""
    if isinstance(args, dict):
        return args
    s = str(args).strip()
    if not s:
        return {}
    # 支援 key=value 或純文字（當作 subject）
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


_VALID_VSRC = ("pexels", "pixabay", "coverr", "local", "curated")


def _default_video_source() -> str:
    """讀 MPT config.toml 的 video_source，失敗回 pexels。"""
    try:
        txt = open(os.path.join(MPT_DIR, "config.toml"), encoding="utf-8").read()
        m = re.search(r'^\s*video_source\s*=\s*"([^"]+)"', txt, re.M)
        if m and m.group(1) in _VALID_VSRC:
            return m.group(1)
    except Exception:
        pass
    return "pexels"


# 抽象／具象詞表：供 video_source 留空時自動分流用（刀3）
_ABSTRACT_HINTS = (
    "微光", "孤獨", "資本", "時代", "意義", "本質", "思維", "認知", "心理", "情緒",
    "哲學", "命運", "自由", "成長", "選擇", "人性", "效率", "財富", "價值", "趨勢",
    "焦慮", "內耗", "自律", "格局", "邏輯", "智慧", "心法", "秘密", "真相", "力量",
    "知識", "習慣", "專注", "關係", "溝通", "幸福", "成功", "失敗", "時間", "記憶",
)
_CONCRETE_HINTS = (
    "寵物", "貓", "狗", "城市", "咖啡", "美食", "運動", "旅行", "森林",
    "天空", "星空", "汽車", "手機", "電腦", "厨房", "料理", "健身",
    "花園", "街道", "市場", "校園", "醫院", "機場", "火車", "動物",
    "嬰兒", "孩子", "音樂", "食物", "水果", "蔬菜", "機械", "太空",
    "大海", "海洋", "海邊", "海浪", "高山", "山峰", "花朵", "花海",
    "下雨", "雪花", "小鳥", "魚缸", "書本", "舞蹈", "狗仔", "毛孩",
)


def _looks_abstract(subject: str) -> bool:
    """主題是否偏抽象（→ 自動走 curated 混合流）。

    判定序：先數抽象詞／具象詞命中數，具象不少於抽象 → 具象（避免「深海裡的微光」
    被單字「海」誤判）；抽象較多 → 抽象；兩者皆 0 且主題短 → 抽象。
    """
    s = (subject or "").strip()
    if not s:
        return False
    n_abs = sum(1 for k in _ABSTRACT_HINTS if k in s)
    n_con = sum(1 for k in _CONCRETE_HINTS if k in s)
    if n_con > n_abs:
        return False          # 具象詞明確較多 → 走實拍
    if n_abs > 0:
        return True           # 抽象較多或打和 → 走混合流（抽象句自動字卡，最安全）
    return len(s) <= 10


# --------------------------------------------------------------------------- #
# video_source=curated：逐句拆 beat + CLIP 視覺閘門選材
# --------------------------------------------------------------------------- #
CURATED_ENGINE = "/home/ubuntu/.mok/tools/curated_materials.py"
MPT_LLM = "/home/ubuntu/.mok/tools/mpt_llm.py"
MPT_CONFIG = os.path.join(MPT_DIR, "config.toml")
CURATED_WORKDIR = os.path.join(MPT_DIR, "storage", "curated_work")


async def _run_cli(cmd, timeout=900):
    """執行 MPT CLI，回傳 (returncode, 文字輸出)。-1 代表逾時。"""
    proc = await asyncio.create_subprocess_exec(
        *cmd,
        cwd=MPT_DIR,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
    )
    try:
        out, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        proc.kill()
        return -1, "TIMEOUT"
    return proc.returncode, out.decode("utf-8", errors="ignore")


def _cli_result_json(text):
    """從 CLI stdout 取最後一行結果 JSON（{"task_id":..., "result":...}）。

    MoneyPrinterTurbo 的 cli.py 成功時會 print 一行 JSON，其中 result 直接
    帶著本次生成的 script。注意 --stop-at script 不會寫 script.json，因此
    這行 JSON 是唯一可靠取得「本次腳本」的來源；不可再用 mtime 猜目錄，
    否則會抓到別的舊任務旁白（曾導致整支影片文不對題）。
    """
    if not text:
        return None
    for line in reversed(str(text).strip().splitlines()):
        line = line.strip()
        if line.startswith("{") and "task_id" in line:
            try:
                obj = json.loads(line)
            except Exception:
                continue
            if isinstance(obj, dict) and obj.get("task_id"):
                return obj
    return None


def _newest_task_script():
    """讀取最新 task 目錄的 script.json，取得旁白全文（僅作最後退路）。"""
    newest, mt = None, 0.0
    if os.path.isdir(STORAGE):
        for name in os.listdir(STORAGE):
            d = os.path.join(STORAGE, name)
            if os.path.isdir(d):
                m = os.path.getmtime(d)
                if m > mt:
                    mt, newest = m, d
    if not newest:
        return ""
    try:
        data = json.load(open(os.path.join(newest, "script.json"), encoding="utf-8"))
        return (data.get("script") or "").strip()
    except Exception:
        return ""


def _extract_json(text):
    """從 LLM 回覆中抽出第一個 JSON 物件。"""
    if not text:
        return None
    if isinstance(text, dict):
        return text
    i, j = text.find("{"), text.rfind("}")
    if i < 0 or j <= i:
        return None
    frag = text[i:j + 1]
    try:
        return json.loads(frag)
    except Exception:
        try:
            return json.loads(frag.replace("，", ",").replace("：", ":"))
        except Exception:
            return None


def _split_sentences(script):
    parts = re.split(r"[。！？!?\n]+", script or "")
    return [s.strip() for s in parts if len(s.strip()) >= 4]


async def _gen_script(subject, language, paragraphs):
    """只生成旁白（stop-at script），回傳腳本文字。"""
    cmd = [
        PYTHON, CLI,
        "--video-subject", subject,
        "--video-language", language,
        "--video-aspect", "9:16",
        "--paragraph-number", str(paragraphs),
        "--video-source", "pexels",
        "--stop-at", "script",
    ]
    rc, text = await _run_cli(cmd, timeout=420)
    # 優先採用 CLI 本次回傳的 result.script（--stop-at script 不寫 script.json）
    obj = _cli_result_json(text)
    if obj:
        s = str((obj.get("result") or {}).get("script") or "").strip()
        if s:
            return s
        tid = str(obj.get("task_id") or "").strip()
        if tid:
            try:
                data = json.load(open(os.path.join(STORAGE, tid, "script.json"), encoding="utf-8"))
                s = (data.get("script") or "").strip()
                if s:
                    return s
            except Exception:
                pass
    logger.warning("[money] 無法從 CLI 結果取得本次腳本，回退 _newest_task_script()")
    return _newest_task_script()


async def _llm_beats(subject, script, agent_config=None):
    """用 LLM 將旁白拆成逐句視覺 beat，並產生 CLIP 用的正/負面描述。"""
    sentences = _split_sentences(script)
    n = max(3, min(12, len(sentences) or 4))
    numbered = "\n".join("%d. %s" % (i + 1, s) for i, s in enumerate(sentences))
    prompt = (
        "你係短影音素材策劃師。以下係一段影片旁白（主題：%s）。\n\n"
        "請做兩件事：\n"
        "1. 按敘事順序，將旁白拆成剛好 %d 個「視覺 beat」，每個 beat 對應一句或一組意思，"
        "次序必須同旁白一致。\n"
        "2. 為每個 beat 寫：\n"
        "   - caption：一句英文 CLIP 視覺描述（描述該句應出現嘅真實畫面，愈具體愈好）\n"
        "   - queries：2 條英文素材搜尋詞（每條 2-5 個字，用於 Pexels/Pixabay）\n"
        "   - visual：true 或 false。該句有明確可拍嘅真實畫面（人／物／地點／動作）→ true；\n"
        "     純抽象（情緒、道理、心法、數字、年份、口號）→ false，此時 queries 可留空。\n"
        "   - zh：該 beat 對應嘅旁白原文（抽象句必須原文照抄，會用來做字卡）\n\n"
        "另外寫：\n"
        "   - positive：一句英文，描述「正確畫面」整體應該係點（例如夜間城市天際線配燈光）\n"
        "   - negatives：3-5 條英文，描述「明顯錯誤/垃圾畫面」（要同 positive 相反或係非實景，"
        "例如日間沙灘、日間山林、文字簡報、圖表、介面截圖、純色背景）\n\n"
        "   - subject_keywords：2-4 個主題關鍵實體（地名/品牌/專有名詞，用於核對素材地域一致性；"
        "若主題係抽象概念，就填最核心嘅視覺名詞）\n\n"
        "旁白逐句：\n%s\n\n"
        "只准輸出以下 JSON（唔要任何解釋、唔要 markdown 圍欄）：\n"
        '{"beats":[{"zh":"中文摘要","visual":true,"caption":"English visual caption","queries":["q1","q2"]}],'
        '"positive":"...","negatives":["...","..."],"subject_keywords":["..."]}'
    ) % (subject, n, numbered)

    os.makedirs(CURATED_WORKDIR, exist_ok=True)
    pf = os.path.join(CURATED_WORKDIR, "beats_prompt.txt")
    with open(pf, "w", encoding="utf-8") as f:
        f.write(prompt)
    rc, text = await _run_cli([PYTHON, MPT_LLM, pf], timeout=300)
    if not text.strip():
        logger.warning("[money] curated beats LLM 無輸出 rc=%s" % rc)
    data = _extract_json(text)
    if not data or not data.get("beats"):
        return None
    def _vis(v):
        if v is None:
            return True
        if isinstance(v, str):
            return v.strip().lower() not in ("false", "0", "no", "none", "abstract", "抽象")
        return bool(v)

    beats = []
    for b in data["beats"]:
        zh = (b.get("zh") or "").strip()
        cap = (b.get("caption") or "").strip()
        qs = [q.strip() for q in (b.get("queries") or []) if str(q).strip()]
        visual = _vis(b.get("visual"))
        if visual and cap and qs:
            beats.append({"zh": zh, "visual": True, "caption": cap, "queries": qs[:3]})
        elif zh:
            # 抽象句 / 缺 caption → 直接字卡（刀1）
            beats.append({"zh": zh, "visual": False, "caption": "", "queries": []})
    if not beats:
        return None
    return {
        "beats": beats,
        "positive": (data.get("positive") or "").strip(),
        "negatives": [n for n in (data.get("negatives") or []) if (n or "").strip()][:6],
        "subject_keywords": [k for k in (data.get("subject_keywords") or []) if str(k).strip()][:6],
    }


async def _handle_curated(subject, language, aspect, paragraphs, stop_at, agent_config):
    """video_source=curated 主流程。"""
    script = await _gen_script(subject, language, paragraphs)
    if not script:
        return json.dumps({"success": False, "error": "curated：無法產生旁白腳本"},
                          ensure_ascii=False)

    if stop_at == "script":
        return json.dumps({"success": True, "video_source": "curated", "stage": "script",
                           "script": script}, ensure_ascii=False)

    # audio / subtitle：沿用同一份旁白，不需要視覺選材
    if stop_at in ("audio", "subtitle"):
        cmd = [
            PYTHON, CLI,
            "--video-subject", subject,
            "--video-script", script,
            "--video-language", language,
            "--video-aspect", aspect,
            "--paragraph-number", str(paragraphs),
            "--video-source", "pexels",
            "--stop-at", stop_at,
        ]
        rc, text = await _run_cli(cmd, timeout=900)
        return _summarize(subject, stop_at, text)

    plan = await _llm_beats(subject, script, agent_config)
    if not plan:
        return json.dumps({"success": False, "error": "curated：無法拆解視覺 beat（LLM 回覆解析失敗）",
                           "script": script}, ensure_ascii=False)

    os.makedirs(CURATED_WORKDIR, exist_ok=True)
    spec = {
        "beats": plan["beats"],
        "positive": plan["positive"],
        "negatives": plan["negatives"],
        "subject_keywords": plan.get("subject_keywords", []),
        "orient": "portrait" if aspect in ("9:16", "1:1") else "landscape",
        "dest": LOCAL_VIDEOS,
        "prefix": "curated",
        "workdir": CURATED_WORKDIR,
        "config": MPT_CONFIG,
        "per_query": 20,
        "min_gate": 0.02,
        "card_seconds": 6.0,
    }
    spec_path = os.path.join(CURATED_WORKDIR, "spec.json")
    json.dump(spec, open(spec_path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    logger.info("[money] curated: %d beats -> %s" % (len(plan["beats"]), spec_path))

    proc = await asyncio.create_subprocess_exec(
        "python3", CURATED_ENGINE, spec_path,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(), timeout=1200)
    except asyncio.TimeoutError:
        proc.kill()
        return json.dumps({"success": False, "error": "curated：選材逾時（>20分鐘）"},
                          ensure_ascii=False)

    stdout = out.decode("utf-8", errors="ignore").strip().splitlines()
    stderr = err.decode("utf-8", errors="ignore")
    logger.info("[money] curated engine stderr tail:\n%s" % stderr[-2000:])
    res = None
    for line in reversed(stdout):
        res = _extract_json(line)
        if res is not None:
            break
    if not res or not res.get("materials"):
        return json.dumps({"success": False, "error": "curated：視覺選材失敗",
                           "detail": stderr[-800:]}, ensure_ascii=False)

    if stop_at == "materials":
        return json.dumps({"success": True, "video_source": "curated", "stage": "materials",
                           "script": script, "materials": res["materials"],
                           "picks": res.get("picks", [])}, ensure_ascii=False)

    # 依旁白長度估算語音秒數，令每段素材時長足以覆蓋旁白（避免素材重複出現）
    n_mat = max(1, len(res["materials"]))
    est_sec = max(8.0, len(script) / 4.2)
    clip_dur = max(4, min(20, int(est_sec / n_mat) + 1))

    cmd = [
        PYTHON, CLI,
        "--video-subject", subject,
        "--video-script", script,
        "--video-language", language,
        "--video-aspect", aspect,
        "--paragraph-number", str(paragraphs),
        "--video-source", "local",
        "--video-materials", ",".join(res["materials"]),
        "--video-concat-mode", "sequential",
        "--video-clip-duration", str(clip_dur),
        "--stop-at", stop_at,
    ]
    logger.info("[money] curated final: %s" % " ".join(cmd))
    rc, text = await _run_cli(cmd, timeout=900)
    result = _summarize(subject, stop_at, text)
    if stop_at == "video":
        try:
            removed = _cleanup_local_videos()
            if removed:
                logger.info("[money] curated 生成完成，已清理 %d 個素材" % removed)
        except Exception:
            logger.exception("[money] 素材清理失敗（不影響結果）")
    return result


def _balance_blocked(tool_name):
    """P0 餘額熔斷：低餘額時暫停非必要高耗工具（fail-open）。"""
    try:
        import sys as _sys
        _core = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "core")
        if _core not in _sys.path:
            _sys.path.insert(0, _core)
        from deepseek_guard import tool_blocked
        return tool_blocked(tool_name)
    except Exception:
        return False


async def handle_money(args, user_id: str = None, agent_config: Optional[Dict] = None) -> str:
    if _balance_blocked("money_video"):
        return json.dumps({"success": False, "error": "DeepSeek 餘額不足，已暫停非必要高耗工具（短影音生成）；請主人補值後再試。"}, ensure_ascii=False)
    try:
        # 硬性前置檢查：沒有 MOK_OUTPUT_DIR（無落點／無身分）＝整個 money 功能停用，拒絕生成。
        # 不做向後相容：不退回 mpt 原路徑，也不產出任何檔案。
        _out_root = (os.environ.get("MOK_OUTPUT_DIR") or "").strip()
        if not _out_root:
            logger.error("[money] 未設定 MOK_OUTPUT_DIR：money 功能已停用，拒絕生成")
            return json.dumps({
                "success": False,
                "disabled": True,
                "error": "money 功能未啟用：環境缺少 MOK_OUTPUT_DIR（無落點／無身分），已停用，不生成。",
            }, ensure_ascii=False)

        p = _parse_args(args)
        subject = (p.get("subject") or "").strip()
        if not subject:
            return json.dumps({"success": False, "error": "請提供影片主題，例如：/money 深海裡的微光"}, ensure_ascii=False)

        language = p.get("language", "zh-CN")
        aspect = p.get("aspect", "9:16")
        paragraphs = int(p.get("paragraphs", 2))
        stop_at = p.get("stop_at", "video")
        video_source = (p.get("video_source") or "").strip().lower()
        video_terms = (p.get("video_terms") or "").strip()
        _explicit_src = bool((p.get("video_source") or "").strip())
        if video_source not in _VALID_VSRC:
            video_source = _default_video_source()
        # 刀3：未明確指定來源、且主題偏抽象 → 自動走 curated 混合流（具象句實拍＋抽象句字卡）
        if not _explicit_src and video_source != "curated" and _looks_abstract(subject):
            logger.info("[money] 主題偏抽象，自動改走 curated 混合流：%s" % subject)
            video_source = "curated"

        if aspect not in ("9:16", "16:9", "1:1"):
            aspect = "9:16"
        if paragraphs < 1 or paragraphs > 6:
            paragraphs = 2
        if stop_at not in ("script", "audio", "subtitle", "materials", "video"):
            stop_at = "video"

        if video_source == "curated":
            return await _handle_curated(subject, language, aspect, paragraphs, stop_at, agent_config)

        cmd = [
            PYTHON, CLI,
            "--video-subject", subject,
            "--video-language", language,
            "--video-aspect", aspect,
            "--paragraph-number", str(paragraphs),
            "--video-source", video_source,
            *(["--video-terms", video_terms] if video_terms else []),
            "--stop-at", stop_at,
        ]

        logger.info(f"[money] running: {' '.join(cmd)}")
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=MPT_DIR,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
        )
        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=900)
        except asyncio.TimeoutError:
            proc.kill()
            return json.dumps({"success": False, "error": "生成逾時（>15分鐘）"}, ensure_ascii=False)

        text = out.decode("utf-8", errors="ignore")
        result = _summarize(subject, stop_at, text)
        # 完整影片生成後自動清理已下載素材，避免堆積佔用磁碟（stop_at=materials 時保留素材供使用）
        if stop_at == "video":
            try:
                removed = _cleanup_local_videos()
                if removed:
                    logger.info(f"[money] 生成完成，已自動清理 {removed} 個素材檔案")
            except Exception:
                logger.exception("[money] 素材清理失敗（不影響結果）")
        return result
    except Exception as e:
        logger.exception("[money] error")
        return json.dumps({"success": False, "error": str(e)}, ensure_ascii=False)


def _cleanup_local_videos() -> int:
    """清理 local_videos 素材下載目錄，返回刪除的檔案數。"""
    if not os.path.isdir(LOCAL_VIDEOS):
        return 0
    removed = 0
    for f in os.listdir(LOCAL_VIDEOS):
        fp = os.path.join(LOCAL_VIDEOS, f)
        try:
            if os.path.isfile(fp):
                os.remove(fp)
                removed += 1
        except OSError:
            logger.warning(f"[money] 無法刪除素材: {fp}")
    return removed


def _route_outputs(result: Dict) -> Dict:
    """把本次生成的產物落到 MOK_OUTPUT_DIR（三層落點權威來源 output_router）。

    - 有 MOK_OUTPUT_DIR：把 task 目錄的「所有產出」整包複製到
      <MOK_OUTPUT_DIR>/money_<task_id>/（影片、音檔、字幕 .srt/.ass/.vtt、
      script.json 等，含子目錄），並把回報路徑改指向該處。
    - 沒有：功能停用，直接失敗（無向後相容、不退回 mpt 原路徑）。
      正常情況已在 handle_money 頂部擋下，此處僅為防禦。
    """
    out_root = (os.environ.get("MOK_OUTPUT_DIR") or "").strip()
    src_dir = result.get("output_dir")
    if not out_root:
        logger.error("[money] 無 MOK_OUTPUT_DIR：落點不可用，money 功能停用（拒絕產出）")
        return {"success": False, "disabled": True,
                "error": "money 功能未啟用：環境缺少 MOK_OUTPUT_DIR，產物無處落點。"}
    if not src_dir or not os.path.isdir(src_dir):
        return result

    task_id = os.path.basename(src_dir.rstrip("/")) or "task"
    dest_dir = os.path.join(out_root, "money_%s" % task_id)
    try:
        os.makedirs(dest_dir, exist_ok=True)
    except Exception:
        logger.exception("[money] 無法建立落點目錄（維持 mpt 原路徑）：%s" % dest_dir)
        return result

    copied_videos = []
    copied_subs = []
    copied_all = []
    try:
        for f in sorted(os.listdir(src_dir)):
            sp = os.path.join(src_dir, f)
            dp = os.path.join(dest_dir, f)
            if os.path.isdir(sp):
                shutil.copytree(sp, dp, dirs_exist_ok=True)
                copied_all.append(dp)
                continue
            if not os.path.isfile(sp):
                continue
            shutil.copy2(sp, dp)
            copied_all.append(dp)
            if f.startswith("final-") and f.endswith(".mp4"):
                copied_videos.append(dp)
            if f.lower().endswith((".srt", ".ass", ".vtt")):
                copied_subs.append(dp)
    except Exception:
        logger.exception("[money] 複製產物失敗（維持 mpt 原路徑）")
        return result

    # 沒有 final-*（例如提前結束）時，退而用目錄內其他 mp4 當回報影片
    if not copied_videos:
        copied_videos = [p for p in copied_all if p.lower().endswith(".mp4")]

    if copied_all:
        result["source_dir"] = src_dir
        result["output_dir"] = dest_dir
        result["files"] = copied_all
        if copied_videos:
            result["videos"] = copied_videos
        if copied_subs:
            result["subtitles"] = copied_subs
        logger.info("[money] 產物已整包落點（%d 檔，含字幕 %d）：%s -> %s"
                    % (len(copied_all), len(copied_subs), src_dir, dest_dir))
    return result


def _summarize(subject: str, stop_at: str, log_text: str) -> str:
    """從 CLI 日誌中提取結果摘要。"""
    # 優先採用 CLI 本次回報的 task_id；取不到才退回 mtime 最新目錄
    newest_task = None
    _obj = _cli_result_json(log_text)
    if _obj:
        _tid = str(_obj.get("task_id") or "").strip()
        if _tid and os.path.isdir(os.path.join(STORAGE, _tid)):
            newest_task = _tid
    newest_mtime = 0
    if not newest_task and os.path.isdir(STORAGE):
        for name in os.listdir(STORAGE):
            d = os.path.join(STORAGE, name)
            if os.path.isdir(d):
                m = os.path.getmtime(d)
                if m > newest_mtime:
                    newest_mtime = m
                    newest_task = name

    finals = []
    if newest_task:
        tdir = os.path.join(STORAGE, newest_task)
        for f in sorted(os.listdir(tdir)):
            if f.startswith("final-") and f.endswith(".mp4"):
                finals.append(os.path.join(tdir, f))

    error_lines = [ln for ln in log_text.splitlines() if "ERROR" in ln or "failed" in ln.lower()]
    errors = error_lines[-3:] if error_lines else []

    result = {
        "success": True,
        "subject": subject,
        "stage": stop_at,
        "output_dir": os.path.join(STORAGE, newest_task) if newest_task else None,
        "videos": finals,
    }
    if errors:
        result["warnings"] = errors
    result = _route_outputs(result)
    return json.dumps(result, ensure_ascii=False)


async def naturalize_money_result(user_text: str = "", raw_result: str = "", ollama_api: str = None,
                                  model_name: str = None, temp_msg=None, context=None,
                                  agent_config: dict = None, result_str: str = None) -> str:
    """把 JSON 結果轉成人話（框架標準簽名）。"""
    raw = result_str if result_str is not None else raw_result
    try:
        d = json.loads(raw) if isinstance(raw, str) else raw
    except Exception:
        return raw
    if not isinstance(d, dict):
        return raw
    if not d.get("success"):
        return f"❌ 生成失敗：{d.get('error', '未知錯誤')}"
    subject = d.get("subject", "")
    stage = d.get("stage", "video")
    videos = d.get("videos") or []
    stage_name = {
        "script": "腳本", "audio": "配音", "subtitle": "字幕",
        "materials": "素材", "video": "完整影片",
    }.get(stage, stage)
    if videos:
        lines = [f"🎬 「{subject}」{stage_name}生成完成！共 {len(videos)} 支："]
        lines += videos
        subs = d.get("subtitles") or []
        if subs:
            lines.append("📄 字幕：" + "、".join(os.path.basename(s) for s in subs))
        return "\n".join(lines)
    if d.get("output_dir"):
        return f"🎬 「{subject}」{stage_name}已生成，輸出目錄：{d['output_dir']}"
    return f"🎬 「{subject}」{stage_name}已生成。"
