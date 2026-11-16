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
    [string]$PluginCacheRoot = "",
    [string]$Target = "",
    [string]$ConfigDir = "",
    [string]$Overlay = "",
    [switch]$Elevate,
    [switch]$ListTargets
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $ScriptRoot "..")).Path
$ps = Join-Path $env:SystemRoot "System32\WindowsPowerShell\v1.0\powershell.exe"

. (Join-Path $ScriptRoot "target.ps1")

if ($ListTargets) {
    $cfg = Get-TargetConfig $ScriptRoot
    foreach ($name in (Get-TargetNames $ScriptRoot)) {
        $p = Resolve-TargetProfile -Name $name -ScriptRoot $ScriptRoot
        $mark = if ($name -eq $cfg.default) { "*" } else { " " }
        Write-Host ("{0} {1,-14} {2}" -f $mark, $name, $p.Label)
        Write-Host ("    install:  {0}  {1}" -f $p.InstallPath, $(if ($p.Installed) { "(found)" } else { "(NOT FOUND)" }))
        Write-Host ("    config:   {0}  {1}" -f $p.ConfigDir, $(if ($p.ConfigExists) { "(found)" } else { "(missing)" }))
        Write-Host ("    cache:    {0}" -f $p.CacheRoot)
        if ($p.Overlay) { Write-Host ("    overlay:  {0}" -f $p.Overlay) }
    }
    exit 0
}

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

$isAdmin = Test-Admin
$hasTarget = [bool]($Template -or $Source -or $Skill)

# -Elevate: relaunch this script as administrator (synchronous) so the install mirror
# gets written too. Without it a non-admin run can only update the plugin cache, and
# builtin skills never become visible to the app.
if ($Elevate -and -not $isAdmin) {
    $fwd = @()
    foreach ($k in @($PSBoundParameters.Keys)) {
        if ($k -eq 'Elevate') { continue }
        $v = $PSBoundParameters[$k]
        if ($v -is [switch]) { if ($v.IsPresent) { $fwd += "-$k" } }
        else { $fwd += "-$k"; $fwd += ('"{0}"' -f $v) }
    }
    $argLine = ('-NoProfile -ExecutionPolicy Bypass -File "{0}" ' -f $PSCommandPath) + ($fwd -join ' ')
    Write-Host "Requesting administrator privileges for the install mirror..." -ForegroundColor Yellow
    try {
        $p = Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $argLine -Wait -PassThru
        exit $p.ExitCode
    } catch {
        throw "Elevation was cancelled or blocked. Re-run from an administrator terminal: scripts\deploy.cmd -Target $Target"
    }
}

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
if ($Target) { $applyArgs += @("-Target", $Target) }
if ($ConfigDir) { $applyArgs += @("-ConfigDir", $ConfigDir) }
if ($Overlay) { $applyArgs += @("-Overlay", $Overlay) }
if ($CacheVersions) { $applyArgs += @("-CacheVersions", $CacheVersions) }
if ($UseCustom) { $applyArgs += "-UseCustom" }
if ($IncludeTemplates) { $applyArgs += "-IncludeTemplates" }
if ($NoPluginCache) { $applyArgs += "-NoPluginCache" }
if ($NoRegisterSkills) { $applyArgs += "-NoRegisterSkills" }
if ($DryRun) { $applyArgs += "-DryRun" }
$writeInstall = (-not $NoInstall) -and ($isAdmin -or $Elevate)
if (-not $writeInstall) { $applyArgs += "-NoInstall" }
if ($Detailed) { $applyArgs += "-Verbose" }

$scope = if ($hasTarget) { "target" } else { "all" }
Write-Host "== Deploy: apply (scope=$scope, install=$(if ($writeInstall) { 'write' } else { 'skip' }), cache=write) ==" -ForegroundColor Cyan
if ($Elevate -and -not $isAdmin) {
    Write-Host "Elevation requested: a UAC prompt will appear; accept it to write the install mirror (skills are only enumerated from there)." -ForegroundColor Yellow
}
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

# Skills are enumerated from the install mirror (or <config>\skills), never from the
# plugin cache: <config>\.skill-list-cache.json scopeKey lists exactly those folders.
$profile = Resolve-TargetProfile -Name $Target -ScriptRoot $ScriptRoot -ConfigDirOverride $ConfigDir -InstallOverride $WorkBuddyPath -CacheRootOverride $PluginCacheRoot -OverlayOverride $Overlay
$installSkills = ""
if ($profile.Installed) {
    $installSkills = Join-Path $profile.InstallPath "resources\app.asar.unpacked\resources\plugins\workbuddy-builtin\skills"
}
$missingInstallSkills = @()
if ($installSkills) {
    foreach ($name in $required) {
        if (-not (Test-Path -LiteralPath (Join-Path $installSkills "$name\SKILL.md"))) { $missingInstallSkills += $name }
    }
}

if ($DryRun) {
    Write-Host "[DryRun] preview only; verify skipped." -ForegroundColor Yellow
    exit 0
}

if (-not $SkipVerify) {
    Write-Host "== Deploy: verify ==" -ForegroundColor Cyan
    $verifyArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $ScriptRoot "verify.ps1"))
    if ($Target) { $verifyArgs += @("-Target", $Target) }
    if ($ConfigDir) { $verifyArgs += @("-ConfigDir", $ConfigDir) }
    if ($WorkBuddyPath) { $verifyArgs += @("-WorkBuddyPath", $WorkBuddyPath) }
    if ($PluginCacheRoot) { $verifyArgs += @("-PluginCacheRoot", $PluginCacheRoot) }
    $verifyArgs += @("-CheckIncludes")
    & $ps @verifyArgs
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

$missingInstallSkills = @()
if ($installSkills) {
    foreach ($name in $required) {
        if (-not (Test-Path -LiteralPath (Join-Path $installSkills "$name\SKILL.md"))) { $missingInstallSkills += $name }
    }
}
if ($missingInstallSkills.Count -gt 0) {
    Write-Host ""
    Write-Host ("!! SKILLS NOT ACTIVE: {0}/{1} builtin skills are missing from the install mirror:" -f $missingInstallSkills.Count, $required.Count) -ForegroundColor Red
    Write-Host ("   {0}" -f ($missingInstallSkills -join ', ')) -ForegroundColor Red
    Write-Host ("   Looked in: {0}" -f $installSkills) -ForegroundColor DarkGray
    Write-Host "   The app enumerates skills only from the install mirror / user skill folder, not from the plugin cache." -ForegroundColor Yellow
    Write-Host ("   Fix: scripts\deploy.cmd -Target {0} -Elevate   (accept the UAC prompt)" -f $profile.Name) -ForegroundColor Yellow
} else {
    Write-Host ("== Deploy: {0} builtin skills present in install mirror ==" -f $required.Count) -ForegroundColor Cyan
}

if (-not $isAdmin -and -not $Elevate) {
    Write-Host ""
    Write-Host "NOTE: not elevated - the install mirror was skipped; only the plugin cache was updated." -ForegroundColor Yellow
    Write-Host "      Prompts take effect from the cache, but builtin skills do NOT: rerun with -Elevate." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Deploy complete. Open a new chat session to pick up the new prompt and skills." -ForegroundColor Green
exit 0
