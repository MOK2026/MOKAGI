#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
check_sync_handlers.py — 工具 Handler 同步/異步一致性檢查
========================================================
背景：2026-08-18 發現 /stt 命令報錯
    TypeError: object str can't be used in 'await' expression
根因：handle_stt 是【同步函數】(def)，但 tool_handler.execute_command
      對所有 handler 直接 `await handler(...)` → await 同步函數返回的 str → TypeError。

本腳本自動檢查：
  1. 所有工具模組中定義的 handler 是同步還是異步（標記潛在風險）
  2. 核心代碼中是否存在「直接 await handler」的危險模式
  3. 輸出報告與建議

用法：
  python3 check_sync_handlers.py
  python3 check_sync_handlers.py --fix     # 無法自動修復，僅提示
"""
import ast
import os
import sys
import re

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))  # ~/.mok/tools
CORE = os.path.dirname(BASE) + "/core"                                # ~/.mok/core

TOOLS_DIR = BASE
CORE_FILES = [
    os.path.join(CORE, "tool_handler.py"),
    os.path.join(CORE, "mokagi.py"),
    os.path.join(CORE, "autofix2.py"),
]

# 需要檢查的「直接 await handler」危險模式
DANGEROUS_PATTERNS = [
    (re.compile(r"await\s+handler\s*\("), "直接 await handler(...) — 若 handler 為同步函數會報 TypeError"),
    (re.compile(r"await\s+\w*handler\w*\s*\("), "直接 await 變量名含 handler 的調用"),
]

def scan_tools():
    """掃描工具目錄，找出所有 handler 定義及其同步/異步屬性"""
    issues = []
    async_handlers = []
    sync_handlers = []
    for fname in sorted(os.listdir(TOOLS_DIR)):
        if not fname.endswith(".py"):
            continue
        fpath = os.path.join(TOOLS_DIR, fname)
        try:
            tree = ast.parse(open(fpath, encoding="utf-8").read())
        except SyntaxError as e:
            issues.append(f"  ⚠️ {fname}: 語法錯誤 {e}")
            continue
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = node.name
                if name.startswith("handle_") or name.startswith("async_handle"):
                    is_async = isinstance(node, ast.AsyncFunctionDef)
                    line = node.lineno
                    entry = f"  {fname}:{line}  {'✅ async' if is_async else '❌ 同步'}  {name}"
                    if is_async:
                        async_handlers.append(entry)
                    else:
                        sync_handlers.append(entry)
                        issues.append(f"  🔴 {fname}:{line} 同步 handler `{name}` — 若被直接 await 會報錯！")
    return issues, async_handlers, sync_handlers

def scan_core():
    """掃描核心檔案，找出直接 await handler 的危險模式"""
    issues = []
    for fpath in CORE_FILES:
        if not os.path.exists(fpath):
            continue
        fname = os.path.basename(fpath)
        try:
            lines = open(fpath, encoding="utf-8").read().splitlines()
        except Exception:
            continue
        for i, line in enumerate(lines, 1):
            for pat, desc in DANGEROUS_PATTERNS:
                if pat.search(line) and "call_tool_handler" not in line and "_call_handler" not in line:
                    # 排除已安全包裝的調用
                    issues.append(f"  🔴 {fname}:{i} {desc}\n      {line.strip()}")
    return issues

def main():
    print("=" * 62)
    print("🔍 工具 Handler 同步/異步一致性檢查")
    print("=" * 62)

    tool_issues, async_handlers, sync_handlers = scan_tools()
    core_issues = scan_core()

    print(f"\n📦 異步 handler（{len(async_handlers)} 個，安全）:")
    for e in async_handlers:
        print(e)

    print(f"\n⚠️ 同步 handler（{len(sync_handlers)} 個，需透過兼容調用）:")
    for e in sync_handlers:
        print(e)

    print(f"\n🔴 潛在問題:")
    all_issues = tool_issues + core_issues
    if all_issues:
        for e in all_issues:
            print(e)
    else:
        print("  ✅ 未發現問題")

    print("\n" + "=" * 62)
    if sync_handlers:
        print("⚠️ 注意：以下同步 handler 必須透過兼容調用（inspect.isawaitable 判斷）")
        print("   而非直接 await。若在 tool_handler.py 或 autofix2.py 中直接 await，")
        print("   會拋出 TypeError: object str can't be used in 'await' expression")
        print("   已修復位置：tool_handler.py execute_command、autofix2.py _call_handler")
    else:
        print("✅ 所有 handler 均為異步，無風險")
    print("=" * 62)

if __name__ == "__main__":
    main()
