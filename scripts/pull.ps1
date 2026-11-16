[CmdletBinding()]
param(
    [string]$WorkBuddyPath = "",
    [string]$Target = "",
    [string]$ConfigDir = "",
    [ValidateSet("All", "Core", "Plugins")]
    [string]$Scope = "All",
    [ValidateSet("Snapshot", "Project", "Custom", "PluginPrompts")]
    [string]$Layout = "Snapshot",
    [switch]$Force,
    [switch]$DryRun,
    [switch]$IncludeTemplates
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $ScriptRoot "..")).Path
$CustomRoot = Join-Path $ProjectRoot "custom"
$PullRoot = Join-Path (Join-Path $ProjectRoot "archive") "pulls"

. (Join-Path $ScriptRoot "target.ps1")
$targetProfile = Resolve-TargetProfile -Name $Target -ScriptRoot $ScriptRoot -ConfigDirOverride $ConfigDir -InstallOverride $WorkBuddyPath
if (-not $WorkBuddyPath) { $WorkBuddyPath = $targetProfile.InstallPath }
if (-not $ConfigDir) { $ConfigDir = $targetProfile.ConfigDir }

function Get-LongPath([string]$path) {
    if ($path.StartsWith('\\?\')) { return $path }
    if ($path -match '^[A-Za-z]:') { return '\\?\' + $path }
    return $path
}

function Get-FileHashLong([string]$path) {
    $stream = [IO.File]::Open((Get-LongPath $path), [IO.FileMode]::Open, [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
    try {
        $sha = [Security.Cryptography.SHA256]::Create()
        try { return ([BitConverter]::ToString($sha.ComputeHash($stream))).Replace('-', '') }
        finally { $sha.Dispose() }
    } finally { $stream.Dispose() }
}

function Test-FileLong([string]$path) {
    return [IO.File]::Exists((Get-LongPath $path))
}

function Copy-FileLong([string]$source, [string]$dest) {
    $parent = [IO.Path]::GetDirectoryName($dest)
    [IO.Directory]::CreateDirectory((Get-LongPath $parent)) | Out-Null
    [IO.File]::Copy((Get-LongPath $source), (Get-LongPath $dest), $true)
}

function Resolve-WorkBuddyPath([string]$explicitPath) {
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($explicitPath) { $candidates.Add($explicitPath) }
    if ($env:ProgramFiles) { $candidates.Add((Join-Path $env:ProgramFiles "WorkBuddy")) }
    if (${env:ProgramFiles(x86)}) { $candidates.Add((Join-Path ${env:ProgramFiles(x86)} "WorkBuddy")) }
    if ($env:LOCALAPPDATA) { $candidates.Add((Join-Path $env:LOCALAPPDATA "Programs\WorkBuddy")) }

    $registryRoots = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    foreach ($root in $registryRoots) {
        try {
            Get-ItemProperty -Path $root -ErrorAction SilentlyContinue |
                Where-Object { $_.DisplayName -like "*WorkBuddy*" -and $_.InstallLocation } |
                ForEach-Object { $candidates.Add($_.InstallLocation) }
        } catch { }
    }

    foreach ($candidate in $candidates) {
        if (-not $candidate) { continue }
        $template = Join-Path $candidate "resources\app.asar.unpacked\resources\templates\workbuddy-prompt.tpl"
        if (Test-Path -LiteralPath $template) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "WorkBuddy installation not found. Pass -WorkBuddyPath 'C:\Program Files\WorkBuddy'."
}

function Get-ResourceRoot([string]$wb) {
    return (Join-Path $wb "resources\app.asar.unpacked\resources")
}

function Get-CustomDest([string]$targetRelative) {
    if ($Layout -eq "PluginPrompts") {
        if ($targetRelative -like 'templates\*') {
            return (Join-Path $CustomRoot $targetRelative)
        }
        if ($targetRelative -like 'plugins\workbuddy-builtin\prompt-common\fragments\*') {
            return (Join-Path $CustomRoot ("fragments\" + [IO.Path]::GetFileName($targetRelative)))
        }
        if ($targetRelative -like 'plugins\workbuddy-builtin\welcomemode\*\prompt.tpl') {
            $mode = Split-Path (Split-Path $targetRelative -Parent) -Leaf
            return (Join-Path $CustomRoot "welcome-mode\$mode.tpl")
        }
        if ($targetRelative -like 'plugins\workbuddy-builtin\*') {
            $rel = $targetRelative.Substring(('plugins\workbuddy-builtin\').Length)
            return (Join-Path $CustomRoot "plugin-prompts\$rel")
        }
        return (Join-Path $CustomRoot $targetRelative)
    }

    # Snapshot / Project / Custom: mirror structure, flatten fragments and welcome-mode
    if ($targetRelative -like 'plugins\workbuddy-builtin\prompt-common\fragments\*') {
        return (Join-Path $DestRoot ("fragments\" + [IO.Path]::GetFileName($targetRelative)))
    }
    if ($targetRelative -like 'plugins\workbuddy-builtin\welcomemode\*\prompt.tpl') {
        $mode = Split-Path (Split-Path $targetRelative -Parent) -Leaf
        return (Join-Path $DestRoot "welcome-mode\$mode.tpl")
    }
    return (Join-Path $DestRoot $targetRelative)
}

$entries = @()

function Copy-PromptFile([string]$source, [string]$dest) {
    $sourceHash = Get-FileHashLong $source
    $status = "copied"

    if (Test-FileLong $dest) {
        $destHash = Get-FileHashLong $dest
        if ($destHash -eq $sourceHash) {
            $status = "skipped-identical"
        } elseif (-not $Force) {
            $status = "skipped-existing"
        } else {
            $status = "overwritten"
        }
    }

    if ($status -in @("copied", "overwritten")) {
        if ($DryRun) {
            $status = if ($status -eq "overwritten") { "would-overwrite" } else { "would-copy" }
        } else {
            Copy-FileLong $source $dest
        }
    }

    return [pscustomobject]@{
        source = $source
        dest = $dest
        rel = $dest.Substring($DestRoot.Length + 1)
        sha256 = $sourceHash
        status = $status
    }
}

$wb = Resolve-WorkBuddyPath $WorkBuddyPath
$resourceRoot = Get-ResourceRoot $wb
$versionFile = Join-Path $wb "version"
$version = if (Test-Path -LiteralPath $versionFile) {
    (Get-Content -LiteralPath $versionFile -Raw).Trim()
} else {
    "unknown"
}

if ($Layout -eq "Snapshot") {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $prefix = if ($Target) { "$Target-" } else { "" }
    $DestRoot = Join-Path $PullRoot "$prefix$stamp"
    $suffix = 2
    while (Test-Path -LiteralPath $DestRoot) {
        $DestRoot = Join-Path $PullRoot "$prefix$stamp-$suffix"
        $suffix++
    }
    if (-not $DryRun) {
        [IO.Directory]::CreateDirectory((Get-LongPath $DestRoot)) | Out-Null
    }
} elseif ($Layout -eq "Project") {
    $DestRoot = (Resolve-Path -LiteralPath $ProjectRoot).Path
} else {
    $DestRoot = (Resolve-Path -LiteralPath $CustomRoot -ErrorAction SilentlyContinue).Path
    if (-not $DestRoot) { $DestRoot = $CustomRoot }
}

Write-Host ("Target:    {0} ({1})" -f $targetProfile.Name, $targetProfile.Label)
Write-Host "WorkBuddy: $wb"
Write-Host "Version:   $version"
Write-Host "Project:   $ProjectRoot"
Write-Host "Dest:      $DestRoot"
Write-Host "Scope:     $Scope"
Write-Host "Layout:    $Layout"
if ($DryRun) { Write-Host "Mode:      DryRun" }
Write-Host ""

if ($Scope -in @("All", "Core")) {
    # templates\*.tpl 不进入模型请求，默认不拉取（避免重新长出诱饵文件）
    $templatesDir = Join-Path $resourceRoot "templates"
    if ($IncludeTemplates -and (Test-Path -LiteralPath $templatesDir)) {
        foreach ($file in @(Get-ChildItem -LiteralPath $templatesDir -File -Filter "*.tpl" -ErrorAction SilentlyContinue)) {
            $targetRelative = "templates\$($file.Name)"
            $entries += Copy-PromptFile $file.FullName (Get-CustomDest $targetRelative)
        }
    }

    $fragmentsDir = Join-Path $resourceRoot "plugins\workbuddy-builtin\prompt-common\fragments"
    if (Test-Path -LiteralPath $fragmentsDir) {
        foreach ($file in @(Get-ChildItem -LiteralPath $fragmentsDir -File -Filter "*.md" -ErrorAction SilentlyContinue)) {
            $targetRelative = "plugins\workbuddy-builtin\prompt-common\fragments\$($file.Name)"
            $entries += Copy-PromptFile $file.FullName (Get-CustomDest $targetRelative)
        }
    }

    $welcomeRoot = Join-Path $resourceRoot "plugins\workbuddy-builtin\welcomemode"
    if (Test-Path -LiteralPath $welcomeRoot) {
        foreach ($dir in @(Get-ChildItem -LiteralPath $welcomeRoot -Directory -ErrorAction SilentlyContinue)) {
            $prompt = Join-Path $dir.FullName "prompt.tpl"
            if (Test-Path -LiteralPath $prompt) {
                $targetRelative = "plugins\workbuddy-builtin\welcomemode\$($dir.Name)\prompt.tpl"
                $entries += Copy-PromptFile $prompt (Get-CustomDest $targetRelative)
            }
        }
    }

    $interactionRoot = Join-Path $resourceRoot "plugins\workbuddy-builtin\interactionmode"
    if (Test-Path -LiteralPath $interactionRoot) {
        foreach ($modeDir in @(Get-ChildItem -LiteralPath $interactionRoot -Directory -ErrorAction SilentlyContinue)) {
            $fragDir = Join-Path $modeDir.FullName "fragments"
            if (-not (Test-Path -LiteralPath $fragDir)) { continue }
            foreach ($file in @(Get-ChildItem -LiteralPath $fragDir -File -Filter "*.md" -ErrorAction SilentlyContinue)) {
                $targetRelative = "plugins\workbuddy-builtin\interactionmode\$($modeDir.Name)\fragments\$($file.Name)"
                $entries += Copy-PromptFile $file.FullName (Get-CustomDest $targetRelative)
            }
        }
    }
}

if ($Scope -in @("All", "Plugins")) {
    $builtinRoot = Join-Path $resourceRoot "plugins\workbuddy-builtin\builtin-plugins"
    if (Test-Path -LiteralPath $builtinRoot) {
        foreach ($file in @(Get-ChildItem -LiteralPath $builtinRoot -File -Recurse -ErrorAction SilentlyContinue)) {
            if ($file.Extension -notin '.md', '.txt', '.tpl') { continue }
            $rel = $file.FullName.Substring($builtinRoot.Length + 1)
            $isPrompt =
                $rel -like '*\agents\*' -or
                $rel -like '*\prompt\*' -or
                $rel -like '*\prompts\*' -or
                $rel -like '*\commands\*' -or
                $rel -like '*\core\engines\*' -or
                $file.Name -eq 'SKILL.md'
            if (-not $isPrompt) { continue }
            $targetRelative = "plugins\workbuddy-builtin\builtin-plugins\$rel"
            $entries += Copy-PromptFile $file.FullName (Get-CustomDest $targetRelative)
        }
    }

    $skillsRoot = Join-Path $resourceRoot "plugins\workbuddy-builtin\skills"
    if (Test-Path -LiteralPath $skillsRoot) {
        foreach ($file in @(Get-ChildItem -LiteralPath $skillsRoot -File -Recurse -Filter "SKILL.md" -ErrorAction SilentlyContinue)) {
            $rel = $file.FullName.Substring($skillsRoot.Length + 1)
            $targetRelative = "plugins\workbuddy-builtin\skills\$rel"
            $entries += Copy-PromptFile $file.FullName (Get-CustomDest $targetRelative)
        }
    }
}

$copied = @($entries | Where-Object { $_.status -in @("copied", "overwritten", "would-copy", "would-overwrite") })
$skipped = @($entries | Where-Object { $_.status -like "skipped-*" })

Write-Host ("Pulled {0}, skipped {1}." -f $copied.Count, $skipped.Count)
if ($skipped.Count -gt 0) {
    $skipped | Group-Object status | ForEach-Object {
        Write-Host ("  {0}: {1}" -f $_.Name, $_.Count)
    }
}

if (-not $DryRun) {
    $manifestPath = if ($Layout -eq "Snapshot") {
        Join-Path $DestRoot "manifest.json"
    } else {
        Join-Path $CustomRoot "PULL-MANIFEST.json"
    }
    $manifest = [pscustomobject]@{
        pulledAt = (Get-Date -Format o)
        workBuddyPath = $wb
        version = $version
        scope = $Scope
        layout = $Layout
        destRoot = $DestRoot
        force = [bool]$Force
        entries = $entries
    }
    $manifest | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $manifestPath -Encoding UTF8
    Write-Host "Manifest: $manifestPath"
}

Write-Host ""
if ($Layout -eq "Snapshot") {
    Write-Host "Snapshot: $DestRoot"
    Write-Host "Next: copy or edit files from this snapshot, then run scripts\deploy.cmd (project root is the apply source)."
} else {
    Write-Host "Next: edit files under the layout root, then run scripts\deploy.cmd."
}
