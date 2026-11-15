#!/usr/bin/env node
const fs = require("fs");
const path = require("path");

const ROOT = path.resolve(__dirname, "..");
const DIST = path.join(ROOT, "dist");

const templates = ["work", "code", "design"];
const modes = ["craft", "plan", "ask", "quick", "expert"];
const failures = [];

function check(condition, message) {
  if (!condition) failures.push(message);
}

function count(text, needle) {
  return text.split(needle).length - 1;
}

if (!fs.existsSync(DIST)) {
  console.error("dist/ not found. Run `npm run render` first.");
  process.exit(1);
}

for (const template of templates) {
  for (const mode of modes) {
    const file = path.join(DIST, `${template}-${mode}.md`);
    if (!fs.existsSync(file)) {
      failures.push(`${template}/${mode}: missing render`);
      continue;
    }

    const text = fs.readFileSync(file, "utf8");
    const label = `${template}/${mode}`;

    check(!text.includes("{{"), `${label}: template leftover "{{"`);
    check(!text.includes("{%"), `${label}: template leftover "{%"`);
    check(text.includes("<quality_policy>"), `${label}: missing quality policy`);
    check(text.includes("<refusal_policy>"), `${label}: missing refusal policy`);
    check(!text.includes("<working_modes>"), `${label}: working_modes should be removed`);
    check(!text.includes("Your capabilities"), `${label}: capabilities list should be removed`);

    if (mode === "craft" || mode === "plan" || mode === "expert") {
      check(text.includes("<agent_contract>"), `${label}: missing agent contract`);
      check(text.includes("<tool_routing>"), `${label}: missing tool routing`);
      check(text.includes("<memory_system>"), `${label}: missing memory block`);
    }

    if (mode === "ask") {
      check(!text.includes("<agent_contract>"), `${label}: ask should not load agent contract`);
      check(!text.includes("<tool_routing>"), `${label}: ask should not load tool routing`);
      check(!text.includes("<current_mode>"), `${label}: ask should not declare current_mode`);
    }

    if (mode === "quick") {
      check(!text.includes("<tool_routing>"), `${label}: quick should not load tool routing`);
      check(count(text, "<current_mode>") === 1, `${label}: quick should keep one current_mode`);
    }

    console.log(`${label}: ${Buffer.byteLength(text)} bytes`);
  }
}

if (failures.length) {
  console.error(`\n${failures.length} check(s) failed:`);
  for (const failure of failures) console.error(`- ${failure}`);
  process.exit(1);
}

console.log("\nALL CHECKS PASSED");
