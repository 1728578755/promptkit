#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Stop idle WorkBuddy `codebuddy --prewarm` helpers so the next chat loads a
freshly patched CLI chunk.

Why a separate script:
  A `--prewarm` command line does NOT mean the process is idle.  The pool marks
  the session file:

      ~/.workbuddy/sessions/<pid>.json
      {"kind":"prewarm","meta":{"status":"idle",...}}

  An acquired prewarm keeps the same command line but becomes
  {"kind":"interactive"}.  Killing by command line would abort a live chat, so
  this script only stops `kind=prewarm` + `meta.status=idle`, by PID.

It never uses taskkill /IM and never touches the WorkBuddy app itself.

Usage:
    python scripts/refresh-cli-prewarm.py --status
    python scripts/refresh-cli-prewarm.py --apply
"""
import argparse
import glob
import json
import os
import subprocess
import sys


def sessions_dir(config_dir):
    return os.path.join(config_dir, "sessions")


def is_alive(pid):
    try:
        p = subprocess.run(["tasklist", "/FI", "PID eq %d" % pid, "/NH"],
                           capture_output=True, text=True, encoding="mbcs", errors="replace")
        return str(pid) in (p.stdout or "")
    except Exception:
        return False


def candidates(config_dir):
    out = []
    for path in glob.glob(os.path.join(sessions_dir(config_dir), "*.json")):
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                meta = json.load(fh)
        except Exception:
            continue
        if not isinstance(meta, dict):
            continue
        if meta.get("kind") != "prewarm":
            continue
        status = ((meta.get("meta") or {}).get("status") or "").lower()
        if status != "idle":
            continue
        pid = meta.get("pid")
        if not isinstance(pid, int) or pid <= 0:
            continue
        if not is_alive(pid):
            continue
        out.append((pid, meta.get("sessionId") or meta.get("meta", {}).get("prewarmId") or path))
    out.sort()
    return out


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--status", action="store_true")
    g.add_argument("--apply", action="store_true")
    ap.add_argument("--config-dir", default=os.path.join(os.path.expanduser("~"), ".workbuddy"))
    args = ap.parse_args()

    rows = candidates(args.config_dir)
    print("config: %s" % args.config_dir)
    print("idle prewarm helpers: %d" % len(rows))
    for pid, label in rows:
        print("  pid=%d  %s" % (pid, label))
    if args.status or not rows:
        return 0 if args.status else 0

    stopped = 0
    for pid, label in rows:
        p = subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                           capture_output=True, text=True, encoding="mbcs", errors="replace")
        ok = p.returncode == 0
        print("  %s pid=%d %s" % ("stopped" if ok else "failed", pid, label))
        if ok:
            stopped += 1
    print("stopped: %d / %d" % (stopped, len(rows)))
    return 0 if stopped == len(rows) else 1


if __name__ == "__main__":
    sys.exit(main())
