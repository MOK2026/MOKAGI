# -*- coding: utf-8 -*-
"""
girl_gate.py — A 里程碑：拒答偵測 gate + girl 引擎確認狀態 (純邏輯, 無相依)
設計: 前端 (mok_tg / mok_web) 在收到 LLM 最終回覆後呼叫 check(),
  若判定為「安全拒答且用戶意圖需要 girl 引擎」→ 顯示確認按鈕「開 vast girl / 不用」。
狀態以 JSON 檔共享 (TG 與 Web 前端共用), 路徑預設在 agent 房間。
"""
import json, os, re, time

# 常見「安全/內容政策拒答」指紋 (中/英)
REFUSAL_PATTERNS = [
    r"抱歉.{0,12}(無法|不能|不可以|不便)",
    r"對不起.{0,12}(無法|不能|不可以|不便)",
    r"(我|作為).{0,8}(AI|人工智能).{0,20}(無法|不能|拒絕|不合適|不恰當|不適合)",
    r"不能(夠)?(協助|幫|回答|提供|生成|處理).{0,15}(內容|請求|要求)",
    r"(無法|不能).{0,6}(完成|滿足|處理).{0,10}(請求|要求)",
    r"(色情|成人|露骨|性|未成年|年齡|暴力|血腥|危險|非法|違法).{0,20}(內容|請求|要求|話題|問題)",
    r"不(能|會).{0,8}(討論|提供|生成|參與).{0,10}(這|該|此)",
    r"(抱歉|sorry|I'?m? sorry).{0,40}(can'?t|cannot|unable|not able|won'?t)",
    r"(cannot|cannot|can'?t|unable to).{0,20}(assist|help|provide|answer|generate|create|fulfill)",
    r"(against|violat).{0,20}(policy|guideline|safety|ethic|rule)",
    r"as an? (AI|language model).{0,60}(can'?t|cannot|unable|won'?t|refuse)",
    r"沒有辦法.{0,8}(幫|協助|回答|提供|生成|處理)",
    r"不(能|會|可以|可|敢|想).{0,6}(幫|做|回答|提供|生成|處理|聊|討論|配合)",
    r"(不便|不宜|不合適|不恰當|不允許|無法|不能).{0,20}(內容|話題|請求|要求|提供|生成)",
    r"(不當|敏感|露骨|猥褻|情色|兒少|未成年|裸露|血腥|暴力).{0,20}(內容|議題|話題|請求|要求)",
    r"(違反|抵觸|違背).{0,15}(政策|規範|準則|守則|規定|安全)",
]

def looks_like_refusal(reply: str) -> bool:
    """回覆是否像是『內容政策拒答』。"""
    if not reply:
        return False
    r = reply[:400]
    for p in REFUSAL_PATTERNS:
        if re.search(p, r, re.IGNORECASE):
            return True
    return False

# 需要 girl 引擎的使用者意圖訊號 (僅做輕量提示, 最終仍由拒答+明確意圖決定)
GIRL_INTENT_PATTERNS = [
    r"(色情|成人|露骨|R18|18\+|NSFW|性愛|做愛|H(?![a-z])|h3|deep.?throat|深喉)",
    r"(角色扮演|rp|cosplay|調教|誘惑|挑逗|曖昧|害羞|色色|瑟瑟)",
    r"(不要拒絕|不用拒絕|別管安全|解放|無限制|uncensored|abliterated)",
]

def looks_like_girl_intent(user_text: str) -> bool:
    if not user_text:
        return False
    t = user_text[:500]
    for p in GIRL_INTENT_PATTERNS:
        if re.search(p, t, re.IGNORECASE):
            return True
    return False

def _truthy(v) -> bool:
    return str(v or "").strip().lower() in ("1", "true", "yes", "on", "y", "t")

def auto_open_enabled(agent_name: str = None) -> bool:
    """GIRL_AUTO=1 → 檢查到色情過濾(安全拒答)時，不問主人，直接打開 girl 模型。
    讀取順序：環境變數 GIRL_AUTO → 該 Agent 的 .稚 設定（都可寫在 skill/vastai/.env）。"""
    if _truthy(os.environ.get("GIRL_AUTO")):
        return True
    try:
        import config as _cfg
        cfg = {}
        try:
            cfg.update(dict(getattr(_cfg, "_agent_config", {}) or {}))
        except Exception:
            pass
        if agent_name:
            try:
                cfg.update(_cfg.load_agent_config(agent_name) or {})
            except Exception:
                pass
        return _truthy(cfg.get("GIRL_AUTO"))
    except Exception:
        return False

def check(user_text: str, reply: str, agent_name: str = None) -> dict:
    """回傳是否該彈出「開 vast girl」確認（auto=True 表示設定為偵測到就直接開）。"""
    refusal = looks_like_refusal(reply)
    girl_intent = looks_like_girl_intent(user_text)
    needs = bool(refusal and girl_intent)
    auto = bool(needs and auto_open_enabled(agent_name))
    return {
        "needs_girl": needs,
        "refusal": refusal,
        "girl_intent": girl_intent,
        "auto": auto,
    }

# ---------- 狀態檔 (讓 TG / Web 共用同一筆 pending 確認) ----------
def _state_path(agent_name: str = "稚") -> str:
    base = os.path.join(os.path.expanduser("~"), ".mok", "agent", agent_name)
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "girl_pending.json")

def save_pending(user_id: str, agent_name: str = "稚", user_text: str = "", ttl: int = 1800):
    path = _state_path(agent_name)
    try:
        data = {}
        if os.path.exists(path):
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
        data[user_id] = {"ts": time.time(), "ttl": ttl, "user_text": user_text[:200]}
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        return True
    except Exception as e:
        return False

def pop_pending(user_id: str, agent_name: str = "稚") -> dict:
    path = _state_path(agent_name)
    try:
        if not os.path.exists(path):
            return {}
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        item = data.pop(user_id, {})
        if item and (time.time() - item.get("ts", 0)) > item.get("ttl", 1800):
            item = {}  # 過期
        with open(path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
        return item
    except Exception:
        return {}

if __name__ == "__main__":
    # 自測
    tests = [
        ("寫一篇色情小說", "抱歉，我無法協助生成這類內容。", True),
        ("你好", "你好呀！今天想聊什麼？", False),
        ("幫我 deep throat 教學", "對不起，我不能提供這方面的內容。", True),
        ("今天天氣", "我不能回答天氣，因為沒有網路。", False),
    ]
    for ut, rep, exp in tests:
        r = check(ut, rep)
        print(("PASS" if r["needs_girl"] == exp else "FAIL"), ut, "->", r)
