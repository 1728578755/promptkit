# test-launch-coverage.ps1 —— 断言 deploy.ps1 的「脚本结束即拉起」覆盖面。
#
# 为什么要单独测：拉起是"跑完才看得见"的行为，等用户真的关掉应用跑一遍才发现
# 某个动作不拉起，成本太高。这里用 AST 把真实的 Start-WorkBuddyIfClosed 取出来执行，
# 并用同名函数覆盖 Start-Process，只记录"本该启动什么"，不真的打开应用。
#
# 用法：powershell -NoProfile -ExecutionPolicy Bypass -File scripts\test-launch-coverage.ps1
# 退出码：0 = 全部通过；1 = 有断言失败。
$ErrorActionPreference = "Stop"
$here = $PSScriptRoot
$src = Join-Path $here "deploy.ps1"
if (-not (Test-Path -LiteralPath $src)) { Write-Host "deploy.ps1 not found: $src" -ForegroundColor Red; exit 1 }
$text = Get-Content -Raw -LiteralPath $src

$script:fail = 0
function Check([bool]$cond, [string]$msg) {
    if ($cond) { Write-Host ("  OK   {0}" -f $msg) }
    else { Write-Host ("  FAIL {0}" -f $msg) -ForegroundColor Red; $script:fail++ }
}

Write-Host "== 静态断言：拉起名单与顺序 =="

# 1. 五个写盘动作都要拉起；只读动作不许进来
foreach ($a in @("deploy", "cli-patch", "asar", "asar-restore", "restore")) {
    Check ($text -match ('\$launchActions\s*=\s*@\([^)]*"' + [regex]::Escape($a) + '"')) "launchActions 含 $a"
}
foreach ($a in @("verify", "pull", "list")) {
    Check (-not ($text -match ('\$launchActions\s*=\s*@\([^)]*"' + [regex]::Escape($a) + '"'))) "launchActions 不含只读动作 $a"
}

# 2. -DryRun 不启动（干跑不该弹窗）
Check ($text -match '\$launchActions -contains \$action\)\s*-and\s*-not\s*\$DryRun') "拉起受 -not `$DryRun 约束"

# 3. 拉起必须发生在"失败退出"之前：失败也要能看到应用起来
$idxLaunch = $text.IndexOf("Start-WorkBuddyIfClosed `$profiles")
$idxFail = $text.IndexOf('if ($failed -gt 0)')
Check ($idxLaunch -gt 0 -and $idxFail -gt 0 -and $idxLaunch -lt $idxFail) "拉起在 exit 1 之前"

# 4. asar 的拉起统一交给脚本末尾：两处调用都必须带 --no-launch，避免双实例
$noLaunch = ([regex]::Matches($text, '"--no-launch"')).Count
Check ($noLaunch -ge 2) ("两处 asar 调用都传 --no-launch（实际 {0} 处）" -f $noLaunch)
Check (-not ($text -match 'if\s*\(\$NoRelaunch\)\s*\{\s*\$asarArgs\s*\+=')) "不再把 --no-launch 绑在 -NoRelaunch 上"

# 5. 函数体里循环用 continue：一套在跑不代表另一套不用启动
$ast = [System.Management.Automation.Language.Parser]::ParseFile($src, [ref]$null, [ref]$null)
$fnAst = $ast.FindAll({
        param($n)
        $n -is [System.Management.Automation.Language.FunctionDefinitionAst] -and $n.Name -eq "Start-WorkBuddyIfClosed"
    }, $true) | Select-Object -First 1
Check ($null -ne $fnAst) "能定位 Start-WorkBuddyIfClosed"
if ($null -eq $fnAst) { Write-Host "无法继续行为测试" -ForegroundColor Red; exit 1 }
$fnText = $fnAst.Extent.Text
Check ($fnText -match 'continue') "循环里用 continue（不短路）"
Check (-not ($fnText -match 'if\s*\(@\(Get-Process[^\)]*\)\.Count\s*-gt\s*0\)\s*\{[^}]*\breturn\s*\}')) "已运行分支不再直接 return"

Write-Host ""
Write-Host "== 行为断言：执行真实函数体，拦截 Start-Process =="

# 只加载这个函数，不执行脚本其它部分
Invoke-Expression $fnText

# 覆盖 Start-Process：只记录参数，不真的启动任何程序
$script:launched = New-Object System.Collections.ArrayList
function Start-Process {
    param($FilePath, $ArgumentList)
    [void]$script:launched.Add([pscustomobject]@{ File = $FilePath; Args = [string]$ArgumentList })
}

$NoRelaunch = $false
$tmp = Join-Path $env:TEMP ("wb-launch-probe-" + [guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $tmp -Force | Out-Null
$probe = Join-Path $tmp "probe-not-running.exe"
Set-Content -LiteralPath $probe -Value "" -Encoding ascii

# explorer 一定在跑：用它当"已运行"的替身，不需要额外造进程
$running = [pscustomobject]@{ Name = "already"; Installed = $true; ExeName = "explorer.exe"; InstallPath = $env:WINDIR }
$closed = [pscustomobject]@{ Name = "closed"; Installed = $true; ExeName = "probe-not-running.exe"; InstallPath = $tmp }

# 场景 1：第一个已运行、第二个未运行 → 必须启动第二个（旧实现会在第一个就 return）
$script:launched.Clear()
$r1 = Start-WorkBuddyIfClosed @($running, $closed)
Check ($script:launched.Count -eq 1) ("已运行+未运行：启动 {0} 次（期望 1）" -f $script:launched.Count)
Check ($script:launched.Count -eq 1 -and $script:launched[0].Args -match 'probe-not-running') "启动的是未运行的那一个"
Check ($r1 -eq $true) "返回值 true 表示确实启动了"

# 场景 2：全部已运行 → 一次都不启动
$script:launched.Clear()
$r2 = Start-WorkBuddyIfClosed @($running)
Check ($script:launched.Count -eq 0) ("全部在跑：启动 {0} 次（期望 0）" -f $script:launched.Count)
Check ($r2 -eq $false) "返回值 false 表示没启动"

# 场景 3：-NoRelaunch → 一次都不启动
$NoRelaunch = $true
$script:launched.Clear()
$r3 = Start-WorkBuddyIfClosed @($closed)
Check ($script:launched.Count -eq 0) ("-NoRelaunch：启动 {0} 次（期望 0）" -f $script:launched.Count)
$NoRelaunch = $false

# 场景 4：未安装的目标要跳过，不报错
$script:launched.Clear()
$missing = [pscustomobject]@{ Name = "missing"; Installed = $false; ExeName = ""; InstallPath = "" }
$r4 = Start-WorkBuddyIfClosed @($missing, $closed)
Check ($script:launched.Count -eq 1) ("未安装+未运行：启动 {0} 次（期望 1）" -f $script:launched.Count)

Remove-Item -LiteralPath $tmp -Recurse -Force -ErrorAction SilentlyContinue

Write-Host ""
if ($script:fail -gt 0) {
    Write-Host ("LAUNCH COVERAGE FAILURES: {0}" -f $script:fail) -ForegroundColor Red
    exit 1
}
Write-Host "LAUNCH COVERAGE OK —— 所有写盘动作结束都会拉起，失败也拉起，只读动作与干跑不会。"
exit 0
