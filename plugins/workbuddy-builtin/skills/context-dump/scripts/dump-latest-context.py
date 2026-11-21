#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Print the latest real WorkBuddy request's context from its trace.

Why: a model asked to "print the full context from memory" reliably
confabulates (a generic Claude Code prompt, its own earlier answer, repeated
blocks).  The trace's `span.toolInput` is the only local evidence of what was
actually sent.

The trace only keeps the first 20,000 chars of `toolInput`; this script says
so explicitly instead of filling the gap.

Usage:
    python scripts/dump-latest-context.py --latest
    python scripts/dump-latest-context.py --latest --json
    python scripts/dump-latest-context.py --list
    python scripts/dump-latest-context.py --trace <path>
    python scripts/dump-latest-context.py --latest --out context.txt
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
FINGERPRINT = "<refusal_policy>"
PREFIX_LIMIT = 20000


def traces(root):
    out = []
    for p in glob.glob(os.path.join(root, "*", "trace_*.jsonl")):
        out.append((os.path.getmtime(p), p))
    out.sort(reverse=True)
    return out


def candidates(root):
    """(startedAt, tracePath, raw toolInput, parseable messages or None)."""
    rows = []
    for _, path in traces(root):
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "toolInput" not in line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                span = obj.get("span") or {}
                raw = span.get("toolInput")
                if not isinstance(raw, str) or len(raw) < 2000:
                    continue
                if FINGERPRINT not in raw:
                    continue
                try:
                    msgs = json.loads(raw)
                except Exception:
                    msgs = None
                if msgs is not None and not isinstance(msgs, list):
                    continue
                rows.append((span.get("startedAt", ""), path, raw, msgs))
    rows.sort(key=lambda r: r[0], reverse=True)
    return rows


def text_of(content):
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for p in content:
            if isinstance(p, str):
                parts.append(p)
            elif isinstance(p, dict):
                if isinstance(p.get("text"), str):
                    parts.append(p["text"])
                elif p.get("type"):
                    parts.append(json.dumps(p, ensure_ascii=False))
        return "\n".join(parts)
    return json.dumps(content, ensure_ascii=False)


def render(rows, as_json=False):
    if not rows:
        return "no WorkBuddy trace with <refusal_policy> found"
    started, path, raw, msgs = rows[0]
    if as_json:
        return json.dumps({
            "trace": path,
            "startedAt": started,
            "prefixChars": len(raw),
            "truncated": msgs is None,
            "raw": raw if msgs is None else None,
            "messages": msgs,
        }, ensure_ascii=False, indent=2)

    lines = [
        "# latest WorkBuddy request context",
        "trace: %s" % path,
        "startedAt: %s" % started,
        "prefixChars: %d" % len(raw),
    ]
    if msgs is None:
        lines += [
            "",
            "[TRUNCATED] the trace only stores a %d-char prefix of toolInput; "
            "it is not valid JSON.  Output below is that exact prefix; do not "
            "fill the gap from memory." % PREFIX_LIMIT,
            "",
            raw,
        ]
        return "\n".join(lines)

    lines.append("messages: %d" % len(msgs))
    lines.append("")
    for i, m in enumerate(msgs):
        if not isinstance(m, dict):
            lines.append("## [%d] raw\n\n%s\n" % (i, json.dumps(m, ensure_ascii=False)))
            continue
        role = m.get("role", "?")
        body = text_of(m.get("content"))
        lines.append("## [%d] %s (%d chars)" % (i, role, len(body)))
        lines.append("")
        lines.append(body)
        lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--latest", action="store_true", help="latest request (default)")
    g.add_argument("--list", action="store_true", help="list the 10 latest requests")
    g.add_argument("--trace", help="specific trace jsonl")
    ap.add_argument("--target", choices=sorted(TARGETS), default="workbuddy")
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--out")
    args = ap.parse_args()

    root = TARGETS[args.target]
    if args.trace:
        path = args.trace
        rows = []
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "toolInput" not in line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                span = obj.get("span") or {}
                raw = span.get("toolInput")
                if not isinstance(raw, str) or len(raw) < 2000 or FINGERPRINT not in raw:
                    continue
                try:
                    msgs = json.loads(raw)
                except Exception:
                    msgs = None
                rows.append((span.get("startedAt", ""), path, raw, msgs))
        rows.sort(key=lambda r: r[0], reverse=True)
    else:
        rows = candidates(root)

    if args.list:
        for started, path, raw, msgs in rows[:10]:
            print("%s  %6d  %s%s" % (started, len(raw), path, "" if msgs is not None else "  [TRUNCATED]"))
        return 0

    text = render(rows, as_json=args.json)
    if args.out:
        with open(args.out, "w", encoding="utf-8", newline="\n") as fh:
            fh.write(text + "\n")
        print("SAVED %s (%d chars)" % (args.out, len(text)))
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
