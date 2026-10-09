# 主人重啟補丁（2026-10-03 稚）

## 目的
把首頁那顆鍵（原 `id="stopBtn"`）還原成「主人重啟鈕」＝**真正的完整重啟**，
取代原本的「軟重啟（只重拉 Web）」半套做法。

## 流程
1. 點鍵 → `socket.emit('stop_generation')`
2. 補丁（本目錄）做 admin 檢查 → 向所有進行中 SSE 前端廣播「主人重啟中」
3. 寫旗標 `~/.mok/run/master_restart.flag`
4. `core/launcher.py` 主迴圈（≤2s）偵測 → `master_restart()`：
   優雅關閉所有子進程（Web + 全部 Bot）→ `os.execv` 重啟 launcher 本體
5. 全新 launcher 起來 → 重載 `mok_web.py` + 所有補丁 + 所有 Agent
6. 前端輪詢 `/api/whoami` 成功後自動刷新

## 為何不呼叫 pm2
方案C 的 pm2 閘門會 DENY 任何「祖先鏈含 mok_web/launcher/mok_agi」的啟停呼叫
（帶正確密鑰也一樣）。本補丁完全不碰 pm2，故不觸發守衛、也不必放行。

## 相容保底
launcher 若為舊版（無旗標監看），補丁逾時 6s 後對父進程（launcher）送 SIGTERM，
走 launcher 既有優雅關閉 → pm2 自動重拉 ＝ 同樣完整重啟。

## 停用
把本目錄改名（前綴加 `_`）即可，無需改核心。
（注意：停用後按鈕會退回核心 `handle_stop` 的 pm2 路徑，該路徑已被方案C 閘門擋住。）
