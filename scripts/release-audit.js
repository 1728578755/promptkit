#!/usr/bin/env node
// release-audit.js —— 公开包发布门槛（发版前必跑，失败即不许发布）
//
// 审计对象永远是"公开包"：
//   · 在源目录（scripts\.. 下还有 release\）跑 → 审计 release\，并额外与源逐字节比对；
//   · 在公开包内（公开仓库根）跑 → 审计自己，只做完整性与"可一键"检查。
//
// 三个维度，任一不过就 exit 1：
//   1. 完整性：deploy 依赖的每一个输入都在包里。缺一件，"一键修改所有"就是假话。
//   2. 一致性：包里的提示词与部署链路与源逐字节一致，杜绝"改了一棵树、装的是另一棵树"。
//   3. 可一键：deploy.cmd 无参数必须自带 -Elevate（全量写入）、必须 exit /b 传退出码、必须 CRLF。
//   另有版本号三处一致（package.json / README / CHANGELOG）。
//
// 用法：
//   node scripts/release-audit.js            # 自动判定上面两种模式
//   node scripts/release-audit.js --json     # 机器可读
//
// 退出码：0 = 可以发布；1 = 有阻塞项。
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");
const { REQUIRED, SYNC_DIRS, SYNC_FILES, SCRIPT_DIFF_ALLOWED, SYNC_IGNORE } = require("./release-manifest");

const HERE = path.resolve(__dirname, "..");
const NESTED = path.join(HERE, "release");
const PKG = fs.existsSync(path.join(NESTED, "scripts", "deploy.ps1")) ? NESTED : HERE;
const SRC = PKG === HERE ? null : HERE; // 源目录（只有工作区模式才有）
const jsonMode = process.argv.includes("--json");

const problems = [];
const notes = [];
const ok = (msg) => notes.push(`  OK   ${msg}`);
const bad = (msg) => problems.push(msg);

const sha = (f) => crypto.createHash("sha256").update(fs.readFileSync(f)).digest("hex");
const at = (base, rel) => path.join(base, ...rel.split("/"));

function walk(dir, out = []) {
  for (const e of fs.readdirSync(dir, { withFileTypes: true })) {
    const p = path.join(dir, e.name);
    if (e.isDirectory()) walk(p, out);
    else if (e.isFile()) out.push(p);
  }
  return out;
}

// ---------- 1. 完整性 ----------
let requiredCount = 0;
for (const [group, files] of Object.entries(REQUIRED)) {
  const missing = files.filter((f) => !fs.existsSync(at(PKG, f)));
  requiredCount += files.length;
  if (missing.length) bad(`完整性/${group}：缺 ${missing.length} → ${missing.join(", ")}`);
  else ok(`完整性/${group}：${files.length} 个齐全`);
}

// ---------- 2. 一致性 ----------
if (!SRC) {
  ok("一致性：当前是独立包（没有上层源目录），跳过逐字节比较");
} else {
  for (const dir of SYNC_DIRS) {
    const a = path.join(SRC, dir);
    const b = path.join(PKG, dir);
    if (!fs.existsSync(a)) { bad(`一致性/${dir}：源目录里没有 ${dir}`); continue; }
    const keep = (relPath) => !SYNC_IGNORE.some((pre) => (dir + "/" + relPath.split(path.sep).join("/")).startsWith(pre));
    const fa = walk(a).map((p) => path.relative(a, p)).filter(keep).sort();
    const fb = fs.existsSync(b) ? walk(b).map((p) => path.relative(b, p)).filter(keep).sort() : [];
    const onlySrc = fa.filter((f) => !fb.includes(f));
    const onlyPkg = fb.filter((f) => !fa.includes(f));
    const differ = fa.filter((f) => fb.includes(f)).filter((f) => sha(path.join(a, f)) !== sha(path.join(b, f)));
    if (onlySrc.length || onlyPkg.length || differ.length) {
      bad(
        `一致性/${dir}：源独有 ${onlySrc.length}、包独有 ${onlyPkg.length}、内容不同 ${differ.length}` +
          (onlySrc.length ? `\n         源独有: ${onlySrc.slice(0, 6).join(", ")}` : "") +
          (onlyPkg.length ? `\n         包独有: ${onlyPkg.slice(0, 6).join(", ")}` : "") +
          (differ.length ? `\n         内容不同: ${differ.slice(0, 6).join(", ")}` : "")
      );
    } else {
      ok(`一致性/${dir}：${fa.length} 个文件逐字节相同`);
    }
  }
  let mismatch = 0;
  for (const f of SYNC_FILES) {
    const a = at(SRC, f);
    const b = at(PKG, f);
    if (!fs.existsSync(a)) { bad(`一致性/${f}：源文件不存在`); mismatch++; continue; }
    if (!fs.existsSync(b)) { bad(`一致性/${f}：包里缺这个文件`); mismatch++; continue; }
    if (sha(a) !== sha(b)) { bad(`一致性/${f}：与源内容不同`); mismatch++; }
  }
  if (!mismatch) ok(`一致性/部署脚本：${SYNC_FILES.length} 个逐字节相同`);
  for (const f of SCRIPT_DIFF_ALLOWED) {
    if (!fs.existsSync(at(PKG, f))) bad(`一致性/${f}：包里缺这个文件`);
  }
}

// ---------- 3. 可一键 ----------
const cmdPath = at(PKG, "scripts/deploy.cmd");
if (!fs.existsSync(cmdPath)) {
  bad("可一键：scripts/deploy.cmd 不存在");
} else {
  const cmd = fs.readFileSync(cmdPath, "utf8");
  if (!/-Elevate/.test(cmd)) bad("可一键：deploy.cmd 无参数时没有 -Elevate，双击只会写缓存");
  else ok("可一键：deploy.cmd 无参数走 -Elevate（全量写入）");
  if (!/exit\s+\/b\s+%RC%/i.test(cmd)) bad("可一键：deploy.cmd 没有 exit /b %RC%，失败会返回 0");
  else ok("可一键：deploy.cmd 把退出码传出去了");
  if (!/runtime-templates/i.test(cmd) || !/product\.json/i.test(cmd)) {
    bad("可一键：deploy.cmd 的提示没有说明会写 runtime-templates / product.json");
  } else {
    ok("可一键：deploy.cmd 的提示写明全量范围（含 runtime-templates / product.json / app.asar）");
  }
  const crlf = (cmd.match(/\r\n/g) || []).length;
  if (crlf === 0) bad("可一键：deploy.cmd 是 LF 行尾，cmd.exe 会解析异常（必须 CRLF）");
  else ok(`可一键：deploy.cmd 是 CRLF 行尾（${crlf} 行）`);
}

// ---------- 4. 版本号三处一致 ----------
try {
  const pkg = JSON.parse(fs.readFileSync(at(PKG, "package.json"), "utf8"));
  const readme = fs.readFileSync(at(PKG, "README.md"), "utf8");
  const changelog = fs.readFileSync(at(PKG, "CHANGELOG.md"), "utf8");
  const readmeVer = (readme.match(/当前版本\s*\*\*v?([0-9.]+)\*\*/) || [])[1];
  const changeVer = (changelog.match(/^## v([0-9.]+)/m) || [])[1];
  if (readmeVer !== pkg.version) bad(`版本号：package.json=${pkg.version}，README=${readmeVer || "未找到"}`);
  else if (changeVer !== pkg.version) bad(`版本号：package.json=${pkg.version}，CHANGELOG 最新=${changeVer || "未找到"}`);
  else ok(`版本号：三处一致（v${pkg.version}）`);
} catch (e) {
  bad(`版本号：读不到元数据（${e.message}）`);
}

// ---------- 输出 ----------
const report = { package: PKG, source: SRC, mode: SRC ? "workspace" : "package", required: requiredCount, problems };
if (jsonMode) {
  console.log(JSON.stringify(report, null, 2));
} else {
  console.log(`release-audit  package=${PKG}`);
  console.log(`  mode=${report.mode}（workspace=与源比对，package=只查完整性）`);
  for (const line of notes) console.log(line);
  console.log("");
  if (problems.length) {
    console.log(`BLOCKED: ${problems.length} 项阻塞发布`);
    for (const p of problems) console.log(`  FAIL ${p}`);
    console.log("\n先补齐/同步再发版：node scripts/sync-release.js（源→包镜像）后重跑本脚本。");
    process.exit(1);
  }
  console.log("RELEASE OK —— 公开包具备一键修改全部提示词的能力，可以发布。");
}
process.exit(problems.length ? 1 : 0);
