#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Mok 公用手機版 CSS 自動注入工具
================================
規則：
  1. 母站「原生有手機版」→ 用原生，不動它。
  2. 母站「沒有手機版」    → 注入公用的 ../css/mok-mobile.css。

用法：
  python3 mok_mobile_css.py scan                 # 只掃描、產生報告
  python3 mok_mobile_css.py scan --fetch         # 額外抓遠端 CSS 判斷（較準、較慢）
  python3 mok_mobile_css.py apply --file a/X.html
  python3 mok_mobile_css.py apply --all
  python3 mok_mobile_css.py revert --all
"""
import argparse
import json
import os
import re
import sys
import time
import urllib.request

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # .../MokCs
A_DIR = os.path.join(BASE, 'a')
CSS_URL = '/static/mok-mobile.css'   # 公用手機版 CSS（由 mok_web.py 的 /static/ 提供，公開可讀）
CSS_FILE = '/home/ubuntu/.mok/html/static/mok-mobile.css'
MARK = 'MOK-MOBILE-CSS v1.0'

VIEWPORT_TAG = '<meta name="viewport" content="width=device-width, initial-scale=1" data-mok-mobile="1">'
LINK_TAG = '<link rel="stylesheet" href="%s" media="all" data-mok-mobile="1">' % CSS_URL

FRAMEWORK_HINTS = [
    'bootstrap', 'tailwind', 'foundation', 'bulma', 'uikit', 'materialize',
    'semantic.min', 'jquery.mobile', 'jquery-mobile', 'skeleton.css', 'pure-min',
    'amazeui', 'element-ui', 'vant', 'flexboxgrid', 'susy', 'webflow',
]
NATIVE_NAME_HINTS = ['responsive', 'mobile', 'mobi', 'phone', 'rwd']
PLUGIN_HINTS = ['fancybox', 'font-awesome', 'animate', 'slick', 'swiper', 'lightbox', 'jquery-ui', 'normalize', 'magnific', 'photoswipe', 'select2', 'colorbox', 'video-js', 'aos']


def read_text(path):
    with open(path, 'rb') as f:
        raw = f.read()
    for enc in ('utf-8', 'utf-8-sig', 'big5', 'gb18030', 'latin-1'):
        try:
            return raw.decode(enc), enc
        except Exception:
            continue
    return raw.decode('utf-8', 'replace'), 'utf-8(replace)'


def has_native_mobile(text):
    """判斷頁面本身是否已有手機版（原生響應式）。"""
    low = text.lower()
    reasons = []

    # 1) 框架
    for h in FRAMEWORK_HINTS:
        if h in low:
            reasons.append('framework:' + h)
            break

    # 2) 檔名含 responsive / mobile
    css_links = re.findall(r'<link[^>]+href=["\']([^"\']+\.css[^"\']*)["\']', text, re.I)
    for u in css_links:
        base = os.path.basename(u.split('?')[0]).lower()
        if any(k in base for k in NATIVE_NAME_HINTS):
            reasons.append('css-name:' + base)
            break

    # 3) inline / 內嵌 @media 有斷點
    for m in re.findall(r'@media[^{]{0,120}', text, re.I):
        ml = m.lower()
        if ('max-width' in ml) or ('max-device-width' in ml) or ('min-device-width' in ml):
            reasons.append('inline-media')
            break
    else:
        # 4) @media 在 <style> 內但仍需檢查（同上已涵蓋）；檢查 width=device-width 不等於響應式
        pass

    # 5) HTML5 響應式圖片 / srcset
    if 'srcset=' in low:
        reasons.append('srcset')

    return (len(reasons) > 0), reasons


def fetch_remote_css(text, timeout=8):
    """抓遠端 CSS，檢查是否含 max-width 斷點。回傳 (有無, 詳細)。"""
    urls = re.findall(r'<link[^>]+href=["\'](https?://[^"\']+\.css[^"\']*)["\']', text, re.I)
    # 也支援 cpress 這類 /load/css/index.php?src=...css 的代理
    urls += re.findall(r'<link[^>]+href=["\'](https?://[^"\']*\?[^"\']*\.css[^"\']*)["\']', text, re.I)
    urls = [u for u in dict.fromkeys(urls)
            if not any(k in os.path.basename(u.split('?')[0]).lower() for k in PLUGIN_HINTS)][:6]
    hits = []
    for u in urls:
        try:
            req = urllib.request.Request(u, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=timeout) as r:
                css = r.read(600000).decode('utf-8', 'replace')
        except Exception:
            continue
        for m in re.findall(r'@media[^{]{0,120}', css, re.I):
            ml = m.lower()
            if 'max-width' in ml or 'max-device-width' in ml:
                hits.append(u)
                break
    return (len(hits) > 0), hits


def analyze(path, do_fetch=False):
    text, enc = read_text(path)
    low = text.lower()
    res = {
        'file': os.path.relpath(path, BASE),
        'size': len(text),
        'encoding': enc,
        'has_viewport': ('name="viewport"' in low) or ("name='viewport'" in low),
        'has_doctype': low.lstrip().startswith('<!doctype'),
        'has_head': '</head>' in low,
        'already_injected': (MARK.lower() in low) or ('data-mok-mobile' in low),
        'empty': len(text.strip()) == 0,
    }
    native, reasons = has_native_mobile(text)
    if do_fetch and not native:
        f_native, hits = fetch_remote_css(text)
        if f_native:
            native = True
            reasons = ['remote-css:' + (hits[0][:80])]
        res['remote_checked'] = True
    res['native_mobile'] = native
    res['native_reasons'] = reasons
    # 決策
    if res['empty']:
        res['action'] = 'skip:empty'
    elif not res['has_head']:
        res['action'] = 'skip:no-head'
    elif res['already_injected']:
        res['action'] = 'already'
    elif native:
        res['action'] = 'keep-native'
    else:
        res['action'] = 'inject'
    return res


BLOCK_RE = re.compile(
    r'\n?[ \t]*<!--\s*MOK-MOBILE-CSS[^>]*-->.*?<!--\s*/MOK-MOBILE-CSS\s*-->[ \t]*\n?',
    re.I | re.S)


def build_block(need_viewport, has_base=False, css_text=None):
    # 含 <base href> 的頁面，相對/絕對路徑都會被解析到原站 → 直接內嵌 CSS
    lines = ['<!-- %s -->' % MARK]
    if need_viewport:
        lines.append(VIEWPORT_TAG)
    if has_base and css_text:
        lines.append('<style data-mok-mobile=1>')
        lines.append(css_text)
        lines.append('</style>')
    else:
        lines.append(LINK_TAG)
    lines.append('<!-- /MOK-MOBILE-CSS -->')
    return '\n' + '\n'.join(lines) + '\n'


def inject(path, info=None, backup=False):
    text, enc = read_text(path)
    if MARK.lower() in text.lower() or 'data-mok-mobile' in text.lower():
        return 'already', text
    m = re.search(r'</head\s*>', text, re.I)
    if not m:
        return 'skip:no-head', text
    low = text.lower()
    need_vp = not (('name="viewport"' in low) or ("name='viewport'" in low))
    has_base = ('<base ' in low) or ('<base>' in low)
    css_text = None
    if has_base:
        try:
            css_text = read_text(CSS_FILE)[0]
        except Exception:
            css_text = None
    block = build_block(need_vp, has_base, css_text)
    new = text[:m.start()] + block + text[m.start():]
    if backup:
        bp = path + '.mokbak'
        if not os.path.exists(bp):
            with open(bp, 'wb') as f:
                f.write(text.encode(enc if enc != 'utf-8(replace)' else 'utf-8'))
    with open(path, 'w', encoding=enc if enc not in ('utf-8(replace)',) else 'utf-8', newline='') as f:
        f.write(new)
    return 'injected', new


def revert(path):
    text, enc = read_text(path)
    if MARK.lower() not in text.lower() and 'data-mok-mobile' not in text.lower():
        return 'nothing'
    new = BLOCK_RE.sub('', text)
    new = re.sub(r'[ \t]*<meta[^>]*data-mok-mobile[^>]*>\s*\n?', '', new, flags=re.I)
    new = re.sub(r'[ \t]*<link[^>]*data-mok-mobile[^>]*>\s*\n?', '', new, flags=re.I)
    # 相容舊格式（只有起始標記、沒有結束標記）
    new = re.sub(r'[ \t]*<!--\s*MOK-MOBILE-CSS[^>]*-->[ \t]*\n?', '', new, flags=re.I)
    new = re.sub(r'[ \t]*<!--\s*/MOK-MOBILE-CSS\s*-->[ \t]*\n?', '', new, flags=re.I)
    new = re.sub(r'[ \t]*<script[^>]*data-mok-mobile[^>]*>.*?</script>\s*\n?', '', new, flags=re.I | re.S)
    with open(path, 'w', encoding=enc if enc not in ('utf-8(replace)',) else 'utf-8', newline='') as f:
        f.write(new)
    return 'reverted'


def iter_files(d):
    for root, _dirs, files in os.walk(d):
        for fn in sorted(files):
            if fn.lower().endswith(('.html', '.htm')):
                yield os.path.join(root, fn)


def main():
    ap = argparse.ArgumentParser(description='Mok 公用手機版 CSS 注入工具')
    ap.add_argument('cmd', choices=['scan', 'apply', 'revert'])
    ap.add_argument('--dir', default=A_DIR)
    ap.add_argument('--file')
    ap.add_argument('--all', action='store_true')
    ap.add_argument('--fetch', action='store_true')
    ap.add_argument('--json')
    ap.add_argument('--backup', action='store_true')
    ap.add_argument('--css-url', default=None, help='自訂公用手機版 CSS 網址（預設 /static/mok-mobile.css）')
    ap.add_argument('--limit', type=int, default=0)
    args = ap.parse_args()

    global CSS_URL, LINK_TAG
    if args.css_url:
        CSS_URL = args.css_url
        LINK_TAG = '<link rel="stylesheet" href="%s" media="all" data-mok-mobile="1">' % CSS_URL

    targets = []
    if args.file:
        targets = [args.file if os.path.isabs(args.file) else os.path.join(args.dir, args.file)]
    else:
        targets = list(iter_files(args.dir))
        if args.limit:
            targets = targets[:args.limit]

    if args.cmd == 'scan':
        rows = []
        stat = {}
        for i, p in enumerate(targets, 1):
            try:
                r = analyze(p, do_fetch=args.fetch)
            except Exception as e:
                r = {'file': os.path.relpath(p, BASE), 'action': 'error', 'error': str(e)}
            stat[r.get('action', '?')] = stat.get(r.get('action', '?'), 0) + 1
            rows.append(r)
            if i % 100 == 0:
                print('  ...掃描 %d/%d' % (i, len(targets)), file=sys.stderr)
        print('=' * 64)
        print('Mok 手機版 CSS 掃描報告   目標檔數：%d' % len(rows))
        print('=' * 64)
        for k in sorted(stat, key=lambda x: -stat[x]):
            print('  %-16s %d' % (k, stat[k]))
        print('-' * 64)
        need = [r for r in rows if r.get('action') == 'inject']
        print('需要注入公用手機版 CSS 的檔案：%d' % len(need))
        for r in need[:30]:
            print('   -', r['file'])
        if len(need) > 30:
            print('   ... 其餘 %d 個' % (len(need) - 30))
        native = [r for r in rows if r.get('action') == 'keep-native']
        print('原生已有手機版（保留不動）：%d' % len(native))
        if args.json:
            with open(args.json, 'w', encoding='utf-8') as f:
                json.dump(rows, f, ensure_ascii=False, indent=1)
            print('詳細 JSON →', args.json)
        return

    if args.cmd == 'apply':
        if not (args.file or args.all):
            print('請加 --file <檔> 或 --all'); return
        n_ok = n_skip = 0
        for p in targets:
            try:
                r = analyze(p, do_fetch=args.fetch)
            except Exception as e:
                print('ERROR', p, e); continue
            if args.all and r['action'] != 'inject':
                n_skip += 1
                continue
            st, _ = inject(p, r, backup=args.backup)
            print('  [%s] %s' % (st, os.path.relpath(p, BASE)))
            if st == 'injected':
                n_ok += 1
            else:
                n_skip += 1
        print('--- 完成：注入 %d，略過 %d ---' % (n_ok, n_skip))
        return

    if args.cmd == 'revert':
        n = 0
        for p in targets:
            try:
                st = revert(p)
            except Exception as e:
                print('ERROR', p, e); continue
            if st == 'reverted':
                n += 1
                print('  [reverted]', os.path.relpath(p, BASE))
        print('--- 還原 %d 個檔案 ---' % n)


if __name__ == '__main__':
    main()
