#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""賺錢王 - 互動式手動掃描（可自訂行業 + 要求內容）"""
import subprocess, sys, os, datetime

os.chdir(os.path.dirname(os.path.abspath(__file__)))

print("")
print("🤖 賺錢王掃描器 - 手動模式（可自訂搜尋要求）")
print("預設行業：餐廳 美容 髮型屋 裝修工程 寵物店 補習社 健身室 牙科診所 汽車維修 洗衣店 花店 攝影")
print("")
cats = input("請輸入要掃描的行業/關鍵詞（Enter=用預設，多個用空格分隔）: ").strip()
print("")
print("請選擇要求內容（可自訂）：")
print("  A  = 只找【沒網頁】的商家")
print("  B  = 只找【有網頁但沒AI客服】的商家")
print("  AB = 兩者都要（預設）")
t = input("請輸入 (A/B/AB，Enter=AB): ").strip().upper() or "AB"

cmd = [sys.executable, "scanner.py"]
if cats:
    cmd += cats.split()
if t != "AB":
    cmd += ["--type", t]

print(f">>> 開始掃描（行業: {cats or '預設'}｜要求: {t}）...")
ts = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")
with open("scanner.log", "a", encoding="utf-8") as f:
    f.write(f"===== {ts} 掃描開始 =====\n")
    subprocess.run(cmd, stdout=f, stderr=subprocess.STDOUT)
    f.write(f"===== {ts} 掃描結束 =====\n")
print(">>> 完成！報告: 報告.html｜記錄: leads_7days.jsonl｜日誌: scanner.log")
