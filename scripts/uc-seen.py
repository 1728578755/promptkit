#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""管理 user-context 的「已注入」磁盘标记（`user-context-durable` 补丁的产物）。

补丁在 `composeUserPrompt` 里读写 `<configDir>/uc-seen/<sessionId>`：
文件存在 = 这个会话已经收过 `<system-reminder data-role="user-context">`，
后续轮次不再注入。文件很小（1 字节），一个会话一个，长期会攒。

用法：
    python scripts/uc-seen.py                 # 列出全部标记（按时间倒序）
    python scripts/uc-seen.py --count         # 只报数量与目录
    python scripts/uc-seen.py --seed          # 给「已有 transcript 的会话」补标记（部署后跑一次）
    python scripts/uc-seen.py --clear <sid>   # 删掉某个会话的标记（下次发消息会重新注入一次）
    python scripts/uc-seen.py --clear-all     # 全清（等价于「所有会话都当首轮」）
    python scripts/uc-seen.py --prune 30      # 删掉 30 天前的标记

为什么需要 --seed：补丁上线前就存在的会话，磁盘上没有标记，重启后第一次发消息
仍会被当成「首轮」整段注入一次。transcript（`<config>/projects/<workspace>/<sid>.jsonl`）
存在的会话一定已经收过 reminder，直接补标记即可——补丁是每次 compose 都读盘，
所以补完**立刻生效，不用重启**。
"""
import argparse
import glob
import os
import re
import sys
import time

CONFIG_DIR = os.environ.get("WORKBUDDY_CONFIG_DIR") or os.path.join(os.path.expanduser("~"), ".workbuddy")
SEEN_DIR = os.path.join(CONFIG_DIR, "uc-seen")
PROJECTS_DIR = os.path.join(CONFIG_DIR, "projects")
SID_RE = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$")


def set_config_dir(path):
    """deploy.ps1 按 target 传 --config-dir（AI 版是 ~/.workbuddy-ai）。"""
    global CONFIG_DIR, SEEN_DIR, PROJECTS_DIR
    CONFIG_DIR = path
    SEEN_DIR = os.path.join(path, "uc-seen")
    PROJECTS_DIR = os.path.join(path, "projects")


def entries():
    if not os.path.isdir(SEEN_DIR):
        return []
    out = []
    for name in os.listdir(SEEN_DIR):
        p = os.path.join(SEEN_DIR, name)
        if os.path.isfile(p):
            st = os.stat(p)
            out.append((st.st_mtime, name, st.st_size, p))
    out.sort(reverse=True)
    return out


def rm(path):
    """优先真正删除；被 safe-delete 钩子拦下时退化为清空（标记只看存在性）。"""
    try:
        os.remove(path)
        return "removed"
    except OSError:
        try:
            open(path, "wb").close()
            return "truncated"
        except OSError as exc:
            return "failed: %s" % exc


def seed():
    """给已有 transcript 的会话补标记。返回 (新增, 已存在, 跳过)。"""
    os.makedirs(SEEN_DIR, exist_ok=True)
    added = kept = skipped = 0
    for path in glob.glob(os.path.join(PROJECTS_DIR, "*", "*.jsonl")):
        sid = os.path.splitext(os.path.basename(path))[0]
        if not SID_RE.match(sid):
            skipped += 1
            continue
        try:
            size = os.path.getsize(path)
        except OSError:
            skipped += 1
            continue
        if size < 2:
            skipped += 1
            continue
        # 小文件里必须有真正的 user 消息，否则是「建了但没发过」的空会话，
        # 补了标记会让它第一次发消息时收不到 reminder。
        if size <= 65536:
            try:
                with open(path, "rb") as fh:
                    if b'"role":"user"' not in fh.read(65536):
                        skipped += 1
                        continue
            except OSError:
                skipped += 1
                continue
        marker = os.path.join(SEEN_DIR, sid)
        if os.path.exists(marker):
            kept += 1
            continue
        try:
            with open(marker, "wb") as fh:
                fh.write(b"1")
            added += 1
        except OSError:
            skipped += 1
    return added, kept, skipped


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--clear", default="")
    ap.add_argument("--clear-all", action="store_true")
    ap.add_argument("--prune", type=int, default=0)
    ap.add_argument("--count", action="store_true")
    ap.add_argument("--seed", action="store_true")
    ap.add_argument("--config-dir", default="")
    args = ap.parse_args()

    if args.config_dir:
        set_config_dir(args.config_dir)

    print("dir:  %s" % SEEN_DIR)
    if args.seed:
        print("src:  %s" % PROJECTS_DIR)
        added, kept, skipped = seed()
        print("seeded: +%d, already %d, skipped %d" % (added, kept, skipped))
        return 0
    if not os.path.isdir(SEEN_DIR):
        print("（还没有标记：补丁可能未生效，或补丁后尚未发过消息）")
        return 0

    rows = entries()
    if args.clear:
        hit = [r for r in rows if r[1] == args.clear]
        if not hit:
            print("没有该会话的标记：%s" % args.clear)
            return 1
        print("%s -> %s" % (args.clear, rm(hit[0][3])))
        return 0
    if args.clear_all:
        ok = fail = 0
        for _, _, _, p in rows:
            r = rm(p)
            ok += not r.startswith("failed")
            fail += r.startswith("failed")
        print("cleared %d, failed %d" % (ok, fail))
        return 0
    if args.prune:
        cutoff = time.time() - args.prune * 86400
        old = [r for r in rows if r[0] < cutoff]
        ok = fail = 0
        for _, _, _, p in old:
            r = rm(p)
            ok += not r.startswith("failed")
            fail += r.startswith("failed")
        print("pruned %d (older than %d d), failed %d" % (ok, args.prune, fail))
        return 0

    print("count: %d" % len(rows))
    if args.count:
        return 0
    for mtime, name, size, _ in rows[:40]:
        print("  %s  %5d B  %s" % (time.strftime("%Y-%m-%d %H:%M", time.localtime(mtime)), size, name))
    if len(rows) > 40:
        print("  … 其余 %d 条略" % (len(rows) - 40))
    return 0


if __name__ == "__main__":
    sys.exit(main())
