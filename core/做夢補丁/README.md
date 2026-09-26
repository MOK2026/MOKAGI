# 🌙 做夢補丁（EXP 反思）

讓 agent 定時「做夢」：讀自己的 `logs/` 與 `soul/`，沉澱經驗、追加到 `soul/EXP.md`。

## 檔案

| 檔案 | 角色 |
|---|---|
| `core/做夢補丁/dream_core.py` | 核心邏輯（可獨立 CLI 執行） |
| `tools/dream.py` | 工具外殼：`/dream` 指令、權限閘、心跳掛載 |
| `core/做夢補丁/README.md` | 本說明 |

零侵入：不改 `mokagi.py`，靠心跳引擎自動掃描 `PLUGIN_INFO["heartbeat"]` 掛載。

## 開關（寫在 agent 設定檔 `.<agent>` 內）

| 變數 | 預設 | 說明 |
|---|---|---|
| `MOK_dream_EXP` | （無） | **必要**。`1` = 啟用做夢；沒設 = 該 agent 完全不做夢 |
| `MOK_dream_interval_h` | 24 | 自節流：每個 agent 幾小時最多做一次 |
| `MOK_dream_max_lines` | 200 | `EXP.md` 行數上限，超過自動歸檔 |
| `MOK_dream_batch_chars` | 12000 | 單次 LLM 內容上限（分批） |
| `MOK_dream_gap_s` | 90 | 全系統兩個 agent 之間最小間隔（分批，避免打爆 API） |
| `MOK_dream_consolidate` | 1 | 是否做「整理 EXP.md」（濃縮） |
| `MOK_dream_consolidate_min_lines` | 40 | EXP.md 少於此行數就不整理 |
| `MOK_dream_max_log_files` | 30 | 一次最多讀幾份新 log |
| `MOK_dream_log_chars` | 6000 | 單份 log 擷取字數 |
| `MOK_dream_soul_chars` | 4000 | 單份 soul 檔擷取字數 |
| `MOK_dream_lock_state` | 0 | 1 = 連工具私有狀態檔也走進化鎖 |

## 觸發條件（全部通過才做夢）

1. 該 agent 設定檔有 `MOK_dream_EXP=1`
2. `logs/` 最後一條與 `soul/.dream.json` 記錄的 `last_log` 不同（有新的 log）
3. 距上次做夢已超過 `MOK_dream_interval_h` 小時
4. 距離全系統上一個做夢已超過 `MOK_dream_gap_s` 秒（心跳模式下）

## 流程

```
權限 → 節流＋新 logs 檢查 → 分批讀（soul 全部 .md，排除 EXP.md ＋ 新 logs）
→ LLM 生成 3 段式（學到什麼 / 踩了什麼坑 / 下次怎麼做）
→ 進化鎖 start → append soul/EXP.md → 進化鎖 done
→ 更新 soul/.dream.json（last_log / last_dream_at）
→ 整理：讀新 EXP.md → LLM 濃縮 → 進化鎖 → 覆寫
→ 超過 MOK_dream_max_lines → 歸檔到 soul/EXP_archive/EXP_<YYYY-MM>.md
```

## 鐵律

1. **只寫** `soul/EXP.md` 與 `soul/EXP_archive/`；**絕不碰** `agent.md` / `user.md`。
2. 寫 `EXP.md` / 歸檔前，一律先走**進化鎖**（`skill/進化/editlock.py start` → 寫 → `done`）。
   被別人鎖住就跳過該輪，記在 `代號 .dream.json` 的狀態，不硬闖。
3. 工具私有狀態檔（`soul/.dream.json`、`core/做夢補丁/.dream_scan.json`）預設直接原子寫，
   不進登記簿（與 `backups/` 同類）；要嚴格全登記就設 `MOK_dream_lock_state=1`。

## 手動使用

```bash
# 只看計畫，不呼叫 LLM、不寫檔（建議先跑這個）
python3 core/做夢補丁/dream_core.py --agent 衍 --dry-run

# 忽略節流，真的做一次（需已啟用 MOK_dream_EXP=1）
python3 core/做夢補丁/dream_core.py --agent 衍 --force

# 掃描所有已啟用 agent（limit 分批，一次最多 N 個）
python3 core/做夢補丁/dream_core.py --all --limit 2

# 測試用：忽略權限開關
python3 core/做夢補丁/dream_core.py --agent 衍 --ignore-permission --dry-run
```

對話中也可用工具：`/dream status`、`/dream dry`、`/dream run`。

## 成本

每個 agent 每天約 1 次生成 ＋（可選）1 次整理 ≈ 2 次小型 LLM 呼叫。
100 個 agent 全開 ≈ 200 次／天，靠 `gap_s` 分批攤平，不會同時打爆 API。

## 生效方式（重要）

`tools/dream.py` 是新增工具，但 `tool_handler.get_tools()` 只回傳**啟動時快取的**工具表，
所以修改後需要重新載入才會掛上心跳：

- 偏好：在 Telegram 對 bot 送 `/reload`（`reload_tools()` 會重新掃 `tools/`）
- 或由主人手動重啟 mokagi（**衍不主動重啟任何 core 進程**）

## 心跳是「非阻塞」的

心跳引擎是**循序** `await` 每個工具的 handler。做夢一次可能跑好幾分鐘（多次 LLM），
若直接 await 會拖住整個心跳（連 `job.py` 的心跳也停擺）。因此：

```
heartbeat_handler()
  → 同步 claim_slot()：佔用全系統 gap 時段（保證一輪只放行一個 agent）
  → threading.Thread(daemon).start()：真正做夢丟到背景執行緒
```

`_busy` 全域鎖確保同時只有一個做夢在跑。

