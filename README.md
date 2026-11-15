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
deploy.config.json  部署目标与目录布局
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
scripts\deploy.cmd
scripts\deploy.cmd -DryRun
scripts\deploy.cmd -TargetRoot D:\my-runtime
```

默认目标是 `%USERPROFILE%\.promptkit\runtime`，也可以改 `deploy.config.json` 的 `targetRoot`。脚本按 `layout` 放置文件：

| 类型 | 默认目标 |
|---|---|
| 入口模板 | `welcome-mode/` |
| 公共片段 | `prompt-common/fragments/` |
| 交互片段 | `interactionmode-<mode>/fragments/` |
| 技能 | `skills/<name>/` |

覆盖前会备份到 `backups/<timestamp>/`，逐文件校验 SHA256；`-DryRun` 只打印计划。目标目录需要可写，通常不需要管理员（除非你指向需要提权的目录）。技能会整体覆盖到目标的 `skills/`，接入已有运行时前建议先备份或调整 `layout`。

## 技能

`plugins/` 下的技能按“触发条件 + 操作流程”组织，包含定时任务、邮件、设计稿验收、团队编排、产品文档查询等工作流。每个技能只在自己的任务出现时加载，不进入每轮常驻上下文。

接入自己的运行时：

- 替换 `productName`、`modelName`、`dataFolderName` 等变量；
- 把技能目录放到运行时的技能扫描路径，并在路由表中改成自己的技能名；
- 删除不需要的路由行；
- 不要把内部路径、凭据、个人配置写进提示词。

## 不包含

运行时、工具实现、业务代码、备份、归档、开发日志与个人配置。

## 使用

按你的运行时的 include 规则放置 `welcome-mode/`、`fragments/`、`plugins/`，渲染入口模板即可。建议先跑一组最小请求，确认 include 路径、变量与模式分支都能解析，再逐项接入技能。

## License

MIT. See [LICENSE](LICENSE).
