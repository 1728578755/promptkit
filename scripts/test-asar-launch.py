#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""验证 patch-asar.py 的「写完之后启动应用」这一段（只启动，不杀任何进程）。

做法：造一个 `probe.vbs`，经 `explorer.exe` 启动后写一个标记文件再退出——
这样既走了真实的启动路径（explorer 代启、不继承提权令牌），又不会弹出任何窗口。

用法：
    python scripts/test-asar-launch.py
退出码：0 = 全部通过；1 = 有失败。
"""
import importlib.util
import os
import sys
import tempfile
import time

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("patch_asar", os.path.join(HERE, "patch-asar.py"))
pa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pa)

fails = []
total = 0


def check(cond, msg):
    global total
    total += 1
    print(("  PASS  " if cond else "  FAIL  ") + msg)
    if not cond:
        fails.append(msg)


def main():
    tmp = tempfile.mkdtemp(prefix="wb-launch-probe-")
    marker = os.path.join(tmp, "started.txt")
    probe = os.path.join(tmp, "probe.vbs")
    with open(probe, "w", encoding="utf-8") as f:
        f.write('CreateObject("Scripting.FileSystemObject").CreateTextFile("%s", True).Close\n'
                % marker.replace("\\", "\\\\"))

    print("\n[1] 正常路径：maybe_launch 经 explorer 把目标拉起来")
    ok = pa.maybe_launch(probe, True)
    check(ok is True, "maybe_launch 返回 True（启动了 %s）" % os.path.basename(probe))

    end = time.time() + 15
    while time.time() < end and not os.path.exists(marker):
        time.sleep(0.3)
    check(os.path.exists(marker), "目标进程真的跑起来了（标记文件已生成）")

    print("\n[2] 没东西可启动 / 明确说不要启动")
    missing = os.path.join(tmp, "no-such-app.exe")
    check(pa.maybe_launch(missing, True) is False, "exe 不存在 → 跳过，返回 False")
    check(pa.maybe_launch(probe, False) is False, "--no-launch（enabled=False）→ 跳过，返回 False")

    print("\n[3] 只启动，不结束进程：源码里没有任何结束进程的调用")
    src = open(os.path.join(HERE, "patch-asar.py"), encoding="utf-8").read()
    for bad in ("taskkill", "TerminateProcess", "Stop-Process", "kill("):
        check(bad not in src, "patch-asar.py 不含 %r" % bad)

    import shutil
    shutil.rmtree(tmp, ignore_errors=True)
    print("\n%d/%d 通过" % (total - len(fails), total))
    return 0 if not fails else 1


if __name__ == "__main__":
    sys.exit(main())
