#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 overrides/ 里的精简描述合并进 product.json（按 prompts[].name 精确覆盖）。

特点：
  * 只改 prompts 中同名条目的 template，其余字段与条目原样保留
  * 写前自动备份到 backups/<时间戳>-product-overrides/
  * --dry-run 只报告，不落盘
  * 同时处理 cli/product.json 与 harness/cli/product.json（两者可能都被读取）

用法：
    python scripts/apply-product-overrides.py --dry-run
    python scripts/apply-product-overrides.py
    python scripts/apply-product-overrides.py --target ai
    python scripts/apply-product-overrides.py --overrides overrides/tools/session-tools.compact.json
"""
import argparse
import builtins
import glob
import json
import os
import shutil
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _install  # noqa: E402  sibling helper: locate an install without hardcoding

PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 各安装点下会出现的 product.json 相对位置（都在 resources/app.asar.unpacked 下）
PRODUCT_RELS = [
    os.path.join("cli", "product.json"),
    os.path.join("node_modules", "@genie", "agent-harness", "cli", "product.json"),
]


def b(s):
    return len((s or "").encode("utf-8"))


def load_targets():
    p = os.path.join(PROJ, "scripts", "targets.json")
    if not os.path.isfile(p):
        return {}
    try:
        d = json.load(open(p, encoding="utf-8"))
    except Exception:
        return {}
    out = {}
    entries = d.get("targets") if isinstance(d, dict) else None
    if isinstance(entries, dict):
        for k, v in entries.items():
            if isinstance(v, dict) and v.get("installPath"):
                out[k] = v["installPath"]
    elif isinstance(entries, list):
        for v in entries:
            if isinstance(v, dict) and v.get("id") and v.get("installPath"):
                out[v["id"]] = v["installPath"]
    return out


def resolve_install(explicit, target):
    """显式 --install > targets.json 里填好的 installPath > 从已发现的 product.json 上溯。"""
    if explicit:
        return explicit
    pinned = load_targets().get(target)
    if pinned:
        return pinned
    found = _install.find_product()
    if found:
        marker = os.path.join("resources", "app.asar.unpacked")
        idx = os.path.normcase(found).find(os.path.normcase(marker))
        if idx > 0:
            return found[:idx]
    return ""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--overrides", nargs="+",
                    default=[os.path.join(PROJ, "overrides", "tools", "session-tools.compact.json")])
    ap.add_argument("--target", default="workbuddy")
    ap.add_argument("--install", help="显式指定安装根目录（覆盖 --target）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--restore", action="store_true",
                    help="从最近一次 *-product-overrides 备份还原 product.json")
    ap.add_argument("--log", default=os.path.join(PROJ, "backups", "apply-product-run.log"),
                    help="提权运行时用于收集输出的日志路径")
    args = ap.parse_args()

    # 提权进程的 stdout 不可见，统一 tee 到日志
    os.makedirs(os.path.dirname(args.log), exist_ok=True)
    logf = open(args.log, "w", encoding="utf-8")
    _print = builtins.print

    def print(*a, **kw):
        _print(*a, **kw)
        kw.pop("flush", None)
        _print(*a, file=logf, **kw)
        logf.flush()

    print("[run] as=%s  at=%s" % (
        os.environ.get("USERNAME", "?"), time.strftime("%Y-%m-%d %H:%M:%S")))

    install = resolve_install(args.install, args.target)
    if not install:
        print("install dir not found; pass --install or pin it in scripts\\targets.json")
        print("  " + _install.HINT)
        return 2
    unpacked = os.path.join(install, "resources", "app.asar.unpacked")

    if args.restore:
        base = os.path.join(PROJ, "backups")
        cands = sorted(d for d in os.listdir(base) if d.endswith("-product-overrides"))
        if not cands:
            print("no *-product-overrides backup found")
            return 2
        src = os.path.join(base, cands[-1])
        n = 0
        for f in os.listdir(src):
            rel = f.replace("__", os.sep)
            dst = os.path.join(unpacked, rel)
            if os.path.isfile(dst):
                shutil.copy2(os.path.join(src, f), dst)
                print("restored %s" % rel)
                n += 1
        print("restored %d file(s) from %s" % (n, src))
        return 0

    # 合并所有 overrides 文件
    patch = {}
    for f in args.overrides:
        if not os.path.isfile(f):
            print("skip (missing): %s" % f)
            continue
        d = json.load(open(f, encoding="utf-8"))
        items = d.get("prompts", d if isinstance(d, list) else [])
        for it in items:
            if not isinstance(it, dict) or not it.get("name"):
                continue
            patch[it["name"]] = it.get("template", "")
    if not patch:
        print("no override entries")
        return 2
    print("overrides: %d entries, %d bytes" % (len(patch), sum(b(v) for v in patch.values())))

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = os.path.join(PROJ, "backups", stamp + "-product-overrides")
    touched = 0
    missing_keys = {k: 0 for k in patch}

    for rel in PRODUCT_RELS:
        path = os.path.join(unpacked, rel)
        if not os.path.isfile(path):
            print("\n[skip] missing: %s" % path)
            continue
        d = json.load(open(path, encoding="utf-8"))
        prompts = d.get("prompts")
        if not isinstance(prompts, list):
            print("\n[skip] %s: prompts is not a list" % path)
            continue

        before = sum(b(p.get("template", "")) for p in prompts)
        changed = []
        for p in prompts:
            name = p.get("name")
            if name in patch:
                old = p.get("template", "")
                new = patch[name]
                if old != new:
                    p["template"] = new
                    changed.append((name, b(old), b(new)))
            elif name in missing_keys:
                missing_keys[name] += 1
        after = sum(b(p.get("template", "")) for p in prompts)

        print("\n%s" % path)
        if not changed:
            print("  no change")
            continue
        for name, o, n in changed:
            print("  %-40s %6d -> %6d (%+d)" % (name, o, n, n - o))
        print("  total %d -> %d bytes (%+d), entries %d (unchanged)" % (
            before, after, after - before, len(prompts)))

        if args.dry_run:
            continue
        os.makedirs(backup_dir, exist_ok=True)
        bpath = os.path.join(backup_dir, rel.replace(os.sep, "__"))
        shutil.copy2(path, bpath)
        with open(path, "w", encoding="utf-8", newline="\n") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
        print("  written; original backed up -> %s" % bpath)
        touched += 1

    miss = {k: v for k, v in missing_keys.items() if v}
    if miss:
        print("\nkeys missing in some product.json (that file left untouched):")
        for k in sorted(miss):
            print("  %s" % k)

    if args.dry_run:
        print("\n[dry-run] nothing written")
    else:
        print("\nwrote %d file(s); backups in %s" % (touched, backup_dir))
        if touched:
            print("Restart WorkBuddy: new sessions pick it up.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
