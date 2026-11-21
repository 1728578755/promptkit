#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""从最近一次真实请求里量各注入块的体积与标记出现次数。

trace 里 span.toolInput 是模型请求体的前缀（运行时截断在约 20,000 字符）。
只看这一段：它是唯一能证明“模型到底收到什么”的证据；
[PromptRenderer] Rendered (N chars) 是 templates\\*.tpl 的旁路数字，不能作为判据。

用法：
    python scripts/measure-prompt-blocks.py                 # 最近一次
    python scripts/measure-prompt-blocks.py --list          # 列最近 10 个候选
    python scripts/measure-prompt-blocks.py --trace <path>
    python scripts/measure-prompt-blocks.py --expect-absent current_time,product_identity,craft_mode
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

# 注入块（按渲染顺序）。用于归因：每块占多少字符。
# 2026-11-17 起 quality_policy / agent_loop / regional_conventions 已按用户要求移除，不再出现在注入里。
BLOCKS = [
    "refusal_policy", "personal_files_safety",
    "agent_contract", "result_presentation", "final_answer_instructions",
    "tool_use", "search_routing", "tool_routing", "task_management", "asking_questions",
    "tool_usage_policy", "agent_skills", "response_language", "memory_system",
    "user_memory", "user_info", "identity_context", "product_identity",
    "user_custom_instructions", "current_time", "craft_mode", "project_context",
    "system-reminder", "industry_mode_context", "boundaries", "interaction_principles",
    "core_capabilities", "tool_and_skill_principles", "tone_and_style",
]


def candidates(root):
    out = []
    for p in glob.glob(os.path.join(root, "*", "trace_*.jsonl")):
        out.append((os.path.getmtime(p), p))
    out.sort(reverse=True)
    return out


def extract(path):
    """返回 (system_text, meta)。toolInput 是 JSON 数组字符串，取第一条 system。"""
    best = ("", {})
    with open(path, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if "toolInput" not in line:
                continue
            try:
                obj = json.loads(line)
            except Exception:
                continue
            ti = (obj.get("span") or {}).get("toolInput")
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
                    if len(text) > len(best[0]):
                        best = (text, {"name": (obj.get("span") or {}).get("name", "")})
                    break
    return best


def block_sizes(text):
    rows = []
    for name in BLOCKS:
        open_tag = "<%s>" % name
        close_tag = "</%s>" % name
        i = text.find(open_tag)
        if i < 0:
            continue
        j = text.find(close_tag, i)
        end = j + len(close_tag) if j > 0 else min(len(text), i + 4000)
        rows.append((name, end - i, text.count(open_tag)))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--target", default="workbuddy", choices=list(TARGETS))
    ap.add_argument("--trace", default="")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--expect-absent", default="")
    ap.add_argument("--expect-present", default="")
    ap.add_argument("--top", type=int, default=12)
    args = ap.parse_args()

    root = TARGETS[args.target]
    cands = candidates(root)
    if not cands:
        print("没有 trace: %s" % root)
        return 2

    if args.list:
        print("%-19s %-10s %10s" % ("时间", "pid", "size"))
        for mt, p in cands[:10]:
            text, _ = extract(p)
            print("%-19s %-10s %10d  %s" % (
                time.strftime("%m-%d %H:%M:%S", time.localtime(mt)),
                os.path.basename(os.path.dirname(p)), len(text), os.path.basename(p)))
        return 0

    if args.trace:
        path = args.trace
    else:
        path = ""
        for _mt, p in cands[: args.top]:
            text, _ = extract(p)
            if text:
                path = p
                break
        if not path:
            print("最近 %d 个 trace 里没有完整 system" % args.top)
            return 2

    text, meta = extract(path)
    if not text:
        print("未提取到 system: %s" % path)
        return 2

    print("trace: %s" % path)
    print("system: %d 字符" % len(text))
    print()
    print("%-26s %8s %6s" % ("块", "字符", "出现"))
    print("-" * 44)
    total = 0
    for name, size, cnt in block_sizes(text):
        total += size
        flag = "" if cnt == 1 else "  <-- %d 次" % cnt
        print("%-26s %8d %6d%s" % ("<" + name + ">", size, cnt, flag))
    print("-" * 44)
    print("%-26s %8d" % ("已归因合计", total))

    rc = 0
    absent = [s.strip() for s in args.expect_absent.split(",") if s.strip()]
    if absent:
        print()
        bad = []
        for name in absent:
            hit = "<%s>" % name
            n = text.count(hit)
            print("  %-24s %s" % (hit, "OK 不存在" if n == 0 else "!! 仍存在 %d 次" % n))
            if n:
                bad.append(hit)
        if bad:
            rc = 1
    present = [s.strip() for s in args.expect_present.split(",") if s.strip()]
    if present:
        print()
        bad2 = []
        for name in present:
            hit = "<%s>" % name
            n = text.count(hit)
            print("  %-24s %s" % (hit, "OK 存在" if n else "!! 缺失"))
            if not n:
                bad2.append(hit)
        if bad2:
            rc = 1
    return rc


if __name__ == "__main__":
    sys.exit(main())
