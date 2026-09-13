# -*- coding: utf-8 -*-
"""
mok_web 保丁（補丁）載入器 v1.0
================================
位置: .mok/frontends/mok_web/保丁.py
作用: mok_web.py 啟動時自動掃描並載入本目錄下所有補丁，
      實現「不改核心、無限擴充」。核心檔 mok_web.py 只加一行引用。

【目錄規則】
    .mok/frontends/mok_web/
    ├── 保丁.py                          ← 本載入器（勿刪勿改）
    └── <補丁名>_<YYYYMMDDHHMM>/          ← 每個補丁一個子目錄
        └── 保丁.py                       ← 補丁程式碼（必需，檔名固定）
        └── README.md                     ← 補丁說明（可選）

【補丁寫法】
    補丁是獨立 Python 模組，透過 sys.modules['__main__'] 取得核心 mok_web，
    重新定義函數後覆蓋回去（monkey patch）：

        import sys
        main = sys.modules['__main__']        # 核心 mok_web 模組

        def get_file_tree(path, depth=0):     # 新實作
            ...

        main.get_file_tree = get_file_tree    # 覆蓋核心函數 ← 關鍵！

【排序】依目錄名稱排序載入，後載入者優先（可覆蓋先載入者）。
【安全】單一補丁失敗只印錯誤，不影響其他補丁與核心啟動。
【停用】把補丁目錄改名（如前面加 _）即可停用，無需改核心。
"""
import os, sys, glob, importlib.util, traceback

# ★ 定位保丁目錄：不依賴自身 __file__（被 exec 執行時 __file__ 是 mok_web.py 的路徑）。
#   一律從核心模組 __main__ 推斷：mok_web.py 所在目錄 + /mok_web
_main_mod = sys.modules.get('__main__')
_web_dir = os.path.dirname(os.path.abspath(getattr(_main_mod, '__file__', os.getcwd())))
PATCH_DIR = os.path.join(_web_dir, 'mok_web')
_PATCH_FILENAME = '保丁.py'          # 每個補丁目錄內的補丁檔名（固定）


def _load_one(patch_file, label):
    """用 importlib 載入單一補丁模組"""
    name = 'mokweb_patch_' + label
    spec = importlib.util.spec_from_file_location(name, patch_file)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    print(f"[保丁] ✅ {label} 已載入 ← {os.path.relpath(patch_file, PATCH_DIR)}")


def load_patches():
    """掃描本目錄下所有補丁子目錄並依序載入"""
    dirs = sorted([d for d in glob.glob(os.path.join(PATCH_DIR, '*/')) if os.path.isdir(d)])
    total, ok = len(dirs), 0
    for d in dirs:
        label = os.path.basename(d.rstrip(os.sep))
        if label.startswith('_'):
            print(f"[保丁] ⏸️  {label} 已停用（目錄前綴 _），跳過")
            continue
        pf = os.path.join(d, _PATCH_FILENAME)
        if not os.path.exists(pf):
            print(f"[保丁] ⚠️  {label} 缺少 {_PATCH_FILENAME}，跳過")
            continue
        try:
            _load_one(pf, label)
            ok += 1
        except Exception as e:
            print(f"[保丁] ❌ {label} 載入失敗: {e}")
            traceback.print_exc()
    print(f"[保丁] 掃描完畢：{ok}/{total} 個補丁成功載入")
    return ok


# 被 mok_web.py 以 exec 方式引用時自動載入。
# ★ 注意：exec 環境的 __name__ 是 '__main__'，不能用 if __name__ 判斷，直接執行。
_PATCHES_LOADED = globals().get('_PATCHES_LOADED', False)
if not _PATCHES_LOADED:
    globals()['_PATCHES_LOADED'] = True
    load_patches()
