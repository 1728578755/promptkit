# PromptKit · 桌面 AI 助手系统提示词框架

> 当前版本 **v1.3.17**（2026-11-21）· 变更记录见 [CHANGELOG.md](CHANGELOG.md)

PromptKit 是一套面向桌面 AI 助手的系统提示词与技能源文件。目标不是"写得更长"，而是让每一轮请求都更短、更稳定、更容易命中前缀缓存，同时把只在特定任务出现的过程放到技能里按需加载。

## 发布要求（Release gate）

> **硬要求：公开包必须做到「双击一次，把全部提示词与运行层改造都改到」。** 缺任何一类输入，这个包就是残包，
> 不允许发布。发版前必须跑 `npm run audit`，输出 `RELEASE OK` 才许打 tag。

| # | 要求 | 机器怎么验 |
|---|---|---|
| 1 | **完整性**：部署链路需要的输入全在包里——`welcome-mode/`、`fragments/`、`plugins/`、`runtime-templates/`、`overrides/`、`apply-product-overrides.py`、`patch-asar.py`、`_install.py`、全部渲染与诊断脚本、两份用户文档 | `release-audit.js` 逐项检查 10 组共 75 个文件 |
| 2 | **一致性**：包内提示词与部署链路必须与维护者源目录逐字节相同，不许"包里一份、源里一份" | workspace 模式下逐文件 SHA256 比对 |
| 3 | **一键全量**：`deploy.cmd` 无参数必须带 `-Elevate`，一次 UAC 写完缓存 + 安装镜像 + `resources\templates\` + `product.json` + `app.asar` | 读 `deploy.cmd` 源码断言 |
| 4 | **失败可见**：`deploy.cmd` 必须 `exit /b %RC%`，不许让 `pause` 把失败吞成 0 | 同上 |
| 5 | **CRLF**：`.cmd` 必须是 CRLF，LF 行尾会让 cmd.exe 解析异常 | 数 `\r\n` |
| 6 | **版本一致**：`package.json` / README 顶部 / CHANGELOG 首条三处相同 | 正则比对 |

维护者流程（在源目录）：

```bash
node scripts/sync-release.js        # 源 → release/，镜像提示词、运行层输入与部署脚本
cd release && npm run audit         # 必须输出 RELEASE OK
cd release && git add -A && git commit && git tag vX.Y.Z && git push origin main --tags
```

在公开包内（没有上层源目录）跑 `npm run audit` 会自动切成 package 模式：只查完整性与「可一键」，
因为那时包本身就是源。`sync-release.js` 只允许在源目录跑，防止造出 `release/release/`。

## 设计原则

1. **前缀缓存优先**
   提示词按"变得多快"分层：恒定内容在前（策略、安全、契约、路由、技能触发），模式相关内容居中，会话级易变内容压到最后（记忆、路径、模型名）。换一个项目、换一个账号、换一个模型，公共前缀仍然保持 99% 以上。顺序本身就是设计，见下文[提示词顺序与前缀缓存](#提示词顺序与前缀缓存)。

2. **惰性技能**
   定时任务、邮件、设计验收、团队编排、产品文档等流程放 `plugins/` 下的技能目录；基座只留一行触发。

3. **模式边界**
   Agent / Plan / Ask / Quick 各自声明能做什么、不能做什么；工具与副作用限制优先于其他策略。

4. **不写质量宣言**
   2026-11-17 起基座不再注入 `<quality_policy>`（原「完成定义 → 风险分层验证 → 证据规则 → 交付前检查」五段）。验证要求改由三段承担：模式边界（Ask 只读、Quick 从简）、`agent-contract` 的一句话「Done means verified, not attempted」，以及具体技能里的验收步骤。要加验证要求，写进技能或对应模式片段，而不是塞回恒定层——那是每轮都要付的固定成本。

5. **团队面板**
   并行或多角色工作走团队成员面板（创建 → 派任务 → 收结论 → 关闭），而不是散装子代理。

## 目录

```
welcome-mode/      三份入口模板（通用 / 代码 / 设计）
fragments/         可复用的公共片段（策略、契约、安全、路由、记忆）
plugins/           交互模式片段与技能源文件（按运行时布局）
overrides/         工具描述与技能描述的精简覆盖表（按 name 精确覆盖）
runtime-templates/ 运行时直接读取的 tpl（user-context / 模式提醒）
scripts/           一键部署、asar 补丁、渲染、缓存量化与校验脚本
examples/          示例渲染上下文
HOWTO-修改系统提示词.md   改哪个文件 → 怎么应用 → 怎么验证（操作手册）
TROUBLESHOOTING-not-effective.md  「改了不生效」的排查链与复用方法论
CHANGELOG.md       版本记录
package.json       nunjucks 依赖与 npm scripts
```

模板通过 Nunjucks include 组合片段。本目录保留源布局，放置到运行时时按下面映射：

| 本目录 | 运行时 |
|---|---|
| `welcome-mode/` | `welcome-mode/` |
| `fragments/` | `prompt-common/fragments/` |
| `plugins/**/interactionmode/<mode>/fragments/` | `interactionmode-<mode>/fragments/` |
| `plugins/**/skills/<name>/` | `skills/<name>/` |
| `runtime-templates/<name>.tpl` | `<安装目录>/resources/templates/<name>.tpl` |
| `overrides/tools/*.compact.json` | `<安装目录>/resources/app.asar.unpacked/cli/product.json` 的 `prompts[]` |

模板里的 include 名（`prompt-common/fragments/...`、`interactionmode-*/fragments/...`）以运行时路径为准。

## 提示词顺序与前缀缓存

三份模板的 include 顺序统一成三层，**顺序就是缓存策略**：

| 层 | 内容 | 何时会变 |
|---|---|---|
| **A 恒定层** | preamble → `policy-common` → `workspace-safety` → `agent-contract` → `search_routing` → `wecom-account-routing` → `tool-routing` → `asking-questions` → `tool-use` | 只在改提示词或换安装时才变；craft / plan / expert 三者逐字相同 |
| **B 模式层** | `interaction`（frontmatter 不进正文）→ `mode-behavior`（ask）→ `current-mode` | 只在对话内切换模式时变 |
| **C 会话层** | `memory_system` → `industry_mode_context` → `<memory>` → `<user_memory>` → `<memory_paths>` → `This conversation is powered by …` | 换项目 / 换账号 / 记忆更新 / 换模型，全部压到最后 |

四条规则：

1. **带模型名的那一行放最末**。`This conversation is powered by …` 曾经在首行，第 38 个字符就带模型名，换模型等于整段前缀作废；移到末尾后同样场景命中率 0.32% → 99.98%。
2. **记忆区不写绝对路径**。`memory_system` 只说"用户级记忆目录 / 工作区记忆目录"，真实值由 `fragments/memory-paths.md` 在最末尾渲染成 `<memory_paths>`。换账号、换项目都不会动到前面的公共部分。
3. **跨模式逐字相同的片段提前**。`tool-use` 在 craft / plan / expert 三份文件里内容一致，放到模式层之前，会话内换模式还能留住更长前缀（`task-management` 已于 2026-11-21 随另外四段一并移除）。
4. **`interactionmode-*/interaction.md` 的 frontmatter 不进正文**（宿主把它解析成工具集），所以它在正文里的位置不影响缓存。

> **2026-11-21 用户要求再删五段**：`<tool_usage_policy>` / `<agent_skills>` / `<task_management>` / `<result_presentation>` / `<final_answer_instructions>` 整段移除。因此 A 层不再有 `tool-usage-policy` / `agent-skills` / `task-management`，B 层不再有 `result-presentation`；ask 模式不再有任何「最终答复」约束段。上表描述的是**移除后**的现行顺序。

> **2026-11-21 追加：旧会话的 reminder 重放也要清，脚本结束自动拉起。**
>
> 模板层只影响新会话；已经打开的会话在创建时固定了系统提示，第一条用户消息里的
> `<system-reminder data-role="user-context">` 也会随历史每个请求重发。`user-context-durable`
> 只阻止"重新注入"，不阻止"历史重放"。
>
> `scripts/patch-cli-trim.py` 在 agent runtime 的 provider 边界清一次（`1849.*.mjs`）：
> 第一轮保留 user-context；后续轮次从历史里整块删除；系统提示同时删五段 +
> `<current_time>` + `<additional_data>`。`deploy.cmd -CliPatch` 一次 UAC 即可，不需要关应用；需要立刻让新聊天用上新 chunk 时跑 `python scripts/refresh-cli-prewarm.py --apply`（只停 `kind=prewarm` + `status=idle`，不碰主程序/当前聊天）。
> 默认全量 deploy 也会在 asar 之前跑它。
>
> **v1.3.5 修正（v1 实际没生效）**：v1 判据要求消息带 `type:"message"`，而真实元素只有 `role`，
> 于是"真实用户消息"恒为空、每条都按首轮保留，历史 user-context 一次都没删过；
> 另外数组 content 清理后 `changed` 没置位，清理结果被整个丢掉。v2 两处都修了，
> 并且 `deploy.ps1` 给每套安装分别传 `--file`——AI 版此前一直在打 CN 的路径，自己那份从未被改。
> `--status` 现在输出 `version:`，`deploy` 见到 v1 会先从最早备份还原再重打。
> **v1.3.6 追加**：用户要求“原样输出完整上下文”时，模型会凭记忆编造（17:12 实际只有 2 条消息，模型却输出 56KB 并自称 Claude Code 系统提示）。新增 `context-dump` 技能 + `dump-latest-context.py`：必须读最新 trace 并逐字输出；trace 只存 20,000 字符前缀时明确标记 `[TRUNCATED]`。已被长回答污染的会话请新开聊天。
>
> **v1.3.7 追加**：`current_time` 还有一条上行路径——`/main/server.js` 的 `resolveMessage`
> 把 `t.currentTime` 发给云端，云端据此生成 `data-role="additional-data"` 块。新增
> `current-time-cloud` 等长补丁把这行改成 `0 … &&(…)`；CLI v3 同时把清空后的
> `additional-data` 空壳整块删除。asar 补丁需关掉 WorkBuddy 后跑 `deploy.cmd` 才写入；
> CLI v3 已在模型边界先清掉。chunk 查找改为按内容回退，别的版本/电脑也能打。
>
> **v1.3.8 追加（另一台电脑上没生效的根因）**：`deploy.ps1` 之前按 `1849.*.mjs` 找 CLI chunk，
> 文件名一变就只打一句 `cli runtime chunk not found` 然后 **SKIP**，退出码仍是 0——看着成功，
> 实际模型边界一个补丁都没打。现在改成把 `cli\dist` 整目录交给 `patch-cli-trim.py`，
> **递归 + 按内容**（`modelInput.instructions` + `modelInput.input`）定位，找不到就 **FAIL**；
> 打完自动 `refresh-cli-prewarm.py --apply`（只停 `kind=prewarm`+`status=idle`，按 PID）。
> `deploy` 结束会自动跑 `scripts/verify-stack.py` 逐层验收：CLI `version=3/PATCHED`、
> asar 9 个补丁逐个、`integ=MATCH`/`exe=MATCH`、最新 trace 的
> `current_time`/`additional-data`/`user-context` 计数，最后打印 `RESULT: PASS|FAIL`
> 并写 `reports/verify-latest.txt`。**只有 PASS 才算部署成功。**
> `--self-test` 也加入了用户原样 182 字符消息（`data-role="additional-data"` + `<current_time>` + `<user_query>`）
> 的断言：清理后必须只剩 `<user_query>…</user_query>`。
>
> **v1.3.9 追加（诊断）**：`scripts\doctor.cmd`（只读、免管理员）逐项打印：
> 缓存模板每个版本、CLI chunk 的 `bytes/version/mtime` 与三个锚点命中数、asar 九个补丁逐条、
> 每个进程的启动时间与补丁时间对比、idle prewarm 数、最新 trace 的逐块计数，
> 并把**磁盘上那份 chunk 里的清理函数抽出来**拿原样块跑一遍（`INSTALLED HELPER OK`）。
> 结论只有三选一：`EFFECTIVE` / `UNPROVEN`（还没有补丁后的新请求）/ `NOT EFFECTIVE: <哪层、什么现象>`，
> 报告写到 `reports\doctor-latest.txt`。`deploy` 结束时的验收也改成调用它。
>
> **v1.3.10 追加**：`doctor.cmd` 双击不再秒关（结束时 `pause`，并优先使用 WorkBuddy 自带
> Python；缺 Python 时也打印安装目录与 chunk 名，不再静默退出），结论放在第一屏。
> 同时修掉一个真 bug：fetch 出口清理拿到的是 JSON 文本，换行是字面量 `\n`，
> `trim()` 不会把它当空白，导致"空 `additional-data` 壳"在 JSON 层从未被删过；
> 现已改为结构扫描 + 转义空白判断，`--self-test` 会拿原样块跑 fetch body 断言。
> 已打过补丁的 chunk 也会**整段升级 helper**（之前 `--apply` 见到标记就跳过，内部修复进不去）。
>
> **v1.3.11 追加（新电脑 / 换路径）**：整套工具不再写死任何安装路径。
> 发现顺序：`WORKBUDDY_INSTALL` → 卸载注册表 → 运行中的 `WorkBuddy*.exe` → 开始菜单/桌面快捷方式 →
> 有界磁盘扫描（每盘 Program Files / Program Files (x86) / Apps / Programs / Software /
> `Users\*\AppData\Local\Programs`，深度 2），再用文件夹名 / `WorkBuddy*.exe` / `product.json`
> 过滤掉别家 Electron 应用。`patch-cli-trim.py` 的 `--file` 默认值改为自动发现。
> 没有 Python 时 `doctor.cmd` 自动转用 `doctor-basic.ps1`（纯 PowerShell），
> 同样会指出每套安装缺哪个标记。另修一个误报：`user_info` / `user-context` /
> `connector-status` 是首轮一次性上下文，不该按"必须 0"判定。
>
> **v1.3.12 追加（按诊断报告修）**：`doctor` 不再一套安装拖垮全局——先判定"当前在用"
> （进程路径，或最新 trace 对应的配置目录），只有它失败才算 `NOT EFFECTIVE`，
> 其余给 `EFFECTIVE/PARTIAL` 并注明，deploy 也不会再因为另一套安装报红。
> `target.ps1` 补上开始菜单快捷方式与有界磁盘扫描，绿色版/便携版也能被 `deploy.cmd` 发现。
> `doctor-basic.ps1` 补上模板缓存检查与 `.workbuddy-ai`。`deploy` 收口会读真实缓存：
> 空缓存（新电脑冷启动）或模板里仍有被删块 → **硬失败**并提示先启动一次客户端。
> 没有系统 Python 时优先用安装自带的 `resources\vendor\python\python.exe`。
> CLI 补丁不再假设 chunk id `1849` 与混淆变量 `g`/`w`，改为按语句前缀和调用点现场发现。
>
> **v1.3.17 追加（零依赖兜底）**：`scripts\wb-fix-lite.cmd` 只需要 Windows 自带 PowerShell，
> 不需要 Python / Node，也不依赖任何 chunk 文件名、chunk id、混淆变量名或代码锚点——
> 它把一段自包含的 fetch 清理**插在 chunk 最前面**，任何构建都能生效。带备份、可 `-Restore`、
> 写入后校验"原文一字不差跟在后面"，有 Node 时再跑 `node --check`，失败自动回滚。
> `deploy.ps1` 的锚点补丁失败时会自动退到这条；`doctor.py` 把"只有轻量补丁"标成 WARN 而不是 FAIL。
>
> **自动拉起（2026-11-21 收口到一处）**：凡是会写盘的动作——`deploy`（全量）、`-CliPatch`、`-Asar`、
> `-AsarRestore`、`-Restore`——结束时都检查应用是否在运行；没在运行就经 `explorer.exe` 启动一次。
> **失败也启动**（脚本结束就该看到结果）；只读动作（`-Verify` / `-Pull` / `-ListTargets`）与 `-DryRun`
> 不启动；`-NoRelaunch` 关闭。**从不结束进程**：应用在跑时 asar 补丁按 exit 4 拒绝写入，
> 需要你自己完全退出后重跑。asar 的两处调用统一传 `--no-launch`，拉起只由脚本末尾做——
> 这样 `uc-seen --seed` 与 `verify` 先跑完再开进程，也不会开成两个实例。
> 已打开的旧聊天需要新开一个聊天或重启应用才能加载新 chunk。实测 `1849.*.mjs` 247,364 → 250,670 字节，
> `--self-test` OK，真实 20,003 字符请求前缀清理后 17,672（删 2,331）。
> 回归断言：`powershell -File scripts/test-launch-coverage.ps1`（20 条）。

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

`npm run render-check` 在渲染之外还断言：没有残留 `{{` / `{%`、任何模式都没有 `<content_policy>`，以及 2026-11-17 移除的三段（`<quality_policy>` / `<agent_loop>` / `<regional_conventions>`）在 CN 与 AI 两个档位、五个模式下都为 0——出现即退出码 1。

**同一份脚本、两种布局**：本包根目录下有 `node_modules/` 与 `examples/`；维护者工作区里这两样在 `release/` 下面。
`scripts/_deps.js` 是唯一判断这件事的地方（`loadNunjucks()` / `dataRoot()` / `dataFile()`），其余脚本都从它取，
所以同一个文件能在两棵树里**逐字节相同**——`release-audit.js` 会逐个比对，没有豁免名单。
这直接修掉了两类漂移：一边有 `--root`、另一边没有；中文表格一边按双宽对齐、另一边按字符数补空格。
`--root <dir>` 仍然可用：目标树自带 `examples/context.json` 就用它的，没有则回退到数据根。

## 一键部署与恢复（Windows）

**两个双击入口，一套实现**：

- `scripts\deploy.cmd`：部署（以及 `-Verify` / `-Pull` / `-Asar` 等动作）。
- `scripts\restore.cmd`：一键恢复，等价于 `deploy.cmd -Restore`，双击即把每个已发现的安装恢复到最近一次备份。

```bat
scripts\deploy.cmd                        :: 双击 = 全量：自动 -Elevate，一次 UAC 写完 缓存 + 安装镜像 + runtime-templates + product.json + app.asar
scripts\deploy.cmd -Elevate               :: 同上（显式写法；已在管理员上下文时不会二次提权）
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
scripts\deploy.cmd -Asar                  :: 退出应用后：提权 → 打补丁 → 自动把应用启动起来
scripts\deploy.cmd -Asar -NoRelaunch       :: 同上，但打完不启动
scripts\deploy.cmd -AsarRestore           :: 还原 app.asar
scripts\deploy.cmd -SkipAsarPatch         :: 本次部署不打 asar 补丁
scripts\deploy.cmd -SkipProductOverrides  :: 本次部署不动 product.json
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

## 运行层改造：asar / product.json / runtime-templates

提示词只是第一层。真正进到请求里的还有三处在**模板之外**：打包在 `app.asar` 里的现场拼装代码、
`product.json` 里的工具描述、以及 `resources\templates\` 下运行时直接读取的 tpl。三处都由同一次
`scripts\deploy.cmd` 处理，不需要额外步骤（asar 需要先关掉应用）。

### 1. `app.asar` 补丁：8 处等长替换

补丁只做**等长原地替换**，asar 头里的 offset/size 全部不变：

| 补丁 | 去掉的东西 |
|---|---|
| `current-time` | 每轮注入的 `<current_time>` 块（内容一直在变，等于每轮让前缀缓存失效） |
| `user-info` | `<user_info>` 里的 OS / 主题 / `Time:`；Shell 兜底写明 Git Bash |
| `connector-status` | `<connector-status>` 里三句重连说明 |
| `project-layout` | `<project_layout>` 的内层标签（保留目录树） |
| `project-context-once` | `project_context` 回到首轮一次，不再每轮带目录快照 |
| `scene-chip` | `@scene#…` 场景引用不再序列化进消息文本 |
| `user-context-once` | `user-context` 首轮一次的判据从进程内存搬到 `localStorage`，`connector-status` 纳入同一判据（**实测该 bundle 里没有 localStorage，这条只是占位；真正生效的是下一条**） |
| `user-context-durable` | 判据改到主进程 + 磁盘：`composeUserPrompt` 读写 `<数据目录>\uc-seen\<sessionId>`，并把项目/Teams 分支的 `connector-status` 纳入同一判据 |

改 asar 必须同时满足 Electron 的两层完整性校验，否则应用启动即退出：

- **每文件哈希**：asar 头里每个 entry 的 `integrity.hash` / `blocks`，脚本按新内容重算；
- **整头哈希**：`WorkBuddy*.exe` 里嵌着 `{"file":"resources\\app.asar","alg":"SHA256","value":"…"}` 清单，脚本同步改掉。

内容 + 头哈希 + exe 清单是一个原子操作，任何一步失败三处一起回滚；`--restore` 断言 asar 与 exe 的
SHA256 都回到改前。**WorkBuddy 升级会同时覆盖 `app.asar` 和 exe，升级后重跑一次。**

```bat
python scripts\patch-asar.py --status  --asar "<安装目录>\resources\app.asar"   :: 只读体检
python scripts\patch-asar.py --dry-run --asar "<安装目录>\resources\app.asar"
scripts\deploy.cmd -Asar                :: 关掉应用后：提权 → 打补丁 → 重启
scripts\deploy.cmd -AsarRestore         :: 还原
scripts\deploy.cmd -SkipAsarPatch       :: 本次部署跳过 asar（默认会打）
```

退出码：`0` 已打好/无需再打，`2` 这个构建里找不到锚点（跳过，不写任何字节），`3` 需要提权，
`4` 应用正在运行（先完全退出，脚本不会替你结束进程）。`--asar` 省略时按 `WORKBUDDY_INSTALL` → `scripts\targets.json` 的 `installPath`
→ 卸载登记项（`DisplayIcon` / `InstallLocation` / `UninstallString`，且目录里必须真有 `WorkBuddy*.exe`）
的顺序找；都没有就报错，不扫盘、不猜目录。

#### 1.1 不杀进程，但打完会把它启动起来

写 asar 与 exe 清单时应用必须不在运行（Electron 把两者整份映射住，写入被系统拒绝）。
本工具链**不会**替你结束进程：检测到被占用就以退出码 `4` 拒绝，一个字节都不写，并提示你完全退出（含托盘）后重跑。

| 行为 | 说明 |
|---|---|
| 只检测，不动手 | 用「试写一次」判断 asar/exe 是否被锁；被锁就 `exit 4`，不写任何字节 |
| 部署会如实报失败 | `deploy.ps1` 把 asar 的 `exit 4` 记为 `FAIL`，整条命令 `exit 1`，不会"看起来成功" |
| 打完必收口 | 补丁写完立刻用 `--status` 复核：任一补丁不是 `patched`、`integ` 或 `exe sync` 不是 `MATCH`，同样记 `FAIL` 并 `exit 1` |
| 打完自动启动 | 写入成功后经 `explorer.exe` 拉起应用（不继承提权令牌），`-NoRelaunch` 或 `--no-launch` 可关；exe 不存在时跳过 |
| 需要管理员 | 写 `Program Files` 与 exe 清单要提权；`deploy.cmd` 无参数会自提权一次 |

### 2. `product.json` 工具描述覆盖

`overrides/tools/session-tools.compact.json` 存本次会话高频 18 个工具的精简描述，按 `prompts[].name`
精确覆盖，其余字段与条目原样保留：**33,422 → 16,727 字节（−50.0%）**。`deploy.ps1` 每次部署都会应用
（`-SkipProductOverrides` 跳过），写前备份到 `backups/<时间戳>-product-overrides/`。

```bat
python scripts\apply-product-overrides.py --dry-run --install "<安装目录>"
python scripts\apply-product-overrides.py --restore          :: 从最近一次备份还原
python scripts\export-tool-desc.py --set session             :: 导出原文，供改写
python scripts\tool-desc-map.py --session                    :: 工具 → prompts 键 → 字节
python scripts\measure-product.py --tools                    :: product.json 各段体积
python scripts\verify-tool-overrides.py                      :: 断言覆盖表条目齐全、变量无丢失
npm run overrides                                            :: 渲染覆盖表，断言无残留标签
```

### 3. 技能描述（`description`）精简

运行时给技能描述的总预算是 8,000 字符，内置技能属于 protected 不截断——31 个内置技能占满预算后，
非内置技能只会剩一个名字。`overrides/skills/desc-compact.json` 把 31 条 description 压到
**3,943 字符**，只保留「触发场景 + 一句能力 + 必要边界」，实现细节留在 `SKILL.md` 正文。

```bat
python scripts\apply-skill-desc.py --dry-run     :: 逐条打印前/后字节与字符
python scripts\apply-skill-desc.py               :: 改写 plugins\ 下的 SKILL.md（写前备份）
python scripts\apply-skill-desc.py --restore     :: 还原
```

它改的是**源文件**，改完要再跑一次 `deploy.cmd` 才能进缓存与安装镜像。

### 4. `runtime-templates/`：运行时直接读的 tpl

`<安装目录>\resources\templates\` 下的 tpl 由宿主在拼 system 时直接读取，不经过 `welcome-mode`。
本包提供 4 个，目标都是"注入块要么删掉、要么只留一次"：

| 文件 | 处理 | 渲染结果 |
|---|---|---|
| `user-context-identity.tpl` | 去掉 `<product_identity>` 与 "The user has provided the following custom instructions" 引导句，保留 `<user_custom_instructions>` 正文 | 561 → 91 字节 |
| `user-context-expert-identity.tpl` | 同上，无 custom 段 | 0 字节（不再注入） |
| `craft-mode-reminder.tpl` | 不再注入 `<craft_mode>` | 0 字节 |
| `ask-mode-reminder.tpl` | 不再注入 `<ask_mode>` | 0 字节 |

```bash
npm run runtime       # 渲染四个 tpl 并断言注入块已按预期去掉（退出码 1 表示断言不成立）
```

部署时写入安装目录（`resources\templates\`），需要 `-Elevate`。

### 5. 诊断工具

改完不生效时用它们定位——真实请求只在 `<数据目录>\traces\` 里（只记首轮，第二轮以后的现象只能当场问模型）。
除 `dump-templates.py`（落盘到 `overrides/_dump/`）与 `slim-mcp-descriptions.py`（写前备份）外，其余不改任何东西：

| 脚本 | 用途 |
|---|---|
| `dump-system-prompt.py` | 把最近一次真实请求的 system 落盘，并打印各块的**出现顺序**索引 |
| `dump-messages.py` | 打印消息序列，逐轮标出四段 user-context（`ICPS` 首轮、`----` 之后）是否出现 |
| `trace-tokens.py` | 最近几个会话的首轮 / 末轮 `prompt_tokens`，验证注入体积是否真的下降 |
| `measure-prompt-blocks.py` | 单个 trace 里各注入块的字节与标记出现次数 |
| `measure-injected.py` | 模拟运行时截断规则，算清单**实际注入**字节（技能预算 8,000） |
| `measure-injection.py` | 技能 / 子代理 / 延迟工具三张清单的注入体积 |
| `list-desc.py` | 列出 `skills` / `agents` 的 frontmatter name+description 与体积 |
| `dump-templates.py` | 把 JSON 里的长 template 逐条拆行落盘，便于逐行读 |
| `asar-grep.py` | 在 asar 里搜字符串并打印上下文（asar 是拼接包，明文可搜） |
| `src-context.py` | 在任意文本文件里按锚点取上下文窗口 |
| `cache-compare.js` | 两份 `cache-prefix.js --json` 的逐项差异，用于核对"只改位置不改内容" |
| `slim-mcp-descriptions.py` | 精简 MCP 插件里被重复拼接的工具描述常量 |
| `test-user-context-once.js` | 断言 `user-context-once` 补丁的判据仍在（改完提示词必跑） |
| `test-asar-launch.py` | 断言「打完启动应用」链路：经 explorer 真拉起一个探针、exe 缺失/`--no-launch` 时跳过、源码里没有任何结束进程的调用（8 项） |
| `test-launch-coverage.ps1` | 断言「脚本结束即拉起」的覆盖面：五个写盘动作都在名单、只读动作不在、`-DryRun` 不启动、拉起发生在失败退出之前、两处 asar 都传 `--no-launch`；并用 AST 取出真实的 `Start-WorkBuddyIfClosed` 跑四种场景（含"第一个在跑、第二个没跑"必须启动第二个）（20 项） |
| `refresh-cli-prewarm.py` | 只停空闲的 `kind=prewarm` / `status=idle` CLI 助手，让下个聊天起新进程；不碰主程序与当前聊天 |
| `trace-turns.py` | 按轮读 trace 的请求体，判断某块是首轮一次还是每轮都有（附实测：trace 只落每个会话的首个请求） |
| `uc-seen.py` | `user-context-durable` 的标记管理：`--count` / `--seed` / `--clear <sid>` / `--prune 30` |

## 技能

`plugins/` 下的 31 个技能按"触发条件 + 操作流程"组织，包含定时任务、邮件、设计稿验收、团队编排、产品文档查询等工作流。每个技能只在自己的任务出现时加载，不进入每轮常驻上下文。

部署脚本会把 `plugins/` 下的技能写到运行时的技能目录并完成登记；只改一个技能时用 `scripts\deploy.cmd -Skill <name>`。不要把内部路径、凭据、个人配置写进提示词。

> 技能能不能被模型自动调用，取决于它是否出现在运行时的技能清单里——而清单只扫描安装目录与用户技能目录。只写插件缓存 = 提示词里提到技能名、模型却找不到，表现上就是"改了不生效"。

## 不包含

运行时本体、工具实现、业务代码、备份（`backups/`）、归档（`archive/`）、渲染产物（`dist/`）、
开发日志与本机个人配置。`scripts\targets.json` 出厂为空，脚本里没有任何写死的安装目录。

运行层改造（`app.asar` 补丁、`product.json` 覆盖、`runtime-templates/`）**在包内**，见上文
[运行层改造](#运行层改造asar--productjson--runtime-templates)——它们只需要安装目录，不需要额外的机器特定输入。

## 使用

运行 `scripts\deploy.cmd` 即可：脚本自动检测所有安装并写入各自的插件缓存（加 `-Elevate` 同时写安装镜像与
`resources\templates\`），随后应用 `product.json` 覆盖、打 `app.asar` 补丁（应用在运行会自动跳过），
不需要手动放置文件、不需要为每个安装各跑一次。只想渲染/校验时用 `npm run render` / `npm run check` /
`npm run cache` / `npm run runtime` / `npm run overrides`。

## License

MIT. See [LICENSE](LICENSE).
