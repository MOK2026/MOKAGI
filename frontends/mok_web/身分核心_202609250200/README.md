# 統一會員身分核心 v1（2026-09-25，作者：凜）

> 保丁（補丁）機制，不改核心；由 `mok_web/保丁.py` 自動載入。
> **需重啟 mok_web 才生效**（由 launcher 管理，請主人手動重啟）。

## 目的
把「admin / 會員」身分判定統一成**唯一真相來源**，任何前端/路徑皆可查；
並把 admin 從「程式碼寫死帳號名」改為 **member.db 驅動（名字可改、明文不入程式碼）**。

## 對應需求（照順序）
| # | 需求 | 本補丁 |
|---|------|--------|
| 1 | 統一機制分辨 admin/free/pro/vip | `resolve_identity()` + `GET /api/identity/resolve` |
| 2 | admin 名字可改、驗證不寫死程式碼 | `users.is_admin/display_name`（DB）；`POST /api/admin/identity`；密碼維持 hash |
| 3 | 全 agent 對主 admin 稱「主人」 | 包裝 `process_message`：admin 呼叫時覆寫 `MOK_ADMIN_NAME` |
| 4 | .agent 除主 admin 不可讀/修 | `/api/file` + 文件樹擋 dotfile；`replace_in_file` 擋 dotfile 寫入 |
| 7 | plans 增「可用 tool / skill」欄位 | `plans.tools / skills`（schema + 預設值） |
| 8 | plans 文案（admin/free/pro/vip 定義） | `_seed_plan_meta()` 寫入 plans.desc |
| 5/6 | 會員自有 agent（基礎） | `create_agent` 記錄 `agent_owners`；`GET /api/agent/owner/<agent>` |

## 新增/變更
### member.db
- `users` + `display_name`, `is_admin`
- `plans`  + `tools`, `skills`
- 新表 `settings(key,value)`、`agent_owners(agent, owner, created_ts)`
- 種子：admin/root → is_admin=1，display_name「主人」

### HTTP
| 方法 | 路徑 | 說明 |
|---|---|---|
| GET | `/api/identity/resolve` | 任何前端查身分（role/plan/is_admin/display_name/tenant） |
| GET | `/api/admin/identity` | （admin）列出用戶、admins、主 admin 顯示名 |
| POST | `/api/admin/identity` | （admin）`{action:'rename_main',display_name}` 或 `{action:'grant'/'revoke',username}` |
| GET | `/api/agent/owner/<agent>` | 查 agent 擁有者（admin 或 owner 本人） |
| GET | `/api/whoami` | 已改為 DB 驅動（回 role/plan/display_name） |

### 包裝
- `_is_privileged_session` / `_can_read_all` → DB 驅動（`is_admin`）
- `process_message` → 主 admin 呼叫時，全 agent 稱其 display_name（預設「主人」）
- `create_agent` → 成功後記錄擁有者
- `get_file_content`（`/api/file`）、`get_file_tree` → dotfile 保護

## 安全
- admin 判定＝`users.is_admin=1`（DB），不再靠寫死帳號名。
- 密碼仍為 hash（bcrypt/sha256）。改名只動 `display_name`，不影響登入。
- dotfile（`.agent` 等）非 admin 不可讀、不可寫。

## 停用
目錄改名加底線開頭 → 重啟 web 即停用（DB 欄位保留，不影響）。
