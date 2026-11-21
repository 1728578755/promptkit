#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""WorkBuddy prompt doctor —— 逐项体检，**没生效的项也必须出现在报告里**。

为什么要有它：之前的验收只打印“找到并打好的东西”，看不到“本应存在却没有”的项，
于是用户只能看到“还是有问题”，无法判断是哪一层没生效。

每一层、每一项都列出来，并给出状态：

    PASS     已生效（读到真实状态）
    FAIL     应该生效但没有
    WARN     生效了但有附加条件（例如进程还没重启）
    SKIP     本机不存在这一层（例如应用没在运行）
    MISSING  目标根本找不到（版本变了 / 路径不同 / 没部署过）

最后是结论：EFFECTIVE / NOT EFFECTIVE:<原因> / UNPROVEN:<原因>。

用法：
    python -X utf8 scripts/doctor.py
    python -X utf8 scripts/doctor.py --json
    python -X utf8 scripts/doctor.py --install "D:\\Program Files\\WorkBuddy"
    python -X utf8 scripts/doctor.py --write-report
"""
import argparse
import datetime as _dt
import glob
import json
import os
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
PROJ = os.path.dirname(HERE)
PY = sys.executable

REMOVED_TAGS = [
    "tool_usage_policy", "result_presentation", "final_answer_instructions",
    "task_management", "agent_skills", "quality_policy", "agent_loop",
    "regional_conventions",
]
TRACE_MARKERS = [
    "<current_time", 'data-role="additional-data"', 'data-role="user-context"',
    "<additional_data", "<user_info", "<connector-status",
]
CLI_ANCHORS = [
    ("helper", "__wbStripLegacyString", 1),
    ("instructions", "systemInstructions:__wbStripLegacyAny(", 2),
    ("input", "input:__wbCleanRuntimeInput(", 2),
]

PASS, FAIL, WARN, SKIP, MISSING = "PASS", "FAIL", "WARN", "SKIP", "MISSING"


def run(args, timeout=180):
    p = subprocess.run(args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def ts(mtime):
    return _dt.datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M:%S")


# ---------------------------------------------------------------- installs
def discover():
    roots = []
    env = os.environ.get("WORKBUDDY_INSTALL", "").strip().strip('"')
    if env:
        roots.append(env)
    try:
        sys.path.insert(0, HERE)
        import _install  # noqa: WPS433
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


def config_dirs():
    home = os.path.expanduser("~")
    out = []
    for name in (".workbuddy", ".workbuddy-ai"):
        p = os.path.join(home, name)
        if os.path.isdir(p):
            out.append(p)
    return out


# ------------------------------------------------------------------ layers
def layer_templates(cfg_dirs):
    rows = []
    for cfg in cfg_dirs:
        pattern = os.path.join(cfg, "plugins", "cache", "workbuddy-builtin",
                               "*", "*", "prompt.tpl")
        versions = {}
        for path in sorted(glob.glob(pattern)):
            ver = os.path.basename(os.path.dirname(path))
            try:
                text = open(path, encoding="utf-8", errors="replace").read()
            except OSError:
                continue
            item = versions.setdefault(ver, {"files": 0, "bytes": 0, "hits": 0, "detail": {}})
            item["files"] += 1
            item["bytes"] += len(text)
            for tag in REMOVED_TAGS:
                n = text.count("<" + tag)
                if n:
                    item["hits"] += n
                    item["detail"][tag] = item["detail"].get(tag, 0) + n
        for ver, item in sorted(versions.items()):
            rows.append({
                "where": "%s  %s" % (os.path.basename(cfg), ver),
                "state": PASS if item["hits"] == 0 else FAIL,
                "detail": "files=%d bytes=%d removed_hits=%d %s"
                          % (item["files"], item["bytes"], item["hits"],
                             json.dumps(item["detail"], ensure_ascii=False) if item["detail"] else ""),
            })
        if not versions:
            rows.append({"where": "%s  prompt.tpl" % os.path.basename(cfg),
                         "state": MISSING, "detail": "没有缓存模板（还没部署过）"})
    return rows


def find_chunks(root):
    """按内容找运行时 chunk，不看文件名。"""
    unpacked = os.path.join(root, "resources", "app.asar.unpacked")
    hits = []
    if not os.path.isdir(unpacked):
        return unpacked, hits
    base_depth = os.path.abspath(unpacked).count(os.sep)
    for dirpath, dirs, files in os.walk(unpacked):
        if dirpath.count(os.sep) - base_depth >= 6:
            dirs[:] = []
        dirs[:] = [d for d in dirs if d.lower() not in
                   {"node_modules", ".git", "renderer", "locales", "assets"}]
        for name in files:
            if not name.endswith(".mjs"):
                continue
            path = os.path.join(dirpath, name)
            try:
                if os.path.getsize(path) > 24 * 1024 * 1024:
                    continue
                data = open(path, "rb").read()
            except OSError:
                continue
            if b"modelInput.instructions" in data and b"modelInput.input" in data:
                hits.append(path)
    return unpacked, sorted(hits)


def layer_cli(root):
    """返回 (rows, chunk_paths)。"""
    unpacked, chunks = find_chunks(root)
    rows = []
    if not os.path.isdir(unpacked):
        rows.append({"where": "app.asar.unpacked", "state": MISSING,
                     "detail": "目录不存在：chunk 可能被打进 asar，本工具无法在包内改写"})
        return rows, chunks
    if not chunks:
        rows.append({"where": unpacked, "state": MISSING,
                     "detail": "没有文件同时含 modelInput.instructions / modelInput.input"})
        return rows, chunks
    for path in chunks:
        data = open(path, "rb").read()
        version = 3 if b"/*__wbTrimV3*/" in data else (1 if b"__wbStripLegacyString" in data else 0)
        rows.append({"where": os.path.basename(path), "state": PASS,
                     "detail": "bytes=%d version=%d mtime=%s"
                               % (len(data), version, ts(os.path.getmtime(path)))})
        for label, anchor, want in CLI_ANCHORS:
            n = data.count(anchor.encode())
            rows.append({"where": "    anchor %-12s" % label,
                         "state": PASS if n >= want else FAIL,
                         "detail": "count=%d (want %d)" % (n, want)})
    return rows, chunks


def layer_asar(root):
    asar = os.path.join(root, "resources", "app.asar")
    code, out = run([PY, "-X", "utf8", os.path.join(HERE, "patch-asar.py"),
                     "--status", "--asar", asar])
    rows, patches, integ, exe = [], {}, "", ""
    for line in out.splitlines():
        if line.startswith("patch:"):
            rest = line.split(":", 1)[1].strip()
            parts = rest.rsplit(None, 1)
            name = parts[0]
            state = parts[1] if len(parts) == 2 else "?"
            patches[name] = state
            rows.append({"where": "    %-20s" % name,
                         "state": PASS if state == "patched" else FAIL,
                         "detail": "patched" if state == "patched" else "raw"})
        elif line.startswith("integ:"):
            integ = line.split(":", 1)[1].strip()
        elif line.startswith("exe sync:"):
            exe = line.split(":", 1)[1].strip()
    if not patches:
        rows.append({"where": "app.asar", "state": MISSING,
                     "detail": (out.strip().splitlines() or ["读不到补丁状态"])[-1][:200]})
        return rows
    rows.append({"where": "    integrity", "state": PASS if integ == "MATCH" else FAIL,
                 "detail": integ or "?"})
    rows.append({"where": "    exe manifest", "state": PASS if exe == "MATCH" else FAIL,
                 "detail": exe or "?"})
    return rows


def processes():
    if os.name != "nt":
        return []
    script = ("Get-CimInstance Win32_Process | Where-Object { $_.Name -match 'WorkBuddy|codebuddy' } | "
              "ForEach-Object { \"{0}|{1}|{2}|{3}\" -f $_.ProcessId,$_.Name,"
              "$($_.CreationDate.ToString('yyyy-MM-dd HH:mm:ss')),$_.CommandLine }")
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           capture_output=True, text=True, encoding="mbcs",
                           errors="replace", timeout=60)
        out = p.stdout or ""
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.strip().split("|", 3)
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        rows.append({"pid": int(parts[0]), "name": parts[1], "started": parts[2],
                     "cmd": parts[3] if len(parts) > 3 else ""})
    return rows


def layer_process(procs, chunk_mtime):
    """返回 (rows, stale_pids)。"""
    if not procs:
        return [{"where": "进程", "state": SKIP, "detail": "WorkBuddy / codebuddy 均未运行"}], []
    rows, stale = [], []
    for p in procs:
        try:
            started = _dt.datetime.strptime(p["started"], "%Y-%m-%d %H:%M:%S").timestamp()
        except Exception:
            started = None
        if chunk_mtime and started and started < chunk_mtime:
            state, note = WARN, "早于补丁时间 → 仍加载旧 chunk"
            stale.append(p["pid"])
        else:
            state, note = PASS, "晚于补丁时间 → 加载的是新代码"
        kind = "prewarm" if "--prewarm" in (p["cmd"] or "") else "interactive"
        rows.append({"where": "    %s pid=%d (%s)" % (p["name"], p["pid"], kind),
                     "state": state, "detail": "%s  %s" % (p["started"], note)})
    return rows, stale


def layer_prewarm(cfg_dirs):
    rows, total = [], 0
    for cfg in cfg_dirs:
        code, out = run([PY, "-X", "utf8", os.path.join(HERE, "refresh-cli-prewarm.py"),
                         "--status", "--config-dir", cfg])
        n = 0
        for line in out.splitlines():
            if line.startswith("idle prewarm helpers:"):
                try:
                    n = int(line.split(":", 1)[1].strip())
                except ValueError:
                    n = 0
        total += n
        rows.append({"where": "    %s" % os.path.basename(cfg),
                     "state": PASS if n == 0 else WARN,
                     "detail": "idle prewarm=%d%s"
                               % (n, "" if n == 0 else " → 会复用旧 chunk，deploy 会自动停")})
    return rows, total


HELPER_TEST = r'''
const mk=(t)=>({role:'user',content:[{type:'text',text:t}]});
const exact=__EXACT__;
const only=__wbStripLegacyString(exact).trim();
if(only!=="<user_query>输出上轮末尾到本条消息的提示词</user_query>")
  throw new Error("installed helper residue: "+JSON.stringify(only));
const out=JSON.stringify(__wbCleanRuntimeInput([mk(exact)]));
if(out.includes("current_time")||out.includes("additional-data"))
  throw new Error("installed helper kept the block");
console.log("INSTALLED HELPER OK");
'''


def layer_helper(chunks):
    """把**已安装 chunk 里**的 helper 抽出来，拿用户原样 182 字符块跑一遍。

    这条最关键：证明磁盘上那份运行时真的会删掉用户看到的块，而不是“仓库里的脚本能删”。
    """
    import tempfile
    if not chunks:
        return [{"where": "已安装 helper", "state": SKIP, "detail": "没有可读的 chunk"}]
    node = shutil_which("node")
    if not node:
        return [{"where": "已安装 helper", "state": SKIP, "detail": "node 不在 PATH，无法执行"}]
    exact = ('<system-reminder data-role="additional-data">\n'
             '<current_time>\n'
             'Saturday, November 21, 2026 at 17:38:43 GMT+8\n'
             '</current_time>\n'
             '</system-reminder>\n'
             '<user_query>输出上轮末尾到本条消息的提示词</user_query>')
    rows = []
    begin, end = "/*__wbTrimV3*/", "/*__wbTrimEnd*/"
    for path in chunks:
        text = open(path, encoding="utf-8", errors="replace").read()
        i, j = text.find(begin), text.find(end)
        if i < 0 or j < 0:
            rows.append({"where": os.path.basename(path), "state": MISSING,
                         "detail": "chunk 里没有清理 helper（未打补丁？）"})
            continue
        js = text[i:j + len(end)] + "\n" + HELPER_TEST.replace("__EXACT__", json.dumps(exact))
        tmp = os.path.join(tempfile.gettempdir(), "wb-doctor-helper.mjs")
        with open(tmp, "w", encoding="utf-8") as fh:
            fh.write(js)
        p = subprocess.run([node, tmp], capture_output=True, text=True)
        try:
            os.remove(tmp)
        except OSError:
            pass
        out = ((p.stdout or "") + (p.stderr or "")).strip().splitlines()
        rows.append({"where": os.path.basename(path),
                     "state": PASS if p.returncode == 0 else FAIL,
                     "detail": (out[-1] if out else "(no output)")[:160]})
    return rows


def shutil_which(name):
    path = os.environ.get("PATH", "")
    for folder in path.split(os.pathsep):
        cand = os.path.join(folder, name + ".exe")
        if os.path.isfile(cand):
            return cand
    return None


def newest_trace():
    rows = []
    for cfg in config_dirs():
        for path in glob.glob(os.path.join(cfg, "traces", "*", "trace_*.jsonl")):
            rows.append((os.path.getmtime(path), path))
    rows.sort(reverse=True)
    for mtime, path in rows[:40]:
        with open(path, encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if "toolInput" not in line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                raw = (obj.get("span") or {}).get("toolInput")
                if not isinstance(raw, str) or "<refusal_policy>" not in raw:
                    continue
                try:
                    msgs = json.loads(raw)
                    count = len(msgs) if isinstance(msgs, list) else None
                    trunc = False
                except Exception:
                    count, trunc = None, True
                return {"path": path, "mtime": mtime, "raw": raw,
                        "messages": count, "truncated": trunc,
                        "marks": {m: raw.count(m) for m in TRACE_MARKERS},
                        "blocks": {t: raw.count("<" + t) for t in REMOVED_TAGS}}
    return None


def layer_trace(trace, chunk_mtime):
    """返回 (rows, verdict, dirty)。verdict: clean/older/dirty/no-trace。"""
    if not trace:
        return [{"where": "最新请求", "state": MISSING,
                 "detail": "没有 trace（还没发过请求）"}], "no-trace", []
    older = bool(chunk_mtime) and trace["mtime"] < chunk_mtime
    rows, dirty = [], []
    rows.append({"where": "    最新请求",
                 "state": WARN if older else PASS,
                 "detail": "%s  messages=%s%s%s"
                           % (ts(trace["mtime"]), trace["messages"],
                              "  [TRUNCATED]" if trace["truncated"] else "",
                              "  ← 早于补丁，不能证明生效" if older else "")})
    for k, n in trace["marks"].items():
        if n:
            dirty.append("%s=%d" % (k, n))
        rows.append({"where": "    %-26s" % k, "state": PASS if n == 0 else FAIL,
                     "detail": "count=%d" % n})
    for k, n in trace["blocks"].items():
        if n:
            dirty.append("%s=%d" % (k, n))
        rows.append({"where": "    %-26s" % ("<" + k), "state": PASS if n == 0 else FAIL,
                     "detail": "count=%d" % n})
    if dirty:
        return rows, "dirty", dirty
    return rows, ("older" if older else "clean"), []


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--install", action="append", default=[])
    ap.add_argument("--json", action="store_true")
    ap.add_argument("--write-report", action="store_true")
    args = ap.parse_args()

    roots = args.install or discover()
    cfgs = config_dirs()
    report = {"time": _dt.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
              "installs": [], "verdict": "", "reason": ""}

    lines = []
    def emit(text=""):
        lines.append(text)

    emit("== WorkBuddy prompt doctor ==")
    emit("time      : %s" % report["time"])
    emit("config    : %s" % (", ".join(cfgs) if cfgs else "NOT FOUND"))
    emit("install   : %s" % (", ".join(roots) if roots else "NOT FOUND"))

    problems, unproven = [], []

    emit("")
    emit("[1] 提示词模板（缓存 prompt.tpl，八个块必须 0）")
    t_rows = layer_templates(cfgs)
    for r in t_rows:
        emit("    %-46s %-6s %s" % (r["where"], r["state"], r["detail"]))
        if r["state"] in (FAIL, MISSING):
            problems.append("templates %s: %s" % (r["where"].strip(), r["detail"]))

    chunk_paths = []
    for root in roots:
        emit("")
        emit("[2] CLI 运行时  %s" % root)
        c_rows, chunks = layer_cli(root)
        chunk_paths.extend(chunks)
        for r in c_rows:
            emit("    %-46s %-6s %s" % (r["where"], r["state"], r["detail"]))
            if r["state"] in (FAIL, MISSING):
                problems.append("cli %s: %s" % (r["where"].strip(), r["detail"]))

        emit("")
        emit("[3] app.asar  %s" % root)
        a_rows = layer_asar(root)
        for r in a_rows:
            emit("    %-46s %-6s %s" % (r["where"], r["state"], r["detail"]))
            if r["state"] == FAIL:
                problems.append("asar %s: %s" % (r["where"].strip(), r["detail"]))
        report["installs"].append({"root": root, "cli": c_rows, "asar": a_rows})

    chunk_mtime = max([os.path.getmtime(p) for p in chunk_paths] or [0.0])

    procs = processes()
    emit("")
    emit("[4] 进程（补丁是否真的被加载）")
    p_rows, stale = layer_process(procs, chunk_mtime)
    for r in p_rows:
        emit("    %-46s %-6s %s" % (r["where"], r["state"], r["detail"]))
    if stale:
        problems.append("stale-process pid=%s 早于补丁启动" % ",".join(str(x) for x in stale))

    emit("")
    emit("[5] prewarm 池")
    w_rows, _ = layer_prewarm(cfgs)
    for r in w_rows:
        emit("    %-46s %-6s %s" % (r["where"], r["state"], r["detail"]))

    emit("")
    emit("[6] 最新真实请求（trace）")
    trace = newest_trace()
    tr_rows, tv, dirty = layer_trace(trace, chunk_mtime)
    for r in tr_rows:
        emit("    %-46s %-6s %s" % (r["where"], r["state"], r["detail"]))
    if tv == "dirty":
        problems.append("trace 仍带 " + ", ".join(dirty))
    elif tv == "older":
        unproven.append("最新请求早于补丁时间")
    elif tv == "no-trace":
        unproven.append("本机没有任何 trace")

    emit("")
    emit("[7] 已安装运行时自检（拿你看到的原样块跑一遍）")
    h_rows = layer_helper(chunk_paths)
    for r in h_rows:
        emit("    %-46s %-6s %s" % (r["where"], r["state"], r["detail"]))
        if r["state"] in (FAIL, MISSING):
            problems.append("helper %s: %s" % (r["where"].strip(), r["detail"]))

    emit("")
    emit("[8] 判读规则（先看这里，再决定下一步）")
    emit("    [1] removed_hits>0    → 模板层没部署到那个版本，重跑 deploy.cmd")
    emit("    [2] MISMATCH / FAIL   → chunk 没打上（文件名变了 / 没管理员 / 被安全软件拦）")
    emit("    [3] 某条 raw          → asar 没打上（应用在运行），关掉应用后重跑 deploy.cmd")
    emit("    [4] WARN 早于补丁     → 那个进程还在跑旧代码；开新聊天或重启应用")
    emit("    [5] idle prewarm>0    → 新聊天会复用旧 chunk；deploy 会自动停，也可手动 --apply")
    emit("    [6] 计数>0            → 这些块确实还在发给模型，按上面行号往上查")
    emit("    [7] FAIL              → 磁盘上那份运行时删不掉该块，补丁没生效")
    emit("    若 [6][7] 全 PASS 而模型回答里仍有该块：")
    emit("      · 先排除旧聊天（旧会话历史里本来就存着它）——新开一个聊天再看；")
    emit("      · 仍出现则说明块是云端在服务端拼 prompt 时加的，本地删不掉；")
    emit("        本地能做的是不再上报时间（current-time / current-time-cloud 两个 asar 补丁）。")

    emit("")
    if problems:
        verdict, reason = "NOT EFFECTIVE", "; ".join(problems)
    elif unproven:
        verdict, reason = "UNPROVEN", "; ".join(unproven)
    else:
        verdict, reason = "EFFECTIVE", "所有层已生效，最新请求也已干净"
    emit("结论      : %s" % verdict)
    emit("原因      : %s" % reason)
    report["verdict"], report["reason"] = verdict, reason

    text = "\n".join(lines)
    print(text)
    if args.write_report:
        out_dir = os.path.join(PROJ, "reports")
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, "doctor-latest.txt")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(text + "\n")
        print("report    : %s" % path)
    if args.json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
    # UNPROVEN 不是"打补丁失败"，只是"还没有补丁后的新请求可以证明"；
    # 只有 NOT EFFECTIVE 才是真的没生效。
    return 1 if verdict == "NOT EFFECTIVE" else 0


if __name__ == "__main__":
    sys.exit(main())
