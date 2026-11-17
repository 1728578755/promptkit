#!/usr/bin/env node
// Render the project's prompt templates with a caller-supplied context.
// Purpose: prove that a target edition's flags (IsOversea / IsPersonalEdition /
// productName ...) still render every mode without missing includes or leftover tags.
//
// Usage:
//   node scripts/render-check.js                 # CN profile from examples/context.json
//   node scripts/render-check.js --profile ai    # international edition profile
//   node scripts/render-check.js --dist <dir>    # keep the rendered files for inspection
//   node scripts/render-check.js --root <dir>    # render another copy of the package
const fs = require("fs");
const path = require("path");

function loadNunjucks() {
  const candidates = [
    "nunjucks",
    path.join(__dirname, "..", "node_modules", "nunjucks"),
  ];
  for (const c of candidates) {
    try { return require(c); } catch { }
  }
  throw new Error("nunjucks not found. Run: npm install");
}

const nunjucks = loadNunjucks();
const ROOT = path.resolve(__dirname, "..");
const templates = ["work", "code", "design"];
const modes = ["craft", "plan", "ask", "quick", "expert"];

const args = process.argv.slice(2);
const profile = args.includes("--profile") ? args[args.indexOf("--profile") + 1] : "cn";
const distIdx = args.indexOf("--dist");
const dist = distIdx >= 0 ? path.resolve(args[distIdx + 1]) : "";
// --root：从别处（如插件缓存快照）渲染，用于验证「部署出去的那份字节」也能组装成功
const rootIdx = args.indexOf("--root");
const RENDER_ROOT = rootIdx >= 0 ? path.resolve(args[rootIdx + 1]) : ROOT;
const CONTEXT_FILE = path.join(RENDER_ROOT, "examples", "context.json");

function findInteractionDir(mode) {
  const dir = path.join(RENDER_ROOT, "plugins", "workbuddy-builtin", "interactionmode", mode);
  return fs.existsSync(dir) ? dir : null;
}

function createLoader() {
  return {
    getSource(name) {
      let file;
      if (name.startsWith("prompt-common/fragments/")) {
        file = path.join(RENDER_ROOT, "fragments", name.slice("prompt-common/fragments/".length));
      } else if (name.startsWith("interactionmode-")) {
        const match = name.match(/^interactionmode-([^/]+)\/fragments\/(.+)$/);
        if (!match) throw new Error(`Unsupported include: ${name}`);
        const [, mode, fragment] = match;
        const dir = findInteractionDir(mode);
        if (!dir) throw new Error(`No interactionmode directory for mode: ${mode}`);
        file = path.join(dir, "fragments", fragment);
      } else {
        file = path.join(RENDER_ROOT, name);
      }
      if (!fs.existsSync(file)) throw new Error(`Include not found: ${name} -> ${file}`);
      return { src: fs.readFileSync(file, "utf8"), path: file, noCache: true };
    },
  };
}

const base = JSON.parse(fs.readFileSync(CONTEXT_FILE, "utf8"));
const profiles = {
  cn: {},
  ai: {
    productName: "WorkBuddy AI",
    dataFolderName: ".workbuddy-ai",
    ResponseLanguage: "English",
    IsOversea: true,
    IsPersonalEdition: true,
    BuddyOneEnabled: false,
    ExpertManagementEnabled: true,
  },
};
if (!profiles[profile]) throw new Error(`unknown profile: ${profile}`);

const env = new nunjucks.Environment(createLoader(), { autoescape: false, throwOnUndefined: false });
if (dist) fs.mkdirSync(dist, { recursive: true });
if (RENDER_ROOT !== ROOT) console.log("render root: " + RENDER_ROOT);

let failures = 0;
for (const template of templates) {
  for (const mode of modes) {
    const context = { ...base, ...profiles[profile], workMode: mode };
    let out;
    try {
      out = env.render(`welcome-mode/${template}.tpl`, context);
    } catch (error) {
      failures++;
      console.log(`FAIL ${template}/${mode}: ${error.message}`);
      continue;
    }
    const leftover = (out.match(/\{\{|\{%/g) || []).length;
    const regional = (out.match(/<regional_conventions>/g) || []).length;
    const policy = (out.match(/<content_policy>/g) || []).length;
    if (dist) fs.writeFileSync(path.join(dist, `${template}-${mode}.md`), out, "utf8");
    console.log(
      `${template}/${mode}: ${Buffer.byteLength(out)} bytes, leftover=${leftover}, ` +
      `regional=${regional}, content_policy=${policy}`
    );
    if (leftover > 0) failures++;
    if (template !== "design" && mode !== "quick" && policy > 0) failures++;
  }
}

const expectRegional = profile === "cn";
console.log(`\nprofile=${profile}; regional_conventions expected=${expectRegional}`);
console.log(failures === 0 ? "RENDER OK" : `RENDER FAILURES: ${failures}`);
process.exit(failures === 0 ? 0 : 1);
