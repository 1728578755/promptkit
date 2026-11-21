#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""校验压缩后的工具描述：变量完整性 + 体积对比 + 语义要点抽查。

用法：
    python scripts/verify-tool-overrides.py                     # 默认 session 集
    python scripts/verify-tool-overrides.py --orig overrides/tools/session-tools.orig.json \
                                             --new  overrides/tools/session-tools.compact.json
    python scripts/verify-tool-overrides.py --strict             # 有缺失变量时退出码 1

检查项：
  1. 新文件 JSON 合法，条目数与原文件一致
  2. 每个 name 都来自原文件（不新增未知键）
  3. 原模板的每个 {{变量}} 与 {%指令%} 在新模板中都存在（漏变量会让渲染出错）
  4. 逐条字节对比，打印压缩率
  5. --strict 时，任何缺失变量或体积反增都判失败
"""
import argparse
import json
import os
import re
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VAR = re.compile(r"\{\{[^}]+\}\}|\{%[^%]+%\}")


def b(s):
    return len((s or "").encode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--orig", default=os.path.join(PROJ, "overrides", "tools", "session-tools.orig.json"))
    ap.add_argument("--new", default=os.path.join(PROJ, "overrides", "tools", "session-tools.compact.json"))
    ap.add_argument("--strict", action="store_true")
    args = ap.parse_args()

    orig = json.load(open(args.orig, encoding="utf-8"))
    raw = json.load(open(args.new, encoding="utf-8"))
    new = {p["name"]: p.get("template", "") for p in raw.get("prompts", [])}

    fails = []
    print("%-42s %7s %7s %8s  %s" % ("prompts 键", "原文", "新版", "变化", "变量"))
    print("-" * 92)

    tot_o = tot_n = 0
    for k, v in sorted(orig.items(), key=lambda x: -b(x[1]["template"])):
        o = v["template"]
        n = new.get(k)
        if n is None:
            fails.append("%s: 新文件缺少该键" % k)
            print("%-42s %7d %7s %8s  MISSING" % (k, b(o), "-", "-"))
            continue
        ov = set(VAR.findall(o))
        nv = set(VAR.findall(n))
        lost = ov - nv
        extra = nv - ov
        mark = "OK" if not lost and not extra else ("丢%d" % len(lost) if lost else "+%d" % len(extra))
        tot_o += b(o)
        tot_n += b(n)
        pct = (b(n) - b(o)) * 100.0 / b(o) if b(o) else 0
        print("%-42s %7d %7d %+7.1f%%  %s" % (k, b(o), b(n), pct, mark))
        for s in sorted(lost):
            fails.append("%s: 丢失变量 %s" % (k, s))
        for s in sorted(extra):
            fails.append("%s: 新增未知变量 %s" % (k, s))
        if b(n) >= b(o):
            fails.append("%s: 体积未减少 (%d -> %d)" % (k, b(o), b(n)))

    unknown = [k for k in new if k not in orig]
    for k in unknown:
        fails.append("%s: 不在原文件中的键" % k)

    print("-" * 92)
    print("合计 %d 条：%d -> %d 字节 (%.1f KB -> %.1f KB, %+.1f%%)" % (
        len(orig), tot_o, tot_n, tot_o / 1024.0, tot_n / 1024.0,
        (tot_n - tot_o) * 100.0 / tot_o if tot_o else 0))

    if fails:
        print("\n未通过 %d 项：" % len(fails))
        for f in fails:
            print("  - %s" % f)
        return 1 if args.strict else 0
    print("\n全部通过：条目齐全、变量无丢失、体积均下降")
    return 0


if __name__ == "__main__":
    sys.exit(main())
