# PromptKit · 桌面 AI 助手系统提示词框架

PromptKit 是一套面向桌面 AI 助手的系统提示词与技能源文件。目标不是“写得更长”，而是让每一轮请求都更短、更稳定、更容易命中前缀缓存，同时把只在特定任务出现的过程放到技能里按需加载。

## 设计原则

1. **前缀缓存优先**
   稳定且跨模式共享的内容放最前（策略、质量闸门），会话级易变内容放最后（记忆）。同一模式换一份记忆内容时，公共前缀仍保持 99% 以上。

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
fragments/       可复用的公共片段（策略、契约、路由、记忆等）
plugins/         交互模式片段与技能源文件（按运行时布局）
scripts/         一键部署、渲染与校验脚本
examples/        示例渲染上下文
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

## 渲染

模板使用 Nunjucks。最小上下文示例：

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
  "UserMemoryContent": ""
}
```

`workMode` 取值：`craft` / `plan` / `ask` / `quick` / `expert`。不同模式会走不同的片段分支；`craft` 是默认执行模式。

渲染全部组合：

```bash
npm install
npm run render   # 3 模板 × 5 模式 → dist/
npm run check    # 结构断言：无模板残留、关键段落与模式边界
```

## 一键部署（Windows）

```bat
scripts\deploy.cmd -ListTargets                    :: 自动发现所有已安装运行时
scripts\deploy.cmd -Target workbuddy-ai
scripts\deploy.cmd -Target workbuddy-ai -Elevate   :: 连安装镜像一起写（UAC）
scripts\deploy.cmd -Target workbuddy-ai -DryRun
scripts\deploy.cmd -Template welcome-mode\work.tpl
scripts\deploy.cmd -Skill team-orchestration
```

**不依赖任何写死的目录**。`scripts\targets.json` 出厂路径为空，安装点由 `scripts\target.ps1` 现场发现：

| 侧 | 来源 |
|---|---|
| 安装 | 卸载注册表（`DisplayName like *WorkBuddy*`）、`App Paths`、运行中的 `WorkBuddy*` 进程、每个固定盘的 `<盘>:\Program Files*\WorkBuddy*` |
| 数据 | `%USERPROFILE%\.workbuddy*`、`WORKBUDDY_CONFIG_DIR` |
| 配对 | `<数据目录>\.skill-list-cache.json` 的 `scopeKey` 里那条 `...\<安装目录>\resources\app.asar.unpacked\resources\plugins\workbuddy-builtin\skills` |

目标 id 由安装目录名推导（`WorkBuddy` → `workbuddy`，`WorkBuddyAI` → `workbuddy-ai`）。只有需要钉死某个路径时，才在 `targets.json` 里填 `installPath` / `configDir`。

### 提示词与技能是两个读取点

| 资产 | 读自 | 写入条件 |
|---|---|---|
| 模板、片段、交互模式文本 | `<数据目录>\plugins\cache\workbuddy-builtin\<插件>\<版本>\` | 任意 deploy，**不需要管理员** |
| 内置技能 `SKILL.md` | `<安装目录>\resources\app.asar.unpacked\resources\plugins\workbuddy-builtin\skills\`（用户技能另在 `<数据目录>\skills\`）——**从不读插件缓存** | 需要 `-Elevate` 或管理员终端 |

所以非管理员部署只改提示词，技能不会出现：`deploy.ps1` 会直接打印 `!! SKILLS NOT ACTIVE: n/7 ...` 并给出 `-Elevate` 命令。应用升级会覆盖安装目录，**每次升级后重跑一次 `deploy.cmd -Target <id> -Elevate`**。

- 覆盖前备份到 `backups/<timestamp>/`（manifest 记录 `target`），逐文件 SHA256 校验。
- 校验 7 个内置技能，然后跑一致性检查；`-SkipVerify` 可跳过。
- `-Detailed` 输出完整映射；`-Template` / `-Skill` 只替换单个文件或技能。

## 技能

`plugins/` 下的技能按“触发条件 + 操作流程”组织，包含定时任务、邮件、设计稿验收、团队编排、产品文档查询等工作流。每个技能只在自己的任务出现时加载，不进入每轮常驻上下文。

部署脚本会把 `plugins/` 下的技能写到运行时的技能目录并完成登记；只改一个技能时用 `scripts\deploy.cmd -Skill <name>`。不要把内部路径、凭据、个人配置写进提示词。

> 技能能不能被模型自动调用，取决于它是否出现在运行时的技能清单里——而清单只扫描安装目录与用户技能目录。只写插件缓存 = 提示词里提到技能名、模型却找不到，表现上就是“改了不生效”。

## 不包含

运行时、工具实现、业务代码、备份、归档、开发日志与个人配置。

## 使用

运行 `scripts\deploy.cmd` 即可：脚本自动检测运行时并写入插件缓存（管理员同时写安装镜像），不需要手动放置文件。只想渲染/校验时用 `npm run render` / `npm run check`。

## License

MIT. See [LICENSE](LICENSE).
