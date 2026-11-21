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
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
BACKUPS = os.path.join(ROOT, "backups")
DEFAULT_GLOB = r"C:\Program Files\WorkBuddy\resources\app.asar.unpacked\cli\dist"

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
ANCHOR = b'export const __rspack_esm_ids=["1849"];'

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


def replace_once(data, old, new, label):
    n = data.count(old)
    if n == 0:
        if data.count(new) >= 1:
            return data, "already"
        raise SystemExit("[FAIL] anchor not found: %s" % label)
    if n != 1:
        raise SystemExit("[FAIL] anchor %s matched %d times" % (label, n))
    return data.replace(old, new, 1), "patched"


def patch(data):
    log = []
    if MARK.encode() not in data:
        if ANCHOR not in data:
            raise SystemExit("[FAIL] module header anchor not found")
        data = data.replace(ANCHOR, ANCHOR + HELPER.encode() + SCRUB.encode(), 1)
        log.append(("helper", "patched"))
    else:
        # 已经打过补丁的 chunk 里，helper 是**旧版本**的代码。只加新东西不够：
        # 修好的 bug 在 helper 内部，必须整段换成仓库里的当前文本，否则永远停在旧逻辑。
        b = data.find(HELPER_BEGIN.encode())
        e = data.find(HELPER_END.encode())
        if b < 0 or e < b:
            raise SystemExit("[FAIL] helper markers not found in patched chunk")
        current = data[b:e + len(HELPER_END)]
        if b"__wbBlank" not in current:
            data = data.replace(current, HELPER.encode(), 1)
            log.append(("helper-upgrade", "patched"))
        else:
            log.append(("helper", "already"))
        if b"__wbScrubFetch" not in data:
            data = data.replace(HELPER_END.encode(), SCRUB.encode() + HELPER_END.encode(), 1)
            log.append(("fetch-scrub", "patched"))
        else:
            log.append(("fetch-scrub", "already"))

    for kind, var in (("non-stream", "g"), ("stream", "w")):
        old = ("systemInstructions:%s.modelInput.instructions," % var).encode()
        new = ("systemInstructions:__wbStripLegacyAny(%s.modelInput.instructions)," % var).encode()
        data, st = replace_once(data, old, new, "%s instructions" % kind)
        log.append(("%s-instructions" % kind, st))

        old = ("input:%s.modelInput.input," % var).encode()
        new = ("input:__wbCleanRuntimeInput(%s.modelInput.input)," % var).encode()
        data, st = replace_once(data, old, new, "%s input" % kind)
        log.append(("%s-input" % kind, st))
    return data, log


def version_of(data):
    """0 = 原始；1 = v1（判据错误，历史 user-context 从未被删）；2 = v2（role 判据/--file）；3 = v3（additional-data 空壳）。"""
    if HELPER_BEGIN.encode() in data:
        return TRIM_VERSION
    if MARK.encode() in data:
        return 1
    return 0


def is_patched(data):
    return (
        version_of(data) == TRIM_VERSION
        and data.count(b"systemInstructions:__wbStripLegacyAny(") == 2
        and data.count(b"input:__wbCleanRuntimeInput(") == 2
        # 出口清理与结构扫描是这一层的关键；缺了就算没打全（老 v3 只有正则版）。
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

    targets = find_targets(args.file)
    if not targets:
        print("[FAIL] no target matched %s" % args.file)
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

        new_data, log = patch(data)
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
