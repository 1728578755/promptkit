#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""导出指定工具的 prompts 模板原文，供改写压缩。

用法：
    python scripts/export-tool-desc.py --set session      # 本会话高频 19 条
    python scripts/export-tool-desc.py --set all          # 全量 65 条
    python scripts/export-tool-desc.py --set session --print-max 6
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _install  # noqa: E402  sibling helper: locate an install without hardcoding

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SESSION_TOOLS = [
    "Read", "ViewImage", "Write", "Edit", "Glob", "Grep", "Bash", "PowerShell",
    "TaskCreate", "TaskGet", "TaskUpdate", "TaskOutput", "TaskList", "SendMessage",
    "DeferExecuteTool", "ToolSearch", "Skill", "Agent",
]


def b(s):
    return len((s or "").encode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", default=_install.find_product(),
                    help="<install>/resources/app.asar.unpacked/cli/product.json (default: WORKBUDDY_INSTALL / targets.json)")
    ap.add_argument("--set", choices=["session", "all"], default="session")
    ap.add_argument("--out")
    ap.add_argument("--print-max", type=int, default=0)
    args = ap.parse_args()

    d = json.load(open(args.product, encoding="utf-8"))
    prompts = {p["name"]: p.get("template", "") for p in d.get("prompts", [])}
    key_of = {t["name"]: t.get("description") for t in d.get("tools", [])}

    tools = SESSION_TOOLS if args.set == "session" else [t["name"] for t in d.get("tools", [])]
    out = {}
    for tool in tools:
        key = key_of.get(tool)
        if key and key in prompts and prompts[key]:
            out[key] = {"tool": tool, "template": prompts[key]}

    path = args.out or os.path.join(PROJ, "overrides", "tools", "%s-tools.orig.json" % args.set)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    json.dump(out, open(path, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
    total = sum(b(v["template"]) for v in out.values())
    print("导出 %d 条，合计 %d 字节 (%.1f KB) -> %s" % (len(out), total, total / 1024.0, path))
    for k, v in sorted(out.items(), key=lambda x: -b(x[1]["template"])):
        print("  %6d  %-40s %s" % (b(v["template"]), k, v["tool"]))

    if args.print_max:
        print("\n" + "=" * 70)
        for k, v in sorted(out.items(), key=lambda x: -b(x[1]["template"]))[: args.print_max]:
            print("\n##### %s (%s) %d 字节 #####" % (k, v["tool"], b(v["template"])))
            print(v["template"])
    return 0


if __name__ == "__main__":
    sys.exit(main())
