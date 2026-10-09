# -*- coding: utf-8 -*-
"""
sync_public_pages.py  (2026-10-03 union 版)  原作：API平台工程師 / 修改：凜
=====================================================
用途：產生「公開白名單」 settings.public_pages，供「公開白名單閘」使用。
      預設採 **union（聯集）**，兩來源取聯集後寫入：

        來源 A：.mok/html/index.html 右側「📡 頻道 ▾」(id=channelDropdown)
                內所有貼文按鈕的 location.href。
        來源 B：免費層（公開層）＝ plans 表中 requires_login=0 的所有方案
                （目前＝ free）之 pages 欄位。→「免費層永遠以方案定義為準」。

      ⚠ 為何是聯集不是覆蓋：舊版預設只取來源 A 並整份覆蓋 public_pages，
        只要免費層方案多開了頁（例：/project/），跑一次本腳本就會被洗掉。

用法：
    python3 sync_public_pages.py            # union：頻道 + 免費層方案（預設）
    python3 sync_public_pages.py --dry      # 只顯示結果，不寫入
    python3 sync_public_pages.py --no-plan  # 只用頻道（舊行為，不建議）
    python3 sync_public_pages.py --show     # 顯示目前白名單，不改
    python3 sync_public_pages.py --set '["/","/game/"]'   # 手動指定（原樣寫入）

規則（頻道來源 A）：
    href 以 index.html 結尾  → 取其資料夾 + '/'（前綴，含子資源）
    href 帶副檔名(非html)    → 精確路徑
    其餘(無副檔名，如 game)   → 前綴 '/game/'
    一律附上首頁 '/' 與 '/index.html'
"""
import os
import re
import sys
import json
import sqlite3

HTML = '/home/ubuntu/.mok/html/index.html'
WEB_DIR = '/home/ubuntu/.mok/frontends/mok_web'


def find_db():
    for d in sorted(os.listdir(WEB_DIR)):
        p = os.path.join(WEB_DIR, d, 'member.db')
        if os.path.exists(p):
            return p
    return os.path.join(WEB_DIR, 'member.db')


def read_setting(db):
    try:
        c = sqlite3.connect(db, timeout=3)
        try:
            r = c.execute("SELECT value FROM settings WHERE key='public_pages'").fetchone()
            return r[0] if r else None
        finally:
            c.close()
    except Exception:
        return None


def write_setting(db, entries):
    from urllib.parse import unquote as _uq
    entries = [x for x in (_uq(str(e or '')).strip() for e in entries) if x]
    c = sqlite3.connect(db, timeout=5)
    try:
        c.execute("CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT)")
        c.execute("INSERT OR REPLACE INTO settings(key,value) VALUES('public_pages',?)",
                  (json.dumps(entries, ensure_ascii=False),))
        c.commit()
    finally:
        c.close()


def _to_list(v):
    if v is None:
        return []
    if isinstance(v, str):
        try:
            v = json.loads(v)
        except Exception:
            v = [v]
    if not isinstance(v, list):
        return []
    return v


def parse_plan_pages(db):
    """來源 B：免費層（公開層）＝ plans 表 requires_login=0 的所有方案 pages。"""
    out = []
    try:
        c = sqlite3.connect(db, timeout=3)
        try:
            rows = c.execute("SELECT pages FROM plans "
                             "WHERE COALESCE(requires_login,1)=0").fetchall()
        finally:
            c.close()
    except Exception as e:
        print('讀不到 plans 表（免費層略過）:', e)
        return []
    for (pages,) in rows:
        for x in _to_list(pages):
            x = str(x).strip()
            if not x or x == '*':   # '*' 是「全部」語意，不適合展開成白名單，略過
                continue
            out.append(x)
    return out


def parse_channels():
    try:
        h = open(HTML, encoding='utf-8').read()
    except Exception as e:
        print('讀不到 index.html:', e)
        return []
    i = h.find('id="channelDropdown"')
    if i < 0:
        print('index.html 找不到 channelDropdown')
        return []
    seg = h[i:i + 4000]
    hrefs = re.findall(r"location\.href\s*=\s*['\"]([^'\"]+)['\"]", seg)
    out = []
    for raw in hrefs:
        u = raw.strip()
        if not u or u.startswith('http') or u.startswith('//') or u.startswith('javascript') or u.startswith('#'):
            continue
        u = '/' + u.lstrip('/')
        last = u.rsplit('/', 1)[-1]
        if last.endswith('index.html'):
            e = u.rsplit('/', 1)[0] + '/'
        elif '.' in last:
            e = u
        else:
            e = u.rstrip('/') + '/'
        if e not in out:
            out.append(e)
    return out


def main():
    db = find_db()
    args = sys.argv[1:]
    if '--show' in args:
        v = read_setting(db)
        print('DB:', db)
        print('目前 public_pages =', v if v else '(未設定，將用內建預設)')
        return
    if '--set' in args:
        try:
            entries = json.loads(args[args.index('--set') + 1])
        except Exception as e:
            print('--set 參數需為 JSON 陣列:', e)
            return
        if not isinstance(entries, list):
            print('--set 參數需為 JSON 陣列')
            return
        src = '手動 --set'
    else:
        chans = parse_channels()
        # 2026-10-08：/chat 為聊天頁正式入口（/、/index.html 皆 301 收斂至此），
        # 一併列入預設白名單，避免日後重跑本腳本把訪客入口沖掉。
        _ALWAYS = ['/', '/index.html', '/chat']
        entries = _ALWAYS + [c for c in chans if c not in tuple(_ALWAYS)]
        if '--no-plan' in args:
            src = '僅頻道(舊行為)'
            print('（--no-plan：只用頻道，舊行為）')
        else:
            plan_pages = parse_plan_pages(db)
            entries += plan_pages
            src = 'union(頻道 %d + 免費層方案 %d)' % (len(entries) - len(plan_pages), len(plan_pages))
    # 正規化（unquote）＋去重保序：
    # 後台／網址複製貼上常帶來 URL 編碼（例：/report/%E6%98%A5/…），先解碼再入庫，
    # 讓白名單與閘門比對（request.path 已解碼）一致。
    from urllib.parse import unquote as _uq
    seen, _out = set(), []
    for _e in entries:
        _e = _uq(str(_e or '')).strip()
        if _e and _e not in seen:
            seen.add(_e)
            _out.append(_e)
    entries = _out
    if '--dry' in args:
        print('[dry-run] 未寫入 DB')
        print('DB:', db)
        print('擬寫入 public_pages (%d 條) [%s]:' % (len(entries), src))
        for e in entries:
            print('   ', e)
        return
    write_setting(db, entries)
    print('DB:', db)
    print('已寫入 public_pages (%d 條) [%s]:' % (len(entries), src))
    for e in entries:
        print('   ', e)


if __name__ == '__main__':
    main()
