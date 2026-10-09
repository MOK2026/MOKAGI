#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
金流巡檢.py  （2026-10-03 支付女）
==================================
由 cron 每 3 分鐘執行一次，做三件事：

  ① 掃 USDC（Base 主網）入帳 → 對上 pending 訂單 → 自動 credit_tokens 入帳
  ② 掃 BTC 入帳（設定檔有填 btc_address 才啟用）→ 同上
  ③ 把 status='review' 的收據丟給 vision 做 AI 預檢，結果寫回 orders.ai_check

對不上任何訂單的鏈上入帳，會記在 chain_tx(handled=0)，等管理員在後台綁定。

用法： python3 金流巡檢.py [--once]
"""
import os, sys, json, time, importlib.util, traceback

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, '/home/ubuntu/.mok/core')
sys.path.insert(0, '/home/ubuntu/.mok/tools')

LOCK = '/tmp/mok_paywatch.lock'
LOG = os.environ.get('MOK_PAYWATCH_LOG', '/tmp/mok_paywatch.log')


def log(msg):
    line = '[%s] %s' % (time.strftime('%Y-%m-%d %H:%M:%S'), msg)
    print(line)
    try:
        with open(LOG, 'a', encoding='utf-8') as f:
            f.write(line + '\n')
    except Exception:
        pass


def _load_patch():
    spec = importlib.util.spec_from_file_location('mok_pay_patch', os.path.join(HERE, '保丁.py'))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


try:
    P = _load_patch()
except Exception as e:
    log('❌ 載入補丁失敗: %s' % e)
    traceback.print_exc()
    sys.exit(1)


# ---- 掃鏈參數（2026-10-07 修法 A：分塊查詢 / 修法 B：block cursor 補掃）----
SCAN_CHUNK          = int(os.environ.get('MOK_SCAN_CHUNK', '2000'))       # A: 每塊格數（大塊省請求，失敗自動縮半）
SCAN_LOOKBACK_FIRST = int(os.environ.get('MOK_SCAN_BLOCKS', '9000'))     # 首次無游標時回看
SCAN_MAX_BACKFILL   = int(os.environ.get('MOK_SCAN_MAX_BACKFILL', '50000'))  # B: 單輪補掃上限（格）
SCAN_MAX_CHUNKS     = int(os.environ.get('MOK_SCAN_MAX_CHUNKS', '400'))  # 每輪最多塊數
CURSOR_KEY          = 'scan_cursor_usdc'                                 # B: 進度游標（settings 表）
SCAN_RETRY          = int(os.environ.get('MOK_SCAN_RETRY', '2'))         # 網路/限流類：退避重試次數
SCAN_BACKOFF        = float(os.environ.get('MOK_SCAN_BACKOFF', '1.0'))   # 網路類退避基數（秒）
SCAN_BACKOFF_RL     = float(os.environ.get('MOK_SCAN_BACKOFF_RL', '4.0'))  # 限流類(403/429)退避基數（秒）
SCAN_MIN_CHUNK      = int(os.environ.get('MOK_SCAN_MIN_CHUNK', '100'))   # 縮塊下限（格）
SCAN_THROTTLE       = float(os.environ.get('MOK_SCAN_THROTTLE', '0.3'))  # 塊間節流（秒），避免觸發節點配額


def pad(a):
    return '0x' + a.lower().replace('0x', '').rjust(64, '0')


def _seen(tx_hash):
    with P._connect() as conn:
        r = conn.execute('SELECT handled FROM chain_tx WHERE tx_hash=?', (tx_hash.lower(),)).fetchone()
    return r


def _remember(tx_hash, chain, amount, frm='', block=0):
    try:
        with P._db_lock, P._connect() as conn:
            conn.execute('INSERT OR IGNORE INTO chain_tx (tx_hash, chain, order_no, from_addr, amount, '
                         'block, seen_ts, handled, note) VALUES (?,?,?,?,?,?,?,?,?)',
                         (tx_hash.lower(), chain, '', frm, float(amount), int(block),
                          time.time(), 0, 'unmatched'))
            conn.commit()
    except Exception as e:
        log('  ! 記錄 chain_tx 失敗: %s' % e)


def _pending_orders(channel, max_age_h=24.0):
    since = time.time() - max_age_h * 3600
    with P._connect() as conn:
        rows = conn.execute("SELECT * FROM orders WHERE channel=? AND status='pending' "
                            "AND created_ts > ? ORDER BY id ASC", (channel, since)).fetchall()
    return [dict(r) for r in rows]


def _try_match(chain, tx_hash, amount, tol_abs=0.02, tol_rel=0.005):
    """把一筆鏈上入帳嘗試配對到 pending 訂單並入帳。回傳 (ok, 說明)"""
    orders = _pending_orders(chain)
    if not orders:
        return False, 'no_pending_order'
    best, bestd = None, None
    for o in orders:
        try:
            need = float(o['pay_amount'])
        except Exception:
            continue
        d = abs(need - float(amount))
        limit = max(tol_abs, need * tol_rel)
        if d <= limit and (bestd is None or d < bestd):
            best, bestd = o, d
    if best is None:
        return False, 'no_amount_match'
    ok, msg, info = P.apply_chain_payment(best, tx_hash, source='watcher')
    if ok:
        return True, '%s -> %s +%s 點' % (tx_hash[:16], best['order_no'], format(info['credited_tokens'], ','))
    return False, '%s 配對 %s 但驗證失敗: %s' % (tx_hash[:16], best['order_no'], msg)


def _get_cursor():
    """B: 讀取掃鏈進度游標（settings.pay_scan_cursor_usdc）；無值回 None。"""
    try:
        v = str(P._sget(CURSOR_KEY) or '').strip()
        return int(v) if v.lstrip('-').isdigit() else None
    except Exception:
        return None


def _set_cursor(blk):
    """B: 只在確定連續掃到該高度後寫入；寫失敗只記錄，不中斷巡檢。"""
    try:
        P._sset(CURSOR_KEY, int(blk))
    except Exception as e:
        log('  ! 寫入掃鏈游標失敗: %s' % e)


def _is_rate_err(msg):
    """限流類錯誤（節點配額 403/429）：該長退避，不該縮塊。"""
    m = (msg or '').lower()
    for k in ('403', '429', 'forbidden', 'too many requests', 'rate limit'):
        if k in m:
            return True
    return False


def _is_range_err(msg):
    """範圍/結果過大類錯誤（該縮塊）vs 限流/網路類（該退避重試）。"""
    m = (msg or '').lower()
    for k in ('413', 'range', '-32602', 'max results', 'too large', 'response size'):
        if k in m:
            return True
    return False


def _get_logs_chunked(frm, to, addr):
    """A: 分塊 eth_getLogs。回傳 (logs, ok_upto)。
    ok_upto = 連續成功掃描到的最高塊；中途失敗即停 → 游標不越過失敗處，下輪自動補掃。
    限流/網路類（403/429/timeout）→ 退避重試同一塊（換節點由 _rpc 內部處理）；
    範圍/結果過大類 → 塊縮半重試（下限 SCAN_MIN_CHUNK）；
    每塊成功後節流 SCAN_THROTTLE 秒，避免觸發節點配額。"""
    logs, size, cur, ok_upto, chunks = [], max(1, SCAN_CHUNK), frm, frm - 1, 0
    while cur <= to:
        if chunks >= SCAN_MAX_CHUNKS:
            log('  ! 本輪達分塊上限 %d 塊，剩 %d 格留待下輪補掃' % (SCAN_MAX_CHUNKS, to - cur + 1))
            break
        hi = min(cur + size - 1, to)
        part, last_err, ok = None, '', False
        for attempt in range(SCAN_RETRY + 1):
            try:
                part = P._rpc('eth_getLogs', [{'address': P.USDC_CONTRACT,
                                               'topics': [P.TRANSFER_TOPIC, None, pad(addr)],
                                               'fromBlock': hex(cur), 'toBlock': hex(hi)}], timeout=25)
                ok = True
                break
            except Exception as e:
                last_err = str(e)
                if _is_range_err(last_err):
                    break
                if attempt < SCAN_RETRY:
                    _base = SCAN_BACKOFF_RL if _is_rate_err(last_err) else SCAN_BACKOFF
                    time.sleep(_base * (2 ** attempt))
        if ok:
            logs.extend(part or [])
            ok_upto, cur, chunks = hi, hi + 1, chunks + 1
            if SCAN_THROTTLE > 0 and cur <= to:
                time.sleep(SCAN_THROTTLE)
            continue
        if _is_range_err(last_err) and size > SCAN_MIN_CHUNK:
            size = max(SCAN_MIN_CHUNK, size // 2)
            log('  ! getLogs %d-%d 範圍/結果過大，塊縮至 %d 格重試' % (cur, hi, size))
            continue
        log('  ! getLogs %d-%d 最終失敗: %s；游標停在 %d，下輪自動補掃'
            % (cur, hi, last_err[:90], ok_upto + 1))
        break
    return logs, ok_upto


def scan_usdc():
    addr = P._sget('usdc_address')
    if not addr:
        return 0
    latest = int(P._rpc('eth_blockNumber', []), 16)
    cur_blk = _get_cursor()
    if cur_blk is None:
        frm = max(0, latest - SCAN_LOOKBACK_FIRST)
        log('  🧭 首次掃鏈（無游標）：回看 %d 格（%d → %d）' % (latest - frm + 1, frm, latest))
    else:
        frm = cur_blk + 1
        behind = latest - frm + 1
        if behind > SCAN_MAX_BACKFILL:
            missed = behind - SCAN_MAX_BACKFILL
            frm = latest - SCAN_MAX_BACKFILL
            log('  ⚠️ 游標落後 %d 格，超出補掃上限 %d 格；最舊 %d 格（約 %.1f 小時）未掃，請人工核對'
                % (behind, SCAN_MAX_BACKFILL, missed, missed * 2.0 / 3600.0))
            try:
                P._sset('scan_gap_notice', '%s 漏掃 %d 格（%d → %d）'
                        % (time.strftime('%Y-%m-%d %H:%M:%S'), missed, cur_blk + 1, frm - 1))
            except Exception:
                pass
        elif behind > 300:
            log('  🧭 補掃中：落後 %d 格（%d → %d）' % (behind, frm, latest))
    if frm > latest:
        return 0
    logs, ok_upto = _get_logs_chunked(frm, latest, addr)
    n = 0
    for lg in logs or []:
        tx = (lg.get('transactionHash') or '').lower()
        if not tx:
            continue
        if _seen(tx):
            continue
        try:
            amount = int(lg['data'], 16) / 1e6
        except Exception:
            continue
        frm_addr = '0x' + (lg.get('topics') or ['', ''])[1][-40:]
        blk = int(lg.get('blockNumber', '0x0'), 16)
        _remember(tx, 'usdc', amount, frm_addr, blk)
        ok, msg = _try_match('usdc', tx, amount)
        log(('  ✅ ' if ok else '  ⏳ ') + msg)
        n += 1
    if ok_upto >= frm:
        _set_cursor(ok_upto)
        log('  🧭 USDC 游標 → %d（本輪掃 %d 格，入帳候選 %d 筆）' % (ok_upto, ok_upto - frm + 1, n))
    return n


def scan_btc():
    addr = (P._sget('btc_address') or '').strip()
    if not addr:
        return 0
    try:
        txs = P._http_get_json(P.MEMPOOL + '/api/address/' + addr + '/txs', timeout=25)
    except Exception as e:
        log('  ! BTC 查詢失敗: %s' % e)
        return 0
    n = 0
    for t in txs or []:
        txid = (t.get('txid') or '').lower()
        if not txid or _seen(txid):
            continue
        recv = 0.0
        for v in t.get('vout', []):
            if v.get('scriptpubkey_address') == addr:
                recv += float(v.get('value') or 0) / 1e8
        if recv <= 0:
            continue
        st = t.get('status') or {}
        _remember(txid, 'btc', recv, '', int(st.get('block_height') or 0))
        ok, msg = _try_match('btc', txid, recv, tol_abs=0.000002, tol_rel=0.005)
        log(('  ✅ ' if ok else '  ⏳ ') + msg)
        n += 1
    return n


def retry_unmatched():
    """把先前未匹配的鏈上入帳再對一次（處理「先轉帳後建單」的時序問題）"""
    with P._connect() as conn:
        rows = conn.execute("SELECT * FROM chain_tx WHERE handled=0 AND seen_ts > ? "
                            "ORDER BY seen_ts DESC LIMIT 50", (time.time() - 86400 * 3,)).fetchall()
    n = 0
    for r in rows:
        ok, msg = _try_match(r['chain'], r['tx_hash'], r['amount'])
        if ok:
            log('  🔁 補配對成功: ' + msg)
            n += 1
    return n


def ai_precheck():
    """收據 AI 預檢：讀出金額 / 日期 / 收款人，寫回 orders.ai_check"""
    with P._connect() as conn:
        rows = conn.execute("SELECT * FROM orders WHERE status='review' AND ai_check='pending' "
                            "AND proof_path != '' ORDER BY id ASC LIMIT 5").fetchall()
    if not rows:
        return 0
    try:
        from vision import handle_vision
        import asyncio
    except Exception as e:
        log('  ! vision 不可用: %s' % e)
        return 0
    n = 0
    q = ('這是一張付款收據 / 轉帳截圖。請只輸出一個 JSON，不要多餘文字，欄位：'
         '{"amount":"金額與幣別","date":"日期時間","payee":"收款人/帳號","payer":"付款人",'
         '"method":"支付方式","order_no":"圖中出現的訂單號或備註","confidence":0-1,'
         '"risk":"可疑之處，例如金額不符/明顯修圖/截圖不完整"}')
    for r in rows:
        p = r['proof_path']
        if not os.path.exists(p):
            P.set_order(r['order_no'], ai_check='missing_file')
            continue
        try:
            res = asyncio.run(handle_vision({'file_path': p, 'question': q},
                                            chat_id='paywatch', agent_config=None))
            try:
                j = json.loads(res)
                txt = j.get('analysis') or j.get('error') or str(res)
            except Exception:
                txt = str(res)
            P.set_order(r['order_no'], ai_check=(txt or '')[:4000])
            log('  🧠 AI 預檢完成 %s' % r['order_no'])
            n += 1
        except Exception as e:
            P.set_order(r['order_no'], ai_check='error: %s' % e)
            log('  ! AI 預檢失敗 %s: %s' % (r['order_no'], e))
    return n


def main():
    try:
        if os.path.exists(LOCK):
            age = time.time() - os.path.getmtime(LOCK)
            if age < 240:
                log('上一輪仍在跑（%.0fs），跳過' % age)
                return
        open(LOCK, 'w').write(str(os.getpid()))
    except Exception:
        pass
    t0 = time.time()
    try:
        P.expire_orders()
        a = scan_usdc()
        b = scan_btc()
        c = retry_unmatched()
        d = ai_precheck()
        log('巡檢完成：USDC %d 筆 / BTC %d 筆 / 補配對 %d / AI 預檢 %d，耗時 %.1fs'
            % (a, b, c, d, time.time() - t0))
    except Exception as e:
        log('❌ 巡檢異常: %s' % e)
        traceback.print_exc()
    finally:
        try:
            os.unlink(LOCK)
        except Exception:
            pass


if __name__ == '__main__':
    main()
