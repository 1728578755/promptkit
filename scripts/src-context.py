#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从文本文件里提取包含指定字符串的上下文窗口。

用法：
    python scripts/src-context.py <file> <needle> [-B 200] [-A 1200] [--max 3]
"""
import argparse
import sys


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("file")
    ap.add_argument("needle")
    ap.add_argument("-B", "--before", type=int, default=200)
    ap.add_argument("-A", "--after", type=int, default=1200)
    ap.add_argument("--max", type=int, default=3)
    args = ap.parse_args()

    text = open(args.file, encoding="utf-8", errors="replace").read()
    n = 0
    start = 0
    while True:
        i = text.find(args.needle, start)
        if i < 0:
            break
        n += 1
        lo = max(0, i - args.before)
        hi = min(len(text), i + len(args.needle) + args.after)
        print("\n===== hit %d @ %d =====" % (n, i))
        print(text[lo:hi])
        if n >= args.max:
            break
        start = i + 1
    print("\n共 %d 处" % n)
    return 0


if __name__ == "__main__":
    sys.exit(main())
