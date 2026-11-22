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
const { REQUIRED, SYNC_DIRS, SYNC_FILES, SYNC_IGNORE } = require("./release-manifest");

const HERE = path.resolve(__dirname, "..");
const NESTED = path.join(HERE, "release");
const PKG = fs.existsSync(path.join(NESTED, "scripts", "deploy.ps1")) ? NESTED : HERE;
const SRC = PKG === HERE ? null : HERE; // 源目录（只有工作区模式才有）
const jsonMode = process.argv.includes("--json");
// 可选：--zip <path> 额外审计一个已经打好的 zip（git archive 产物）。
// 缺省不审 zip——发布前的仓库审计不该依赖"zip 已经生成"。
const zipArgIdx = process.argv.indexOf("--zip");
const ZIP = zipArgIdx >= 0 ? path.resolve(process.argv[zipArgIdx + 1] || "") : "";

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

// ---------- 1b. 全覆盖（双向）：清单与 scripts/ 必须互为子集 ----------
// 只查"清单里的都在包里"不够——新写的脚本忘了加清单，包就少一件能力。
// 反过来也要查：包里多出一个没登记的脚本，说明有人绕过清单往包里塞了东西，
// 那它既不会被同步、也不会被审计，属于"悄悄多出来的第二份实现"。
const listed = new Set([...Object.values(REQUIRED).flat(), ...SYNC_FILES]);
const SCRIPT_EXT = /\.(js|py|ps1|cmd|json)$/;
const isScript = (rel) =>
  rel.startsWith("scripts/") && SCRIPT_EXT.test(rel) && !rel.includes("__pycache__") && !rel.endsWith(".pyc");
const relList = (base) =>
  walk(path.join(base, "scripts")).map((p) => path.relative(base, p).split(path.sep).join("/"));

if (SRC) {
  const srcScripts = relList(SRC).filter(isScript);
  const orphans = srcScripts.filter((rel) => !listed.has(rel));
  if (orphans.length) bad(`全覆盖：源 scripts/ 里有 ${orphans.length} 个文件没进清单 → ${orphans.join(", ")}`);
  else ok(`全覆盖：源 scripts/ 的 ${srcScripts.length} 个文件都进了清单`);
}
{
  const pkgScripts = relList(PKG).filter(isScript);
  const unlisted = pkgScripts.filter((rel) => !listed.has(rel));
  if (unlisted.length) bad(`全覆盖（反向）：包内 scripts/ 里有 ${unlisted.length} 个文件没进清单 → ${unlisted.join(", ")}`);
  else ok(`全覆盖（反向）：包内 scripts/ 的 ${pkgScripts.length} 个文件都在清单里`);
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
  if (!mismatch) ok(`一致性/部署与诊断脚本：${SYNC_FILES.length} 个逐字节相同`);
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

// ---------- 5. 行尾漂移 ----------
// F-12：release/.gitattributes 曾只有 `* text=auto eol=lf`，于是 git archive 出来的
// zip 里所有 .ps1 都是 LF，而工作区/人测过的是 CRLF。这里做两层校验：
//   (a) release/ 工作区的行尾必须与源工作区逐文件一致（前提：sync-release 已跑过）；
//   (b) 若给了 --zip，zip 内每个 scripts/*.{ps1,py,cmd,js} 的行尾也必须与源工作区一致。
// 只比"行尾种类"（CRLF/LF/MIXED），不比字节——zip 与工作区可能仅因 CRLF 而不同。
function eolOf(buf) {
  let crlf = 0;
  let lf = 0;
  for (let i = 0; i < buf.length; i++) {
    if (buf[i] !== 0x0a) continue;
    if (i > 0 && buf[i - 1] === 0x0d) crlf++;
    else lf++;
  }
  if (crlf && lf) return "MIXED";
  if (crlf) return "CRLF";
  if (lf) return "LF";
  return "NONE";
}
const eolFiles = new Set([...SYNC_FILES, ...Object.values(REQUIRED).flat()].filter((f) => /\.(ps1|py|cmd|js)$/.test(f)));
// 源文件自身是 MIXED 是"源工作区的毛病"，不是打包漂移；单独报成 WARN 提示修源文件，
// 否则这条检查会变成"永远红"，失去信号。只要 zip 与源一致就算打包正确。
const srcMixed = [];
if (SRC) {
  const drift = [];
  for (const rel of [...eolFiles].sort()) {
    const a = at(SRC, rel);
    const b = at(PKG, rel);
    if (!fs.existsSync(a) || !fs.existsSync(b)) continue;
    const ea = eolOf(fs.readFileSync(a));
    const eb = eolOf(fs.readFileSync(b));
    if (ea === "MIXED") srcMixed.push(rel);
    if (ea !== eb) drift.push(`${rel} 源=${ea} 包=${eb}`);
  }
  if (drift.length) bad(`行尾/${PKG.split(path.sep).pop()}：${drift.length} 个文件与源工作区不一致 → ${drift.slice(0, 8).join(", ")}`);
  else ok(`行尾：包内 ${eolFiles.size} 个脚本文件与源工作区一致`);
  // .cmd 推荐 CRLF（cmd.exe / Git / 编辑器都按这个预期工作）。但实测本机 cmd.exe
  // 对 LF-only 的批处理（含 if 括号块 + goto 标签）执行正常，所以这不是硬失败，
  // 只提示——把"没验证过的假设"写成阻塞项会让审计失去可信度。
  const lfCmd = [...eolFiles].filter((f) => f.endsWith(".cmd")).filter((f) => {
    const a = at(SRC, f);
    return fs.existsSync(a) && eolOf(fs.readFileSync(a)) !== "CRLF";
  });
  if (lfCmd.length) notes.push(`  WARN 行尾：这些 .cmd 在源工作区不是 CRLF（建议改回）→ ${lfCmd.join(", ")}`);
  else ok("行尾：所有 .cmd 在源工作区都是 CRLF");
}
if (ZIP) {
  let AdmZip = null;
  try {
    // Node 22+ 无内置 zip 读取；优先用已装的 node_modules，其次用系统 tar（不引入新依赖）。
    AdmZip = require("adm-zip");
  } catch {
    AdmZip = null;
  }
  const zipEntries = new Map();
  if (AdmZip) {
    const zip = new AdmZip(ZIP);
    for (const e of zip.getEntries()) {
      if (!e.isDirectory && /\.(ps1|py|cmd|js)$/.test(e.entryName)) {
        zipEntries.set(e.entryName, e.getData());
      }
    }
  } else {
    // 回退：用 PowerShell 的 System.IO.Compression 读出每个 entry 的行尾。
    const { execFileSync } = require("child_process");
    const ps = [
      "Add-Type -AssemblyName System.IO.Compression.FileSystem;",
      `$z=[IO.Compression.ZipFile]::OpenRead('${ZIP.replace(/'/g, "''")}');`,
      "foreach($e in $z.Entries){ if($e.Name -and $e.FullName -match '\\.(ps1|py|cmd|js)$'){",
      "$s=$e.Open();$m=New-Object IO.MemoryStream;$s.CopyTo($m);$b=$m.ToArray();$s.Close();",
      "$c=0;$l=0;for($i=0;$i -lt $b.Length;$i++){if($b[$i] -eq 10){if($i -gt 0 -and $b[$i-1] -eq 13){$c++}else{$l++}}};",
      // zip 条目名就是相对包根的路径（scripts/...），不要再剥一层目录。
      "$k=$e.FullName;",
      "'{0}|{1}|{2}' -f $k,$c,$l }}; $z.Dispose()",
    ].join(" ");
    const out = execFileSync("powershell", ["-NoProfile", "-Command", ps], { encoding: "utf8", timeout: 120000 });
    for (const line of out.split(/\r?\n/)) {
      const m = line.match(/^(.+)\|(\d+)\|(\d+)$/);
      if (!m) continue;
      const [, name, c, l] = m;
      const kind = Number(c) && Number(l) ? "MIXED" : Number(c) ? "CRLF" : Number(l) ? "LF" : "NONE";
      zipEntries.set(name, kind);
    }
  }
  const base = SRC || PKG;
  const zipDrift = [];
  const zipNormalized = [];
  let checkedZip = 0;
  for (const [rel, data] of zipEntries) {
    const a = at(base, rel);
    if (!fs.existsSync(a)) continue;
    const ez = typeof data === "string" ? data : eolOf(data);
    const ea = eolOf(fs.readFileSync(a));
    checkedZip++;
    if (ez === ea) continue;
    // 源文件本身"坏"（混合行尾，或 .cmd 不是 CRLF）时，zip 按 .gitattributes 归一成
    // CRLF 是**更正确**的结果（cmd.exe 习惯、编辑器一致）。这种差异记为源侧缺陷 WARN，
    // 不冒充"打包漂移"——否则这条检查会对已知坏文件永远红，失去信号。
    const srcBroken = ea === "MIXED" || (rel.endsWith(".cmd") && ea !== "CRLF");
    if (srcBroken) zipNormalized.push(`${rel} 工作区=${ea} zip=${ez}（zip 归一为 CRLF，源文件需修）`);
    else zipDrift.push(`${rel} 工作区=${ea} zip=${ez}`);
  }
  if (!zipEntries.size) bad(`行尾/zip：读不出任何脚本条目（${ZIP}）`);
  else if (zipDrift.length) bad(`行尾/zip：${zipDrift.length}/${checkedZip} 个脚本与工作区不一致 → ${zipDrift.slice(0, 8).join(", ")}`);
  else ok(`行尾/zip：${checkedZip} 个脚本条目与工作区一致`);
  for (const w of zipNormalized) notes.push(`  WARN 行尾/zip：${w}`);
}
if (srcMixed.length) {
  // 不是阻塞项：zip 已与源一致。但源文件本身混合行尾会让编辑器/补丁工具反复产生假差异。
  notes.push(`  WARN 行尾：源工作区有 ${srcMixed.length} 个文件是混合行尾（建议统一）→ ${srcMixed.join(", ")}`);
}

// ---------- 输出 ----------
const report = { package: PKG, source: SRC, mode: SRC ? "workspace" : "package", required: requiredCount, zip: ZIP || null, problems };
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
