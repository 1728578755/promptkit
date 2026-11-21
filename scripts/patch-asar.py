#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""去掉 app.asar 里 AdditionalDataSection 每轮注入的 <current_time> 块。

原理
----
`<current_time>` 不是模板，是打包在 app.asar 里的 JS 现场拼出来的：

    r=[`<current_time>`,new Intl.DateTimeFormat(`en-US`,{...}).format(new Date),`</current_time>`],i=...

做法是**等长原地替换**成 `r=[/* … */]`（数组置空，注释补齐字节），
所以 asar 头里的 offset/size 全部不变，运行时的别的代码一个字节都不动。

两层 asar 完整性校验（2026-11-17 补）
------------------------------------
WorkBuddy 的 Electron 启用了 asar 完整性校验，改 asar 必须同时满足两层：

1. **每文件哈希**：asar 头里每个文件都有
       "integrity":{"algorithm":"SHA256","hash":"<整文件哈希>",
                    "blockSize":4194304,"blocks":["<每块哈希>"]}
   只改内容不改它，重启会报
       ASAR Integrity Violation: got a hash mismatch (新哈希 vs 头里旧哈希)
   然后退出。本脚本会同步更新该 entry 的 hash / blocks（等长替换）。

2. **整头哈希**：WorkBuddy.exe 里嵌了一张清单
       [{"file":"resources\\\\app.asar","alg":"SHA256","value":"<asar 头 JSON 的 SHA256>"}, …]
   改了头（第 1 步）不更新它，会报
       FATAL asar_util.cc:143 Integrity check failed for asar archive (实际 vs 清单)
   本脚本会把清单里 resources\\\\app.asar 的 value 改成新头哈希（等长，只动 64 字符）。

于是 apply 是“内容 227 字节 + asar 头 2 处哈希 + exe 清单 2 处哈希”的原子操作；
任何一步失败，三处一起回滚。restore 反向执行并断言全文件哈希回到 shaBefore。

安全性
------
- 只改目标段、该 entry 的 integrity、exe 清单里 app.asar 的 value；
- 备份原始字节片段（几百字节，不是 173 MB）+ 补丁前后全文件 SHA256；
- 解出承载 bundle 跑 `node --check`；
- 写完后重新解析 asar 头，断言 integrity.hash/blocks 与实际内容一致；
- 任何一步失败，内容、头哈希、exe 清单一起回滚；
- `--restore` 断言 asar 与 exe 的 SHA256 都回到 shaBefore。

用法
----
    python scripts/patch-asar.py --status --asar "<安装目录>\\resources\\app.asar"
    python scripts/patch-asar.py --dry-run --asar "<安装目录>\\resources\\app.asar"
    python scripts/patch-asar.py --apply --asar "<安装目录>\\resources\\app.asar"
    python scripts/patch-asar.py --restore --asar "<安装目录>\\resources\\app.asar"
    python scripts/patch-asar.py --apply --no-exe      # 只改 asar（应用会起不来，仅排障用）

`--asar` 省略时按 `WORKBUDDY_INSTALL` 环境变量 → `scripts\\targets.json` 里填好的
`installPath` 顺序找；两处都没有就报错，不猜目录（安装目录用 `scripts\\deploy.cmd
-ListTargets` 查）。`scripts\\deploy.cmd -Asar` 会自动带上正确的路径。

需要管理员（安装目录只读）。WorkBuddy 升级会同时覆盖 app.asar 和 WorkBuddy.exe，
升级后重跑一次。
"""
import argparse
import glob
import hashlib
import json
import os
import re
import shutil
import struct
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _install  # noqa: E402  sibling helper: locate an install without hardcoding

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
BACKUP_ROOT = os.path.join(PROJECT_ROOT, "backups")
DEFAULT_BLOCK_SIZE = 4 * 1024 * 1024

# AdditionalDataSection.render 里的那一段
START = b"r=[`<current_time>`"
END = b"`</current_time>`]"
# 另外两条旁路（不同入口），默认不碰；--all 时一起处理
OPTIONAL = [
    (b"<current_time>${t.toLocaleString(`zh-CN`,{timeZone:`Asia/Shanghai`,hour12:!1})} GMT+8",
     b"<current_time>${t.toLocaleString(`zh-CN`,{timeZone:`Asia/Shanghai`,hour12:!1})} GMT+8"),
]

# exe 里嵌的清单项：{"file":"resources\\app.asar","alg":"SHA256","value":"<64 hex>"}
APP_ASAR_ENTRY_RE = re.compile(
    rb'\{"file":"resources\\\\app\.asar","alg":"[^"]+","value":"([0-9a-fA-F]{64})"\}'
)


def sha256_bytes(b):
    return hashlib.sha256(b).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(8 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def find_segment(data):
    """返回 (start, end) —— 覆盖 r=[`<current_time>` … `</current_time>`]。"""
    i = data.find(START)
    if i < 0:
        return None
    j = data.find(END, i)
    if j < 0:
        return None
    return i, j + len(END)


def make_replacement(length):
    """等长、合法、求值为空数组的表达式： r=[/* … */]"""
    head, tail = b"r=[/*", b"*/]"
    pad = length - len(head) - len(tail)
    if pad < 0:
        raise ValueError("segment too short to neutralise (%d bytes)" % length)
    return head + b" " * pad + tail


def _comment(n):
    """n 字节的块注释（等长补白用）。"""
    if n < 4:
        raise ValueError("补白过短：%d 字节" % n)
    return b"/*" + b" " * (n - 4) + b"*/"


def _sandwich(prefix, suffix, old_len):
    """prefix + 注释 + suffix，总长恰为 old_len。"""
    inner = old_len - len(prefix) - len(suffix)
    return prefix + _comment(inner) + suffix


def _pairs_current_time(data):
    seg = find_segment(data)
    if not seg:
        return []
    start, end = seg
    old = bytes(data[start:end])
    return [(old, make_replacement(len(old)))]


def _pairs_user_info(data):
    """重写整个 <user_info> 表达式：去掉 OS/主题与 Time，Shell 兜底写明 Git Bash。

    为什么去掉 Time：这一块会进到「第一条用户消息」里。只要它的内容变一次
    （重启进程时重新渲染、时间戳不同），整段历史的前缀就作废——DeepSeek 的
    前缀缓存从第一处不同开始失效，实测重启后首轮 417,913 token 只命中 24,576。
    时间改由模型自己用 `date` 取（`<tool_use>` 里已经这么要求）。
    """
    i = data.find(b"`<user_info>`")
    if i < 0:
        return []
    s = data.rfind(b"return", 0, i)
    j = data.find(b"].join(", i)
    if s < 0 or j < 0:
        return []
    j += len(b"]")
    old = bytes(data[s:j])

    core = re.sub(rb"/\* +\*/", b"", old)                       # 去掉补白注释
    TIME_EL = b",`Time: ${new Date().toLocaleString(`sv-SE`)}`"
    if TIME_EL not in core:
        raise ValueError("user_info 里没有找到 Time 元素，形态可能变了")
    core = core.replace(TIME_EL, b"", 1)

    delta = len(old) - len(core)
    if delta < 4:
        raise ValueError("user_info 没有可回收的字节（delta=%d）" % delta)
    new = core.replace(b"return[", b"return" + _comment(delta) + b"[", 1)
    if len(new) != len(old):
        raise ValueError("user_info 重写长度不匹配")
    return [(old, new)]


CONNECTOR_S1 = (b"`For an upstream authentication failure or user-requested reconnection, use list_installed_plugins"
                b" to get the bound connector's exact pluginName, then call reconnect_plugin even if its status is"
                b" connected.`")
CONNECTOR_S2 = (b"`Do not reconnect unrelated connectors or treat disabled, network-error, unavailable, timeouts,"
                b" 5xx, or business permission errors as expired credentials.`")
CONNECTOR_S3 = (b"`After successful reconnection, retry only the original failed operation once. On cancellation or"
                b" a second failure, stop retrying and explain the blocker.`")


def _pairs_connector(data):
    old = b"," + CONNECTOR_S1 + b"," + CONNECTOR_S2 + b"," + CONNECTOR_S3 + b","
    return [(old, b"," + _comment(len(old) - 1))]


SCENE_MAIN_OLD = (b"if(e.uri.startsWith(yn)){let t=e.uri.slice(8),n=e.name||t;return{text:e.name&&t&&e.name!==t"
                  b"&&isBareInlineBadgeValue(t)?`@scene#${t}:${encodeInlineBadgeValue(n)}`:"
                  b"`@scene:${encodeInlineBadgeValue(n)}`}}")
SCENE_RENDERER_OLD = (b"if(e.uri.startsWith(E)){let t=e.uri.slice(8),n=e.name||t;return{text:e.name&&t&&e.name!==t"
                      b"&&isBareInlineBadgeValue(t)?`@scene#${t}:${encodeInlineBadgeValue(n)}`:"
                      b"`@scene:${encodeInlineBadgeValue(n)}`}}")


def _pairs_scene_chip(data):
    """把场景引用的序列化文本置空：chip 还是 chip，但发出去的文字里不再有 @scene#…

    两个副本：main/account.js（拼 prompt 用）与 renderer/assets/common-*.js（输入框序列化用）。
    """
    pairs = []
    for old, var in ((SCENE_MAIN_OLD, b"yn"), (SCENE_RENDERER_OLD, b"E")):
        if data.count(old) != 1:
            continue
        prefix = b"if(e.uri.startsWith(" + var + b")){return{text:``}"
        pairs.append((old, _sandwich(prefix, b"}", len(old))))
    return pairs


def _pairs_project_context(data):
    """把 <project_context> 从「每轮一次」改回「首轮一次」。

    2026-11-17 14:4x：用户明确不要每轮都带目录快照，改回 stage=first_turn，
    同时恢复 shouldApply 里的 alreadyInjectedUserContext 判断（两段等长，直接对调）。
    """
    patched_stage = b"ProjectContextSection=class{constructor(){this.stage=`every_turn`}"
    if data.count(patched_stage) != 1:
        return []
    i = data.find(patched_stage)
    j = data.find(b"async render(e){", i)
    if j < 0:
        raise ValueError("project_context 段尾定位失败")
    old = bytes(data[i:j])
    new = (b"ProjectContextSection=class{constructor(){this.stage=`first_turn`}"
           b"shouldApply(e){return e.codebuddyMeta.alreadyInjectedUserContext===!0||"
           b"isAskLikeMode(e.codebuddyMeta,e.desiredConfig)?!1:typeof e.cwd==`string`&&"
           b"e.cwd.trim().length>0}")
    if len(old) != len(new):
        raise ValueError("project_context 反向补丁长度不匹配：%d vs %d" % (len(old), len(new)))
    return [(old, new)]


def _pairs_project_layout(data):
    b1_head = b"let n=[`<project_layout>`,"
    b1_push = b"n.push(`</project_layout>`),"
    b2_head = b",[`<project_layout>`,"
    b2_tail = b",`</project_layout>`]"
    return [
        (b1_head, _sandwich(b"let n=[", b"", len(b1_head))),
        (b1_push, _sandwich(b"n.push(", b"),", len(b1_push))),
        (b2_head, _sandwich(b",[", b"", len(b2_head))),
        (b2_tail, _sandwich(b",", b"]", len(b2_tail))),
    ]


# ---- user-context 一次性（2026-11-17 第七轮）--------------------------------
# 硬性规则：<system-reminder data-role="user-context"> 只在会话第一轮出现。
# 上游的判据是 `alreadyInjectedUserContext`，取值来自渲染进程内存里的
#   this.sessionInjectedUserContext.has(sessionId)   —— 一个 Set，进程一换就空。
# 换工作区 / 重启 / 换进程 → 判据归零 → user_info、自定义指令、project_context、
# connector-status 又整体注一遍（实测 1,684 字符/轮）。
# 改法：把这条内存标记落到 localStorage（同 bundle 里已有 8 处 localStorage 用法），
#   并把 connector-status 也纳入同一个判据（它原本不在 first_turn 门里）。
UC_HAS_OLD = b"this.sessionInjectedUserContext.has(a)"
UC_HAS_NEW = b"self.localStorage?.getItem(a)   !=null"
UC_ADD_OLD = b"this.sessionInjectedUserContext.add(a)"
UC_ADD_NEW = b"self.localStorage?.setItem(a,`1`     )"
CSTAT_OLD = b"x=renderConnectorStatusBlock(o);x.length>0&&ee.push("
CSTAT_NEW = b"x=r?0:renderConnectorStatusBlock(o);x[0]  &&ee.push("


def _pairs_user_context_once(data):
    """把「首轮一次」的判据从进程内存搬到磁盘，并让 connector-status 也守同一判据。"""
    pairs = [
        (UC_HAS_OLD, UC_HAS_NEW),
        (UC_ADD_OLD, UC_ADD_NEW),
        (CSTAT_OLD, CSTAT_NEW),
    ]
    out = []
    for old, new in pairs:
        if len(old) != len(new):
            raise ValueError("user-context-once 不等长：%d vs %d" % (len(old), len(new)))
        hit = data.count(old)
        if hit == 1:
            out.append((old, new))
        elif data.count(new) != 1:
            raise ValueError("user-context-once 锚点命中 %d 次：%r" % (hit, old[:40]))
    return out


# 文本补丁注册表：每条都是**等长**替换，可独立回滚
PATCHES = [
    {
        "name": "current-time",
        "title": "去掉每轮注入的 <current_time> 块",
        "fn": _pairs_current_time,
        "applied": lambda d: b"*/],i=renderPanelTabsContext" in d,
    },
    {
        "name": "user-info",
        "title": "user_info：去 OS/主题/Time（Time 会破坏前缀缓存），Shell 兜底写明 Git Bash",
        "fn": _pairs_user_info,
        "applied": lambda d: b"<user_info>" in d and b"`Time: ${new Date()" not in d,
    },
    {
        "name": "connector-status",
        "title": "去掉 <connector-status> 里三句重连说明",
        "fn": _pairs_connector,
        "applied": lambda d: b"<connector-status>" in d and b"For an upstream authentication failure" not in d,
    },
    {
        "name": "project-layout",
        "title": "去掉 <project_layout> 内层标签（保留目录树）",
        "fn": _pairs_project_layout,
        "applied": lambda d: b"<project_layout>" not in d,
    },
    {
        "name": "project-context-once",
        "title": "project_context 回到「首轮一次」（不再每轮都带目录快照）",
        "fn": _pairs_project_context,
        "applied": lambda d: b"ProjectContextSection=class{constructor(){this.stage=`first_turn`}" in d,
    },
    {
        "name": "scene-chip",
        "title": "场景引用 @scene#… 不再序列化进消息文本",
        "fn": _pairs_scene_chip,
        "applied": lambda d: SCENE_MAIN_OLD not in d and SCENE_RENDERER_OLD not in d,
    },
    {
        "name": "user-context-once",
        "title": "user-context 只在首轮注入：判据从进程内存搬到 localStorage，并把 connector-status 纳入同一判据",
        "fn": _pairs_user_context_once,
        "applied": lambda d: (b"self.localStorage?.getItem(a)" in d
                              and b"self.localStorage?.setItem(a,`1`" in d
                              and b"x=r?0:renderConnectorStatusBlock(o)" in d),
    },
]


def collect_content_edits(data):
    """返回 (edits, done_names, missing_names)，edits 为 [(abs_offset, old, new)]。"""
    edits, done, missing = [], [], []
    for spec in PATCHES:
        if spec["applied"](data):
            done.append(spec["name"])
            continue
        pairs = spec["fn"](data)
        if not pairs:
            missing.append(spec["name"])
            continue
        for old, new in pairs:
            if len(old) != len(new):
                raise ValueError("%s 替换不等长：%d → %d" % (spec["name"], len(old), len(new)))
            hit = data.count(old)
            if hit != 1:
                raise ValueError("%s 锚点命中 %d 次（应为 1）：%r" % (spec["name"], hit, old[:60]))
            edits.append((data.find(old), old, new))
        done.append(spec["name"])
    return edits, done, missing


def patch_states(data):
    return [(spec["name"], "patched" if spec["applied"](data) else "raw") for spec in PATCHES]


def parse_header(data):
    """解析 asar 头，返回 JSON 区间、数据起点和 header 对象。

    @electron/asar 的头部布局：
        0..4    uint32 = 4
        4..8    uint32 = pickle size
        8..12   uint32 = pickle[0:4]
        12..16  uint32 = JSON 长度
        16..    header JSON
    """
    if len(data) < 16 or struct.unpack("<I", data[0:4])[0] != 4:
        raise ValueError("不是 asar 文件")
    pickle_size = struct.unpack("<I", data[4:8])[0]
    data_off = 8 + pickle_size
    json_len = struct.unpack("<I", data[12:16])[0]
    if data_off > len(data) or 16 + json_len > len(data):
        raise ValueError("asar 头长度越界")
    header = json.loads(data[16:16 + json_len].decode("utf-8"))
    if not isinstance(header, dict) or "files" not in header:
        raise ValueError("asar 头缺少 files")
    return {
        "json_start": 16,
        "json_len": json_len,
        "json_end": 16 + json_len,
        "data_off": data_off,
        "header": header,
    }


def header_sha(data, info):
    """整头哈希：exe 清单里 value 校验的对象。"""
    return sha256_bytes(bytes(data[info["json_start"]:info["json_end"]]))


def walk_entries(header):
    stack = [("", header)]
    while stack:
        prefix, node = stack.pop()
        for name, entry in (node.get("files") or {}).items():
            rel = prefix + "/" + name
            if not isinstance(entry, dict):
                continue
            if "files" in entry:
                stack.append((rel, entry))
                continue
            yield rel, entry


def find_entry(header, data_off, abs_offset):
    """找出承载 abs_offset 的文件 entry。"""
    rel_off = abs_offset - data_off
    for rel, entry in walk_entries(header):
        if entry.get("unpacked"):
            continue
        raw_off = entry.get("offset")
        if raw_off is None:
            continue
        off = int(raw_off)
        size = int(entry.get("size", 0))
        if off <= rel_off < off + size:
            return rel, entry
    return None, None


def find_integrity_span(data, info, entry):
    """返回 entry 的 integrity 对象在文件中的 [start, end) 字节区间。"""
    size = int(entry.get("size", 0))
    off = int(entry["offset"])
    js_start, js_end = info["json_start"], info["json_end"]
    markers = [
        b'"size":%d,"offset":"%d"' % (size, off),
        b'"size":%d,"offset":%d' % (size, off),
    ]
    hits = []
    for marker in markers:
        pos = data.find(marker, js_start, js_end)
        if pos >= 0:
            hits.append(pos)
    if not hits:
        raise ValueError("在 asar 头里定位不到 entry JSON（size=%d offset=%d）" % (size, off))
    if len(set(hits)) > 1:
        raise ValueError("entry JSON 出现多个候选，拒绝修改 asar 头")
    hit = hits[0]
    integ = data.find(b'"integrity"', hit, js_end)
    if integ < 0:
        raise ValueError("entry 没有 integrity 字段")
    brace = data.find(b"{", integ, js_end)
    if brace < 0:
        raise ValueError("entry integrity 不是对象")
    text = data[brace:js_end].decode("utf-8")
    obj, consumed = json.JSONDecoder().raw_decode(text)
    if not isinstance(obj, dict):
        raise ValueError("entry integrity 解析失败")
    return brace, brace + consumed, obj


def compute_block_hashes(content, block_size):
    if block_size <= 0:
        block_size = DEFAULT_BLOCK_SIZE
    return [sha256_bytes(content[i:i + block_size]) for i in range(0, len(content), block_size)]


def build_integrity_edits(data, info, entry, content):
    """计算把 entry integrity 同步到 content 所需的等长替换。

    返回 (edits, detail)；edits 为 [(abs_offset, old_bytes, new_bytes), …]。
    entry 没有 integrity 字段时返回 ([], None) —— 说明这个 asar 没启用校验。
    """
    integ = entry.get("integrity")
    if not integ:
        return [], None
    block_size = int(integ.get("blockSize") or DEFAULT_BLOCK_SIZE)
    old_hash = integ.get("hash")
    old_blocks = list(integ.get("blocks") or [])
    new_hash = sha256_bytes(content)
    new_blocks = compute_block_hashes(content, block_size)
    if old_blocks and len(old_blocks) != len(new_blocks):
        raise ValueError("块数不一致：header=%d 实际=%d（文件大小变了？）" % (len(old_blocks), len(new_blocks)))

    mapping = {}

    def add(old, new, label):
        if not old or not new or old == new:
            return
        if old in mapping and mapping[old] != new:
            raise ValueError("%s：同一个旧哈希对应多个新哈希，拒绝改头" % label)
        mapping[old] = new

    add(old_hash, new_hash, "hash")
    for i, (o, n) in enumerate(zip(old_blocks, new_blocks)):
        add(o, n, "blocks[%d]" % i)

    sig_start, sig_end, _ = find_integrity_span(data, info, entry)
    edits = []
    for old, new in mapping.items():
        ob, nb = old.encode("ascii"), new.encode("ascii")
        if len(ob) != len(nb):
            raise ValueError("哈希长度不一致：%d vs %d" % (len(ob), len(nb)))
        pos = sig_start
        while True:
            i = data.find(ob, pos, sig_end)
            if i < 0:
                break
            edits.append((i, ob, nb))
            pos = i + len(ob)
    if mapping and not edits:
        raise ValueError("integrity 哈希对不上，但头里找不到旧值")
    detail = {
        "file": None,
        "blockSize": block_size,
        "oldHash": old_hash,
        "newHash": new_hash,
        "oldBlocks": old_blocks,
        "newBlocks": new_blocks,
        "edits": [[off, old.decode(), new.decode()] for off, old, new in edits],
    }
    return edits, detail


def default_exe_for(asar):
    """<install>\\WorkBuddy*.exe：CN 版是 WorkBuddy.exe，国际版是 WorkBuddyAI.exe。

    只按固定文件名找会让国际版永远 exit 1（exe 清单同步不了 → 应用起不来）。
    优先级：精确的 WorkBuddy.exe → 唯一的 WorkBuddy*.exe（排除卸载器）→ 第一个候选。
    """
    install = os.path.dirname(os.path.dirname(asar))
    exact = os.path.join(install, "WorkBuddy.exe")
    if os.path.isfile(exact):
        return exact
    cands = [p for p in sorted(glob.glob(os.path.join(install, "WorkBuddy*.exe")))
             if "ninstall" not in os.path.basename(p)]
    if cands:
        return cands[0]
    return exact  # 不存在时保留路径，交给上层报错（信息里带全路径）


def exe_write_blocked(exe_path):
    """能不能写 exe。运行中的 exe 是镜像映射，写会被拒；直接试一次比查进程名可靠
    （复制到别处的副本同名但不锁，按名字判断会误报）。"""
    if not os.path.isfile(exe_path):
        return False
    try:
        with open(exe_path, "r+b") as f:
            f.seek(0)
        return False
    except PermissionError:
        return True
    except Exception:
        return True


def exe_manifest_hashes(exe_path):
    with open(exe_path, "rb") as f:
        blob = f.read()
    return [m.group(1).decode("ascii") for m in APP_ASAR_ENTRY_RE.finditer(blob)]


def plan_exe_manifest_sync(exe_path, desired_hash):
    """把 exe 清单里 resources\\app.asar 的 value 同步到 desired_hash。

    返回 (edits, total_entries)。edits 为 [(abs_offset, old_bytes, new_bytes), …]。
    """
    with open(exe_path, "rb") as f:
        blob = f.read()
    matches = list(APP_ASAR_ENTRY_RE.finditer(blob))
    if not matches:
        raise ValueError("exe 里找不到 resources\\app.asar 的完整性清单")
    edits = []
    for m in matches:
        cur = m.group(1).decode("ascii")
        if cur.lower() == desired_hash.lower():
            continue
        # 保持原清单的大小写风格
        want = desired_hash.upper() if any(c.isupper() for c in cur) else desired_hash.lower()
        edits.append((m.start(1), cur.encode("ascii"), want.encode("ascii")))
    return edits, len(matches)


def write_edits(path, edits):
    with open(path, "r+b") as f:
        for off, _old, new in edits:
            f.seek(off)
            f.write(new)
        f.flush()
        os.fsync(f.fileno())


def read_back(path, edits):
    with open(path, "rb") as f:
        for off, _old, new in edits:
            f.seek(off)
            if f.read(len(new)) != new:
                return False
    return True


def check_integrity(asar, abs_offset):
    """重新解析落盘后的 asar，断言 entry integrity 与实际内容一致。"""
    with open(asar, "rb") as f:
        head = f.read(16)
        pickle_size = struct.unpack("<I", head[4:8])[0]
        f.seek(0)
        blob = f.read(8 + pickle_size)
        info = parse_header(blob)
        rel, entry = find_entry(info["header"], info["data_off"], abs_offset)
        if not entry:
            return None, "没定位到承载文件"
        integ = entry.get("integrity")
        if not integ:
            return None, "entry 没有 integrity 字段"
        _start, _end, obj = find_integrity_span(blob, info, entry)
        size = int(entry.get("size", 0))
        f.seek(info["data_off"] + int(entry["offset"]))
        content = f.read(size)
    block_size = int(integ.get("blockSize") or DEFAULT_BLOCK_SIZE)
    calc_hash = sha256_bytes(content)
    calc_blocks = compute_block_hashes(content, block_size)
    ok = calc_hash == obj.get("hash") and calc_blocks == list(obj.get("blocks") or [])
    return ok, "%s hash=%s… blocks=%d" % (rel, calc_hash[:16], len(calc_blocks))


def extract_containing_bundle(asar_path, abs_offset):
    """从 asar 头里找出承载 abs_offset 的文件，解到临时目录，返回路径。"""
    with open(asar_path, "rb") as f:
        head = f.read(16)
        pickle_size = struct.unpack("<I", head[4:8])[0]
        f.seek(0)
        blob = f.read(8 + pickle_size)
        info = parse_header(blob)
        rel, entry = find_entry(info["header"], info["data_off"], abs_offset)
        if not entry:
            return None, None, 0
        size = int(entry.get("size", 0))
        f.seek(info["data_off"] + int(entry["offset"]))
        content = f.read(size)
    out = os.path.join(tempfile.gettempdir(), "wb-asar-check.js")
    with open(out, "wb") as fh:
        fh.write(content)
    return out, rel, size


def node_check(path):
    node = shutil.which("node")
    if not node:
        return None, "node not found in PATH"
    p = subprocess.run([node, "--check", path], capture_output=True, text=True)
    return p.returncode == 0, (p.stderr or p.stdout).strip()[:800]


def backup_dir(asar_path):
    stamp = time.strftime("%Y%m%d-%H%M%S")
    d = os.path.join(BACKUP_ROOT, "%s-asar-current-time" % stamp)
    os.makedirs(d, exist_ok=True)
    return d


def latest_backup(asar_path):
    if not os.path.isdir(BACKUP_ROOT):
        return None
    cands = []
    for name in os.listdir(BACKUP_ROOT):
        if not name.endswith("-asar-current-time"):
            continue
        meta = os.path.join(BACKUP_ROOT, name, "meta.json")
        if not os.path.isfile(meta):
            continue
        try:
            with open(meta, encoding="utf-8") as f:
                m = json.load(f)
        except Exception:
            continue
        if os.path.normcase(m.get("asar", "")) == os.path.normcase(asar_path):
            cands.append((m.get("appliedAt", ""), name))
    if not cands:
        return None
    cands.sort()
    return os.path.join(BACKUP_ROOT, cands[-1][1])


def status(data):
    seg = find_segment(data)
    if seg:
        return "raw"
    if b"r=[/*" in data and b"*/],i=renderPanelTabsContext" in data:
        return "patched"
    # 定位不到任何形态
    i = data.find(b"<current_time>")
    return "unknown@%d" % i if i >= 0 else "absent"


def locate_target(data):
    """返回目标段起点（raw 与 patched 都适用），找不到返回 -1。"""
    seg = find_segment(data)
    if seg:
        return seg[0]
    pos = data.find(b"*/],i=renderPanelTabsContext")
    if pos >= 0:
        return data.rfind(b"r=[/*", 0, pos)
    return -1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--asar", default=_install.find_asar(),
                    help="<安装目录>\\resources\\app.asar（省略时按 WORKBUDDY_INSTALL / targets.json 找）")
    ap.add_argument("--exe", default=None, help="WorkBuddy.exe 路径（默认从 asar 推）")
    ap.add_argument("--no-exe", action="store_true", help="不同步 exe 清单（应用会起不来，仅排障）")
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--restore", action="store_true")
    ap.add_argument("--status", action="store_true")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--no-node-check", action="store_true")
    args = ap.parse_args()

    asar = os.path.abspath(args.asar) if args.asar else ""
    if not asar:
        print("没有指定 app.asar，也没在 WORKBUDDY_INSTALL / scripts\\targets.json 里找到安装")
        print("  " + _install.HINT)
        return 2
    if not os.path.isfile(asar):
        print("asar 不存在: %s" % asar)
        return 2
    exe = None if args.no_exe else os.path.abspath(args.exe or default_exe_for(asar))

    size = os.path.getsize(asar)
    with open(asar, "rb") as f:
        data = bytearray(f.read())
    st = status(data)
    try:
        info = parse_header(data)
    except Exception as e:
        print("asar 头解析失败: %s" % e)
        return 1

    if args.status or (not args.apply and not args.restore and not args.dry_run):
        print("asar:   %s" % asar)
        print("size:   %d" % size)
        for name, s in patch_states(data):
            print("patch:  %-17s %s" % (name, s))
        print("sha256: %s" % sha256_bytes(data))
        target = locate_target(data)
        if target >= 0:
            try:
                rel, entry = find_entry(info["header"], info["data_off"], target)
                if entry:
                    content_start = info["data_off"] + int(entry["offset"])
                    content = bytes(data[content_start:content_start + int(entry["size"])])
                    integ = entry.get("integrity") or {}
                    calc = sha256_bytes(content)
                    stored = integ.get("hash")
                    print("entry:  %s" % rel)
                    print("integ:  %s" % ("MATCH" if stored == calc else "MISMATCH (%s vs %s)" % (calc[:16], (stored or "none")[:16])))
            except Exception as e:
                print("integ:  无法检查（%s）" % e)
        hh = header_sha(data, info)
        print("header: %s" % hh)
        if exe:
            if not os.path.isfile(exe):
                print("exe:    不存在 %s" % exe)
            else:
                try:
                    vals = exe_manifest_hashes(exe)
                    print("exe:    %s" % exe)
                    print("exe manifest: %d entries, values=%s" % (len(vals), ", ".join(v[:16] + "…" for v in vals)))
                    ok = bool(vals) and all(v.lower() == hh.lower() for v in vals)
                    print("exe sync: %s" % ("MATCH" if ok else "MISMATCH"))
                except Exception as e:
                    print("exe:    无法检查（%s）" % e)
        return 0

    if args.restore:
        d = latest_backup(asar)
        if not d:
            print("没有找到 %s 的备份" % asar)
            return 2
        with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)

        # 新格式：sites（按承载文件整份备份）；兼容旧格式：offset + segment.bin
        sites = meta.get("sites")
        if not sites:
            seg_path = os.path.join(d, "segment.bin")
            if not os.path.isfile(seg_path):
                print("备份里既没有 sites 也没有 segment.bin")
                return 2
            sites = [{"offset": int(meta["offset"]), "length": os.path.getsize(seg_path),
                      "file": "segment.bin", "rel": "?"}]

        buf = bytearray(data)
        asar_edits = []
        for site in sites:
            off = int(site["offset"])
            blob = open(os.path.join(d, site["file"]), "rb").read()
            if bytes(buf[off:off + len(blob)]) != blob:
                asar_edits.append((off, bytes(buf[off:off + len(blob)]), blob))
                buf[off:off + len(blob)] = blob

        # 头哈希回到原始值：按还原后的内容重新同步（可能涉及多个承载文件）
        try:
            for site in sites:
                off = int(site["offset"])
                rel, entry = find_entry(info["header"], info["data_off"], off)
                if not entry:
                    continue
                content_start = info["data_off"] + int(entry["offset"])
                content = bytes(buf[content_start:content_start + int(entry["size"])])
                hedits, detail = build_integrity_edits(buf, info, entry, content)
                for eoff, old, new in hedits:
                    buf[eoff:eoff + len(old)] = new
                    asar_edits.append((eoff, old, new))
        except Exception as e:
            print("!! 还原头哈希失败：%s" % e)
            return 1
        want_header = header_sha(buf, info)

        exe_edits = []
        exe_path = (meta.get("exe") or {}).get("path") or exe
        if exe_path:
            try:
                exe_edits, n_entries = plan_exe_manifest_sync(exe_path, want_header)
            except Exception as e:
                print("!! 还原 exe 清单失败：%s" % e)
                return 1
        else:
            n_entries = 0

        if not asar_edits and not exe_edits:
            print("已经是原始状态，无需还原")
            return 0

        if exe_edits and exe_path and exe_write_blocked(exe_path):
            print("!! WorkBuddy 正在运行，无法写 WorkBuddy.exe；还原未执行（asar 未改）。")
            print("   关闭 WorkBuddy 后重跑 scripts\\restore-asar.cmd")
            return 4

        try:
            if asar_edits:
                write_edits(asar, asar_edits)
            if exe_edits:
                write_edits(exe_path, exe_edits)
        except PermissionError as e:
            print("写入被拒绝（需要管理员）: %s" % e)
            return 3
        if asar_edits and not read_back(asar, asar_edits):
            print("!! asar 回读不一致")
            return 1
        if exe_edits and not read_back(exe_path, exe_edits):
            print("!! exe 回读不一致")
            return 1
        after = sha256_file(asar)
        ok = after == meta["shaBefore"]
        exe_ok = True
        if exe_edits:
            exe_before = (meta.get("exe") or {}).get("shaBefore")
            if exe_before:
                exe_ok = sha256_file(exe_path) == exe_before
        print("restored: %d file(s), %d asar edit(s), exe %d manifest value(s)" % (
            len(sites), len(asar_edits), len(exe_edits)))
        print("asar sha256 %s (expect %s) -> %s" % (after[:16], meta["shaBefore"][:16], "OK" if ok else "MISMATCH"))
        if exe_edits:
            print("exe  sha256 -> %s" % ("OK" if exe_ok else "MISMATCH"))
        return 0 if (ok and exe_ok) else 1

    # ---- apply / dry-run ----
    try:
        content_edits, done, missing = collect_content_edits(data)
    except ValueError as e:
        print("!! 补丁锚点异常：%s" % e)
        return 2

    if missing:
        print("!! 本版本代码里找不到这些补丁的锚点：%s" % ", ".join(missing))
        print("   可能 WorkBuddy 升级改写了对应代码；本次不写入任何改动。")
        for name in missing:
            spec = next(s for s in PATCHES if s["name"] == name)
            print("   - %-17s %s" % (name, spec["title"]))
        return 2

    if not content_edits:
        # 内容补丁全部已在位；可能只是 exe 清单没同步（旧版脚本只改了头）
        hh = header_sha(data, info)
        print("already patched: %s" % ", ".join(done))
        print("header:  %s" % hh)
        if not exe:
            print("exe:     跳过（--no-exe）")
            return 0
        try:
            exe_edits, n_entries = plan_exe_manifest_sync(exe, hh)
        except Exception as e:
            print("!! exe 清单处理失败：%s" % e)
            return 1
        if not exe_edits:
            print("exe sync: MATCH（%d 处清单已是当前头哈希）" % n_entries)
            return 0
        old_vals = exe_manifest_hashes(exe)
        exe_sha_before = sha256_file(exe)
        if exe_write_blocked(exe):
            print("!! WorkBuddy.exe 写不进去（通常因为 WorkBuddy 正在运行，exe 被镜像锁定）。")
            print("   请关闭 WorkBuddy 后运行 scripts\\apply-asar.cmd")
            return 4
        try:
            write_edits(exe, exe_edits)
        except PermissionError as e:
            print("写入被拒绝（需要管理员）: %s" % e)
            return 3
        if not read_back(exe, exe_edits):
            write_edits(exe, [(o, n, o) for o, o, n in exe_edits])
            print("!! exe 回读不一致，已回滚")
            return 1
        d = latest_backup(asar)
        if d:
            with open(os.path.join(d, "meta.json"), encoding="utf-8") as f:
                meta = json.load(f)
        else:
            d = backup_dir(asar)
            meta = {"asar": asar, "offset": 0, "length": 0, "shaBefore": sha256_file(asar),
                    "appliedAt": time.strftime("%Y-%m-%dT%H:%M:%S")}
            open(os.path.join(d, "segment.bin"), "wb").close()
        meta["exe"] = {
            "path": exe,
            "shaBefore": exe_sha_before,
            "shaAfter": sha256_file(exe),
            "edits": [[o, old.decode(), new.decode()] for o, old, new in exe_edits],
        }
        if old_vals:
            meta.setdefault("headerHashBefore", old_vals[0].lower())
        meta["headerHashAfter"] = hh
        with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
        print("exe sync: %d 处清单已更新 -> %s" % (len(exe_edits), hh[:16]))
        print("exe sha256: %s -> %s" % (exe_sha_before[:16], meta["exe"]["shaAfter"][:16]))
        print("生效时机：重启 WorkBuddy")
        return 0

    print("asar:      %s" % asar)
    print("patches:   %s" % ", ".join(done))

    # 先在内存里算出内容 + 头哈希的全部改动，失败就不落盘
    buf = bytearray(data)
    for off, old, new in content_edits:
        if bytes(buf[off:off + len(old)]) != old:
            print("!! offset %d 处内容与预期不符，放弃" % off)
            return 1
        buf[off:off + len(old)] = new

    # 受影响的承载文件可能不止一个（正文与 renderer 是两个 bundle）
    affected = []
    seen_rel = set()
    for off, _old, _new in content_edits:
        rel, entry = find_entry(info["header"], info["data_off"], off)
        if not entry:
            print("!! 没在 asar 头里定位到 offset %d 的承载文件" % off)
            return 1
        if rel not in seen_rel:
            seen_rel.add(rel)
            affected.append((rel, entry))

    hedits_all, details = [], []
    try:
        for rel, entry in affected:
            content_start = info["data_off"] + int(entry["offset"])
            content = bytes(buf[content_start:content_start + int(entry["size"])])
            hedits, detail = build_integrity_edits(buf, info, entry, content)
            hedits_all += hedits
            if detail:
                detail["file"] = rel
                details.append(detail)
    except Exception as e:
        print("!! 计算 integrity 改动失败：%s" % e)
        return 1
    for eoff, old, new in hedits_all:
        buf[eoff:eoff + len(old)] = new
    new_header = header_sha(buf, info)

    for detail in details:
        print("integrity: %-42s %s -> %s（%d 处头哈希）" % (
            detail["file"], detail["oldHash"][:16], detail["newHash"][:16], len(detail["edits"])))
    if not details:
        print("integrity: 该 asar 没有 integrity 字段，跳过头同步")

    exe_edits = []
    n_entries = 0
    if exe:
        try:
            exe_edits, n_entries = plan_exe_manifest_sync(exe, new_header)
        except Exception as e:
            print("!! exe 清单处理失败：%s" % e)
            return 1
        print("exe:       %s  (%d 处 app.asar 清单，%d 处需更新)" % (exe, n_entries, len(exe_edits)))
    else:
        print("exe:       跳过（--no-exe）—— 应用启动会被整头校验拦住")

    if args.dry_run:
        print("[dry-run] 不写文件")
        return 0

    if exe and exe_edits and exe_write_blocked(exe):
        print("!! WorkBuddy.exe 写不进去（通常因为 WorkBuddy 正在运行，exe 被镜像锁定）。")
        print("   本次未写入任何改动（asar 也未改）。请关闭 WorkBuddy 后运行：")
        print("   scripts\\apply-asar.cmd   （会提权、打补丁、再把 WorkBuddy 启动起来）")
        return 4

    d = backup_dir(asar)
    sites = []
    for idx, (rel, entry) in enumerate(affected):
        cs = info["data_off"] + int(entry["offset"])
        size_e = int(entry["size"])
        name = "file%d.bin" % idx
        with open(os.path.join(d, name), "wb") as f:
            f.write(bytes(data[cs:cs + size_e]))
        sites.append({"offset": cs, "length": size_e, "file": name, "rel": rel})
    sha_before = sha256_bytes(data)
    exe_sha_before = sha256_file(exe) if exe else None
    meta = {
        "asar": asar,
        "sites": sites,
        "shaBefore": sha_before,
        "patches": done,
        "headerHashBefore": details[0]["oldHash"] if details else None,
        "headerHashAfter": new_header,
        "appliedAt": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": "patch-asar.py restore 用；按承载文件整份备份 + 头哈希 + exe 清单",
        "integrity": details,
        "exe": {
            "path": exe,
            "shaBefore": exe_sha_before,
            "edits": [[o, old.decode(), new.decode()] for o, old, new in exe_edits],
        } if exe else None,
    }
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)

    asar_edits = list(content_edits) + hedits_all
    try:
        write_edits(asar, asar_edits)
        if exe_edits:
            write_edits(exe, exe_edits)
    except PermissionError as e:
        print("写入被拒绝（需要管理员）: %s" % e)
        return 3

    def rollback(reason):
        print("!! %s，立即回滚" % reason)
        try:
            if exe_edits:
                write_edits(exe, [(off, new, old) for off, old, new in exe_edits])
            write_edits(asar, [(off, new, old) for off, old, new in asar_edits])
        except Exception as e2:
            print("!! 回滚写入失败：%s" % e2)
            return False
        back = sha256_file(asar)
        print("   回滚后 asar sha256 %s -> %s" % (back[:16], "OK" if back == sha_before else "MISMATCH"))
        if exe and exe_sha_before:
            eb = sha256_file(exe)
            print("   回滚后 exe  sha256 %s -> %s" % (eb[:16], "OK" if eb == exe_sha_before else "MISMATCH"))
        return back == sha_before

    if not read_back(asar, asar_edits):
        rollback("asar 回读不一致")
        return 1
    if exe_edits and not read_back(exe, exe_edits):
        rollback("exe 回读不一致")
        return 1
    if os.path.getsize(asar) != size:
        rollback("asar 文件大小变了")
        return 1

    integ_ok = True
    for site in sites:
        ok, msg = check_integrity(asar, int(site["offset"]))
        if ok is False:
            integ_ok = False
            print("integrity check: FAILED (%s)" % msg)
            break
        print("integrity check: %s" % ("OK (%s)" % msg if ok else "跳过（%s）" % msg))
    if not integ_ok:
        rollback("integrity 校验不匹配")
        return 1
    if exe:
        try:
            vals = exe_manifest_hashes(exe)
            synced = bool(vals) and all(v.lower() == new_header.lower() for v in vals)
            print("exe manifest:   %s" % ("MATCH（%d 处）" % len(vals) if synced else "MISMATCH"))
            if not synced:
                rollback("exe 清单未同步")
                return 1
        except Exception as e:
            rollback("exe 清单无法复核（%s）" % e)
            return 1

    ok = True
    if not args.no_node_check:
        for rel_s, entry_s in affected:
            cs = info["data_off"] + int(entry_s["offset"])
            bundle, rel2, bsize = extract_containing_bundle(asar, cs)
            if not bundle:
                print("node --check: 跳过（没在 asar 头里定位到承载文件）")
                break
            good, msg = node_check(bundle)
            if good is None:
                print("node --check: 跳过（%s）" % msg)
            else:
                print("node --check: %s  (%s, %d bytes)" % ("OK" if good else "FAILED", rel2, bsize))
                if not good:
                    print(msg)
                    ok = False
            try:
                os.remove(bundle)
            except Exception:
                pass

    if not ok:
        rollback("语法校验失败")
        return 1

    meta["shaAfter"] = sha256_file(asar)
    if exe:
        meta["exe"]["shaAfter"] = sha256_file(exe)
    with open(os.path.join(d, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, ensure_ascii=False, indent=2)
    print("backup:    %s" % d)
    print("sha256:    %s -> %s" % (sha_before[:16], meta["shaAfter"][:16]))
    if exe:
        print("exe:       %s -> %s" % (exe_sha_before[:16], meta["exe"]["shaAfter"][:16]))
    print("patches:   %s" % ", ".join("%s=%s" % (n, s) for n, s in patch_states(open(asar, "rb").read())))
    print("生效时机：重启 WorkBuddy（已加载的进程沿用旧代码）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
