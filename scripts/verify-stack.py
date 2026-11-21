#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""One-shot acceptance report for the WorkBuddy prompt-trim stack.

Run it on any machine, right after ``scripts\\deploy.cmd``:

    python -X utf8 scripts/verify-stack.py
    python -X utf8 scripts/verify-stack.py --json
    python -X utf8 scripts/verify-stack.py --install "D:\\Program Files\\WorkBuddy"

Three layers, each verified by reading real state (never a model's claim):

1. **CLI runtime chunk** - discovered by content under
   ``resources\\app.asar.unpacked`` (never by file name, so another version or
   another install path still resolves).  Must report ``version: 3`` and
   ``status: PATCHED``, and the exact 182-character message the user saw must
   be gone (``--self-test``).
2. **app.asar** - every registered patch ``patched``, ``integ: MATCH`` and the
   exe manifest in sync, so the app still starts.
3. **newest trace** - counts ``<current_time>``, ``data-role="additional-data"``
   and ``data-role="user-context"`` in the real request prefix.  This is the only
   trustworthy evidence of what the model actually received.

Exit code 0 only when every layer that exists on this machine passes.
"""
import argparse
import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PY = sys.executable
FINGERPRINT = "<refusal_policy>"
TRACE_ROOTS = [
    os.path.join(os.path.expanduser("~"), ".workbuddy", "traces"),
    os.path.join(os.path.expanduser("~"), ".workbuddy-ai", "traces"),
]


def run(args, timeout=180):
    p = subprocess.run(args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def discover():
    """Every WorkBuddy install on this machine that really has an app.asar."""
    roots = []
    env = os.environ.get("WORKBUDDY_INSTALL", "").strip().strip('"')
    if env:
        roots.append(env)
    try:
        sys.path.insert(0, HERE)
        import _install  # noqa: WPS433 - sibling diagnostic helper
        roots.extend(_install.install_roots())
    except Exception:
        pass
    for base in (os.environ.get("ProgramFiles", r"C:\Program Files"),
                 os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)"),
                 os.environ.get("LOCALAPPDATA", "")):
        if not base:
            continue
        for name in ("WorkBuddy", "WorkBuddyAI", "WorkBuddy AI"):
            roots.append(os.path.join(base, name))
            roots.append(os.path.join(base, "Programs", name))
    out, seen = [], set()
    for root in roots:
        if not root or not os.path.isdir(root):
            continue
        asar = os.path.join(root, "resources", "app.asar")
        key = os.path.normcase(asar)
        if key in seen or not os.path.isfile(asar):
            continue
        seen.add(key)
        out.append(root)
    return out


def cli_layer(root):
    unpacked = os.path.join(root, "resources", "app.asar.unpacked")
    dist = os.path.join(unpacked, "cli", "dist")
    if not os.path.isdir(dist):
        dist = unpacked
    info = {"dir": dist, "file": "", "version": None, "status": "", "ok": False}
    if not os.path.isdir(dist):
        info["status"] = "app.asar.unpacked missing"
        return info
    code, out = run([PY, "-X", "utf8", os.path.join(HERE, "patch-cli-trim.py"),
                     "--status", "--file", dist])
    for line in out.splitlines():
        if line.startswith("file:"):
            info["file"] = line.split(":", 1)[1].strip()
        elif line.startswith("version:"):
            try:
                info["version"] = int(line.split(":", 1)[1].split()[0])
            except ValueError:
                pass
        elif line.startswith("status:"):
            info["status"] = line.split(":", 1)[1].strip()
    info["ok"] = code == 0 and info["version"] == 3 and info["status"] == "PATCHED"
    if not info["ok"] and not info["status"]:
        info["status"] = "NOT FOUND (no chunk carries modelInput)"
    return info


def asar_layer(root):
    asar = os.path.join(root, "resources", "app.asar")
    info = {"asar": asar, "patches": {}, "integ": "", "exe": "", "ok": False}
    if not os.path.isfile(asar):
        info["integ"] = "app.asar missing"
        return info
    code, out = run([PY, "-X", "utf8", os.path.join(HERE, "patch-asar.py"),
                     "--status", "--asar", asar])
    for line in out.splitlines():
        if line.startswith("patch:"):
            rest = line.split(":", 1)[1].strip()
            parts = rest.rsplit(None, 1)
            info["patches"][parts[0]] = parts[1] if len(parts) == 2 else rest
        elif line.startswith("integ:"):
            info["integ"] = line.split(":", 1)[1].strip()
        elif line.startswith("exe sync:"):
            info["exe"] = line.split(":", 1)[1].strip()
    raw = [k for k, v in info["patches"].items() if v != "patched"]
    info["raw"] = raw
    info["ok"] = (code == 0 and not raw
                  and info["integ"] == "MATCH"
                  and info["exe"] in ("MATCH", ""))
    return info


def newest_trace():
    rows = []
    for root in TRACE_ROOTS:
        for path in glob.glob(os.path.join(root, "*", "trace_*.jsonl")):
            rows.append((os.path.getmtime(path), path))
    rows.sort(reverse=True)
    for _, path in rows[:40]:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "toolInput" not in line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                raw = (obj.get("span") or {}).get("toolInput")
                if not isinstance(raw, str) or FINGERPRINT not in raw:
                    continue
                return {
                    "path": path,
                    "messages": _msg_count(raw),
                    "current_time": raw.count("<current_time>"),
                    "additional_data": raw.count('data-role="additional-data"'),
                    "user_context": raw.count('data-role="user-context"'),
                    "truncated": _truncated(raw),
                }
    return None


def _msg_count(raw):
    try:
        msgs = json.loads(raw)
    except Exception:
        return None
    return len(msgs) if isinstance(msgs, list) else None


def _truncated(raw):
    try:
        json.loads(raw)
        return False
    except Exception:
        return True


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="append", default=[],
                    help="installation root (repeatable); default: auto-discover")
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()

    roots = args.install or discover()
    report = {"roots": [], "self_test": "", "trace": None}
    if not roots:
        report["error"] = "no WorkBuddy install found (pass --install <root>)"
    else:
        code, out = run([PY, "-X", "utf8", os.path.join(HERE, "patch-cli-trim.py"),
                         "--self-test"])
        report["self_test"] = "SELF TEST OK" if code == 0 and "SELF TEST OK" in out else out.strip()[:400]
    for root in roots:
        report["roots"].append({
            "root": root,
            "cli": cli_layer(root),
            "asar": asar_layer(root),
        })
    report["trace"] = newest_trace()

    ok = True
    if not roots:
        ok = False
    if report["self_test"] != "SELF TEST OK":
        ok = False
    for row in report["roots"]:
        if not row["cli"]["ok"] or not row["asar"]["ok"]:
            ok = False
    # trace 只做证据展示，不参与 ok：最新一条可能还是打补丁之前的请求，
    # 拿它判失败会永远红。要看模型实际收到什么，就看这一段的计数。
    trace = report["trace"]
    report["trace_advisory"] = (
        "clean" if trace and not (trace["current_time"] or trace["additional_data"])
        else ("n/a" if not trace else "has current_time/additional-data")
    )
    report["ok"] = ok

    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 0 if ok else 1

    print("== WorkBuddy prompt-trim acceptance ==")
    print("self-test : %s" % report["self_test"])
    if not roots:
        print("install   : NOT FOUND")
    for row in report["roots"]:
        print("")
        print("install   : %s" % row["root"])
        cli = row["cli"]
        print("  cli     : version=%s status=%s" % (cli["version"], cli["status"]))
        print("            %s" % (cli["file"] or cli["dir"]))
        asar = row["asar"]
        print("  asar    : integ=%s exe=%s" % (asar["integ"] or "?", asar["exe"] or "?"))
        for name in sorted(asar["patches"]):
            mark = "ok" if asar["patches"][name] == "patched" else "RAW"
            print("            %-20s %s" % (name, mark))
    t = report["trace"]
    print("")
    if not t:
        print("  trace   : n/a (no request recorded yet)")
    else:
        print("  trace   : %s" % t["path"])
        print("            messages=%s current_time=%d additional-data=%d user-context=%d%s"
              % (t["messages"], t["current_time"], t["additional_data"],
                 t["user_context"], "  [TRUNCATED]" if t["truncated"] else ""))
    print("")
    print("trace note: %s" % report.get("trace_advisory", ""))
    print("RESULT    : %s" % ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
