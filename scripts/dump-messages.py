#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""打印一次真实请求的消息序列，并逐条标出 4 段 user-context 注入是否出现。

用途（2026-11-17 起）：验证硬性规则「<system-reminder data-role="user-context">
只在会话第一轮出现」。判据看 flags 列：
    I = <user_info>   C = <user_custom_instructions>
    P = <project_context>   S = <connector-status>
第一轮应当是 ICPS，第二轮起应当是 `----`。

默认挑「消息最多」的那条 WorkBuddy 请求（= 当前会话走得最远的一轮），
因为 trace 只记录部分请求，取最新一条可能还是首轮。

用法：
    python scripts/dump-messages.py                # 最深的一轮
    python scripts/dump-messages.py --list         # 列候选（时间/消息数/flags 摘要）
    python scripts/dump-messages.py --head 160     # 每段展示多少字符
    python scripts/dump-messages.py --trace <p>    # 指定 trace 文件
"""
import argparse
import glob
import json
import os
import sys

HOME = os.path.expanduser("~")
ROOTS = {
    "workbuddy": os.path.join(HOME, ".workbuddy", "traces"),
    "workbuddy-ai": os.path.join(HOME, ".workbuddy-ai", "traces"),
}
FINGERPRINT = "<refusal_policy>"
FLAG_TAGS = [
    ("<user_info>", "I"),
    ("<user_custom_instructions>", "C"),
    ("<project_context>", "P"),
    ("<connector-status>", "S"),
]


def candidates(root):
    out = [(os.path.getmtime(p), p) for p in glob.glob(os.path.join(root, "*", "trace_*.jsonl"))]
    out.sort(reverse=True)
    return out


def text_of(m):
    c = m.get("content")
    if isinstance(c, str):
        return c
    if isinstance(c, list):
        return "".join(x.get("text", "") for x in c if isinstance(x, dict))
    return json.dumps(c, ensure_ascii=False)


def requests_in(path):
    """产出 (startedAt, messages)。"""
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
            if not isinstance(ti, str) or len(ti) < 1500:
                continue
            try:
                msgs = json.loads(ti)
            except Exception:
                continue
            if isinstance(msgs, list) and msgs:
                yield span.get("startedAt", ""), msgs


def flags_of(text):
    return "".join(letter if tag in text else "-" for tag, letter in FLAG_TAGS)


def is_workbuddy(msgs):
    if not msgs or msgs[0].get("role") != "system":
        return False
    return FINGERPRINT in text_of(msgs[0])


def summarize(msgs):
    users = [m for m in msgs if m.get("role") == "user"]
    return ",".join(flags_of(text_of(m)) for m in users)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="workbuddy", choices=list(ROOTS))
    ap.add_argument("--trace", default="")
    ap.add_argument("--head", type=int, default=110)
    ap.add_argument("--list", action="store_true")
    args = ap.parse_args()

    files = [args.trace] if args.trace else [p for _mt, p in candidates(ROOTS[args.target])[:40]]
    found = []
    for p in files:
        try:
            for started, msgs in requests_in(p):
                if not is_workbuddy(msgs):
                    continue
                found.append((started, msgs, p))
        except OSError:
            continue
    if not found:
        print("没找到 WorkBuddy 请求（检查 --target / --trace）")
        return 2

    if args.list:
        print("%-26s %4s  %s" % ("startedAt", "msgs", "每条 user 消息的 I/C/P/S"))
        for started, msgs, p in sorted(found, key=lambda r: r[0], reverse=True)[:12]:
            print("%-26s %4d  %s" % (started, len(msgs), summarize(msgs)))
        return 0

    found.sort(key=lambda r: (len(r[1]), r[0]), reverse=True)
    started, msgs, path = found[0]

    print("trace     : %s" % path)
    print("startedAt : %s" % started)
    print("消息数    : %d" % len(msgs))
    print("flags     : %s   （I=user_info C=自定义指令 P=project_context S=connector-status）" % summarize(msgs))
    print()
    for i, m in enumerate(msgs):
        t = text_of(m)
        head = t[: args.head].replace("\r", "\\r").replace("\n", "\\n")
        print("[%02d] %-9s %6d  %-5s %s" % (i, m.get("role"), len(t), flags_of(t), head))
    return 0


if __name__ == "__main__":
    sys.exit(main())
