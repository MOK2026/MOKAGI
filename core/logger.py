"""
logger.py - 獨立的日誌模塊
提供統一的日誌記錄功能，用於多步任務、工具調用等場景。
自動創建日誌目錄和 Markdown 格式的日誌文件。
"""

import os, re, json
from datetime import datetime
from typing import Optional, Callable

# 清理舊日誌，只保留最新 10 條
logs_keep = 10



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
        # ===== 修改：優先使用外部傳入的 LLM 標題 =====
        if title and isinstance(title, str) and title.strip():
            safe_title = re.sub(r'[^\w\-_\.]', '_', title.strip()[:20])
        elif goal and isinstance(goal, str):
            safe_title = re.sub(r'[^\w\-_\.]', '_', goal[:10])
        else:
            safe_title = "chat"
        # ===== 結束 =====
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        self.log_path = os.path.join(base_dir, f"{timestamp}_{safe_title}.md")
        self._write_header()

        # 內部記錄是否已完成
        self._finished = False


    @staticmethod
    def _cleanup_old_logs(base_dir: str, keep: int = 10):
        """清理舊日誌文件，只保留最新 N 條 .md 日誌"""
        try:
            log_files = [os.path.join(base_dir, f) for f in os.listdir(base_dir) if f.endswith(".md")]
            if len(log_files) <= keep:
                return
            log_files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
            for old_file in log_files[keep:]:
                try:
                    os.remove(old_file)
                except OSError:
                    pass
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
        safe_new = re.sub(r'[^\w\-_\.]', '_', new_title.strip()[:20])
        dir_name = os.path.dirname(self.log_path)
        timestamp = os.path.basename(self.log_path).split("_")[0]
        new_path = os.path.join(dir_name, f"{timestamp}_{safe_new}.md")
        if new_path != self.log_path and not os.path.exists(new_path):
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
