#!/usr/bin/env node
// sync-release.js —— 把源目录的提示词与部署链路镜像到 release/（公开包）。
//
// 设计原则：提示词只在源目录改；release/ 是产物。这个脚本是唯一被允许改 release/ 提示词的地方，
// 所以它只做一件事：让 release/ 与源逐字节相同（多出来的文件会被删掉，删了会打印出来）。
//
// 用法（必须在源目录根跑，即 scripts\.. 下有 release\）：
//   node scripts/sync-release.js            # 同步并打印改动
//   node scripts/sync-release.js --dry-run  # 只看会改什么
//
// 之后按发布流程：node scripts/release-audit.js → cd release && git add/commit/tag/push
"use strict";
const fs = require("fs");
const path = require("path");
const { SYNC_DIRS, SYNC_FILES, SYNC_IGNORE } = require("./release-manifest");

const ROOT = path.resolve(__dirname, "..");
const TARGET = path.join(ROOT, "release");
const dry = process.argv.includes("--dry-run");

if (!fs.existsSync(path.join(TARGET, "scripts", "deploy.ps1"))) {
  console.error("这不是源目录：%s 下没有 release\\scripts\\deploy.ps1", ROOT);
  console.error("请在项目根（脚本的上一级）运行；公开包内部不需要同步。");
  process.exit(2);
}

const copied = [];
const removed = [];
const same = [];

function walk(dir, base = dir, out = []) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, entry.name);
    if (entry.isDirectory()) walk(p, base, out);
    else if (entry.isFile()) out.push(path.relative(base, p));
  }
  return out;
}

function sameBytes(a, b) {
  if (!fs.existsSync(b)) return false;
  const sa = fs.statSync(a);
  const sb = fs.statSync(b);
  if (sa.size !== sb.size) return false;
  return fs.readFileSync(a).equals(fs.readFileSync(b));
}

function ensureDir(file) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
}

for (const dir of SYNC_DIRS) {
  const srcDir = path.join(ROOT, dir);
  const dstDir = path.join(TARGET, dir);
  if (!fs.existsSync(srcDir)) {
    console.error("源目录缺少 %s，跳过", dir);
    continue;
  }
  const keep = (relPath) => !SYNC_IGNORE.some((pre) => (dir + "/" + relPath.split(path.sep).join("/")).startsWith(pre));
  const srcFiles = walk(srcDir).filter(keep);
  for (const rel of srcFiles) {
    const a = path.join(srcDir, rel);
    const b = path.join(dstDir, rel);
    const id = `${dir}/${rel.split(path.sep).join("/")}`;
    if (sameBytes(a, b)) { same.push(id); continue; }
    if (!dry) { ensureDir(b); fs.copyFileSync(a, b); }
    copied.push(id);
  }
  // 源里已经删掉的文件，包里也要删（否则包会带着一份无人引用的旧内容）
  if (fs.existsSync(dstDir)) {
    for (const rel of walk(dstDir).filter(keep)) {
      if (!srcFiles.includes(rel)) {
        const p = path.join(dstDir, rel);
        if (!dry) fs.rmSync(p);
        removed.push(`${dir}/${rel.split(path.sep).join("/")}`);
      }
    }
  }
}

for (const rel of SYNC_FILES) {
  const a = path.join(ROOT, rel);
  const b = path.join(TARGET, rel);
  if (!fs.existsSync(a)) { console.error("源文件缺少 %s，跳过", rel); continue; }
  if (sameBytes(a, b)) { same.push(rel); continue; }
  if (!dry) { ensureDir(b); fs.copyFileSync(a, b); }
  copied.push(rel);
}

const verb = dry ? "would copy" : "copied";
console.log(`sync-release  ${ROOT} -> ${TARGET}`);
console.log(`  ${verb}: ${copied.length}   deleted: ${removed.length}   unchanged: ${same.length}`);
for (const f of copied) console.log(`  ${verb.padEnd(10)} ${f}`);
for (const f of removed) console.log(`  ${(dry ? "would delete" : "deleted").padEnd(10)} ${f}`);
if (!copied.length && !removed.length) console.log("  已是最新，无需改动。");
console.log("\n下一步：node scripts/release-audit.js");
