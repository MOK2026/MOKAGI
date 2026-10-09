"""
logger.py - 獨立的日誌模塊
提供統一的日誌記錄功能，用於多步任務、工具調用等場景。
自動創建日誌目錄和 Markdown 格式的日誌文件。
"""

import os, re, json
from datetime import datetime
from typing import Optional, Callable

# ===== 日誌政策（唯一真相：core/log_policy.py）2026-10-04 by 衍 =====
# 寫入端只做「保險」prune 到 LOG_KEEP=20；淘汰一律軟刪進系統資源回收筒（~/.mok/trash/）。
# 真正的消費者是做夢（門檻 DREAM_TRIGGER=10，夢完由 dream_core consume 剛吃掉的批次）。
try:
    from log_policy import LOG_KEEP as logs_keep, prune as _log_prune
except Exception:  # 極端情況（core 未掛上 sys.path）→ 退回安全預設，且不硬刪
    logs_keep = 20

    def _log_prune(base_dir, keep=logs_keep):
        return []



class WorkflowLogger:
    """
    日誌記錄器
    用法:
        logger = WorkflowLogger(user_id, goal)
        logger.log_step(step_num, description, tool_name, params, success, result)
        logger.log_error(error_msg)
        logger.finish(summary)

    ========== 其他工具（如 web_search.py）調用日誌 ==========
        from workflow_logger import log_info, log_error

        log_info(f"執行搜索: {query}")
        if error:
            log_error(f"搜索失敗: {error}", "web_search")

    """

    # 批次E：日誌檔名標題取「內文首 X 字」
    LOG_TITLE_CHARS = 30

    @classmethod
    def _make_log_title(cls, raw):
        """取內文首 X 字當日誌檔名標題：只取第一行、壓縮空白、清掉不安全字元。"""
        text = "" if raw is None else str(raw)
        text = text.replace("\r\n", "\n").replace("\r", "\n").split("\n")[0]
        text = re.sub(r"\s+", " ", text).strip()
        safe = re.sub(r"[^\w\-_.]", "_", text[: cls.LOG_TITLE_CHARS]).strip("_.")
        return safe or "chat"

    # ===== LLM 一句話標題（2026-09-27 衍，E1830）=====
    # 舊：檔名取 prompt 首 30 字 → 20260927_135748_id__headerTokenBalance__變成_id.md
    # 新：輕量 LLM 生一句話中文標題 → 20260927_135748_header餘額鈕改版.md
    LLM_TITLE_MAX = 14          # 標題字數上限
    LLM_TITLE_TIMEOUT = 8.0     # 秒；逾時沿用原檔名，絕不阻塞對話
    _TITLE_CACHE = {}           # goal -> title，同一目標不重複問 LLM
    # 防呆：模型不可用／上游錯誤時常回這類字眼，不可當檔名（2026-09-27 衍）
    _TITLE_BAD = ("錯誤", "失敗", "異常", "無法", "請檢查", "網絡", "超時", "逾時",
                  "error", "Error", "ERROR", "http", "HTTP", "status", "timeout")

    @classmethod
    def _safe_title(cls, text, maxlen=None):
        """把任意字串壓成安全檔名片段（只留中英數與底線）。"""
        text = "" if text is None else str(text)
        text = text.splitlines()[0] if text.strip() else ""
        text = " ".join(text.split())
        safe = "".join(ch for ch in text if (ch.isalnum() or ch == "_"))
        return safe[: (maxlen or cls.LLM_TITLE_MAX)].strip("_.") or ""

    @classmethod
    async def make_llm_title(cls, goal_text):
        """用輕量 LLM 把任務目標壓成一句話中文標題（不超過 14 字）。失敗回 None。"""
        if not goal_text or not str(goal_text).strip():
            return None
        key = " ".join(str(goal_text).split())[:200]
        if key in cls._TITLE_CACHE:
            return cls._TITLE_CACHE[key]
        try:
            import asyncio
            import mokagi
            prompt = (
                "用繁體中文幫下面這段任務取一個精簡標題，"
                f"6~{cls.LLM_TITLE_MAX} 個字，名詞優先（例：header餘額鈕改版），"
                "不要標點、不要引號、不要 emoji、不要換行。只輸出標題本身。"
                "\n\n"
                f"任務：{key[:300]}"
            )
            res = await asyncio.wait_for(
                mokagi.call_llm(prompt=prompt, user_id="system", stream=False, temperature=0.3),
                timeout=cls.LLM_TITLE_TIMEOUT,
            )
            if isinstance(res, tuple):
                res = next((x for x in res if isinstance(x, str)), "")
            raw = "" if res is None else str(res).strip()
            # 防呆：錯誤訊息／過長字串不是標題，寧可沿用舊檔名（2026-09-27 衍）
            if (not raw) or len(raw) > 60 or any(b in raw for b in cls._TITLE_BAD):
                return None
            title = cls._safe_title(raw, cls.LLM_TITLE_MAX)
            if title:
                cls._TITLE_CACHE[key] = title
                return title
        except Exception as e:
            try:
                import logging
                logging.getLogger(__name__).debug("LLM 標題生成略過: %s", e)
            except Exception:
                pass
        return None

    async def llm_retitle(self, goal_text=None):
        """把日誌檔名換成 LLM 一句話標題（保留時間戳前綴）。失敗則維持原檔名。"""
        try:
            title = await self.make_llm_title(goal_text or self.goal)
            if not title:
                return None
            new_path = os.path.join(self._base_dir, f"{self._ts_prefix}_{title}.md")
            if os.path.abspath(new_path) != os.path.abspath(self.log_path):
                os.replace(self.log_path, new_path)
                self.log_path = new_path
            return new_path
        except Exception:
            return None

    def __init__(
        self,
        user_id: str,
        goal: Optional[str] = None,
        agent_name: str = "agent",
        base_dir: Optional[str] = None,
        title: Optional[str] = None
    ):
        """
        初始化日誌記錄器，創建日誌文件。

        Args:
            user_id: 用戶標識
            goal: 任務目標
            agent_name: Agent 名稱（用於目錄結構）
            base_dir: 日誌根目錄，默認 ~/.{MOKAGI_HOME}/{agent_name}/logs
        """
        import mokagi  # 延遲導入避免循環依賴

        self.user_id = user_id
        self.goal = goal
        self.agent_name = agent_name

        if base_dir is None:
            try:
                mokagi_home = mokagi.MOKAGI_home
                # 使用傳入的 agent_name 參數，而不是全局的 MOK_AGENT_NAME
                base_dir = os.path.expanduser(
                    f"~/.{mokagi_home}/agent/{self.agent_name}/logs"
                )
            except (AttributeError, NameError):
                base_dir = os.path.expanduser("~/agent_logs")

        os.makedirs(base_dir, exist_ok=True)
        # 清理舊日誌，只保留最新 10 條
        self._cleanup_old_logs(base_dir, keep=logs_keep)
        # ===== 批次E：日誌檔名標題取「內文首 X 字」 =====
        raw_title = ""
        if title and isinstance(title, str) and title.strip():
            raw_title = title
        elif goal and isinstance(goal, str) and goal.strip():
            raw_title = goal
        safe_title = self._make_log_title(raw_title)
        # ===== 結束 =====
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        # 供 LLM 重命名用（2026-09-27 衍，E1830）
        self._base_dir = base_dir
        self._ts_prefix = timestamp
        self.log_path = os.path.join(base_dir, f"{timestamp}_{safe_title}.md")
        self._write_header()

        # 內部記錄是否已完成
        self._finished = False


    @staticmethod
    def _cleanup_old_logs(base_dir: str, keep: int = None):
        """清理舊日誌：只保留最新 N 條 .md；淘汰者『軟刪』進系統資源回收筒（不硬刪）。
        實際刪除政策集中在 core/log_policy.py，這裡只負責呼叫。"""
        try:
            if keep is None:
                keep = logs_keep
            _log_prune(base_dir, keep=keep)
        except Exception:
            pass  # 清理失敗不影響正常日誌記錄


    def _write_header(self):
        import mokagi 
        owner = mokagi._agent_config.get("MOK_ADMIN_NAME")# {owner}名稱
        """寫入日誌文件頭部"""
        with open(self.log_path, "w", encoding="utf-8") as f:
            f.write(f"# {self.agent_name} 執行日誌\n")
            f.write(f"**{owner}ID**: {self.user_id}\n")
            f.write(f"**目標**: {self.goal}\n")
            f.write(f"**開始時間**: {datetime.now().strftime('%Y%m%d %H%M%S')}\n\n")
            f.write("## 執行過程\n\n")

    def _append(self, content: str):
        """追加內容到日誌文件"""
        with open(self.log_path, "a", encoding="utf-8") as f:
            f.write(content + "\n\n")

    def log_plan(self, plan: list):
        """
        記錄任務規劃（整體步驟列表）
        Args:
            plan: 步驟列表，每個步驟為 dict，包含 tool, params, success_criteria, description
        """
        content = "### 任務規劃\n```json\n" + json.dumps(plan, ensure_ascii=False, indent=2) + "\n```"
        self._append(content)

    def log_step_start(self, step_num: int, total_steps: int, description: str, tool_name: str, params: dict):
        """記錄步驟開始"""
        content = f"#### 步驟 {step_num}/{total_steps}: {description}\n**工具**: {tool_name}\n**參數**: {json.dumps(params, ensure_ascii=False)}"
        self._append(content)

    def log_tool_result(self, attempt: int, raw_result: str, natural_result: str, success: bool = True):
        """記錄工具執行結果"""
        status = "✅" if success else "❌"
        content = f"**嘗試 {attempt}** {status}\n```\n{raw_result[:2000]}\n```\n自然化結果: {natural_result[:500]}"
        self._append(content)

    def log_step_success(self, step_num: int, description: str, result_preview: str):
        """記錄步驟成功"""
        content = f"✅ 步驟 {step_num} 成功: {description}\n結果摘要: {result_preview}"
        self._append(content)

    def log_step_failure(self, step_num: int, description: str, error: str, attempt: int = None):
        """記錄步驟失敗"""
        if attempt:
            content = f"⚠️ 步驟 {step_num} 第 {attempt} 次嘗試失敗: {error}"
        else:
            content = f"❌ 步驟 {step_num} 最終失敗: {error}\n描述: {description}"
        self._append(content)

    def log_replan(self, new_plan: list):
        """記錄重新規劃"""
        content = "### 重新規劃\n```json\n" + json.dumps(new_plan, ensure_ascii=False, indent=2) + "\n```"
        self._append(content)

    def log_error(self, error_msg: str, context: str = ""):
        """記錄錯誤"""
        content = f"### 錯誤\n**錯誤**: {error_msg}\n**上下文**: {context}"
        self._append(content)

    def log_info(self, info: str, level: str = "INFO"):
        """記錄一般信息"""
        self._append(f"### {level}\n{info}")

    def log_prompt(self, title: str, prompt: str):
        """記錄 LLM prompt（可選，用於深度調試）"""
        content = f"### {title}\n```\n{prompt}\n```"
        self._append(content)

    def log_llm_response(self, title: str, response: str):
        """記錄 LLM 響應"""
        content = f"### {title}\n```\n{response}\n```"
        self._append(content)

    def finish(self, summary: str):
        """任務完成，寫入總結"""
        if self._finished:
            return
        self._finished = True
        content = f"## 最終總結\n{summary}\n**完成時間**: {datetime.now().isoformat()}"
        self._append(content)

    def abort(self, reason: str):
        """任務中止"""
        content = f"## 任務中止\n**原因**: {reason}\n**中止時間**: {datetime.now().isoformat()}"
        self._append(content)
        self._finished = True

    def get_log_path(self) -> str:
        """返回日誌文件路徑"""
        return self.log_path

    def set_title(self, new_title: str):
        """事後更新日誌標題（重新命名檔案），用於在輸出完成後更新標題"""
        if not new_title or not isinstance(new_title, str) or not new_title.strip():
            return
        safe_new = self._make_log_title(new_title)   # 批次E：重新命名也取內文首 X 字
        dir_name = os.path.dirname(self.log_path)
        timestamp = os.path.basename(self.log_path).split("_")[0]
        new_path = os.path.join(dir_name, f"{timestamp}_{safe_new}.md")
        if new_path == self.log_path:
            return
        # 同日同標題防碰撞：20260927_xxx.md 已存在時依序加 _2、_3…
        if os.path.exists(new_path):
            _i = 2
            while os.path.exists(os.path.join(dir_name, f"{timestamp}_{safe_new}_{_i}.md")):
                _i += 1
            new_path = os.path.join(dir_name, f"{timestamp}_{safe_new}_{_i}.md")
        try:
            os.rename(self.log_path, new_path)
            self.log_path = new_path
        except OSError:
            pass

    # 兼容舊代碼的 `append_log` 函數風格
    def append_raw(self, content: str):
        """直接追加原始內容"""
        self._append(content)


# ---------- 便捷函數 ----------
_default_logger: Optional[WorkflowLogger] = None


def get_default_logger() -> Optional[WorkflowLogger]:
    """獲取當前默認的日誌器（由 set_default_logger 設置）"""
    return _default_logger


def set_default_logger(logger: WorkflowLogger):
    """設置默認日誌器，供全局使用"""
    global _default_logger
    _default_logger = logger


def log_info(msg: str):
    """使用默認日誌器記錄信息（若無則忽略）"""
    if _default_logger:
        _default_logger.log_info(msg)


def log_error(msg: str, ctx: str = ""):
    if _default_logger:
        _default_logger.log_error(msg, ctx)
