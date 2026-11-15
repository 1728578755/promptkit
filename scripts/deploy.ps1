[CmdletBinding()]
param(
    [string]$Config = "",
    [string]$TargetRoot = "",
    [switch]$DryRun,
    [switch]$NoBackup
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"

trap {
    Write-Host ""
    Write-Host ("Deploy failed: " + $_.Exception.Message) -ForegroundColor Red
    exit 1
}

$Root = Split-Path $PSScriptRoot -Parent
if ([string]::IsNullOrWhiteSpace($Config)) {
    $Config = Join-Path $Root "deploy.config.json"
}
if (-not (Test-Path -LiteralPath $Config)) {
    throw "Config not found: $Config"
}

$cfg = Get-Content -LiteralPath $Config -Raw | ConvertFrom-Json
$layout = $cfg.layout
if (-not $layout) {
    $layout = [pscustomobject]@{}
}

function Get-LayoutValue([string]$name, [string]$default) {
    $value = $layout.$name
    if ([string]::IsNullOrWhiteSpace($value)) { return $default }
    return $value
}

$targetRaw = $TargetRoot
if ([string]::IsNullOrWhiteSpace($targetRaw)) { $targetRaw = $cfg.targetRoot }
if ([string]::IsNullOrWhiteSpace($targetRaw)) { $targetRaw = "%USERPROFILE%/.promptkit/runtime" }

$target = [Environment]::ExpandEnvironmentVariables($targetRaw)
if (-not [IO.Path]::IsPathRooted($target)) {
    $target = [IO.Path]::Combine($Root, $target)
}
$target = [IO.Path]::GetFullPath($target)
$targetTrim = $target.TrimEnd('\')
$rootTrim = ([IO.Path]::GetFullPath($Root)).TrimEnd('\')

if ($targetTrim -eq $rootTrim) {
    throw "Target must not be the repository root: $target"
}
if ($target -match '^[A-Za-z]:\\?$' -or $targetTrim -eq '') {
    throw "Refusing to deploy to a drive root: $target"
}
$forbidden = @($env:WINDIR, $env:SystemRoot, $env:ProgramFiles, ${env:ProgramFiles(x86)}, $env:USERPROFILE)
foreach ($path in $forbidden) {
    if ($path -and $targetTrim -eq ([IO.Path]::GetFullPath($path)).TrimEnd('\')) {
        throw "Refusing to deploy directly into a system folder: $target"
    }
}

$welcomeLayout = Get-LayoutValue "welcomeMode" "welcome-mode/{file}"
$fragmentsLayout = Get-LayoutValue "fragments" "prompt-common/fragments/{file}"
$interactionLayout = Get-LayoutValue "interactionMode" "interactionmode-{mode}/fragments/{file}"
$skillsLayout = Get-LayoutValue "skills" "skills/{name}/{path}"

$plan = New-Object System.Collections.Generic.List[object]
$destinations = @{}

function Add-PlanItem([string]$source, [string]$relative, [string]$category) {
    $relative = ($relative -replace '/', '\').TrimStart('\')
    if ([string]::IsNullOrWhiteSpace($relative)) {
        throw "Empty destination for $source"
    }
    $destination = [IO.Path]::Combine($target, $relative)
    $key = $destination.ToLowerInvariant()
    if ($destinations.ContainsKey($key)) {
        throw "Destination collision: $relative"
    }
    $destinations[$key] = $true
    $plan.Add([pscustomobject]@{
        Source = $source
        Relative = $relative
        Destination = $destination
        Category = $category
    })
}

$welcomeDir = Join-Path $Root "welcome-mode"
if (Test-Path -LiteralPath $welcomeDir) {
    Get-ChildItem -LiteralPath $welcomeDir -File -Filter "*.tpl" | ForEach-Object {
        Add-PlanItem $_.FullName ($welcomeLayout.Replace("{file}", $_.Name)) "template"
    }
}

$fragmentsDir = Join-Path $Root "fragments"
if (Test-Path -LiteralPath $fragmentsDir) {
    Get-ChildItem -LiteralPath $fragmentsDir -File -Filter "*.md" | ForEach-Object {
        Add-PlanItem $_.FullName ($fragmentsLayout.Replace("{file}", $_.Name)) "fragment"
    }
}

$pluginsDir = Join-Path $Root "plugins"
if (Test-Path -LiteralPath $pluginsDir) {
    $interactionRoots = Get-ChildItem -LiteralPath $pluginsDir -Recurse -Directory -Filter "interactionmode" -ErrorAction SilentlyContinue
    foreach ($interactionRoot in $interactionRoots) {
        foreach ($modeDir in (Get-ChildItem -LiteralPath $interactionRoot.FullName -Directory -ErrorAction SilentlyContinue)) {
            $fragmentDir = Join-Path $modeDir.FullName "fragments"
            if (-not (Test-Path -LiteralPath $fragmentDir)) { continue }
            foreach ($file in (Get-ChildItem -LiteralPath $fragmentDir -File -ErrorAction SilentlyContinue)) {
                $relative = $interactionLayout.Replace("{mode}", $modeDir.Name).Replace("{file}", $file.Name)
                Add-PlanItem $file.FullName $relative "interaction"
            }
        }
    }

    $skillRoots = Get-ChildItem -LiteralPath $pluginsDir -Recurse -Directory -Filter "skills" -ErrorAction SilentlyContinue
    foreach ($skillRoot in $skillRoots) {
        foreach ($skillDir in (Get-ChildItem -LiteralPath $skillRoot.FullName -Directory -ErrorAction SilentlyContinue)) {
            $files = Get-ChildItem -LiteralPath $skillDir.FullName -File -Recurse -ErrorAction SilentlyContinue
            foreach ($file in $files) {
                $inside = $file.FullName.Substring($skillDir.FullName.Length + 1)
                $relative = $skillsLayout.Replace("{name}", $skillDir.Name).Replace("{path}", $inside).Replace("{file}", $file.Name)
                Add-PlanItem $file.FullName $relative "skill"
            }
        }
    }
}

if ($plan.Count -eq 0) {
    throw "Nothing to deploy; check the source layout."
}

Write-Host "== Deploy plan ==" -ForegroundColor Cyan
Write-Host ("Target: {0}" -f $target)
Write-Host ("Files:  {0}" -f $plan.Count)
$plan | Group-Object Category | Sort-Object Name | ForEach-Object {
    Write-Host ("  {0,-12} {1}" -f $_.Name, $_.Count)
}
Write-Host ""

if ($DryRun) {
    Write-Host ""
    Write-Host "First mappings:" -ForegroundColor Cyan
    $plan | Select-Object -First 8 | ForEach-Object {
        Write-Host ("  {0} -> {1}" -f $_.Relative, $_.Destination)
    }
    Write-Host ""
    Write-Host "[DryRun] No files were written." -ForegroundColor Yellow
    exit 0
}

$doBackup = (-not $NoBackup) -and ($cfg.backup -ne $false)
$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupRoot = Join-Path $Root ("backups\" + $stamp)
$backedUp = 0

if ($doBackup) {
    foreach ($item in $plan) {
        if (Test-Path -LiteralPath $item.Destination -PathType Leaf) {
            $backupPath = Join-Path $backupRoot $item.Relative
            $backupDir = Split-Path $backupPath -Parent
            if (-not (Test-Path -LiteralPath $backupDir)) {
                New-Item -ItemType Directory -Path $backupDir -Force | Out-Null
            }
            Copy-Item -LiteralPath $item.Destination -Destination $backupPath -Force
            $backedUp++
        }
    }
}

$copied = 0
foreach ($item in $plan) {
    $destinationDir = Split-Path $item.Destination -Parent
    if (-not (Test-Path -LiteralPath $destinationDir)) {
        New-Item -ItemType Directory -Path $destinationDir -Force | Out-Null
    }
    Copy-Item -LiteralPath $item.Source -Destination $item.Destination -Force
    $sourceHash = (Get-FileHash -LiteralPath $item.Source -Algorithm SHA256).Hash
    $destinationHash = (Get-FileHash -LiteralPath $item.Destination -Algorithm SHA256).Hash
    if ($sourceHash -ne $destinationHash) {
        throw ("Hash mismatch after copy: " + $item.Relative)
    }
    $copied++
}

Write-Host ""
Write-Host ("Deployed {0} file(s) to {1}" -f $copied, $target) -ForegroundColor Green
if ($doBackup) {
    Write-Host ("Backed up {0} existing file(s) to {1}" -f $backedUp, $backupRoot)
}
Write-Host "Done."
exit 0
