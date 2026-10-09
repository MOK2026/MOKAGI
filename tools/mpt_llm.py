#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""mpt_llm.py — 用 MoneyPrinterTurbo 已配置的 LLM 產生文字。

用法：python mpt_llm.py <prompt_file> [out_file]
在 MPT 目錄下以 MPT venv 執行（cwd=/home/ubuntu/.mok/mpt/MoneyPrinterTurbo）。
"""
import os
import sys

sys.path.insert(0, os.getcwd())


def main():
    # 僅在實際被當腳本執行時才匯入（此時 cwd=MPT 目錄）。
    # tool_handler 會掃描 tools/ 下所有 .py，若寫在模組層會在啟動時噴 ModuleNotFoundError。
    from app.services import llm

    prompt = open(sys.argv[1], encoding="utf-8").read()
    text = llm._generate_response(prompt)
    if len(sys.argv) > 2:
        open(sys.argv[2], "w", encoding="utf-8").write(text or "")
    sys.stdout.write(text or "")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
