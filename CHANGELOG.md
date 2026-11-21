# Changelog

本文件记录 PromptKit 公开版的内容变化。提示词是会被逐字节比较的资产，所以每个版本都写明"改了什么位置"，而不只写"优化了提示词"。

## v1.2.0 — 2026-11-21

主题：**补齐运行层改造——公开版不再"少很多内容"。**
v1.1.6 及以前只发布提示词与部署脚本，`app.asar` 补丁、`product.json` 工具描述覆盖、`runtime-templates/`
三块被写在「不包含」里单独维护。这一版把它们全部收进包内，且由同一次 `deploy.cmd` 一起生效。

### 新增内容

| 目录 | 内容 | 规模 |
|---|---|---:|
| `scripts/` | 23 个脚本：asar 补丁、product.json 覆盖、技能描述精简、runtime 渲染、trace 诊断 | 23 个文件 |
| `overrides/tools/` | 18 个高频工具描述的精简版 + 原文基线 | 33,422 → 16,727 字节（−50.0%） |
| `overrides/skills/` | 31 个内置技能 description 的精简版 + 预算台账 | 3,943 字符（运行时预算 8,000） |
| `runtime-templates/` | 运行时直接读取的 4 个 tpl（user-context × 2、模式提醒 × 2） | 561 → 91 字节（另 3 个渲染为空） |

- `deploy.ps1` 的两条既有能力（`Invoke-ProductOverrides` / `Invoke-AsarPatch`）此前因缺文件静默跳过，
  现在文件到位，默认随部署执行；`-SkipProductOverrides` / `-SkipAsarPatch` 可单独关掉。
- `package.json` 新增 `npm run runtime`（渲染并断言四个 tpl）与 `npm run overrides`（渲染覆盖表）。
- `README.md` 新增「运行层改造」一节，「不包含」不再把这三块列为不发布。

### 去写死路径（公开包要能直接跑）

- 新增 `scripts/_install.py`：按 `WORKBUDDY_INSTALL` → `scripts\targets.json` 的 `installPath` →
  卸载登记项（`DisplayIcon` / `InstallLocation` / `UninstallString`，目录里必须真有 `WorkBuddy*.exe`）
  的顺序解析 `<安装目录>\resources\...`，**不扫盘、不猜目录**；`patch-asar.py` / `asar-grep.py` /
  `export-tool-desc.py` / `measure-product.py` / `tool-desc-map.py` / `apply-product-overrides.py` 全部改用。
  注册表过滤是必需的：本机登记的 `D:\Program Files\ZCode` 也有 `resources\app.asar`，只按结构匹配会认错产品。
- 删掉 `C:\Program Files\WorkBuddy` 与 `D:\Program Files\WorkBuddyAI` 两处硬编码默认值；省略 `--asar`
  且无任何线索时打印提示并退出码 2（此前会指向一台并不存在的机器）。
- 修 `apply-product-overrides.py` 注释里的乱码字节（`相对位��` → `相对位置`）。

### 验证（全部 exit=0）

| 命令 | 结果 |
|---|---|
| `npm run render` / `npm run check` | 15 份渲染 / ALL CHECKS PASSED |
| `npm run render-check -- --profile cn` / `--profile ai` | 15 组合 ×2，`removed_blocks=0`，RENDER OK |
| `npm run cache -- --strict` | 同模式最低 96.19%，STRICT OK |
| `npm run check-memory` | 四种记忆开关组合全部 OK |
| `npm run runtime` | RUNTIME RENDER OK（4 个 tpl） |
| `npm run overrides` | RENDER OK（18 条，无残留标签） |
| `patch-asar.py --status` | 7 个补丁全 `patched`，`integ: MATCH`，`exe sync: MATCH` |
| `apply-product-overrides.py --dry-run` | 18 条 16,727 字节，`no change`（本机已应用） |
| `apply-skill-desc.py --dry-run` | 31 条 3,943 字符 |
| `verify-tool-overrides.py` | 18 条齐全、变量无丢失、体积均下降 |

### 一键全量 + 发布门槛（同日收尾）

- **`deploy.cmd` 无参数 = 全量**：双击自动带 `-Elevate`，一次 UAC 写完 缓存 + 安装镜像 +
  `resources\templates\` + `product.json` + `app.asar`；带参数时行为不变。
  修掉 `pause` 吞退出码：现在结尾是 `exit /b %RC%`，失败不会再显示成成功。`restore.cmd` 同步修。
- **`patch-asar.py` 的 exe 探测改 `WorkBuddy*.exe`**：国际版主程序是 `WorkBuddyAI.exe`，
  以前只认 `WorkBuddy.exe`，AI 安装上永远 `exit 1`（exe 清单同步不了 → 应用起不来）。
- **新增发布门槛**（对应 README「发布要求」一节，机器强制）：
  - `scripts/release-manifest.js`：公开包"必须有什么"的唯一清单（9 组 69 个文件 + 同步清单）。
  - `scripts/release-audit.js`：完整性 / 与源逐字节一致 / 一键全量 / 失败可见 / CRLF / 版本三处一致，任一不过 `exit 1`。
  - `scripts/sync-release.js`：源 → 包镜像（只许在源目录跑），修漂移用。
  - `package.json` 新增 `npm run audit`。
  - 自检记录：删掉 `runtime-templates/user-context-identity.tpl` → `BLOCKED: 2 项`（exit 1）；
    在 `fragments/policy-common.md` 末尾加 1 字节 → `一致性/fragments：内容不同 1`（exit 1）；
    `node scripts/sync-release.js` 修回（copied 1 / unchanged 207）→ `RELEASE OK`（exit 0）。

## v1.1.6 — 2026-11-17

主题：**移除三段——`<quality_policy>` / `<agent_loop>` / `<regional_conventions>`。**
用户指令：「去掉 quality_policy，agent_loop，regional_conventions」。

### 改了什么

| 位置 | 动作 | 字节 |
|---|---|---:|
| `fragments/policy-common.md` | 删掉 `<quality_policy>` 整段（quick / ask / 其余三个分支一起删），只留 `<refusal_policy>` | 4,609 → 572 |
| `fragments/regional-conventions.md` | 整文件删除 | −116 |
| `plugins/**/interactionmode/{craft,plan,expert}/fragments/agent-loop.md` | 整文件删除；`ask/agent-loop.md`（0 字节空文件）一并删除 | −269 / −346 / −346 / 0 |
| `welcome-mode/{work,code,design}.tpl` | 删掉 `regional-conventions` 与 `agent-loop` 两处 include，包括只为它们存在的 `{% elif %}` 分支 | — |
| `fragments/agent-contract.md` | 去掉对已删质量节的悬空引用：`"Done" is defined by the quality policy above.` → `"Done" means verified, not attempted.` | −10 |

### 渲染变化（CN 档，`npm run render` 后 dist 的字节）

| 模板 / 模式 | 前 | 后 | 变化 |
|---|---:|---:|---:|
| work·code craft | 13,538 | 10,315 | −3,223（−23.81%） |
| work·code plan | 14,544 | 11,244 | −3,300（−22.69%） |
| work·code expert | 13,869 | 10,569 | −3,300（−23.79%） |
| work·code ask | 4,741 | 3,438 | −1,303（−27.48%） |
| work·code quick | 1,967 | 1,583 | −384（−19.52%） |
| design craft | 18,939 | 15,716 | −3,223（−17.02%） |
| design plan | 19,945 | 16,645 | −3,300（−16.55%） |
| design expert | 19,270 | 15,970 | −3,300（−17.13%） |
| design ask | 6,512 | 5,209 | −1,303（−20.01%） |
| design quick | 5,265 | 4,765 | −500（−9.50%） |
| **15 份合计** | **167,249** | **132,603** | **−34,646（−20.72%）** |

### 断言与脚本

- `scripts/render-check.js`：新增 `REMOVED_BLOCKS` 断言——任何模板 / 模式 / 档位出现这三个标签即退出码 1。
  原来只在国际版下检查 `<regional_conventions>`，现在是两个档位、五个模式、三份模板全查。
- `scripts/check.js`：原来的 `missing quality policy` 断言（要求存在）反转为「出现即失败」，并覆盖三段。
- `package.json` / `package-lock.json`：1.1.5 → 1.1.6。

### 缓存影响（`npm run cache -- --strict`）

- 同模式场景最低命中率 96.94% → 96.19%，95% 门禁仍通过；换模型场景保持 99.98%。
- 跨模式 `craft~ask` 大幅变好：work·code 19.66% → **36.22%**，design 14.2% → **73.07%**（ask 不再有与 craft 不同的质量分支）。
- 跨模式 `craft~plan` 79.98% → 75.87% 是算术效应，不是缓存退化：删掉的约 2.9K 属于 craft/plan 共享前缀，
  分母同时少了约 3.2K；绝对公共前缀 9,795 → 6,882 字符，与提示词缩短幅度同量级。

### 验证

- `npm run render` / `npm run check` / `npm run render-check -- --profile cn` / `--profile ai` /
  `npm run cache -- --strict` / `npm run check-memory` 全部退出码 0；15 份渲染均 `removed_blocks=0`。
- 被删的原文可从本仓库历史取回（`git show v1.1.5:fragments/policy-common.md` 等）。

## v1.1.5 — 2026-11-17

主题：**一键恢复脚本。**

- 新增 `scripts\restore.cmd`，双击即把每个已发现的安装恢复到最近一次备份；它只是 `deploy.ps1 -Restore` 的包装，
  两个入口共用一套实现：
  `restore.cmd` / `-List` / `-Original` / `-Target <id>` / `-BackupPath <dir>` / `-Template <path>` / `-Elevate` / `-DryRun`。
- `restore.ps1`：安装目录只读时不再中断整次恢复——逐项捕获、跳过并计数，末尾提示
  `Skipped N install-mirror file(s) ... rerun with -Elevate`；缓存侧（运行时真正读取的位置）照常恢复。
- 文档：README × 2 说明"两个双击入口、一套实现"；`scripts/README.md` 的 Files 节补 `restore.cmd`。

实测（非管理员）：

- `restore.cmd -DryRun` / `-List` 正常；真实执行 `-Restore -Target all` 与 `-Restore -Target workbuddy` 均退出码 0，
  分别恢复 148 / 282 个缓存文件，缓存内容哈希保持 `b432ca80…`（备份内容与当前一致，属预期）。
- 备份集里没有安装镜像条目时逐项显示 `remove (was new)` / `skip remove (no install copy expected)`，不会误删安装目录文件。

## v1.1.4 — 2026-11-17

主题：**只从系统登记的信息取安装位置，不再扫盘。**

### 发现来源收敛为五路

| 优先级 | 来源 |
|---|---|
| 1 显式 | `-WorkBuddyPath` / `-ConfigDir`、`targets.json` 的 `installPath` / `configDir` |
| 2 已安装程序 | 卸载登记项：`DisplayIcon` → exe 所在目录、`UninstallString` → 卸载器所在目录、`InstallLocation`；64/32 位视图、HKLM 与 HKCU |
| 3 App Paths | 安装器登记的可执行文件路径，遍历整棵子树，`WorkBuddy*.exe` 都算 |
| 4 数据目录配对 | `<数据目录>\.skill-list-cache.json` 的 `scopeKey` 反查；`settings.json` 兜底 |
| 5 运行中的进程 | 进程名匹配 `WorkBuddy*` 的 exe 实际目录 |

- 删除：`PATH` 查找、开始菜单快捷方式递归、`LocalAppData` / `scoop` / 盘根等目录枚举、整盘扫描（`Find-InstallByScan`）。
- 五路都落空时不再猜目录：直接报错并列出已登记的安装，提示 `-WorkBuddyPath` 或写进 `targets.json`；
  显式给了 `-WorkBuddyPath` 时会为这个未注册的安装建一个临时目标（`custom`），不必先登记。
- 本机两套安装的 `InstallLocation` 都是空的，靠 `DisplayIcon`（`...\WorkBuddy.exe,0`）解析出目录，已实测可用。

### 修复

- 进程枚举改为先按进程名筛再读 `Path`：此前对全部进程读 `Path`，冷启动 `-ListTargets` 要 16.8 s，现在 0.9 s。
- 顶层目标解析失败时打印一行错误并以退出码 1 结束，不再抛裸异常栈。

### 实测

- `-ListTargets` 0.92 s；`-DryRun` / `-Verify` 两目标通过。
- 负面验证：盘根放一个只有 `WorkBuddyScanTest.exe` 的假目录，`-ListTargets` 不收录、显式 `-Target` 报"未登记"并给出指定方法。
- 正面验证：给该假目录传 `-WorkBuddyPath` + `-ConfigDir`，能正常当作 `custom` 目标预演。

## v1.1.3 — 2026-11-17

主题：**安装目录不再假定在 `Program Files`。**

### 发现顺序改为六级，逐级尝试

| 优先级 | 来源 |
|---|---|
| 1 显式 | `-WorkBuddyPath` / `-ConfigDir`、`targets.json` 的 `installPath` / `configDir` |
| 2 配对 | `<数据目录>\.skill-list-cache.json` 的 `scopeKey` 反查；`settings.json` 兜底 |
| 3 注册表 | `App Paths` 遍历整棵子树、卸载项（DisplayName / Publisher / InstallLocation） |
| 4 运行痕迹 | 运行中的进程、`PATH`、开始菜单快捷方式 |
| 5 常见位置 | `LocalAppData\Programs`、`AppData`、`scoop`、`ProgramData`、各固定盘 `Program Files*`、盘根与下一层 |
| 6 扫盘 | 仅当 1–5 落空且显式点名 `-Target` 时 |

- 主程序按 `WorkBuddy*.exe` 通配识别（`WorkBuddy Beta.exe` 一类也能认），安装目录名可以任意。
- 删掉 `apply.ps1` / `restore.ps1` / `verify.ps1` / `pull.ps1` 里各自重复的安装路径解析器：统一走 `target.ps1`，
  不再有 `%USERPROFILE%\.workbuddy` 兜底，也不再提示 `Pass -WorkBuddyPath 'C:\Program Files\WorkBuddy'`。
- 父进程通过 `PROMPTKIT_TARGET_<ID>_INSTALL` / `_CONFIG` 把解析结果传给子脚本，子进程不再重复扫盘，也不会解析到另一套。

### 修复

- `target.ps1` 缺 UTF-8 BOM，含中文注释时 Windows PowerShell 5.1 会按 ANSI 读并报语法错误（`pwsh` 7 看不出来）；已补 BOM。

### 实测

- 假安装放在 `E:\workbuddyspace\_discovery-test\Apps\WorkBuddyTest`（非 Program Files）：仅靠数据目录配对即被识别为 `workbuddytest`。
- 假安装放在 `E:\WorkBuddyScanTest`（无配对、盘根）：显式 `-Target workbuddyscantest` 时靠扫盘兜底识别。
- 真实两套（`C:\Program Files\WorkBuddy`、`D:\Program Files\WorkBuddyAI`）`-ListTargets` / `-DryRun` / `-Verify` 全部通过。

## v1.1.2 — 2026-11-17

- `deploy.cmd -Verify` 透传 `-TraceFile`：指定某个 trace 文件做断言，不必再绕过入口直接调 `verify.ps1`。
  `-Verify` 现在完整覆盖 `-ShowSystemPrompt` / `-Expect` / `-Absent` / `-ListTraces` / `-TraceFile`。

## v1.1.1 — 2026-11-17

主题：**只留一个入口，一次改完所有安装。**

### 单入口

- 只保留 `scripts\deploy.cmd`。`restore.cmd`、`restore-original.cmd`、`verify.cmd`、`pull.cmd`、`apply-asar.cmd`、`restore-asar.cmd`（含 `-elevated` 变体）都变成它的动作：

| 旧命令 | 现在 |
|---|---|
| `restore.cmd -List` / `-All` / `-Template X` / `-Original` | `deploy.cmd -Restore -List` / `-Restore` / `-Restore -Template X` / `-Restore -Original` |
| `verify.cmd -CheckIncludes` | `deploy.cmd -Verify` |
| `pull.cmd` | `deploy.cmd -Pull` |
| `apply-asar.cmd` | `deploy.cmd -Asar` |
| `restore-asar.cmd` | `deploy.cmd -AsarRestore` |

### 多安装同时改

- 默认目标从"一套"变成"所有已发现的安装"：一次运行同时更新 WorkBuddy 与 WorkBuddy AI 的插件缓存，逐目标备份、逐目标校验、汇总表输出，任一目标失败即非零退出。
- `-Elevate` 只弹一次 UAC，随后写两侧安装镜像；`-Target workbuddy` / `-Target workbuddy-ai` 仍可只改一套。
- `targets.json` 不再带默认目标，`-Target` 缺省即 `all`。

### 去写死目录

- `apply.ps1` / `restore.ps1` / `verify.ps1` / `target.ps1` 里写死的 `%USERPROFILE%\.workbuddy` 兜底全部删除：改为按 `.skill-list-cache.json` 的 `scopeKey` 与本安装配对反查，配不上才退回 `%USERPROFILE%\.<id>`。
- `deploy.ps1` 不再写死 `WindowsPowerShell\v1.0\powershell.exe`（用 `$PSHOME`，找不到再回退 PATH），python 先找目标数据目录下的 `binaries\python\versions`。

### 脚本修复

- 子脚本输出在重定向、CI、IDE 终端下被吞掉：改为捕获后经 `Write-Host` 回显。
- `-DryRun` 不再触发 UAC；`-Restore -DryRun` 的备注从"已恢复"改为"仅预演"。
- `patch-asar.py` 退出码 2（当前构建里找不到锚点）记为 `SKIP`，不再算失败。

### 兼容性

- `deploy.cmd -Template` / `-Skill` / `-DryRun` / `-ListTargets` / `-SkipAsarPatch` / `-NoInstall` 等参数不变。
- 旧命令不再存在；仓库历史与 CHANGELOG 旧条目保留原始命令名。

## v1.1.0 — 2026-11-17

主题：**为前缀缓存重排提示词顺序，并把易变内容全部压到最末尾。**

### 顺序重排（三份入口模板统一）

模板的 include 顺序改为三层，顺序本身就是缓存策略：

| 层 | 内容 | 何时会变 |
|---|---|---|
| A 恒定层 | preamble → `policy-common` → `workspace-safety` / `regional-conventions` → `agent-contract` → `search_routing` → `wecom-account-routing` → `tool-routing` → `asking-questions` → `tool-usage-policy` → `agent-skills` → `tool-use` / `task-management` | 只在改提示词或换安装时才变 |
| B 模式层 | `interaction` → `mode-behavior`(ask) → `current-mode` → `agent-loop` → `result-presentation` | 只在对话内切换模式时变 |
| C 会话层 | `memory_system` → `industry_mode_context` → `<memory>` → `<user_memory>` → `<memory_paths>` → `This conversation is powered by …` | 换项目 / 换账号 / 记忆更新 / 换模型 |

- `This conversation is powered by …` 从**首行**移到**最末行**。它带模型名，原来在第 38 个字符就出现，换模型等于整段前缀作废。
- `tool-use` / `task-management` 提前到模式层之前：craft / plan / expert 三份内容逐字相同，提前后会话内换模式的公共前缀明显变长。

### 记忆区

- 新增 `fragments/memory-paths.md`：`memory_system` 正文不再出现绝对路径，只保留"用户级记忆目录 / 工作区记忆目录"这种稳定措辞；真实绝对路径由该片段在提示词最末尾拼成 `<memory_paths>`。
- `fragments/workbuddy-memory-system.md`、`fragments/workspace-project-memory.md` 同步改写为相对措辞。
- 开关矩阵（`scripts/check-memory-blocks.js` 断言）：

| CloudMemoryPlatformEnabled | WorkspaceMemoryDisabled | `<memory_system>` | `<memory_paths>` |
|---|---|---|---|
| false | false | 三层 | 用户级 + 工作区两行 |
| false | true | 两层 | 用户级一行 |
| true | false | 云记忆工具 + 工作区 | 工作区一行 |
| true | true | 只有云记忆 | 不出现 |

### 片段拆分与去重

- 新增 `fragments/workspace-safety.md`（数据目录提醒 + 个人目录安全）、`fragments/regional-conventions.md`（区域约定）、`fragments/asking-questions.md`（提问时机）。原来这三段是内联写在三份模板里的多份副本，改一处要改三处。
- ask 模式的只读约束原来在 `mode-behavior.md` 与 `tool-use.md` 各写一遍，合并到 `tool-use.md` 一处。
- 删除 `plugins/workbuddy-builtin/interactionmode/craft/fragments/interaction-tool-use.md`：该文件无任何 `{% include %}` 引用，是历史遗留副本。
- 精简 `agent-skills.md`（3 行 → 2 行）、`policy-common.md`（去掉与模式无关的两条）、`tool-routing.md`（去掉与技能清单重复的触发词说明）。

### 脚本

- `scripts/deploy.cmd`：只有不带参数（双击）时才 `pause`，脚本化调用不再永久阻塞。
- 修：`scripts/render-check.js` 在公开包里把上下文路径写成了 `release/examples/context.json`，v1.0.6 中直接运行会 ENOENT；同时修正 `nunjucks` 的回退路径与提示文案。
- `scripts/render-check.js`：新增 `--root <dir>`，可从插件缓存快照等外部目录渲染同一套模板。
- 新增 `scripts/cache-prefix.js`：逐场景量"只差一个变量"的两份渲染的最长公共前缀（LCP），`--strict` 可做门禁。
- 新增 `scripts/check-memory-blocks.js`：四种记忆开关组合下 `<memory_system>` / `<memory_paths>` 形状断言。

### 实测（`node scripts/cache-prefix.js`，craft）

| 场景 | 重排前 | 重排后 |
|---|---:|---:|
| 换一个项目目录 | 97.26% | 99.38% |
| 换一个账号 | 94.58% | 99.05% |
| 换模型 | 0.32% | 99.98% |
| 会话内 craft→plan | 29.19% | 79.98% |
| 会话内 craft→expert | 29.20% | 80.00% |
| design craft→plan | 20.17% | 86.08% |

代价：每轮多约 238 字节（`<memory_paths>` 指引 + 末尾路径块），约 60 token。

### 兼容性

- 渲染上下文变量未改名：`WorkbuddyUserMemoryDir` / `WorkbuddyMemoryDir` 仍然由 `memory-paths.md` 使用。
- 部署目标发现、备份、校验、回滚流程不变。
- 本版本不含针对具体安装的运行时改造（`app.asar` 补丁、`product.json` 工具描述覆盖、`runtime-templates/`）：那些依赖安装目录与本机覆盖表，不属于提示词框架。

## v1.0.6 — 2026-11-17

- 安装点自动发现（卸载注册表 / App Paths / 运行中进程 / 各盘 `Program Files*`），`targets.json` 出厂路径为空。
- `deploy.ps1 -Elevate`：同步提权写安装镜像；内置技能只从安装目录枚举，非管理员部署会打印 `!! SKILLS NOT ACTIVE: n/7`。
- `verify -CheckIncludes` 解析 include；安装目录缺失时退化为只校验缓存侧；新增 STALE 闸门（请求早于部署时间时不误判失败）。

## v1.0.4 — 2026-11-15

- 修：安装目录未探测到时，缓存侧部署不再直接失败。

## v1.0.3 — 2026-11-15

- 一键部署默认落到已探测到的运行时。

## v1.0.2 — 2026-11-15

- 新增 `scripts/deploy.cmd` / `deploy.ps1` / `apply.ps1` 一键部署。

## v1.0.1 — 2026-11-15

- 新增公开渲染与结构校验脚本（`render.js` / `check.js`）。

## v1.0.0 — 2026-11-15

- 首个公开版本：前缀缓存友好的提示词框架、公共片段、技能源文件、MIT 许可。
