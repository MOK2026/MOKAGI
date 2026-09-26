#!/usr/bin/env python3
# core_guard.py - L2 核心檔檔案層守護者
# 以 Linux 不可變屬性(chattr +i) 加 SHA256 基線，把保護下沉到檔案系統層，
# 不再依賴對命令字串的比對。編輯期間(編輯登記.json 有效鎖)會自動解鎖。
import os, sys, json, time, hashlib, shutil, subprocess

HOME = os.path.expanduser("~")
MOK  = os.path.join(HOME, ".mok")
BASE = os.path.join(MOK, "backups", "core_baseline")
MANI = os.path.join(BASE, "manifest.json")
LOG  = os.path.join(MOK, "logs", "core_guard.log")
REG  = os.path.join(MOK, "skill", "進化", "編輯登記.json")
DEFAULT_TTL = int(os.environ.get("MOK_EDITLOCK_TTL") or 3 * 3600)

DEFAULT_LIST = [
    "tools/admin.py",
    "tools/replace_in_file.py",
    "tools/memory.py",
    "core/mokagi.py",
    "core/config.py",
    "core/tool_handler.py",
    "core/workflow.py",
    "MOKAGI.sh",
    "env.env",
]

def rel_list():
    f = os.path.join(MOK, "protected_core.txt")
    if os.path.exists(f):
        out = []
        for line in open(f, encoding="utf-8"):
            line = line.strip()
            if line and not line.startswith("#"):
                out.append(line)
        if out:
            return out
    return DEFAULT_LIST

def log(msg):
    try:
        os.makedirs(os.path.dirname(LOG), exist_ok=True)
        with open(LOG, "a", encoding="utf-8") as f:
            f.write("[%s] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), msg))
    except Exception:
        pass
    print(msg)

def sudo(args):
    return subprocess.run(["sudo", "-n"] + args, capture_output=True, text=True)

def sha256(p):
    h = hashlib.sha256()
    with open(p, "rb") as f:
        for b in iter(lambda: f.read(65536), b""):
            h.update(b)
    return h.hexdigest()

def is_immutable(p):
    r = sudo(["lsattr", "--", p])
    if r.returncode != 0:
        return None
    line = (r.stdout or "").splitlines()
    line = line[0] if line else ""
    parts = line.split()
    flags = parts[0] if parts else ""
    return "i" in flags

def set_immutable(p, on):
    r = sudo(["chattr", "+i" if on else "-i", "--", p])
    return r.returncode == 0

def norm(p):
    p = (p or "").strip()
    for pre in (MOK + os.sep, MOK + "/", "./", "/"):
        if p.startswith(pre):
            p = p[len(pre):]
    return p.rstrip("/")

def active_edits():
    try:
        data = json.load(open(REG, encoding="utf-8"))
    except Exception:
        return set()
    now = int(time.time())
    out = set()
    for r in data:
        if r.get("status") == "editing" and not r.get("end"):
            ttl = int(r.get("ttl") or 0) or DEFAULT_TTL
            ts = int(r.get("start_ts") or now)
            if (now - ts) < ttl:
                out.add(norm(r.get("file")))
    return out

def cmd_baseline():
    os.makedirs(BASE, exist_ok=True)
    m = {}
    for rel in rel_list():
        src = os.path.join(MOK, rel)
        if not os.path.exists(src):
            log("baseline: 缺少 %s" % rel)
            continue
        dst = os.path.join(BASE, rel.replace("/", "__"))
        shutil.copy2(src, dst)
        m[rel] = sha256(src)
    json.dump(m, open(MANI, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    log("baseline 建立完成：%d 檔" % len(m))

def cmd_guard(dry=False):
    edits = active_edits()
    man = {}
    if os.path.exists(MANI):
        try:
            man = json.load(open(MANI, encoding="utf-8"))
        except Exception:
            man = {}
    if not man:
        cmd_baseline()
        man = json.load(open(MANI, encoding="utf-8"))
    for rel in rel_list():
        p = os.path.join(MOK, rel)
        if not os.path.exists(p):
            continue
        editing = (rel in edits) or (norm(rel) in edits)
        imm = is_immutable(p)
        if editing:
            if imm:
                if dry:
                    log("[dry] 解鎖(編輯中) %s" % rel)
                else:
                    set_immutable(p, False)
                    log("解鎖(編輯中) %s" % rel)
            continue
        base = man.get(rel)
        cur = sha256(p)
        if base and cur != base:
            bak = os.path.join(BASE, rel.replace("/", "__"))
            if os.path.exists(bak):
                if dry:
                    log("[dry] 偵測竄改並還原 %s" % rel)
                else:
                    set_immutable(p, False)
                    shutil.copy2(bak, p)
                    set_immutable(p, True)
                    log("!! 偵測竄改並還原：%s" % rel)
                continue
            log("!! 竄改但無備份可還原：%s" % rel)
        if not imm:
            if dry:
                log("[dry] 鎖定 %s" % rel)
            else:
                set_immutable(p, True)
                log("鎖定 %s" % rel)

def cmd_set(on):
    for rel in rel_list():
        p = os.path.join(MOK, rel)
        if os.path.exists(p):
            ok = set_immutable(p, on)
            log(("鎖定 " if on else "解鎖 ") + rel + (" OK" if ok else " 失敗"))

def cmd_status():
    man = {}
    if os.path.exists(MANI):
        try:
            man = json.load(open(MANI, encoding="utf-8"))
        except Exception:
            man = {}
    edits = active_edits()
    print("== core_guard status ==")
    print("編輯中:", ", ".join(sorted(edits)) or "(無)")
    for rel in rel_list():
        p = os.path.join(MOK, rel)
        if not os.path.exists(p):
            print("  %-28s 不存在" % rel)
            continue
        imm = is_immutable(p)
        cur = sha256(p)
        ok = ("OK" if man.get(rel) == cur else "DIFF") if rel in man else "NO-BASE"
        print("  %-28s imm=%s sha=%s" % (rel, ("Y" if imm else "n"), ok))


def cmd_accept(rel):
    rel = norm(rel)
    if rel not in [norm(x) for x in rel_list()]:
        return
    p = os.path.join(MOK, rel)
    if not os.path.exists(p):
        log("accept: 不存在 %s" % rel)
        return
    os.makedirs(BASE, exist_ok=True)
    dst = os.path.join(BASE, rel.replace("/", "__"))
    set_immutable(p, False)
    shutil.copy2(p, dst)
    man = {}
    if os.path.exists(MANI):
        try:
            man = json.load(open(MANI, encoding="utf-8"))
        except Exception:
            man = {}
    man[rel] = sha256(p)
    json.dump(man, open(MANI, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
    set_immutable(p, True)
    log("accept(新基線+鎖定) %s" % rel)

def cmd_release(rel):
    rel = norm(rel)
    if rel not in [norm(x) for x in rel_list()]:
        return
    p = os.path.join(MOK, rel)
    if os.path.exists(p):
        set_immutable(p, False)
        log("release(解鎖) %s" % rel)

if __name__ == "__main__":
    argv = sys.argv[1:]
    cmd = argv[0] if argv else "status"
    dry = "--dry" in argv
    if cmd == "baseline":
        cmd_baseline()
    elif cmd == "guard":
        cmd_guard(dry)
    elif cmd == "lock":
        cmd_set(True)
    elif cmd == "unlock":
        cmd_set(False)
    elif cmd == "status":
        cmd_status()
    elif cmd == "accept" and len(argv) >= 2:
        cmd_accept(argv[1])
    elif cmd == "release" and len(argv) >= 2:
        cmd_release(argv[1])
    else:
        print("用法: core_guard.py [status|baseline|guard [--dry]|lock|unlock|accept <rel>|release <rel>]")
