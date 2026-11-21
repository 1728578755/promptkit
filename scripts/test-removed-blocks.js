#!/usr/bin/env node
// test-removed-blocks.js —— 证明「已移除段」的断言真的会红。
//
// 为什么需要这个脚本：REMOVED_BLOCKS 是一组"不许出现"的标签，它有个天然的坏结局——
// 某天有人改坏了断言（或改坏了渲染），它依然天天通过，而没人发现它已经失效。
// 这里主动把一段被删的标签塞回模板副本里，跑同一份 render-check，**必须 exit 1**。
//
// 做法：在项目根建临时目录，用 junction 把 fragments / plugins 指回真树（不复制，省时间也不留两份内容），
// 只把 welcome-mode/*.tpl 复制过去并注入 canary，然后 `render-check.js --root <临时目录>`。
//
// 用法：
//   node scripts/test-removed-blocks.js          # 期望 exit 0（= 断言确实会红）
//   node scripts/test-removed-blocks.js --keep   # 留下临时目录用于人工查看
"use strict";
const fs = require("fs");
const os = require("os");
const path = require("path");
const { execFileSync } = require("child_process");

const ROOT = path.resolve(__dirname, "..");
const KEEP = process.argv.includes("--keep");
const CANARY = "<tool_usage_policy>canary: this tag was removed on 2026-11-21</tool_usage_policy>";

function contextFile(base) {
  for (const rel of ["examples/context.json", "release/examples/context.json"]) {
    const p = path.join(base, rel);
    if (fs.existsSync(p)) return p;
  }
  throw new Error("examples/context.json not found under " + base);
}

function link(target, linkPath) {
  // Windows 上用 junction（不需要管理员权限）；其它平台用目录软链。
  fs.symlinkSync(target, linkPath, process.platform === "win32" ? "junction" : "dir");
}

function main() {
  const tmp = fs.mkdtempSync(path.join(ROOT, ".tmp-removed-proof-"));
  let code = 0;
  try {
    fs.mkdirSync(path.join(tmp, "welcome-mode"));
    fs.mkdirSync(path.join(tmp, "examples"));
    link(path.join(ROOT, "fragments"), path.join(tmp, "fragments"));
    link(path.join(ROOT, "plugins"), path.join(tmp, "plugins"));
    fs.copyFileSync(contextFile(ROOT), path.join(tmp, "examples", "context.json"));

    const tplDir = path.join(ROOT, "welcome-mode");
    for (const name of fs.readdirSync(tplDir).filter((f) => f.endsWith(".tpl"))) {
      fs.copyFileSync(path.join(tplDir, name), path.join(tmp, "welcome-mode", name));
    }
    const target = path.join(tmp, "welcome-mode", "work.tpl");
    fs.appendFileSync(target, CANARY + "\n", "utf8");

    let out = "";
    let exit = 0;
    try {
      out = execFileSync(
        process.execPath,
        [path.join(ROOT, "scripts", "render-check.js"), "--profile", "cn", "--root", tmp],
        { encoding: "utf8" }
      );
    } catch (e) {
      exit = e.status === undefined ? 1 : e.status;
      out = (e.stdout || "") + (e.stderr || "");
    }

    const failures = (out.match(/RENDER FAILURES: (\d+)/) || [])[1];
    const hitCraft = /work\/craft:.*removed_blocks=tool_usage_policy:1/.test(out);
    console.log(`render-check --root <canary tree>  exit=${exit}  failures=${failures || 0}  canary_detected=${hitCraft}`);
    if (exit !== 1) {
      console.error(`FAIL: 注入 canary 后 render-check 仍返回 ${exit}（应为 1）——"不许出现"的断言已经失效`);
      code = 1;
    } else if (!hitCraft) {
      console.error("FAIL: render-check 红了，但不是因为 tool_usage_policy（可能红在别的断言上）");
      code = 1;
    } else {
      console.log("OK: 断言会红，且红因正确");
    }
  } finally {
    if (KEEP) {
      console.log("kept: " + tmp);
    } else {
      // junction 用 rm -rf 不会跟进去删目标（Node 只删链接本身）
      fs.rmSync(tmp, { recursive: true, force: true });
    }
  }
  process.exit(code);
}

main();
