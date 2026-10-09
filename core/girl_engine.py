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
    """★ 2026-09-28 稚（根治「沒要出片卻自己租機」）：
    偵測到安全拒答時，**不再自動租 GPU**。改為只通知主人，由主人明確決定。
    回傳 False = 沒有自動開啟（前端應顯示「請按按鈕確認」而非「已自動打開」）。
    原因：主人鐵令「我沒要出片時，侍女不得自己租機」；自動租機鏈已整條切除。"""
    try:
        _msg = ("🧠 偵測到一般模型安全拒答。\n"
                "⚠ 自動租機已停用（避免沒下令就燒錢）。如需 vast girl 引擎，請主人明確下令／按按鈕，"
                "或叫侍女開閘：python3 ~/.mok/skill/vastai/scripts/rent_guard.py arm --minutes 90 --reason 出片")
        try:
            import importlib.util as _ilu
            _p = os.path.join(HOME, ".mok", "skill", "vastai", "scripts", "vast_notify.py")
            if os.path.exists(_p):
                _s = _ilu.spec_from_file_location("_vg_notify", _p)
                _m = _ilu.module_from_spec(_s); _s.loader.exec_module(_m)
                _m.notify(_msg)
            else:
                print(_msg)
        except Exception:
            print(_msg)
    except Exception:
        pass
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
        # ★ 2026-09-28 汐修正（根絕「20 機嘖血」）：預設改 1，不再強制至少 20 台。
        #   根因：vastai_rent_llm 常駐無限重試 × 強制 20 競速 × RETAIN=1（停機仍計費）× 無熔斷。
        #   要競速請明確設 LLM_RACE_N>1；租機另有 rent_guard 單日 US$1 熔斷把關。
        _env_race_n = os.environ.get("LLM_RACE_N") or os.environ.get("GIRL_RACE_N") or "1"
        try:
            _n = int(_env_race_n)
        except Exception:
            _n = 1
        if _n < 1:
            _n = 1
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
    # 完全沒機 → 走到這裡代表「主人明確按了按鈕／明確下令」，屬明確動作：
    # 先開租機閘門（預設 60 分鐘，20 台競速額度），再租機。逾時自動關，杜絕自走。
    try:
        import importlib.util as _ilu2
        _rgp = os.path.join(HOME, ".mok", "skill", "vastai", "scripts", "rent_guard.py")
        if os.path.exists(_rgp):
            _s2 = _ilu2.spec_from_file_location("_rent_guard_gate", _rgp)
            _rg = _ilu2.module_from_spec(_s2); _s2.loader.exec_module(_rg)
            _rg.arm(minutes=60, by="主人", reason="開 vast girl 引擎（按鈕/明確下令）", max_machines=20)
    except Exception as _e_arm:
        print(f"[girl_engine] 開閘失敗（不影響流程）: {_e_arm}")
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
