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
<install>/resources/app.asar.unpacked/cli/dist/lazy-lite-wb/1849.*.mjs
(the agent runtime chunk that builds `modelInput.instructions` / `.input`).

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
DEFAULT_GLOB = r"C:\Program Files\WorkBuddy\resources\app.asar.unpacked\cli\dist\lazy-lite-wb\1849.*.mjs"

MARK = "__wbStripLegacyString"
TRIM_VERSION = 2
HELPER_BEGIN = "/*__wbTrimV2*/"
HELPER_END = "/*__wbTrimEnd*/"
ANCHOR = b'export const __rspack_esm_ids=["1849"];'

HELPER = r'''/*__wbTrimV2*/
function __wbStripLegacyString(s){
  if(typeof s!=="string")return s;
  const tags=["tool_usage_policy","result_presentation","final_answer_instructions","task_management","agent_skills","quality_policy","agent_loop","regional_conventions"];
  for(const t of tags){s=s.replace(new RegExp("<"+t+"\\b[^>]*>[\\s\\S]*?<\\/"+t+">\\s*","g"),"");}
  s=s.replace(/<current_time>[\s\S]*?<\/current_time>\s*/g,"");
  s=s.replace(/<additional_data>[\s\S]*?<\/additional_data>\s*/g,"");
  return s;
}
function __wbStripUserContext(s){
  return typeof s==="string"?s.replace(/<system-reminder\s+data-role="user-context">[\s\S]*?<\/system-reminder>\s*/g,""):s;
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


def find_targets(pattern):
    if os.path.isfile(pattern):
        return [pattern]
    return sorted(glob.glob(pattern))


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
        data = data.replace(ANCHOR, ANCHOR + HELPER.encode(), 1)
        log.append(("helper", "patched"))
    else:
        log.append(("helper", "already"))

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
    """0 = 原始；1 = v1（判据错误，历史 user-context 从未被删）；2 = 当前版本。"""
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
    lines = [
        "const mk=(t)=>({role:'user',content:[{type:'text',text:t}]});",
        "const asst={role:'assistant',content:[{type:'text',text:'<current_time>keep me</current_time>'}]};",
        "const two=[mk(%s),asst,mk(%s)];" % (json.dumps(first_turn), json.dumps(second_turn)),
        "const t=JSON.stringify(__wbCleanRuntimeInput(two));",
        "if(t.includes('user-context'))throw new Error('historical user-context kept');",
        "if(t.includes('tool_usage_policy'))throw new Error('legacy tag kept');",
        "if(!t.includes('hello')||!t.includes('second'))throw new Error('real query lost');",
        "if(!t.includes('panel_tabs'))throw new Error('current turn additional-data lost');",
        "if(!t.includes('keep me'))throw new Error('assistant history was rewritten');",
        "const one=[mk(%s)];" % json.dumps(first_turn),
        "if(!JSON.stringify(__wbCleanRuntimeInput(one)).includes('user_info'))throw new Error('first turn lost context');",
        "const four=[mk(%s),asst,mk(%s),mk(%s)];" % (
            json.dumps(first_turn), json.dumps(second_turn), json.dumps(reminder_only)),
        "const o4=__wbCleanRuntimeInput(four);",
        "if(o4.length!==3)throw new Error('reminder-only message should be dropped, got '+o4.length);",
        "const sys=__wbStripLegacyAny('<current_time>now</current_time><user_query>x</user_query>');",
        "if(sys.includes('current_time'))throw new Error('system current_time kept');",
        "console.log('SELF TEST OK');",
    ]
    js = HELPER + "\n" + "\n".join(lines) + "\n"
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
    ap.add_argument("--file", default=DEFAULT_GLOB, help="chunk path or glob")
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
