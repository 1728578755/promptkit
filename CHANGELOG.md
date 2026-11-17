# Changelog

本文件记录 PromptKit 公开版的内容变化。提示词是会被逐字节比较的资产，所以每个版本都写明"改了什么位置"，而不只写"优化了提示词"。

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
