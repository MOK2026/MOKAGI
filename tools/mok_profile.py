# -*- coding: utf-8 -*-
# ------------------------------------------------------------------------------------ #
# 全系統唯一 的 Chromium 持久化 profile 推導模組（單一真相來源）
#
# 目的：任何需要 browser profile 的程式（tools 目錄下的 browser、linkedin、
#       領妹 linkedin_libs 的 li_web、li_pw、li_tool、
#       skill 下的 whatsappWeb wa_auto、upwork scripts、賺錢王 cold_call …）
#       一律呼叫本模組，不再各自重寫推導邏輯。
#
# 統一推導順序：
#   1. explicit    呼叫端明確指定（絕對路徑，或純 profile 名稱如 wa、upwork）
#   2. 環境變數     MOK_BROWSER_PROFILE_DIR
#   3. 侍女名稱     agent_config 的 MOK_AGENT_NAME、name，或環境變數 MOK_AGENT_NAME
#   4. 回退         共用舊版目錄 browser_profile2
#
# 侍女專屬 profile（browser_profiles 下的 name 子目錄）若不存在或為空，
# 會自動從 browser_profile2 複製一份種子（含既有登入狀態）。
# ------------------------------------------------------------------------------------ #
import os
import re
import shutil

MOK = os.path.join(os.path.expanduser("~"), ".mok")
PROFILES_ROOT = os.path.join(MOK, "browser_profiles")        # 各 profile 獨立設定檔根目錄
DEFAULT_PROFILE_DIR = os.path.join(MOK, "browser_profile2")  # 舊版共用目錄（回退與種子來源）
ENV_PROFILE_DIR = "MOK_BROWSER_PROFILE_DIR"
ENV_AGENT_NAME = "MOK_AGENT_NAME"
DEFAULT_PROFILE_NAME = "default"

# 這些名稱視為無有效侍女名稱，一律回退 default。
_INVALID_NAMES = ("", "?", "default", "助手", "none", "None", "null")

_COPY_IGNORE = ("lockfile", ".lock")


def safe_profile_name(name) -> str:
    """把侍女或整合名稱清洗成可安全作為目錄名的字串；無效則回傳空字串（規則與 browser 工具一致）。"""
    name = str(name or "").strip()
    if name in _INVALID_NAMES:
        return ""
    return re.sub(r"[^\w\u4e00-\u9fff.-]", "_", name).strip("._")


def agent_profile_name(agent_config=None) -> str:
    """由呼叫端侍女推導 profile 名稱；無可用名稱時回退 default。"""
    name = ""
    if isinstance(agent_config, dict):
        name = agent_config.get("MOK_AGENT_NAME") or agent_config.get("name") or ""
    if not name:
        name = os.environ.get(ENV_AGENT_NAME, "")
    return safe_profile_name(name) or DEFAULT_PROFILE_NAME


def profiles_root() -> str:
    """回傳各 profile 獨立設定檔的根目錄（browser_profiles）。"""
    return PROFILES_ROOT


def default_profile_dir() -> str:
    """回傳舊版共用 profile 目錄（browser_profile2）。"""
    return DEFAULT_PROFILE_DIR


def _is_bare_name(value: str) -> bool:
    """純名稱（不含路徑分隔）才視為 browser_profiles 下的子目錄名。"""
    value = str(value or "")
    return bool(value) and os.sep not in value


def _under_profiles_root(path: str) -> bool:
    try:
        return os.path.abspath(path).startswith(os.path.abspath(PROFILES_ROOT) + os.sep)
    except Exception:
        return False


def _ignore(_dir, names):
    return [n for n in names if n.startswith("Singleton") or n in _COPY_IGNORE]


def seed_profile(profile_dir: str) -> bool:
    """若 profile_dir 不存在或為空，從 browser_profile2 複製一份種子。回傳是否真的複製了。"""
    if not profile_dir or os.path.abspath(profile_dir) == os.path.abspath(DEFAULT_PROFILE_DIR):
        return False
    try:
        if os.path.isdir(profile_dir) and os.listdir(profile_dir):
            return False
        src = DEFAULT_PROFILE_DIR
        if not os.path.isdir(src):
            return False
        os.makedirs(os.path.dirname(profile_dir), exist_ok=True)
        shutil.copytree(src, profile_dir, dirs_exist_ok=True, ignore=_ignore)
        return True
    except Exception:
        return False


def resolve_profile_dir(explicit=None, agent_config=None, seed=True) -> str:
    """統一的 Chromium profile 目錄推導（全系統唯一入口）。

    explicit     : 呼叫端明確指定（絕對路徑，或純 profile 名稱；可為 None）
    agent_config : 呼叫端侍女設定（dict，含 MOK_AGENT_NAME）或 None
    seed         : 推導出侍女專屬 profile 且不存在時，是否自動複製種子
    """
    # 1. explicit（呼叫端明確指定）
    if explicit:
        explicit = str(explicit).strip()
        if _is_bare_name(explicit):
            if explicit == DEFAULT_PROFILE_NAME:
                return DEFAULT_PROFILE_DIR
            d = os.path.join(PROFILES_ROOT, explicit)
        else:
            d = os.path.expanduser(explicit)
        if seed and _under_profiles_root(d):
            seed_profile(d)
        return d

    # 2. 環境變數 MOK_BROWSER_PROFILE_DIR
    env_dir = (os.environ.get(ENV_PROFILE_DIR) or "").strip()
    if env_dir:
        if _is_bare_name(env_dir):
            if env_dir == DEFAULT_PROFILE_NAME:
                return DEFAULT_PROFILE_DIR
            d = os.path.join(PROFILES_ROOT, env_dir)
            if seed:
                seed_profile(d)
            return d
        return os.path.expanduser(env_dir)

    # 3. 侍女名稱（agent_config 優先，其次環境變數 MOK_AGENT_NAME）
    safe = ""
    if isinstance(agent_config, dict):
        safe = safe_profile_name(agent_config.get("MOK_AGENT_NAME")
                                 or agent_config.get("name") or "")
    if not safe:
        safe = safe_profile_name(os.environ.get(ENV_AGENT_NAME, ""))
    if safe:
        d = os.path.join(PROFILES_ROOT, safe)
        if seed:
            seed_profile(d)
        return d

    # 4. 回退共用舊版目錄
    return DEFAULT_PROFILE_DIR


# 便利別名
profile_dir = resolve_profile_dir
