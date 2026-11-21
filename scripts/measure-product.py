#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""统计 product.json 各段体积，并展开 tools / prompts / builtInSubagents。

用法：
    python scripts/measure-product.py                 # 默认 CN 安装
    python scripts/measure-product.py --top 20
    python scripts/measure-product.py --tools        # 逐个工具的体积与描述长度
    python scripts/measure-product.py --prompts      # 逐个 prompt 键
    python scripts/measure-product.py --subagents
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _install  # noqa: E402  sibling helper: locate an install without hardcoding

def b(o):
    return len(json.dumps(o, ensure_ascii=False).encode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", default=_install.find_product(),
                    help="<install>/resources/app.asar.unpacked/cli/product.json (default: WORKBUDDY_INSTALL / targets.json)")
    ap.add_argument("--top", type=int, default=12)
    ap.add_argument("--tools", action="store_true")
    ap.add_argument("--prompts", action="store_true")
    ap.add_argument("--subagents", action="store_true")
    args = ap.parse_args()

    d = json.load(open(args.product, encoding="utf-8"))
    total = os.path.getsize(args.product)
    print("product.json: %d 字节 (%.1f KB)" % (total, total / 1024.0))
    print("顶层键体积 top%d:" % args.top)
    rows = sorted(((k, b(v)) for k, v in d.items()), key=lambda x: -x[1])
    for k, n in rows[: args.top]:
        print("  %8d  %5.1f%%  %s" % (n, 100.0 * n / total, k))

    if args.tools:
        tools = d.get("tools")
        print("\n[tools] 类型=%s 条目=%s 体积=%d" % (
            type(tools).__name__, len(tools) if hasattr(tools, "__len__") else "?", b(tools)))
        if isinstance(tools, dict):
            items = tools.items()
        elif isinstance(tools, list):
            items = [(t.get("name", "?"), t) for t in tools]
        else:
            items = []
        for name, t in sorted(items, key=lambda x: -b(x[1]))[: args.top]:
            desc = ""
            if isinstance(t, dict):
                desc = t.get("description") or (t.get("tool", {}) or {}).get("description") or ""
            print("  %7d  desc=%5d  %s" % (b(t), len(desc), name))

    if args.prompts:
        pr = d.get("prompts") or []
        if isinstance(pr, dict):
            pr = [{"name": k, "template": v} for k, v in pr.items()]
        print("\n[prompts] 条目=%d 体积=%d (%.1f KB)" % (len(pr), b(pr), b(pr) / 1024.0))
        for it in sorted(pr, key=lambda x: -b(x.get("template", ""))):
            print("  %7d  %s" % (b(it.get("template", "")), it.get("name", "?")))

    if args.subagents:
        sa = d.get("builtInSubagents")
        print("\n[builtInSubagents] 类型=%s 体积=%d" % (type(sa).__name__, b(sa)))
        if isinstance(sa, dict):
            for k, v in sorted(sa.items(), key=lambda x: -b(x[1])):
                desc = (v.get("description") if isinstance(v, dict) else "") or ""
                print("  %7d  desc=%5d  %s" % (b(v), len(desc), k))
        elif isinstance(sa, list):
            for v in sa:
                if isinstance(v, dict):
                    print("  %7d  desc=%5d  %s" % (
                        b(v), len(v.get("description") or ""), v.get("name", "?")))
    return 0


if __name__ == "__main__":
    sys.exit(main())
