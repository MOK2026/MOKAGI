#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""等級同步巡檢.py  (2026-10-07, 凜)
==================================================
由 cron 每 10 分鐘執行一次：把 users.plan 校正到與「vip_eligible 旗標 + 餘額」一致。

規則（主人核定 2026-10-07）：
    vip = 曾成功付款(vip_eligible=1) 且 餘額 > 0
    餘額 <= 0 → vip_eligible=0，並把 plan 降回 pro
    只作用於 plan ∈ {pro, vip}；free / admin / root 一律不動。

本檔是「讀取兜底」以外的定時兜底，防漏網（例如直接改 DB、舊資料）。
單一實例鎖：避免與前一輪或彼此重疊。不重啟任何服務、不改 core。
可用環境變數 MOK_MEMBER_DB 指定別的 db（測試用）。
"""
import os
import sys
import time
import glob
import sqlite3
import fcntl

_HOME = os.path.expanduser('~/.mok')
LOCK_PATH = '/tmp/mok_vip_sync.lock'
PLANS = ('pro', 'vip')
# 祖父白名單：名單內帳號一律 vip（不受餘額／付費紀錄影響）。
_GF_EXPR = "EXISTS (SELECT 1 FROM member_vip_grandfather g WHERE g.username=users.username)"
EXPR = ("CASE WHEN " + _GF_EXPR + " THEN 'vip' "
        "WHEN vip_eligible=1 AND balance_tokens>0 THEN 'vip' ELSE 'pro' END")


def _find_db():
    env = os.environ.get('MOK_MEMBER_DB')
    if env:
        return env
    hits = sorted(glob.glob(os.path.join(_HOME, 'frontends', 'mok_web', '會員系統_*', 'member.db')))
    return hits[-1] if hits else None


def main():
    fp = open(LOCK_PATH, 'w')
    try:
        fcntl.flock(fp, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print('[等級同步] 前一輪仍在執行，本輪跳過')
        return 0

    db = _find_db()
    if not db or not os.path.exists(db):
        print('[等級同步] 找不到 member.db，跳過')
        return 1

    try:
        with sqlite3.connect(db, timeout=15) as c:
            c.execute('PRAGMA busy_timeout=8000')
            # 祖父白名單：只在「首次建立此表」當下回填一次（無付費帳本紀錄的既有 vip），
            #   之後永不自動回填 —— 否則付費戶餘額一旦歸零會被回填成祖父、永遠降不下來。
            _tbls0 = [r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
            if 'member_vip_grandfather' not in _tbls0:
                c.execute('CREATE TABLE member_vip_grandfather ('
                          "username TEXT PRIMARY KEY, reason TEXT DEFAULT '', "
                          "added_at REAL, added_by TEXT DEFAULT '凜')")
                if 'ledger' in _tbls0:
                    c.execute('INSERT OR IGNORE INTO member_vip_grandfather '
                              "(username, reason, added_at, added_by) SELECT username, "
                              "'上線前既有vip且無付費帳本紀錄', ?, '凜' FROM users WHERE plan='vip' "
                              'AND NOT EXISTS (SELECT 1 FROM ledger l WHERE l.username=users.username '
                              "AND l.delta_tokens>0 AND l.reason LIKE 'recharge%')", (time.time(),))
            cols = [r[1] for r in c.execute('PRAGMA table_info(users)').fetchall()]
            migrated = False
            if 'vip_eligible' not in cols:
                # 一次性遷移（與會員系統補丁同語意；先到者做，後到者不會重做）
                c.execute('ALTER TABLE users ADD COLUMN vip_eligible INTEGER DEFAULT 0')
                tbls = [r[0] for r in c.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'").fetchall()]
                if 'ledger' in tbls:
                    # 回填兩類：① 帳本有成功付款者 ② 現時已是 vip 者（祖父條款）；
                    # 與會員系統補丁同一語意，避免修正上線時降級既有 vip 帳號。
                    c.execute("UPDATE users SET vip_eligible=1 WHERE plan='vip' OR EXISTS ("
                              "SELECT 1 FROM ledger l WHERE l.username=users.username "
                              "AND l.delta_tokens>0 AND l.reason LIKE 'recharge%')")
                migrated = True
            c.execute('UPDATE users SET vip_eligible=0 WHERE balance_tokens<=0 AND plan IN (?,?) '
                      'AND username NOT IN (SELECT username FROM member_vip_grandfather)', PLANS)
            cur = c.execute(
                'UPDATE users SET plan=' + EXPR + ' WHERE plan IN (?,?) AND plan<>' + EXPR, PLANS)
            n = cur.rowcount or 0
            c.commit()
    except Exception as e:
        print('[等級同步] 失敗: %s' % e)
        return 1

    ts = time.strftime('%Y-%m-%d %H:%M:%S')
    if migrated:
        print('[%s] 已建立 users.vip_eligible 並完成一次性回填' % ts)
    if n:
        print('[%s] 等級同步：更新 %d 筆' % (ts, n))
    return 0


if __name__ == '__main__':
    sys.exit(main())
