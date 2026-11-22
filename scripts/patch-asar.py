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
- **asar 与 exe 的写入是一个事务**：exe 写失败（权限 / 被锁 / 任何异常）立即
  回滚 asar 并打印"已回滚 asar，避免 integrity 失配"——app.asar 已改而 exe
  清单没跟上，客户端启动会直接 `FATAL asar_util.cc:143 Integrity check failed`；
- 备份目录先以 `tmp-<stamp>-asar-current-time` 存在，写盘成功且回读 / integrity /
  exe 清单 / 语法全部通过后才改名为正式备份；失败或回滚时删掉临时目录，
  `latest_backup()` 只认正式备份（`tmp-` / `failed-` 前缀一律跳过）；
- 任何一步失败，内容、头哈希、exe 清单一起回滚；
- `--restore` 断言 asar 与 exe 的 SHA256 都回到 shaBefore。

补丁分级（2026-11-22）
----------------------
`core` = `current-time` / `current-time-cloud`：入口清洗与云下发阻断，缺了它们
补丁等于没打。core 锚点失配 → 整体 exit 2，一个字节都不写（保持原有语义）。

`aux` = 其余补丁。aux 锚点失配只跳过这一条，core 与可用的 aux 照常写入，
exit 0 并在 stdout 打出 `N 条补丁未应用（锚点失配）`。`--status` 里把这种
"本构建没有锚点"的补丁标成 `skipped`（区别于"可打但还没打"的 `raw`）。

`user-context-once` 已移除：它把判据写进渲染进程的 `localStorage`，而那个
bundle 里 `localStorage` 并不存在（现有用法全被 `typeof localStorage<'u'` 或
try/catch 保护），`self.localStorage?.` 静默 no-op，实测从未生效。判据现在由
`user-context-durable` 落在主进程磁盘上。

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
import fnmatch
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
# WORKBUDDY_BACKUP_ROOT 只给自测/仿真用：把备份写到临时目录，不污染真实 backups/。
BACKUP_ROOT = (os.environ.get("WORKBUDDY_BACKUP_ROOT")
               or os.path.join(PROJECT_ROOT, "backups"))
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


def _pairs_current_time_cloud(data):
    """不把 current_time 发给云端 resolveMessage；云端据此生成 additional-data 块。"""
    old = b"t.currentTime&&(r.current_time=t.currentTime),"
    new = b"0" + b" " * 12 + b"&&(r.current_time=t.currentTime),"
    if len(old) != len(new):
        raise ValueError("current-time-cloud 不等长：%d vs %d" % (len(old), len(new)))
    n = data.count(old)
    if n == 1:
        return [(old, new)]
    if data.count(new) == 1:
        return []
    raise ValueError("current-time-cloud 锚点命中 %d 次" % n)

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

    两个副本都在才算这条补丁"能打"：只打主进程那一处，输入框序列化仍然会把
    `@scene#…` 写进消息文本，等于补丁只生效一半却报成功。所以任一副本的锚点
    缺失都抛 ValueError —— 由 collect_content_edits 归入 missing_aux，
    apply 会打 WARN 并把这条标成 skipped（上层照常写 core + 其它 aux）。
    """
    pairs = []
    found = []
    for old, var in ((SCENE_MAIN_OLD, b"yn"), (SCENE_RENDERER_OLD, b"E")):
        if data.count(old) != 1:
            continue
        prefix = b"if(e.uri.startsWith(" + var + b")){return{text:``}"
        pairs.append((old, _sandwich(prefix, b"}", len(old))))
        found.append("main" if var == b"yn" else "renderer")
    if len(pairs) != 2:
        raise ValueError("scene-chip 只找到 %d/2 处副本（%s）——主进程与 renderer 必须都在"
                         % (len(pairs), ", ".join(found) or "无"))
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


# ---- user-context 一次性（2026-11-17 第七轮）—— 已废弃，仅留档 ---------------
# **2026-11-22 移除**：这条补丁把「已注入」判据写到渲染进程的 localStorage
#   this.sessionInjectedUserContext.has(a)  →  self.localStorage?.getItem(a)
# 但那个 bundle 里 localStorage 并不存在（自带的 8 处用法全被
# `typeof localStorage<'u'` 或 try/catch 保护），`self.localStorage?.` 静默
# no-op；源码注释当时就自承「实测从未生效」。真实请求里
# alreadyInjectedUserContext 53/53 全是 false（11/18–11/21 日志）。
# 判据现在由下面的 `user-context-durable` 落在主进程磁盘上，所以这条不再
# 注册进 PATCHES：不写、不检查、`--status` 也不再列它（已打过它的旧安装
# 无需还原，那段 bytes 只是死代码）。
UC_HAS_OLD = b"this.sessionInjectedUserContext.has(a)"
UC_HAS_NEW = b"self.localStorage?.getItem(a)   !=null"
UC_ADD_OLD = b"this.sessionInjectedUserContext.add(a)"
UC_ADD_NEW = b"self.localStorage?.setItem(a,`1`     )"
CSTAT_OLD = b"x=renderConnectorStatusBlock(o);x.length>0&&ee.push("
CSTAT_NEW = b"x=r?0:renderConnectorStatusBlock(o);x[0]  &&ee.push("


# ---- user-context 判据落盘（2026-11-21，2026-11-22 加固）---------------------
# 把判据放到**主进程 + 磁盘**：composeUserPrompt 里同步读写
# `<configDir>/uc-seen/<sessionId>`，与原标志取或（r=r||…），
# 跨进程、跨工作区、跨重启都成立。util 是 h.a()（runtime-context2 的
# getWorkbuddyRuntimeConfigDir），fs 已在本文件顶部绑成 O。
#
# 已知取舍：/clear 走 `isClearSession===!0` 提前 return，标记不会被清，
# 所以同一 sessionId 清空对话后不再重注一次；桌面端「新对话」是新 sessionId，
# 不受影响。要清就删 `~/.workbuddy/uc-seen/<sessionId>`（见 scripts/uc-seen.py）。
#
# 加固（P7）：旧补丁体 `try{…}catch{}` 把 h/O 改名后的 ReferenceError 整个吞掉，
# 运行时零痕迹、--status 仍然报 patched。新补丁体：
#   * 仍然整段包在 try/catch 里（绝不让补丁自身把 composeUserPrompt 弄挂）；
#   * catch 里 call console.error(x) —— 失败在客户端日志里可见；
#   * 语句压缩到 142 字节，与旧补丁体**严格等长**，因此既能给原始 bundle 打，
#     也能给已经打过旧补丁的安装原地升级（旧体 → 新体，一次等长替换）。
# 说明：h.a() / O 仍是压缩后的模块绑定，单靠字节预算做不到完全摆脱；但改名
# 现在会在运行时打出错误，而 --status 也要求「新补丁体 + mkdirSync 调用」双条件，
# 不会再出现「静默失效还报 patched」。
UC_SEEN_ANCHOR = b"this.logger.info(`[WorkbuddyUserPromptService] compose project context "
UC_SEEN_TAIL = b"isAskLike:isAskLikeMode(n,e.desiredConfig)});"
UC_SEEN_BODY_OLD = (b"try{let ucd=h.a()+`/uc-seen/`;O.mkdirSync(ucd,{recursive:!0}),"
                    b"r=r||O.existsSync(ucd+e.sessionId),O.writeFileSync(ucd+e.sessionId,`1`)}catch{};")
# **严格 142 字节**，与旧补丁体逐字节等长 —— 真实安装里旧体后面紧跟
# `this.logger…`，没有任何空白可供回收，所以等长是能否原地升级的硬前提。
#
#   `r||=…`              逻辑或赋值（Node 15+ / Chromium 85+），比 `r=r||…` 省 4 字节
#   `{recursive:!0}`     必须是 **boolean true**：Node 24 对 `{recursive:1}` 抛
#                        ERR_INVALID_ARG_TYPE（"must be of type boolean"），
#                        补丁会被 catch 静默吞掉、标记永不落盘
#   `catch(x){console.error(x)}`
#                        整段包住，失败时 console.error 留痕（P7）。变量名必须是
#                        `x`：用 `e` 会遮蔽外层 composeUserPrompt 的 `e.sessionId`。
#   `u` 是目录，`f` 是文件     两者必须分开：把文件全路径传给 mkdirSync 会建出
#                        同名目录，writeFileSync 随即 EISDIR——标记永远写不成。
#                        这个坑是实测出来的（Node 24 fs），不是理论推导。
#   `writeFileSync(f,```)      写入空字符串。判据只看"文件是否存在"，内容无关；
#                        这比 `` `1` `` 省 2 字节，正好把加固后的长度压回 142。
UC_SEEN_BODY_NEW = (b"try{let u=h.a()+`/uc-seen/`,f=u+e.sessionId;O.mkdirSync(u,{recursive:!0}),"
                    b"r||=O.existsSync(f),O.writeFileSync(f,``)}catch(x){console.error(x)}")
# 将来新体万一又变长：只要后面有尾随空白就能对齐；没有就让这条补丁 skipped，
# 绝不硬写（_fit_replacement 的契约）。
UC_SEEN_BODY_SLACK = 8
CSTAT_PROJ_OLD = b"i=renderConnectorStatusBlock(o);i.length>0&&"
CSTAT_PROJ_NEW = b"i=r?0:renderConnectorStatusBlock(o);i[0]  &&"

_UC_DELTA = len(UC_SEEN_BODY_NEW) - len(UC_SEEN_BODY_OLD)
if _UC_DELTA > UC_SEEN_BODY_SLACK:  # 差额必须能靠尾随空白补回来
    raise AssertionError("uc-durable 新补丁体比旧体长 %d 字节，超过可回收的 %d 字节空白"
                         % (_UC_DELTA, UC_SEEN_BODY_SLACK))


_JS_BLANK = (b" ", b"\n", b"\t", b"\r")


def _fit_replacement(data, start, end, new_body):
    """在 data[start:end] 上构造一个与 new_body **等长**的替换。

    新体比旧体短：尾部补空格（与旧补丁的做法一致，JS 里空白无语义）。
    新体比旧体长：把 [end, …) 里紧邻的空白一起算进 old，并把同样多的空白补在
    new 尾部 —— 总长都等于 (end-start)+delta，严格等长。空白不够就抛 ValueError
    （宁可让这条补丁 skipped，也不去动非空白字节）。

    返回 (old_bytes, new_bytes)，两者长度相同。
    """
    delta = len(new_body) - (end - start)
    if delta <= 0:
        return bytes(data[start:end]), new_body + b" " * (-delta)
    j = end
    while j < len(data) and (j - end) < delta and data[j:j + 1] in _JS_BLANK:
        j += 1
    if (j - end) != delta:
        raise ValueError("uc-durable 新补丁体多出 %d 字节，后面只有 %d 字节空白可回收"
                         % (delta, j - end))
    # old 扩了 delta 字节（那些空白本就无语义），new 就是 new_body 本身：
    # 两者长度都是 (end-start)+delta，严格等长。
    return bytes(data[start:j]), new_body


def uc_seen_patched(data):
    """P7 双条件：新补丁体在位 **且** 补丁体里确实有 mkdirSync 调用。

    只查 `/uc-seen/` 会把旧版静默补丁体（catch{}）也算成 patched；只查
    字符串则可能被别处的同名常量误导。两个都满足才算真的生效形态。
    """
    return (UC_SEEN_BODY_NEW in data
            and b"mkdirSync(" in UC_SEEN_BODY_NEW   # 常量自检：防止改体时漏掉调用
            and CSTAT_PROJ_NEW in data)


def _pairs_user_context_durable(data):
    """在 composeUserPrompt 里插入磁盘标记：读 → 取或给 r → 写。

    三种输入形态都处理：
      1. 原始 bundle           → 整段（body+log+gate）等长替换；
      2. 打过旧补丁体的安装     → 只把 142 字节旧体换成新体（P7 升级路径）；
      3. 只有项目分支 cstat 没打 → 单独补那一段。
    """
    pairs = []
    if UC_SEEN_BODY_NEW not in data:
        if UC_SEEN_BODY_OLD in data:
            # 旧补丁体 → 新补丁体，逐字节等长（都是 142），原地升级。
            pairs.append((UC_SEEN_BODY_OLD, UC_SEEN_BODY_NEW))
        else:
            i = data.find(UC_SEEN_ANCHOR)
            if i < 0:
                raise ValueError("uc-durable：找不到 compose project context 日志锚点")
            j = data.find(b"logMemoryIndexComposeGate({", i)
            if j < 0:
                raise ValueError("uc-durable：找不到 logMemoryIndexComposeGate 调用")
            end = data.find(UC_SEEN_TAIL, j)
            if end < 0:
                raise ValueError("uc-durable：gate 调用尾部锚点缺失")
            end += len(UC_SEEN_TAIL)
            new = (
                UC_SEEN_BODY_NEW + b";"
                b"this.logger.info(`[WorkbuddyUserPromptService] compose project context "
                b"${JSON.stringify({sessionId:e.sessionId,alreadyInjectedUserContext:r})}`);"
                b"this.logMemoryIndexComposeGate({sessionId:e.sessionId,"
                b"hasPriorUserMessages:e.hasPriorUserMessages===!0,alreadyInjectedUserContext:r,"
                b"isAskLike:isAskLikeMode(n,e.desiredConfig)});"
            )
            # 整段区间替换：原始 bundle 的这个区间比新内容长得多，直接用 _fit_replacement
            # 统一处理（多余字节以空格补回，全落在 JS 语句之间）。
            old, new = _fit_replacement(data, i, end, new)
            pairs.append((old, new))
    if CSTAT_PROJ_NEW not in data:
        n = data.count(CSTAT_PROJ_OLD)
        if n == 1:
            pairs.append((CSTAT_PROJ_OLD, CSTAT_PROJ_NEW))
        elif n != 0:
            raise ValueError("uc-durable：项目分支 connector-status 锚点命中 %d 次" % n)
        elif UC_SEEN_BODY_NEW not in data:
            raise ValueError("uc-durable：项目分支 connector-status 锚点缺失")
    return pairs


# 文本补丁注册表：每条都是**等长**替换，可独立回滚。
#
# core  = 入口清洗 / 云下发阻断。缺一条就等于没打，锚点失配必须整体拒绝（exit 2）。
# aux   = 其余瘦身补丁。锚点失配只跳过自己，core 与可用的 aux 照常写入（exit 0 + WARN）。
#
# targets：--status 时按这些 asar 内路径（fnmatch 通配）校验 entry integrity。
# 原来只校验 current_time 所在 entry，scene-chip 改的 renderer bundle 没人看，
# renderer 那份 integrity 坏了 `--status` 也照样绿灯。
PATCHES = [
    {
        "name": "current-time",
        "core": True,
        "title": "去掉每轮注入的 <current_time> 块",
        "fn": _pairs_current_time,
        "applied": lambda d: b"*/],i=renderPanelTabsContext" in d,
        "targets": [],  # 由 locate_target() 动态定位（raw/patched 两种形态）
    },
    {
        "name": "current-time-cloud",
        "core": True,
        "title": "current_time 不再发给云端 resolveMessage（additional-data 块的上游）",
        "fn": _pairs_current_time_cloud,
        "applied": lambda d: b"0            &&(r.current_time=t.currentTime)" in d,
        "targets": ["/main/server.js"],
    },
    {
        "name": "user-info",
        "core": False,
        "title": "user_info：去 OS/主题/Time（Time 会破坏前缀缓存），Shell 兜底写明 Git Bash",
        "fn": _pairs_user_info,
        "applied": lambda d: b"<user_info>" in d and b"`Time: ${new Date()" not in d,
        "targets": ["/main/connector-harness-marketplace.js"],
    },
    {
        "name": "connector-status",
        "core": False,
        "title": "去掉 <connector-status> 里三句重连说明",
        "fn": _pairs_connector,
        "applied": lambda d: b"<connector-status>" in d and b"For an upstream authentication failure" not in d,
        "targets": ["/main/connector-harness-marketplace.js"],
    },
    {
        "name": "project-layout",
        "core": False,
        "title": "去掉 <project_layout> 内层标签（保留目录树）",
        "fn": _pairs_project_layout,
        # 同 scene-chip：只看"标签不在"会把"新版本压根没用这个标签"误判成已打过。
        # 补丁的产物是 `let n=[/*` / `n.push(/*`（原标签位置被换成注释），
        # 用正面标记一起判。
        "applied": lambda d: (b"<project_layout>" not in d
                              and b"let n=[/*" in d and b"n.push(/*" in d),
        "targets": ["/main/connector-harness-marketplace.js"],
    },
    {
        "name": "project-context-once",
        "core": False,
        "title": "project_context 回到「首轮一次」（不再每轮都带目录快照）",
        "fn": _pairs_project_context,
        "applied": lambda d: b"ProjectContextSection=class{constructor(){this.stage=`first_turn`}" in d,
        "targets": ["/main/connector-harness-marketplace.js"],
    },
    {
        "name": "scene-chip",
        "core": False,
        "title": "场景引用 @scene#… 不再序列化进消息文本",
        "fn": _pairs_scene_chip,
        # 判据不能只看"原文不在"——新版本重写这段逻辑（既没有原文、也没有本补丁
        # 的形态）会被误判成"已打过"，于是主流程跳过它、--status 还报 patched。
        # 补丁的产物是两个 `return{text:``}`（原来那段被换成空文本 + 注释），
        # 用正面标记 + 原文不在，两条同时成立才算 patched。
        "applied": lambda d: (SCENE_MAIN_OLD not in d and SCENE_RENDERER_OLD not in d
                              and d.count(b"return{text:``}") >= 2),
        # 两处副本：主进程拼 prompt 用 /main/account.js，输入框序列化用 renderer 的
        # common-<hash>.js（名字随构建变）。renderer 那个用内容标记锁定：同目录另外
        # 6 个 common-*.js 与这条补丁无关。两组标记分别覆盖补丁前后形态：
        #   raw     —— `encodeInlineBadgeValue`（只有这个 bundle 有）；
        #   patched —— `return{text:``}`（补丁把整段换成它 + 注释，全库仅此 1 处）。
        # 实测 `e.uri.startsWith(` 太通用（无关的 common-THF5HTIS.js 里 18 处），
        # 会把别人的 bundle 也算进来，所以不用它当标记。
        "targets": ["/main/account.js",
                    ("/renderer/assets/common-*.js",
                     [b"encodeInlineBadgeValue", b"return{text:``}"])],
    },
    {
        "name": "user-context-durable",
        "core": False,
        "title": "user-context 判据落盘：composeUserPrompt 读写 <configDir>/uc-seen/<sessionId>，项目分支的 connector-status 也守同一判据",
        "fn": _pairs_user_context_durable,
        "applied": uc_seen_patched,
        "targets": ["/main/connector-harness-marketplace.js"],
    },
]


def _probe_patch(spec, data):
    """这条补丁在本构建里能不能打：返回 (pairs, error)。

    pairs 为空 = 找不到锚点（本构建形态变了）；error 非空 = 锚点形态异常。
    两者对 aux 都只跳过，对 core 都拒绝写入。
    """
    try:
        return spec["fn"](data), None
    except ValueError as e:
        return [], str(e)


def collect_content_edits(data):
    """返回 (edits, done, missing_core, missing_aux)。

    edits 为 [(abs_offset, old, new)]；missing_* 为 [(name, reason)]。
    单条 aux 的锚点异常不会连累其它补丁（F-07 的"一损俱损"）。
    """
    edits, done, missing = [], [], []
    for spec in PATCHES:
        if spec["applied"](data):
            done.append(spec["name"])
            continue
        pairs, err = _probe_patch(spec, data)
        if not pairs:
            missing.append((spec["name"], err or "锚点缺失（本构建代码形态变了）"))
            continue
        ok = True
        local = []
        for old, new in pairs:
            if len(old) != len(new):
                err = "替换不等长：%d → %d" % (len(old), len(new))
                ok = False
                break
            hit = data.count(old)
            if hit != 1:
                err = "锚点命中 %d 次（应为 1）：%r" % (hit, old[:60])
                ok = False
                break
            local.append((data.find(old), old, new))
        if not ok:
            missing.append((spec["name"], err))
            continue
        edits.extend(local)
        done.append(spec["name"])
    missing_core = [(n, r) for n, r in missing if _spec(n)["core"]]
    missing_aux = [(n, r) for n, r in missing if not _spec(n)["core"]]
    return edits, done, missing_core, missing_aux


def _spec(name):
    return next(s for s in PATCHES if s["name"] == name)


def patch_states(data):
    """逐条状态：patched / raw / skipped。

    skipped = 本构建里连锚点都没有（aux 补丁被降级跳过），与"能打但还没打"的
    raw 区分开。core 补丁缺锚点时报 `missing`（沿用 deploy.ps1 已在匹配的
    `raw|missing` 判据，不会因为改名 skipped 而漏判成通过）。
    """
    out = []
    for spec in PATCHES:
        if spec["applied"](data):
            out.append((spec["name"], "patched"))
            continue
        pairs, _err = _probe_patch(spec, data)
        if pairs:
            out.append((spec["name"], "raw"))
        else:
            out.append((spec["name"], "missing" if spec["core"] else "skipped"))
    return out


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


# WinError 语义（P3）
# ----------------
# 5  (ERROR_ACCESS_DENIED)      = 权限不够 → 需要管理员，exit 3
# 32 (ERROR_SHARING_VIOLATION)  = 文件被别的进程独占/镜像锁定 → 应用在运行，exit 4
# 33 (ERROR_LOCK_VIOLATION)     = 区域锁冲突 → 同上，按"在运行"处理
# 其余（杀软/EDR 偶发 1/87/… ）保守当作"被占用"，宁可让用户去关应用。
WINERROR_ACCESS_DENIED = 5
WINERROR_SHARING_VIOLATION = 32
WINERROR_LOCK_VIOLATION = 33
# Python 的 io 层在 Windows 上常常只给 errno=13、不给 winerror（实测 conda 3.12：
# `open(...,"r+b")` 被拒时 `e.winerror` 是 None）。所以还要走一次 CreateFileW，
# 直接拿系统错误码，不能只靠异常对象。
_ACCESS_DENIED_ERRNOS = {13, 1}  # EACCES, EPERM


def probe_open_error(path, write=True):
    """试开一次目标文件，返回 Windows 错误码（0 = 可写）。

    Python 的 `open()` 把 WinError 5 和 32 都折叠成 `PermissionError(13)`，
    光看异常分不出「要提权」还是「应用在跑」。这里用 CreateFileW 拿原始
    GetLastError：这是唯一能可靠区分两者的地方（且不写任何字节——
    dwDesiredAccess=GENERIC_WRITE 但 CreateFileW 本身不写内容）。
    """
    if os.name != "nt":
        try:
            with open(path, "r+b" if write else "rb") as f:
                f.seek(0)
            return 0
        except PermissionError:
            return WINERROR_ACCESS_DENIED
        except OSError:
            return WINERROR_SHARING_VIOLATION
    try:
        import ctypes
        from ctypes import wintypes
    except Exception:
        return WINERROR_SHARING_VIOLATION  # 判断不了就保守当占用
    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    create = k32.CreateFileW
    create.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                       wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    create.restype = wintypes.HANDLE
    access = 0x40000000 if write else 0x80000000  # GENERIC_WRITE / GENERIC_READ
    share = 0 if write else 3                     # 写探测不共享：能暴露真实占用
    # OPEN_EXISTING=3, FILE_ATTRIBUTE_NORMAL=0x80
    handle = create(path, access, share, None, 3, 0x80, None)
    if handle in (None, 0, ctypes.c_void_p(-1).value):
        return ctypes.get_last_error() or k32.GetLastError() or WINERROR_SHARING_VIOLATION
    k32.CloseHandle(handle)
    return 0


def write_probe(path):
    """返回 (blocked, reason)：reason ∈ {"locked", "denied", "error:…", None}。

    locked → 应用在运行（exit 4）；denied → 需要管理员（exit 3）。
    """
    if not os.path.isfile(path):
        return False, None
    winerror = probe_open_error(path, write=True)
    if winerror == 0:
        return False, None
    if winerror in (WINERROR_SHARING_VIOLATION, WINERROR_LOCK_VIOLATION):
        return True, "locked"
    if winerror == WINERROR_ACCESS_DENIED:
        # 只读属性 / ACL 拒绝：不是"应用在跑"，而是权限问题。
        return True, "denied"
    # 其它错误码：保守当作被占用（可能是 EDR 拦截），但仍把原码打出来。
    return True, "error:%d" % winerror


def exe_write_blocked(exe_path):
    """能不能写 exe。运行中的 exe 是镜像映射，写会被拒绝；直接试一次比查进程名可靠
    （复制到别处的副本同名但不锁，按名字判断会误报）。

    注意：返回 True 只表示"写不了"，不表示"应用在运行"——调用方必须用
    `exe_write_denied()` / `write_probe()` 区分 exit 4 与 exit 3。
    """
    blocked, _reason = write_probe(exe_path)
    return blocked


def exe_write_denied(path):
    """True = 写被拒是权限问题（需要管理员），而不是应用占用。"""
    blocked, reason = write_probe(path)
    return blocked and reason == "denied"


def launch_app(exe_path):
    """把应用启动起来（**只启动，从不结束任何进程**）。

    经 `explorer.exe` 拉起：本脚本通常是提权跑的，直接 Popen 会让应用继承管理员令牌；
    资源管理器代启才是普通用户权限（deploy.ps1 早先也是这么做的）。
    """
    import subprocess
    exe_path = os.path.abspath(exe_path)
    if os.name == "nt":
        subprocess.Popen(["explorer.exe", exe_path], close_fds=True)
    else:
        subprocess.Popen([exe_path], cwd=os.path.dirname(exe_path), close_fds=True)


def maybe_launch(exe_path, enabled=True):
    """写完之后把应用开回来。exe 不存在（比如在副本目录里排障）就什么都不做。"""
    if not enabled:
        print("启动：跳过（--no-launch）")
        return False
    if not exe_path or not os.path.isfile(exe_path):
        print("启动：跳过（找不到 %s）" % (exe_path or "exe"))
        return False
    try:
        launch_app(exe_path)
        print("已启动：%s" % exe_path)
        print("   （只启动，不结束任何进程；应用起来后才读到新 asar）")
        return True
    except Exception as e:
        print("!! 启动失败：%s" % e)
        print("   请手动打开 %s" % exe_path)
        return False


def file_locked(path):
    """asar 能不能写（运行中的 asar 是被镜像映射的）。

    返回 True 只表示"写不了"；要区分"应用在运行"与"需要管理员"，
    用 `write_denied(path)`（P3）。
    """
    blocked, _reason = write_probe(path)
    return blocked


def write_denied(path):
    """True = 写不进去的原因是权限不足（WinError 5），不是应用占用。"""
    blocked, reason = write_probe(path)
    return blocked and reason == "denied"


def write_block_reason(path):
    """给错误信息用：None / "locked" / "denied" / "error:<n>"。"""
    return write_probe(path)[1]


PROC_SCRIPT = (r"Get-CimInstance Win32_Process | "
               r"Where-Object { $_.Name -match 'WorkBuddy|codebuddy' } | "
               r"ForEach-Object { '{0}|{1}|{2}' -f $_.ProcessId,$_.Name,$_.ExecutablePath }")


def running_workbuddy():
    """运行中的 WorkBuddy* / codebuddy 进程：[(pid, name, exe_path), …]。

    WinError 32 的直接原因就是这些进程把 app.asar / exe 镜像映射住了。
    输出里必须带进程名与 PID，用户才知道该关哪个（脚本从不结束任何进程）。
    """
    if os.name != "nt":
        return []
    try:
        p = subprocess.run(["powershell", "-NoProfile", "-Command", PROC_SCRIPT],
                           capture_output=True, text=True, encoding="mbcs",
                           errors="replace", timeout=60)
        out = p.stdout or ""
    except Exception:
        return []
    rows = []
    for line in out.splitlines():
        parts = line.strip().split("|", 2)
        if len(parts) < 2 or not parts[0].isdigit():
            continue
        rows.append((int(parts[0]), parts[1], parts[2] if len(parts) > 2 else ""))
    return rows


def report_running(procs, action):
    """打印占用者清单；返回 True 表示有进程在跑。"""
    if not procs:
        return False
    print("!! 检测到 %d 个正在运行的 WorkBuddy / codebuddy 进程（%s 需要它们完全退出）：" % (len(procs), action))
    for pid, name, exe in procs:
        print("   pid=%-7d %-18s %s" % (pid, name, exe or "(路径不可读)"))
    print("   这些进程把 app.asar / exe 镜像映射住（WinError 32）。脚本不会结束它们；")
    print("   请完全退出 WorkBuddy（含托盘图标）后重跑。")
    return True


def exe_manifest_hashes(exe_path):
    with open_for_read(exe_path) as f:
        blob = f.read()
    return [m.group(1).decode("ascii") for m in APP_ASAR_ENTRY_RE.finditer(blob)]


class TargetBusy(Exception):
    """读目标文件时它被独占打开（WinError 32/33）。运行中的 exe 就是这种状态。

    单独建异常类型，是为了让上层能把它翻译成 exit 4（应用在运行），而不是
    exit 1（未分类失败）。之前 `open(exe,"rb")` 在独占锁下抛 PermissionError，
    被 `except Exception` 收进"exe 清单处理失败 → exit 1"，用户既看不到
    "正在运行"也拿不到正确退出码。
    """


class TargetDenied(Exception):
    """读目标文件时被拒绝（WinError 5）。需要管理员，对应 exit 3。"""


def open_for_read(path):
    """读目标文件的统一入口：把 OS 错误翻译成 TargetBusy / TargetDenied。"""
    try:
        return open(path, "rb")
    except (PermissionError, OSError) as e:
        winerror = getattr(e, "winerror", None)
        if winerror is None:
            winerror = probe_open_error(path, write=False)
        if winerror in (WINERROR_SHARING_VIOLATION, WINERROR_LOCK_VIOLATION):
            raise TargetBusy(str(e))
        if winerror == WINERROR_ACCESS_DENIED:
            raise TargetDenied(str(e))
        raise


def plan_exe_manifest_sync(exe_path, desired_hash):
    """把 exe 清单里 resources\\app.asar 的 value 同步到 desired_hash。

    返回 (edits, total_entries)。edits 为 [(abs_offset, old_bytes, new_bytes), …]。
    读不到时抛 TargetBusy / TargetDenied（见上），由调用方翻译成 exit 4 / 3。
    """
    with open_for_read(exe_path) as f:
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


def revert_written(path, edits):
    """只回退**确实已经落盘**的编辑，返回 (reverted, failed)。

    为什么不是一个 `write_edits(path, 逆编辑)`：写盘可能在中间失败（磁盘满、
    杀软中断、句柄被撤），此时只有前几处落地了。整份逆编辑会去写那些本来
    就没改过的位置，既扩大风险面，也可能在"写不了"的同一原因下再次失败，
    于是回滚看起来失败、其实早先那几处已经改坏了。

    逐条检查"这个位置现在是不是新值"：是才写回旧值。没写过的一律不碰。
    """
    if not edits:
        return 0, 0
    reverted = failed = 0
    try:
        fh = open(path, "r+b")
    except OSError:
        return 0, len(edits)
    with fh:
        for off, old, new in edits:
            try:
                fh.seek(off)
                cur = fh.read(len(new))
                if cur != new:      # 没写进去 / 已被别人改回：不动它
                    continue
                fh.seek(off)
                fh.write(old)
                reverted += 1
            except OSError as e:
                failed += 1
                print("!! 回退 offset %d 失败：%s" % (off, e))
        try:
            fh.flush()
            os.fsync(fh.fileno())
        except OSError:
            pass
    return reverted, failed


def check_integrity(asar, abs_offset=None, rel_path=None):
    """重新解析落盘后的 asar，断言 entry integrity 与实际内容一致。

    两种定位方式：
      * `abs_offset` —— 承载该绝对偏移的文件（写完后按 site 校验）；
      * `rel_path`   —— asar 内的 entry 路径（`--status` 按补丁声明的 targets
        校验，连 renderer bundle 也能覆盖，P8）。

    返回 (ok, msg)：ok 为 True/False/None（None = 该 entry 没有 integrity，跳过）。
    """
    with open(asar, "rb") as f:
        head = f.read(16)
        pickle_size = struct.unpack("<I", head[4:8])[0]
        f.seek(0)
        blob = f.read(8 + pickle_size)
        info = parse_header(blob)
        if rel_path is not None:
            rel, entry = find_entry_by_path(info["header"], rel_path)
            if entry is None:
                return None, "%s 不在 asar 里" % rel_path
        else:
            rel, entry = find_entry(info["header"], info["data_off"], abs_offset)
            if not entry:
                return None, "没定位到承载文件"
        if entry.get("unpacked") or entry.get("offset") is None:
            # asar.unpacked 里的文件没有 offset/integrity，无从校验（也不是本脚本改的）
            return None, "%s 是 unpacked entry，跳过" % rel
        integ = entry.get("integrity")
        if not integ:
            return None, "%s 没有 integrity 字段" % rel
        _start, _end, obj = find_integrity_span(blob, info, entry)
        size = int(entry.get("size", 0))
        f.seek(info["data_off"] + int(entry["offset"]))
        content = f.read(size)
    block_size = int(integ.get("blockSize") or DEFAULT_BLOCK_SIZE)
    calc_hash = sha256_bytes(content)
    calc_blocks = compute_block_hashes(content, block_size)
    ok = calc_hash == obj.get("hash") and calc_blocks == list(obj.get("blocks") or [])
    return ok, "%s hash=%s… blocks=%d" % (rel, calc_hash[:16], len(calc_blocks))


def find_entry_by_path(header, rel_path):
    """按 asar 内路径找 entry。路径统一用 `/` 开头的形式（与 walk_entries 一致）。"""
    want = rel_path if rel_path.startswith("/") else "/" + rel_path
    for rel, entry in walk_entries(header):
        if rel == want:
            return rel, entry
    return None, None


def match_entries(header, pattern, markers=None, asar=None):
    """按 fnmatch 通配找 entry 路径。

    P8 的现实约束：scene-chip 改的 renderer bundle 叫 `common-<hash>.js`，哈希
    随构建变，只能用通配；但 `common-*.js` 一次匹配到 7 个文件，只有 1 个含场景
    代码。所以再按 `markers`（内容标记）过滤——通配负责"文件名形态"，marker
    负责"确实是那个 bundle"。

    `markers` 是**任一命中即可**的列表：补丁前后 bundle 的内容不同（整段被换成
    注释），所以 raw 形态与 patched 形态的标记都要给，否则打完补丁就找不到自己
    改过的文件了。
    """
    want = pattern if pattern.startswith("/") else "/" + pattern
    if markers is None:
        markers = []
    hits = []
    for rel, entry in walk_entries(header):
        if not fnmatch.fnmatchcase(rel, want):
            continue
        if markers:
            if entry.get("unpacked") or entry.get("offset") is None or asar is None:
                continue
            content = read_entry_bytes(asar, header, entry)
            if not any(m in content for m in markers):
                continue
        hits.append(rel)
    return hits


def read_entry_bytes(asar, header, entry):
    """读出一个 entry 的原始内容（只在按 marker 过滤时用，单次 open）。"""
    data_off = None
    with open(asar, "rb") as f:
        head = f.read(16)
        pickle_size = struct.unpack("<I", head[4:8])[0]
        data_off = 8 + pickle_size
        f.seek(data_off + int(entry["offset"]))
        return f.read(int(entry.get("size", 0)))


def integrity_report(asar, header, patterns, seen=None):
    """对一组 entry 通配跑 integrity 校验，返回 [(pattern, rel, ok, msg)]。

    `ok is False` 才算失败；True = 匹配；None = 没 integrity 字段 / unpacked /
    该构建没有匹配的 entry（这三种都不是"坏"，由调用方按补丁状态决定要不要提示）。

    `seen` 可跨调用共享，避免多条补丁重复校验同一个 entry。
    """
    out = []
    seen = set() if seen is None else seen
    for pat in patterns:
        if not pat:
            continue
        # 形态：字符串，或 (glob, [marker, …])
        if isinstance(pat, tuple):
            glob_pat, markers = pat
        else:
            glob_pat, markers = pat, None
        hits = match_entries(header, glob_pat, markers=markers, asar=asar)
        if not hits:
            out.append((glob_pat, glob_pat, None, "该构建没有匹配的 entry"))
            continue
        for rel in hits:
            if rel in seen:
                continue
            seen.add(rel)
            ok, msg = check_integrity(asar, rel_path=rel)
            out.append((pat, rel, ok, msg))
    return out


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


BACKUP_SUFFIX = "-asar-current-time"
BACKUP_TMP_PREFIX = "tmp-"
BACKUP_FAILED_PREFIX = "failed-"


def now_stamp():
    """备份时间戳：本地时间 ISO 秒 + 微秒。

    只用秒的话，同一秒内连打两次（自测 / 部署脚本连跑两个 target）会得到
    同一个 appliedAt，`latest_backup()` 的排序就退化成看目录名，可能挑错
    那一层。带微秒后"谁更晚"是确定的。

    用 time_ns() 而不是 `time.time()*1e6`：后者与 strftime 各自取一次系统
    时间，跨秒边界时秒部分与微秒部分会来自不同的时刻。
    """
    ns = time.time_ns()
    secs, rem = divmod(ns, 1_000_000_000)
    return (time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(secs))
            + (".%06d" % (rem // 1000)))
# 明确的写入点：备份目录在这里改名/落 meta。自测可以换成临时目录。
BACKUP_DIRNAME_RE = re.compile(r"^\d{8}-\d{6}" + re.escape(BACKUP_SUFFIX) + r"$")


def backup_dir(asar_path, stamp=None):
    """写盘前的**临时**备份目录（P4）。

    以前这里直接建正式目录并写 meta，写盘失败/回滚后脏备份会留在 backups/ 里，
    被 `latest_backup()` 选中（restore 会拿一个根本没生效的快照去还原）。
    现在先落在 `tmp-<stamp>-asar-current-time`，只有写盘全部成功、回读 /
    integrity / exe 清单 / 语法校验都通过之后才 `commit_backup()` 改名为正式名；
    失败/回滚时 `discard_backup()` 删掉临时目录。
    """
    if stamp is None:
        stamp = time.strftime("%Y%m%d-%H%M%S")
    d = os.path.join(BACKUP_ROOT, "%s%s%s" % (BACKUP_TMP_PREFIX, stamp, BACKUP_SUFFIX))
    os.makedirs(d, exist_ok=True)
    return d


def is_formal_backup_name(name):
    """正式备份名：`YYYYMMDD-HHMMSS-asar-current-time`。

    `tmp-` / `failed-` 前缀与任何手工目录都不算——`latest_backup()` 只认正式的。
    """
    if not name.endswith(BACKUP_SUFFIX):
        return False
    if name.startswith(BACKUP_TMP_PREFIX) or name.startswith(BACKUP_FAILED_PREFIX):
        return False
    return bool(BACKUP_DIRNAME_RE.match(name))


def commit_backup(tmp_dir, meta=None):
    """把临时备份目录转正（P4 的"提交"）。

    先写 meta.json（含 shaAfter），再 os.replace 改名；改名是同一卷上的原子
    操作，所以 `latest_backup()` 永远看不到"半个"正式备份。返回正式目录路径。
    目标名已存在时加 -2/-3 后缀，绝不覆盖已有备份。
    """
    tmp_dir = os.path.abspath(tmp_dir)
    base = os.path.basename(tmp_dir)
    if base.startswith(BACKUP_TMP_PREFIX):
        base = base[len(BACKUP_TMP_PREFIX):]
    final = os.path.join(BACKUP_ROOT, base)
    n = 1
    while os.path.exists(final):
        n += 1
        final = os.path.join(BACKUP_ROOT, "%s-%d" % (base, n))
    if meta is not None:
        with open(os.path.join(tmp_dir, "meta.json"), "w", encoding="utf-8") as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    os.replace(tmp_dir, final)
    return final


def discard_backup(tmp_dir):
    """失败/回滚时删掉临时备份目录。删不掉也不抛——只是留个 tmp- 目录，
    而 tmp- 永远不会被 `latest_backup()` 选中。"""
    if not tmp_dir:
        return
    if not os.path.basename(os.path.normpath(tmp_dir)).startswith(BACKUP_TMP_PREFIX):
        return  # 只删自己建的临时目录，正式备份一个都不碰
    try:
        shutil.rmtree(tmp_dir, ignore_errors=True)
    except Exception:
        pass


def _backup_meta(d):
    meta = os.path.join(d, "meta.json")
    if not os.path.isfile(meta):
        return None
    try:
        with open(meta, encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return None


def latest_backup(asar_path, current_sha=None):
    """挑 restore 用的备份（P5：多次增量打补丁的备份链）。

    规则（按优先级）：
      1. `shaAfter` 与**当前 asar** 的 sha256 相同的**最新**备份 —— 这份备份正是
         在"当前这个文件状态"上取下来的，它记录的 sites 偏移与 entry 内容对当前
         文件有效，回滚它等于撤销最近一次改动。多次增量打补丁后，每层都有一份
         这样的备份，恢复是逐层往回退，绝不会拿一份 offsets 已经错位的快照去写。
      2. 没有匹配（应用升级过、asar 被替换、或只读运行查不到 sha）时，取**最旧**
         的正式备份 —— 那是这条链上最初的原始 asar，用来兜底回原始版。
      3. 任何 `tmp-` / `failed-` 前缀、缺 meta.json、meta 损坏的目录一律跳过。

    为什么用 shaAfter 而不是 shaBefore：shaBefore 匹配意味着"这份备份就是从这个
    状态开始打的"，此时回滚它是把 asar 写回它自己，纯 no-op；而且 restored 之后
    sha 仍然等于 shaBefore，再跑一次 restore 还会选它，永远退不回去。shaAfter
    匹配才能保证"每一步都往更早的状态走一步"。
    """
    if not os.path.isdir(BACKUP_ROOT):
        return None
    rows = []
    for name in os.listdir(BACKUP_ROOT):
        if not is_formal_backup_name(name):
            continue
        d = os.path.join(BACKUP_ROOT, name)
        if not os.path.isdir(d):
            continue
        m = _backup_meta(d)
        if not m:
            continue
        if os.path.normcase(m.get("asar", "")) != os.path.normcase(asar_path):
            continue
        rows.append((m.get("appliedAt", ""), name,
                     str(m.get("shaAfter") or "").lower(), d))
    if not rows:
        return None
    rows.sort(key=lambda r: (r[0], r[1]))
    if current_sha is None:
        try:
            current_sha = sha256_file(asar_path)
        except OSError:
            current_sha = ""
    current_sha = (current_sha or "").lower()
    if current_sha:
        matches = [r for r in rows if r[2] and r[2] == current_sha]
        if matches:
            return matches[-1][3]          # 规则 1：与当前 sha 匹配的最新一份
    return rows[0][3]                      # 规则 2：最旧（最初的原始备份）


def backup_lineage(asar_path):
    """诊断用：按时间列出这条备份链（--status 打印，便于人工确认 restore 会选谁）。"""
    if not os.path.isdir(BACKUP_ROOT):
        return []
    rows = []
    for name in sorted(os.listdir(BACKUP_ROOT)):
        if not is_formal_backup_name(name):
            continue
        d = os.path.join(BACKUP_ROOT, name)
        m = _backup_meta(d)
        if not m:
            continue
        if os.path.normcase(m.get("asar", "")) != os.path.normcase(asar_path):
            continue
        rows.append((m.get("appliedAt", ""), name,
                     str(m.get("shaBefore") or "")[:16], str(m.get("shaAfter") or "")[:16],
                     str(m.get("shaAfter") or "").lower()))
    return rows


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


def _force_utf8_stdio():
    """P9：输出统一走 UTF-8。

    Windows 上 Python 的 stdout 默认跟控制台代码页（cp936）走，经 PowerShell
    管道重定向时中文会被按错误编码解码成乱码。deploy.ps1 已经设了
    PYTHONIOENCODING=utf-8，但手工运行时没有，所以脚本自己兜底。
    reconfigure 失败（比如被替换成 StringIO）就静默跳过，不影响功能。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass


def main():
    _force_utf8_stdio()
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
    ap.add_argument("--no-launch", action="store_true",
                    help="写完不要启动应用（默认会启动；本脚本从不结束任何进程）")
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
        procs = running_workbuddy()
        if procs:
            print("running: %s" % ", ".join("%s(pid=%d)" % (n, p) for p, n, _e in procs))
        states = patch_states(data)
        for name, s in states:
            print("patch:  %-17s %s" % (name, s))
        print("sha256: %s" % sha256_bytes(data))

        # ---- P8：按补丁声明的 targets 校验 entry integrity ----
        # 旧版只看 current_time 那个 entry；scene-chip 改的是 renderer 的
        # common-*.js，它坏了 --status 也照样绿。
        #
        # 输出契约（deploy.ps1 / doctor.py 只认这一行）：
        #   `integ: MATCH` 或 `integ: MISMATCH (…)` —— 单行、值以 MATCH 开头。
        # 逐补丁明细放 `entry:` 行（两个消费者都不解析），失败时另打 `warn:`。
        problems = []
        unchecked = []
        checked = 0
        handled = set()
        state_of = dict(states)
        # entry -> 声明它的补丁（多条补丁共用同一个 bundle，展示时合并）
        owners = {}
        for spec in PATCHES:
            patterns = spec.get("targets") or []
            if not patterns:
                continue
            state = state_of.get(spec["name"])
            for _pat, rel, ok, msg in integrity_report(asar, info["header"], patterns, seen=handled):
                if not rel.startswith("/"):
                    # 通配没匹配到任何 entry：补丁已 patched 才值得提示
                    if state == "patched":
                        unchecked.append(spec["name"])
                    continue
                owners.setdefault(rel, []).append(spec["name"])
                if ok is None:
                    # unpacked / 没 integrity 字段：不是"坏"，只是无从校验
                    continue
                checked += 1
                if not ok:
                    problems.append("%s %s" % (spec["name"], rel))
                    print("warn:   %s 的 entry integrity 不匹配：%s" % (spec["name"], msg))
        for rel, names in owners.items():
            ok, msg = check_integrity(asar, rel_path=rel)
            if ok is None:
                print("entry:  %-52s integrity=%-10s %s" % (rel, "跳过", ", ".join(names)))
            else:
                print("entry:  %-52s integrity=%-10s %s"
                      % (rel, "MATCH" if ok else "MISMATCH", ", ".join(names)))
        if unchecked:
            print("warn:   %s 报 patched，但本构建没有匹配 targets 的 entry"
                  % ", ".join(unchecked))
        # current-time 的目标段按 raw/patched 两种形态动态定位，不在 spec 里写死
        target = locate_target(data)
        if target >= 0:
            try:
                rel, entry = find_entry(info["header"], info["data_off"], target)
                if entry and rel not in owners:
                    content_start = info["data_off"] + int(entry["offset"])
                    content = bytes(data[content_start:content_start + int(entry["size"])])
                    stored = (entry.get("integrity") or {}).get("hash")
                    good = stored == sha256_bytes(content)
                    checked += 1
                    print("entry:  %-52s integrity=%-10s %s" % (rel, "MATCH" if good else "MISMATCH", "current-time"))
                    if not good:
                        problems.append("current-time %s" % rel)
            except Exception as e:
                print("entry:  current-time 承载文件无法检查（%s）" % e)
        if problems:
            print("integ:  MISMATCH (%s)" % "; ".join(problems))
        elif checked:
            print("integ:  MATCH")
        else:
            print("integ:  跳过（该 asar 没有可校验的 integrity entry）")

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
        # 备份链（只读）：restore 会按 sha 匹配挑一份，人工核对时用得到
        lineage = backup_lineage(asar)
        if lineage:
            print("backups: %d 份正式备份" % len(lineage))
            chosen = latest_backup(asar, current_sha=sha256_bytes(data))
            for row in lineage[-5:]:
                name, before, after = row[1], row[2], row[3]
                print("  %s%s  %s -> %s"
                      % (name, "  <- restore 会选它" if chosen and name in chosen else "",
                         before, after))
        return 0

    if args.restore:
        d = latest_backup(asar, current_sha=sha256_bytes(data))
        if not d:
            print("没有找到 %s 的备份" % asar)
            return 2
        print("backup:    %s" % d)
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
            except TargetBusy as e:
                print("!! WorkBuddy.exe 正在被使用（WorkBuddy 在运行）：%s" % e)
                report_running(running_workbuddy(), "asar 还原")
                print("   还原未执行（未改任何字节）。请完全退出 WorkBuddy 后重跑 scripts\\restore.cmd。")
                return 4
            except TargetDenied as e:
                print("!! WorkBuddy.exe 读取被拒绝（需要管理员权限）：%s" % e)
                print("   还原未执行（未改任何字节）。请以管理员身份重跑 scripts\\restore.cmd。")
                return 3
            except Exception as e:
                print("!! 还原 exe 清单失败：%s" % e)
                return 1
        else:
            n_entries = 0

        if not asar_edits and not exe_edits:
            print("已经是原始状态，无需还原")
            return 0

        if exe_edits and exe_path:
            # P3：先分清"应用在跑"（exit 4）与"没提权"（exit 3）
            reason = write_block_reason(asar) or write_block_reason(exe_path)
            if reason == "denied":
                print("!! 写入被拒绝（需要管理员）：请以管理员身份重跑 scripts\\restore.cmd。")
                print("   还原未执行（未改任何字节）。")
                return 3
            if reason:
                print("!! WorkBuddy 正在运行，无法写 WorkBuddy.exe / app.asar；还原未执行（未改任何字节）。")
                report_running(running_workbuddy(), "asar 还原")
                print("   请完全退出 WorkBuddy（含托盘）后重跑 scripts\\restore.cmd。")
                return 4

        # P1 同类问题：restore 也是"asar 改了、exe 没改"就变砖，必须同事务。
        def _restore_rollback(reason_text):
            print("!! %s，回滚 restore" % reason_text)
            try:
                if exe_edits:
                    write_edits(exe_path, [(off, new, old) for off, old, new in exe_edits])
                if asar_edits:
                    write_edits(asar, [(off, new, old) for off, old, new in asar_edits])
            except Exception as e2:
                print("!! 回滚写入失败：%s" % e2)
                return False
            try:
                back = sha256_file(asar)
            except OSError:
                return False
            print("   回滚后 asar sha256 %s" % back[:16])
            return True

        try:
            if asar_edits:
                write_edits(asar, asar_edits)
        except (PermissionError, OSError) as e:
            print("!! asar 写入失败：%s" % e)
            if not read_back(asar, asar_edits):
                _restore_rollback("asar 写入中断")
            print("   已尝试清理半写状态；若上一行不是 OK 请手工用备份还原。")
            return 3
        if exe_edits:
            try:
                write_edits(exe_path, exe_edits)
            except Exception as e:
                print("!! exe 写入失败：%s" % e)
                _restore_rollback("exe 写入失败（权限 / 被杀软拦截 / 被锁）")
                print("   已回滚 asar，避免 integrity 失配。")
                return 3
        if asar_edits and not read_back(asar, asar_edits):
            print("!! asar 回读不一致")
            return 1
        if exe_edits and not read_back(exe_path, exe_edits):
            _restore_rollback("exe 回读不一致")
            print("!! exe 回读不一致（已回滚）")
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
        if ok and exe_ok:
            maybe_launch(exe_path or exe, not args.no_launch)
        return 0 if (ok and exe_ok) else 1

    # ---- apply / dry-run ----
    # P6 分级：core 缺锚点 = 整体拒绝（exit 2，一个字节不写）；
    #          aux  缺锚点 = 只跳过它，其余照常写入（exit 0 + WARN）。
    content_edits, done, missing_core, missing_aux = collect_content_edits(data)

    if missing_core:
        print("!! core 补丁的锚点在本版本里找不到：%s" % ", ".join(n for n, _r in missing_core))
        print("   可能 WorkBuddy 升级改写了对应代码；本次不写入任何改动。")
        for name, reason in missing_core:
            print("   - %-17s %s" % (name, _spec(name)["title"]))
            print("     reason: %s" % reason)
        return 2

    if missing_aux:
        print("WARN: %d 条补丁未应用（锚点失配）：" % len(missing_aux))
        for name, reason in missing_aux:
            print("WARN:   - %-17s %s" % (name, _spec(name)["title"]))
            print("WARN:     reason: %s" % reason)
        print("WARN: core 补丁与其余 aux 照常应用；下面 --status 会把这些标成 skipped。")

    if not content_edits:
        # 内容补丁全部已在位；可能只是 exe 清单没同步（旧版脚本只改了头）
        hh = header_sha(data, info)
        print("already patched: %s" % ", ".join(done))
        print("header:  %s" % hh)
        if missing_aux:
            print("WARN: %d 条补丁未应用（锚点失配），其余已在位。" % len(missing_aux))
        if not exe:
            print("exe:     跳过（--no-exe）")
            return 0
        try:
            exe_edits, n_entries = plan_exe_manifest_sync(exe, hh)
        except TargetBusy as e:
            print("!! WorkBuddy.exe 正在被使用（WorkBuddy 在运行）：%s" % e)
            report_running(running_workbuddy(), "exe 清单同步")
            print("   请完全退出 WorkBuddy（含托盘）后重跑 scripts\\deploy.cmd -Asar。")
            return 4
        except TargetDenied as e:
            print("!! WorkBuddy.exe 读取被拒绝（需要管理员权限）：%s" % e)
            print("   请以管理员身份重跑 scripts\\deploy.cmd -Asar。")
            return 3
        except Exception as e:
            print("!! exe 清单处理失败：%s" % e)
            return 1
        if not exe_edits:
            print("exe sync: MATCH（%d 处清单已是当前头哈希）" % n_entries)
            return 0
        # P3：先分清 exit 4（应用在跑）与 exit 3（需要管理员）
        reason = write_block_reason(asar) or write_block_reason(exe)
        if reason == "denied":
            print("!! WorkBuddy.exe 写入被拒绝（需要管理员权限）：%s" % exe)
            print("   本次未写入任何改动。请以管理员身份重跑 scripts\\deploy.cmd -Asar。")
            return 3
        if reason:
            print("!! WorkBuddy.exe 写不进去（WorkBuddy 正在运行，exe / asar 被镜像锁定）。")
            report_running(running_workbuddy(), "exe 清单同步")
            print("   请完全退出 WorkBuddy（含托盘）后重跑 scripts\\deploy.cmd -Asar。")
            return 4
        old_vals = exe_manifest_hashes(exe)
        exe_sha_before = sha256_file(exe)
        try:
            write_edits(exe, exe_edits)
        except (PermissionError, OSError) as e:
            print("写入被拒绝（需要管理员）: %s" % e)
            return 3
        if not read_back(exe, exe_edits):
            # P2：这里以前写的是 `[(o, n, o) for o, o, n in exe_edits]`——
            # 三个变量名只解出两个真实值，触发即 TypeError。对齐 rollback() 的写法。
            try:
                write_edits(exe, [(off, new, old) for off, old, new in exe_edits])
                print("!! exe 回读不一致，已回滚")
            except Exception as e2:
                print("!! exe 回读不一致，且回滚失败：%s" % e2)
            return 1
        # 只补 exe 清单也要留备份链：新建一份临时备份，成功后才转正（P4）
        d = backup_dir(asar)
        tmp_owner = d
        meta = {"asar": asar, "offset": 0, "length": 0, "shaBefore": sha256_file(asar),
                "appliedAt": now_stamp(),
                "note": "只同步了 exe 清单（asar 内容未变）"}
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
        try:
            final = commit_backup(tmp_owner, meta)
        except Exception as e:
            discard_backup(tmp_owner)
            print("!! 备份提交失败：%s" % e)
            return 1
        print("exe sync: %d 处清单已更新 -> %s" % (len(exe_edits), hh[:16]))
        print("exe sha256: %s -> %s" % (exe_sha_before[:16], meta["exe"]["shaAfter"][:16]))
        print("backup:    %s" % final)
        if missing_aux:
            print("WARN: %d 条补丁未应用（锚点失配）：%s"
                  % (len(missing_aux), ", ".join(n for n, _r in missing_aux)))
        maybe_launch(exe, not args.no_launch)
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
        except TargetBusy as e:
            print("!! WorkBuddy.exe 正在被使用（WorkBuddy 在运行），读不到清单：%s" % e)
            report_running(running_workbuddy(), "asar 写入")
            print("   本次未写入任何改动（asar 也未改）。请完全退出 WorkBuddy（含托盘）后重跑：")
            print("   scripts\\deploy.cmd -Asar")
            return 4
        except TargetDenied as e:
            print("!! WorkBuddy.exe 读取被拒绝（需要管理员权限）：%s" % e)
            print("   本次未写入任何改动（asar 也未改）。请以管理员身份重跑 scripts\\deploy.cmd -Asar。")
            return 3
        except Exception as e:
            print("!! exe 清单处理失败：%s" % e)
            return 1
        print("exe:       %s  (%d 处 app.asar 清单，%d 处需更新)" % (exe, n_entries, len(exe_edits)))
    else:
        print("exe:       跳过（--no-exe）—— 应用启动会被整头校验拦住")

    if args.dry_run:
        reason = write_block_reason(asar) or (write_block_reason(exe) if exe else None)
        if reason == "denied":
            print("[dry-run] 注意：当前未提权，实跑会以退出码 3 拒绝写入（需要管理员）")
        elif reason:
            print("[dry-run] 注意：asar / exe 当前被占用（WorkBuddy 可能正在运行），实跑会以退出码 4 拒绝写入")
            report_running(running_workbuddy(), "asar 写入")
        print("[dry-run] 不写文件")
        if missing_aux:
            print("[dry-run] WARN: %d 条补丁未应用（锚点失配）：%s"
                  % (len(missing_aux), ", ".join(n for n, _r in missing_aux)))
        return 0

    # P3：先分清"应用在跑"（WinError 32 → exit 4）与"没提权"（WinError 5 → exit 3）。
    # 以前一律报"WorkBuddy 正在运行"，非管理员的拒绝访问被误报成进程占用。
    procs = running_workbuddy()
    blocked_asar = file_locked(asar)
    blocked_exe = bool(exe and exe_edits and exe_write_blocked(exe))
    if blocked_asar or blocked_exe:
        denied = (write_denied(asar) if blocked_asar else False) or \
                 (write_denied(exe) if blocked_exe else False)
        if denied:
            print("!! 写入被拒绝（需要管理员权限），不是应用占用：")
            for p, b in ((asar, blocked_asar), (exe, blocked_exe)):
                if b:
                    print("   %s" % p)
            print("   本次未写入任何改动（asar 也未改）。请以管理员身份重跑：")
            print("   scripts\\deploy.cmd -Asar")
            return 3
        print("!! WorkBuddy.exe / app.asar 写不进去（WorkBuddy 正在运行，文件被镜像锁定）。")
        if procs:
            report_running(procs, "asar 写入")
        else:
            print("   （没看到 WorkBuddy 进程，但文件确实被占用；可能是杀软/EDR 正在扫描）")
        print("   本次未写入任何改动（asar 也未改）。请完全退出 WorkBuddy（含托盘）后重跑：")
        print("   scripts\\deploy.cmd -Asar")
        return 4

    # P4：临时备份目录。正式目录只在写盘 + 全部校验通过后由 commit_backup() 转正。
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
        "patches_skipped": [n for n, _r in missing_aux],
        "headerHashBefore": details[0]["oldHash"] if details else None,
        "headerHashAfter": new_header,
        "appliedAt": now_stamp(),
        "note": "patch-asar.py restore 用；按承载文件整份备份 + 头哈希 + exe 清单",
        "integrity": details,
        "exe": {
            "path": exe,
            "shaBefore": exe_sha_before,
            "edits": [[o, old.decode(), new.decode()] for o, old, new in exe_edits],
        } if exe else None,
    }

    asar_edits = list(content_edits) + hedits_all

    def rollback(reason):
        # 回滚顺序：先把 exe 改回去，再把 asar 改回去。任何一步失败都要如实说，
        # 因为"asar 已改、exe 没改"就是客户端 FATAL Integrity check failed 的成因。
        #
        # 判定只看**结束状态**：回滚的意义是把 asar（与 exe）恢复成写入前那一个
        # 字节不差的样子。中间某条逆编辑失败但整体 sha 已经回到 sha_before，
        # 也是成功的（例如那条本来就没写进去，revert_written 主动跳过）。
        print("!! %s，立即回滚" % reason)
        exe_rev = exe_fail = 0
        if exe_edits:
            exe_rev, exe_fail = revert_written(exe, exe_edits)
            if exe_fail:
                print("!! exe 有 %d 处回退失败" % exe_fail)
        asar_rev, asar_fail = revert_written(asar, asar_edits)
        if asar_fail:
            print("!! asar 有 %d 处回退失败" % asar_fail)
        try:
            back = sha256_file(asar)
        except OSError as e:
            print("!! 回滚后读不出 asar：%s" % e)
            return False
        asar_ok = back == sha_before
        exe_ok = True
        if exe_edits and exe_sha_before:
            try:
                exe_ok = sha256_file(exe) == exe_sha_before
            except OSError:
                exe_ok = False
        if asar_ok and exe_ok:
            print("   已回滚 asar，避免 integrity 失配")
            print("   回滚后 asar sha256 %s -> OK（%d 处 %s）"
                  % (back[:16], asar_rev, "已还原" if asar_rev else "无需改动"))
            if exe_edits and exe_sha_before:
                print("   回滚后 exe  sha256 %s -> OK（%d 处 %s）"
                      % (sha256_file(exe)[:16], exe_rev, "已还原" if exe_rev else "无需改动"))
            return True
        if not asar_ok:
            print("   回滚后 asar sha256 %s -> MISMATCH（期望 %s）" % (back[:16], sha_before[:16]))
        if not exe_ok:
            print("   回滚后 exe sha256 MISMATCH（期望 %s）" % exe_sha_before[:16])
        print("!! 回滚不完整：请用 backups/ 里的正式备份手工还原，或重装 WorkBuddy。")
        return False

    def fail(reason, code):
        """统一失败出口：先回滚，再丢掉临时备份，最后按原退出码返回。"""
        rb = rollback(reason)
        if not rb:
            discard_backup(d)  # 回滚不完整时更要清掉临时目录，别让它混进备份链
            return 1
        discard_backup(d)
        return code

    # ---- P1：asar 与 exe 是一个事务 ----
    # 只写 asar、exe 没跟上 → 客户端启动 FATAL asar_util.cc:143 Integrity check failed。
    # 所以 exe 的任何失败（权限 / 被锁 / 杀软拦截 / 任意 OSError / 任意异常）都必须回滚 asar。
    try:
        write_edits(asar, asar_edits)
    except PermissionError as e:
        print("!! asar 写入被拒绝（需要管理员）：%s" % e)
        # asar 可能已被部分写入：用同样的回滚路径把 sha 恢复回去
        rb = rollback("asar 写入失败（权限）")
        discard_backup(d)
        return 3 if rb else 1
    except OSError as e:
        print("!! asar 写入失败：%s" % e)
        rb = rollback("asar 写入失败")
        discard_backup(d)
        return 3 if rb else 1
    if not read_back(asar, asar_edits):
        return fail("asar 回读不一致", 1)

    if exe_edits:
        try:
            write_edits(exe, exe_edits)
        except PermissionError as e:
            print("!! exe 写入被拒绝：%s" % e)
            if write_denied(exe):
                print("   原因：没有管理员权限（WinError 5），不是应用占用。")
            else:
                print("   原因：文件被占用或被安全软件拦截（WinError 32/33 等）。")
            fail("exe 写入失败（权限 / 被锁 / 被杀软拦截）", 3)
            return 3
        except Exception as e:
            # 任何 OSError / EDR 注入的异常：一律回滚 asar。这正是 F-07 的变砖路径。
            print("!! exe 写入失败：%s: %s" % (type(e).__name__, e))
            fail("exe 写入失败（权限 / 被锁 / 被杀软拦截）", 3)
            return 3
        if not read_back(exe, exe_edits):
            return fail("exe 回读不一致", 1)

    # 回读已在 P1 事务段里做过；这里只补"文件大小没变"这条形态断言。
    if os.path.getsize(asar) != size:
        return fail("asar 文件大小变了", 1)

    integ_ok = True
    for site in sites:
        ok, msg = check_integrity(asar, int(site["offset"]))
        if ok is False:
            integ_ok = False
            print("integrity check: FAILED (%s)" % msg)
            break
        print("integrity check: %s" % ("OK (%s)" % msg if ok else "跳过（%s）" % msg))
    if not integ_ok:
        return fail("integrity 校验不匹配", 1)
    # P8：按补丁声明的 targets 再校验一遍（写盘后的落盘态）。
    # 必须**重新解析落盘后的 asar 头**：info["header"] 是写盘前的内存快照，
    # 用它去查刚改过 integrity 的 entry，会把没参与本次改动的补丁（例如只换了
    # 另一条补丁的 entry）误判成 MISMATCH，然后触发一次不必要的回滚。
    with open(asar, "rb") as fh:
        head = fh.read(16)
        pickle_size = struct.unpack("<I", head[4:8])[0]
        fh.seek(0)
        disk_blob = fh.read(8 + pickle_size)
        disk_info = parse_header(disk_blob)
    disk_states = dict(patch_states(open(asar, "rb").read()))
    for spec in PATCHES:
        if not spec.get("targets"):
            continue
        if disk_states.get(spec["name"]) == "skipped":
            continue  # 这条补丁本来就没打，不要求它的目标 entry 存在
        for _pat_t, rel_t, ok_t, msg_t in integrity_report(asar, disk_info["header"], spec["targets"]):
            if ok_t is False:
                print("integrity check: FAILED (%s %s)" % (spec["name"], msg_t))
                return fail("integrity 校验不匹配（%s）" % rel_t, 1)
    if exe:
        try:
            vals = exe_manifest_hashes(exe)
            synced = bool(vals) and all(v.lower() == new_header.lower() for v in vals)
            print("exe manifest:   %s" % ("MATCH（%d 处）" % len(vals) if synced else "MISMATCH"))
            if not synced:
                return fail("exe 清单未同步", 1)
        except Exception as e:
            return fail("exe 清单无法复核（%s）" % e, 1)

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
        return fail("语法校验失败", 1)

    # ---- P4：全部校验通过后才提交备份（写盘前只有 tmp-<stamp> 目录）----
    meta["shaAfter"] = sha256_file(asar)
    if exe:
        meta["exe"]["shaAfter"] = sha256_file(exe)
    try:
        final_dir = commit_backup(d, meta)
    except Exception as e:
        # 备份提交失败不该把已经打好的 asar 回滚（补丁本身是好的）；
        # 但必须如实说明"这次没有可 restore 的正式备份"。
        discard_backup(d)
        print("!! 备份提交失败：%s" % e)
        print("   补丁已写入且校验通过，但本次没有留下正式备份（tmp 目录已清理）。")
        return 1
    print("backup:    %s" % final_dir)
    print("sha256:    %s -> %s" % (sha_before[:16], meta["shaAfter"][:16]))
    if exe:
        print("exe:       %s -> %s" % (exe_sha_before[:16], meta["exe"]["shaAfter"][:16]))
    print("patches:   %s" % ", ".join("%s=%s" % (n, s) for n, s in patch_states(open(asar, "rb").read())))
    if missing_aux:
        print("WARN: %d 条补丁未应用（锚点失配）：%s"
              % (len(missing_aux), ", ".join(n for n, _r in missing_aux)))
    maybe_launch(exe, not args.no_launch)
    return 0


if __name__ == "__main__":
    sys.exit(main())
