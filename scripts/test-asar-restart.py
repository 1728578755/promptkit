#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 patch-asar.py 的「识别占用 → 只关该关的进程 → 等解锁 → 再启动」。

安全约定（重要）
----------------
1. 真实 WorkBuddy 一次都不碰：所有 exe 都是 `<tmp>` 里的 python 副本。
2. 段 2 只做**只读**断言：同名 WorkBuddy.exe 的副本必须只匹配到它自己。
3. 段 3 用 monkeypatch 截获 subprocess.run，断言 close_app **只会**发出
   `taskkill /PID … /T`，永远不许出现 `/IM`（按映像名批量杀会连带杀掉真实应用，
   2026-11-21 就是这么误杀过一次）。
4. 唯一真杀的是 `<tmp>\\wb-restart-probe.exe` 这类不重名的替身。

用法：
    python scripts/test-asar-restart.py
退出码：0 = 全部断言通过；1 = 有失败。
"""
import importlib.util
import os
import shutil
import subprocess
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("patch_asar", os.path.join(HERE, "patch-asar.py"))
pa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pa)

REAL_EXE = r"C:\Program Files\WorkBuddy\WorkBuddy.exe"
REAL_ASAR = r"C:\Program Files\WorkBuddy\resources\app.asar"
fails = []
total = 0


def check(cond, msg):
    global total
    total += 1
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def wait_for(fn, want, timeout=15.0):
    end = time.time() + timeout
    while time.time() < end:
        if bool(fn()) == want:
            return True
        time.sleep(0.3)
    return bool(fn()) == want


def phase_basic(tmp):
    print("\n[1] 关进程 / 等解锁 / 再启动（替身：wb-restart-probe.exe）")
    probe = os.path.join(tmp, "wb-restart-probe.exe")
    shutil.copy2(sys.executable, probe)

    check(pa.find_app_processes(probe) == [], "启动前：找不到该 exe 的进程")
    check(pa.file_locked(probe) is False, "启动前：exe 可写（file_locked=False）")

    pa.launch_app(probe, ["-c", "import time; time.sleep(120)"])
    check(wait_for(lambda: pa.find_app_processes(probe), True),
          "启动后：按全路径找到进程 %s" % (pa.find_app_processes(probe) or "[]"))
    check(pa.file_locked(probe) is True, "运行中：exe 不可写（这正是要等解锁的原因）")

    closed = pa.close_app(probe, grace=6.0)
    check(bool(closed) and wait_for(lambda: pa.find_app_processes(probe), False, 20.0)
          and pa.file_locked(probe) is False,
          "close_app：%d 个进程退出且文件解锁" % len(closed))

    pa.launch_app(probe, ["-c", "import time; time.sleep(120)"])
    check(wait_for(lambda: pa.find_app_processes(probe), True), "launch_app：进程被重新拉起")
    pa.close_app(probe, grace=6.0)


def phase_path_match(tmp):
    print("\n[2] 同名副本只匹配自己 + 真实占用能被识别（只读，不杀）")
    fake = os.path.join(tmp, "install")
    os.makedirs(os.path.join(fake, "resources"), exist_ok=True)
    fake_exe = os.path.join(fake, "WorkBuddy.exe")
    shutil.copy2(sys.executable, fake_exe)
    fake_asar = os.path.join(fake, "resources", "app.asar")
    with open(fake_asar, "wb") as f:
        f.write(b"x" * 4096)
    holder = os.path.join(fake, "hold.py")
    with open(holder, "w", encoding="utf-8") as f:
        f.write("import time\ntime.sleep(120)\n")

    pa.launch_app(fake_exe, [holder])
    ok = wait_for(lambda: pa.find_app_processes(fake_exe), True)
    fake_pids = set(pa.find_app_processes(fake_exe))
    real_pids = set(pa.find_app_processes(REAL_EXE))
    check(ok, "假 install：找到副本进程 %s" % sorted(fake_pids))
    check(bool(fake_pids) and not (fake_pids & real_pids),
          "同名 WorkBuddy.exe：只匹配副本自己的 pid，碰不到真实应用（真实 %d 个）" % len(real_pids))

    for pid in fake_pids:  # 明确按 pid 收尾，不用 /IM
        subprocess.run(["taskkill", "/F", "/PID", str(pid)], capture_output=True)
    wait_for(lambda: pa.find_app_processes(fake_exe), False, 10.0)

    # 占用识别对着真实 asar 验（只读）：Electron 把 asar 整份映射住，写不进去
    if os.path.isfile(REAL_ASAR):
        check(pa.file_locked(REAL_ASAR) is True, "真实 app.asar：file_locked=True（运行中写不进）")
        check(pa.wait_unlocked(REAL_ASAR, timeout=0.8) is False, "wait_unlocked：真被占时超时返回 False")
    check(pa.file_locked(fake_asar) is False, "未被占的文件：file_locked=False")
    check(pa.wait_unlocked(fake_asar, timeout=0.8) is True, "wait_unlocked：没被占时立即返回 True")


def phase_no_im_kill(tmp):
    print("\n[3] close_app 只发 taskkill /PID（截获命令行，不真杀）")
    captured = []

    class FakeResult:
        returncode = 0
        stdout = ""
        stderr = ""

    real_run = subprocess.run
    real_find = pa.find_app_processes

    def fake_run(args, **kw):
        captured.append(list(args))
        return FakeResult()

    seq = {"n": 0}

    def fake_find(_path):
        seq["n"] += 1
        return [4242] if seq["n"] <= 1 else []

    subprocess.run = fake_run          # close_app 内部 `import subprocess` 拿的就是这个模块
    pa.find_app_processes = fake_find
    try:
        pa.close_app(os.path.join(tmp, "install", "WorkBuddy.exe"), grace=0.2, hard=0.2)
    finally:
        subprocess.run = real_run
        pa.find_app_processes = real_find

    flat = [" ".join(c) for c in captured]
    check(bool(captured), "close_app 发出了 %d 次结束请求" % len(captured))
    check(all(c[0] == "taskkill" for c in captured), "全部是 taskkill")
    check(all("/IM" not in c for c in captured), "没有任何 /IM（按映像名批量杀）：%s" % (flat or "-"))
    check(all("/PID" in c for c in captured), "每次都用 /PID 指定目标：%s" % (flat or "-"))


def main():
    tmp = tempfile.mkdtemp(prefix="wb-restart-probe-")
    try:
        phase_basic(tmp)
        phase_path_match(tmp)
        phase_no_im_kill(tmp)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d/%d 通过" % (total - len(fails), total))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
