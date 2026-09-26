# -*- coding: utf-8 -*-
"""E 里程碑：把指定侍女 .稚 的 MOK_CURRENT_MODEL 切到 girl:qwen-Claude (僅改檔, 重載由前端/重啟完成)"""
import os, sys, re

def switch(agent_name="稚", model="girl:qwen-Claude"):
    path = os.path.join(os.path.expanduser("~"), ".mok", "agent", agent_name, f".{agent_name}")
    if not os.path.exists(path):
        return False, f"找不到設定檔 {path}"
    with open(path, "r", encoding="utf-8") as f:
        lines = f.readlines()
    hit = False
    for i, ln in enumerate(lines):
        if ln.startswith("MOK_CURRENT_MODEL="):
            lines[i] = f"MOK_CURRENT_MODEL={model}\n"
            hit = True
            break
    if not hit:
        return False, ".稚 內無 MOK_CURRENT_MODEL 行"
    with open(path, "w", encoding="utf-8") as f:
        f.writelines(lines)
    return True, f"{agent_name} MOK_CURRENT_MODEL -> {model}（需 reload/重啟生效）"

if __name__ == "__main__":
    agent = sys.argv[1] if len(sys.argv) > 1 else "稚"
    model = sys.argv[2] if len(sys.argv) > 2 else "girl:qwen-Claude"
    ok, msg = switch(agent, model)
    print(msg)
