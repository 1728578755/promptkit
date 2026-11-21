# 回归：deploy.ps1 末尾的逐层验收（Invoke-StackVerify）必须真的能跑。
#
# 为什么要单独测：这一段只在 deploy 结束时执行，平时要看它得真的写 Program Files
# （需要 UAC）。这里把函数从 deploy.ps1 里原样取出来，喂一个最小 profile，
# 验证它读得到 verify-stack.py、能打印 RESULT、能落 reports\verify-latest.txt。
$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $ScriptRoot "..")).Path

$src = Get-Content -LiteralPath (Join-Path $ScriptRoot "deploy.ps1") -Raw
$m = [regex]::Match($src, 'function Invoke-StackVerify\(\$profiles\) \{[\s\S]*?\r?\n\}')
if (-not $m.Success) {
    Write-Host "FAIL: Invoke-StackVerify not found in deploy.ps1" -ForegroundColor Red
    exit 1
}

# Get-PythonExe 在 deploy.ps1 里也有，这里给一个同样行为的实现，保证被取出的函数能独立跑
function Get-PythonExe([string]$configDir) {
    if ($env:WORKBUDDY_PYTHON) { return $env:WORKBUDDY_PYTHON }
    foreach ($cand in @("python", "python3", "py")) {
        $c = Get-Command $cand -ErrorAction SilentlyContinue
        if ($c -and $c.Source -notmatch 'WindowsApps') { return $c.Source }
    }
    return $null
}

Invoke-Expression $m.Value

$profiles = @([pscustomobject]@{
    Name = "harness"
    Label = "verify hook"
    Installed = $true
    InstallPath = "C:\Program Files\WorkBuddy"
    ConfigDir = (Join-Path $env:USERPROFILE ".workbuddy")
})

$code = Invoke-StackVerify $profiles
$report = Join-Path $ProjectRoot "reports\verify-latest.txt"

$problems = @()
if ($code -ne 0) { $problems += "exit code $code (expected 0)" }
if (-not (Test-Path -LiteralPath $report)) { $problems += "report not written: $report" }
else {
    $text = Get-Content -LiteralPath $report -Raw
    if ($text -notmatch 'RESULT\s*:\s*(PASS|FAIL)') { $problems += "report has no RESULT line" }
    if ($text -notmatch 'version=3') { $problems += "report has no cli version=3" }
    if ($text -notmatch 'current-time-cloud') { $problems += "report has no asar patch list" }
}

if ($problems.Count -gt 0) {
    foreach ($p in $problems) { Write-Host ("FAIL: " + $p) -ForegroundColor Red }
    exit 1
}
Write-Host "VERIFY HOOK OK" -ForegroundColor Green
exit 0
