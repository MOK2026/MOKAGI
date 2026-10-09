# -*- coding: utf-8 -*-
"""
Key Pool patch for MOKAGI (plan B / v2)
建立新 Agent 時，從 Key Pool 領一把全新的真 DeepSeek API key，寫進 .<agent>
（MOK_MODEL_token6/7/13 ＋ 主模型 MOK_MODEL_token，並把當前模型切成 config.json 指定的模型）。

【v2 修正 2026-09-28】舊版只包 app.view_functions['create_agent']，但載入順序為
「依目錄名排序、後者覆蓋前者」，身分核心_202609250200（排在其後）同樣改寫該 endpoint、
且是綁回核心原始函數 → 舊版 wrapper 被整條蓋掉，新 agent 的 token 全空。
（實測：ttt1 建立後 token6/7/13 全空、key_pool used=0。）

v2 三重保險：
  1) 目錄名用全形ｚ開頭 → 排最後載入
  2) 同時寫 app.view_functions 與 main.create_agent
  3) app.after_request 兜底：偵測 /api/create_agent 成功就補注入（不依賴 monkey patch）
     注入前檢查槽位是否已有真 key → 不重複領、不燒 key
停用：目錄改名加前綴「_」再重啟。
"""
import os
import re
import sys
import traceback

KP_DIR = '/home/ubuntu/.mok/agent/泠/jobs/DeepSeek_key'
if KP_DIR not in sys.path:
    sys.path.insert(0, KP_DIR)

main = sys.modules.get('__main__')
app = getattr(main, 'app', None) if main is not None else None

if app is None:
    print('[KP] WARN: no core app', flush=True)
else:
    def _kp_inject(name):
        try:
            import inject as _inj
            dot_file = os.path.expanduser('~/.mok/agent/%s/.%s' % (name, name))
            if not os.path.exists(dot_file):
                return {'ok': False, 'reason': 'dot_file_not_found'}
            cfg = _inj.load_cfg()
            text = open(dot_file, encoding='utf-8').read()
            for slot in cfg.get('ds_slots', ['6', '7', '13']):
                m = re.search(r'(?m)^MOK_MODEL_token%s=(.+)$' % re.escape(str(slot)), text)
                if m and m.group(1).strip():
                    return {'ok': False, 'reason': 'already_injected'}
            r = _inj.inject(dot_file, name)
            print('[KP] injected %s -> %s' % (name, r), flush=True)
            return r
        except Exception:
            traceback.print_exc()
            return {'ok': False, 'reason': 'exception'}

    _orig = app.view_functions.get('create_agent')
    if _orig is not None:
        def create_agent_keypool(*a, **k):
            resp = _orig(*a, **k)
            try:
                from flask import request
                data = request.get_json(silent=True) or {}
                name = (data.get('name') or '').strip()
                body = resp[0] if isinstance(resp, tuple) else resp
                if name and isinstance(body, dict) and body.get('status') == 'ok':
                    _kp_inject(name)
            except Exception:
                traceback.print_exc()
            return resp

        app.view_functions['create_agent'] = create_agent_keypool
        try:
            main.create_agent = create_agent_keypool
        except Exception:
            pass
        print('[KP] Key Pool patch mounted (create_agent -> keypool)', flush=True)
    else:
        print('[KP] WARN: create_agent endpoint not found', flush=True)

    try:
        from flask import request as _rq
        _done = set()

        @app.after_request
        def _kp_after(response):
            try:
                if _rq.method == 'POST' and _rq.path.rstrip('/').endswith('/api/create_agent'):
                    if getattr(response, 'status_code', 200) == 200:
                        data = _rq.get_json(silent=True) or {}
                        name = (data.get('name') or '').strip()
                        if name and name not in _done:
                            _done.add(name)
                            _kp_inject(name)
            except Exception:
                pass
            return response

        print('[KP] Key Pool fallback after_request mounted', flush=True)
    except Exception:
        traceback.print_exc()
