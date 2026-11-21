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
import re
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
# 必须 0 次：这些每轮重发就是 bug
MUST_ZERO = [
    "<current_time", 'data-role="additional-data"',
]
# 首轮允许 1 次、之后必须 0 次：这些是"一次性上下文"
ALLOW_ONCE = [
    'data-role="user-context"', "<additional_data", "<user_info",
    "<connector-status",
]
# 不看"打了几处"：不同构建的分支数不同。看"还有没有没包上的调用点"。
RE_INS_RAW = re.compile(rb"systemInstructions:(?!__wbStripLegacyAny\()[A-Za-z$_][\w$]*\.modelInput\.instructions,")
RE_IN_RAW = re.compile(rb"input:(?!__wbCleanRuntimeInput\()[A-Za-z$_][\w$]*\.modelInput\.input,")

PASS, FAIL, WARN, SKIP, MISSING = "PASS", "FAIL", "WARN", "SKIP", "MISSING"


def run(args, timeout=180):
    p = subprocess.run(args, capture_output=True, text=True,
                       encoding="utf-8", errors="replace", timeout=timeout)
    return p.returncode, (p.stdout or "") + (p.stderr or "")


def ts(mtime):
    return _dt.datetime.fromtimestamp(mtime).strftime("%m-%d %H:%M:%S")


# ---------------------------------------------------------------- installs
def discover():
    """所有能找到的安装：环境变量 / 注册表 / 进程 / 快捷方式 / 磁盘扫描。

    不写死任何路径：新电脑上装到 D:、用户目录、改了目录名，都能找到。
    """
    try:
        sys.path.insert(0, HERE)
        import _install  # noqa: WPS433
        return _install.install_roots_deep()
    except Exception:
        return []


def searched():
    """找不到安装时，把"找过哪里"打印出来，避免只给一句 NOT FOUND。"""
    try:
        import _install
        return _install.scan_locations()
    except Exception:
        return []


def _is_client_data_dir(path):
    """像不像客户端数据目录：有 plugins / sessions / traces / settings.json 之一。

    只看名字 `.workbuddy*` 会把辅助目录（例如只放 connector-keys 的
    `.workbuddy-key-fallback`）也当成数据目录，然后误报"缓存冷启动缺失"。
    """
    if not os.path.isdir(path):
        return False
    for rel in (("plugins",), ("sessions",), ("traces",), ("settings.json",)):
        if os.path.exists(os.path.join(path, *rel)):
            return True
    return False


def config_dirs():
    """所有数据目录：.workbuddy、.workbuddy-ai 以及任何像数据目录的 .workbuddy* 变体。"""
    home = os.path.expanduser("~")
    out = []
    env = os.environ.get("WORKBUDDY_CONFIG_DIR")
    if env and _is_client_data_dir(env):
        out.append(env)
    for name in (".workbuddy", ".workbuddy-ai"):
        p = os.path.join(home, name)
        if _is_client_data_dir(p):
            out.append(p)
    try:
        for entry in sorted(os.listdir(home)):
            if not entry.startswith(".workbuddy"):
                continue
            p = os.path.join(home, entry)
            if p in out:
                continue
            if _is_client_data_dir(p):
                out.append(p)
    except OSError:
        pass
    return out


def install_from_data_dir(cfg):
    """<数据目录>\\.skill-list-cache.json 的 scopeKey 反查安装目录（便携/未注册安装也能配对）。"""
    cache_file = os.path.join(cfg, ".skill-list-cache.json")
    try:
        with open(cache_file, encoding="utf-8", errors="replace") as fh:
            data = json.load(fh)
    except Exception:
        return ""
    keys = data.get("scopeKey") if isinstance(data, dict) else None
    if isinstance(keys, str):
        # 实际格式是 JSON 数组的字符串（不是单个路径），先尝试解析，失败再当一条路径。
        try:
            parsed = json.loads(keys)
            keys = parsed if isinstance(parsed, list) else [keys]
        except Exception:
            keys = [keys]
    for s in keys or []:
        if not isinstance(s, str):
            continue
        m = re.match(r"^(.*?)\\resources\\app\.asar\.unpacked", s)
        if m:
            return m.group(1)
    return ""


# ------------------------------------------------------------------ layers
def pair_config(install_root, cfgs):
    """安装 -> 它使用的配置目录（CN 用 .workbuddy，国际版用 .workbuddy-ai）。"""
    endpoint = ""
    try:
        pj = os.path.join(install_root, "resources", "app.asar.unpacked", "cli", "product.json")
        with open(pj, encoding="utf-8", errors="replace") as fh:
            endpoint = (json.load(fh).get("endpoint") or "").lower()
    except Exception:
        pass
    want = ".workbuddy-ai" if "workbuddy.ai" in endpoint else ".workbuddy"
    for c in cfgs:
        if os.path.basename(c).lower() == want:
            return c
    # 名字对不上时按 scopeKey 反查：便携/改名安装的数据目录不叫 .workbuddy 也能配对
    target = os.path.normcase(os.path.abspath(install_root).rstrip("\\"))
    for c in cfgs:
        paired = install_from_data_dir(c)
        if paired and os.path.normcase(os.path.abspath(paired).rstrip("\\")) == target:
            return c
    return ""


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
                "cfg": cfg,
                "state": PASS if item["hits"] == 0 else FAIL,
                "detail": "files=%d bytes=%d removed_hits=%d %s"
                          % (item["files"], item["bytes"], item["hits"],
                             json.dumps(item["detail"], ensure_ascii=False) if item["detail"] else ""),
            })
        if not versions:
            rows.append({"where": "%s  prompt.tpl" % os.path.basename(cfg),
                         "cfg": cfg,
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


def cli_state(path):
    """一个 chunk 的补丁状态：raw=没包上的调用点数量。"""
    data = open(path, "rb").read()
    version = 3 if b"/*__wbTrimV3*/" in data else (1 if b"__wbStripLegacyString" in data else 0)
    return {
        "bytes": len(data),
        "version": version,
        "mtime": os.path.getmtime(path),
        "ins_raw": len(RE_INS_RAW.findall(data)),
        "in_raw": len(RE_IN_RAW.findall(data)),
        "ins_wrapped": data.count(b"__wbStripLegacyAny("),
        "in_wrapped": data.count(b"__wbCleanRuntimeInput("),
        "helper": b"__wbTrimV3" in data,
        "scrub": data.count(b"__wbScrubFetch") >= 2,
        "blank": b"__wbBlank" in data,
        "drop": b"__wbDropReminder" in data,
        # 轻量兜底：头部插入的 fetch 清理（不依赖任何锚点）
        "lite": b"__wbTrimLite" in data,
    }


def cli_problems(state):
    out = []
    if not state["helper"]:
        out.append("helper")
    if state["ins_raw"] or not state["ins_wrapped"]:
        out.append("instructions")
    if state["in_raw"] or not state["in_wrapped"]:
        out.append("input")
    if not state["scrub"]:
        out.append("fetch-scrub")
    if not state["blank"] or not state["drop"]:
        out.append("helper-outdated")
    return out


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
        st = cli_state(path)
        bad = cli_problems(st)
        # 只打了"轻量出口清理"时，模型边界仍然被清（WARN，不是 FAIL）：
        # 这种构建是锚点对不上才退到轻量修法，不该让人以为完全没生效。
        lite_only = bool(bad) and st["lite"]
        state = PASS if not bad else (WARN if lite_only else FAIL)
        rows.append({"where": os.path.basename(path),
                     "state": state,
                     "detail": "bytes=%d version=%d mtime=%s%s"
                               % (st["bytes"], st["version"], ts(st["mtime"]),
                                  "" if not bad else
                                  ("  ← 仅轻量出口清理（" + ",".join(bad) + "）" if lite_only
                                   else "  ← " + ",".join(bad))),
                     "lite_only": lite_only})
        sub_ok = lambda ok: PASS if ok else (WARN if lite_only else FAIL)
        rows.append({"where": "    instructions",
                     "state": sub_ok(st["ins_raw"] == 0 and st["ins_wrapped"]),
                     "detail": "wrapped=%d raw=%d (raw 必须 0)" % (st["ins_wrapped"], st["ins_raw"])})
        rows.append({"where": "    input",
                     "state": sub_ok(st["in_raw"] == 0 and st["in_wrapped"]),
                     "detail": "wrapped=%d raw=%d (raw 必须 0)" % (st["in_wrapped"], st["in_raw"])})
        rows.append({"where": "    fetch-scrub",
                     "state": sub_ok(st["scrub"]),
                     "detail": "present=%s" % st["scrub"]})
        rows.append({"where": "    helper",
                     "state": sub_ok(st["helper"] and st["blank"] and st["drop"]),
                     "detail": "v3=%s blank=%s drop=%s" % (st["helper"], st["blank"], st["drop"])})
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
              "ForEach-Object { \"{0}|{1}|{2}|{3}|{4}\" -f $_.ProcessId,$_.Name,"
              "$($_.CreationDate.ToString('yyyy-MM-dd HH:mm:ss')),$_.CommandLine,$_.ExecutablePath }")
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-Command", script],
                           capture_output=True, text=True, encoding="mbcs",
                           errors="replace", timeout=60)
        out = p.stdout or ""
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.strip().split("|", 4)
        if len(parts) < 3 or not parts[0].isdigit():
            continue
        rows.append({"pid": int(parts[0]), "name": parts[1], "started": parts[2],
                     "cmd": parts[3] if len(parts) > 3 else "",
                     "path": parts[4] if len(parts) > 4 else ""})
    return rows


def layer_process(procs, root_mtimes):
    """返回 (rows, stale_pids)。只比较"进程所在安装"的 chunk 修改时间。"""
    if not procs:
        return [{"where": "进程", "state": SKIP, "detail": "WorkBuddy / codebuddy 均未运行"}], []
    rows, stale = [], []
    for p in procs:
        path = (p.get("path") or "").lower()
        owner = ""
        for root in root_mtimes:
            if path.startswith(root.lower().rstrip("\\") + "\\"):
                owner = root
                break
        if not owner:
            continue
        mtime = root_mtimes.get(owner) or 0.0
        try:
            started = _dt.datetime.strptime(p["started"], "%Y-%m-%d %H:%M:%S").timestamp()
        except Exception:
            started = None
        if mtime and started and started < mtime:
            state, note = WARN, "早于补丁时间 → 仍加载旧 chunk"
            stale.append(p["pid"])
        else:
            state, note = PASS, "晚于补丁时间 → 加载的是新代码"
        kind = "prewarm" if "--prewarm" in (p["cmd"] or "") else "interactive"
        rows.append({"where": "    %s pid=%d (%s)" % (p["name"], p["pid"], kind),
                     "state": state, "detail": "%s  %s" % (p["started"], note)})
    if not rows:
        rows.append({"where": "进程", "state": SKIP,
                     "detail": "运行中的进程不属于本次检查的安装"})
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
            lite = "__wbTrimLite" in text
            rows.append({"where": os.path.basename(path), "path": path,
                         "state": WARN if lite else MISSING,
                         "detail": ("只有轻量出口清理（__wbTrimLite），没有完整 helper"
                                    if lite else "chunk 里没有清理 helper（未打补丁？）")})
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
        rows.append({"where": os.path.basename(path), "path": path,
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
                        "marks": {m: raw.count(m) for m in MUST_ZERO + ALLOW_ONCE},
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
    # messages<=2 视为首轮：一次性上下文允许出现 1 次
    first_turn = (trace["messages"] or 0) <= 2
    for k, n in trace["marks"].items():
        limit = 0 if k in MUST_ZERO else (1 if first_turn else 0)
        ok = n <= limit
        if not ok:
            dirty.append("%s=%d" % (k, n))
        rows.append({"where": "    %-26s" % k, "state": PASS if ok else FAIL,
                     "detail": "count=%d (limit %d%s)" % (n, limit, "，首轮" if limit else "")})
    for k, n in trace["blocks"].items():
        if n:
            dirty.append("%s=%d" % (k, n))
        rows.append({"where": "    %-26s" % ("<" + k), "state": PASS if n == 0 else FAIL,
                     "detail": "count=%d" % n})
    if dirty:
        return rows, "dirty", dirty
    return rows, ("older" if older else "clean"), []


def decide(roots, active, failing, global_problems, warnings, unproven):
    """把逐层结果折算成 (verdict, reason, hard, soft)。

    规则（B：未在使用的安装不得一票否决）：
      * active 只来自"正在运行的进程"——这是唯一能证明"现在用的是哪套"的证据。
        最新 trace 可能来自旧会话，不能作为"当前使用"的判据。
      * 有 active：只有 active 里的安装失败才算 hard；其余一律 soft（WARN）。
      * 没有 active：只有"全部安装都失败"才是 hard（无法判断在用哪套，保守判失败）；
        部分失败 -> soft（PARTIAL），不影响退出码。
    """
    failing = {r: p for r, p in failing.items() if p}
    if active:
        hard = {r: p for r, p in failing.items() if r in active}
        soft = {r: p for r, p in failing.items() if r not in active}
    elif failing and len(failing) >= len(roots):
        hard, soft = dict(failing), {}
    else:
        hard, soft = {}, dict(failing)

    if global_problems or hard:
        verdict = "NOT EFFECTIVE"
        reason = "; ".join(global_problems + [x for p in hard.values() for x in p])
        if soft:
            reason += ("；另有 %d 套未在使用的安装没打全（WARN，不计入失败）：%s"
                       % (len(soft), ", ".join(sorted(soft))))
    elif soft and not active:
        verdict = "PARTIAL"
        reason = ("有 %d 套安装没打全（本机没有运行中的进程，无法判断你在用哪套）：%s"
                  % (len(soft), ", ".join(sorted(soft))))
    elif soft:
        verdict = "EFFECTIVE"
        reason = ("当前使用的安装已生效；另有 %d 套未打全、不影响使用（WARN）：%s"
                  % (len(soft), ", ".join(sorted(soft))))
    elif unproven:
        verdict, reason = "UNPROVEN", "; ".join(unproven)
    else:
        verdict = "EFFECTIVE"
        reason = "所有层已生效，最新请求也已干净"
        if warnings:
            reason += "；另有提示：" + "; ".join(warnings)
    return verdict, reason, hard, soft


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
    if not roots:
        emit("            找过: WORKBUDDY_INSTALL / 注册表卸载项 / 运行中进程 / 开始菜单快捷方式 /")
        for loc in searched()[:12]:
            emit("                  %s" % loc)
        emit(r'            若安装在别处：scripts\doctor.cmd --install "<安装根目录>"')

    global_problems, per_install, unproven, warnings = [], {}, [], []
    # 与"哪一套安装"绑定的问题：先按配置目录/安装根归类，结论区再按"当前使用"分流。
    # 否则一套没在用的安装（旧版本、便携副本）会把全局结论打成 NOT EFFECTIVE。
    cfg_problems = {}
    template_problems = {}

    emit("")
    emit("[1] 提示词模板（缓存 prompt.tpl，八个块必须 0）")
    t_rows = layer_templates(cfgs)
    for r in t_rows:
        emit("    %-46s %-6s %s" % (r["where"], r["state"], r["detail"]))
        if r["state"] in (FAIL, MISSING):
            # 先按配置目录记账：等安装列表算出来后再归属到具体安装（见 [3.5] 前的汇总）
            template_problems.setdefault(r.get("cfg") or "", []).append(
                "templates %s: %s" % (r["where"].strip(), r["detail"]))

    chunk_paths = []
    chunk_owner = {}
    root_mtimes = {}
    cfg_owner = {}
    for root in roots:
        cfg = pair_config(root, cfgs)
        if cfg:
            cfg_owner[cfg] = root
        emit("")
        emit("[2] CLI 运行时  %s" % root)
        c_rows, chunks = layer_cli(root)
        chunk_paths.extend(chunks)
        for c in chunks:
            chunk_owner[c] = root
        if chunks:
            root_mtimes[root] = max(os.path.getmtime(c) for c in chunks)
        for r in c_rows:
            emit("    %-46s %-6s %s" % (r["where"], r["state"], r["detail"]))
            if r["state"] in (FAIL, MISSING):
                per_install.setdefault(root, []).append("cli %s: %s" % (r["where"].strip(), r["detail"]))
            elif r["state"] == WARN:
                warnings.append("cli %s: %s" % (r["where"].strip(), r["detail"]))

        emit("")
        emit("[3] app.asar  %s" % root)
        a_rows = layer_asar(root)
        for r in a_rows:
            emit("    %-46s %-6s %s" % (r["where"], r["state"], r["detail"]))
            if r["state"] == FAIL:
                per_install.setdefault(root, []).append("asar %s: %s" % (r["where"].strip(), r["detail"]))
        report["installs"].append({"root": root, "cli": c_rows, "asar": a_rows})

    chunk_mtime = max([os.path.getmtime(p) for p in chunk_paths] or [0.0])

    procs = processes()
    emit("")
    emit("[4] 进程（补丁是否真的被加载）")
    p_rows, stale = layer_process(procs, root_mtimes)
    for r in p_rows:
        emit("    %-46s %-6s %s" % (r["where"], r["state"], r["detail"]))
    if stale:
        global_problems.append("stale-process pid=%s 早于补丁启动" % ",".join(str(x) for x in stale))

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
        global_problems.append("trace 仍带 " + ", ".join(dirty))
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
            owner = chunk_owner.get(r.get("path") or "", "")
            if owner:
                cfg_problems.setdefault(owner, []).append("helper %s: %s" % (r["where"].strip(), r["detail"]))
            else:
                global_problems.append("helper %s: %s" % (r["where"].strip(), r["detail"]))
        elif r["state"] == WARN:
            warnings.append("helper %s: %s" % (r["where"].strip(), r["detail"]))

    emit("")
    emit("[8] 判读规则（先看这里，再决定下一步）")
    emit("    !! 验证必须新开空白会话（见结论区的「验证」一行）：旧会话历史里本来就存着旧块，")
    emit("       旧聊天会把它原样重发，看起来像“没生效”，实际是历史回放。")
    emit("    [1] removed_hits>0    → 模板层没部署到那个版本，重跑 deploy.cmd")
    emit("    [2] MISMATCH / FAIL   → chunk 没打上（文件名变了 / 没管理员 / 被安全软件拦）")
    emit("    [3] 某条 raw          → asar 没打上（应用在运行），关掉应用后重跑 deploy.cmd")
    emit("    [4] WARN 早于补丁     → 那个进程还在跑旧代码；开新聊天或重启应用")
    emit("    [5] idle prewarm>0    → 新聊天会复用旧 chunk；deploy 会自动停，也可手动 --apply")
    emit("    [6] 计数>0            → 这些块确实还在发给模型，按上面行号往上查")
    emit("    [7] FAIL              → 磁盘上那份运行时删不掉该块，补丁没生效；")
    emit(r"                            可跑 scripts\wb-fix-lite.cmd（免 Python/Node、不依赖锚点）")
    emit("    若 [6][7] 全 PASS 而模型回答里仍有该块：")
    emit("      · 先排除旧聊天（旧会话历史里本来就存着它）——新开一个聊天再看；")
    emit("      · 仍出现则说明块是云端在服务端拼 prompt 时加的，本地删不掉；")
    emit("        本地能做的是不再上报时间（current-time / current-time-cloud 两个 asar 补丁）。")

    # 哪一套是"当前在用"：唯一可信的证据是"有进程正在从它运行"。
    # 最新 trace 只能说明"最近写过什么"，旧会话/另一套安装的 trace 都会误导，故不用它判 active。
    active = set()
    for pr in procs:
        path = (pr.get("path") or "").lower()
        for r in roots:
            if path.startswith(r.lower().rstrip("\\") + "\\"):
                active.add(r)
    if not active and per_install and len(per_install) == 1 and len(roots) == 1:
        # 只有一套安装时不存在"拿哪套当当前"的歧义
        active.add(roots[0])

    # 模板层的问题按"配置目录 -> 安装"归属；认不出归属的才是全局问题。
    for cfg, items in template_problems.items():
        if not items:
            continue
        owner = cfg_owner.get(cfg, "")
        if owner:
            cfg_problems.setdefault(owner, []).extend(items)
        else:
            global_problems.extend(items)

    failing = {r: p for r, p in per_install.items() if p}
    # 把模板层的问题并进每套安装的失败清单
    for r, items in cfg_problems.items():
        failing.setdefault(r, []).extend(items)

    verdict, reason, hard, soft = decide(roots, active, failing, global_problems, warnings, unproven)
    report["verdict"], report["reason"] = verdict, reason

    emit("")
    emit("[3.5] 每套安装的结论")
    for r in roots:
        state = FAIL if r in hard else (WARN if r in failing else PASS)
        tag = "当前使用" if r in active else ("未在使用" if active else "")
        emit("    %-46s %-6s %s%s" % (r, state, tag,
                                      "" if state != WARN else "  （未在使用的安装没打全：不影响当前结论）"))

    # 结论放最上面：窗口第一屏就能看到，不用滚到底。
    # 验证口径也放在结论区：旧会话历史里本来就存着旧块，用它验证永远"看起来没生效"。
    head, rest = lines[:3], lines[3:]
    lines = head + ["", "结论      : %s" % verdict, "原因      : %s" % reason,
                    "验证      : 必须新开空白会话 —— 旧会话历史里本来就存着旧块，"]
    lines[-1] = lines[-1] + "\n             旧聊天会把它原样重发，看起来像“没生效”，实际是历史回放。"
    lines = lines + rest
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
    # 只有 NOT EFFECTIVE 才非 0：UNPROVEN 是"还没有新请求可证明"，PARTIAL 是
    # "当前这套好了、别的安装没打全"，都不该让 deploy 报红。
    return 1 if verdict == "NOT EFFECTIVE" else 0


if __name__ == "__main__":
    sys.exit(main())
