# -*- coding: utf-8 -*-
"""
統一確認碼管理模組（admin 與 cron 共用一套）

目的：
  1. admin 的 /admin confirm 與 cron 的 /cron confirm 共用同一套確認流程（可互相確認）。
  2. 明確區分「已消耗（已執行）」「已過期」「不存在」，避免誤報含混的「無效或已過期」。
  3. 確認碼 TTL 可統一設定，且預設拉長。

TTL 設定優先序：
  環境變數 MOK_CONFIRM_TTL → 環境變數 MOK_ADMIN_CONFIRM_TTL
  → agent 設定檔 MOK_CONFIRM_TTL / MOK_ADMIN_CONFIRM_TTL → 預設 DEFAULT_TTL
  值 <= 0 表示不設時間限制。

儲存：
  - 待確認：  ~/.mok/.pending_confirm/<token>.json（跨進程 / 重啟持久化）
  - 封存：    ~/.mok/.pending_confirm/.archive.json（已消耗/已過期，保留 ARCHIVE_TTL 秒）
  - 相容舊版：載入時會一併掃描 ~/.mok/.pending_admin_confirm 與 .pending_cron_confirm。

狀態機：pending → consumed（成功執行，一次性） / expired（超時）
"""

import os
import json
import time
import hashlib
import logging

DEFAULT_TTL = 1800          # 預設確認碼有效期限：30 分鐘
ARCHIVE_TTL = 24 * 3600     # 封存保留 24 小時

_MOK_HOME = os.path.expanduser("~/.mok")
PENDING_DIR = os.path.join(_MOK_HOME, ".pending_confirm")
_ARCHIVE_FILE = os.path.join(PENDING_DIR, ".archive.json")

# 舊版目錄（僅在載入時做一次性相容匯入）
_LEGACY_DIRS = [
    os.path.join(_MOK_HOME, ".pending_admin_confirm"),
    os.path.join(_MOK_HOME, ".pending_cron_confirm"),
]

_PENDING = {}          # token -> record
_ARCHIVE = {}          # token -> {reason, at, kind, cmd, args, chat_id}
_loaded = False


# ------------------------------------------------------------------ 基礎工具

def _ensure_dirs():
    try:
        os.makedirs(PENDING_DIR, exist_ok=True)
    except Exception:
        pass


def _safe_token(token):
    return "".join(c for c in str(token) if c.isalnum() or c in "-_")[:64]


def _token_path(token):
    return os.path.join(PENDING_DIR, f"{_safe_token(token)}.json")


def get_ttl(agent_config=None):
    """回傳確認碼有效期限（秒）。<=0 表示不設時間限制。"""
    candidates = []
    for key in ("MOK_CONFIRM_TTL", "MOK_ADMIN_CONFIRM_TTL"):
        v = os.environ.get(key)
        if v not in (None, ""):
            candidates.append(v)
    if agent_config:
        try:
            for key in ("MOK_CONFIRM_TTL", "MOK_ADMIN_CONFIRM_TTL"):
                v = agent_config.get(key)
                if v not in (None, ""):
                    candidates.append(str(v))
        except Exception:
            pass
    for raw in candidates:
        try:
            return float(raw)
        except (TypeError, ValueError):
            continue
    return float(DEFAULT_TTL)


def is_expired(timestamp, ttl=None):
    if ttl is None:
        ttl = DEFAULT_TTL
    try:
        ttl = float(ttl)
    except (TypeError, ValueError):
        ttl = DEFAULT_TTL
    if ttl <= 0:
        return False
    try:
        return (time.time() - float(timestamp)) > ttl
    except (TypeError, ValueError):
        return True


def ttl_hint(ttl=None):
    if ttl is None:
        ttl = DEFAULT_TTL
    try:
        ttl = float(ttl)
    except (TypeError, ValueError):
        ttl = DEFAULT_TTL
    if ttl <= 0:
        return "不限時間"
    if ttl < 60:
        return f"{int(ttl)} 秒"
    if ttl < 3600:
        return f"{int(ttl // 60)} 分鐘"
    return f"{ttl / 3600:.1f} 小時"


def generate_token(chat_id, kind, cmd, args):
    """生成一次性確認 token（kind: admin / cron）。"""
    raw = f"{chat_id}_{kind}_{cmd}_{args}_{time.time()}_{os.urandom(4).hex()}"
    return hashlib.md5(raw.encode()).hexdigest()[:12]


# ------------------------------------------------------------------ 封存帳本

def _prune_archive():
    now = time.time()
    for tok in list(_ARCHIVE.keys()):
        try:
            if now - float(_ARCHIVE[tok].get("at", 0)) > ARCHIVE_TTL:
                del _ARCHIVE[tok]
        except Exception:
            _ARCHIVE.pop(tok, None)


def _archive(token, reason, rec):
    try:
        _ARCHIVE[token] = {
            "reason": reason,
            "at": time.time(),
            "kind": rec.get("kind"),
            "cmd": rec.get("cmd"),
            "args": rec.get("args"),
            "chat_id": str(rec.get("chat_id", "")),
        }
    except Exception:
        pass


def _save_archive():
    _prune_archive()
    _ensure_dirs()
    try:
        tmp = _ARCHIVE_FILE + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(_ARCHIVE, f, ensure_ascii=False)
        os.replace(tmp, _ARCHIVE_FILE)
    except Exception as e:
        logging.warning(f"[confirm] 寫入封存失敗: {e}")


# ------------------------------------------------------------------ 持久化

def _persist_token(token, rec):
    _ensure_dirs()
    try:
        p = _token_path(token)
        tmp = p + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rec, f, ensure_ascii=False)
        os.replace(tmp, p)
    except Exception as e:
        logging.warning(f"[confirm] 寫入待確認失敗: {e}")


def _remove_token_file(token):
    try:
        p = _token_path(token)
        if os.path.exists(p):
            os.remove(p)
    except Exception:
        pass


def _read_json(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def _load_all():
    """首次載入：讀封存 + 掃描新舊待確認目錄。"""
    global _loaded
    if _loaded:
        return
    _loaded = True
    _ensure_dirs()
    # 1) 封存
    data = _read_json(_ARCHIVE_FILE)
    if isinstance(data, dict):
        _ARCHIVE.update(data)
    _prune_archive()
    # 2) 待確認
    _refresh_pending(include_legacy=True)
    _save_archive()


def _refresh_pending(include_legacy=False):
    """重新掃描磁碟上的待確認檔（跨進程可見），並清掉已過期者。"""
    _ensure_dirs()
    ttl = get_ttl(None)
    dirs = [PENDING_DIR]
    if include_legacy:
        dirs = dirs + _LEGACY_DIRS
    for d in dirs:
        try:
            if not os.path.isdir(d):
                continue
            for fn in os.listdir(d):
                if not fn.endswith(".json") or fn.startswith("."):
                    continue
                rec = _read_json(os.path.join(d, fn))
                if not isinstance(rec, dict):
                    continue
                tok = rec.get("token") or fn[:-5]
                if tok in _PENDING:
                    continue
                if is_expired(rec.get("timestamp", 0), ttl):
                    _archive(tok, "expired", rec)
                    try:
                        os.remove(os.path.join(d, fn))
                    except Exception:
                        pass
                    continue
                if not rec.get("kind"):
                    # 舊版 admin 記錄沒有 kind 欄位 → 補上
                    rec["kind"] = "admin"
                rec.setdefault("token", tok)
                _PENDING[tok] = rec
                if d != PENDING_DIR:
                    # 匯入舊版後寫入新目錄，讓新流程可管理
                    _persist_token(tok, rec)
        except Exception as e:
            logging.warning(f"[confirm] 掃描 {d} 失敗: {e}")


def _expire_pending(ttl):
    """清除已過期的待確認（標記為 expired）。"""
    for tok in list(_PENDING.keys()):
        rec = _PENDING[tok]
        if is_expired(rec.get("timestamp", 0), ttl):
            del _PENDING[tok]
            _remove_token_file(tok)
            _archive(tok, "expired", rec)
    _save_archive()


# ------------------------------------------------------------------ 對外 API

def register(token, kind, cmd, args, chat_id, description="", extra=None):
    """登記一筆待確認命令。"""
    _load_all()
    rec = {
        "token": token,
        "kind": kind,
        "cmd": cmd,
        "args": args,
        "chat_id": str(chat_id),
        "timestamp": time.time(),
        "description": description,
    }
    if extra:
        try:
            rec.update(extra)
        except Exception:
            pass
    _PENDING[token] = rec
    _persist_token(token, rec)
    return token


def pending_dict():
    """回傳記憶體中待確認字典（by reference），供 admin 相容使用。"""
    _load_all()
    return _PENDING


def is_pending(token):
    _load_all()
    _refresh_pending()
    return token in _PENDING


def get_pending(token):
    _load_all()
    return _PENDING.get(token)


def redeem(chat_id, token, agent_config=None):
    """嘗試兌換確認碼。

    回傳 (status, payload)：
      - ("ok", record)         成功（已消耗）
      - ("expired", record)    已過期
      - ("already_used", arc)  已使用過
      - ("wrong_user", record) 非本人
      - ("not_found", None)    不存在
    """
    _load_all()
    _refresh_pending()
    ttl = get_ttl(agent_config)
    _expire_pending(ttl)

    rec = _PENDING.get(token)
    if rec is None:
        arc = _ARCHIVE.get(token)
        if arc:
            reason = arc.get("reason")
            if reason == "consumed":
                return "already_used", arc
            if reason == "expired":
                return "expired", arc
        return "not_found", None

    if str(rec.get("chat_id", "")) != str(chat_id):
        return "wrong_user", rec

    # 消耗（一次性）：先移除，再交由呼叫方執行
    del _PENDING[token]
    _remove_token_file(token)
    _archive(token, "consumed", rec)
    _save_archive()
    return "ok", rec


def status_message(status, payload, agent_config=None):
    """把 redeem 的結果轉成人類可讀訊息（非 ok 時使用）。"""
    hint = ttl_hint(get_ttl(agent_config))
    if status == "expired":
        return f"❌ 確認碼已超時（有效期限 {hint}）。請重新發送原命令。"
    if status == "already_used":
        info = ""
        if isinstance(payload, dict):
            cmd = payload.get("cmd")
            args = payload.get("args")
            if cmd:
                info = f"（{cmd}" + (f": {args}" if args else "") + "）"
        return f"⚠️ 此確認碼已使用過，對應操作已經執行{info}，請勿重複確認。"
    if status == "wrong_user":
        return "❌ 確認碼與用戶不匹配。"
    return "❌ 確認碼無效（找不到對應的待確認操作，可能已使用或從未存在）。請重新發送原命令。"
