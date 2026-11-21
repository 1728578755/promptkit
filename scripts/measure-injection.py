#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""测量注入清单体积：技能 / 子代理 / 延迟工具。

用法：
    python scripts/measure-injection.py                # 两套 target 都测
    python scripts/measure-injection.py --target cn
    python scripts/measure-injection.py --target ai
    python scripts/measure-injection.py --json out.json

口径说明：
  * 技能：读 <configDir>/.skill-list-cache.json 的 results，只统计 disable=false
    的条目；按 source/目录分组，累加 "slug: description" 的 UTF-8 字节。
  * 延迟工具：读 <configDir>/mcp-tool-list.json，按 server 分组累加 name+description。
  * 子代理：读项目内 builtin-plugins/*/agents/*.md 的 YAML frontmatter
    （name/description 行）。asar 内置子代理无法从磁盘读到，单独标注。
"""
import argparse
import json
import os
import re
import sys

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HOME = os.path.expanduser("~")
TARGETS = {
    "cn": os.path.join(HOME, ".workbuddy"),
    "ai": os.path.join(HOME, ".workbuddy-ai"),
}


def b(s):
    return len((s or "").encode("utf-8"))


def measure_skills(cfg):
    p = os.path.join(cfg, ".skill-list-cache.json")
    if not os.path.exists(p):
        return None
    d = json.load(open(p, encoding="utf-8"))
    rows = []
    for r in d.get("results", []):
        if r.get("disable"):
            continue
        name = r.get("name") or r.get("slug") or "?"
        desc = r.get("description") or ""
        slug = r.get("slug") or name
        rows.append({
            "name": name,
            "slug": slug,
            "src": r.get("source", "?"),
            "file": r.get("filePath", ""),
            "model_invokable": not r.get("disableModelInvocation", False),
            "desc_bytes": b(desc),
            "line_bytes": b("- %s: %s\n" % (slug, desc)),
            "desc": desc,
        })
    return rows


def measure_deferred(cfg):
    p = os.path.join(cfg, "mcp-tool-list.json")
    if not os.path.exists(p):
        return None
    d = json.load(open(p, encoding="utf-8"))
    out = []
    for key, tools in (d.get("entries") or {}).items():
        for t in tools:
            out.append({
                "server_key": key,
                "name": t.get("name", "?"),
                "desc_bytes": b(t.get("description", "")),
                "desc": t.get("description", ""),
            })
    return out


FM = re.compile(r"^---\s*\n(.*?)\n---\s*\n", re.S)


def measure_agents():
    rows = []
    base = os.path.join(PROJ, "plugins", "workbuddy-builtin", "builtin-plugins")
    for root, _dirs, files in os.walk(base):
        if "agents" not in root.replace("\\", "/").split("/"):
            continue
        for f in files:
            if not f.endswith(".md"):
                continue
            fp = os.path.join(root, f)
            txt = open(fp, encoding="utf-8", errors="replace").read()
            m = FM.match(txt)
            fm = m.group(1) if m else ""
            name = ""
            desc = ""
            for line in fm.splitlines():
                if line.startswith("name:"):
                    name = line[5:].strip()
                elif line.startswith("description:"):
                    desc = line[12:].strip()
            rows.append({
                "file": os.path.relpath(fp, PROJ).replace("\\", "/"),
                "name": name,
                "desc_bytes": b(desc),
                "desc": desc,
            })
    return rows


def summarize(label, items, key="line_bytes"):
    if not items:
        print("  %-14s <缺失>" % label)
        return 0
    tot = sum(i.get(key, i.get("desc_bytes", 0)) for i in items)
    print("  %-14s %3d 项  %7d 字节  (%.1f KB)" % (label, len(items), tot, tot / 1024.0))
    return tot


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", choices=["cn", "ai", "all"], default="all")
    ap.add_argument("--json")
    ap.add_argument("--top", type=int, default=0, help="打印最大的 N 条明细")
    args = ap.parse_args()

    targets = ["cn", "ai"] if args.target == "all" else [args.target]
    dump = {}
    grand = 0
    for t in targets:
        cfg = TARGETS[t]
        if not os.path.isdir(cfg):
            continue
        print("\n[%s] %s" % (t, cfg))
        skills = measure_skills(cfg)
        deferred = measure_deferred(cfg)
        agents = measure_agents()
        sub = 0
        sub += summarize("技能清单", skills)
        sub += summarize("延迟工具", deferred)
        sub += summarize("子代理(项目内)", agents)
        grand = max(grand, sub)
        dump[t] = {"skills": skills, "deferred": deferred, "agents": agents, "total": sub}
        if args.top:
            print("  -- 技能 top%d --" % args.top)
            for r in sorted(skills or [], key=lambda x: -x["desc_bytes"])[: args.top]:
                print("   %6d  %s" % (r["desc_bytes"], r["slug"]))
            print("  -- 延迟工具 top%d --" % args.top)
            for r in sorted(deferred or [], key=lambda x: -x["desc_bytes"])[: args.top]:
                print("   %6d  %s" % (r["desc_bytes"], r["name"]))
    print("\n合计（单 target 最大）: %d 字节 = %.1f KB" % (grand, grand / 1024.0))

    if args.json:
        json.dump(dump, open(args.json, "w", encoding="utf-8"), ensure_ascii=False, indent=1)
        print("明细已写入 %s" % args.json)
    return 0


if __name__ == "__main__":
    sys.exit(main())
