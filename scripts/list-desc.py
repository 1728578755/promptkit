#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""列出项目内技能 / 子代理的 frontmatter name+description 及体积。

用法：
    python scripts/list-desc.py skills          # 技能
    python scripts/list-desc.py agents          # 子代理
    python scripts/list-desc.py skills --json out.json
"""
import argparse
import json
import os
import re
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def b(s):
    return len((s or "").encode("utf-8"))


def parse(path):
    """解析 YAML frontmatter 的 name/description，支持单行、引号、>- 与 | 块标量。"""
    txt = open(path, encoding="utf-8", errors="replace").read()
    m = FM.match(txt)
    fm = m.group(1) if m else ""
    lines = fm.splitlines()
    name = desc = ""
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("name:"):
            name = line[5:].strip().strip("\"'")
        elif line.startswith("description:"):
            raw = line[12:].strip()
            if raw in (">-", ">", "|", "|-"):
                # 块标量：收集后续缩进行
                buf = []
                i += 1
                while i < len(lines):
                    nxt = lines[i]
                    if nxt.strip() and not nxt.startswith((" ", "\t")):
                        break
                    buf.append(nxt.strip())
                    i += 1
                joined = (" " if raw.startswith(">") else "\n").join(
                    x for x in buf if x)
                desc = joined.strip()
                continue
            desc = raw.strip("\"'")
        i += 1
    return name, desc


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("kind", choices=["skills", "agents"])
    ap.add_argument("--json")
    args = ap.parse_args()

    rows = []
    if args.kind == "skills":
        base = os.path.join(PROJ, "plugins", "workbuddy-builtin", "skills")
        for d in sorted(os.listdir(base)):
            p = os.path.join(base, d, "SKILL.md")
            if not os.path.isfile(p):
                continue
            name, desc = parse(p)
            rows.append({"dir": d, "name": name or d, "file": p, "desc": desc,
                         "bytes": b(desc)})
    else:
        base = os.path.join(PROJ, "plugins", "workbuddy-builtin", "builtin-plugins")
        for root, _dirs, files in os.walk(base):
            parts = root.replace("\\", "/").split("/")
            if "agents" not in parts:
                continue
            for f in sorted(files):
                if not f.endswith(".md"):
                    continue
                p = os.path.join(root, f)
                name, desc = parse(p)
                rows.append({"dir": os.path.basename(root), "name": name or f,
                             "file": os.path.relpath(p, PROJ).replace("\\", "/"),
                             "desc": desc, "bytes": b(desc)})

    rows.sort(key=lambda r: -r["bytes"])
    tot = sum(r["bytes"] for r in rows)
    for r in rows:
        print("%6d  %-26s %s" % (r["bytes"], r["name"], r["desc"][:110].replace("\n", " ")))
    print("\n%d 项，description 合计 %d 字节 (%.1f KB)" % (len(rows), tot, tot / 1024.0))

    if args.json:
        json.dump(rows, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("明细写入 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
