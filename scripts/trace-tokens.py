#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""列出最近几个会话的首轮 / 末轮 prompt_tokens，用于验证注入体积是否真的下降。

首轮 tokens ≈ 该会话的固定成本（系统提示词 + 工具定义 + 清单）；
末轮 - 首轮 ≈ 对话历史增量。

用法：
    python scripts/trace-tokens.py                    # 最近 6 个会话
    python scripts/trace-tokens.py --target workbuddy-ai
    python scripts/trace-tokens.py --min 20000        # 只看首轮超过 2 万的
"""
import argparse
import glob
import json
import os
import re
import sys
import time

HOME = os.path.expanduser("~")
TARGETS = {
    "workbuddy": os.path.join(HOME, ".workbuddy", "traces"),
    "workbuddy-ai": os.path.join(HOME, ".workbuddy-ai", "traces"),
}
TOKEN_RE = re.compile(r'prompt_tokens\\?"\s*:\s*(\d+)')


def sessions(trace_root):
    rows = []
    for p in glob.glob(os.path.join(trace_root, "*", "*.jsonl")):
        toks = []
        started = os.path.getmtime(p)
        with open(p, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "prompt_tokens" not in line:
                    continue
                m = TOKEN_RE.search(line)
                if m:
                    toks.append(int(m.group(1)))
        if toks:
            rows.append({"pid": os.path.basename(os.path.dirname(p)),
                         "file": os.path.basename(p)[:12],
                         "n": len(toks), "first": toks[0], "last": toks[-1],
                         "mtime": started})
    rows.sort(key=lambda r: -r["mtime"])
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="workbuddy", choices=list(TARGETS))
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--min", type=int, default=0)
    args = ap.parse_args()

    rows = [r for r in sessions(TARGETS[args.target]) if r["first"] >= args.min]
    if not rows:
        print("没有找到带 prompt_tokens 的 trace")
        return 1
    print("%-19s %-8s %-13s %4s %10s %10s %9s" % (
        "时间", "pid", "trace", "轮数", "首轮", "末轮", "历史增量"))
    print("-" * 82)
    for r in rows[: args.top]:
        print("%-19s %-8s %-13s %4d %10d %10d %9d" % (
            time.strftime("%m-%d %H:%M:%S", time.localtime(r["mtime"])),
            r["pid"], r["file"], r["n"], r["first"], r["last"],
            r["last"] - r["first"]))
    print("\n首轮 = 该会话的固定注入成本；比较重启前后新会话的首轮即可验证瘦身效果。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
