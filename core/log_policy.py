# -*- coding: utf-8 -*-
"""log_policy.py — 日誌政策唯一真相來源（做夢補丁 2026-10-04 by 衍）

把「寫入端保險 prune」與「做夢端 consume」收斂成一條線、一個刪除機制。

政策
  LOG_KEEP      = 20  寫入端硬上限（logger 每次寫新 log 時 prune 到剩最新 20 份）
  DREAM_TRIGGER = 10  累積幾份新 log 才做夢（= MOK_dream_min_new_logs 預設）
  DREAM_BATCH   = 10  一次最多讀幾份（= MOK_dream_max_log_files 預設）

因為 KEEP(20) > TRIGGER(10)，做夢永遠有窗口先把 log 讀走再刪；
正常目錄大小落在 0～19 份，不會再無聲爆掉。

軟刪（本模組唯一的刪除方式）
  所有被淘汰的 log 一律移入【系統資源回收筒】~/.mok/trash/，與 tools/trash.sh 同一套：
    * 命名 <原檔名>_<stamp>（碰撞自動加 _i）
    * 追加記錄到 ~/.mok/trash/.origin.log（格式與 trash.sh 完全相同）
    * 可用 `bash ~/.mok/tools/trash.sh restore <關鍵字>` 還原到原路徑
    * 由每日 05:30 clean_trash.sh 自動清除超過 30 天的項目
  絕不 os.remove / rm 硬刪。
"""
import os
import time

HOME = os.path.expanduser("~")
MOK = os.path.join(HOME, ".mok")

# --- 政策常數（唯一真相）---
LOG_KEEP = 20        # 寫入端硬上限
DREAM_TRIGGER = 10   # 累積幾份才做夢
DREAM_BATCH = 10     # 一次最多讀幾份

# --- 系統資源回收筒（與 tools/trash.sh 一致）---
TRASH_DIR = os.path.join(MOK, "trash")
ORIGIN_LOG = os.path.join(TRASH_DIR, ".origin.log")


def list_logs(base_dir):
    """回傳 base_dir 下所有 .md 日誌的檔名（依檔名升冪＝時間序）。"""
    try:
        names = [f for f in os.listdir(base_dir) if f.endswith(".md")]
    except OSError:
        return []
    return sorted(names)


def _log_origin(src, dest):
    """把搬移紀錄追加到 .origin.log（格式與 trash.sh 完全相同，供 restore 解析）。"""
    try:
        with open(ORIGIN_LOG, "a", encoding="utf-8") as f:
            f.write("%s  %s  ->  %s\n"
                    % (time.strftime("%Y-%m-%d %H:%M:%S"), src, dest))
    except OSError:
        pass


def _trash_one(path):
    """把單一檔案移入系統資源回收筒；回傳 True/False。"""
    if not os.path.isfile(path):
        return False
    try:
        os.makedirs(TRASH_DIR, exist_ok=True)
    except OSError:
        return False
    stamp = str(int(time.time()))
    base = os.path.basename(path.rstrip("/")) or "log"
    dest = os.path.join(TRASH_DIR, "%s_%s" % (base, stamp))
    i = 0
    while os.path.exists(dest):
        i += 1
        dest = os.path.join(TRASH_DIR, "%s_%s_%d" % (base, stamp, i))
    try:
        os.replace(path, dest)          # 同檔系（~/.mok 內）原子搬移
    except OSError:
        try:
            import shutil
            shutil.move(path, dest)     # 跨檔系備援
        except Exception:
            return False
    _log_origin(path, dest)
    return True


def trash(paths):
    """把 paths（檔案路徑清單）軟刪進資源回收筒；回傳成功搬移的原始路徑清單。"""
    moved = []
    for p in paths or []:
        if _trash_one(p):
            moved.append(p)
    return moved


def prune(base_dir, keep=LOG_KEEP):
    """寫入端保險：把超過 keep 份的最舊日誌軟刪進回收筒。回傳被搬移的清單。"""
    names = list_logs(base_dir)
    if len(names) <= keep:
        return []
    files = [os.path.join(base_dir, f) for f in names]
    try:
        files.sort(key=lambda f: os.path.getmtime(f), reverse=True)
    except OSError:
        pass
    return trash(files[keep:])


def consume(base_dir, paths):
    """做夢端：把剛吃完的 log 軟刪進回收筒。回傳被搬移的清單。"""
    return trash(paths)
