#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trim WorkBuddy's repeated prompt blocks at the agent-runtime boundary.

Why this exists
---------------
The template fix removes the five blocks for *new* sessions, but an already
open session keeps its old system prompt and its first user message keeps the
`<system-reminder data-role="user-context">` block.  Every following request
therefore re-sends both.  This patch cleans the model input right before the
provider call, so the model never receives them again, without touching the
conversation history or the UI.

Target
------
<install>/resources/app.asar.unpacked/cli/dist/**  (the agent runtime chunk that
builds `modelInput.instructions` / `.input`).  The file name is discovered by
content, so another WorkBuddy version, another install path, or the AI edition
gets patched too instead of silently skipped.

What it strips
--------------
System instructions (every request):
  <tool_usage_policy> <result_presentation> <final_answer_instructions>
  <task_management> <agent_skills> <quality_policy> <agent_loop>
  <regional_conventions> <current_time> <additional_data>
  <system-reminder data-role="user-context">

User messages:
  * first turn: everything is kept (the one-time context still reaches the model)
  * later turns: historical user-context blocks and reminder-only messages are
    dropped; the current user message and everything after it are kept
  * the removed policy tags are stripped from every message

Usage
-----
    python scripts/patch-cli-trim.py --status
    python scripts/patch-cli-trim.py --apply [--dry-run] [--file PATH]
    python scripts/patch-cli-trim.py --restore [--file PATH]
    python scripts/patch-cli-trim.py --self-test
"""
import argparse
import datetime as _dt
import glob
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BACKUPS = os.path.join(ROOT, "backups")
DEFAULT_GLOB = ""   # 空 = 自动发现（环境变量 / 注册表 / 进程 / 快捷方式 / 磁盘扫描）；不写死任何机器路径

MARK = "__wbStripLegacyString"
TRIM_VERSION = 3
HELPER_BEGIN = "/*__wbTrimV3*/"
HELPER_END = "/*__wbTrimEnd*/"

# 最后一道：在进程出口（fetch）再扫一遍请求体。
# 前面所有清理都在 modelInput 边界；如果某个调用方绕开了那条路径、或者块是别处拼进来的，
# 只要它出现在要发出去的 JSON 里，这里就一定会删掉。只在真的含标记时才动 body，
# 其它请求原样透传；任何异常都退回原始 fetch。
SCRUB = r'''/*__wbTrimScrub*/
function __wbScrubFetch(){
  try{
    const f=globalThis.fetch;
    if(typeof f!=="function"||f.__wbScrubbed)return;
    const wrapped=function(input,init){
      try{
        if(init&&typeof init.body==="string"&&(init.body.indexOf("additional-data")>=0||init.body.indexOf("<current_time")>=0)){
          const b=__wbStripLegacyString(init.body);
          if(b!==init.body)init=Object.assign({},init,{body:b});
        }
      }catch(e){}
      return f.call(this,input,init);
    };
    wrapped.__wbScrubbed=true;
    globalThis.fetch=wrapped;
  }catch(e){}
}
__wbScrubFetch();
/*__wbTrimScrubEnd*/'''
# 只认语句开头，不认具体的 chunk id：别的构建可能是 1850/其他数字。
ANCHOR_PREFIX = b"export const __rspack_esm_ids=["

HELPER = r'''/*__wbTrimV3*/
function __wbStripLegacyString(s){
  if(typeof s!=="string")return s;
  const tags=["tool_usage_policy","result_presentation","final_answer_instructions","task_management","agent_skills","quality_policy","agent_loop","regional_conventions"];
  for(const t of tags){s=s.replace(new RegExp("<"+t+"\\b[^>]*>[\\s\\S]*?<\\/"+t+">\\s*","g"),"");}
  s=s.replace(/<current_time>[\s\S]*?<\/current_time>\s*/g,"");
  s=s.replace(/<additional_data>[\s\S]*?<\/additional_data>\s*/g,"");
  // 云端/桌面端会把 current_time 包进 data-role="additional-data"；清掉 current_time 后
  // 整块只剩空白，就把空壳也删掉。用结构扫描而不是正则：这一层还会收到 JSON 序列化后的
  // 文本（fetch body），里面的引号写成 \" 甚至 \\\"，正则很容易漏。
  s=__wbDropReminder(s,"additional-data",true);
  return s;
}
// 按结构删除 <system-reminder ... role ...>inner</system-reminder>：
// open 取 role 之前最近的 "<system-reminder"，close 取其后第一个闭合标签。
// emptyOnly=true 时只有 inner 全空白才删（additional-data 还要留给 panel_tabs 等）。
// JSON 文本里的换行是字面量 反斜杠+n、tab 是 反斜杠+t，trim() 不会去掉它们，
// 所以"只剩空白"的判断必须把这些转义也当成空白。（用 fromCharCode 避免多层转义）
function __wbBlank(inner){
  const B=String.fromCharCode(92);
  return inner.split(B+"n").join("").split(B+"r").join("")
              .split(B+"t").join("").split(B+'"').join("").trim()==="";
}
function __wbDropReminder(s,role,emptyOnly){
  let pos=0;
  for(;;){
    const a=s.indexOf(role,pos);
    if(a<0)return s;
    const open=s.lastIndexOf("<system-reminder",a);
    const gt=s.indexOf(">",a);
    if(open<0||gt<0){pos=a+role.length;continue;}
    if(s.slice(open,gt).indexOf(role)<0){pos=a+role.length;continue;}
    const close=s.indexOf("</system-reminder>",gt);
    if(close<0){pos=a+role.length;continue;}
    const end=close+"</system-reminder>".length;
    if(emptyOnly&&!__wbBlank(s.slice(gt+1,close))){pos=end;continue;}
    s=s.slice(0,open)+s.slice(end);
    pos=open;
  }
}
function __wbStripUserContext(s){
  return typeof s==="string"?__wbDropReminder(s,"user-context",false):s;
}
function __wbStripLegacyAny(x){
  if(typeof x==="string")return __wbStripUserContext(__wbStripLegacyString(x));
  if(Array.isArray(x))return x.map(__wbStripLegacyAny);
  if(x&&typeof x==="object"){
    const y={...x};
    if(typeof y.content==="string")y.content=__wbStripUserContext(__wbStripLegacyString(y.content));
    if(typeof y.text==="string")y.text=__wbStripUserContext(__wbStripLegacyString(y.text));
    return y;
  }
  return x;
}
function __wbTextOf(it){
  if(!it||typeof it!=="object")return "";
  const c=it.content;
  if(typeof c==="string")return c;
  if(Array.isArray(c))return c.filter(p=>p&&(p.type==="text"||p.type==="input_text")&&typeof p.text==="string").map(p=>p.text).join("\n");
  return "";
}
// 真实 input 元素是 {"role":"user","content":[{"type":"text","text":"..."}]}，
// 没有 type:"message"。v1 要求 type==="message"，于是把每条消息都当成"非消息"原样透传，
// 历史 user-context 从来没有被删掉过——用户看到的"每轮重发"就是它。
function __wbIsUser(it){return !!it&&it.role==="user"}
function __wbIsReminderOnly(it){
  if(!__wbIsUser(it))return false;
  const t=__wbTextOf(it);
  return /^\s*<system-reminder\b/.test(t)&&!t.includes("<user_query>");
}
function __wbCleanMessage(it,keepUserContext){
  if(!it||typeof it!=="object")return it;
  const c=it.content;
  if(typeof c==="string"){
    let t=__wbStripLegacyString(c);
    if(!keepUserContext)t=__wbStripUserContext(t);
    t=t.trim();
    if(!t)return null;
    return t===c?it:{...it,content:t};
  }
  if(Array.isArray(c)){
    let changed=false,parts=[];
    for(const p of c){
      if(p&&(p.type==="text"||p.type==="input_text")&&typeof p.text==="string"){
        let t=__wbStripLegacyString(p.text);
        if(!keepUserContext)t=__wbStripUserContext(t);
        t=t.trim();
        if(!t){changed=true;continue;}
        if(t!==p.text){changed=true;parts.push({...p,text:t});}
        else parts.push(p);
      }else{parts.push(p)}
    }
    if(parts.length===0)return null;
    return changed?{...it,content:parts}:it;
  }
  return it;
}
function __wbCleanRuntimeInput(input){
  if(!Array.isArray(input))return input;
  const real=[];
  for(let i=0;i<input.length;i++){
    const it=input[i];
    if(!__wbIsUser(it))continue;
    if(!__wbIsReminderOnly(it))real.push(i);
  }
  const lastReal=real.length?real[real.length-1]:-1;
  const firstTurn=real.length<=1;
  const out=[];
  for(let i=0;i<input.length;i++){
    const it=input[i];
    // 只改注入进 user 消息的内容。assistant / tool 的历史原样保留：
    // 模型自己讨论这些标签时，正文不能被当成注入物删掉。
    if(!__wbIsUser(it)){out.push(it);continue;}
    // 纯提醒消息（没有 <user_query>）在任何位置都是注入物，直接丢
    if(__wbIsReminderOnly(it))continue;
    if(!firstTurn&&i<lastReal){
      const x=__wbCleanMessage(it,false);
      if(x)out.push(x);
      continue;
    }
    const x=__wbCleanMessage(it,true);
    if(x)out.push(x);
  }
  return out;
}
/*__wbTrimEnd*/'''.strip()


def sha256(b):
    return hashlib.sha256(b).hexdigest()


def read(path):
    with open(path, "rb") as fh:
        return fh.read()


def write(path, data):
    tmp = path + ".tmp-trim"
    with open(tmp, "wb") as fh:
        fh.write(data)
    os.replace(tmp, path)


def default_targets():
    """不写死路径：从环境变量 / 注册表 / 进程 / 快捷方式 / 磁盘扫描取每套安装的 cli\\dist。"""
    try:
        sys.path.insert(0, HERE)
        import _install  # noqa: WPS433
        roots = _install.install_roots_deep()
    except Exception:
        roots = []
    out = []
    for root in roots:
        dist = os.path.join(root, "resources", "app.asar.unpacked", "cli", "dist")
        if os.path.isdir(dist):
            out.append(dist)
    return out


SKIP_DIRS = {"node_modules", ".git", "renderer", "locales", "assets"}
MAX_DEPTH = 6
MAX_FILE = 24 * 1024 * 1024


def carries_runtime(path):
    """True when this file is the agent runtime that builds modelInput."""
    try:
        if os.path.getsize(path) > MAX_FILE:
            return False
        data = read(path)
    except OSError:
        return False
    return b'modelInput.instructions' in data and b'modelInput.input' in data


def find_targets(pattern):
    """Resolve a chunk path / glob / directory to the runtime chunk(s).

    Never trust the file name.  A WorkBuddy update renames the chunk
    (``1849.<hash>.mjs`` -> another id/hash) and another machine may install to
    a different folder (D:\\, the AI edition, a per-user install).  Only the
    content identifies the runtime, so a directory is walked recursively until
    the file carrying ``modelInput.instructions`` + ``modelInput.input`` shows
    up.  This is what makes the patch work on someone else's computer instead of
    silently skipping.
    """
    if os.path.isfile(pattern):
        return [pattern]
    if os.path.isdir(pattern):
        folder = pattern
        hits = []
    else:
        folder = os.path.dirname(pattern)
        hits = sorted(p for p in glob.glob(pattern) if os.path.isfile(p))
        if hits:
            return hits
    if not os.path.isdir(folder):
        return []
    base = os.path.abspath(folder).rstrip("\\/")
    base_depth = base.count(os.sep)
    for root, dirs, files in os.walk(base):
        depth = root.count(os.sep) - base_depth
        if depth >= MAX_DEPTH:
            dirs[:] = []
        dirs[:] = [d for d in dirs if d.lower() not in SKIP_DIRS]
        for name in sorted(files):
            if not name.endswith(".mjs"):
                continue
            path = os.path.join(root, name)
            if carries_runtime(path):
                hits.append(path)
    return sorted(set(hits))


def node_check(path):
    node = shutil.which("node")
    if not node:
        return True, "node not found; skipped syntax check"
    p = subprocess.run([node, "--check", path], capture_output=True, text=True)
    if p.returncode != 0:
        return False, (p.stdout or "") + (p.stderr or "")
    return True, "node --check OK"


def _fail_with_context(data, label, needle=b"", span=160):
    """锚点找不到 -> 硬失败，并把锚点附近的字节打出来。

    规范要求：任何 "锚点未命中" 都必须 FAIL（非零退出码）+ 明确原因 + 锚点上下文字节，
    不许降级为 SKIP 后继续报成功。needle 为空时打印模块头附近。
    """
    at = data.find(needle) if needle else 0
    if at < 0:
        at = 0
    lo = max(0, at - span)
    hi = min(len(data), at + len(needle) + span if needle else at + span * 2)
    snippet = data[lo:hi]
    try:
        text = snippet.decode("utf-8", "backslashreplace")
    except Exception:
        text = repr(snippet)
    sys.stderr.write("[FAIL] anchor not found: %s\n" % label)
    sys.stderr.write("       chunk bytes=%d, context at %d..%d:\n" % (len(data), lo, hi))
    sys.stderr.write("       ----8<----\n")
    sys.stderr.write(text.replace("\r\n", "\n"))
    sys.stderr.write("\n       ----8<----\n")
    raise SystemExit(2)


def replace_once(data, old, new, label):
    n = data.count(old)
    if n == 0:
        if data.count(new) >= 1:
            return data, "already"
        _fail_with_context(data, label, old)
    if n != 1:
        raise SystemExit("[FAIL] anchor %s matched %d times (want exactly 1)" % (label, n))
    return data.replace(old, new, 1), "patched"


INJECT_KEYWORDS = (b"systemInstructions:", b"input:")

# 收口用：任何"还没包上"的注入点（不看数量，只看有没有漏网的）。
RE_INS_RAW = re.compile(rb"(?<![A-Za-z0-9_$])systemInstructions:(?!__wbStripLegacyAny\()"
                        rb"[A-Za-z$_][\w$]*\.modelInput\.instructions,")
RE_IN_RAW = re.compile(rb"(?<![A-Za-z0-9_$])input:(?!__wbCleanRuntimeInput\()"
                       rb"[A-Za-z$_][\w$]*\.modelInput\.input,")


def _dynamic_inject_points(data):
    """用对混淆名不敏感的结构化正则定位注入点，返回 [(kind, var, start, end), …]。

    kind    : "instructions" | "input"
    var     : chunk 里的实际标识符（g / w / a … 由内容决定，不假设具体字母）
    结构化正则：
      instructions:  <任意标识符>.modelInput.instructions  紧跟在 systemInstructions: 之后，
                     且前面不能已经包过 __wbStripLegacyAny(
      input:         <任意标识符>.modelInput.input         紧跟在 input: 之后（前面不能是
                     systemInstructions: / previousResponseId: 等别的键），且没包过 __wbCleanRuntimeInput(

    两种真实形态都覆盖：
      systemInstructions:g.modelInput.instructions,prompt:…
      …input:g.modelInput.input,previousResponseId:…
    """
    points = []

    # 1) instructions：systemInstructions:<IDENT>.modelInput.instructions,
    pat = re.compile(rb"(?<![A-Za-z0-9_$])systemInstructions:((?!__wbStripLegacyAny\()"
                     rb"([A-Za-z$_][\w$]*)\.modelInput\.instructions),")
    for m in pat.finditer(data):
        points.append(("instructions", m.group(2), m.start(), m.end()))

    # 2) input：input:<IDENT>.modelInput.input, —— 但排除 previousResponseId:"input" 之类
    #    只在 "input:" 是对象字面量的键时匹配：前面必须是 { 或 ,（允许空白）。
    pat_in = re.compile(rb"(?<![A-Za-z0-9_$])input:((?!__wbCleanRuntimeInput\()"
                        rb"([A-Za-z$_][\w$]*)\.modelInput\.input),")
    for m in pat_in.finditer(data):
        # 前面紧邻的字符决定它是不是一个真正的对象键（而不是 previousResponseId:"input"）
        j = m.start() - 1
        while j >= 0 and data[j:j + 1] in b" \t\r\n":
            j -= 1
        if j >= 0 and data[j:j + 1] not in (b"{", b","):
            continue
        points.append(("input", m.group(2), m.start(), m.end()))

    # 去重（同一站点可能被两个正则同时命中，例如 input: 前面恰好是 {）
    seen, out = set(), []
    for kind, var, s, e in points:
        key = (kind, s)
        if key in seen:
            continue
        seen.add(key)
        out.append((kind, var, s, e))
    return out


def _injection_groups(data):
    """按"承载对象标识符"归组所有 modelInput 注入点：{ident: {(kind, state), …}}。

    真实构建里每个 provider 分支的对象（g / w / a …）都同时有 instructions 与 input。
    这个对称性是**结构事实**，与混淆名无关；用它可以在"某个调用点被改名/换了形态"时
    发现缺口——只看"还能找到几个"会漏掉被改坏的那一个。
    """
    groups = {}
    pats = (
        ("instructions", "raw",
         re.compile(rb"(?<![A-Za-z0-9_$])systemInstructions:(?!__wbStripLegacyAny\()"
                    rb"([A-Za-z$_][\w$]*)\.modelInput\.instructions,")),
        ("instructions", "wrapped",
         re.compile(rb"systemInstructions:__wbStripLegacyAny\("
                    rb"([A-Za-z$_][\w$]*)\.modelInput\.instructions\)")),
        ("input", "raw",
         re.compile(rb"(?<![A-Za-z0-9_$])input:(?!__wbCleanRuntimeInput\()"
                    rb"([A-Za-z$_][\w$]*)\.modelInput\.input,")),
        ("input", "wrapped",
         re.compile(rb"input:__wbCleanRuntimeInput\(([A-Za-z$_][\w$]*)\.modelInput\.input\)")),
    )
    for kind, state, pat in pats:
        for m in pat.finditer(data):
            groups.setdefault(m.group(1), set()).add((kind, state))
    return groups


def _symmetry_gaps(data):
    """返回 [(ident, 缺什么), …]：每个 modelInput 对象必须两种注入点都在、且都没漏包。"""
    gaps = []
    for ident, states in sorted(_injection_groups(data).items()):
        kinds = {k for k, _s in states}
        for kind in ("instructions", "input"):
            if kind not in kinds:
                gaps.append((ident, "missing %s call site" % kind))
                continue
            if (kind, "raw") in states:
                gaps.append((ident, "un-wrapped %s call site" % kind))
            if (kind, "wrapped") not in states:
                gaps.append((ident, "no wrapped %s call site" % kind))
    return gaps


def discover_module_ids(data, path=""):
    """模块 ID 从安装的 chunk 文件名 / 内容里发现，不假设 "1849"。

    返回 (ids, evidence)：
      ids      : 发现到的模块 id 列表（去重、保持出现顺序）
      evidence : [(来源, id), …]，给 --status / 失败信息用
    """
    ids, evidence, seen = [], [], set()

    def add(value, source):
        if not value:
            return
        v = value.decode("ascii", "replace") if isinstance(value, bytes) else str(value)
        v = v.strip().strip('"').strip("'")
        if not v or v in seen:
            return
        seen.add(v)
        ids.append(v)
        evidence.append((source, v))

    head = data[:8192]
    m = re.search(rb"__rspack_esm_ids=\[([^\]]*)\]", head)
    if m:
        for part in re.findall(rb'"([^"]+)"|\'([^\']+)\'|(\d+)', m.group(1)):
            add(next((x for x in part if x), b""), "content:__rspack_esm_ids")
    m = re.search(rb"__rspack_esm_id=\"([^\"]+)\"", head)
    if m:
        add(m.group(1), "content:__rspack_esm_id")
    if path:
        m = re.match(r"^(\d+)\.[0-9a-fA-F]+\.mjs$", os.path.basename(path))
        if m:
            add(m.group(1), "filename")
    return ids, evidence


def _anchor_of(data):
    """定位模块头的 rspack id 语句，按语句边界取，不写死 id。

    新版打包器可能只导出 __rspack_esm_id="1849"（单数），也可能两个都有。
    锚点取"含 rspack id 的整条 export 语句"，不依赖具体 id 值。
    """
    for prefix in (ANCHOR_PREFIX, b"export const __rspack_esm_id="):
        i = data.find(prefix)
        if i < 0:
            continue
        j = data.find(b";", i)
        if j < 0:
            continue
        return data[i:j + 1]
    return None


def patch(data, path=""):
    log = []
    if MARK.encode() not in data:
        anchor = _anchor_of(data)
        if not anchor:
            _fail_with_context(data, "module header (__rspack_esm_ids / __rspack_esm_id)",
                               b"__rspack_esm_id")
        data = data.replace(anchor, anchor + HELPER.encode() + SCRUB.encode(), 1)
        log.append(("helper", "patched"))
    else:
        # 已经打过补丁的 chunk 里，helper 是**旧版本**的代码。只加新东西不够：
        # 修好的 bug 在 helper 内部，必须整段换成仓库里的当前文本，否则永远停在旧逻辑。
        b = data.find(HELPER_BEGIN.encode())
        e = data.find(HELPER_END.encode())
        if b < 0 or e < b:
            _fail_with_context(data, "helper markers in an already-patched chunk", HELPER_BEGIN.encode())
        current = data[b:e + len(HELPER_END)]
        if b"__wbBlank" not in current or b"__wbDropReminder" not in current:
            # 旧版 helper（没有 __wbBlank/__wbDropReminder）：整段换成仓库里的当前版本。
            data = data.replace(current, HELPER.encode(), 1)
            log.append(("helper-upgrade", "patched"))
        else:
            log.append(("helper", "already"))
        if b"__wbScrubFetch" not in data:
            data = data.replace(HELPER_END.encode(), SCRUB.encode() + HELPER_END.encode(), 1)
            log.append(("fetch-scrub", "patched"))
        elif b"/*__wbTrimScrub*/" not in data:
            # 旧版 helper 有 __wbScrubFetch 定义却没有 Scrub 段标记（v3 早期），
            # 补上标记并不改语义，只是让 is_patched() 的检查口径一致。
            raise SystemExit("[FAIL] chunk has __wbScrubFetch but no /*__wbTrimScrub*/ marker; "
                             "restore the original chunk and re-apply (--restore)")
        else:
            log.append(("fetch-scrub", "already"))

    # 注入点用结构化正则定位；变量名（压缩后常是 g/w/a…）从内容里发现，不假设具体字母。
    # 每个 provider 分支各有一处 systemInstructions / input。
    # 两类情况不许混为一谈：
    #   * 一个未包装的调用点都没有，但已经有包装过的 -> 已经打好了（helper 可能刚升级过）
    #   * 连包装过的都没有 / 还有未包装的 -> 锚点对不上，硬失败并打印上下文
    points = _dynamic_inject_points(data)
    ins = [(v, s, e) for kind, v, s, e in points if kind == "instructions"]
    inp = [(v, s, e) for kind, v, s, e in points if kind == "input"]

    # 结构化对称性检查只对"已经打过补丁"的形态生效（此时每个对象两种调用点都应已包好）。
    # 原始 chunk 里所有调用点都未包装，属于正常输入，不能因此失败。
    if not ins and not inp:
        gaps = _symmetry_gaps(data)
        if gaps:
            detail = "; ".join("%s: %s" % (ident.decode("ascii", "replace"), why) for ident, why in gaps)
            _fail_with_context(data, "modelInput injection sites are asymmetric (%s)" % detail,
                               b".modelInput.instructions,")

    if not ins:
        if RE_INS_RAW.search(data) or data.count(b"__wbStripLegacyAny(") == 0:
            _fail_with_context(data, "systemInstructions:<ident>.modelInput.instructions",
                               b"systemInstructions:")
        log.append(("instructions", "already"))
    if not inp:
        if RE_IN_RAW.search(data) or data.count(b"__wbCleanRuntimeInput(") == 0:
            _fail_with_context(data, "input:<ident>.modelInput.input", b"modelInput.input")
        log.append(("input", "already"))

    ids, evidence = discover_module_ids(data, path)
    if evidence:
        log.append(("module-id", ", ".join("%s->%s" % (src, v) for src, v in evidence)))

    # 从后往前替换：偏移量不受前面替换影响。只替换每个站点的精确字节区间。
    edits = []
    for var, s, e in ins:
        old = data[s:e]
        new = b"systemInstructions:__wbStripLegacyAny(" + var + b".modelInput.instructions),"
        edits.append((s, e, old, new, "instructions(%s)" % var.decode("ascii", "replace")))
    for var, s, e in inp:
        old = data[s:e]
        new = b"input:__wbCleanRuntimeInput(" + var + b".modelInput.input),"
        edits.append((s, e, old, new, "input(%s)" % var.decode("ascii", "replace")))

    for s, e, old, new, label in sorted(edits, key=lambda x: x[0], reverse=True):
        data = data[:s] + new + data[e:]
        log.append((label, "patched"))

    # 收口：替换后必须一处 raw 都不剩；还剩就硬失败（而不是打一半算成功）。
    leftover = len(RE_INS_RAW.findall(data)) + len(RE_IN_RAW.findall(data))
    if leftover:
        raise SystemExit("[FAIL] %d injection site(s) still un-wrapped after patch" % leftover)

    # 收口 2：打完补丁的形态必须对称 —— 每个 modelInput 对象两种调用点都包上了。
    # 某个分支被改名/换了形态时，这里会发现"只包了一半"。
    gaps = _symmetry_gaps(data)
    if gaps:
        detail = "; ".join("%s: %s" % (ident.decode("ascii", "replace"), why) for ident, why in gaps)
        # 上下文锚点用 ".modelInput."（被改坏的站点多半就是删掉了 .modelInput.instructions，
        # 拿它当锚会定位不到，反而打印文件头）。同对象的 input 注入点通常还在附近。
        _fail_with_context(data, "patch left asymmetric injection sites (%s)" % detail,
                           b".modelInput.")
    return data, log


def version_of(data):
    """0 = 原始；1 = v1（判据错误，历史 user-context 从未被删）；2 = v2（role 判据/--file）；3 = v3（additional-data 空壳）。"""
    if HELPER_BEGIN.encode() in data:
        return TRIM_VERSION
    if MARK.encode() in data:
        return 1
    return 0


def is_patched(data):
    # 不数"打了几处"（不同构建的分支数不同），改看"还有没有没包上的调用点"。
    # fetch-scrub 用"定义 + 调用"两处来判断（__wbScrubFetch 在 SCRUB 里出现两次）。
    return (
        version_of(data) == TRIM_VERSION
        and not RE_INS_RAW.search(data)
        and not RE_IN_RAW.search(data)
        and data.count(b"__wbStripLegacyAny(") >= 1
        and data.count(b"__wbCleanRuntimeInput(") >= 1
        and data.count(b"__wbScrubFetch") >= 2
        and b"__wbBlank" in data
        and b"__wbDropReminder" in data
    )


def earliest_backup(path):
    """同一目标的备份里最早的那份就是原始 chunk（v1 升级 v2 时用它回到干净状态）。"""
    if not os.path.isdir(BACKUPS):
        return None
    cands = []
    for f in os.listdir(BACKUPS):
        fp = os.path.join(BACKUPS, f)
        if f.endswith(".json") or not os.path.isfile(fp):
            continue
        try:
            meta = json.load(open(fp + ".json", encoding="utf-8"))
        except Exception:
            meta = {}
        if meta.get("source") == path:
            cands.append((os.path.getmtime(fp), fp))
    return sorted(cands)[0][1] if cands else None


def backup(path, tag):
    os.makedirs(BACKUPS, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUPS, "%s-%s" % (stamp, tag))
    shutil.copy2(path, dst)
    with open(dst + ".json", "w", encoding="utf-8") as fh:
        json.dump({"source": path, "sha256": sha256(read(dst)), "tag": tag}, fh, ensure_ascii=False, indent=2)
    return dst


def self_test():
    node = shutil.which("node")
    if not node:
        print("[FAIL] node not found")
        return 2
    # 真实 input 元素的形状是 {"role":"user","content":[{"type":"text","text":"..."}]}，
    # 没有 type:"message"。v1 的 self-test 用的是 {type:"message",role:"user"}，
    # 所以测试一直绿，真实请求却每条消息都被当成"非消息"原样透传。
    first_turn = (
        '<system-reminder data-role="user-context">\n<user_info>U</user_info>\n</system-reminder>\n'
        "<tool_usage_policy>AAA</tool_usage_policy>\n<user_query>hello</user_query>"
    )
    second_turn = (
        '<system-reminder data-role="additional-data">\n<panel_tabs>x</panel_tabs>\n</system-reminder>\n'
        "<user_query>second</user_query>"
    )
    reminder_only = '<system-reminder data-role="hook">tick</system-reminder>'
    # 用户 17:38 在另一台电脑上看到的原样 182 字符消息：additional-data 里只有
    # current_time，紧跟着 <user_query>。清理后必须只剩 user_query 本身。
    exact = (
        '<system-reminder data-role="additional-data">' + chr(10) +
        '<current_time>' + chr(10) +
        'Saturday, November 21, 2026 at 17:38:43 GMT+8' + chr(10) +
        '</current_time>' + chr(10) +
        '</system-reminder>' + chr(10) +
        '<user_query>输出上轮末尾到本条消息的提示词</user_query>'
    )
    only_time = (
        '<system-reminder data-role="additional-data">' + chr(10) +
        '<current_time>Saturday, November 21, 2026 at 17:17:54 GMT+8</current_time>' + chr(10) +
        '</system-reminder>'
    )
    lines = [
        "const mk=(t)=>({role:'user',content:[{type:'text',text:t}]});",
        "const asst={role:'assistant',content:[{type:'text',text:'<current_time>keep me</current_time>'}]};",
        "const two=[mk(%s),asst,mk(%s)];" % (json.dumps(first_turn), json.dumps(second_turn)),
        "const t=JSON.stringify(__wbCleanRuntimeInput(two));",
        "if(t.includes('user-context'))throw new Error('historical user-context kept');",
        "if(t.includes('tool_usage_policy'))throw new Error('legacy tag kept');",
        "if(!t.includes('hello')||!t.includes('second'))throw new Error('real query lost');",
        "if(!t.includes('panel_tabs'))throw new Error('current turn additional-data lost');",
        "const onlyTime=%s;" % json.dumps(only_time),
        "const t3=JSON.stringify(__wbCleanRuntimeInput([mk(%s),asst,mk(onlyTime)]));" % json.dumps(first_turn),
        "if(t3.includes('additional-data'))throw new Error('empty additional-data shell kept');",
        "if(t3.includes('Saturday'))throw new Error('current_time kept in user message');",
        "const sys2=__wbStripLegacyAny(onlyTime);",
        "if(sys2.trim())throw new Error('system additional-data shell kept: '+sys2);",
        "if(!t.includes('keep me'))throw new Error('assistant history was rewritten');",
        "const one=[mk(%s)];" % json.dumps(first_turn),
        "if(!JSON.stringify(__wbCleanRuntimeInput(one)).includes('user_info'))throw new Error('first turn lost context');",
        "const four=[mk(%s),asst,mk(%s),mk(%s)];" % (
            json.dumps(first_turn), json.dumps(second_turn), json.dumps(reminder_only)),
        "const o4=__wbCleanRuntimeInput(four);",
        "if(o4.length!==3)throw new Error('reminder-only message should be dropped, got '+o4.length);",
        "const sys=__wbStripLegacyAny('<current_time>now</current_time><user_query>x</user_query>');",
        "if(sys.includes('current_time'))throw new Error('system current_time kept');",
        "const exact=%s;" % json.dumps(exact),
        "const e1=__wbStripLegacyString(exact);",
        "if(e1.includes('current_time')||e1.includes('additional-data'))throw new Error('exact user block kept: '+e1);",
        "const e2=e1.trim();",
        "if(e2!=='<user_query>%s</user_query>')throw new Error('exact block residue: '+JSON.stringify(e2));" % "输出上轮末尾到本条消息的提示词",
        "const o5=JSON.stringify(__wbCleanRuntimeInput([mk(%s),asst,mk(exact)]).filter(x=>x.role==='user'));" % json.dumps(first_turn),
        "if(o5.includes('additional-data')||o5.includes('current_time'))throw new Error('exact block survived runtime clean');",
        "// 出口清理：fetch 的请求体里不能留下这些标记",
        "let seen=null;",
        "globalThis.fetch=async function(u,i){seen=i&&i.body;return{ok:true};};",
        "globalThis.fetch.__wbScrubbed=false;",
        "__wbScrubFetch();",
        "await globalThis.fetch('x',{method:'POST',body:JSON.stringify({input:[{role:'user',content:[{type:'text',text:exact}]}]})});",
        "if(!seen||seen.includes('current_time')||seen.includes('additional-data'))throw new Error('fetch body not scrubbed: '+seen);",
        "if(!seen.includes('user_query'))throw new Error('fetch scrub lost the user query');",
        "const plain='{\"a\":1}';",
        "seen=null;",
        "await globalThis.fetch('x',{method:'POST',body:plain});",
        "if(seen!==plain)throw new Error('clean fetch body was rewritten');",
        "console.log('SELF TEST OK');",
    ]
    js = HELPER + SCRUB + "\n" + "\n".join(lines) + "\n"
    tmp = os.path.join(ROOT, "tmp-helper-test.mjs")
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(js)
    p = subprocess.run([node, tmp], capture_output=True, text=True)
    try:
        os.remove(tmp)
    except OSError:
        pass
    print((p.stdout or "") + (p.stderr or ""))
    return 0 if p.returncode == 0 else 1


def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--apply", action="store_true")
    g.add_argument("--restore", action="store_true")
    g.add_argument("--status", action="store_true")
    g.add_argument("--self-test", action="store_true")
    ap.add_argument("--file", default=DEFAULT_GLOB,
                    help="chunk path, glob, or a directory (e.g. <install>\\resources\\app.asar.unpacked\\cli\\dist); "
                         "content discovery finds the runtime chunk whatever it is named")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    if args.self_test:
        return self_test()

    if args.file:
        targets = find_targets(args.file)
    else:
        targets = []
        for dist in default_targets():
            targets.extend(find_targets(dist))
        targets = sorted(set(targets))
    if not targets:
        print("[FAIL] no target found: no file under any install carries "
              "modelInput.instructions / modelInput.input.")
        print("       pass --file <chunk|glob|directory>, or set WORKBUDDY_INSTALL.")
        return 2

    rc = 0
    for path in targets:
        data = read(path)
        print("file:   %s" % path)
        print("bytes:  %d" % len(data))
        print("sha256: %s" % sha256(data))
        if args.status:
            ok = is_patched(data)
            print("version: %d (expected %d)" % (version_of(data), TRIM_VERSION))
            ids, evidence = discover_module_ids(data, path)
            if evidence:
                print("module: %s" % ", ".join("%s->%s" % (src, v) for src, v in evidence))
            print("status: %s" % ("PATCHED" if ok else "NOT PATCHED"))
            rc = 0 if ok else 1
            continue

        if args.restore:
            cands = []
            if os.path.isdir(BACKUPS):
                for f in os.listdir(BACKUPS):
                    fp = os.path.join(BACKUPS, f)
                    if f.endswith(".json") or not os.path.isfile(fp):
                        continue
                    meta = fp + ".json"
                    try:
                        m = json.load(open(meta, encoding="utf-8"))
                    except Exception:
                        m = {}
                    if m.get("source") == path:
                        cands.append((os.path.getmtime(fp), fp))
            if not cands:
                print("[FAIL] no backup for %s" % path)
                rc = 2
                continue
            src = sorted(cands)[0][1]  # earliest backup = the original chunk
            if args.dry_run:
                print("[dry-run] would restore from %s" % src)
                continue
            shutil.copy2(src, path)
            print("restored: %s -> %s" % (src, path))
            print("sha256:   %s" % sha256(read(path)))
            continue

        if is_patched(data):
            print("status: already patched (v%d); nothing to do" % TRIM_VERSION)
            continue

        ver = version_of(data)
        if ver == 1:
            # v1 的判据是错的（历史 user-context 从来没被删），不能叠加，先回到原始 chunk 再打
            src = earliest_backup(path)
            if not src:
                print("[FAIL] v1 patch found but no backup to roll back; run --restore first")
                rc = 2
                continue
            print("upgrade: v1 -> v%d (restore %s first)" % (TRIM_VERSION, src))
            if args.dry_run:
                print("[dry-run] would restore the original chunk, then patch it")
                continue
            data = read(src)
            print("bytes:  %d (restored original)" % len(data))

        new_data, log = patch(data, path)
        for name, st in log:
            print("  %-24s %s" % (name, st))
        print("bytes:  %d -> %d" % (len(data), len(new_data)))
        if args.dry_run:
            print("[dry-run] no write")
            continue
        dst = backup(path, os.path.basename(path) + ".bak")
        print("backup: %s" % dst)
        write(path, new_data)
        ok, msg = node_check(path)
        print(msg)
        if not ok:
            shutil.copy2(dst, path)
            print("[FAIL] syntax check failed; restored original")
            rc = 3
            continue
        print("sha256: %s" % sha256(read(path)))
        print("status: PATCHED")
    return rc


if __name__ == "__main__":
    sys.exit(main())
