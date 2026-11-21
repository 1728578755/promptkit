#!/usr/bin/env node
// 量化系统提示词的前缀缓存友好度：对每对「只差一个变量」的上下文渲染两份，
// 求最长公共前缀（LCP），输出 LCP 占全文的百分比。
//
// 覆盖的变化维度：换项目目录、换账号的长期记忆、切模型、云端记忆刷新、
// 用户级记忆更新、开关行业模式——这些是日常使用中最常见的前缀失效来源。
//
// 为什么用 LCP：缓存命中的单位就是「从头开始连续相同的那些 token」。
// 一个绝对路径、一个模型名出现在第 3000 字符，都会让后面全部失效。
//
// 用法：
//   node scripts/cache-prefix.js                # 全部场景
//   node scripts/cache-prefix.js --template work --mode craft
//   node scripts/cache-prefix.js --json         # 机器可读，便于回归比较
//   node scripts/cache-prefix.js --strict       # 低于阈值(默认 95%)时退出码 1
const fs = require("fs");
const path = require("path");
// 依赖与数据根的解析都在 _deps.js 里做，包内与工作区共用同一份脚本实现。
const { loadNunjucks, dataFile } = require("./_deps");

const nunjucks = loadNunjucks();
const ROOT = path.resolve(__dirname, "..");
// 内容树（welcome-mode / fragments / plugins）在两种布局里都在脚本上一级；
// examples/ 只在数据根里——工作区里是 release/examples。
const CONTEXT_FILE = dataFile("examples/context.json");
const base = JSON.parse(fs.readFileSync(CONTEXT_FILE, "utf8"));

const args = process.argv.slice(2);
function argVal(name, dflt) {
  const i = args.indexOf(name);
  return i >= 0 ? args[i + 1] : dflt;
}
const onlyTemplate = argVal("--template", "");
const onlyMode = argVal("--mode", "");
const asJson = args.includes("--json");
const strict = args.includes("--strict");
const threshold = Number(argVal("--threshold", "95"));
// 跨模式（mode-switch）结构上不可能都很高：ask 只保留极少数块，与 craft 的分叉点在
// policy-common 的 ask 分支（约第 900 字符）。默认不纳入门禁，需要时用 --mode-threshold 显式设。
const modeThreshold = Number(argVal("--mode-threshold", "0"));

const TEMPLATES = ["work", "code", "design"];
const MODES = ["craft", "plan", "ask", "quick", "expert"];

function stripFrontmatter(src) {
  // 真实请求里 interactionmode-*.md 的 frontmatter 被宿主解析成 tools 列表后剥掉，
  // 不进入 system 文本。这里同样剥掉，让量出来的字节与模型真正看到的对齐。
  if (!src.startsWith("---")) return src;
  const end = src.indexOf("\n---", 3);
  if (end < 0) return src;
  const after = src.indexOf("\n", end + 4);
  return after < 0 ? "" : src.slice(after + 1);
}

function createLoader() {
  return {
    getSource(name) {
      let file;
      if (name.startsWith("prompt-common/fragments/")) {
        file = path.join(ROOT, "fragments", name.slice("prompt-common/fragments/".length));
      } else if (name.startsWith("interactionmode-")) {
        const m = name.match(/^interactionmode-([^/]+)\/fragments\/(.+)$/);
        if (!m) throw new Error("Unsupported include: " + name);
        file = path.join(ROOT, "plugins", "workbuddy-builtin", "interactionmode", m[1], "fragments", m[2]);
      } else {
        file = path.join(ROOT, name);
      }
      if (!fs.existsSync(file)) throw new Error("Include not found: " + name + " -> " + file);
      return { src: stripFrontmatter(fs.readFileSync(file, "utf8")), path: file, noCache: true };
    },
  };
}

const env = new nunjucks.Environment(createLoader(), { autoescape: false, throwOnUndefined: false });

function render(template, mode, patch) {
  return env.render(`welcome-mode/${template}.tpl`, { ...base, workMode: mode, ...patch });
}

function lcp(a, b) {
  const n = Math.min(a.length, b.length);
  let i = 0;
  while (i < n && a.charCodeAt(i) === b.charCodeAt(i)) i++;
  return i;
}

// 场景：每项 [名称, 说明, 变体A补丁, 变体B补丁]
const MEM_A = '<memory>\nprofile A\n</memory>\n';
const MEM_B = '<memory>\nprofile B — 完全不同的长期记忆内容\n</memory>\n';
const USR_A = '<user_memory>\n- prefers terse answers\n</user_memory>\n';
const USR_B = '<user_memory>\n- prefers terse answers\n- always ship a screenshot\n- never hardcode defaults\n</user_memory>\n';

const SCENARIOS = [
  ["project-switch", "换一个项目目录（同一个人、同一个模型）",
    { WorkbuddyMemoryDir: "E:/ws/project-a/.workbuddy/memory" },
    { WorkbuddyMemoryDir: "E:/ws/project-b/.workbuddy/memory" }],
  ["account-switch", "换一个账号的用户级记忆目录",
    { WorkbuddyUserMemoryDir: "C:/Users/A/.workbuddy/user-aaaa-personal" },
    { WorkbuddyUserMemoryDir: "C:/Users/A/.workbuddy/user-bbbb-personal" }],
  ["model-switch", "同一会话里切换模型",
    { modelName: "Model-X", modelId: "x-model" },
    { modelName: "Model-Y", modelId: "y-model" }],
  ["working-memory", "云端记忆刷新（<memory> 内容变化）",
    { WorkingMemoryContent: MEM_A },
    { WorkingMemoryContent: MEM_B }],
  ["user-memory", "用户级记忆更新（<user_memory> 内容变化）",
    { UserMemoryContent: USR_A },
    { UserMemoryContent: USR_B }],
  ["industry-mode", "开关行业模式",
    { industryModeSystemPromptAppend: "" },
    { industryModeSystemPromptAppend: "# 行业模式\n金融/医疗场景的额外约束若干。" }],
];

const rows = [];
let failures = 0;

for (const template of TEMPLATES) {
  if (onlyTemplate && template !== onlyTemplate) continue;
  for (const mode of MODES) {
    if (onlyMode && mode !== onlyMode) continue;

    for (const [name, desc, pa, pb] of SCENARIOS) {
      const a = render(template, mode, pa);
      const b = render(template, mode, pb);
      const n = lcp(a, b);
      const pct = (n / Math.min(a.length, b.length)) * 100;
      rows.push({ template, mode, scenario: name, desc, lcp: n, total: Math.min(a.length, b.length), pct: +pct.toFixed(2) });
      if (strict && pct < threshold) failures++;
    }
  }
}

// 跨模式：同模板两个模式的公共前缀
for (const template of TEMPLATES) {
  if (onlyTemplate && template !== onlyTemplate) continue;
  for (const [ma, mb] of [["craft", "plan"], ["craft", "ask"], ["plan", "expert"], ["craft", "expert"]]) {
    if (onlyMode && (ma !== onlyMode && mb !== onlyMode)) continue;
    const a = render(template, ma, {});
    const b = render(template, mb, {});
    const n = lcp(a, b);
    const pct = (n / Math.min(a.length, b.length)) * 100;
    rows.push({ template, mode: `${ma}~${mb}`, scenario: "mode-switch", desc: "会话内切换模式",
      lcp: n, total: Math.min(a.length, b.length), pct: +pct.toFixed(2) });
    if (strict && modeThreshold > 0 && pct < modeThreshold) failures++;
  }
}

if (asJson) {
  console.log(JSON.stringify(rows, null, 2));
} else {
  // 中文是双宽字符，按字符数补空格会错位；按显示宽度补。
  const dispWidth = (s) => {
    let w = 0;
    for (const ch of String(s)) {
      const c = ch.codePointAt(0);
      const wide =
        (c >= 0x1100 && c <= 0x115f) || (c >= 0x2e80 && c <= 0xa4cf) ||
        (c >= 0xac00 && c <= 0xd7a3) || (c >= 0xf900 && c <= 0xfaff) ||
        (c >= 0xfe30 && c <= 0xfe6f) || (c >= 0xff00 && c <= 0xff60) ||
        (c >= 0xffe0 && c <= 0xffe6);
      w += wide ? 2 : 1;
    }
    return w;
  };
  const pad = (s, n) => String(s) + " ".repeat(Math.max(0, n - dispWidth(s)));
  const COLS = [8, 14, 16, 10, 10];
  console.log(pad("模板", COLS[0]) + pad("模式", COLS[1]) + pad("场景", COLS[2]) + pad("公共前缀", COLS[3]) + pad("全文", COLS[4]) + "命中率");
  console.log("-".repeat(COLS.reduce((a, b) => a + b, 0) + 6));
  for (const r of rows) {
    console.log(
      pad(r.template, COLS[0]) + pad(r.mode, COLS[1]) + pad(r.scenario, COLS[2]) +
      pad(r.lcp, COLS[3]) + pad(r.total, COLS[4]) + r.pct + "%"
    );
  }
  console.log("-".repeat(COLS.reduce((a, b) => a + b, 0) + 6));
  const worst = rows.reduce((a, b) => (a.pct <= b.pct ? a : b));
  console.log(`最差：${worst.template}/${worst.mode}/${worst.scenario} = ${worst.pct}%`);
  const gate = `同模式场景 >= ${threshold}%` + (modeThreshold > 0 ? `，跨模式 >= ${modeThreshold}%` : `，跨模式不设门禁`);
  console.log(strict ? (failures === 0 ? `STRICT OK（${gate}）` : `STRICT FAILURES: ${failures}（${gate}）`) : "（--strict 未启用）");
}

process.exit(failures === 0 ? 0 : 1);
