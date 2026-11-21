#!/usr/bin/env node
// _deps.js —— 「同一份脚本、两种布局」的唯一依赖 / 数据根解析处。
//
// 同一批诊断脚本要同时活在两棵树里，并且**逐字节相同**：
//   · 公开包根        <pkg>/scripts/x.js   → node_modules/ 与 examples/ 就在 <pkg> 下
//   · 维护者工作区    <src>/scripts/x.js   → 内容树在 <src>/release/ 下，依赖也在那里
//
// 以前每个脚本各写一遍候选路径，结果两边实现漂移（一边有 --root、一边没有），
// 用户看到的就是「公开包跟源目录行为不一样」。现在只在这里判断一次。
"use strict";
const fs = require("fs");
const path = require("path");

const SCRIPTS_DIR = __dirname;
const PARENT = path.resolve(SCRIPTS_DIR, "..");
// 候选根，按优先级：自己这棵树 → 工作区里的 release/（包内不存在，自然跳过）
const ROOTS = [PARENT, path.join(PARENT, "release")];

// nunjucks 只在渲染类脚本里用；两种布局的 node_modules 位置不同，统一在这里试。
function loadNunjucks() {
  const tried = [];
  for (const c of ["nunjucks", ...ROOTS.map((r) => path.join(r, "node_modules", "nunjucks"))]) {
    try {
      return require(c);
    } catch (e) {
      tried.push(c);
    }
  }
  throw new Error(
    "nunjucks not found. Tried:\n  " + tried.join("\n  ") +
    "\nRun `npm install` in the package root (or `cd release && npm install` in the workspace)."
  );
}

// 数据根 = 真正放 examples/ 的那棵树。包内就是自己；工作区里是 release/。
function dataRoot() {
  for (const r of ROOTS) {
    if (fs.existsSync(path.join(r, "examples", "context.json"))) return r;
  }
  return PARENT;
}

// 相对数据根取文件（examples/context.json、dist/、overrides/…）
function dataFile(rel) {
  return path.join(dataRoot(), ...String(rel).split("/"));
}

module.exports = { loadNunjucks, dataRoot, dataFile, ROOTS, PARENT };
