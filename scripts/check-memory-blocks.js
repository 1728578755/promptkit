#!/usr/bin/env node
// 断言记忆区在四种开关组合下都正确：<memory_system> / <memory_paths> 的出现与行数。
//
// 组合矩阵（CloudMemoryPlatformEnabled × WorkspaceMemoryDisabled）：
//   false/false → memory_system 三层 + memory_paths 两行（用户级 + 工作区）
//   false/true  → memory_system 两层 + memory_paths 一行（用户级）
//   true/false  → memory_system（云记忆工具）+ memory_paths 一行（工作区）
//   true/true   → memory_system 只有云记忆 + 无 memory_paths
//
// 用法：node scripts/check-memory-blocks.js
const fs = require("fs");
const path = require("path");
// 依赖与数据根的解析都在 _deps.js 里做，包内与工作区共用同一份脚本实现。
const { loadNunjucks, dataFile } = require("./_deps");

const nunjucks = loadNunjucks();
const ROOT = path.resolve(__dirname, "..");
const base = JSON.parse(fs.readFileSync(dataFile("examples/context.json"), "utf8"));

function stripFrontmatter(src) {
  if (!src.startsWith("---")) return src;
  const end = src.indexOf("\n---", 3);
  if (end < 0) return src;
  const after = src.indexOf("\n", end + 4);
  return after < 0 ? "" : src.slice(after + 1);
}

const env = new nunjucks.Environment({
  getSource(name) {
    let file;
    if (name.startsWith("prompt-common/fragments/")) {
      file = path.join(ROOT, "fragments", name.slice("prompt-common/fragments/".length));
    } else if (name.startsWith("interactionmode-")) {
      const m = name.match(/^interactionmode-([^/]+)\/fragments\/(.+)$/);
      file = path.join(ROOT, "plugins", "workbuddy-builtin", "interactionmode", m[1], "fragments", m[2]);
    } else {
      file = path.join(ROOT, name);
    }
    if (!fs.existsSync(file)) throw new Error("Include not found: " + name);
    return { src: stripFrontmatter(fs.readFileSync(file, "utf8")), path: file, noCache: true };
  },
}, { autoescape: false, throwOnUndefined: false });

const CASES = [
  { cloud: false, wsDisabled: false, sys: 1, paths: 1, pathLines: 2, layer3: 1, cloudTools: 0 },
  { cloud: false, wsDisabled: true, sys: 1, paths: 1, pathLines: 1, layer3: 0, cloudTools: 0 },
  { cloud: true, wsDisabled: false, sys: 1, paths: 1, pathLines: 1, layer3: 0, cloudTools: 1 },
  { cloud: true, wsDisabled: true, sys: 1, paths: 0, pathLines: 0, layer3: 0, cloudTools: 1 },
];

let failures = 0;
for (const c of CASES) {
  const out = env.render("welcome-mode/work.tpl", {
    ...base,
    workMode: "craft",
    CloudMemoryPlatformEnabled: c.cloud,
    WorkspaceMemoryDisabled: c.wsDisabled,
    WorkbuddyUserMemoryDir: "C:/Users/T/.assistant/user-abcd-personal",
    WorkbuddyMemoryDir: "E:/ws/proj/.assistant/memory",
  });
  const sysBlocks = (out.match(/<memory_system>/g) || []).length;
  const pathBlocks = (out.match(/<memory_paths>\n/g) || []).length; // 只数真正的块开头
  const m = out.match(/<memory_paths>([\s\S]*?)<\/memory_paths>/);
  const pathLines = m ? m[1].trim().split("\n").filter((l) => l.includes("directory:")).length : 0;
  const layer3 = (out.match(/# Layer 3 — Workspace Memory/g) || []).length;
  const cloudTools = (out.match(/MemoryRecall/g) || []).length;
  const absolute = /C:\/Users\/T\/\.assistant\/user-abcd-personal|E:\/ws\/proj\/\.assistant\/memory/.test(
    (out.match(/<memory_system>[\s\S]*?<\/memory_system>/) || [""])[0]
  );

  const ok = sysBlocks === c.sys && pathBlocks === c.paths && pathLines === c.pathLines &&
    layer3 === c.layer3 && cloudTools >= c.cloudTools * 1 && absolute === false;
  if (!ok) failures++;
  console.log(
    `cloud=${c.cloud} wsDisabled=${c.wsDisabled}  ` +
    `memory_system=${sysBlocks}(期望${c.sys})  memory_paths=${pathBlocks}(期望${c.paths})  ` +
    `路径行=${pathLines}(期望${c.pathLines})  Layer3=${layer3}  MemoryRecall=${cloudTools}  ` +
    `memory_system 内绝对路径=${absolute ? "有(BAD)" : "无(OK)"}  ${ok ? "OK" : "FAIL"}`
  );
  if (!ok) console.log("---- 渲染片段 ----\n" + out.slice(-800) + "\n------------------");
}

console.log(failures === 0 ? "MEMORY BLOCKS OK" : `MEMORY BLOCKS FAILURES: ${failures}`);
process.exit(failures === 0 ? 0 : 1);
