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
  * 写后跑 `node --check`，语法不过就自动还原；没有 Node 时降级为读回比对
    （不抛 FileNotFoundError，也不把“没校验”当成“校验通过”）
  * 幂等：已经是精简文本就跳过
  * 同时支持 ~/.workbuddy 与 ~/.workbuddy-ai（CN / 国际版）；两者都存在时
    两个缓存目录都会被处理

注意：插件升级会覆盖这些文件，升级后重跑本脚本即可。

用法：
    python scripts/slim-mcp-descriptions.py --dry-run
    python scripts/slim-mcp-descriptions.py
    python scripts/slim-mcp-descriptions.py --restore
    python scripts/slim-mcp-descriptions.py --root <cache dir>
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
PROJ = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# 常量名 -> 精简后的字符串（保留全部约束，只压冗词）
NEW = {
    "SCENARIO_GATE_DESCRIPTION":
        'ONLY inside the "多模态内容生成" (Multimodal Generation) scenario; '
        'never for general chat or unrelated skills.',
    "TEMP_KEY_PREFLIGHT_DESCRIPTION":
        "PREFLIGHT: call `connect_cloud_service` in this turn (or reuse a still-valid `clientTempToken` "
        "from this session) and pass it as `tempKey`. Never invent or hard-code one. On "
        "AUTH_INVALID_TEMP_KEY / AUTH_MISSING_TEMP_KEY, re-call it for a fresh token and retry.",
    "DELIVERY_DESCRIPTION":
        "On success, pass `resultFiles[].localPath` to `miora_write_canvas` as `items[].source_path`; "
        "hand the returned `copied_assets` paths to `present_files` — never `localPath` (hidden "
        "plugin dir, never becomes an artifact) and never `signedUrl` (opens an in-app browser panel "
        "and expires). Only use `signedUrl` when `localPath` is absent.",
}

PATTERN = r"((?:const|let|var)\s+%s\s*=\s*)(['\"])(?:(?!\2)[^\\]|\\.)*\2"


def js_literal(text):
    """用单引号包裹，转义反斜杠与单引号。"""
    return "'" + text.replace("\\", "\\\\").replace("'", "\\'") + "'"


def b(s):
    return len((s or "").encode("utf-8"))


def discover_cache_dirs():
    """返回存在的插件缓存根目录：~/.workbuddy 与 ~/.workbuddy-ai 都支持。

    F-11：旧版把 CACHE 写死成 ~/.workbuddy/plugins/cache/workbuddy-builtin，
    国际版（数据目录 .workbuddy-ai）永远找不到。返回的是“缓存根”
    （.../plugins/cache/workbuddy-builtin），与 --root 的语义一致。
    """
    out = []
    for cfg_name in (".workbuddy", ".workbuddy-ai"):
        root = os.path.join(HOME, cfg_name, "plugins", "cache", "workbuddy-builtin")
        if os.path.isdir(root) and root not in out:
            out.append(root)
    # 其它 .workbuddy* 变体（便携版/自定义目录名）也顺带看一眼，但不递归。
    try:
        for name in sorted(os.listdir(HOME)):
            if not name.startswith(".workbuddy"):
                continue
            root = os.path.join(HOME, name, "plugins", "cache", "workbuddy-builtin")
            if os.path.isdir(root) and root not in out:
                out.append(root)
    except OSError:
        pass
    return out


def find_files(root):
    return sorted(glob.glob(os.path.join(root, "mcp-*", "*", "dist", "*.cjs")))


def node_check(path):
    """返回 (ok, message)。没有 Node 时返回 (None, 'node unavailable')。"""
    try:
        r = subprocess.run(["node", "--check", path], capture_output=True, text=True)
    except FileNotFoundError:
        return None, "node unavailable"
    except OSError as e:
        return None, "node unavailable (%s)" % e
    if r.returncode == 0:
        return True, "node --check OK"
    err = (r.stderr or "").strip().splitlines()
    return False, (err[-1] if err else "node --check failed")


def verify_readback(path, expected):
    """Node 不可用时的降级校验：读回逐字节比对刚写入的文本。"""
    try:
        with open(path, encoding="utf-8", errors="surrogateescape", newline="") as fh:
            got = fh.read()
    except OSError as e:
        return False, "read-back failed (%s)" % e
    if got != expected:
        return False, "read-back mismatch"
    return True, "read-back OK"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--root", default=None,
                    help="plugin cache dir (default: every ~/.workbuddy*/plugins/cache/workbuddy-builtin that exists)")
    args = ap.parse_args()

    if args.root:
        roots = [args.root]
    else:
        roots = discover_cache_dirs()
    if not roots:
        print("没有找到插件缓存目录（~/.workbuddy 或 ~/.workbuddy-ai 下的 plugins/cache/workbuddy-builtin）")
        return 2

    stamp = time.strftime("%Y%m%d-%H%M%S")
    bdir = os.path.join(PROJ, "backups", stamp + "-mcp-desc")

    if args.restore:
        base = os.path.join(PROJ, "backups")
        if not os.path.isdir(base):
            print("no backups dir; nothing to restore")
            return 2
        cands = sorted(d for d in os.listdir(base) if d.endswith("-mcp-desc"))
        if not cands:
            print("no *-mcp-desc backup")
            return 2
        src = os.path.join(base, cands[-1])
        n = 0
        for f in os.listdir(src):
            # 备份文件名 = [<配置目录名>__]<相对 --root 的路径，__ 代替分隔符>。
            # 新备份带配置目录前缀（.workbuddy / .workbuddy-ai …），旧备份没有；
            # 两种都要能还原。
            parts = f.split("__")
            tag = None
            if parts and parts[0].startswith("."):
                tag = parts[0]
                rel = os.sep.join(parts[1:])
            else:
                rel = os.sep.join(parts)
            for root in roots:
                if tag and os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(root)))) != tag:
                    continue
                dst = os.path.join(root, rel)
                if os.path.isfile(dst):
                    shutil.copy2(os.path.join(src, f), dst)
                    n += 1
                    print("restored %s" % dst)
        print("restored %d file(s)" % n)
        return 0 if n else 2

    files = []
    for root in roots:
        for path in find_files(root):
            files.append((root, path))
    if not files:
        print("没有找到 mcp-*/dist/*.cjs（roots: %s）" % ", ".join(roots))
        return 2

    total_before = total_after = 0
    written = 0
    failed = 0
    for root, path in files:
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
        rel = os.path.relpath(path, root)
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
        # 备份名带上 root 的标识，避免 .workbuddy / .workbuddy-ai 同名文件互相覆盖。
        root_tag = os.path.basename(os.path.dirname(os.path.dirname(os.path.dirname(root))))
        bpath = os.path.join(bdir, root_tag + "__" + rel.replace(os.sep, "__"))
        shutil.copy2(path, bpath)
        try:
            with open(path, "w", encoding="utf-8", errors="surrogateescape", newline="") as f:
                f.write(out)
        except OSError as e:
            print("    !! 写入失败，未改动：%s" % e)
            failed += 1
            continue
        written += 1

        # 语法校验：失败就还原。没有 Node 时降级为读回比对（F-11：旧版直接
        # subprocess.run(["node", ...])，无 Node 环境抛 FileNotFoundError）。
        ok, msg = node_check(path)
        if ok is None:
            ok, msg = verify_readback(path, out)
            msg = "node 不可用，跳过语法校验；" + msg
        if not ok:
            shutil.copy2(bpath, path)
            print("    !! 校验失败，已还原：%s" % msg)
            failed += 1
            continue
        print("    %s" % msg)

    print("\n合计 %d -> %d 字节（%+d）" % (total_before, total_after, total_after - total_before))
    if args.dry_run:
        print("[dry-run] nothing written")
    else:
        if written:
            print("备份 -> %s" % bdir)
        print("重启 WorkBuddy（重新拉起 MCP server）后生效；升级会覆盖，重跑本脚本。")
    if failed:
        print("%d 个文件失败（见上）" % failed)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
