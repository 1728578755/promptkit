[CmdletBinding()]
param(
    [string]$Template = "",
    [string]$Source = "",
    [string]$Skill = "",
    [switch]$All,
    [switch]$DryRun,
    [switch]$NoInstall,
    [switch]$SkipVerify,
    [switch]$Detailed,
    [switch]$UseCustom,
    [switch]$IncludeTemplates,
    [switch]$NoPluginCache,
    [switch]$NoRegisterSkills,
    [ValidateSet("All", "Latest")]
    [string]$CacheVersions = "",
    [string]$WorkBuddyPath = "",
    [string]$PluginCacheRoot = ""
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $ScriptRoot "..")).Path
$ps = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

$isAdmin = Test-Admin
$hasTarget = [bool]($Template -or $Source -or $Skill)

$applyArgs = @(
    "-NoProfile", "-ExecutionPolicy", "Bypass",
    "-File", (Join-Path $ScriptRoot "apply.ps1"),
    "-NoElevate"
)
if ($All -or -not $hasTarget) { $applyArgs += "-All" }
if ($Template) { $applyArgs += @("-Template", $Template) }
if ($Source) { $applyArgs += @("-Source", $Source) }
if ($Skill) { $applyArgs += @("-Skill", $Skill) }
if ($WorkBuddyPath) { $applyArgs += @("-WorkBuddyPath", $WorkBuddyPath) }
if ($PluginCacheRoot) { $applyArgs += @("-PluginCacheRoot", $PluginCacheRoot) }
if ($CacheVersions) { $applyArgs += @("-CacheVersions", $CacheVersions) }
if ($UseCustom) { $applyArgs += "-UseCustom" }
if ($IncludeTemplates) { $applyArgs += "-IncludeTemplates" }
if ($NoPluginCache) { $applyArgs += "-NoPluginCache" }
if ($NoRegisterSkills) { $applyArgs += "-NoRegisterSkills" }
if ($DryRun) { $applyArgs += "-DryRun" }
if ($NoInstall -or -not $isAdmin) { $applyArgs += "-NoInstall" }
if ($Detailed) { $applyArgs += "-Verbose" }

$scope = if ($hasTarget) { "target" } else { "all" }
Write-Host "== Deploy: apply (scope=$scope, install=$(if ($NoInstall -or -not $isAdmin) { 'skip' } else { 'write' }), cache=write) ==" -ForegroundColor Cyan
& $ps @applyArgs
if ($LASTEXITCODE -ne 0) {
    throw "apply.ps1 failed with exit code $LASTEXITCODE"
}

$required = @(
    "visualizer",
    "mcp-setup",
    "automation-setup",
    "agent-mail",
    "ardot-canvas-verify",
    "team-orchestration",
    "workbuddy-docs"
)
foreach ($name in $required) {
    $path = Join-Path $ProjectRoot "plugins\workbuddy-builtin\skills\$name\SKILL.md"
    if (-not (Test-Path -LiteralPath $path)) {
        throw "missing builtin skill source: $name ($path)"
    }
}
Write-Host ("== Deploy: {0} builtin skill sources present ==" -f $required.Count) -ForegroundColor Cyan
Write-Host ""

if ($DryRun) {
    Write-Host "[DryRun] preview only; verify skipped." -ForegroundColor Yellow
    exit 0
}

if (-not $SkipVerify) {
    Write-Host "== Deploy: verify ==" -ForegroundColor Cyan
    & $ps -NoProfile -ExecutionPolicy Bypass -File (Join-Path $ScriptRoot "verify.ps1")
    $verifyExit = $LASTEXITCODE
    if ($verifyExit -eq 1) {
        if ($NoInstall -or -not $isAdmin) {
            Write-Host "verify could not complete (install mirror not available); cache files were hash-verified by apply." -ForegroundColor Yellow
        } else {
            throw "verify.ps1 reported a failure (exit 1)"
        }
    }
    if ($verifyExit -eq 2) {
        Write-Host "verify: a marker was not covered by the captured prefix (exit 2); check manually." -ForegroundColor Yellow
    }
}

if (-not $isAdmin) {
    Write-Host ""
    Write-Host "NOTE: not elevated - the install mirror was skipped; the plugin cache (runtime read point) is updated." -ForegroundColor Yellow
    Write-Host "      Run this script from an administrator terminal to sync the install mirror." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Deploy complete. Open a new chat session to pick up the new prompt and skills." -ForegroundColor Green
exit 0
