# API 敏感端點閘 v1（2026-10-03）

**作者**：API平台工程師　**掛載**：mok_web 保丁載入器自動掃描（目錄名 9 個 ｚ，最後載入）

## 問題：為什麼這些端點會「bypass」
既有的閘門分兩類，相加後留下一個大洞：

- `ｚｚ機密閘_20260925`：黑名單式，只擋 ADMIN_ONLY 清單，最後一行 `return None`＝放行。
- `z三級權限閘_202609131430` 與 `ｚｚｚｚｚｚｚｚ公開白名單閘_20261003`：把「/api」整段併入 ALWAYS_OPEN，註解寫「各後台自行判斷」。

→ 結果：**沒被列進黑名單的 API 端點＝預設放行**，連未登入訪客都能打。

## 本閘補上的規則
| 層級 | 端點 | 規則 |
|---|---|---|
| 系統級 | `set_model`、`agent_voice`、`agent_pets`、`create_agent`、`clear_all_jobs`、`pm2panel` | 非 admin 回 403 |
| 需登入 | `tools`、`create_folder` | 未登入回 403 |
| 方案白名單 | `set_env` | agent 必須在呼叫者方案白名單內（訪客視為 free 方案） |
| 輸出遮蔽 | `models` | 非 admin 時拿掉 `url` 欄位（上游／內網端點不外洩） |

（上表端點皆位於 mok_web 的 API 命名空間下。）

## 驗證
`smoke_test_gate.py`（假 Flask app 離線跑）11 個案例全部符合預期：
訪客打 `tools`／`agent_pets`／`set_model`／`agent_voice`／`create_folder` 回 403；
`models` 對非 admin 無 url；`set_env` 免費 agent 放行、收費 agent 擋下；admin 全部正常。

## 停用
目錄改名加底線開頭（`_ｚｚｚｚｚｚｚｚｚＡＰＩ權限閘_20261003`）後，下次服務載入即失效。
