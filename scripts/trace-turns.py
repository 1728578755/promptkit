#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""逐轮读出 traces 里真实请求的「消息序列」，用来判断某个注入块是首轮一次还是每轮都有。

与 dump-system-prompt.py 的分工：
  dump-system-prompt.py 只取最新一条 system 文本（看块顺序）；
  本脚本按 generation span 的时间顺序，把同一 trace 里每次真实请求列一行，
  并统计最后一条 user 消息里是否还带 <user_info> / <user_custom_instructions> /
  <project_context> / <connector-status> / <system-reminder>。

用法：
    python scripts/trace-turns.py                    # 最近 6 个 trace，每个的内部轮次
    python scripts/trace-turns.py --trace <path>
    python scripts/trace-turns.py --all              # 所有 trace
    python scripts/trace-turns.py --block user_info  # 只看某一块的命中
"""
import argparse
import glob
import json
import os
import sys

HOME = os.path.expanduser("~")
TARGETS = {
    "workbuddy": os.path.join(HOME, ".workbuddy", "traces"),
    "workbuddy-ai": os.path.join(HOME, ".workbuddy-ai", "traces"),
}

BLOCKS = [
    "<user_info>",
    "<user_custom_instructions>",
    "<project_context>",
    "<connector-status>",
    "<system-reminder",
    "<working_memory_content>",
]


def generations(path):
    """产出 (startedAt, messages)：trace 里每次真实模型请求的消息列表。"""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "toolInput" not in line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            span = obj.get("span") or {}
            ti = span.get("toolInput")
            if not isinstance(ti, str) or len(ti) < 200:
                continue
            try:
                msgs = json.loads(ti)
            except Exception:
                continue
            if not isinstance(msgs, list) or not msgs:
                continue
            if not all(isinstance(m, dict) and "role" in m for m in msgs):
                continue
            yield span.get("startedAt", ""), msgs


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        out = []
        for part in content:
            if isinstance(part, dict):
                out.append(str(part.get("text") or ""))
            else:
                out.append(str(part))
        return "".join(out)
    return json.dumps(content, ensure_ascii=False) if content is not None else ""


def analyse(path, only=None, verbose=False):
    rows = []
    for started, msgs in generations(path):
        sys_len = sum(len(text_of(m.get("content"))) for m in msgs if m.get("role") == "system")
        users = [m for m in msgs if m.get("role") == "user"]
        assistants = [m for m in msgs if m.get("role") == "assistant"]
        last_user = text_of(users[-1].get("content")) if users else ""
        hits = [b for b in (BLOCKS if not only else [only]) if b in last_user]
        rows.append(
            {
                "started": started,
                "msgs": len(msgs),
                "sys": sys_len,
                "user_len": len(last_user),
                "n_user": len(users),
                "n_asst": len(assistants),
                "hits": hits,
                "head": last_user[:70].replace("\n", " "),
            }
        )
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="workbuddy", choices=list(TARGETS))
    ap.add_argument("--trace", default="")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--limit", type=int, default=6)
    ap.add_argument("--block", default="")
    ap.add_argument("--verbose", action="store_true")
    args = ap.parse_args()

    if args.trace:
        files = [args.trace]
    else:
        root = TARGETS[args.target]
        if not os.path.isdir(root):
            print("no traces root: %s" % root)
            return 2
        cand = []
        for p in glob.glob(os.path.join(root, "*", "trace_*.jsonl")):
            cand.append((os.path.getmtime(p), p))
        cand.sort(reverse=True)
        files = [p for _, p in (cand if args.all else cand[: args.limit])]

    total_multi = 0
    for path in files:
        rows = analyse(path, only=args.block or None, verbose=args.verbose)
        if not rows:
            continue
        if len(rows) < 2 and not args.all:
            # 单次请求的 trace 对「是否每轮重发」没有信息量，只在 --all 时列
            pass
        print("=" * 100)
        print("%s  (%d request, %s)" % (os.path.basename(path), len(rows), path))
        if len(rows) >= 2:
            total_multi += 1
        print("  %-24s %4s %8s %8s %5s %5s  %s" % ("startedAt", "msgs", "sys_len", "user_len", "nU", "nA", "hits"))
        for i, r in enumerate(rows, 1):
            print(
                "  #%-2d %-21s %4d %8d %8d %5d %5d  %s"
                % (i, r["started"], r["msgs"], r["sys"], r["user_len"], r["n_user"], r["n_asst"],
                   ",".join(h[1:-1] if h.startswith("<") else h for h in r["hits"]) or "-")
            )
    print("\nmulti-request traces: %d / %d" % (total_multi, len(files)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
