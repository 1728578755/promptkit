#!/usr/bin/env node
// 渲染 runtime-templates\ 下真正会被运行时读取的 tpl，并断言注入块已按预期去掉。
//
//   node scripts/render-runtime-templates.js
//   node scripts/render-runtime-templates.js --dist out/runtime
//
// 断言项：
//   user-context-identity.tpl   -> 不含 <product_identity>；不含 "The user has provided the following custom instructions"
//                                  但必须仍然渲染出 <user_custom_instructions> 的内容与 <identity_context>
//   user-context-expert-identity.tpl -> 同上（无 custom 段）
//   craft-mode-reminder.tpl     -> 渲染结果 trim 后为空（不再注入 <craft_mode>）
// 退出码 1 表示有断言不成立。
const fs = require("fs");
const path = require("path");
// 依赖与数据根的解析都在 _deps.js 里做，包内与工作区共用同一份脚本实现。
const { loadNunjucks } = require("./_deps");

const nunjucks = loadNunjucks();
const ROOT = path.resolve(__dirname, "..");
const DIR = path.join(ROOT, "runtime-templates");

const args = process.argv.slice(2);
const distIdx = args.indexOf("--dist");
const dist = distIdx >= 0 ? path.resolve(args[distIdx + 1]) : "";
if (dist) fs.mkdirSync(dist, { recursive: true });

const env = new nunjucks.Environment(
  {
    getSource(name) {
      const file = path.join(DIR, name);
      if (!fs.existsSync(file)) throw new Error(`Include not found: ${name} -> ${file}`);
      return { src: fs.readFileSync(file, "utf8"), path: file, noCache: true };
    },
  },
  { autoescape: false, throwOnUndefined: false }
);

const base = {
  productName: "WorkBuddy",
  dataFolderName: ".workbuddy",
  WorkspaceIdentityMode: "on",
  SoulPath: "C:/x/SOUL.md",
  SoulContent: "soul",
  IdentityPath: "C:/x/IDENTITY.md",
  IdentityContent: "",
  UserPath: "C:/x/USER.md",
  UserContent: "user",
  ToneStyleContent: "",
  UserCustomPrompt: "自定义指令正文 CUSTOM-BODY",
  CloudMemoryPlatformEnabled: true,
};

const cases = [
  {
    file: "user-context-identity.tpl",
    mustHave: ["<user_custom_instructions>", "CUSTOM-BODY"],
    mustNot: [
      "<identity_context>", "Injected workspace identity files", "Update SOUL.md",
      "<product_identity>", "The user has provided the following custom instructions", "{{", "{%",
    ],
  },
  {
    file: "user-context-identity.tpl",
    name: "user-context-identity.tpl (onboarding)",
    ctx: { WorkspaceIdentityMode: "onboarding", BootstrapPath: "C:/x/BOOTSTRAP.md", BootstrapContent: "" },
    mustHave: ["<user_custom_instructions>", "CUSTOM-BODY"],
    mustNot: [
      "<identity_context>", "BOOTSTRAP.md", "Keep the conversation natural",
      "<product_identity>", "The user has provided the following custom instructions", "{{", "{%",
    ],
  },
  {
    file: "user-context-expert-identity.tpl",
    mustHave: [],
    mustNot: ["<identity_context>", "<product_identity>", "{{", "{%"],
    expectEmpty: true,
  },
  {
    file: "craft-mode-reminder.tpl",
    mustHave: [],
    mustNot: ["<craft_mode>", "{{", "{%"],
    expectEmpty: true,
  },
  {
    file: "ask-mode-reminder.tpl",
    mustHave: [],
    mustNot: ["<ask_mode>", "{{", "{%"],
    expectEmpty: true,
  },
];

let failures = 0;
for (const c of cases) {
  const label = c.name || c.file;
  const ctx = { ...base, ...(c.ctx || {}) };
  let out;
  try {
    out = env.render(c.file, ctx);
  } catch (e) {
    failures++;
    console.log(`FAIL ${label}: ${e.message}`);
    continue;
  }
  const problems = [];
  for (const s of c.mustHave) if (!out.includes(s)) problems.push(`missing: ${s}`);
  for (const s of c.mustNot) if (out.includes(s)) problems.push(`present: ${s}`);
  if (c.expectEmpty && out.trim() !== "") problems.push(`not empty: ${JSON.stringify(out.trim().slice(0, 60))}`);
  if (!c.expectEmpty && out.includes("\n\n\n")) problems.push("blank-line pile-up (\\n\\n\\n)");
  if (dist) fs.writeFileSync(path.join(dist, label.replace(/[^\w.-]+/g, "_") + ".txt"), out, "utf8");
  console.log(`${problems.length ? "FAIL" : "OK  "} ${label}: ${Buffer.byteLength(out)} bytes${problems.length ? "  -> " + problems.join("; ") : ""}`);
  if (problems.length) failures++;
}

console.log(failures === 0 ? "\nRUNTIME RENDER OK" : `\nRUNTIME RENDER FAILURES: ${failures}`);
process.exit(failures === 0 ? 0 : 1);
