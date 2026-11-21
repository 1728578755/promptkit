#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""按映射批量改写 SKILL.md frontmatter 的 description（只改 description，不动 description_en）。

用法：
    python scripts/apply-skill-desc.py --map overrides/skills/desc-compact.json --dry-run
    python scripts/apply-skill-desc.py --map overrides/skills/desc-compact.json
    python scripts/apply-skill-desc.py --map ... --restore     # 从备份还原

行为：
  * 仅在文件首个 YAML frontmatter（--- ... ---）范围内替换 ^description: 开头的那一行
  * 写前把原文件备份到 backups/<时间戳>-skill-desc/<skill>/SKILL.md
  * 打印每条的前后字节与百分比
"""
import argparse
import json
import os
import re
import shutil
import sys
import time

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SKILLS = os.path.join(PROJ, "plugins", "workbuddy-builtin", "skills")
FM = re.compile(r"\A---\s*\n(.*?\n)---\s*\n", re.S)


def b(s):
    return len((s or "").encode("utf-8"))


def replace_desc(text, new_desc):
    """在首个 frontmatter 内替换 description（含 YAML 块标量的续行），返回 (新文本, 旧值)。"""
    m = FM.match(text)
    if not m:
        return None, None
    lines = m.group(1).split("\n")
    out = []
    old = None
    i = 0
    while i < len(lines):
        line = lines[i]
        if old is None and line.startswith("description:"):
            raw = line[len("description:"):].strip()
            if raw in (">-", ">", "|", "|-"):
                # 块标量：吃掉后续所有缩进/空行，整块换成单行
                buf = []
                i += 1
                while i < len(lines):
                    nxt = lines[i]
                    if nxt.strip() and not nxt.startswith((" ", "\t")):
                        break
                    if nxt.strip():
                        buf.append(nxt.strip())
                    i += 1
                sep = " " if raw.startswith(">") else "\n"
                old = sep.join(buf)
            else:
                old = raw.strip("\"'")
                i += 1
            out.append("description: " + new_desc)
            continue
        out.append(line)
        i += 1
    if old is None:
        return None, None
    # 保证闭合定界符独占一行：原 frontmatter 末尾可能没有空行
    body = "\n".join(out)
    if not body.endswith("\n"):
        body += "\n"
    return "---\n" + body + "---\n" + text[m.end():], old


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--map", default=os.path.join(PROJ, "overrides", "skills", "desc-compact.json"))
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--restore", action="store_true", help="从最近一次备份还原")
    args = ap.parse_args()

    mapping = json.load(open(args.map, encoding="utf-8"))
    mapping = {k: v for k, v in mapping.items() if not k.startswith("_") and isinstance(v, str)}
    stamp = time.strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(PROJ, "backups", stamp + "-skill-desc")

    if args.restore:
        base = os.path.join(PROJ, "backups")
        cands = sorted(d for d in os.listdir(base) if d.endswith("-skill-desc"))
        if not cands:
            print("no skill-desc backup found")
            return 2
        src = os.path.join(base, cands[-1])
        n = 0
        for skill in os.listdir(src):
            s = os.path.join(src, skill, "SKILL.md")
            if os.path.isfile(s):
                shutil.copy2(s, os.path.join(SKILLS, skill, "SKILL.md"))
                n += 1
        print("restored %d file(s) from %s" % (n, src))
        return 0

    tot_o = tot_n = tot_oc = tot_nc = 0
    print("%-34s %7s %7s %6s %6s %8s" % ("skill", "bytes前", "bytes后", "字符前", "字符后", "变化"))
    print("-" * 78)
    for skill, new_desc in sorted(mapping.items()):
        path = os.path.join(SKILLS, skill, "SKILL.md")
        if not os.path.isfile(path):
            print("%-34s  MISSING" % skill)
            continue
        text = open(path, encoding="utf-8", newline="").read()
        new_text, old = replace_desc(text, new_desc)
        if new_text is None:
            print("%-34s  no description line" % skill)
            continue
        o, n = b(old), b(new_desc)
        oc, nc = len(old), len(new_desc)
        tot_o += o
        tot_n += n
        tot_oc += oc
        tot_nc += nc
        pct = (nc - oc) * 100.0 / oc if oc else 0
        print("%-34s %7d %7d %6d %6d %+7.1f%%" % (skill, o, n, oc, nc, pct))
        if args.dry_run:
            continue
        os.makedirs(os.path.join(bdir, skill), exist_ok=True)
        shutil.copy2(path, os.path.join(bdir, skill, "SKILL.md"))
        with open(path, "w", encoding="utf-8", newline="") as f:
            f.write(new_text)

    print("-" * 78)
    print("total %d -> %d bytes (%+d) | %d -> %d chars (%+d, %+.1f%%)" % (
        tot_o, tot_n, tot_n - tot_o, tot_oc, tot_nc, tot_nc - tot_oc,
        (tot_nc - tot_oc) * 100.0 / tot_oc if tot_oc else 0))
    print("注：运行时预算按字符计（8000），不是字节。")
    if args.dry_run:
        print("[dry-run] nothing written")
    else:
        print("backups -> %s" % bdir)
        print("Run scripts\\deploy.cmd to push into cache + install mirror.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
