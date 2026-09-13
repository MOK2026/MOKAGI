# -*- coding: utf-8 -*-
"""
[保丁] 漫劇王 操作台掛載（2026-09-10）
把 衍 Agent 的 jobs/漫劇王 Blueprint 掛進 mok_web（url_prefix=/manju）。
不改核心：只向 __main__.app 註冊 blueprint。
停用：目錄名前加 _ 即可。
"""
import sys, os

_main = sys.modules.get("__main__")
app = getattr(_main, "app", None)

ROOM = "/home/ubuntu/.mok/agent/衍/jobs/漫劇王"
if ROOM not in sys.path:
    sys.path.insert(0, ROOM)

try:
    from manju_bp import bp as _manju_bp
    if app is not None and "manju" not in getattr(app, "blueprints", {}):
        app.register_blueprint(_manju_bp, url_prefix="/manju")
        print("[漫劇王] 操作台已掛載 → http://<host>/manju/")
    else:
        print("[漫劇王] 略過（app 不存在或已掛載）")
except Exception as e:
    print("[漫劇王] 掛載失敗（不影響主服務）:", e)
