#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""工具描述映射表：工具名 -> prompts 键 -> 字节数，含分档统计。

用法：
    python scripts/tool-desc-map.py                # 全量对照表
    python scripts/tool-desc-map.py --session      # 只看本会话高频工具
    python scripts/tool-desc-map.py --json out.json
"""
import argparse
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _install  # noqa: E402  sibling helper: locate an install without hardcoding

# 2026-11-17 work craft 会话实际注入的工具（由本会话 system 的 <functions> 逐条核对）
SESSION_TOOLS = [
    "Read", "ViewImage", "Write", "Edit", "Glob", "Grep", "Bash", "PowerShell",
    "TaskCreate", "TaskGet", "TaskUpdate", "TaskOutput", "TaskList", "SendMessage",
    "DeferExecuteTool", "automation_update", "present_files", "show_widget",
    "widget_guidelines", "ToolSearch", "Skill", "Agent",
]


def b(s):
    return len((s or "").encode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--product", default=_install.find_product(),
                    help="<install>/resources/app.asar.unpacked/cli/product.json (default: WORKBUDDY_INSTALL / targets.json)")
    ap.add_argument("--session", action="store_true")
    ap.add_argument("--json")
    args = ap.parse_args()

    d = json.load(open(args.product, encoding="utf-8"))
    prompts = {p["name"]: p.get("template", "") for p in d.get("prompts", [])}
    rows = []
    for t in d.get("tools", []):
        name = t.get("name", "?")
        key = t.get("description") or ""
        tpl = prompts.get(key, "")
        rows.append({"tool": name, "key": key, "bytes": b(tpl), "found": bool(tpl), "tpl": tpl})

    if args.session:
        rows = [r for r in rows if r["tool"] in SESSION_TOOLS]

    rows.sort(key=lambda r: -r["bytes"])
    tot = sum(r["bytes"] for r in rows)
    print("%-22s %-38s %7s" % ("工具", "prompts 键", "字节"))
    for r in rows:
        flag = "" if r["found"] else "  <键未找到>"
        print("%-22s %-38s %7d%s" % (r["tool"], r["key"], r["bytes"], flag))
    print("\n%d 条，合计 %d 字节 = %.1f KB" % (len(rows), tot, tot / 1024.0))

    missing = [r["tool"] for r in rows if not r["found"]]
    if missing:
        print("未匹配到描述键: %s" % ", ".join(missing))

    # 未被 tools 引用的 tool-* prompts（可能由其他路径消费）
    used = {r["key"] for r in rows}
    orphan = [(k, b(v)) for k, v in prompts.items() if k.startswith("tool-") and k not in used]
    if orphan and not args.session:
        print("\n未被 tools 引用的 tool-* 模板: %d 条 %d 字节" % (
            len(orphan), sum(x[1] for x in orphan)))
        for k, n in sorted(orphan, key=lambda x: -x[1]):
            print("  %7d  %s" % (n, k))

    if args.json:
        json.dump(rows, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("\n明细写入 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
