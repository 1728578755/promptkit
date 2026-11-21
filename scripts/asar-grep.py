#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""在 asar 里搜字符串并打印上下文（asar 是拼接包，明文可搜）。

用法：
    python scripts/asar-grep.py "skills under folder" --before 300 --after 800 --max 3
    python scripts/asar-grep.py "available_deferred_tools" -A 600
    python scripts/asar-grep.py "truncate" --asar "<安装目录>/resources/app.asar"
"""
import argparse
import io
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _install  # noqa: E402  sibling helper: locate an install without hardcoding


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("needle")
    ap.add_argument("--asar", default=_install.find_asar(),
                    help="<install>/resources/app.asar (default: WORKBUDDY_INSTALL / targets.json)")
    ap.add_argument("-B", "--before", type=int, default=200)
    ap.add_argument("-A", "--after", type=int, default=600)
    ap.add_argument("--max", type=int, default=3)
    ap.add_argument("--encoding", default="utf-8")
    args = ap.parse_args()

    if not os.path.exists(args.asar):
        print("asar 不存在: %s" % args.asar)
        return 2
    needle = args.needle.encode(args.encoding, "ignore")
    chunk = 8 << 20
    overlap = args.before + args.after + 4096
    hits = 0
    with open(args.asar, "rb") as f:
        pos = 0
        tail = b""
        while True:
            buf = f.read(chunk)
            if not buf:
                break
            data = tail + buf
            start = 0
            while True:
                i = data.find(needle, start)
                if i < 0:
                    break
                hits += 1
                lo = max(0, i - args.before)
                hi = min(len(data), i + len(needle) + args.after)
                text = data[lo:hi].decode(args.encoding, "replace")
                print("\n===== hit %d @ offset %d =====" % (hits, pos - len(tail) + i))
                print(text)
                if hits >= args.max:
                    return 0
                start = i + 1
            tail = data[-overlap:] if len(data) > overlap else data
            pos += len(buf)
    print("\n共 %d 处" % hits)
    return 0


if __name__ == "__main__":
    sys.exit(main())
