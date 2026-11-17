# Changelog

本文件记录 PromptKit 公开版的内容变化。提示词是会被逐字节比较的资产，所以每个版本都写明"改了什么位置"，而不只写"优化了提示词"。

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
