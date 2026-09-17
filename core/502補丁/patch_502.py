# -*- coding: utf-8 -*-
"""
502 補丁 (patch_502)
------------------------------------------------------------
目標
----
當 mokagi 出現任何 502（本體回應 502、上游代理 502）時，
把首頁「/」轉顯示成救援頁 html/jobs/502/index.html；
救援頁列出【最近修改的文件】（只掃 core/ + frontends/），
並提供【一鍵還原】——走 ~/.mok/backups 的 /backup 系統，
或該檔同層既有的 .bak* 版本。

設計原則（對應主人指示）
------------------------
1. 只掃 core/ + frontends/。
2. 還原來源走 /backup 系統（~/.mok/backups/*.tar.gz）。
3. 不修改 mok_web.py 任何既有路由 / 既有邏輯，
   只在 mok_web.py 加一小段 try/except 接線（register(app)），
   完全比照 core/暫停補丁 的作法；載入失敗絕不影響原本功能。
4. 所有新增檔案都放在 core/502補丁/ 與 html/jobs/502/ 之內。

偵測 502
--------
- Flask after_request：任何回應狀態 502 → 標記 broken 並記錄路徑。
- errorhandler(502)：HTML 請求顯示救援頁；API/JSON 請求維持原樣。
- 手動：POST/GET /api/502/flag?reason=...  模擬；/api/502/clear 解除。

安全
----
- 還原前，先把「現行檔案」另存 <檔名>.bak_502restore_<時間>，可再還原回去。
- 只允許還原 core/ 與 frontends/ 底下、且已存在的檔案（防目錄穿越）。
- 全程 try/except，任何例外都不影響主站。
"""
import os
import time
import shutil
import tarfile
import threading
import datetime as _dt

MOK = os.path.expanduser("~/.mok")
SCAN_DIRS = [os.path.join(MOK, "core"), os.path.join(MOK, "frontends")]
BACKUP_DIR = os.path.join(MOK, "backups")
TEMPLATE_502 = "jobs/502/index.html"
# 502 鎖首頁的自動解除秒數（逾時無人就自動放行，避免首頁被永久鎖住）
AUTO_TTL = int(os.environ.get("PATCH502_TTL", "600"))

_SKIP_DIRS = {"__pycache__"}
_SKIP_EXT = (".pyc", ".pyo", ".log")

_lock = threading.Lock()
_state = {
    "broken": False,
    "since": None,
    "since_ts": None,
    "last_reason": "",
    "last_path": "",
    "count": 0,
    "history": [],
}


def _now():
    return _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def flag_502(reason="", path=""):
    """標記系統進入 502 狀態。"""
    with _lock:
        _state["broken"] = True
        _state["since"] = _state["since"] or _now()
        _state["since_ts"] = _state["since_ts"] or time.time()
        _state["count"] += 1
        _state["last_reason"] = str(reason)[:300]
        _state["last_path"] = str(path)[:300]
        _state["history"].insert(0, {"t": _now(), "reason": _state["last_reason"], "path": _state["last_path"]})
        del _state["history"][20:]
        return dict(_state)


def clear_502():
    with _lock:
        _state.update({"broken": False, "since": None, "since_ts": None,
                       "last_reason": "", "last_path": "", "count": 0, "history": []})
        return dict(_state)


def state_502():
    with _lock:
        return dict(_state)


def _humanize(sec):
    try:
        sec = int(sec)
    except Exception:
        return "?"
    if sec < 60:
        return "%d 秒前" % sec
    if sec < 3600:
        return "%d 分鐘前" % (sec // 60)
    if sec < 86400:
        return "%d 小時前" % (sec // 3600)
    return "%d 天前" % (sec // 86400)


def _is_skippable(name):
    if ".bak" in name:
        return True
    if name.endswith(_SKIP_EXT):
        return True
    return False


def recent_files(n=10):
    """只掃 core/ + frontends/，回傳最近修改的檔案（排除 .bak / __pycache__ / log）。"""
    items = []
    now = time.time()
    for d in SCAN_DIRS:
        if not os.path.isdir(d):
            continue
        for root, dirs, files in os.walk(d):
            dirs[:] = [x for x in dirs if x not in _SKIP_DIRS]
            for fn in files:
                if _is_skippable(fn):
                    continue
                ap = os.path.join(root, fn)
                try:
                    st = os.stat(ap)
                except OSError:
                    continue
                items.append({
                    "rel": os.path.relpath(ap, MOK).replace(os.sep, "/"),
                    "mtime": st.st_mtime,
                    "mtime_str": _dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                    "ago": _humanize(now - st.st_mtime),
                    "size": st.st_size,
                })
    items.sort(key=lambda x: x["mtime"], reverse=True)
    return items[:n]


def sibling_backups(rel_path):
    """該檔同層的 .bak* 版本（新→舊）。"""
    ap = os.path.join(MOK, rel_path)
    d = os.path.dirname(ap)
    base = os.path.basename(ap)
    out = []
    try:
        for fn in os.listdir(d):
            if fn == base or base not in fn or ".bak" not in fn:
                continue
            fp = os.path.join(d, fn)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            out.append({
                "name": fn,
                "mtime": st.st_mtime,
                "mtime_str": _dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M:%S"),
                "size": st.st_size,
            })
    except OSError:
        pass
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out


def full_backups(limit=5):
    """~/.mok/backups 內的完整備份（新→舊）。"""
    out = []
    try:
        for fn in os.listdir(BACKUP_DIR):
            if not fn.endswith(".tar.gz"):
                continue
            fp = os.path.join(BACKUP_DIR, fn)
            try:
                st = os.stat(fp)
            except OSError:
                continue
            out.append({
                "name": fn,
                "mtime": st.st_mtime,
                "mtime_str": _dt.datetime.fromtimestamp(st.st_mtime).strftime("%Y-%m-%d %H:%M"),
                "size": st.st_size,
            })
    except OSError:
        pass
    out.sort(key=lambda x: x["mtime"], reverse=True)
    return out[:limit]


def _safe_rel(rel):
    """確保 rel 位於 core/ 或 frontends/ 底下、且為已存在檔案。"""
    rel = (rel or "").replace("\\", "/").strip().lstrip("./")
    ap = os.path.abspath(os.path.join(MOK, rel))
    ok = False
    for d in SCAN_DIRS:
        ad = os.path.abspath(d)
        if ap.startswith(ad + os.sep):
            ok = True
            break
    if not ok or not os.path.isfile(ap):
        raise ValueError("不允許的路徑（僅限 core/ 與 frontends/ 下已存在的檔案）：" + str(rel))
    return ap, rel


def restore_from_sibling(rel, bak_name):
    ap, rel = _safe_rel(rel)
    src = os.path.join(os.path.dirname(ap), os.path.basename(bak_name or ""))
    if not os.path.isfile(src):
        raise ValueError("找不到備份檔：" + str(bak_name))
    safety = ap + ".bak_502restore_" + time.strftime("%Y%m%d_%H%M%S")
    shutil.copy2(ap, safety)
    shutil.copy2(src, ap)
    return {"ok": True, "file": rel, "from": os.path.basename(src),
            "safety": os.path.relpath(safety, MOK)}


def restore_from_latest_backup(rel, archive_name=None):
    ap, rel = _safe_rel(rel)
    bks = full_backups(limit=10)
    if not bks:
        raise ValueError("沒有任何完整備份")
    if archive_name:
        bks = [b for b in bks if b["name"] == archive_name] or bks
    relp = rel.replace(os.sep, "/")
    candidates = [relp, "./" + relp, "./" + relp.lstrip("./")]
    member = candidates[0]
    last_err = None
    for b in bks:
        try:
            fp = os.path.join(BACKUP_DIR, b["name"])
            with tarfile.open(fp, "r:gz") as tf:
                m = None
                for cand in candidates:
                    try:
                        m = tf.getmember(cand)
                        member = cand
                        break
                    except KeyError:
                        continue
                if m is None:
                    last_err = "%s 內找不到 %s" % (b["name"], relp)
                    continue
                f = tf.extractfile(m)
                if f is None:
                    last_err = "%s 無法讀取 %s" % (b["name"], member)
                    continue
                data = f.read()
            safety = ap + ".bak_502restore_" + time.strftime("%Y%m%d_%H%M%S")
            shutil.copy2(ap, safety)
            with open(ap, "wb") as w:
                w.write(data)
            return {"ok": True, "file": rel, "from": b["name"],
                    "safety": os.path.relpath(safety, MOK), "bytes": len(data)}
        except tarfile.TarError as e:
            last_err = "%s 讀取失敗：%s" % (b["name"], e)
            continue
    raise ValueError("在備份中找不到此檔：" + (last_err or rel))


def register(app):
    """把 502 補丁掛上 Flask app。"""
    from flask import request, jsonify, render_template

    def _render_page():
        try:
            return render_template(TEMPLATE_502, state=state_502())
        except Exception as e:
            return ("<h1>502</h1><p>mokagi 偵測到 502，但救援頁模板讀取失敗："
                    + str(e) + "</p><p>請檢查 html/jobs/502/index.html</p>"), 200

    @app.route("/502")
    def _p502_page():
        return _render_page()

    @app.route("/api/502/status")
    def _p502_status():
        s = state_502()
        s["backups"] = full_backups(limit=5)
        return jsonify(s)

    @app.route("/api/502/recent")
    def _p502_recent():
        try:
            n = max(1, min(50, int(request.args.get("n", 10))))
        except Exception:
            n = 10
        files = recent_files(n=n)
        for f in files:
            f["backups"] = sibling_backups(f["rel"])
        return jsonify({"ok": True, "files": files, "state": state_502(),
                        "backups": full_backups(limit=5)})

    @app.route("/api/502/flag", methods=["POST", "GET"])
    def _p502_flag():
        body = request.get_json(silent=True) or {}
        reason = request.args.get("reason") or body.get("reason") or "manual"
        path = request.args.get("path") or body.get("path") or ""
        return jsonify(flag_502(reason=reason, path=path))

    @app.route("/api/502/clear", methods=["POST", "GET"])
    def _p502_clear():
        return jsonify(clear_502())

    @app.route("/api/502/restore", methods=["POST"])
    def _p502_restore():
        body = request.get_json(silent=True) or {}
        rel = body.get("path") or request.form.get("path")
        source = body.get("source") or request.form.get("source") or "latest"
        archive = body.get("archive") or request.form.get("archive")
        try:
            if source == "latest":
                res = restore_from_latest_backup(rel, archive_name=archive)
            else:
                res = restore_from_sibling(rel, source)
            return jsonify(res)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 400

    @app.before_request
    def _p502_before():
        try:
            st = state_502()
            if st["broken"] and st.get("since_ts") and (time.time() - st["since_ts"]) > AUTO_TTL:
                clear_502()  # 逾時自動解除，避免首頁被永久鎖在救援頁
                st = state_502()
            if not st["broken"]:
                return None
            if request.args.get("real") in ("1", "true"):
                return None
            if (request.path or "/") in ("/", "/index.html", "/home"):
                return _render_page()
        except Exception:
            pass
        return None

    @app.after_request
    def _p502_after(resp):
        try:
            if resp.status_code == 502:
                flag_502(reason="HTTP 502 on " + str(request.path), path=request.path)
        except Exception:
            pass
        return resp

    @app.errorhandler(502)
    def _p502_err(e):
        try:
            if request.accept_mimetypes.best == "text/html":
                return _render_page()
            resp = e.get_response() if hasattr(e, "get_response") else None
            if resp is not None:
                return resp
        except Exception:
            pass
        return jsonify({"error": "502"}), 502

    print("[patch_502] 已註冊：502 時首頁轉救援頁，/502 可手動查看")
    return True
