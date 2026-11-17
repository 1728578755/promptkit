# PromptKit · 桌面 AI 助手系统提示词框架

> 当前版本 **v1.1.5**（2026-11-17）· 变更记录见 [CHANGELOG.md](CHANGELOG.md)

PromptKit 是一套面向桌面 AI 助手的系统提示词与技能源文件。目标不是"写得更长"，而是让每一轮请求都更短、更稳定、更容易命中前缀缓存，同时把只在特定任务出现的过程放到技能里按需加载。

## 设计原则

1. **前缀缓存优先**
   提示词按"变得多快"分层：恒定内容在前（策略、安全、契约、路由、技能触发），模式相关内容居中，会话级易变内容压到最后（记忆、路径、模型名）。换一个项目、换一个账号、换一个模型，公共前缀仍然保持 99% 以上。顺序本身就是设计，见下文[提示词顺序与前缀缓存](#提示词顺序与前缀缓存)。

2. **惰性技能**
   定时任务、邮件、设计验收、团队编排、产品文档等流程放 `plugins/` 下的技能目录；基座只留一行触发。

3. **模式边界**
   Agent / Plan / Ask / Quick 各自声明能做什么、不能做什么；工具与副作用限制优先于其他策略。

4. **质量闸门**
   完成定义 → 风险分层验证 → 证据规则 → 交付前检查。短答案也要有检查，长答案不堆砌。

5. **团队面板**
   并行或多角色工作走团队成员面板（创建 → 派任务 → 收结论 → 关闭），而不是散装子代理。

## 目录

```
welcome-mode/    三份入口模板（通用 / 代码 / 设计）
fragments/       可复用的公共片段（策略、契约、安全、路由、记忆）
plugins/         交互模式片段与技能源文件（按运行时布局）
scripts/         一键部署、渲染、缓存量化与校验脚本
examples/        示例渲染上下文
CHANGELOG.md     版本记录
package.json     nunjucks 依赖与 npm scripts
```

模板通过 Nunjucks include 组合片段。本目录保留源布局，放置到运行时时按下面映射：

| 本目录 | 运行时 |
|---|---|
| `welcome-mode/` | `welcome-mode/` |
| `fragments/` | `prompt-common/fragments/` |
| `plugins/**/interactionmode/<mode>/fragments/` | `interactionmode-<mode>/fragments/` |
| `plugins/**/skills/<name>/` | `skills/<name>/` |

模板里的 include 名（`prompt-common/fragments/...`、`interactionmode-*/fragments/...`）以运行时路径为准。

## 提示词顺序与前缀缓存

三份模板的 include 顺序统一成三层，**顺序就是缓存策略**：

| 层 | 内容 | 何时会变 |
|---|---|---|
| **A 恒定层** | preamble → `policy-common` → `workspace-safety` / `regional-conventions` → `agent-contract` → `search_routing` → `wecom-account-routing` → `tool-routing` → `asking-questions` → `tool-usage-policy` → `agent-skills` → `tool-use` / `task-management` | 只在改提示词或换安装时才变；craft / plan / expert 三者逐字相同 |
| **B 模式层** | `interaction`（frontmatter 不进正文）→ `mode-behavior`（ask）→ `current-mode` → `agent-loop` → `result-presentation` | 只在对话内切换模式时变 |
| **C 会话层** | `memory_system` → `industry_mode_context` → `<memory>` → `<user_memory>` → `<memory_paths>` → `This conversation is powered by …` | 换项目 / 换账号 / 记忆更新 / 换模型，全部压到最后 |

四条规则：

1. **带模型名的那一行放最末**。`This conversation is powered by …` 曾经在首行，第 38 个字符就带模型名，换模型等于整段前缀作废；移到末尾后同样场景命中率 0.32% → 99.98%。
2. **记忆区不写绝对路径**。`memory_system` 只说"用户级记忆目录 / 工作区记忆目录"，真实值由 `fragments/memory-paths.md` 在最末尾渲染成 `<memory_paths>`。换账号、换项目都不会动到前面的公共部分。
3. **跨模式逐字相同的片段提前**。`tool-use` / `task-management` 在 craft / plan / expert 三份文件里内容一致，放到模式层之前，会话内换模式还能留住更长前缀。
4. **`interactionmode-*/interaction.md` 的 frontmatter 不进正文**（宿主把它解析成工具集），所以它在正文里的位置不影响缓存。

量化和断言（改顺序后必跑）：

```bash
npm run cache            # 逐场景量最长公共前缀（LCP），中文表格输出
npm run cache -- --strict   # 同模式场景低于 95% 直接失败；跨模式用 --mode-threshold N 打开
npm run check-memory     # 四种记忆开关组合下 <memory_system> / <memory_paths> 的形状
npm run render-check -- --profile cn   # 15 组合渲染；--profile ai 跑国际版上下文
```

实测（craft，`npm run cache`）：

| 场景 | 重排前 | 重排后 |
|---|---:|---:|
| 换一个项目目录 | 97.26% | 99.38% |
| 换一个账号 | 94.58% | 99.05% |
| 换模型 | 0.32% | 99.98% |
| 会话内 craft→plan | 29.19% | 79.98% |
| 会话内 craft→expert | 29.20% | 80.00% |
| design craft→plan | 20.17% | 86.08% |

代价是每轮多约 238 字节（`<memory_paths>` 的指引加末尾路径块），约 60 token。

> 改顺序只应该改变"位置"，不改变"内容"。核对办法：把改前快照与改后渲染各去空行排序后 diff，差异只应来自本次有意改的那几行。

## 渲染

模板使用 Nunjucks。最小上下文示例见 `examples/context.json`：

```json
{
  "workMode": "craft",
  "productName": "Assistant",
  "modelName": "Model",
  "dataFolderName": ".assistant",
  "ResponseLanguage": "中文",
  "productFeatures": { "DisableMultimodalGeneration": false },
  "WorkingMemoryContent": "",
  "UserInstructionsContent": "",
  "UserMemoryContent": "",
  "WorkbuddyUserMemoryDir": "~/.assistant/user",
  "WorkbuddyMemoryDir": ".assistant/memory"
}
```

`workMode` 取值：`craft` / `plan` / `ask` / `quick` / `expert`。不同模式会走不同的片段分支；`craft` 是默认执行模式。

```bash
npm install
npm run render        # 3 模板 × 5 模式 → dist/
npm run check         # 结构断言：无模板残留、关键段落、模式边界
```

`npm run render-check` 在渲染之外还断言：没有残留 `{{` / `{%`、国际版上下文下没有 `<regional_conventions>`、任何模式都没有 `<content_policy>`。

## 一键部署与恢复（Windows）

**两个双击入口，一套实现**：

- `scripts\deploy.cmd`：部署（以及 `-Verify` / `-Pull` / `-Asar` 等动作）。
- `scripts\restore.cmd`：一键恢复，等价于 `deploy.cmd -Restore`，双击即把每个已发现的安装恢复到最近一次备份。

```bat
scripts\deploy.cmd                        :: 部署到所有已发现的安装（CN + AI 一起改）
scripts\deploy.cmd -Elevate               :: 同上，先弹一次 UAC，再写两侧安装镜像
scripts\deploy.cmd -Target workbuddy-ai   :: 只改一套（默认 all）
scripts\deploy.cmd -ListTargets           :: 列出发现的安装与数据目录
scripts\deploy.cmd -DryRun                :: 预演，不写文件
scripts\deploy.cmd -Template welcome-mode\work.tpl
scripts\deploy.cmd -Skill team-orchestration

scripts\restore.cmd                       :: 一键恢复：每个目标回到最近一次备份
scripts\restore.cmd -List                 :: 列出每个目标的备份集
scripts\restore.cmd -Original             :: 恢复原始基线
scripts\restore.cmd -Target workbuddy-ai  :: 只恢复一套
scripts\restore.cmd -Elevate              :: 连安装镜像一起恢复（一次 UAC）
scripts\restore.cmd -DryRun               :: 只看计划

scripts\deploy.cmd -Verify                :: 每个目标的一致性校验（含 include 解析）
scripts\deploy.cmd -Pull                  :: 每个目标快照到 archive\pulls\
scripts\deploy.cmd -Asar                  :: 关掉应用后：提权 → 打 app.asar 补丁 → 重启
scripts\deploy.cmd -AsarRestore           :: 还原 app.asar
```

一次运行会把**所有发现的安装**都改掉，不需要为每个安装各跑一次。

**不依赖任何写死的目录**。`scripts\targets.json` 出厂路径为空，安装点由 `scripts\target.ps1` 现场发现。
**只认系统里登记过的安装，不扫盘、不猜目录**，按下面顺序取值：

| 优先级 | 来源 |
|---|---|
| 1 显式 | `-WorkBuddyPath` / `-ConfigDir`，或 `targets.json` 的 `installPath` / `configDir` |
| 2 已安装程序 | 卸载登记项：`DisplayIcon` → exe 所在目录、`UninstallString` → 卸载器所在目录、`InstallLocation`；64/32 位视图、HKLM 与 HKCU 都查 |
| 3 App Paths | 安装器登记的可执行文件路径，遍历整棵子树，`WorkBuddy*.exe` 都算 |
| 4 数据目录配对 | `<数据目录>\.skill-list-cache.json` 的 `scopeKey` 反查安装目录；`settings.json` 里的 `...\resources\app.asar.unpacked` 兜底 |
| 5 运行中的进程 | 进程名匹配 `WorkBuddy*` 的 exe 实际所在目录 |

五路都没有结论时直接报错并列出已登记的安装，不会去翻硬盘：未注册的便携版用
`-WorkBuddyPath "<目录>"` 指定一次，或写进 `targets.json` 长期钉住。

主程序按 `WorkBuddy*.exe` 通配识别；安装目录名可以任意，只要里面有 `resources\app.asar` 或 `WorkBuddy*.exe` 就认。
目标 id 由安装目录名推导（`WorkBuddy` → `workbuddy`，`WorkBuddyAI` → `workbuddy-ai`）；数据目录先按上面的配对反查，配不上才退回 `%USERPROFILE%\.<id>`。只有需要钉死某个路径时，才在 `targets.json` 里填 `installPath` / `configDir`。

### 提示词与技能是两个读取点

| 资产 | 读自 | 写入条件 |
|---|---|---|
| 模板、片段、交互模式文本 | `<数据目录>\plugins\cache\workbuddy-builtin\<插件>\<版本>\` | 任意 deploy，**不需要管理员** |
| 内置技能 `SKILL.md` | `<安装目录>\resources\app.asar.unpacked\resources\plugins\workbuddy-builtin\skills\`（用户技能另在 `<数据目录>\skills\`）——**从不读插件缓存** | 需要 `-Elevate` 或管理员终端 |

所以非管理员部署只改提示词，技能不会出现：`deploy.ps1` 会直接打印 `!! SKILLS NOT ACTIVE: n/7 ...` 并给出 `-Elevate` 命令。应用升级会覆盖安装目录，**每次升级后重跑一次 `deploy.cmd -Elevate`**。

- 覆盖前备份到 `backups/<timestamp>/`（manifest 记录 `target`），逐文件 SHA256 校验。
- 校验 7 个必备内置技能，然后逐目标跑一致性检查；`-SkipVerify` 可跳过。
- `-Detailed` 输出完整映射；`-Template` / `-Skill` 只替换单个文件或技能。
- 脚本里不再有写死的产品目录、数据目录或 PowerShell 路径：`$PSHOME` 起子进程，python 先找目标数据目录下的 `binaries\python\versions`。

## 技能

`plugins/` 下的 31 个技能按"触发条件 + 操作流程"组织，包含定时任务、邮件、设计稿验收、团队编排、产品文档查询等工作流。每个技能只在自己的任务出现时加载，不进入每轮常驻上下文。

部署脚本会把 `plugins/` 下的技能写到运行时的技能目录并完成登记；只改一个技能时用 `scripts\deploy.cmd -Skill <name>`。不要把内部路径、凭据、个人配置写进提示词。

> 技能能不能被模型自动调用，取决于它是否出现在运行时的技能清单里——而清单只扫描安装目录与用户技能目录。只写插件缓存 = 提示词里提到技能名、模型却找不到，表现上就是"改了不生效"。

## 不包含

运行时、工具实现、业务代码、备份、归档、开发日志与个人配置。

也不包含针对某一台机器的运行时改造（`app.asar` 补丁、`product.json` 工具描述覆盖、`runtime-templates/`）——那些依赖具体安装目录和本机覆盖表，单独维护，不属于提示词框架。

## 使用

运行 `scripts\deploy.cmd` 即可：脚本自动检测所有安装并写入各自的插件缓存（加 `-Elevate` 同时写安装镜像），不需要手动放置文件、不需要为每个安装各跑一次。只想渲染/校验时用 `npm run render` / `npm run check` / `npm run cache`。

## License

MIT. See [LICENSE](LICENSE).
