#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
gui_agent.py — 【路線 A】把「截圖 → vision(多模態看圖) → 行動(xdotool)」的 Agent 迴圈
包成一個單一工具：呼叫一次 = 自主執行完整任務（截圖→思考→動作→再截圖…），直到
done / fail / 達最大步數。

- 操作對象：Xvfb 虛擬桌面（預設 :1，主人透過 noVNC 可即時看到動作）
- 每步：scrot 截圖 → 送給多模態 vision 模型（含任務+歷史+畫面）→ 模型回 JSON 指令
       → xdotool 執行（click / type / key / scroll / wait）→ 下一步
- 回傳：完整 step log、最後截圖路徑、成功/失敗結論

依賴：scrot(或ffmpeg)、xdotool、以及 vision.py 的多模態呼叫（同目錄模組）
用法（LLM 工具呼叫）：
  {"task":"在瀏覽器打開 example.com", "max_steps":10, "display":":1"}
  {"task":"...", "pause":0.8, "max_steps":15}

更新記錄:
  20260905 - 初版（路線 A）
"""

import os
import re
import sys
import json
import time
import asyncio
import hashlib
import logging
import subprocess
from datetime import datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# 讓本工具在同目錄下可 import vision（mokagi 框架已把 tools 目錄放進 sys.path）
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)
try:
    import vision as _vision  # noqa
except Exception:
    _vision = None

# ------------------------------------------------------------------
# 常數
# ------------------------------------------------------------------
WORKSPACE = os.path.expanduser("~/.mok/_tmp/gui_agent")   # 20260913 移入暫存區 _tmp，避免佔用 ~/.mok 根目錄
SHOT_DIR = os.path.join(WORKSPACE, "shots")
DEFAULT_DISPLAY = ":1"
DEFAULT_MAX_STEPS = 10
DEFAULT_PAUSE = 0.6
HARD_TIME_LIMIT = 240.0          # 整體硬性時間上限（秒）
ALLOWED_ACTIONS = {"click", "doubleclick", "rightclick", "type", "key", "scroll", "wait", "done", "fail"}

# xdotool 按鍵名對照
_KEYMAP = {
    "enter": "Return", "return": "Return", "esc": "Escape", "escape": "Escape",
    "space": "space", "tab": "Tab", "backspace": "BackSpace", "delete": "Delete",
    "del": "Delete", "insert": "Insert", "up": "Up", "down": "Down", "left": "Left",
    "right": "Right", "home": "Home", "end": "End", "pageup": "Page_Up",
    "pagedown": "Page_Down", "super": "Super_L", "cmd": "Super_L", "win": "Super_L",
    "windows": "Super_L", "alt": "Alt_L", "ctrl": "ctrl", "control": "ctrl",
    "shift": "shift", "printscreen": "Print", "pause": "Pause", "menu": "Menu",
}
for _i in range(1, 13):
    _KEYMAP[f"f{_i}"] = f"F{_i}"

PLUGIN_INFO = {
    "command": "/gui_agent",
    "icon": "🖥️",
    "handler": "handle_gui_agent",
    "description": (
        "GUI 自動化 Agent：在 Xvfb 虛擬桌面上自主執行「截圖→看圖思考→滑鼠鍵盤行動」的迴圈，"
        "直到達成任務。呼叫一次即為完整任務執行。"
    ),
    "intent_keywords": [
        ("/gui", "/gui_agent"),
        ("/桌面操作", "/gui_agent"),
        ("/gui_agent", "/gui_agent"),
    ],
    "tool_schema": {
        "name": "gui_agent",
        "description": (
            "GUI 自動化 Agent 工具：把「截圖→vision看圖→行動」迴圈包成單一工具。\n"
            "給定一個要在虛擬桌面（Xvfb :1，主人可經 noVNC 即時看到動作）上完成的任務，"
            "工具會自主迴圈：截圖 → 用多模態模型分析畫面並決定下一步 → 用 xdotool 執行"
            "（滑鼠點擊/打字/按鍵/滾動/等待）→ 再截圖…直到完成(done)、確定無法完成(fail)、"
            "或達 max_steps。\n\n"
            "【返回格式】JSON：{success, task, steps[], finished, reason, last_screenshot, elapsed}\n"
            "【何時使用】需要在桌面上點開 App、操作瀏覽器、跑指令、移動滑鼠鍵盤等實體 GUI 操作。\n"
            "【注意】這是阻塞式呼叫，會自主執行多步，通常耗時 5~90 秒；把目標寫清楚、拆小任務；"
            "step log 會包含每步動作與最後截圖路徑。"
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "task": {
                    "type": "string",
                    "description": "要在虛擬桌面上達成的任務目標（自然語言）。例如：在 Chromium 開啟 example.com 並回報頁面標題。"
                },
                "display": {
                    "type": "string",
                    "description": "X display 編號（預設 :1）。"
                },
                "max_steps": {
                    "type": "integer",
                    "description": "最大迴圈步數（1~20，預設 10）。"
                },
                "pause": {
                    "type": "number",
                    "description": "每個動作後的停頓秒數（0.1~3，預設 0.6），讓畫面/動畫更新。"
                }
            },
            "required": ["task"]
        }
    }
}

# ------------------------------------------------------------------
# 基礎工具函數
# ------------------------------------------------------------------
def _env(display: str) -> dict:
    return {**os.environ, "DISPLAY": display}

def _geom(display: str):
    """讀取虛擬桌面解析度 (w, h)"""
    try:
        out = subprocess.run(["xdotool", "getdisplaygeometry"], env=_env(display),
                             capture_output=True, text=True, timeout=10)
        parts = out.stdout.strip().split()
        if len(parts) >= 2:
            return int(parts[0]), int(parts[1])
    except Exception:
        pass
    return 1280, 800

def _xdo(display: str, *args):
    subprocess.run(["xdotool", *args], env=_env(display),
                   capture_output=True, timeout=20)

def _shot(display: str, path: str) -> str | None:
    """截圖（scrot 優先，失敗改用 ffmpeg x11grab）"""
    try:
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    except Exception:
        pass
    env = _env(display)
    try:
        r = subprocess.run(["scrot", "-o", path], env=env, capture_output=True, timeout=20)
        if r.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 2000:
            return path
    except Exception:
        pass
    try:
        w, h = _geom(display)
        r = subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "x11grab",
                            "-video_size", f"{w}x{h}", "-i", display,
                            "-frames:v", "1", path], env=env, capture_output=True, timeout=25)
        if r.returncode == 0 and os.path.exists(path) and os.path.getsize(path) > 2000:
            return path
    except Exception:
        pass
    return None

def _md5(path: str) -> str:
    try:
        with open(path, "rb") as f:
            return hashlib.md5(f.read()).hexdigest()
    except Exception:
        return ""

# ------------------------------------------------------------------
# 按鍵與動作
# ------------------------------------------------------------------
def _norm_key(raw) -> str:
    if not raw:
        return "Return"
    s = str(raw).strip()
    if not s:
        return "Return"
    parts = [p.strip() for p in re.split(r"\+|\s+", s) if p.strip()]
    mapped = []
    for p in parts:
        low = p.lower()
        if low in _KEYMAP:
            mapped.append(_KEYMAP[low])
        elif len(p) == 1:
            mapped.append(p)
        else:
            mapped.append(low)
    return "+".join(mapped)

async def _exec_action(display: str, act: dict, w: int, h: int, pause: float) -> str:
    """執行單一動作，回傳備註文字（供寫入 step log）"""
    a = str(act.get("action", "")).lower()
    if a not in ALLOWED_ACTIONS:
        return f"未知動作: {a}"
    x, y = act.get("x"), act.get("y")
    if isinstance(x, (int, float)):
        x = max(0, min(int(round(x)), w - 1))
    if isinstance(y, (int, float)):
        y = max(0, min(int(round(y)), h - 1))

    note = ""
    if a == "click":
        if x is None or y is None:
            return "click 缺少 x/y"
        _xdo(display, "mousemove", str(x), str(y), "click", "1")
        note = f"click ({x},{y})"
    elif a == "doubleclick":
        if x is None or y is None:
            return "doubleclick 缺少 x/y"
        _xdo(display, "mousemove", str(x), str(y), "click", "--repeat", "2", "--delay", "80", "1")
        note = f"doubleclick ({x},{y})"
    elif a == "rightclick":
        if x is None or y is None:
            return "rightclick 缺少 x/y"
        _xdo(display, "mousemove", str(x), str(y), "click", "3")
        note = f"rightclick ({x},{y})"
    elif a == "type":
        text = str(act.get("text") or "")[:500]
        if not text:
            return "type 缺少 text"
        _xdo(display, "type", "--delay", "15", text)
        note = f"type: {text[:60]}"
        if any(ord(c) > 127 for c in text):
            note += "（含非 ASCII，可能無法完整輸入）"
    elif a == "key":
        k = _norm_key(act.get("key"))
        _xdo(display, "key", k)
        note = f"key: {k}"
    elif a == "scroll":
        direction = str(act.get("direction") or "down").lower()
        button = "5" if direction == "down" else "4"
        if x is not None and y is not None:
            _xdo(display, "mousemove", str(x), str(y))
        _xdo(display, "click", "--repeat", "4", "--delay", "40", button)
        note = f"scroll {direction}"
    elif a == "wait":
        secs = max(0.2, min(float(act.get("seconds") or 1), 6))
        await asyncio.sleep(secs)
        note = f"wait {secs}s"
    elif a in ("done", "fail"):
        note = f"{a}: {act.get('result') or ''}"

    if pause > 0:
        await asyncio.sleep(pause)
    return note

# ------------------------------------------------------------------
# 提示詞與回覆解析
# ------------------------------------------------------------------
def _build_prompt(task: str, step: int, w: int, h: int, display: str,
                  history: list, same: bool, unchanged: int) -> str:
    L = []
    L.append("你是 GUI 自動化 Agent，正在操作一台 Xvfb 虛擬桌面。")
    L.append(f"桌面代號 {display}，畫面解析度 {w}x{h} 像素。你看到的截圖就是即時畫面；圖中座標 = 實際像素座標。")
    L.append("")
    L.append(f"【任務目標】{task}")
    L.append("")
    if history:
        L.append("【目前已做過的操作】(愈新愈重要)")
        for hh in history[-8:]:
            desc = f"{hh.get('action','')}"
            note = hh.get("note") or ""
            L.append(f"- 步驟{hh['n']}: 觀察「{(hh.get('thought') or '')[:100]}」→ {desc}{('｜' + note) if note else ''}")
        L.append("")
    if same and unchanged >= 1:
        L.append(f"⚠️ 畫面與上一步幾乎相同（已連續 {unchanged + 1} 步沒變化）。請改變策略：改用鍵盤、雙擊、滾動、先點別處或等待更久；若真的卡住，請用 fail 回報。")
        L.append("")
    L.append("請觀察目前畫面，決定『下一步的單一動作』。只輸出一個 JSON 物件，不要 code fence、不要任何其他文字：")
    L.append('{"thought":"...","action":"click|doubleclick|rightclick|type|key|scroll|wait|done|fail","x":0,"y":0,"text":"...","key":"Return","direction":"down","seconds":1,"result":"..."}')
    L.append("")
    L.append("動作規則：")
    L.append("- click/doubleclick/rightclick 需給 x,y（目標中心點，0~W、0~H）。")
    L.append("- 打字前通常先 click 輸入框取得焦點，下一步再 type；type 只支援 ASCII（英文/數字/符號），不要輸出中文。")
    L.append("- key 用於 Enter/Tab/Escape/方向鍵/F5 重新整理/alt+Tab/ctrl+l/ctrl+w 等（xdotool 風格，組合鍵用 +）。")
    L.append("- scroll 給 direction=up/down（可加 x,y 指向要滾動的區域）。")
    L.append("- wait 是等待幾秒（0.5~3）讓畫面載入/動畫完成。")
    L.append("- 一次只做一個小動作；畫面有變化後再繼續下一步。")
    L.append("- 真的看到任務達成的結果（不是猜測）才用 done，並在 result 寫結論；連續嘗試仍無法完成才用 fail。")
    return "\n".join(L)

def _fix_prompt(raw: str) -> str:
    return (
        "你剛才的回覆無法被解析成 JSON：\n" + (raw[:800] if raw else "(空白)") +
        "\n\n請只輸出『單一合法 JSON 物件』（不要 code fence、不要註解、不要其他文字），欄位格式如前一次說明。"
    )

def _parse_decision(txt: str) -> dict | None:
    if not txt:
        return None
    t = txt.strip()
    t = re.sub(r"^```(?:json)?\s*", "", t)
    t = re.sub(r"\s*```$", "", t)
    m = re.search(r"\{.*\}", t, re.S)
    if not m:
        return None
    try:
        return json.loads(m.group(0))
    except Exception:
        return None

# ------------------------------------------------------------------
# vision（多模態）呼叫：含 model 輪換重試
# ------------------------------------------------------------------
async def _ask_vision(agent_config, img_path: str, prompt: str) -> str:
    """回傳模型原始文字；失敗回傳 {"success":false,...} json 字串"""
    if _vision is None:
        return json.dumps({"success": False, "error": "無法 import vision 模組"})
    try:
        img_b64, mime = _vision._get_image_base64(img_path)
    except Exception as e:
        return json.dumps({"success": False, "error": f"讀圖失敗: {e}"})
    order = list(getattr(_vision, "VISION_MODEL_PREFERENCE", []) or [])
    attempts = [None] + order[:4]          # None = 用預設最佳模型
    last_err = ""
    for pref in attempts:
        try:
            cfg = _vision._get_vision_model_config(agent_config or {}, pref)
            if not cfg or not cfg.get("api_url"):
                continue
            txt = await asyncio.wait_for(
                _vision._call_vision_model(img_b64, mime, prompt, cfg), timeout=120)
            if not txt:
                continue
            try:
                j = json.loads(txt)
                if isinstance(j, dict) and j.get("success") is False:
                    last_err = j.get("error") or txt
                    continue
            except Exception:
                pass
            return txt
        except asyncio.TimeoutError:
            last_err = "vision 呼叫逾時"
        except Exception as e:
            last_err = str(e)
    return json.dumps({"success": False, "error": last_err or "沒有可用 vision 模型"})

# ------------------------------------------------------------------
# 參數解析 / config 兜底
# ------------------------------------------------------------------
def _parse_args(args) -> dict:
    if isinstance(args, dict):
        return dict(args)
    if isinstance(args, str):
        s = args.strip()
        if not s:
            return {}
        try:
            return json.loads(s)
        except Exception:
            return {"task": s}   # 整串當任務
    return {}

def _load_agent_config() -> dict:
    """兜底：找不到 agent_config 時，從目前 agent 的 .卓 設定檔載入 model keys"""
    cfg = {}
    name = os.environ.get("MOK_AGENT_NAME") or "卓"
    f = os.path.expanduser(f"~/.mok/agent/{name}/.{name}")
    if not os.path.exists(f):
        f = os.path.expanduser("~/.mok/agent/卓/.卓")
    try:
        with open(f, "r", encoding="utf-8", errors="ignore") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                cfg[k.strip()] = v.strip()
    except Exception:
        pass
    return cfg

# ------------------------------------------------------------------
# 主迴圈
# ------------------------------------------------------------------
async def handle_gui_agent(args, chat_id="web", agent_config=None):
    t0 = time.time()
    try:
        p = _parse_args(args)
    except Exception as e:
        return json.dumps({"success": False, "error": f"參數解析失敗: {e}"}, ensure_ascii=False)

    task = str(p.get("task") or p.get("goal") or "").strip()
    if not task:
        return json.dumps({"success": False, "error": "缺少 task（要達成的桌面任務目標）"}, ensure_ascii=False)

    display = str(p.get("display") or DEFAULT_DISPLAY)
    try:
        max_steps = max(1, min(int(p.get("max_steps") or DEFAULT_MAX_STEPS), 20))
    except Exception:
        max_steps = DEFAULT_MAX_STEPS
    try:
        pause = max(0.1, min(float(p.get("pause") or DEFAULT_PAUSE), 3.0))
    except Exception:
        pause = DEFAULT_PAUSE

    if agent_config is None or not isinstance(agent_config, dict):
        agent_config = _load_agent_config()

    os.makedirs(SHOT_DIR, exist_ok=True)
    run_dir = os.path.join(SHOT_DIR, datetime.now().strftime("%Y%m%d_%H%M%S"))
    os.makedirs(run_dir, exist_ok=True)

    w, h = _geom(display)
    history = []
    prev_md5, unchanged = "", 0
    finished, final_action, final_reason = False, "", ""
    steps = []
    last_shot = ""

    for step in range(1, max_steps + 1):
        if time.time() - t0 > HARD_TIME_LIMIT:
            final_reason = "超過整體時間上限，強制結束"
            break
        # 1) 截圖
        shot_path = os.path.join(run_dir, f"step{step:02d}.png")
        sp = _shot(display, shot_path)
        if not sp:
            final_reason = f"步驟{step} 截圖失敗"
            break
        last_shot = sp
        md5 = _md5(sp)
        same = bool(md5 and md5 == prev_md5 and prev_md5)
        unchanged = unchanged + 1 if same else 0
        prev_md5 = md5

        # 2) vision 看圖 → 決定下一步
        prompt = _build_prompt(task, step, w, h, display, history, same, unchanged)
        raw = await _ask_vision(agent_config, sp, prompt)
        decision = _parse_decision(raw)
        if decision is None:
            # 一次修正：把無法解析的回覆再丟回去，只要 JSON
            fix_raw = await _ask_vision(agent_config, sp, _fix_prompt(raw))
            decision = _parse_decision(fix_raw)
        if decision is None:
            final_reason = f"步驟{step}: vision 回覆無法解析為 JSON（{raw[:120]}）"
            break

        action = str(decision.get("action") or "").lower()
        thought = str(decision.get("thought") or "")[:200]
        # 3) 行動
        if action in ("done", "fail"):
            final_action = action
            final_reason = str(decision.get("result") or ("任務完成" if action == "done" else "無法完成"))
            steps.append({"step": step, "action": action, "thought": thought,
                          "note": final_reason, "screenshot": sp})
            finished = True
            break

        if action not in ALLOWED_ACTIONS:
            final_reason = f"步驟{step}: 模型給了非法動作 {action!r}"
            break

        note = await _exec_action(display, decision, w, h, pause)
        history.append({"n": step, "action": f"{action} {note}", "thought": thought, "note": note})
        steps.append({"step": step, "action": action,
                      "thought": thought, "note": note, "screenshot": sp})

        if step >= max_steps:
            final_reason = "已達 max_steps 上限"

    result = {
        "success": finished and final_action == "done",
        "task": task,
        "display": display,
        "resolution": f"{w}x{h}",
        "finished": finished,
        "final_action": final_action,
        "reason": final_reason,
        "step_count": len(steps),
        "steps": steps[-15:],
        "last_screenshot": last_shot,
        "screenshots_dir": run_dir,
        "elapsed_sec": round(time.time() - t0, 1),
    }
    return json.dumps(result, ensure_ascii=False)

# ------------------------------------------------------------------
# CLI 測試入口：python3 gui_agent.py "任務" [max_steps] [display]
# ------------------------------------------------------------------
async def _cli_main():
    task = sys.argv[1] if len(sys.argv) > 1 else "回報目前桌面狀態"
    max_steps = int(sys.argv[2]) if len(sys.argv) > 2 else 4
    display = sys.argv[3] if len(sys.argv) > 3 else DEFAULT_DISPLAY
    cfg = _load_agent_config()
    out = await handle_gui_agent({"task": task, "max_steps": max_steps, "display": display},
                                 "cli", cfg)
    print(out)

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    asyncio.run(_cli_main())
