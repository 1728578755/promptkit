#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""把 overrides/ 里的精简描述合并进 product.json（按 prompts[].name 精确覆盖）。

特点：
  * 只改 prompts 中同名条目的 template，其余字段与条目原样保留
  * 写前自动备份到 backups/<时间戳>-product-overrides/
  * --dry-run 只报告，不落盘（连日志也不写）
  * 同时处理 cli/product.json 与 harness/cli/product.json（两者可能都被读取）
  * 退出码诚实：0 = 全部目标已应用（或本来就无变化）；1 = 有目标写入失败；
    2 = 没有可应用的目标（product.json 全缺失 / prompts 非列表 / 无 override 条目）

用法：
    python scripts/apply-product-overrides.py --dry-run
    python scripts/apply-product-overrides.py
    python scripts/apply-product-overrides.py --target ai
    python scripts/apply-product-overrides.py --overrides overrides/tools/session-tools.compact.json
"""
import argparse
import builtins
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

EXIT_OK = 0
EXIT_WRITE_FAILED = 1
EXIT_NO_TARGET = 2


def b(s):
    return len((s or "").encode("utf-8"))


def load_targets():
    p = os.path.join(PROJ, "scripts", "targets.json")
    if not os.path.isfile(p):
        return {}
    try:
        with open(p, encoding="utf-8") as fh:
            d = json.load(fh)
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


def load_patch(paths, print):
    """合并所有 overrides 文件 -> {name: template}。"""
    patch = {}
    for f in paths:
        if not os.path.isfile(f):
            print("skip (missing): %s" % f)
            continue
        with open(f, encoding="utf-8") as fh:
            d = json.load(fh)
        items = d.get("prompts", d if isinstance(d, list) else [])
        if not isinstance(items, list):
            print("skip (prompts is not a list): %s" % f)
            continue
        for it in items:
            if not isinstance(it, dict) or not it.get("name"):
                continue
            patch[it["name"]] = it.get("template", "")
    return patch


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
                    help="提权运行时用于收集输出的日志路径（--dry-run 不写）")
    args = ap.parse_args()

    # 提权进程的 stdout 不可见，统一 tee 到日志。--dry-run 承诺不落盘，连日志也不写
    # （F-11：旧版 dry-run 也会创建/截断 backups\apply-product-run.log）。
    logf = None
    log = None
    if not args.dry_run:
        # tee 到日志；但如果日志路径本身不可写（目录只读/被占用），不能让整个
        # 应用流程失败——降级为只打印到 stdout，并在最后说明。
        log = args.log
        try:
            logdir = os.path.dirname(args.log)
            if logdir:
                os.makedirs(logdir, exist_ok=True)
            logf = open(args.log, "w", encoding="utf-8")
        except OSError as e:
            logf = None
            log = None
            builtins.print("warning: cannot write log %s (%s); continuing with stdout only"
                           % (args.log, e))
    _print = builtins.print

    def print(*a, **kw):
        _print(*a, **kw)
        if logf is not None:
            kw.pop("flush", None)
            _print(*a, file=logf, **kw)
            logf.flush()

    try:
        return run(args, print)
    finally:
        if logf is not None:
            logf.close()
        if log:
            _print("[log] %s" % log)


def run(args, print):
    print("[run] as=%s  at=%s" % (
        os.environ.get("USERNAME", "?"), time.strftime("%Y-%m-%d %H:%M:%S")))

    install = resolve_install(args.install, args.target)
    if not install:
        print("install dir not found; pass --install or pin it in scripts\\targets.json")
        print("  " + _install.HINT)
        return EXIT_NO_TARGET
    unpacked = os.path.join(install, "resources", "app.asar.unpacked")

    if args.restore:
        base = os.path.join(PROJ, "backups")
        if not os.path.isdir(base):
            print("no backups dir; nothing to restore")
            return EXIT_NO_TARGET
        cands = sorted(d for d in os.listdir(base) if d.endswith("-product-overrides"))
        if not cands:
            print("no *-product-overrides backup found")
            return EXIT_NO_TARGET
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
        return EXIT_OK if n else EXIT_NO_TARGET

    patch = load_patch(args.overrides, print)
    if not patch:
        print("no override entries")
        return EXIT_NO_TARGET
    print("overrides: %d entries, %d bytes" % (len(patch), sum(b(v) for v in patch.values())))

    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup_dir = os.path.join(PROJ, "backups", stamp + "-product-overrides")

    # 先找出真正可应用的目标：product.json 存在且 prompts 是列表。
    # （F-11：旧版在 for 循环里统计缺失键，`elif name in missing_keys` 永远不可达，
    #  且所有目标都缺失时静默 return 0。）
    targets = []
    for rel in PRODUCT_RELS:
        path = os.path.join(unpacked, rel)
        if not os.path.isfile(path):
            print("\n[skip] missing: %s" % path)
            continue
        try:
            with open(path, encoding="utf-8") as fh:
                d = json.load(fh)
        except Exception as e:
            print("\n[skip] %s: cannot read (%s)" % (path, e))
            continue
        prompts = d.get("prompts") if isinstance(d, dict) else None
        if not isinstance(prompts, list):
            print("\n[skip] %s: prompts is not a list" % path)
            continue
        targets.append((rel, path, d, prompts))

    if not targets:
        print("\n未找到任何可应用的目标（%s 下没有可用的 product.json）" % unpacked)
        print("  expected one of: %s" % ", ".join(PRODUCT_RELS))
        return EXIT_NO_TARGET

    # 缺失统计：同一个 key 可能只存在于其中一个 product.json，属于正常。
    # 只有在所有目标里都找不到的 key 才值得报警（旧版这段逻辑不可达）。
    present_names = set()
    for _rel, _path, _d, prompts in targets:
        for p in prompts:
            if isinstance(p, dict) and p.get("name"):
                present_names.add(p["name"])
    never_applied = sorted(k for k in patch if k not in present_names)
    any_matched = bool(present_names & set(patch))
    if never_applied and any_matched:
        # 部分覆盖是正常的（overrides 可以同时服务多个产品版本），只提示。
        print("\noverride keys absent from every product.json (skipped, not an error):")
        for k in never_applied:
            print("  %s" % k)

    touched = 0
    unchanged = 0
    failed = 0

    for rel, path, d, prompts in targets:
        before = sum(b(p.get("template", "")) for p in prompts)
        changed = []
        for p in prompts:
            if not isinstance(p, dict):
                continue
            name = p.get("name")
            if name in patch:
                old = p.get("template", "")
                new = patch[name]
                if old != new:
                    p["template"] = new
                    changed.append((name, b(old), b(new)))
        after = sum(b(p.get("template", "")) for p in prompts)

        print("\n%s" % path)
        if not changed:
            print("  no change")
            unchanged += 1
            continue
        for name, o, n in changed:
            print("  %-40s %6d -> %6d (%+d)" % (name, o, n, n - o))
        print("  total %d -> %d bytes (%+d), entries %d (unchanged)" % (
            before, after, after - before, len(prompts)))

        if args.dry_run:
            continue
        bpath = os.path.join(backup_dir, rel.replace(os.sep, "__"))
        try:
            os.makedirs(backup_dir, exist_ok=True)
            shutil.copy2(path, bpath)
            with open(path, "w", encoding="utf-8", newline="\n") as f:
                json.dump(d, f, ensure_ascii=False, indent=2)
        except OSError as e:
            print("  WRITE FAILED: %s" % e)
            failed += 1
            continue
        print("  written; original backed up -> %s" % bpath)
        touched += 1

    if args.dry_run:
        print("\n[dry-run] nothing written")
        return EXIT_OK

    print("\nwrote %d file(s), unchanged %d, failed %d" % (touched, unchanged, failed))
    if touched:
        print("  backups: %s" % backup_dir)
        print("Restart WorkBuddy: new sessions pick it up.")
    if failed:
        print("有些目标写入失败（见上），退出码 1。")
        return EXIT_WRITE_FAILED
    if not any_matched:
        # 每个 override key 在所有目标里都不存在：这不是“已应用”，是没找到地方应用。
        # （never_applied 只要求**至少一个** key 缺失，正常的部分覆盖也会命中它，
        #   所以不能用它来判断失败。）
        print("overrides 与目标 product.json 完全不匹配，未改动任何文件，退出码 2。")
        return EXIT_NO_TARGET
    return EXIT_OK


if __name__ == "__main__":
    sys.exit(main())
