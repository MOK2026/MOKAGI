# -*- coding: utf-8 -*-
"""
girl_engine.py — D/E 里程碑膠水
按「開 vast girl」按鈕後呼叫 request_girl_start():
  1) 檢查 127.0.0.1:11435 (反向隧道) 是否已通、girl:qwen-Claude 是否已載入
  2) 已上線 → 回傳狀態 (E 切模型由外部/後續流程完成)
  3) 未上線 → 檢查 vast 是否有實例在部署；若完全沒機 → 自動 spawn
     scripts/vastai_rent_llm.py (D 自動租機+部署+反向隧道)
純標準庫, 無第三方相依。
"""
import json, os, subprocess, sys, time, urllib.request, urllib.error

GIRL_MODEL = "girl:qwen-Claude"
OLLAMA_URL = "http://127.0.0.1:11435"
AGENT = "稚"
HOME = os.path.expanduser("~")
VAST_DIR = os.path.join(HOME, ".mok", "agent", AGENT, "jobs", "vastai")
RENT_DRIVER = os.path.join(VAST_DIR, "scripts", "vastai_rent_llm.py")
RENT_LOG = os.path.join(VAST_DIR, "logs", "llm_rent_driver.out")


def _tags():
    try:
        req = urllib.request.Request(OLLAMA_URL + "/api/tags", method="GET")
        with urllib.request.urlopen(req, timeout=6) as r:
            data = json.loads(r.read().decode("utf-8"))
        return data.get("models", [])
    except Exception:
        return None


def _load_env():
    env = {}
    p = os.path.join(VAST_DIR, ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def _vast_instances():
    """回傳進行中 vast 實例列表(讀取權限即可)；失敗回 None"""
    env = _load_env()
    key = env.get("VAST_API_KEY")
    if not key:
        return None
    try:
        req = urllib.request.Request("https://console.vast.ai/api/v0/instances/?limit=100",
                                     headers={"Authorization": "Bearer " + key})
        with urllib.request.urlopen(req, timeout=15) as r:
            d = json.loads(r.read().decode("utf-8"))
        insts = d.get("instances", [])
        return [i for i in insts if i.get("actual_status") in
                ("running", "loading", "offering", "initializing", "starting")]
    except Exception:
        return None


def spawn_auto_start(user_id: str = "", agent: str = "稚") -> bool:
    """★ 檢查到色情過濾(安全拒答) → 射後不理地在背景打開 girl 模型。
    供前端 girl_gate(auto=True) 直接呼叫；不阻塞對話串流。"""
    try:
        import asyncio as _asyncio, threading as _threading
        def _run():
            try:
                _asyncio.run(request_girl_start(user_id, agent))
            except Exception:
                pass
        _threading.Thread(target=_run, daemon=True).start()
        return True
    except Exception:
        return False


def _state_path():
    base = os.path.join(HOME, ".mok", "agent", AGENT)
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "girl_state.json")


def _load_state():
    p = _state_path()
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            return {}
    return {}


def _spawn_rent(agent="稚"):
    """背景啟動 vastai_rent_llm.py (已核准租 GPU)"""
    os.makedirs(os.path.dirname(RENT_LOG), exist_ok=True)
    if not os.path.exists(RENT_DRIVER):
        return False, f"找不到租機程式 {RENT_DRIVER}"
    try:
        cmd = [sys.executable, RENT_DRIVER]
        # 多租競速（SOP_LLM引擎.md）：★ 主人 2026-09-13 鐵令「一律 20 台競速」
        # （H3 出片 / LLM / 日後所有 vast 用途都必須）；開不到的立即刪、留最穩最便宜。
        # 除錯真的想單租時，直接跑 vastai_rent_llm.py --no-race，不要從這裡降級。
        _env_race_n = os.environ.get("LLM_RACE_N") or os.environ.get("GIRL_RACE_N") or "20"
        try:
            _n = int(_env_race_n)
        except Exception:
            _n = 20
        if _n < 20:
            _n = 20   # 硬化：正式路徑一律至少 20 競速
        if _n > 1:
            cmd += ["--race", str(_n)]  # 多租競速: 開不到的立即刪, 開到的留最穩最便宜
        with open(RENT_LOG, "ab") as f:
            subprocess.Popen(cmd, stdout=f, stderr=f, start_new_session=True,
                             cwd=VAST_DIR)
        # 就緒自動收尾：掛 watcher，模型上線後自動把該侍女切到 girl 模型並 reload（冪等）
        try:
            _flip = os.path.join(VAST_DIR, "scripts", "e_flip_when_ready.py")
            if os.path.exists(_flip):
                _lf = open(RENT_LOG, "ab")
                subprocess.Popen([sys.executable, _flip], stdout=_lf, stderr=_lf,
                                 start_new_session=True, cwd=VAST_DIR,
                                 env=dict(os.environ, GIRL_FLIP_AGENT=str(agent)))
        except Exception:
            pass
        return True, ""
    except Exception as e:
        return False, repr(e)


async def request_girl_start(user_id: str = "", agent: str = "稚"):
    """回傳 (ok, msg)。ok=True 表示引擎可用/已在路上; False 表示受阻需主人處理。"""
    models = _tags()
    if models is not None:
        names = [m.get("name", "") for m in models]
        if any(GIRL_MODEL in n for n in names):
            return (True, f"✅ vast girl 引擎已在線（{GIRL_MODEL}）。已把該侍女切到 girl 模型，請重發一次剛才的問題。")
        return (True, f"🟡 vast girl ollama 已通（{len(names)} 個模型: {', '.join(names[:5])}），但 {GIRL_MODEL} 尚未建好。")
    # 母機端 11435 未通 → 看 vast 是否已有實例在部署
    insts = _vast_instances()
    if insts:
        ids = ", ".join(str(i.get("id")) for i in insts[:3])
        return (False, f"⏳ vast girl 實例({ids}) 部署中（反向隧道尚未就緒）。請稍候再按，或叫我查進度。")
    # 完全沒機 → D: 自動租機部署 (主人已核准)
    ok, err = _spawn_rent(agent)
    if ok:
        return (False,
                "🚀 已自動開始租 vast GPU（girl:qwen-Claude 27B｜已烘焙範本・0 秒開機）。\n"
                "多租競速進行中：開不到的立即刪、留最穩最便宜一台，上線通常 1–5 分鐘（租機→反向隧道 11435/6969）。\n"
                "就緒後會自動把妳切到 girl 模型並自動刷新頁面（不重啟服務）。進度可叫我查「girl 進度」。")
    return (False,
            f"❌ vast girl 引擎未開機，且自動租機啟動失敗: {err}\n"
            "請主人檢查 jobs/vastai/.env 的 VAST_API_KEY（需具 instance_write 權限）。")


if __name__ == "__main__":
    import asyncio
    print(asyncio.run(request_girl_start()))
