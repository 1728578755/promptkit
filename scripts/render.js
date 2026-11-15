#!/usr/bin/env node
const fs = require("fs");
const path = require("path");
const nunjucks = require("nunjucks");

const ROOT = path.resolve(__dirname, "..");
const DIST = path.join(ROOT, "dist");
const CONTEXT_FILE = path.join(ROOT, "examples", "context.json");

const templates = ["work", "code", "design"];
const modes = ["craft", "plan", "ask", "quick", "expert"];

function findInteractionDir(mode) {
  const stack = [path.join(ROOT, "plugins")];
  while (stack.length) {
    const dir = stack.pop();
    let entries;
    try {
      entries = fs.readdirSync(dir, { withFileTypes: true });
    } catch {
      continue;
    }
    for (const entry of entries) {
      if (!entry.isDirectory()) continue;
      const full = path.join(dir, entry.name);
      if (entry.name === "interactionmode") {
        const candidate = path.join(full, mode);
        if (fs.existsSync(candidate)) return candidate;
      }
      stack.push(full);
    }
  }
  return null;
}

function createLoader() {
  const interactionCache = new Map();

  return {
    getSource(name) {
      let file;
      if (name.startsWith("prompt-common/fragments/")) {
        file = path.join(ROOT, "fragments", name.slice("prompt-common/fragments/".length));
      } else if (name.startsWith("interactionmode-")) {
        const match = name.match(/^interactionmode-([^/]+)\/fragments\/(.+)$/);
        if (!match) throw new Error(`Unsupported include: ${name}`);
        const [, mode, fragment] = match;
        if (!interactionCache.has(mode)) interactionCache.set(mode, findInteractionDir(mode));
        const dir = interactionCache.get(mode);
        if (!dir) throw new Error(`No interactionmode directory for mode: ${mode}`);
        file = path.join(dir, "fragments", fragment);
      } else {
        file = path.join(ROOT, name);
      }

      if (!fs.existsSync(file)) {
        throw new Error(`Include not found: ${name} -> ${file}`);
      }

      return {
        src: fs.readFileSync(file, "utf8"),
        path: file,
        noCache: true,
      };
    },
  };
}

function main() {
  const baseContext = JSON.parse(fs.readFileSync(CONTEXT_FILE, "utf8"));
  const env = new nunjucks.Environment(createLoader(), {
    autoescape: false,
    throwOnUndefined: false,
  });

  fs.mkdirSync(DIST, { recursive: true });

  let count = 0;
  for (const template of templates) {
    for (const mode of modes) {
      const context = { ...baseContext, workMode: mode };
      const output = env.render(`welcome-mode/${template}.tpl`, context);
      const target = path.join(DIST, `${template}-${mode}.md`);
      fs.writeFileSync(target, output, "utf8");
      console.log(`${template}/${mode}: ${Buffer.byteLength(output)} bytes`);
      count++;
    }
  }

  console.log(`\nRendered ${count} files to ${path.relative(ROOT, DIST)}/`);
}

try {
  main();
} catch (error) {
  console.error(`Render failed: ${error.message}`);
  process.exit(1);
}
