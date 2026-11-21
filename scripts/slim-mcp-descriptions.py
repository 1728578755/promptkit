#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""精简本地 MCP 插件的工具描述常量（当前覆盖 miora 的 3 个共享常量）。

背景：miora 的 8 个工具都用
      description = "<独有段> " + SCENARIO_GATE_DESCRIPTION + " " + TEMP_KEY_PREFLIGHT_DESCRIPTION + " " + DELIVERY_DESCRIPTION ...
   三个常量共 1,266 字节，却在 8 个工具里各出现一次 —— 注入侧实际占约 10 KB。
   压常量等于一次性压 8 个工具的重复部分，且不改任何逻辑。

安全性：
  * 只替换这三个 `const XXX_DESCRIPTION = '...'` 的字符串字面量，不动其他代码
  * 写前备份到 backups/<时间戳>-mcp-desc/
  * 写后跑 `node --check`，语法不过就自动还原
  * 幂等：已经是精简文本就跳过

注意：插件升级会覆盖这些文件，升级后重跑本脚本即可。

用法：
    python scripts/slim-mcp-descriptions.py --dry-run
    python scripts/slim-mcp-descriptions.py
    python scripts/slim-mcp-descriptions.py --restore
"""
import argparse
import glob
import os
import re
import shutil
import subprocess
import sys
import time

HOME = os.path.expanduser("~")
CACHE = os.path.join(HOME, ".workbuddy", "plugins", "cache", "workbuddy-builtin")
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 常量名 -> 精简后的字符串（保留全部约束，只压冗词）
NEW = {
    "SCENARIO_GATE_DESCRIPTION":
        'ONLY inside the "\u591a\u6a21\u6001\u5185\u5bb9\u751f\u6210" (Multimodal Generation) scenario; '
        'never for general chat or unrelated skills.',
    "TEMP_KEY_PREFLIGHT_DESCRIPTION":
        "PREFLIGHT: call `connect_cloud_service` in this turn (or reuse a still-valid `clientTempToken` "
        "from this session) and pass it as `tempKey`. Never invent or hard-code one. On "
        "AUTH_INVALID_TEMP_KEY / AUTH_MISSING_TEMP_KEY, re-call it for a fresh token and retry.",
    "DELIVERY_DESCRIPTION":
        "On success, pass `resultFiles[].localPath` to `miora_write_canvas` as `items[].source_path`; "
        "hand the returned `copied_assets` paths to `present_files` \u2014 never `localPath` (hidden "
        "plugin dir, never becomes an artifact) and never `signedUrl` (opens an in-app browser panel "
        "and expires). Only use `signedUrl` when `localPath` is absent.",
}

PATTERN = r"((?:const|let|var)\s+%s\s*=\s*)(['\"])(?:(?!\2)[^\\]|\\.)*\2"


def js_literal(text):
    """用单引号包裹，转义反斜杠与单引号。"""
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def b(s):
    return len((s or "").encode("utf-8"))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--root", default=CACHE)
    args = ap.parse_args()

    files = sorted(glob.glob(os.path.join(args.root, "mcp-*", "*", "dist", "*.cjs")))
    if not files:
        print("没有找到 mcp-*/dist/*.cjs")
        return 2

    stamp = time.strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(PROJ, "backups", stamp + "-mcp-desc")

    if args.restore:
        base = os.path.join(PROJ, "backups")
        cands = sorted(d for d in os.listdir(base) if d.endswith("-mcp-desc"))
        if not cands:
            print("no *-mcp-desc backup")
            return 2
        src = os.path.join(base, cands[-1])
        n = 0
        for f in os.listdir(src):
            dst = os.path.join(args.root, f.replace("__", os.sep))
            if os.path.isfile(dst):
                shutil.copy2(os.path.join(src, f), dst)
                n += 1
                print("restored %s" % dst)
        print("restored %d file(s)" % n)
        return 0

    total_before = total_after = 0
    for path in files:
        src = open(path, encoding="utf-8", errors="surrogateescape", newline="").read()
        before = b(src)
        changed = []
        out = src
        for name, new_text in NEW.items():
            pat = re.compile(PATTERN % re.escape(name))
            m = pat.search(out)
            if not m:
                continue
            old_lit = m.group(0)[len(m.group(1)):]
            new_lit = js_literal(new_text)
            if old_lit == new_lit:
                continue
            out = out[: m.start()] + m.group(1) + new_lit + out[m.end():]
            changed.append((name, b(old_lit), b(new_lit)))
        after = b(out)
        rel = os.path.relpath(path, args.root)
        if not changed:
            print("%-52s 已是最简" % rel[:52])
            continue
        print("%-52s %d -> %d 字节" % (rel[:52], before, after))
        for n, o, w in changed:
            print("    %-32s %4d -> %4d" % (n, o, w))
        total_before += before
        total_after += after
        if args.dry_run:
            continue
        os.makedirs(bdir, exist_ok=True)
        bpath = os.path.join(bdir, os.path.relpath(path, args.root).replace(os.sep, "__"))
        shutil.copy2(path, bpath)
        with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
            f.write(out)
        # 语法校验：失败就还原
        r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
        if r.returncode != 0:
            shutil.copy2(bpath, path)
            print("    !! node --check 失败，已还原：%s" % (r.stderr.strip().splitlines() or [''])[-1])
            return 1
        print("    node --check OK")

    print("\n合计 %d -> %d 字节（%+d）" % (total_before, total_after, total_after - total_before))
    if args.dry_run:
        print("[dry-run] nothing written")
    else:
        print("备份 -> %s" % bdir)
        print("重启 WorkBuddy（重新拉起 MCP server）后生效；升级会覆盖，重跑本脚本。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
