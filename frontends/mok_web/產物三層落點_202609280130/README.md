# 產物三層落點 (202609280130)

## 目的
把「依身分決定輸出目錄」的權威來源接到前端，補上三層存放規則的缺口。

## 三層規則（權威來源：`core/output_router.py`）
| 層 | 身分 | 落點 |
|---|---|---|
| 1 | 未登入 / 訪客（`guest:*` / `web_guest_*`） | `~/.mok/_tmp/anon/<sid>/` |
| 2 | 已登入會員（`member.db.users`） | `~/.mok/agent/<會員帳號>/` |
| 3 | owner / admin | `~/.mok/agent/<agent房間>/jobs/<jobs名｜當日日期>/` |

## 本補丁做什麼
- 核心 `mokagi.process_message()` 已新增 `output_dir / anon_sid / output_job` 參數並自動推導落點。
- 但 `process_message` 在 socketio 背景執行緒被呼叫，拿不到 cookie；本補丁在**請求執行緒**攔 `resolve_tenant`，記下該使用者的 `mok_anon` sid，再於包裝 `process_message` 時帶入。
- 取不到 sid 時退回 guest uuid，仍落在 `_tmp/anon/` 內（不會污染 agent 房間）。

## 影響
- `mokagi.get_system_context()` 會在系統提示注入【產出位置｜本回合所有產物一律寫這裡】區塊，並設環境變數 `MOK_OUTPUT_DIR` / `MOK_OUTPUT_ROLE`。
- 不改核心 `mok_web.py`（monkey patch）。

## 停用
把本目錄改名（前面加 `_`）後重啟即可。
