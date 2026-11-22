#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Trim WorkBuddy's repeated prompt blocks at the two layers that really matter.

Why this exists
---------------
The template fix removes the five blocks for *new* sessions, but an already
open session keeps its old system prompt and its first user message keeps the
`<system-reminder data-role="user-context">` block.  Every following request
therefore re-sends both.  This patch cleans them right before the provider
call, so the model never receives them again, without touching the
conversation history or the UI.

Two patch kinds
---------------
``runtime``   ``lazy-lite-wb/<id>.<hash>.mjs`` (the chunk that builds
              ``modelInput.instructions`` / ``modelInput.input``).
              Wraps the two call sites in ``__wbStripLegacyAny`` /
              ``__wbCleanRuntimeInput`` and installs the JSON-safe helpers.
              This layer is unchanged in spirit: it is the only place that can
              drop whole reminder-only user messages.

``transport`` ``codebuddy-lite-wb.mjs`` (the ~12 MB main bundle).
              F-01: the OpenAI client is built as
              ``fetch: this.axiosToFetchAdapter()`` and that adapter goes
              through ``restOperations.request`` -> Axios -> Node ``http``.
              ``globalThis.fetch`` is never called, so a fetch hijack alone
              intercepts 0 requests.  The adapter returned there is the true
              egress for *model* traffic, and the OpenAI SDK calls it as
              ``this.fetch.call(void 0, url, init)`` with a JSON string body.
              This patch wraps that adapter (``__wbOutletFetch``) and scrubs
              the JSON body per message.  ``globalThis.fetch`` is still
              wrapped as a cheap secondary net for other callers.

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

Egress safety (F-10 / E2 / E5)
------------------------------
The outlet never runs a regex over serialized JSON.  It does
``JSON.parse`` -> per-message ``role``/``content`` cleaning -> ``JSON.stringify``.
Unparseable bodies (SSE fragments, binary) pass through byte-identical and are
counted in ``globalThis.__wbScrubStats``.

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
TRIM_VERSION = 4                       # E6: rev 4 = E3/E4 fixes + scrub moved out of the helper
HELPER_BEGIN = "/*__wbTrimV3*/"        # 旧名字保留：v3 的 chunk 也要能被认出来并就地升级
HELPER_END = "/*__wbTrimEnd*/"
HELPER_REV = "/*__wbTrimRev4*/"

SCRUB_BEGIN = "/*__wbTrimScrub*/"
SCRUB_END = "/*__wbTrimScrubEnd*/"

OUTLET_BEGIN = "/*__wbOutletV1*/"
OUTLET_END = "/*__wbOutletEnd*/"
OUTLET_INSERT_ANCHOR = b"var __webpack_modules__={"
OUTLET_CALL = b"fetch:this.axiosToFetchAdapter()"
OUTLET_WRAPPED_CALL = b"fetch:__wbOutletFetch(this.axiosToFetchAdapter())"
ADAPTER_ANCHORS = (b'adapter:["xhr","http","fetch"]',
                   b"adapter:['xhr','http','fetch']")

# ---------------------------------------------------------------------------
# Shared cleaner (used by BOTH patch kinds; keps the transport block
# self-contained because it lives in a different file/module).
# ---------------------------------------------------------------------------
CLEANER_JS = r'''
function __wbStats(){
  var g=typeof globalThis!=="undefined"?globalThis:(typeof window!=="undefined"?window:{});
  var s=null;
  try{s=g.__wbScrubStats;}catch(e){}
  if(!s||typeof s!=="object"){
    s={calls:0,skipped:0,parsed:0,rewritten:0,parseFailed:0,errors:0,bytesBefore:0,bytesAfter:0,lastError:""};
    try{g.__wbScrubStats=s;}catch(e){}
    try{if(typeof window!=="undefined"&&window&&window!==g)window.__wbScrubStats=s;}catch(e){}
  }
  return s;
}
// additional-data 里只剩 current_time 时，清掉 current_time 会留下换行/空格。
// 这里把各种空白（含 反斜杠+n / 反斜杠+t / 引号）都当空白判断。
// 用 fromCharCode 取反斜杠，避免帮助文本被多层字符串转义弄错。
function __wbBlank(inner){
  const B=String.fromCharCode(92);
  return inner.split(B+"n").join("").split(B+"r").join("")
              .split(B+"t").join("").split(B+'"').join("").trim()==="";
}
// 按结构删除 <system-reminder ... role ...>inner</system-reminder>。
// E3 (F-10 Bug A): role 必须位于**开标签内部**。若 open 之后第一个 ">" 出现在
// role 之前，说明 role 是正文文字（或属于更早一个已闭合的标签），一律不动。
function __wbDropReminder(s,role,emptyOnly){
  const OPEN="<system-reminder",CLOSE="</system-reminder>";
  let pos=0;
  for(;;){
    const a=s.indexOf(role,pos);
    if(a<0)return s;
    const open=s.lastIndexOf(OPEN,a);
    if(open<0){pos=a+role.length;continue;}
    const gt=s.indexOf(">",open);
    if(gt<0||a>gt){pos=a+role.length;continue;}
    const head=s.slice(open,a);
    if(head.indexOf(">")>=0||head.indexOf(CLOSE)>=0){pos=a+role.length;continue;}
    const openTag=s.slice(open,gt+1);
    if(openTag.indexOf(role)<0){pos=a+role.length;continue;}
    const close=s.indexOf(CLOSE,gt+1);
    if(close<0){pos=a+role.length;continue;}
    const end=close+CLOSE.length;
    if(emptyOnly&&!__wbBlank(s.slice(gt+1,close))){pos=end;continue;}
    s=s.slice(0,open)+s.slice(end);
    pos=open;
  }
}
// E5: 只在**单条消息字符串**内部使用；绝不对序列化后的整段 JSON 跑它（那会跨
// 消息/跨 JSON 对象边界删除，见 F-10 Bug C）。
function __wbStripLegacyString(s){
  if(typeof s!=="string")return s;
  const tags=["tool_usage_policy","result_presentation","final_answer_instructions","task_management","agent_skills","quality_policy","agent_loop","regional_conventions"];
  for(const t of tags){s=s.replace(new RegExp("<"+t+"\\b[^>]*>[\\s\\S]*?<\\/"+t+">\\s*","g"),"");}
  s=s.replace(/<current_time>[\s\S]*?<\/current_time>\s*/g,"");
  s=s.replace(/<additional_data>[\s\S]*?<\/additional_data>\s*/g,"");
  s=__wbDropReminder(s,"additional-data",true);
  return s;
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
// 单个 content part 内删掉所有完整的 <system-reminder>…</system-reminder> 后剩下的文本。
// 这里只处理一条 part 字符串，不跨消息。
function __wbReminderResidue(text){
  return String(text).replace(/<system-reminder\b[^>]*>[\s\S]*?<\/system-reminder>/g,"").trim();
}
// E2/E5: JSON 安全清洗。role!=="user" 的消息永不改写（assistant/tool 历史里模型
// 自己讨论这些标签时是正常内容）。tool_result 的数据永不改写。
function __wbScrubMessageObject(it,legacy,ctx,keepCtx){
  if(!it||typeof it!=="object")return it;
  if(it.role!=="user")return it;
  let changed=false;
  const fix=(s)=>{
    let t=legacy(s);
    if(!keepCtx)t=ctx(t);
    if(t!==s)changed=true;
    return t;
  };
  const c=it.content;
  if(typeof c==="string"){
    const n=fix(c);
    return changed?Object.assign({},it,{content:n}):it;
  }
  if(Array.isArray(c)){
    const parts=c.map((p)=>{
      if(!p||typeof p!=="object")return p;
      if(typeof p.text==="string"&&(p.type==="text"||p.type==="input_text"||p.type==="output_text")){
        const n=fix(p.text);
        return n===p.text?p:Object.assign({},p,{text:n});
      }
      return p;
    });
    return changed?Object.assign({},it,{content:parts}):it;
  }
  return it;
}
function __wbScrubJsonText(text){
  if(typeof text!=="string"||!text)return {text:text,changed:false,parsed:false};
  let obj;
  try{obj=JSON.parse(text);}catch(e){return {text:text,changed:false,parsed:false};}
  if(!obj||typeof obj!=="object"||Array.isArray(obj))return {text:text,changed:false,parsed:true};
  let changed=false;
  const legacy=(s)=>__wbStripLegacyString(s);
  const ctx=(s)=>__wbStripUserContext(s);
  for(const key of ["messages","input"]){
    const arr=obj[key];
    if(!Array.isArray(arr))continue;
    let users=0;
    for(const it of arr){if(it&&typeof it==="object"&&it.role==="user")users++;}
    // 与 modelInput 层一致：整段只有一个 user 消息时保留那一次 user-context。
    const keepFirstContext=users<=1;
    let firstUserSeen=false;
    const out=[];
    for(const it of arr){
      if(typeof it==="string"){
        const n=legacy(it);
        if(n!==it)changed=true;
        out.push(n);
        continue;
      }
      if(!it||typeof it!=="object"){out.push(it);continue;}
      let keepCtx=true;
      if(it.role==="user"){keepCtx=keepFirstContext&&!firstUserSeen;firstUserSeen=true;}
      const nu=__wbScrubMessageObject(it,legacy,ctx,keepCtx);
      if(nu!==it)changed=true;
      out.push(nu);
    }
    obj[key]=out;
  }
  for(const key of ["instructions","system"]){
    if(typeof obj[key]==="string"){
      const n=__wbStripUserContext(__wbStripLegacyString(obj[key]));
      if(n!==obj[key]){obj[key]=n;changed=true;}
    }
  }
  if(!changed)return {text:text,changed:false,parsed:true};
  try{return {text:JSON.stringify(obj),changed:true,parsed:true};}
  catch(e){return {text:text,changed:false,parsed:true};}
}
function __wbScrubInit(init){
  if(!init||typeof init.body!=="string"||init.body.length<1)return init;
  const st=__wbStats();
  st.calls++;
  try{
    if(init.body.indexOf("<")<0){st.skipped++;return init;}
    const r=__wbScrubJsonText(init.body);
    if(r.parsed)st.parsed++;else st.parseFailed++;
    if(r.changed){
      st.rewritten++;
      st.bytesBefore+=init.body.length;
      st.bytesAfter+=r.text.length;
      return Object.assign({},init,{body:r.text});
    }
  }catch(e){st.errors++;try{st.lastError=String(e&&e.message||e);}catch(_){}}
  return init;
}
'''.strip()

# ---------------------------------------------------------------------------
# modelInput layer (runtime chunk only)
# ---------------------------------------------------------------------------
RUNTIME_JS = r'''
// 真实 input 元素是 {"role":"user","content":[{"type":"text","text":"..."}]}，
// 没有 type:"message"。v1 要求 type==="message"，于是把每条消息都当成"非消息"原样透传，
// 历史 user-context 从来没有被删掉过——用户看到的"每轮重发"就是它。
function __wbIsUser(it){return !!it&&it.role==="user"}
// E4 (F-10 Bug B): 只有在**全部** content part 都是纯 reminder 文本时才算"纯提醒"。
// tool_result / image / 普通文本 / 空数组 => false，整条消息永不丢弃。
function __wbIsReminderOnly(it){
  if(!__wbIsUser(it))return false;
  const pure=(t)=>{
    if(typeof t!=="string")return false;
    const s=t.trim();
    if(s.indexOf("<system-reminder")!==0)return false;
    if(s.indexOf("<user_query>")>=0)return false;
    return __wbReminderResidue(s)==="";
  };
  const c=it.content;
  if(typeof c==="string")return pure(c);
  if(!Array.isArray(c)||c.length===0)return false;
  for(const p of c){
    if(!p||typeof p!=="object")return false;
    if(p.type!=="text"&&p.type!=="input_text")return false;
    if(!pure(p.text))return false;
  }
  return true;
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
'''.strip()

HELPER = (HELPER_BEGIN + "\n" + HELPER_REV + "\n" + CLEANER_JS + "\n"
          + RUNTIME_JS + "\n" + HELPER_END)

# ---------------------------------------------------------------------------
# Egress blocks
# ---------------------------------------------------------------------------
OUTLET_JS = r'''
// E1 (F-01): the OpenAI SDK calls `this.fetch.call(void 0, url, init)` where the
// fetch it captured is this.axiosToFetchAdapter().  Wrapping the adapter return
// value puts the scrub on the one path every model request must take, no matter
// which Axios adapter wins.  `globalThis.fetch` is not involved.
function __wbOutletFetch(fn){
  if(typeof fn!=="function")return fn;
  if(fn.__wbOutletWrapped)return fn;
  const __wbWrapped=async function(input,init){
    return fn.call(this,input,__wbScrubInit(init));
  };
  __wbWrapped.__wbOutletWrapped=true;
  return __wbWrapped;
}
'''.strip()

OUTLET = (OUTLET_BEGIN + "\n" + CLEANER_JS + "\n" + OUTLET_JS + "\n" + OUTLET_END)

SCRUB_JS = r'''
// 次级网：其它调用方直接走 globalThis.fetch 时也能清一遍。
// 主出口是 transport chunk 里的 __wbOutletFetch（F-01：模型请求不走 fetch）。
function __wbScrubFetch(){
  try{
    const f=globalThis.fetch;
    if(typeof f!=="function"||f.__wbScrubbed)return;
    const __wbWrapped=function(input,init){
      return f.call(this,input,__wbScrubInit(init));
    };
    __wbWrapped.__wbScrubbed=true;
    globalThis.fetch=__wbWrapped;
  }catch(e){}
}
__wbScrubFetch();
'''.strip()

SCRUB = SCRUB_BEGIN + "\n" + SCRUB_JS + "\n" + SCRUB_END

# 旧版 fetch 劫持（v3）在升级时要被认出来并替换掉。
LEGACY_SCRUB_MARK = b"__wbScrubFetch"


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


# E8: node_modules 不再整棵跳过——_install.py 里存在
# node_modules/@genie/agent-harness/cli/ 布局。只放行 node_modules/@genie/…，
# 其余第三方包仍然跳过。扩展名也从 .mjs 扩到 .js。
SKIP_NAMES = {".git", "renderer", "locales", "assets"}
MAX_DEPTH = 6
MAX_FILE = 24 * 1024 * 1024


def _walk_allowed(rel_parts):
    """rel_parts: 当前目录相对 base 的路径分量（含待判断的目录名）。"""
    for i, part in enumerate(rel_parts):
        if part.lower() != "node_modules":
            continue
        if i + 1 >= len(rel_parts):
            return True          # 需要进入 node_modules 才能看到它的孩子
        if rel_parts[i + 1].lower() != "@genie":
            return False         # 非 @genie 的包一律不进
    return True


def carries_patchable(path):
    """True when this file carries a patchable anchor (runtime or transport)."""
    try:
        if os.path.getsize(path) > MAX_FILE:
            return False
        data = read(path)
    except OSError:
        return False
    if b'modelInput.instructions' in data and b'modelInput.input' in data:
        return True
    return OUTLET_INSERT_ANCHOR in data and (OUTLET_CALL in data or OUTLET_WRAPPED_CALL in data)


def find_targets(pattern):
    """Resolve a chunk path / glob / directory to the patchable chunk(s).

    Never trust the file name.  A WorkBuddy update renames the chunk
    (``1849.<hash>.mjs`` -> another id/hash) and another machine may install to
    a different folder (D:\\, the AI edition, a per-user install).  Only the
    content identifies the file, so a directory is walked recursively until a
    file carrying ``modelInput.*`` (runtime) or the Axios egress adapter
    (transport) shows up.
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
        rel = os.path.relpath(root, base)
        rel_parts = [] if rel == "." else rel.split(os.sep)
        dirs[:] = [d for d in dirs
                   if d.lower() not in SKIP_NAMES and _walk_allowed(rel_parts + [d])]
        for name in sorted(files):
            if not (name.endswith(".mjs") or name.endswith(".js")):
                continue
            path = os.path.join(root, name)
            if carries_patchable(path):
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


# ---------------------------------------------------------------------------
# Injection sites (E7: optional chaining + "," or "}" terminator)
# ---------------------------------------------------------------------------
_IDENT = rb"[A-Za-z$_][\w$]*"
# E7: 同时接受 . 与 ?.（可选链），并把**完整访问器表达式**原样捕获，
# 替换时逐字复用，绝不把 `g?.modelInput?.instructions` 改写成 `g.modelInput.instructions`
# （那会把"安全短路"变成 TypeError）。
_ACCESS_INS = rb"(?P<expr>(?P<ident>" + _IDENT + rb")(?:\?\.|\.)modelInput(?:\?\.|\.)instructions)"
_ACCESS_IN = rb"(?P<expr>(?P<ident>" + _IDENT + rb")(?:\?\.|\.)modelInput(?:\?\.|\.)input)"

PAT_INS_RAW = re.compile(
    rb"(?<![A-Za-z0-9_$])systemInstructions:((?!__wbStripLegacyAny\()"
    + _ACCESS_INS + rb")(?P<term>[,}])")
PAT_IN_RAW = re.compile(
    rb"(?<![A-Za-z0-9_$])input:((?!__wbCleanRuntimeInput\()"
    + _ACCESS_IN + rb")(?P<term>[,}])")
PAT_INS_WRAPPED = re.compile(
    rb"systemInstructions:__wbStripLegacyAny\(" + _ACCESS_INS + rb"\)(?P<term>[,}])")
PAT_IN_WRAPPED = re.compile(
    rb"input:__wbCleanRuntimeInput\(" + _ACCESS_IN + rb"\)(?P<term>[,}])")

RE_INS_RAW = PAT_INS_RAW
RE_IN_RAW = PAT_IN_RAW


def _is_object_key(data, i):
    """'input:' 必须是一个对象字面量的键（前面是 { 或 ,），不是 previousResponseId:"input"。"""
    j = i - 1
    while j >= 0 and data[j:j + 1] in b" \t\r\n":
        j -= 1
    return j < 0 or data[j:j + 1] in (b"{", b",")


def _raw_sites(data, kind):
    pat = PAT_INS_RAW if kind == "instructions" else PAT_IN_RAW
    out = []
    for m in pat.finditer(data):
        if kind == "input" and not _is_object_key(data, m.start()):
            continue
        out.append(m)
    return out


def _inject_diagnosis(data):
    """E7: 失败时给出可读的"探过哪些形态 / 实际见到什么"。"""
    return ("probed systemInstructions:<id>[?.]modelInput[?.]instructions(,|}) and "
            "input:<id>[?.]modelInput[?.]input(,|}); chunk has "
            "systemInstructions:%d modelInput.instructions:%d modelInput.input:%d "
            "wrapped:%d"
            % (data.count(b"systemInstructions:"), data.count(b".modelInput.instructions"),
               data.count(b".modelInput.input"), data.count(b"__wbStripLegacyAny(")))


def _dynamic_inject_points(data):
    """用对混淆名不敏感的结构化正则定位注入点，返回 [(kind, expr, start, end, term), …]。

    kind    : "instructions" | "input"
    expr    : chunk 里的**完整访问器表达式**（`g.modelInput.instructions` 或
              `g?.modelInput?.instructions`），逐字复用，不按标识符重建
    term    : 原始结尾字符 b"," 或 b"}"（E7：对象末尾属性后跟 } 也要能包）
    """
    points = []
    for m in _raw_sites(data, "instructions"):
        points.append(("instructions", m.group("expr"), m.start(), m.end(), m.group("term")))
    for m in _raw_sites(data, "input"):
        points.append(("input", m.group("expr"), m.start(), m.end(), m.group("term")))

    seen, out = set(), []
    for kind, expr, s, e, term in points:
        key = (kind, s)
        if key in seen:
            continue
        seen.add(key)
        out.append((kind, expr, s, e, term))
    return out


def _injection_groups(data):
    """按"承载对象标识符"归组所有 modelInput 注入点：{ident: {(kind, state), …}}。"""
    groups = {}
    for kind, pats in (("instructions", (("raw", PAT_INS_RAW), ("wrapped", PAT_INS_WRAPPED))),
                       ("input", (("raw", PAT_IN_RAW), ("wrapped", PAT_IN_WRAPPED)))):
        for state, pat in pats:
            for m in pat.finditer(data):
                if state == "raw" and kind == "input" and not _is_object_key(data, m.start()):
                    continue
                groups.setdefault(m.group("ident"), set()).add((kind, state))
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
    """模块 ID 从安装的 chunk 文件名 / 内容里发现，不假设 "1849"。"""
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
    """定位模块头的 rspack id 语句，按语句边界取，不写死 id。"""
    for prefix in (ANCHOR_PREFIX, b"export const __rspack_esm_id="):
        i = data.find(prefix)
        if i < 0:
            continue
        j = data.find(b";", i)
        if j < 0:
            continue
        return data[i:j + 1]
    return None


ANCHOR_PREFIX = b"export const __rspack_esm_ids=["


def _segment(data, begin, end):
    """返回 (整段字节, start, end)；缺任一标记返回 (None, -1, -1)。"""
    b = data.find(begin.encode())
    if b < 0:
        return None, -1, -1
    e = data.find(end.encode(), b)
    if e < 0:
        return None, -1, -1
    e += len(end)
    return data[b:e], b, e


def probe_adapter_anchor(data):
    """E1/E10: 报告 Axios 默认 adapter 列表锚点是否存在（方案 3 的可行性证据）。

    方案 3（只留 "fetch"）在本 build 里锚点存在，但已实测被否决：axios 的 fetch
    adapter 对流式响应返回 Web ReadableStream，而 axiosToFetchAdapter 的流式分支
    只接受 Node Readable（`n.data.pipe` / `n.on("data")`），改过去会把 SSE 响应
    变成 `JSON.stringify(stream)==="{}"`。所以这里只做探测/汇报，不做替换。
    """
    return [a.decode("ascii") for a in ADAPTER_ANCHORS if a in data]


# ---------------------------------------------------------------------------
# Runtime patch
# ---------------------------------------------------------------------------
def _ensure_runtime_block(data, log):
    """E6: helper 整段替换；SCRUB 永远是 HELPER_END 之后的独立段落。

    旧版（v3 及更早）把 SCRUB 插在 /*__wbTrimEnd*/ 里面，下一次 helper 整段替换
    会把它一起删掉。这里先把旧 SCRUB 剪出来（无论在哪），替换 helper，再把它
    放回 HELPER_END 之后。
    """
    seg, sb, se = _segment(data, SCRUB_BEGIN, SCRUB_END)
    hb = data.find(HELPER_BEGIN.encode())
    he = data.find(HELPER_END.encode(), hb) if hb >= 0 else -1
    scrub_inside = seg is not None and he >= 0 and sb < he
    scrub_old = seg is not None and seg != SCRUB.encode()

    if seg is None and LEGACY_SCRUB_MARK in data:
        # 有 __wbScrubFetch 却没有任何 scrub 段标记（v3 极早期）：再插一份就会出现
        # 两个 __wbScrubFetch 定义。让用户先回到原始 chunk。
        _fail_with_context(data, "chunk has __wbScrubFetch but no /*__wbTrimScrub*/ marker; "
                           "run --restore and re-apply", LEGACY_SCRUB_MARK)

    if seg is not None:
        data = data[:sb] + data[se:]

    hb = data.find(HELPER_BEGIN.encode())
    he = data.find(HELPER_END.encode(), hb)
    if hb < 0 or he < 0:
        _fail_with_context(data, "helper markers in an already-patched chunk",
                           HELPER_BEGIN.encode())
    end = he + len(HELPER_END)
    helper_ok = data[hb:end] == HELPER.encode()
    if helper_ok:
        log.append(("helper", "already"))
    else:
        data = data[:hb] + HELPER.encode() + data[end:]
        log.append(("helper-upgrade", "patched"))

    hb = data.find(HELPER_BEGIN.encode())
    he = data.find(HELPER_END.encode(), hb) + len(HELPER_END)
    data = data[:he] + b"\n" + SCRUB.encode() + data[he:]
    if scrub_inside:
        log.append(("fetch-scrub", "relocated outside helper"))
    elif scrub_old:
        log.append(("fetch-scrub", "upgraded"))
    elif seg is not None and helper_ok:
        log.append(("fetch-scrub", "already"))
    else:
        log.append(("fetch-scrub", "patched"))
    return data


def patch_runtime(data, path=""):
    log = []
    if HELPER_BEGIN.encode() not in data:
        anchor = _anchor_of(data)
        if not anchor:
            _fail_with_context(data, "module header (__rspack_esm_ids / __rspack_esm_id)",
                               b"__rspack_esm_id")
        data = data.replace(anchor, anchor + HELPER.encode() + b"\n" + SCRUB.encode(), 1)
        log.append(("helper", "patched"))
        log.append(("fetch-scrub", "patched"))
    else:
        data = _ensure_runtime_block(data, log)

    points = _dynamic_inject_points(data)
    ins = [p for p in points if p[0] == "instructions"]
    inp = [p for p in points if p[0] == "input"]

    # 结构化对称性检查只对"已经打过补丁"的形态生效（此时每个对象两种调用点都应已包好）。
    if not ins and not inp:
        gaps = _symmetry_gaps(data)
        if gaps:
            detail = "; ".join("%s: %s" % (ident.decode("ascii", "replace"), why)
                               for ident, why in gaps)
            _fail_with_context(data, "modelInput injection sites are asymmetric (%s); %s"
                               % (detail, _inject_diagnosis(data)),
                               b".modelInput.instructions,")

    if not ins:
        if RE_INS_RAW.search(data) or data.count(b"__wbStripLegacyAny(") == 0:
            _fail_with_context(data, "systemInstructions:<ident>.modelInput.instructions[,}]; "
                               + _inject_diagnosis(data), b"systemInstructions:")
        log.append(("instructions", "already"))
    if not inp:
        if RE_IN_RAW.search(data) or data.count(b"__wbCleanRuntimeInput(") == 0:
            _fail_with_context(data, "input:<ident>.modelInput.input[,}]; " + _inject_diagnosis(data),
                               b"modelInput.input")
        log.append(("input", "already"))

    ids, evidence = discover_module_ids(data, path)
    if evidence:
        log.append(("module-id", ", ".join("%s->%s" % (src, v) for src, v in evidence)))

    # 从后往前替换：偏移量不受前面替换影响。只替换每个站点的精确字节区间。
    # E7: expr 是原样的访问器（含 ?.），不重建、不改写短路语义。
    edits = []
    for kind, expr, s, e, term in ins:
        new = b"systemInstructions:__wbStripLegacyAny(" + expr + b")" + term
        edits.append((s, e, new, "instructions(%s)" % expr.decode("ascii", "replace")))
    for kind, expr, s, e, term in inp:
        new = b"input:__wbCleanRuntimeInput(" + expr + b")" + term
        edits.append((s, e, new, "input(%s)" % expr.decode("ascii", "replace")))

    for s, e, new, label in sorted(edits, key=lambda x: x[0], reverse=True):
        data = data[:s] + new + data[e:]
        log.append((label, "patched"))

    # 收口：替换后必须一处 raw 都不剩；还剩就硬失败（而不是打一半算成功）。
    leftover = len(RE_INS_RAW.findall(data)) + len(RE_IN_RAW.findall(data))
    if leftover:
        raise SystemExit("[FAIL] %d injection site(s) still un-wrapped after patch" % leftover)

    gaps = _symmetry_gaps(data)
    if gaps:
        detail = "; ".join("%s: %s" % (ident.decode("ascii", "replace"), why)
                           for ident, why in gaps)
        _fail_with_context(data, "patch left asymmetric injection sites (%s)" % detail,
                           b".modelInput.")
    return data, log


# ---------------------------------------------------------------------------
# Transport patch (E1)
# ---------------------------------------------------------------------------
def patch_transport(data, path=""):
    log = []
    if OUTLET_BEGIN.encode() in data:
        seg, b, e = _segment(data, OUTLET_BEGIN, OUTLET_END)
        if seg is None:
            _fail_with_context(data, "outlet markers in an already-patched chunk",
                               OUTLET_BEGIN.encode())
        if seg == OUTLET.encode():
            log.append(("outlet", "already"))
        else:
            data = data[:b] + OUTLET.encode() + data[e:]
            log.append(("outlet", "upgraded"))
    else:
        i = data.find(OUTLET_INSERT_ANCHOR)
        if i < 0:
            _fail_with_context(data, "transport outlet insertion anchor "
                               "(var __webpack_modules__={)", b"__webpack_modules__")
        data = data[:i] + OUTLET.encode() + b"\n" + data[i:]
        log.append(("outlet", "patched"))

    n = data.count(OUTLET_CALL)
    if n == 0:
        if OUTLET_WRAPPED_CALL in data:
            log.append(("axios-fetch", "already"))
        else:
            _fail_with_context(data, "OpenAI client option fetch:this.axiosToFetchAdapter() "
                               "(probed __wbOutletFetch wrap)", b"axiosToFetchAdapter")
    else:
        data = data.replace(OUTLET_CALL, OUTLET_WRAPPED_CALL)
        log.append(("axios-fetch", "patched x%d" % n))
    return data, log


# ---------------------------------------------------------------------------
# Kind dispatch
# ---------------------------------------------------------------------------
def patch_kind(data):
    if b"modelInput.instructions" in data and b"modelInput.input" in data:
        return "runtime"
    if OUTLET_INSERT_ANCHOR in data and (OUTLET_CALL in data or OUTLET_WRAPPED_CALL in data):
        return "transport"
    return ""


def patch(data, path=""):
    kind = patch_kind(data)
    if kind == "runtime":
        return patch_runtime(data, path)
    if kind == "transport":
        return patch_transport(data, path)
    _fail_with_context(data, "no patchable anchor: need either "
                       "modelInput.instructions+modelInput.input (runtime) or "
                       "var __webpack_modules__={ plus fetch:this.axiosToFetchAdapter() (transport)",
                       b"__webpack_modules__")


def version_of(data):
    """0 = 原始；1 = v1；2 = v2；3 = v3（旧 helper，无 E3/E4 修复）；4 = 当前。

    旧 helper 里出现 __wbBlank / __wbDropReminder 不代表修好了：E3/E4 修的正是
    __wbDropReminder 的边界判断和 __wbIsReminderOnly 的判据，光有这两个名字
    一样是 v3。所以用 rev 标记区分，不靠函数名。
    """
    if HELPER_BEGIN.encode() in data:
        return TRIM_VERSION if HELPER_REV.encode() in data else 3
    if MARK.encode() in data:
        return 1
    return 0


def outlet_version_of(data):
    return 1 if OUTLET_BEGIN.encode() in data else 0


def is_patched(data):
    """E6: 两种 SCRUB 布局都算已打（位置不参与判定，升级时会被搬出去）。"""
    return (
        version_of(data) == TRIM_VERSION
        and not RE_INS_RAW.search(data)
        and not RE_IN_RAW.search(data)
        and data.count(b"__wbStripLegacyAny(") >= 1
        and data.count(b"__wbCleanRuntimeInput(") >= 1
        and data.count(b"__wbScrubFetch") >= 2
        and SCRUB_BEGIN.encode() in data
        and SCRUB_END.encode() in data
        and b"__wbBlank" in data
        and b"__wbDropReminder" in data
    )


def is_outlet_patched(data):
    return (OUTLET_BEGIN.encode() in data
            and OUTLET_END.encode() in data
            and OUTLET_WRAPPED_CALL in data
            and OUTLET_CALL not in data)


def scrub_layout_of(data):
    seg, sb, _se = _segment(data, SCRUB_BEGIN, SCRUB_END)
    if seg is None:
        return "missing"
    hb = data.find(HELPER_BEGIN.encode())
    he = data.find(HELPER_END.encode(), hb) if hb >= 0 else -1
    return "inside-helper (old)" if he >= 0 and sb < he else "outside-helper"


def _same_source(recorded, path):
    """备份元数据里的 source 与当前路径是否同一个文件（F-10 L798 修复）。

    比对绝对化 + 大小写不敏感 + 统一斜杠：Windows 上同一个 chunk 可能被写成
    ``D:\\...`` / ``d:/...`` / ``\\\\?\\D:\\...``，字面比较会找不到备份。
    """
    if not recorded or not path:
        return False
    def norm(p):
        p = str(p).replace("/", "\\").rstrip("\\")
        if p.startswith("\\\\?\\"):
            p = p[4:]
        return os.path.normcase(os.path.abspath(p))
    try:
        return norm(recorded) == norm(path)
    except Exception:
        return False


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
        if _same_source(meta.get("source"), path):
            cands.append((os.path.getmtime(fp), fp))
    return sorted(cands)[0][1] if cands else None


def backup(path, tag):
    os.makedirs(BACKUPS, exist_ok=True)
    stamp = _dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = os.path.join(BACKUPS, "%s-%s" % (stamp, tag))
    shutil.copy2(path, dst)
    with open(dst + ".json", "w", encoding="utf-8") as fh:
        json.dump({"source": os.path.abspath(path), "sha256": sha256(read(dst)), "tag": tag},
                  fh, ensure_ascii=False, indent=2)
    return dst


# ---------------------------------------------------------------------------
# Self-test
# ---------------------------------------------------------------------------
def _node_run(node, js_text, name):
    tmp = os.path.join(ROOT, name)
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(js_text)
    p = subprocess.run([node, tmp], capture_output=True, text=True)
    try:
        os.remove(tmp)
    except OSError:
        pass
    return p


def self_test():
    node = shutil.which("node")
    if not node:
        print("[FAIL] node not found")
        return 2
    rc = 0

    # --- E9.3 / E1: outlet anchor exists and the transport chunk patches -------------
    sample_transport = (
        b'import{fileURLToPath as __rspack_fileURLToPath}from"node:url";'
        b'var __webpack_modules__={6441(t,i,n){class X{buildOpenAIClientOptions(t,i){'
        b'return{baseURL:t,apiKey:i,fetch:this.axiosToFetchAdapter(),maxRetries:0}}'
        b'axiosToFetchAdapter(){return async(t,i)=>({ok:true})}}},49877(){}};'
    )
    try:
        out, log = patch_transport(sample_transport)
        assert OUTLET_BEGIN.encode() in out
        assert OUTLET_WRAPPED_CALL in out
        assert OUTLET_CALL not in out
        assert b"__wbOutletFetch" in out
        print("[PASS] E1 outlet anchor: transport chunk wraps axiosToFetchAdapter()")
    except Exception as exc:
        print("[FAIL] E1 outlet anchor: %s" % exc)
        rc = 1
        out = sample_transport
    path = os.path.join(ROOT, "tmp-outlet-anchor.mjs")
    with open(path, "wb") as fh:
        fh.write(out)
    ok, msg = node_check(path)
    try:
        os.remove(path)
    except OSError:
        pass
    if ok:
        print("[PASS] E1 outlet chunk parses (node --check)")
    else:
        print("[FAIL] E1 outlet chunk parse: %s" % msg)
        rc = 1

    # E9.3: the 方案 3 adapter list anchor is detectable (evidence only, not applied)
    probe = b'let e0={transitional:e$,adapter:["xhr","http","fetch"],transformRequest:[]}'
    if probe_adapter_anchor(probe):
        print("[PASS] E1 adapter-list anchor probe: %s" % ", ".join(probe_adapter_anchor(probe)))
    else:
        print("[FAIL] E1 adapter-list anchor probe found nothing")
        rc = 1

    first_turn = (
        '<system-reminder data-role="user-context">\n<user_info>U</user_info>\n</system-reminder>\n'
        "<tool_usage_policy>AAA</tool_usage_policy>\n<user_query>hello</user_query>"
    )
    second_turn = (
        '<system-reminder data-role="additional-data">\n<panel_tabs>x</panel_tabs>\n</system-reminder>\n'
        "<user_query>second</user_query>"
    )
    reminder_only = '<system-reminder data-role="hook">tick</system-reminder>'
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
    # E3: body text that *mentions* the keyword, with two reminder tags around it.
    keyword_body = ('A <system-reminder data-role="hook">tick</system-reminder> '
                    'B user-context C '
                    '<system-reminder data-role="hook">tock</system-reminder> D')
    real_ctx = '<system-reminder data-role="user-context">u</system-reminder>'

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
        # ---- E9.1 (E4): tool_result / image messages are never dropped -------------
        "const trMsg={role:'user',content:[{type:'tool_result',tool_use_id:'t1',content:'ok'},"
        "{type:'text',text:'<system-reminder data-role=\"hook\">tick</system-reminder>'}]};",
        "const o6=__wbCleanRuntimeInput([mk(%s),asst,trMsg]);" % json.dumps(first_turn),
        "if(o6.length!==3)throw new Error('E4: tool_result message dropped, got '+o6.length);",
        "if(!JSON.stringify(o6).includes('tool_result'))throw new Error('E4: tool_result payload lost');",
        "const imgMsg={role:'user',content:[{type:'image',source:{}},"
        "{type:'text',text:'<system-reminder data-role=\"hook\">x</system-reminder>'}]};",
        "const o7=__wbCleanRuntimeInput([mk(%s),asst,imgMsg]);" % json.dumps(first_turn),
        "if(o7.length!==3)throw new Error('E4: image message dropped, got '+o7.length);",
        "if(__wbIsReminderOnly({role:'user',content:[{type:'tool_result',tool_use_id:'t',content:'x'}]}))"
        "throw new Error('E4: tool_result-only message considered reminder-only');",
        # ---- E9.4 (E3): body text mentioning user-context is not deleted ----------
        "const kw=%s;" % json.dumps(keyword_body),
        "const kwOut=__wbStripUserContext(kw);",
        "if(kwOut!==kw)throw new Error('E3: keyword body rewritten: '+JSON.stringify(kwOut));",
        "if(!kwOut.includes('user-context')||!kwOut.includes('A ')||!kwOut.includes(' D'))"
        "throw new Error('E3: keyword body content lost');",
        "const rc1=__wbStripUserContext(%s);" % json.dumps(real_ctx),
        "if(rc1!=='')throw new Error('E3: real user-context block kept: '+JSON.stringify(rc1));",
        "const mixed=%s;" % json.dumps(real_ctx + " hello"),
        "const mixOut=__wbStripUserContext(mixed);",
        "if(mixOut.trim()!=='hello')throw new Error('E3: real block + text residue: '+JSON.stringify(mixOut));",
        # ---- E9.2 (E2/E5): cross-message current_time must not splice JSON --------
        "const cross={messages:[{role:'user',content:'<current_time>'},"
        "{role:'assistant',content:'mid'},{role:'user',content:'</current_time> hello'}]};",
        "const cr=__wbScrubJsonText(JSON.stringify(cross));",
        "if(!cr.parsed)throw new Error('E2: cross-message body failed to parse');",
        "if(cr.changed)throw new Error('E2: unpaired tags were rewritten');",
        "const cback=JSON.parse(cr.text);",
        "if(cback.messages.length!==3)throw new Error('E2: messages merged');",
        "if(!cback.messages[0].content.includes('<current_time>'))throw new Error('E2: opening tag lost');",
        "if(!cback.messages[2].content.includes('</current_time>'))throw new Error('E2: closing tag lost');",
        "const paired={messages:[{role:'user',content:'<current_time>t</current_time> hello'},"
        "{role:'assistant',content:'<current_time>keep</current_time>'}]};",
        "const pr=__wbScrubJsonText(JSON.stringify(paired));",
        "const pback=JSON.parse(pr.text);",
        "if(pback.messages[0].content.trim()!=='hello')throw new Error('E2: paired tag not stripped: '+pback.messages[0].content);",
        "if(!pback.messages[1].content.includes('keep'))throw new Error('E5: assistant history rewritten');",
        "const notJson='data: {\"a\":1}';",
        "const nj=__wbScrubJsonText(notJson);",
        "if(nj.changed||nj.text!==notJson)throw new Error('E2: non-JSON body was modified');",
        # ---- legacy fetch scrub still JSON-safe ----------------------------------
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
    p = _node_run(node, HELPER + "\n" + SCRUB + "\n" + "\n".join(lines) + "\n",
                  "tmp-helper-test.mjs")
    print((p.stdout or "") + (p.stderr or ""))
    if p.returncode != 0:
        rc = 1
    else:
        print("[PASS] runtime helper + scrub self-test")

    # --- outlet self-test (separate file: OUTLET re-declares the shared cleaners) ---
    outlet_lines = [
        "const mk=(t)=>({role:'user',content:[{type:'text',text:t}]});",
        "const exact=%s;" % json.dumps(exact),
        "let seen=null;",
        "const fake=async(u,i)=>{seen=i&&i.body;return{ok:true};};",
        "const w=__wbOutletFetch(fake);",
        "if(w!==__wbOutletFetch(w))throw new Error('outlet double-wrapped');",
        "await w('x',{method:'POST',body:JSON.stringify({messages:[{role:'user',content:[{type:'text',text:exact}]}]})});",
        "if(!seen||seen.includes('current_time')||seen.includes('additional-data'))throw new Error('outlet body not scrubbed: '+seen);",
        "if(!seen.includes('user_query'))throw new Error('outlet lost the user query');",
        "const parsed=JSON.parse(seen);",
        "if(parsed.messages.length!==1)throw new Error('outlet changed message count');",
        "const st=__wbStats();",
        "if(st.calls<1||st.parsed<1||st.rewritten<1)throw new Error('outlet stats not counted: '+JSON.stringify(st));",
        "seen=null;",
        "const skippedBefore=__wbStats().skipped;",
        "await w('x',{method:'POST',body:'data: not-json'});",
        "if(seen!=='data: not-json')throw new Error('outlet rewrote a non-JSON body');",
        "if(__wbStats().skipped<=skippedBefore)throw new Error('outlet skip not counted');",
        "seen=null;",
        "await w('x',{method:'POST',body:'data: <not-json>'});",
        "if(seen!=='data: <not-json>')throw new Error('outlet rewrote an unparseable body');",
        "if(__wbStats().parseFailed<1)throw new Error('outlet parse-failure not counted');",
        "seen=null;",
        "await w('x',{method:'POST',body:new Uint8Array([1,2,3])});",
        "if(!(seen instanceof Uint8Array))throw new Error('outlet touched a binary body');",
        "const trMsg={role:'user',content:[{type:'tool_result',tool_use_id:'t',content:'ok'},"
        "{type:'text',text:'<system-reminder data-role=\"hook\">tick</system-reminder>'}]};",
        "seen=null;",
        "await w('x',{method:'POST',body:JSON.stringify({messages:[trMsg]})});",
        "const tb=JSON.parse(seen);",
        "if(tb.messages.length!==1)throw new Error('outlet changed message count');",
        "const keptParts=tb.messages[0].content.filter(p=>p&&p.type==='tool_result');",
        "if(keptParts.length!==1||keptParts[0].tool_use_id!=='t')throw new Error('outlet dropped tool_result');",
        "const kw=%s;" % json.dumps(keyword_body),
        "seen=null;",
        "await w('x',{method:'POST',body:JSON.stringify({messages:[mk(kw)]})});",
        "if(!JSON.parse(seen).messages[0].content[0].text.includes('user-context'))"
        "throw new Error('outlet deleted body text mentioning user-context');",
        "console.log('OUTLET SELF TEST OK');",
    ]
    p2 = _node_run(node, OUTLET + "\n" + "\n".join(outlet_lines) + "\n",
                   "tmp-outlet-test.mjs")
    print((p2.stdout or "") + (p2.stderr or ""))
    if p2.returncode != 0:
        rc = 1
    else:
        print("[PASS] transport outlet self-test")
    return rc


# ---------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser()
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--apply", action="store_true")
    g.add_argument("--restore", action="store_true")
    g.add_argument("--status", action="store_true")
    g.add_argument("--self-test", action="store_true")
    ap.add_argument("--file", default=DEFAULT_GLOB,
                    help="chunk path, glob, or a directory (e.g. <install>\\resources\\app.asar.unpacked\\cli\\dist); "
                         "content discovery finds the runtime chunk and the transport chunk whatever they are named")
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
              "modelInput.instructions / modelInput.input (runtime) or "
              "var __webpack_modules__={ + fetch:this.axiosToFetchAdapter() (transport).")
        print("       pass --file <chunk|glob|directory>, or set WORKBUDDY_INSTALL.")
        return 2

    rc = 0
    for path in targets:
        data = read(path)
        kind = patch_kind(data)
        print("file:   %s" % path)
        print("bytes:  %d" % len(data))
        print("sha256: %s" % sha256(data))
        print("kind:   %s" % (kind or "unknown"))
        if args.status:
            ids, evidence = discover_module_ids(data, path)
            if evidence:
                print("module: %s" % ", ".join("%s->%s" % (src, v) for src, v in evidence))
            if kind == "transport":
                ok = is_outlet_patched(data)
                print("version: %d (expected 1)" % outlet_version_of(data))
                print("adapter-anchor: %s" % (", ".join(probe_adapter_anchor(data)) or "none"))
                print("status: %s" % ("PATCHED" if ok else "NOT PATCHED"))
                rc = 0 if ok else 1
                continue
            ok = is_patched(data)
            print("version: %d (expected %d)" % (version_of(data), TRIM_VERSION))
            print("scrub-layout: %s" % scrub_layout_of(data))
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
                    if _same_source(m.get("source"), path):
                        cands.append((os.path.getmtime(fp), fp))
            if not cands:
                print("[FAIL] no backup for %s" % path)
                rc = 2
                continue
            # earliest backup = the original chunk（v3 就地升级后 --restore 也回到它，
            # 而不是回到"升级前那一版带旧 bug 的补丁"）
            src = sorted(cands)[0][1]
            if args.dry_run:
                print("[dry-run] would restore from %s" % src)
                continue
            shutil.copy2(src, path)
            print("restored: %s -> %s" % (src, path))
            print("sha256:   %s" % sha256(read(path)))
            continue

        already = is_outlet_patched(data) if kind == "transport" else is_patched(data)
        if already:
            print("status: already patched (v%d); nothing to do"
                  % (1 if kind == "transport" else TRIM_VERSION))
            continue

        ver = version_of(data)
        if kind == "runtime" and ver == 1:
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
