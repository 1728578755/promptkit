#!/usr/bin/env node
// 对比两份 cache-prefix.js --json 的结果，输出「改前 / 改后 / 变化」表。
//
// 用法：
//   node scripts/cache-prefix.js --json > before.json
//   node scripts/cache-prefix.js --json > after.json
//   node scripts/cache-compare.js before.json after.json [--sort]
const fs = require("fs");

const [beforePath, afterPath] = process.argv.slice(2).filter((a) => !a.startsWith("--"));
if (!beforePath || !afterPath) {
  console.error("usage: node scripts/cache-compare.js <before.json> <after.json> [--sort]");
  process.exit(2);
}
const before = JSON.parse(fs.readFileSync(beforePath, "utf8"));
const after = JSON.parse(fs.readFileSync(afterPath, "utf8"));
const sortByDelta = process.argv.includes("--sort");

const key = (r) => `${r.template}|${r.mode}|${r.scenario}`;
const byKey = new Map(before.map((r) => [key(r), r]));

const pad = (s, n) => String(s).padEnd(n);
const lpad = (s, n) => String(s).padStart(n);

let rows = [];
for (const r of after) {
  const o = byKey.get(key(r));
  if (!o) continue;
  rows.push({
    label: `${r.template}/${r.mode}/${r.scenario}`,
    beforeLcp: o.lcp, beforePct: o.pct,
    afterLcp: r.lcp, afterPct: r.pct,
    dPct: +(r.pct - o.pct).toFixed(2),
    dLcp: r.lcp - o.lcp,
  });
}
if (sortByDelta) rows.sort((a, b) => a.dPct - b.dPct);

console.log(pad("模板/模式/场景", 34) + lpad("改前公共前缀", 14) + lpad("改后公共前缀", 14) + lpad("Δ字符", 9) + lpad("Δ命中率", 10));
console.log("-".repeat(81));
for (const r of rows) {
  const mark = r.dPct > 0.05 ? " +" : r.dPct < -0.05 ? " -" : "  ";
  console.log(
    pad(r.label, 34) +
    lpad(`${r.beforeLcp} / ${r.beforePct}%`, 14) +
    lpad(`${r.afterLcp} / ${r.afterPct}%`, 14) +
    lpad((r.dLcp >= 0 ? "+" : "") + r.dLcp, 9) +
    lpad((r.dPct >= 0 ? "+" : "") + r.dPct, 10) + mark
  );
}
console.log("-".repeat(81));

const improved = rows.filter((r) => r.dPct > 0.05).length;
const regressed = rows.filter((r) => r.dPct < -0.05);
const worst = rows.reduce((a, b) => (a.dPct <= b.dPct ? a : b));
console.log(`改善 ${improved} 项 / 共 ${rows.length} 项`);
if (regressed.length) {
  console.log(`命中率下降 ${regressed.length} 项（多为分母变大导致的百分比下降，看 Δ字符）：`);
  for (const r of regressed) console.log(`  ${r.label}  Δ字符=${r.dLcp >= 0 ? "+" : ""}${r.dLcp}  Δ=${r.dPct}%`);
}
console.log(`最差项：${worst.label} ${worst.dPct}%（Δ字符 ${worst.dLcp >= 0 ? "+" : ""}${worst.dLcp}）`);
