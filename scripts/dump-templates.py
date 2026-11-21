#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 JSON 里的长 template 拆行落盘，便于逐行阅读（避免 Read 的单行 2000 字符截断）。

用法：
    python scripts/dump-templates.py --src overrides/tools/session-tools.orig.json --outdir overrides/_dump
"""
import argparse
import json
import os
import re
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default="overrides/tools/session-tools.orig.json")
    ap.add_argument("--outdir", default="overrides/_dump")
    args = ap.parse_args()

    d = json.load(open(args.src, encoding="utf-8"))
    os.makedirs(args.outdir, exist_ok=True)
    for k, v in d.items():
        tpl = v["template"] if isinstance(v, dict) else v
        safe = re.sub(r"[^A-Za-z0-9_.-]", "_", k)
        path = os.path.join(args.outdir, safe + ".txt")
        # 统一换行，逐段落盘
        text = tpl.replace("\r\n", "\n").replace("\r", "\n")
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            for i, line in enumerate(text.split("\n"), 1):
                f.write("%3d| %s\n" % (i, line))
        print("%-46s %6d 字节 -> %s" % (k, len(tpl.encode()), path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
