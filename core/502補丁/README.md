# 502 補丁（patch_502）

> 當 mokagi 出現任何 **502**，首頁 `/` 會自動轉顯示救援頁
> `html/jobs/502/index.html`：列出【最近修改的文件】（只掃 `core/` + `frontends/`），
> 並提供【一鍵還原】——走 `/backup` 系統（`~/.mok/backups/*.tar.gz`）或該檔同層 `.bak*`。
>
> **不使用** 直接改寫 `mok_web.py` 的既有路由／邏輯。
> 完全比照 `core/暫停補丁` 的「加掛式補丁」作法。

## 檔案
| 檔案 | 作用 |
|---|---|
| `patch_502.py` | 後端：狀態機 + Flask 路由 + 檔案掃描 + 還原 |
| `__init__.py` | 套件標記 |
| `README.md` | 本文件 |
| `html/jobs/502/index.html` | 救援頁（獨立靜態頁，用 `/api/502/*` 取資料） |

## 端點
| Method | Path | 作用 |
|---|---|---|
| GET  | `/502` | 手動打開救援頁 |
| GET  | `/api/502/status` | 目前 502 狀態 + 完整備份清單 |
| GET  | `/api/502/recent?n=10` | 最近修改檔案（含各自同層 `.bak*`、完整備份清單） |
| POST | `/api/502/flag?reason=...` | 手動標記 502（測試用） |
| POST | `/api/502/clear` | 解除 502 狀態 |
| POST | `/api/502/restore` | `{path, source}`：`source="latest"` 從最新完整備份還原；否則為同層 `.bak` 檔名 |

## 行為
1. **偵測**：`after_request` 看到任何回應狀態 `502` → 標記 broken（記路徑、時間、累計次數）。
2. **轉頁**：broken 時，`before_request` 攔截 `/`、`/index.html`、`/home` → 改顯示救援頁。
   - 想看真正的首頁：加 `?real=1`。
   - `errorhandler(502)`：瀏覽器（Accept: text/html）顯示救援頁；API/JSON 維持原 JSON，不打斷前台。
3. **還原安全**：還原前先把現行檔另存 `<檔名>.bak_502restore_<時間>`，可再退回。
4. **範圍限制**：只能還原 `core/`、`frontends/` 底下、且已存在的檔案（防目錄穿越）。

## 接線（僅動 `frontends/mok_web.py` 一處，最省侵入）
在既有 `暫停補丁` 接線段之後（`@app.context_processor` 之前）加入：

```python
# ===== 502 補丁（patch_502）：502 時首頁轉救援頁，不改原有路由 =====
try:
    import sys as _s502
    import os as _o502
    _p502_dir = _o502.path.join(_o502.path.dirname(_o502.path.dirname(_o502.path.abspath(__file__))), 'core', '502補丁')
    if _o502.path.isdir(_p502_dir) and _p502_dir not in _s502.path:
        _s502.path.insert(0, _p502_dir)
    import patch_502
    patch_502.register(app)
except Exception as _p502_e:
    print('[patch_502] load failed:', _p502_e)
```

載入失敗只印一行，**不影響**原本啟動。

## 解除 / 卸載
- 暫時停用：註解掉上面那段接線即可（或 `POST /api/502/clear` 只解除當下狀態）。
- 完全移除：刪除 `core/502補丁/` 與 `html/jobs/502/`，並移除接線。

## 已知限制
- 若 **Flask 進程本身已死**（5000 埠無人服務），任何 `~/.mok/html/` 的頁面都無法被送出，
  此時 502 由 Cloudflare／nginx 邊緣產生，本補丁幫不上；
  該情境需靠 `nginx error_page 502` 或獨立的 guardian（如 `core/warden`）覆蓋。
- 從完整備份還原需解壓 2GB 級 tar.gz，約 20～60 秒。
