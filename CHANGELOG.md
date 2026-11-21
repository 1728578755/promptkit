# Changelog

本文件记录 PromptKit 公开版的内容变化。提示词是会被逐字节比较的资产，所以每个版本都写明"改了什么位置"，而不只写"优化了提示词"。

## v1.3.18 — 2026-11-21

主题：**新环境（全新电脑/新安装）部署失败的九项根因修复（A–I）**。
来源：`DIAGNOSIS-NEW-ENVIRONMENT.md` 深度诊断，逐条对应修复；全部验证走副本与伪造
`USERPROFILE` 的仿真环境，未触碰任何真实安装。

### A. `scripts/target.ps1` —— 与 `_install.py` 探测裂脑（新环境致命）
原来只认注册表 / App Paths / 快捷方式，绿色版、解压版、便携版安装直接报
"No installation found"。现在与诊断层 `install_roots_deep()` 对齐：
- 新增 `Test-LooksLikeWorkBuddy`（目录名 / `WorkBuddy*.exe` / `product.json` 内容判据，
  与 `_install._looks_like_workbuddy()` 同一套规则）与 `Test-ClientDataDir`；
- 兜底扫描扩到盘根（C–K）、`Program Files` / `(x86)` / Apps / Programs / Software、
  每个用户的 `AppData\Local\Programs`、`WORKBUDDY_INSTALL` 显式线索；
- 结果合并去重：父目录已是候选的子目录（`resources\`、`share-target\` 里也有
  `WorkBuddy*.exe`）被剪掉——这个假 target 是本轮新发现并修掉的；
- 反查不到数据目录时，仅在本机只有一套安装且恰有一个像数据目录的 `.workbuddy*`
  时才配对（`Get-LooseConfigDir`），避免多安装互相污染。
另修：`.skill-list-cache.json` 的 `scopeKey` 实际是"JSON 数组的字符串"，原配对代码
一直解析失败；已修，便携安装现在能正确配到数据目录。

### B. `scripts/doctor.py` —— 多安装一票否决误报
一台机器上闲置的那套安装没打补丁时，全局结论被打成 NOT EFFECTIVE、退出码 1，
即使当前使用的安装全部 PASS。现在结论由纯函数 `decide()` 计算：
- "当前使用"只认"有进程正在从它运行"（不再回退到 trace 所属配置——trace 可能来自
  旧会话）；
- 未在使用的安装问题降级为 WARN，不再拖垮全局结论；
- 10 条单测（`_selftest/test-doctor-decide.py`，`python -I` 运行）覆盖便携/注册表缺失
  等场景，不是碰巧通过。

### C. `scripts/doctor-basic.ps1` —— 无 Python 兜底诊断的两个盲区
- 只看 `.workbuddy`，不认 `.workbuddy-ai` 等变体目录 → 现在逐个探测变体；
- 完全不检查模板缓存层 → 现在检查 `welcomemode-*/prompt.tpl` 是否存在、8 个废弃
  策略块（tool_usage_policy / result_presentation / final_answer_instructions /
  task_management / agent_skills / quality_policy / agent_loop / regional_conventions）
  是否已剔除（removed_hits）。

### D. `scripts/_install.py` —— 反斜杠转义 SyntaxWarning
PowerShell 命令字符串里 `\M` `\D` `\.` 等未用 raw string，Python 3.12+ 触发
SyntaxWarning，未来版本会变 SyntaxError 直接崩。已全部改为 raw string /
双反斜杠；`python -W error::SyntaxWarning -c "import _install"` 零警告。

### E. 冷启动缓存缺失的"静默假成功"（新环境致命）
新电脑还没启动过客户端时，插件缓存目录为空，部署脚本打印 `Summary: OK` 但实际
写入 0 个文件。现在写入数为 0 必须非零退出失败，并明确提示"请先启动客户端一次
再部署"；严禁静默 OK。

### F. 无 Python / Node 时 deploy.ps1 不再裸崩
解释器按顺序查找：PATH 里的 python → py launcher → WorkBuddy 自带
`binaries\python\versions`；三处都没有时打印清晰缺失清单并指向免 Python/Node 的
`wb-fix-lite.cmd`，退出码非零，不静默跳过。

### G. asar 被运行中客户端锁定时的可诊断性
`patch-asar.py` / `apply.ps1` 在写 asar 前检测 `WorkBuddy*` / `codebuddy` 运行进程，
直接打印进程名与 PID（原来只抛 `[WinError 32]`）；asar 写完后调用
`refresh-cli-prewarm.py --apply` 刷新空闲预热进程，避免新聊天复用旧 chunk。

### H. `scripts/patch-cli-trim.py` —— 脆弱硬编码改为动态发现
- 模块导出 ID 不再写死 `1849`：从已安装 chunk 文件名/内容动态发现；
- `systemInstructions` 注入点定位改用对混淆名不敏感的结构化正则（捕获实际标识符
  再引用，不再假设 `g` / `w`），并加对称性校验；
- 任何锚点找不到 → 硬失败，打印锚点上下文字节，严禁"找不到就跳过"。
仿真验证：锚点被改坏 → 非零退出并指名缺失锚点；锚点正常 → PATCHED；两种情况
都"未写入任何字节"才算通过。

### I. 旧会话历史自污染的显著提示
doctor.py 结论区现在显式输出："验证必须新开空白会话——旧会话历史里本来就存着
旧块"，避免把历史残留误判为补丁失效。

### 验收（全部真实运行）
- `python -X utf8 scripts/doctor.py` exit 0，双安装 PASS（不比修复前差）；
- `python -W error::SyntaxWarning -c "import _install"` 零警告；
- `powershell -File scripts/doctor-basic.ps1` 输出 `.workbuddy-ai` 与模板层检查；
- `_selftest/run-newenv-tests.ps1` 17/17 PASS：冷启动 0 写入→FAIL+提示；无 Python→
  缺失清单+指向 wb-fix-lite；好锚点 PATCHED / 坏锚点 FAIL+上下文字节；
- `node scripts/release-audit.js` RELEASE OK。

## v1.3.17 — 2026-11-21

> 本版只修一处：`doctor.py` 里那条提示字符串不是 raw string，Python 3.12+ 首次运行会打印
> `SyntaxWarning: invalid escape sequence '\w'`，看起来像报错。已改为 raw string，
> `python -W error::SyntaxWarning -m py_compile` 通过。

> **补丁（同版本第二次发布）**：上一版 `wb-fix-lite.cmd` 自己就跑不起来。
>
> 1. `echo ... (needed ...)` 的括号写在 `if (...)` 块里 → 整个批处理解析失败，
>    只输出 `... was unexpected at this time.` 就退出。
> 2. 提权实例丢掉了 `-DryRun` / `-Restore` 参数，且执行完窗口立刻关闭；
>    于是"只想看看"的调用会真的去写文件，而且看不到任何输出。
>
> 现在的行为：
> - `-DryRun` 不触发 UAC，直接在当前窗口跑完并 `pause`；
> - 提权实例用 `-NoExit` 启动，**窗口不会关**，参数通过环境变量原样转发；
> - 不再用 `net session` 判断管理员（Server 服务停用会误判），改用 `whoami /groups`
>   里的高完整性 SID `S-1-16-12288`；
> - `wb-fix-lite.ps1` 的 apply / restore 两条写入路径都加了 try/catch，
>   没权限时只打印"写入失败 + 用 wb-fix-lite.cmd 提权"，不再抛一屏异常。
>
> 实测：
> ```
> WB_NO_PAUSE=1 cmd /c "scripts\wb-fix-lite.cmd -DryRun"
>  -> == wb-fix-lite ... ==  install: C:\...  已有轻量补丁（无需重复）  exit=0
> powershell -File scripts/wb-fix-lite.ps1 -Install "C:\Program Files\WorkBuddy" -Restore
>  -> 写入失败：…访问被拒绝。 / 需要管理员权限：请用 scripts\wb-fix-lite.cmd（会自动提权）。
> ```

主题：**一条不依赖 Python / Node / 安装路径 / 代码锚点的修法。**

### 为什么还要再加一层

前面每一层都依赖某种"固定东西"：Python、Node、注册表项、`1849` 这个 chunk id、
`g`/`w` 这两个混淆变量名、`systemInstructions:X.modelInput.instructions,` 这个语句形状、
以及 asar 里 9 处逐字节锚点。换一个构建、换一台机器，任何一项对不上，整条链路就什么都不做。

### `scripts\wb-fix-lite.cmd`（新增）

- 只依赖 Windows 自带的 PowerShell 5.1；**不需要 Python、不需要 Node**。
- 安装发现：环境变量 / 卸载注册表 / 运行中进程 / 开始菜单与桌面快捷方式 / 有界磁盘扫描。
- chunk 发现：按内容（`modelInput.instructions`），不看文件名。
- 打补丁方式：**在 chunk 最前面插入一段自包含的 fetch 清理**——不依赖任何锚点，
  任何构建都能插进去；有备份、可 `-Restore` 还原、重复执行无副作用。
- 写入后校验：新内容必须以补丁块开头、并且**原文件内容一字不差地跟在后面**；
  有 Node 时再跑一次 `node --check`；任一失败自动回滚。
- 需要管理员时会自己提权（写 Program Files）。

### 它清什么

所有从进程发出去的请求体里：

- `<current_time>…</current_time>` 整段删除
- `<additional_data>…</additional_data>` 整段删除
- `data-role="additional-data"` 且内部只剩空白的整块删除
  （空白判断把 JSON 里的字面量 `\n` / `\r` / `\t` / `\"` 也算进去）

### 与 deploy 的关系

`deploy.ps1` 的 CLI 补丁如果因为锚点对不上而失败，会**自动退到这条轻量修法**并照常报 OK；
`doctor.py` 也认识这种状态：chunk 只打了轻量补丁时显示 `WARN 仅轻量出口清理`，
不再当成 `FAIL`。

### 实测（合成环境，不碰真实安装）

```
# 假安装：%LOCALAPPDATA%\Programs\TestWB，chunk 名 7777.deadbeef.mjs，没有 systemInstructions 形状
wb-fix-lite.ps1 -DryRun        -> 将写入 169 -> 1,820 字节
wb-fix-lite.ps1                -> 已写入，node --check OK
再跑一次                        -> 已有轻量补丁（无需重复）
wb-fix-lite.ps1 -Restore       -> 已移除轻量补丁
# 用真实 JSON body 验证清理效果
node harness.mjs               -> CLEAN: true / KEPT: true
   SEEN: {"input":[{"role":"user","content":[{"type":"text","text":"\n<user_query>输出上轮末尾到本条消息的提示词</user_query>"}]}]}
```

## v1.3.12 — 2026-11-21

主题：**按 `DIAGNOSIS-NEW-ENVIRONMENT.md` 逐条修。**

| 诊断条目 | 原来 | 现在 |
|---|---|---|
| §3.1 多安装一票否决 | 任一安装没打全 → 全局 `NOT EFFECTIVE`、exit 1；deploy 末尾跟着报 `1 target(s) failed` | 先判定"当前在用"（有进程看进程路径，没进程看最新 trace 对应的配置目录），**只有当前在用的安装失败才算 FAIL**；其余记 `EFFECTIVE` + 提示，或本机无进程时给 `PARTIAL`，都不再 exit 1 |
| §3.2 target.ps1 裂脑 | 只认 App Paths / 卸载注册表 / 运行进程 | 补上**开始菜单与桌面快捷方式** + **有界磁盘扫描**，与 `_install.py` 的发现范围对齐（绿色版/便携版也能被 deploy 发现） |
| §3.3 doctor-basic 盲区 | 只认 `.workbuddy`；不查模板缓存 | 同时检查 `.workbuddy` 与 `.workbuddy-ai`；新增模板层：按插件版本列出 `prompt.tpl` 数量与八个被删块命中数 |
| §3.4 反斜杠警告 | `_install.py` 里拼接 PowerShell 字符串触发 `SyntaxWarning` | 改用 `.NET` 已知文件夹取值，彻底不拼引号/反斜杠；`-W error::SyntaxWarning` 验证 |
| §4.1 缓存冷启动静默 0 | 缓存目录为空时 `apply.ps1` 返回 0 个目标，deploy 仍打印 OK | deploy 收口读真实缓存：`files=0` → **FAIL** 并提示"先启动一次客户端、进任意模式、完全退出（含托盘）再重跑"；`removed_hits>0` 也 FAIL；正常则打印 `cache templates trimmed: N files` |
| §4.2 Python 依赖 | 没有系统 Python 就直接跳过所有补丁 | 优先用安装自带的 `resources\vendor\python\python.exe`（实测 3.13.14），已内置到 CLI/asar/apply 三条路径 |
| §4.4 Chunk ID / 变量硬编码 | `ANCHOR = export const __rspack_esm_ids=["1849"];` + 写死 `g` / `w` | 锚点只认语句前缀 `export const __rspack_esm_ids=[`；变量名从 `systemInstructions:X.modelInput.instructions,` 与 `input:X.modelInput.input,` 现场发现。已用改名 chunk（`9999.aaa.mjs`）和改名变量（`Q`/`R`）实测通过 |
| 判定口径 | `is_patched` 数"打了几处"，换个构建就误判 | 改为看"还有没有没包上的调用点"（正则残留为 0） |

### 验证

```
python scripts/patch-cli-trim.py --self-test                -> SELF TEST OK
python scripts/doctor.py                                    -> 结论 EFFECTIVE（另有 1 套未打全、不影响使用），exit 0
powershell -File scripts/doctor-basic.ps1                   -> 两个配置目录的模板 removed_hits=0
powershell -File scripts/test-verify-hook.ps1               -> VERIFY HOOK OK（含冷启动 0 文件断言）
powershell -File scripts/deploy.ps1 -DryRun                 -> exit 0
改名 chunk 9999.aaa.mjs + 变量 Q/R                           -> 253,385 字节、PATCHED、node --check OK
```

## v1.3.11 — 2026-11-21

主题：**把写死的机器路径全部拆掉；没有 Python 也能诊断。**

### 写死在哪（用户指出的问题）

| 文件 | 原来 | 现在 |
|---|---|---|
| `patch-cli-trim.py` | `DEFAULT_GLOB = r"C:\Program Files\WorkBuddy\...\cli\dist"` | 默认空 = 自动发现；`--file` 仍可显式指定 |
| `doctor.cmd` | 无 Python 时只列 `C:\Program Files\WorkBuddy`、`D:\Program Files\WorkBuddyAI` | 转调 `doctor-basic.ps1`，由它自己发现 |
| `doctor.py` | 候选目录只来自固定的 Program Files 变量 | 环境变量 / 注册表 / 运行进程 / 开始菜单快捷方式 / 有界磁盘扫描 |

### 发现顺序（都不写死路径）

1. `WORKBUDDY_INSTALL`
2. 卸载注册表（`DisplayIcon` / `InstallLocation` / `UninstallString`）
3. **正在运行的 `WorkBuddy*.exe` 的 `ExecutablePath`**
4. 开始菜单 / 桌面的 `.lnk` 目标
5. 有界磁盘扫描：每盘 `<盘>:\Program Files`、`Program Files (x86)`、`Apps`、`Programs`、`Software`、`Users\*\AppData\Local\Programs`，深度 2、只看名字像 WorkBuddy 的目录
6. 结果再用「文件夹名 / WorkBuddy*.exe / `product.json` 的 productName」过滤，避免把别的 Electron 应用（ZCode、Qwen、5EClient…）当成 WorkBuddy

### 没有 Python 也能诊断

新增 `doctor-basic.ps1`（纯 PowerShell，Windows 自带）：打印每套安装的 asar
`current-time-cloud` 状态、chunk 文件名/大小/修改时间、以及
`helper / scrub / blank / instructions / input` 五个标记的实际值。大 asar 用 4MB 分块
扫描，不会把 170MB 整个读进内存。

### 顺带修掉一个误报

trace 里的 `<user_info>` / `user-context` / `connector-status` 是**首轮一次性上下文**，
原来按"必须 0"判定，导致真实请求明明是正常的也报 FAIL。现在按 `messages<=2` 视作首轮，
这些允许 1 次；`<current_time>` 与 `data-role="additional-data"` 仍然必须 0。

### 本机实测（真实请求，不是模拟）

```
最新请求 18:22:13  messages=2
<current_time                 count=0 (limit 0)
data-role="additional-data"   count=0 (limit 0)
data-role="user-context"      count=0 (limit 1，首轮)
<user_info                    count=1 (limit 1，首轮)
八个被删块                     全 0
```

搬移测试：把一份 chunk 放到 `%LOCALAPPDATA%\Programs\VendorX\WorkBuddy`（既不在
Program Files、也没有注册表项），发现逻辑能命中；同时 `5EClient`、`166game`、`ZCode`、
`Qwen`、`Codex` 等同类 Electron 应用被正确排除。

## v1.3.10 — 2026-11-21

主题：**`doctor.cmd` 双击后窗口不再秒关；同时修掉一个真正让补丁失效的 bug。**

### 1. "运行结果页没有"

- 旧 `doctor.cmd` 没有 `pause`：双击 → 跑完 → 窗口立刻消失，看起来"什么都没发生"。
  现在结束时一定停住，并打印报告路径与退出码；脚本调用可设 `WB_NO_PAUSE=1`。
- 旧脚本只找 PATH 里的 `python`。WorkBuddy 自带的
  `%USERPROFILE%\.workbuddy\binaries\python\versions\*\python.exe` 现在会被优先使用；
  都没有时打印原始信息（安装目录 + chunk 文件名）而不是静默退出。
- `doctor.cmd` 改为纯 ASCII（中文只由 Python 输出），避免 cmd 代码页把批处理本身搞坏。
- 结论移到报告**第一屏**，不用滚到底。

### 2. 真正让块留在请求里的 bug（关键）

出口清理（fetch）拿到的 body 是 **JSON 文本**，其中换行是字面量 `\n`（反斜杠+n），
`inner.trim()` 不会把它当空白 —— 于是"`additional-data` 只剩空白就删掉"这条规则
在 JSON 层**从来没生效过**：

```text
{"text":"<system-reminder data-role=\"additional-data\">\n\n</system-reminder>\n<user_query>…"}
```

现在改成结构扫描 + 显式把 `\n` / `\r` / `\t` / `\"` 当空白判断（`__wbBlank`），
并新增 `--self-test` 断言：把原样块塞进 `fetch` 的 JSON body，清理后必须
`CLEAN: true` 且 `user_query` 仍在；干净 body 必须原样透传。

### 3. 已打过补丁的 chunk 现在能升级

之前 `--apply` 见到 `MARK` 就 `already`，**help 函数内部的修复永远进不去**。
现在会比对 helper 文本（缺 `__wbBlank` / `__wbDropReminder` 即视为旧版），
整段替换为仓库当前版本，再补 fetch 出口清理。

### 4. 证据

- 副本升级：251,478 → 253,385 字节，`helper-upgrade` + `fetch-scrub` = `patched`，
  `node --check OK`，`--status` = `PATCHED`。
- 用升级后的 chunk 跑真实 JSON body：
  `CLEAN: true`、`QUERY KEPT: true`，输出只剩
  `{"input":[{"role":"user","content":[{"type":"text","text":"\n<user_query>…</user_query>"}]}]}`。
- `doctor.py` 的 `[2]` 增加 `anchor fetch-scrub`；报告首行即结论。

## v1.3.9 — 2026-11-21

主题：**一条 `doctor` 命令把每一项都打出来——包括"应该存在却没有"的项，并直接说为什么没生效。**

### 上一版的问题

v1.3.8 的验收只打印"找到并打好的东西"。某个补丁没打上、某个块还在发给模型时，
报告里根本不出现这一行，于是只能说"还是有问题"，无法定位。

### 改了什么

- 新增 `scripts/doctor.py` + `scripts/doctor.cmd`（**只读，不需要管理员**，随时可跑）：
  1. 提示词模板：每个缓存插件版本各一行，逐个数八个被删的块；
  2. CLI 运行时：按内容找 chunk，打印 `bytes / version / mtime` 与三个锚点的实际命中数；
  3. `app.asar`：9 个补丁逐条 `patched / raw`，外加 integrity / exe 清单；
  4. 进程：每个 `WorkBuddy.exe` / `codebuddy` 的启动时间与补丁时间比较，
     早于补丁的直接标 `WARN → 仍加载旧 chunk`；
  5. prewarm 池：idle 数量，>0 说明新聊天会复用旧代码；
  6. 最新 trace：逐块计数（`<current_time`、`additional-data`、`user-context`、八个块……）；
  7. **已安装运行时自检**：把磁盘上那份 chunk 里的 helper 抽出来，拿用户原样
     182 字符块跑一遍，输出 `INSTALLED HELPER OK` / 残留内容；
  8. 判读规则：每条异常该往哪查，以及"全 PASS 但模型仍打印该块"时的两种真实来路。
- 结论固定为三选一并带原因：`EFFECTIVE` / `UNPROVEN`（还没有补丁后的新请求）/
  `NOT EFFECTIVE: <哪一层、什么现象>`；报告写 `reports/doctor-latest.txt`。
- `verify-stack.py` 变为兼容入口，直接转调 `doctor.py`（实现只有一份）。
- `deploy.cmd` 结束时的验收改为调用 doctor，并修了中文结论在 PowerShell 5.1 里
  变成乱码的问题（调用前把 `[Console]::OutputEncoding` 切到 UTF-8）。
- `UNPROVEN` 不再算失败（它只是"还没有新请求可以证明"）；只有 `NOT EFFECTIVE` 才非 0。
- `test-verify-hook.ps1` 增加断言：报告必须含 `EFFECTIVE/UNPROVEN`、`version=3`、
  asar 补丁列表、`[7]` 段与 `INSTALLED HELPER OK`，且不得是乱码。

### 在另一台电脑上（只读诊断，先跑这个）

```bat
scripts\doctor.cmd
```

把最后 15 行（含 `结论` / `原因`）发回来即可定位，不需要重启、不需要管理员。

## v1.3.8 — 2026-11-21

主题：**在“另一台电脑”上真正打上补丁 —— 不再按文件名找 chunk，部署结束自动逐层验收。**

### 根因

- 上一版仍按 `1849.*.mjs` 找 CLI chunk。WorkBuddy 升级会改 id/哈希，别的电脑上
  文件名不同时，`deploy` 只打一行 `cli runtime chunk not found` 就 **SKIP**，
  退出码仍是 0 —— 看起来部署成功，实际模型边界一个补丁都没打，块当然还在。
- 已经起着的 prewarm 助手握着旧 chunk，新聊天命中它时同样看不到补丁。

### 改了什么

- `patch-cli-trim.py`：目录发现改为**递归 + 按内容**
  （`modelInput.instructions` + `modelInput.input`），不再依赖任何文件名；
  目录默认值改为 `…\cli\dist`。
- `deploy.ps1`：`Invoke-CliPatchTarget` 把 `cli\dist` 整个目录交给 Python，
  找不到就是 **FAIL**，不再静默 SKIP；打完自动跑
  `refresh-cli-prewarm.py --apply`（只停 `kind=prewarm`+`status=idle`，按 PID，不动主程序和当前聊天）。
- 新增 `scripts/verify-stack.py`：一条命令逐层验收
  （CLI `version`/`status`、asar 9 个补丁逐个、integrity/exe 清单、最新 trace 的
  `current_time`/`additional-data`/`user-context` 计数），输出 `RESULT: PASS|FAIL`，
  同时写 `reports/verify-latest.txt`。`deploy.cmd` 结束时自动调用。
- `--self-test` 增加**用户原样 182 字符消息**断言：
  `<system-reminder data-role="additional-data">` + `<current_time>` + `<user_query>` 清理后
  必须只剩 `<user_query>…</user_query>`。

### 在另一台电脑上怎么用（一次）

```bat
:: 1. 完全退出 WorkBuddy（asar 层需要；CLI 层不需要）
:: 2. 解压 promptkit-v1.3.8.zip，右键管理员运行
scripts\deploy.cmd
```

结束时屏幕最后会有一段验收：

```text
== WorkBuddy prompt-trim acceptance ==
self-test : SELF TEST OK
install   : C:\Program Files\WorkBuddy
  cli     : version=3 status=PATCHED
  asar    : integ=MATCH exe=MATCH
            current-time-cloud   ok
trace note: clean
RESULT    : PASS
```

`RESULT: PASS` 才是成功；`FAIL` 会直接写明哪一层、为什么（应用在运行 / 找不到 chunk / 锚点变了）。

## v1.3.7 — 2026-11-21

主题：**`current_time` 的 `data-role="additional-data"` 空壳彻底清掉；别的电脑/别的版本也能打。**

### 根因

- 用户仍看到：

  ```text
  <system-reminder data-role="additional-data">
  <current_time>Saturday, November 21, 2026 at 17:17:54 GMT+8</current_time>
  </system-reminder>
  ```

- 该块由云端 `resolveMessage` 生成：`/main/server.js` 的
  `t.currentTime&&(r.current_time=t.currentTime)` 把时间发给云端；
  旧 `current-time` 补丁只改了 `AdditionalDataSection.render`，没改这条上行字段。
- CLI v2 只删 `<current_time>` 标签，留下空的 `data-role="additional-data"` 壳。

### 改了什么

- `patch-cli-trim.py` **v3**：删掉 `<current_time>` 后，若 `data-role="additional-data"`
  块只剩空白，把整个空壳也删除；`--self-test` 新增该断言。
- chunk 查找从硬编码 `1849.*.mjs` 改为**按内容回退**（找同时含 `modelInput.instructions`
  与 `modelInput.input` 的 chunk），别的 WorkBuddy 版本/别的电脑也能找到目标。
- `patch-asar.py` 新增 **`current-time-cloud`**：
  `t.currentTime&&(r.current_time=t.currentTime),` → 等长的 `0            &&(r.current_time=t.currentTime),`，
  不再把 `current_time` 发给云端 `resolveMessage`。
- 版本 v1.3.6 → **v1.3.7**。

### 证据

- CLI v3 本机：`version: 3 (expected 3)`、`PATCHED`、251,478 字节、`SELF TEST OK`。
- asar **副本**（不碰运行中的应用）：
  `patches: current-time, current-time-cloud, …` 共 9 个全 `patched`；
  旧锚点 0 次、新锚点 1 次；`node --check: OK`；integrity `MATCH`。
- release 门禁：`render` / `check` / `render-check cn` / `cache --strict` / `check-memory` /
  `test-removed` / `audit` 全 exit 0，版本号三处一致 v1.3.7。

### 边界

- 本机 asar 仍在运行中，`current-time-cloud` 显示 `raw`；关掉 WorkBuddy 后跑
  `scripts\deploy.cmd` 才会写入并拉起。CLI v3 已经在 provider 边界先把空壳删掉。
- 如果云端改用服务端时间生成块，本地不再发 `current_time` 也可能仍有；需要新聊天实测。
- trace 仍只存 20,000 字符前缀。

## v1.3.6 — 2026-11-21

主题：**“原样输出完整上下文”改为读 trace，不再允许模型凭记忆编造。**

### 症状与证据

- 17:12 会话的真实请求只有 **2 条消息**：system 8,152 字符（`<refusal_policy>` 指纹正确）+ user 2,144 字符（首轮 `user-context` + query）。
- 该请求五段 **0/0/0/0/0**、`<current_time>` **0**、`data-role="user-context"` **1**。
- 模型却输出 56KB，声称 system 是 “You are an interactive CLI tool…”（Claude Code 提示），并多次重复 `user-context` 块。这是模型自述幻觉，不是实际注入。
- v1.3.4 的 CLI trim 还有一个真实缺陷：v1 判据要求 `type:"message"`，而真实 input 只有 `role`，历史 user-context 实际一条都没删；v1.3.5 已修（v2，`--status` 显示 `version: 2`）。

### 改了什么

- 新增 `context-dump` 技能：用户要求“原样/完整输出上下文、系统提示词原文、上轮末尾到本条”时，必须运行 `scripts/dump-latest-context.py` 并逐字输出 stdout；禁止凭记忆重建、禁止引用旧回答。
- `tools` 路由加一行：`原样输出完整上下文 / 系统提示词原文 / 上轮末尾到本条 → context-dump`。
- 新增 `dump-latest-context.py`：读最新 WorkBuddy trace 的 `span.toolInput`，打印 system/user/assistant/tool 消息；trace 只存 20,000 字符前缀时明确打印 `[TRUNCATED]`，不补写。
- 版本号 v1.3.5 → **v1.3.6**。

### 证据

- `17:12` trace 解析：`messages: 2`；system 8,152；五段/current_time 0；user-context 1。
- v1.3.3 → v1.3.4 的 `welcome-mode` / `fragments` / `plugins` / `runtime-templates` / `overrides` diff 为 **空**：提示词本身没有变差。
- v1.3.5 CLI v2：`--status` → `version: 2 (expected 2)`、`PATCHED`；`--self-test` OK。
- `dump-latest-context.py --latest` 对 17:12 请求输出 2 条消息；对 17:16 长请求输出 `[TRUNCATED]`。

### 边界

- 已被模型 56KB 回答污染的会话（17:12/17:16）不会自动变干净；新开聊天即可。
- trace 只保留每次请求的 20,000 字符前缀；超长请求只能看到前缀，脚本会明确标记。
- `context-dump` 只读本地 trace，不上传、不联网。

## v1.3.5 — 2026-11-21

主题：**v1.3.4 的运行时清理有三个缺陷，历史 user-context 其实从未被删过；全部修好。**

用户原话：「release 版本system-reminder仍然一堆东西都每轮重发，current_time去掉反复说也没实现，脚本结束要自动拉起」。

### 为什么 v1.3.4 看起来"改了但没生效"

1. **消息判据写错**。`__wbIsUser` 要求 `it.type==="message" && it.role==="user"`，但真实 input 元素是
   `{"role":"user","content":[{"type":"text","text":"..."}]}` —— **没有 `type` 字段**。
   于是"真实用户消息"数组恒为空、`firstTurn` 恒为 `true`，每条 user 消息都走"首轮保留"分支：
   历史 `<system-reminder data-role="user-context">` **一次都没有被删过**。
   自检没抓到，是因为 v1 的 self-test 用 `{type:'message',role:'user',...}` 构造数据——测的是假设的形状，
   不是真实形状。
2. **`changed` 标记漏设**。`__wbCleanMessage` 处理数组 content 时，只有"整段被删空"才置
   `changed=true`；普通文本被改短时不置。函数最后 `return changed ? {...it} : it` 于是把清理结果整个丢掉。
   **即使判据改对，这条也会让清理无效**——两个 bug 必须一起修。
3. **AI 版从来没打过**。`deploy.ps1` 调 `patch-cli-trim.py --apply` 时没传 `--file`，
   脚本内置默认路径是 CN 的 `C:\Program Files\WorkBuddy\...`；`D:\Program Files\WorkBuddyAI` 下那份
   `1849.d105c1cb.mjs`（247,364 B）一直是原始文件。

### 改了什么

- `scripts/patch-cli-trim.py` 升到 **v2**：
  - `__wbIsUser` 只看 `role==="user"`，不再要求 `type`；
  - 数组 content 的文本一被改动就置 `changed=true`；
  - 纯提醒消息（不含 `<user_query>`）在**任何位置**都丢弃；
  - assistant / tool 的历史**不再触碰**——模型自己讨论这些标签时，正文不能被当成注入物删掉。
- **版本标记**：helper 用 `/*__wbTrimV2*/ … /*__wbTrimEnd*/` 包起来，`--status` 输出 `version:`；
  检测到 v1 时**先从最早备份还原原始 chunk 再打**，不叠加、不猜测。
- `scripts/deploy.ps1`：`Invoke-CliPatchTarget` 与 `--status` 都传 `--file <该安装>\resources\
  app.asar.unpacked\cli\dist\lazy-lite-wb\1849.*.mjs`，两套安装各打自己的；
  找不到 chunk 的构建记 `SKIP`（不判失败），避免老版本直接红。
- **自检重写**：用真实形状（`{role:"user",content:[{type:"text",text}]}`）构造，覆盖
  历史删除 / 首轮保留 / assistant 不被改写 / reminder-only 丢弃 / system 里的 `current_time`。

### 证据

重新注入缺陷，自检立刻红（先证红）：

| 回退哪一处 | 结果 |
|---|---|
| `__wbIsUser` 改回要求 `type==="message"` | `Error: historical user-context kept`，exit 1 |
| `changed` 标记改回只在删空时置 | Node 抛错，exit 1 |
| 用真实结构跑 v1 helper | `contains_user_context=true`，exit 1 |

修复后：

- `python scripts/patch-cli-trim.py --self-test` → `SELF TEST OK`
- 最小用例：`"<tool_usage_policy>A</tool_usage_policy>x<system-reminder data-role=\"user-context\">q</system-reminder>y"` → `"xy"`
- 两个安装的 chunk：`version: 2`、`status: PATCHED`、sha256 同为 `f9f8b0945c5ea25135a38242f321c03f6be586ac6269c37054c603129df80033`
- CN 250,670 → 251,167 字节；AI 247,364 → 251,167 字节（同一份实现）

### 生效条件（重要）

chunk 是 Node 进程启动时读进内存的。**已经打开的聊天不会热加载**——需要新开一个聊天，或完全退出后重开应用。
脚本按用户要求**从不结束进程**，所以这一步只能手动。

## v1.3.4 — 2026-11-21

主题：**旧会话不再重放 user-context / 五段；部署脚本结束时自动拉起。**

用户原话：「system-reminder 仍然一堆东西都每轮重发，current_time 去掉反复说也没实现，脚本结束要自动拉起」，随后确认「是 WorkBuddy 不是 ZCode」。

### 根因

- 模板层的五段删除只影响新会话；14:20 创建的会话在 14:32 删段后仍固定旧系统提示，15:35 trace 仍带五段。
- `user-context-durable` 只阻止"重新注入"，不阻止"历史重放"：dbe0f043 存储里 user-context 只有 1 份，但每个请求都随第一条用户消息重发。
- 最新会话 dbe0f043 的 `<current_time>` 已为 0；旧会话 / 云端助手 / 项目详情页仍可能带。

### 改了什么

- 新增 `scripts/patch-cli-trim.py`：在 agent runtime chunk `1849.*.mjs` 的 provider 边界清理 `modelInput.instructions` 与 `.input`。
  - 第一轮保留 user-context；后续轮次从历史用户消息整块删除；只含 reminder 的消息丢弃。
  - 系统提示删五段 + `quality_policy` / `agent_loop` / `regional_conventions` + `<current_time>` + `<additional_data>`。
  - 流式 / 非流式两处都包；`node --check` 失败回滚；提供 `--status` / `--apply` / `--restore` / `--self-test`。
- `scripts/deploy.ps1`：新增 `-CliPatch` / `-SkipCliPatch`；另加 `scripts/refresh-cli-prewarm.py`（只停 `kind=prewarm` + `status=idle` 的助手，不碰主程序/当前聊天）；默认全量 deploy 在 asar 之前跑 CLI 补丁；新增 `Start-WorkBuddyIfClosed`，deploy / cli-patch 结束时如果应用没在运行就经 `explorer.exe` 自动拉起（**从不结束进程**）。
- `scripts/release-manifest.js` + `sync-release.js`：新脚本随包发布，审计全覆盖。

### 公开包与源目录：把两份实现合成一份（本轮新增）

用户原话：「release 的功能内容不全，存在缺陷，跟目录的行为不一样，脚本结束要自动拉起」。

- **新增 `scripts/_deps.js`**：唯一判断"nunjucks 在哪、数据根在哪"的地方（`loadNunjucks()` / `dataRoot()` / `dataFile()`）。
  包根有 `node_modules/` 与 `examples/`，维护者工作区里这两样在 `release/` 下——以前每个脚本各写一遍候选路径，
  于是同一份脚本在两个地方长成了两套实现。
- **5 个脚本合并成逐字节相同的单一实现**：`render-check.js` / `cache-prefix.js` / `check-memory-blocks.js` /
  `render-runtime-templates.js` / `render-tool-overrides.js`。`release-manifest.js` 里那份 `SCRIPT_DIFF_ALLOWED`
  豁免名单**整份删除**，改由 `SYNC_FILES` 逐字节比对。
- **两处真实漂移因此被修回**：
  - 源的 `render-check.js` 丢过 `--root` 的上下文跟随（`--root` 渲染别的树时仍读本地 `examples/`）；现为"目标树有 examples 就用它，没有回退数据根"。
  - 源的 `cache-prefix.js` 丢过中文双宽对齐（表格按字符数补空格，中文列错位）；现已与包内一致。
- **`render.js` / `check.js` 搬进源目录**（原先只在包里）：用 `dataRoot()` 解析，源目录跑出的结果与包内完全一致（写到 `release/dist`）。
- **审计新增反向断言**：不只看"清单里的文件都在包里"，还看"包里 `scripts/` 每个文件都在清单里"——
  绕过清单塞进包、既不同步也不受审计的"第二份实现"会直接 fail。
- **自动拉起收口到一处**（用户要求「脚本结束要自动拉起」）：
  - 覆盖面从 `deploy` / `cli-patch` 扩到 `deploy` / `cli-patch` / `asar` / `asar-restore` / `restore`；
  - **失败也拉起**（原先 `if ($failed -gt 0) { exit 1 }` 在拉起之前，失败就静默退出、不启动）；
  - 只读动作（`-Verify` / `-Pull` / `-ListTargets`）与 `-DryRun` 不启动；`-NoRelaunch` 关闭；
  - 两处 asar 调用统一传 `--no-launch`，拉起只由脚本末尾做——保证 `uc-seen --seed` / `verify` 先收口再开进程，也避免双实例；
  - 修 `Start-WorkBuddyIfClosed` 的**多目标短路**：原来第一个目标已运行就 `return`，第二个未运行的目标永远不会被拉起，现改为 `continue` 并只在真的启动后返回。
- **新增回归断言 `scripts/test-launch-coverage.ps1`**：静态断言（名单/顺序/`--no-launch`）+ 用 AST 取出真实的
  `Start-WorkBuddyIfClosed` 跑四种场景（`Start-Process` 被同名函数拦截，不会真开窗口），20 条。
  造两处回归（把 `continue` 改回 `return`、从名单删掉 `asar-restore`/`restore`）→ 7 条失败、exit 1；复原 → 全绿。
- **文档补全**：`HOWTO-修改系统提示词.md`（改哪个文件 → 怎么应用 → 怎么验证）与
  `TROUBLESHOOTING-not-effective.md`（「改了不生效」的排查链）随包发布；后者的本机路径已换成 `%USERPROFILE%` / `$HOME`。
  清单从 9 组 69 文件增到 **10 组 75 文件**。
- **`scripts/README.md` 补上漏掉的条目**：`refresh-cli-prewarm.py`、`_deps.js`、`test-launch-coverage.ps1`。

### 证据

- `1849.*.mjs` 247,364 → 250,670 字节；`--status` PATCHED；`--self-test` OK。
- 真实 20,003 字符请求前缀清理后 17,672（删 2,331），五段 / current_time / user-context 全无，query 保留。
- 真实会话 dbe0f043 第一条消息：第一轮保留 `user_info`；第二轮历史 user-context 删除、两条 query 保留。
- `deploy.ps1 -CliPatch -DryRun`：两个 target OK，`App already running`，exit 0。
- `npm run audit`：`RELEASE OK`，版本号三处一致（v1.3.4）。

### 边界

- 已打开的旧聊天不会热加载 chunk；开新聊天或完全退出后重开 WorkBuddy。脚本不杀进程。
- trace / model-io 记录清理前快照；实际发给 provider 的是清理后的副本。
- renderer 两处 `<current_time>` 源码仍在（云端助手 / 项目详情页），由 CLI 边界删除产出；要删源码需关应用后做 asar 补丁。

## v1.3.3 — 2026-11-21

主题：**按用户要求再删五段过程性说明，提示词继续瘦身。**

用户原话：「删除tool_usage_policy，result_presentation，final_answer_instructions，task_management，agent_skills」。

### 删了什么

- `fragments/tool-usage-policy.md`（423 B，`<tool_usage_policy>`）整份删除。
- `fragments/agent-skills.md`（384 B，`<agent_skills>`）整份删除。
- `plugins/workbuddy-builtin/interactionmode/{craft,expert,plan}/fragments/task-management.md`（各 352 B，`<task_management>`）整份删除。
- `plugins/workbuddy-builtin/interactionmode/{ask,craft,expert,plan}/fragments/result-presentation.md`（630 / 1,163 / 1,379 / 1,379 B）整份删除——这四个文件里装的就是 `<result_presentation>` 与 `<final_answer_instructions>`；ask 那份只有 `<final_answer_instructions>`，所以 ask 模式从此不再有任何「最终答复」约束段。
- 三份入口模板各删 4 处 `{% include %}`：`work.tpl` 4,748 → 3,907、`code.tpl` 4,436 → 3,595、`design.tpl` 10,055 → 9,214（各 −841 B）。

### 没动什么（避免误读）

- `<tool_use>`、`<asking_questions>`、`<tool_routing>`、`<agent_contract>`、`<search_routing>` 全部保留。
- `interactionmode-*/interaction.md` 里的 `tools:` 工具集**没有动**：`TaskCreate` / `TaskUpdate` / `TaskList`、`present_files` 仍然可用。删掉的是「怎么用它们的说明」，不是能力本身——是否保留工具由用户决定，不由框架代劳。

### 渲染体积（CN 档，`npm run render` 后 dist 实际字节）

| 模板/模式 | 前 | 后 | 变化 |
|---|---:|---:|---:|
| work·code craft | 10,315 | 7,983 | −2,332（−22.61%） |
| work·code plan | 11,244 | 8,696 | −2,548（−22.66%） |
| work·code expert | 10,569 | 8,021 | −2,548（−24.11%） |
| work·code ask | 3,438 | 2,806 | −632（−18.38%） |
| work·code quick | 1,583 | 1,581 | −2（仅少一行空行） |
| design craft | 15,716 | 13,384 | −2,332（−14.84%） |
| design plan | 16,645 | 14,097 | −2,548（−15.31%） |
| design expert | 15,970 | 13,422 | −2,548（−15.95%） |
| design ask | 5,209 | 4,577 | −632（−12.13%） |
| design quick | 4,765 | 4,763 | −2（仅少一行空行） |
| **合计（15 份）** | **132,603** | **108,417** | **−24,186（−18.24%）** |

> 体积口径：`dist/` 数字包含 `interactionmode-*/interaction.md` 的 YAML frontmatter（craft / plan / expert 826–863 B、ask 163 B、quick 94 B），运行时宿主会把它解析成工具集后剥离，不发进请求。absolute 值因此略偏大，**同一轮的前后差值不受影响**。

### 断言与门禁

- `scripts/render-check.js`：`REMOVED_BLOCKS` 由 3 个扩到 8 个（11-17 三段 + 本轮五段），任一渲染出现即 `failures++`，`exit 1`。
- `scripts/check.js`：同一份 8 个标签的「出现即失败」断言。
- `scripts/measure-prompt-blocks.py`：这 5 个名字从归因表 `BLOCKS` 移到新列表 `REMOVED`，读真实 trace 时自动判红（不再依赖有没有记得传 `--expect-absent`）。
- 反向验证（先证断言会红）：把删前的三份模板与九个片段还原到一棵临时树，用新断言渲染 → `RENDER FAILURES: 12`、`exit 1`（quick 三份与 ask 的 `<result_presentation>` 本就不含，故 3 份通过）。

### 前缀缓存

`npm run cache -- --strict` 仍 `exit 0`：同模式场景最低 96.19% → 96.18%（门禁 95%）。跨模式因共享前缀占比上升而改善：work craft~plan 75.87% → 84.76%、craft~ask 36.22% → 44.91%、design craft~plan 84.84% → 91.52%、craft~ask 73.07% → 83.60%。

### 归档

`archive/20261121-removed-five-blocks/`：九个片段原件、删前模板、`tools/remove-five-blocks.py`（幂等，可 `--check` 干跑）与回滚说明。

## v1.3.2 — 2026-11-21

主题：**保留"打完启动"，只去掉"主动结束"**。

- `patch-asar.py` 打完 asar / exe 清单后自动把应用启动起来：经 `explorer.exe` 拉起（本脚本通常是提权跑的，
  直接 `Popen` 会让应用继承管理员令牌），`--no-launch` 可关；exe 不存在（例如在副本目录排障）就跳过。
- `deploy.ps1` 的 `-Asar` 动作不再自己重启（避免与上面重复开两个实例），统一由 `patch-asar.py` 负责；
  `-NoRelaunch` 现在等价于 `--no-launch`。部署路径（`deploy.cmd` 无参数）同样受益。
- 仍然**从不结束任何进程**：应用在运行时 `patch-asar.py` 退出码 `4`、一个字节不写，
  `deploy.ps1` 记 `FAIL` + 整体 `exit 1`。
- 新增 `scripts/test-asar-launch.py`（8 项）：经 explorer 真拉起一个探针并断言标记文件生成、
  exe 缺失 / `--no-launch` 时跳过、`patch-asar.py` 源码里不含 `taskkill` / `TerminateProcess` / `Stop-Process` / `kill(`。
- 起因：用户「保留启动」。

## v1.3.1 — 2026-11-21

主题：**移除"主动结束 WorkBuddy 进程"的能力**，改为只检测、不动手。

- `deploy.cmd` / `deploy.ps1` / `patch-asar.py` 不再结束任何进程：删除 `--restart` / `-NoRestart`
  与整套进程枚举、关闭、重启实现（约 7 KB）。
- 应用在运行时回到明确失败：`patch-asar.py` 退出码 `4`、一个字节都不写；`deploy.ps1` 记为 `FAIL`、
  整条命令 `exit 1`，提示"完全退出 WorkBuddy（含托盘）后重跑"。
- **保留**：打完 `--status` 收口（任一补丁非 `patched`、`integ`/`exe sync` 非 `MATCH` 即 `FAIL`）、
  `uc-seen.py --seed`、`user-context-durable` 补丁、"源 `scripts/` 全覆盖"审计断言、`-Asar` 成功后的重启（`-NoRelaunch` 可关）。
- 移除 `scripts/test-asar-restart.py`（它验证的就是被删掉的那条链路）。
- 起因：v1.3.0 的 `close_app` 曾用 `taskkill /IM` 误杀过真实进程；虽然随后改为 pid-only，
  按用户要求把"主动结束进程"整块去掉。

## v1.3.0 — 2026-11-21

> 注：本版引入的自动「关闭 → 注入 → 重启」已在 v1.3.1 移除。

主题：**一键"关闭 → 注入 → 重启"，且打完必复核；`user-context` 的"只发一次"真正落盘。**

### 一键部署不再要求你先关应用

- `deploy.cmd` / `deploy.ps1` 默认自动：识别谁占着 `app.asar` 与 exe（按**可执行文件全路径**匹配进程）
  → 请它自己关（8 s）→ 仍在则强杀它自己的进程树（15 s）→ 等文件解锁（40 s）→ 写入 → 再启动。
- **只按 pid 杀**（`taskkill /PID … /T`），删除了一切 `/IM` 用法：按映像名批量杀会连带杀掉同名副本
  或另一个安装——2026-11-21 用替身 exe 做测试时正是这样误杀了真实进程，这条写进了代码注释与回归断言。
- 关过就一定开回来：关进程后注册退出钩子，写入失败 / 校验失败 / 异常退出都会重启。
- 打完立刻用 `--status` 收口：任一补丁不是 `patched`、`integ` 或 `exe sync` 不是 `MATCH`，
  本次部署记 `FAIL` 并整体 `exit 1`——不再出现"看起来成功、其实没打上"。
- `-NoRestart`（旧参数 `-NoRelaunch`）退回手工模式：应用在跑时直接 `FAIL`，绝不静默跳过。

### `user-context` 判据落盘（第 8 个 asar 补丁）

- 新增补丁 `user-context-durable`：`composeUserPrompt` 里同步读写 `<数据目录>\uc-seen\<sessionId>`，
  与 renderer 传来的标志取或；项目/Teams 分支的 `connector-status` 也纳入同一判据。
- 为什么必须换地方：v1.1.x 的 `user-context-once` 把判据写进 renderer 的 `localStorage`，
  而该 bundle 里**根本没有 localStorage**（自带的 8 处用法全被 `typeof localStorage<'u'` 或 try/catch
  保护），`self.localStorage?.` 静默 no-op → `alreadyInjectedUserContext` 实测 **53/53 全 false**
  （11/18–11/21 主进程日志）。旧补丁保留但标注为占位，真正生效的是新补丁。
- `uc-seen.py --seed`：给"补丁上线前就存在、且已有 transcript"的会话补标记，
  补完**立即生效**（每次 compose 都读盘，不用重启）；读不到 `"role":"user"` 的空草稿会跳过。
- `deploy.ps1` 在 asar 补丁之后自动执行 seed。
- 语义边界：`/clear` 走 `isClearSession===!0` 提前 return，标记不会被清 → 同一 sessionId 清空对话后
  不再重注；桌面端「新对话」是新 sessionId，不受影响。要清用 `uc-seen.py --clear <sid>`。

### 发布门槛补一条硬断言

- `release-audit.js` 新增「全覆盖」：**源 `scripts/` 下每个文件都必须在 manifest 里**，
  漏一个就 `exit 1`。这条把"新写了脚本但忘了进包"从人肉检查变成机器门槛（本次先证它会红，
  抓到 `test-asar-restart.py` 漏登记，再补清单转绿）。

### 新增文件

| 文件 | 用途 |
|---|---|
| `scripts/uc-seen.py` | `user-context-durable` 标记管理：`--count` / `--seed` / `--clear` / `--prune` |
| `scripts/trace-turns.py` | 按轮读 trace 请求体，判断某块是首轮一次还是每轮都有（附实测：trace 只落每个会话的首个请求） |
| `scripts/test-asar-restart.py` | 断言关进程链路：全路径匹配、只发 `/PID`、同名副本不误杀，16 项 |

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

> 下表与下面几条描述的是当时的顺序；其中 `tool-usage-policy` / `agent-skills` / `task-management` / `result-presentation` 已于 2026-11-21 按用户要求整段移除，此处只作历史记录。

| 层 | 内容 | 何时会变 |
|---|---|---|
| A 恒定层 | preamble → `policy-common` → `workspace-safety` / `regional-conventions` → `agent-contract` → `search_routing` → `wecom-account-routing` → `tool-routing` → `asking-questions` → `tool-usage-policy` → `agent-skills` → `tool-use` / `task-management` | 只在改提示词或换安装时才变 |
| B 模式层 | `interaction` → `mode-behavior`(ask) → `current-mode` → `agent-loop` → `result-presentation` | 只在对话内切换模式时变 |
| C 会话层 | `memory_system` → `industry_mode_context` → `<memory>` → `<user_memory>` → `<memory_paths>` → `This conversation is powered by …` | 换项目 / 换账号 / 记忆更新 / 换模型 |

- `This conversation is powered by …` 从**首行**移到**最末行**。它带模型名，原来在第 38 个字符就出现，换模型等于整段前缀作废。
- `tool-use` / `task-management` 提前到模式层之前：craft / plan / expert 三份内容逐字相同，提前后会话内换模式的公共前缀明显变长（`task-management` 已于 2026-11-21 移除）。

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
- 精简 `agent-skills.md`（3 行 → 2 行，该文件 2026-11-21 整份删除）、`policy-common.md`（去掉与模式无关的两条）、`tool-routing.md`（去掉与技能清单重复的触发词说明）。

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
