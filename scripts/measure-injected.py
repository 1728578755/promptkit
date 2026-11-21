#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""模拟运行时截断规则，算出各类清单**实际注入**的字节数（不是源文件总量）。

规则来自 harness（dist-server/1029.cjs）：
  * 技能：charsFor = name.len + 4 + (desc ? desc.len+1 : 0)；folder 头 = folder.len + 22
          总预算 skillCharBudget = 8000（硬编码）
          protected（source = bundled|builtin）不截断，其余截到动态上限（实测 82 字符）
          超预算先降级为 names-only，再不够就丢条目
  * 延迟工具：总预算 CODEBUDDY_DEFERRED_TOOLS_CHAR_BUDGET，默认 20000 字符
  * 注：预算单位是 JS 字符数，这里同时给出字符数与 UTF-8 字节数

用法：
    python scripts/measure-injected.py
    python scripts/measure-injected.py --target workbuddy --budget 8000 --deferred 20000
"""
import argparse
import json
import os
import sys

HOME = os.path.expanduser("~")
TARGETS = {
    "workbuddy": os.path.join(HOME, ".workbuddy"),
    "workbuddy-ai": os.path.join(HOME, ".workbuddy-ai"),
}
SKILL_BUDGET = 8000          # eI.uK, hard-coded
DESC_CAP = 82                # measured from app log this session
FOLDER_HEAD = 22


def chars(s):
    return len(s or "")


def bts(s):
    return len((s or "").encode("utf-8"))


def load_skills(cfg):
    p = os.path.join(cfg, ".skill-list-cache.json")
    if not os.path.isfile(p):
        return []
    d = json.load(open(p, encoding="utf-8"))
    out = []
    for r in d.get("results", []):
        if r.get("disable"):
            continue
        out.append({
            "name": r.get("slug") or r.get("name") or "?",
            "desc": (r.get("description") or "").replace("\n", " "),
            "src": r.get("source", "?"),
            "file": r.get("filePath", ""),
        })
    return out


def folder_of(s):
    # 与运行时一致：内置显示 workbuddy-builtin，其余按父目录
    if s["src"] in ("bundled", "builtin"):
        return "workbuddy-builtin"
    fp = s["file"].replace("\\", "/")
    parts = [x for x in fp.split("/") if x]
    return "/".join(parts[:-2]) if len(parts) >= 2 else parts[0] if parts else "?"


def render_skills(skills, budget=SKILL_BUDGET, cap=DESC_CAP):
    """返回 (总字符, 总字节, 模式, 明细行)"""
    groups = {}
    for s in skills:
        groups.setdefault(folder_of(s), []).append(s)
    protected = [s for s in skills if s["src"] in ("bundled", "builtin")]
    others = [s for s in skills if s["src"] not in ("bundled", "builtin")]

    def line(s, desc):
        return "- %s: %s" % (s["name"], desc) if desc else "- %s;" % s["name"]

    def total_for(desc_fn):
        n = 0
        lines = []
        for folder, items in groups.items():
            head = "skills under folder: %s" % folder
            n += chars(head) + 1
            lines.append(head)
            for s in items:
                ln = line(s, desc_fn(s))
                n += chars(ln) + 1
                lines.append(ln)
        return n, lines

    full = lambda s: s["desc"]
    n_full, lines_full = total_for(full)
    if n_full <= budget:
        return n_full, bts("\n".join(lines_full)), "none", lines_full

    # 非 protected 截到 cap，protected 保留全量
    capped = lambda s: s["desc"] if s["src"] in ("bundled", "builtin") else s["desc"][: max(0, cap - 1)] + ("…" if chars(s["desc"]) > cap else "")
    n_cap, lines_cap = total_for(capped)
    if n_cap <= budget:
        return n_cap, bts("\n".join(lines_cap)), "trimmed@%d" % cap, lines_cap

    # 再不行：非 protected 只留名字
    names_only = lambda s: s["desc"] if s["src"] in ("bundled", "builtin") else ""
    n_no, lines_no = total_for(names_only)
    return n_no, bts("\n".join(lines_no)), "names-only", lines_no


def load_deferred(cfg):
    p = os.path.join(cfg, "mcp-tool-list.json")
    if not os.path.isfile(p):
        return []
    d = json.load(open(p, encoding="utf-8"))
    out = []
    for key, tools in (d.get("entries") or {}).items():
        for t in tools:
            out.append({"name": t.get("name", "?"), "desc": (t.get("description") or "").replace("\n", " ")})
    return out


def render_deferred(tools, budget):
    tools = sorted(tools, key=lambda t: t["name"])
    out, used = [], 0
    for t in tools:
        ln = "- %s: %s" % (t["name"], t["desc"])
        if used + chars(ln) + 1 > budget:
            break
        out.append(ln)
        used += chars(ln) + 1
    return used, bts("\n".join(out)), out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="workbuddy")
    ap.add_argument("--budget", type=int, default=SKILL_BUDGET)
    ap.add_argument("--deferred", type=int, default=20000)
    args = ap.parse_args()

    cfg = TARGETS.get(args.target, args.target)
    print("target: %s  (%s)" % (args.target, cfg))

    skills = load_skills(cfg)
    n_chars, n_bytes, mode, lines = render_skills(skills, args.budget)
    print("\n[技能清单] %d 条, 截断模式=%s" % (len(skills), mode))
    print("  实际注入: %d 字符 / %d 字节 (%.1f KB)" % (n_chars, n_bytes, n_bytes / 1024.0))
    print("  预算 %d 字符, 占用 %.0f%%" % (args.budget, 100.0 * n_chars / args.budget))

    tools = load_deferred(cfg)
    d_chars, d_bytes, dlines = render_deferred(tools, args.deferred)
    print("\n[延迟工具] 源 %d 项" % len(tools))
    print("  实际注入: %d 项, %d 字符 / %d 字节 (%.1f KB)" % (
        len(dlines), d_chars, d_bytes, d_bytes / 1024.0))
    print("  预算 %d 字符, 占用 %.0f%%" % (args.deferred, 100.0 * d_chars / args.deferred))

    print("\n  合计两类: %.1f KB" % ((n_bytes + d_bytes) / 1024.0))
    return 0


if __name__ == "__main__":
    sys.exit(main())
