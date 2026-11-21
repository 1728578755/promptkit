#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把最近一次真实请求里的 **WorkBuddy** system 提示词落盘，并打印「块出现顺序」索引。

为什么不能只取"最长的那条 system"：同一个 trace 文件里可能混着 CodeBuddy 子代理的请求，
它有自己的 system（含 `<content_policy>`，不含 `<refusal_policy>`），按长度会选错。
这里用本仓库注入的 `<refusal_policy>` 做指纹，并且同样本按 startedAt 取最新。

用法：
    python scripts/dump-system-prompt.py                    # 最新一条 WorkBuddy system
    python scripts/dump-system-prompt.py --out out/sys.txt   # 同时落盘
    python scripts/dump-system-prompt.py --list              # 列最近 10 条候选（含时间/长度）
    python scripts/dump-system-prompt.py --trace <path>      # 指定 trace 文件
"""
import argparse
import glob
import json
import os
import re
import sys

HOME = os.path.expanduser("~")
TARGETS = {
    "workbuddy": os.path.join(HOME, ".workbuddy", "traces"),
    "workbuddy-ai": os.path.join(HOME, ".workbuddy-ai", "traces"),
}

# WorkBuddy 定制提示词的指纹；CodeBuddy 子代理的 system 没有它
FINGERPRINT = "<refusal_policy>"
TAG_RE = re.compile(r"<([A-Za-z_][\w-]*)>")


def system_texts(path):
    """产出 (startedAt, text)：该 trace 里所有够长的 system 消息。"""
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
            if not isinstance(ti, str) or len(ti) < 2000:
                continue
            try:
                msgs = json.loads(ti)
            except Exception:
                continue
            if not isinstance(msgs, list):
                continue
            for m in msgs:
                if isinstance(m, dict) and m.get("role") == "system":
                    c = m.get("content")
                    text = c if isinstance(c, str) else json.dumps(c, ensure_ascii=False)
                    yield span.get("startedAt", ""), text
                    break


def candidates(root):
    out = []
    for p in glob.glob(os.path.join(root, "*", "trace_*.jsonl")):
        out.append((os.path.getmtime(p), p))
    out.sort(reverse=True)
    return out


def order(text):
    first = {}
    for m in TAG_RE.finditer(text):
        first.setdefault(m.group(1), m.start())
    return sorted(((p, n) for n, p in first.items()))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="workbuddy", choices=list(TARGETS))
    ap.add_argument("--trace", default="")
    ap.add_argument("--out", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--any", action="store_true", help="不要求指纹（看子代理/其他 system 时用）")
    args = ap.parse_args()

    root = TARGETS[args.target]
    files = [args.trace] if args.trace else [p for _mt, p in candidates(root)[:40]]

    found = []  # (startedAt, text, file)
    for p in files:
        try:
            for started, text in system_texts(p):
                if not args.any and FINGERPRINT not in text:
                    continue
                found.append((started, text, p))
        except OSError:
            continue
    if not found:
        print("没找到符合条件的 system（试试 --any 或 --trace）")
        return 2

    found.sort(key=lambda r: r[0], reverse=True)

    if args.list:
        print("%-26s %8s  %s" % ("startedAt", "字符", "trace"))
        for started, text, p in found[:10]:
            print("%-26s %8d  %s" % (started, len(text), p))
        return 0

    started, text, path = found[0]
    print("trace     : %s" % path)
    print("startedAt : %s" % started)
    print("字符数    : %d" % len(text))
    print()
    print("%-8s %s" % ("位置", "顶层标签（首次出现顺序）"))
    print("-" * 52)
    for pos, name in order(text):
        print("%-8d <%s>" % (pos, name))

    if args.out:
        d = os.path.dirname(os.path.abspath(args.out))
        if d:
            os.makedirs(d, exist_ok=True)
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text)
        print()
        print("已写出: %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
