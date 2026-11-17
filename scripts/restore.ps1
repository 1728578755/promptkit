[CmdletBinding()]
param(
    [string]$WorkBuddyPath = "",
    [string]$Template = "",
    [string]$BackupPath = "",
    [string]$PluginCacheRoot = "",
    [string]$Target = "",
    [string]$ConfigDir = "",
    [switch]$All,
    [switch]$Original,
    [switch]$List,
    [switch]$DryRun,
    [switch]$NoElevate,
    [string]$UserSkillsRoot = ""
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$BackupRoot = Join-Path $ScriptRoot "..\backups"

. (Join-Path $ScriptRoot "target.ps1")
$targetProfile = Resolve-TargetProfile -Name $Target -ScriptRoot $ScriptRoot -ConfigDirOverride $ConfigDir -InstallOverride $WorkBuddyPath -CacheRootOverride $PluginCacheRoot
if (-not $WorkBuddyPath) { $WorkBuddyPath = $targetProfile.InstallPath }
if (-not $ConfigDir) { $ConfigDir = $targetProfile.ConfigDir }
if (-not $PluginCacheRoot -and $targetProfile.CacheRoot) { $PluginCacheRoot = $targetProfile.CacheRoot }

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

function Test-FileLong([string]$path) { return [IO.File]::Exists((Get-LongPath $path)) }
function Test-DirLong([string]$path) { return [IO.Directory]::Exists((Get-LongPath $path)) }

function Copy-FileLong([string]$source, [string]$dest) {
    $parent = [IO.Path]::GetDirectoryName($dest)
    [IO.Directory]::CreateDirectory((Get-LongPath $parent)) | Out-Null
    [IO.File]::Copy((Get-LongPath $source), (Get-LongPath $dest), $true)
}

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
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

function Get-PluginCacheRoot([string]$explicitRoot) {
    if ($explicitRoot) { return [IO.Path]::GetFullPath($explicitRoot) }
    if ($ConfigDir) { return [IO.Path]::GetFullPath((Join-Path $ConfigDir "plugins\cache\workbuddy-builtin")) }
    $configDir = $env:WORKBUDDY_CONFIG_DIR
    if (-not $configDir) {
        if (-not $env:USERPROFILE) { return "" }
        # 不写死 .workbuddy：按这套安装反查配对的数据目录
        $configDir = Get-DefaultConfigDir -InstallPath $WorkBuddyPath -Id $targetProfile.Name
    }
    return [IO.Path]::GetFullPath((Join-Path $configDir "plugins\cache\workbuddy-builtin"))
}

function Normalize-RelativePath([string]$path) {
    return (($path -replace '/', '\').TrimStart('\'))
}

function Resolve-TargetRelative([string]$value) {
    $p = Normalize-RelativePath $value
    if ($p -match '^[A-Za-z]:') {
        throw "Target must be relative to the resources directory, not an absolute path: $value"
    }
    if ($p -like 'plugin-prompts\*') {
        $p = $p.Substring(('plugin-prompts\').Length)
    }
    if ($p -like '@user-skill\*') { return $p }
    if ($p -like 'user-skills\*') { return "@user-skill\" + $p.Substring(('user-skills\').Length) }
    if ($p -like 'templates\*' -or $p -like 'plugins\*') { return $p }
    if ($p -like 'builtin-plugins\*' -or $p -like 'skills\*' -or $p -like 'prompt-common\*' -or $p -like 'welcomemode\*' -or $p -like 'interactionmode\*') {
        return "plugins\workbuddy-builtin\$p"
    }
    if ($p -like '*.tpl') { return "templates\$p" }
    if ($p -like '*.md') { return "plugins\workbuddy-builtin\prompt-common\fragments\$p" }
    throw "Cannot infer target path from '$value'."
}

function Get-BackupEntries([string]$directory) {
    $entries = @()
    $manifestPath = Join-Path $directory "manifest.json"
    if (Test-Path -LiteralPath $manifestPath) {
        $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        $filesRoot = Join-Path $directory "files"
        $cacheFilesRoot = Join-Path $directory "files-cache"
        foreach ($entry in @($manifest.entries)) {
            $target = Normalize-RelativePath ([string]$entry.target)
            $file = Join-Path $filesRoot $target
            $existed = $true
            if ($null -ne $entry.targetExisted) { $existed = [bool]$entry.targetExisted }

            $cacheTargets = @()
            foreach ($ct in @($entry.cacheTargets)) {
                if ($null -eq $ct) { continue }
                $rel = Normalize-RelativePath ([string]$ct.path)
                if (-not $rel) { continue }
                $cacheBackup = Join-Path $cacheFilesRoot $rel
                $cacheTargets += [pscustomobject]@{
                    Relative = $rel
                    Existed = [bool]$ct.existed
                    BackupFile = if (Test-FileLong $cacheBackup) { $cacheBackup } else { "" }
                    OriginalHash = [string]$ct.originalHash
                    NewHash = [string]$ct.newHash
                }
            }

            $hasInstallBackup = Test-FileLong $file
            if ($hasInstallBackup -or $existed -eq $false -or $cacheTargets.Count -gt 0) {
                $entries += [pscustomobject]@{
                    Target = $target
                    BackupFile = if ($hasInstallBackup) { $file } else { "" }
                    Existed = $existed
                    CacheTargets = $cacheTargets
                }
            }
        }
        $userFilesRoot = Join-Path $directory "files-user-skills"
        foreach ($us in @($manifest.userSkills)) {
            if ($null -eq $us) { continue }
            $rel = Normalize-RelativePath ([string]$us.relative)
            if (-not $rel) { continue }
            $backup = Join-Path $userFilesRoot $rel
            $existed = $true
            if ($null -ne $us.targetExisted) { $existed = [bool]$us.targetExisted }
            $entries += [pscustomobject]@{
                Target = "@user-skill\$rel"
                BackupFile = if (Test-FileLong $backup) { $backup } else { "" }
                Existed = $existed
                CacheTargets = @()
            }
        }
        return $entries
    }

    $flat = Join-Path $directory "workbuddy-prompt.tpl"
    if (Test-Path -LiteralPath $flat) {
        $entries += [pscustomobject]@{ Target = "templates\workbuddy-prompt.tpl"; BackupFile = $flat; Existed = $true; CacheTargets = @() }
    }
    foreach ($file in @(Get-ChildItem -LiteralPath $directory -File -Filter "workbuddy-*.tpl" -ErrorAction SilentlyContinue)) {
        if ($file.Name -eq "workbuddy-prompt.tpl") { continue }
        $entries += [pscustomobject]@{ Target = "templates\$($file.Name)"; BackupFile = $file.FullName; Existed = $true; CacheTargets = @() }
    }

    $templatesDir = Join-Path $directory "templates"
    if (Test-Path -LiteralPath $templatesDir) {
        foreach ($file in @(Get-ChildItem -LiteralPath $templatesDir -File -Filter "*.tpl" -ErrorAction SilentlyContinue)) {
            $entries += [pscustomobject]@{ Target = "templates\$($file.Name)"; BackupFile = $file.FullName; Existed = $true; CacheTargets = @() }
        }
    }

    $fragmentsDir = Join-Path $directory "fragments"
    if (Test-Path -LiteralPath $fragmentsDir) {
        foreach ($file in @(Get-ChildItem -LiteralPath $fragmentsDir -File -Filter "*.md" -ErrorAction SilentlyContinue)) {
            $entries += [pscustomobject]@{
                Target = "plugins\workbuddy-builtin\prompt-common\fragments\$($file.Name)"
                BackupFile = $file.FullName
                Existed = $true
                CacheTargets = @()
            }
        }
    }

    $welcomeDir = Join-Path $directory "welcome-mode"
    if (Test-Path -LiteralPath $welcomeDir) {
        foreach ($file in @(Get-ChildItem -LiteralPath $welcomeDir -File -Filter "*.tpl" -ErrorAction SilentlyContinue)) {
            $mode = [IO.Path]::GetFileNameWithoutExtension($file.Name)
            $entries += [pscustomobject]@{
                Target = "plugins\workbuddy-builtin\welcomemode\$mode\prompt.tpl"
                BackupFile = $file.FullName
                Existed = $true
                CacheTargets = @()
            }
        }
    }

    $pluginPromptsDir = Join-Path $directory "plugin-prompts"
    if (Test-Path -LiteralPath $pluginPromptsDir) {
        $pluginBase = (Resolve-Path -LiteralPath $pluginPromptsDir).Path
        foreach ($file in @(Get-ChildItem -LiteralPath $pluginPromptsDir -File -Recurse -ErrorAction SilentlyContinue)) {
            $rel = Normalize-RelativePath $file.FullName.Substring($pluginBase.Length + 1)
            $target = if ($rel -like 'templates\*' -or $rel -like 'plugins\*') {
                $rel
            } else {
                "plugins\workbuddy-builtin\$rel"
            }
            $entries += [pscustomobject]@{ Target = $target; BackupFile = $file.FullName; Existed = $true; CacheTargets = @() }
        }
    }

    $pluginsDir = Join-Path $directory "plugins"
    if (Test-Path -LiteralPath $pluginsDir) {
        $pluginsBase = (Resolve-Path -LiteralPath $pluginsDir).Path
        foreach ($file in @(Get-ChildItem -LiteralPath $pluginsDir -File -Recurse -ErrorAction SilentlyContinue)) {
            $rel = Normalize-RelativePath $file.FullName.Substring($pluginsBase.Length + 1)
            $entries += [pscustomobject]@{ Target = "plugins\$rel"; BackupFile = $file.FullName; Existed = $true; CacheTargets = @() }
        }
    }

    $filesRoot = Join-Path $directory "files"
    if (Test-Path -LiteralPath $filesRoot) {
        foreach ($file in @(Get-ChildItem -LiteralPath $filesRoot -File -Recurse -ErrorAction SilentlyContinue)) {
            $target = Normalize-RelativePath $file.FullName.Substring((Resolve-Path -LiteralPath $filesRoot).Path.Length + 1)
            $entries += [pscustomobject]@{ Target = $target; BackupFile = $file.FullName; Existed = $true; CacheTargets = @() }
        }
    }

    $userFilesRootPlain = Join-Path $directory "files-user-skills"
    if (Test-Path -LiteralPath $userFilesRootPlain) {
        $ub = (Resolve-Path -LiteralPath $userFilesRootPlain).Path
        foreach ($file in @(Get-ChildItem -LiteralPath $ub -File -Recurse -ErrorAction SilentlyContinue)) {
            $rel = Normalize-RelativePath $file.FullName.Substring($ub.Length + 1)
            $entries += [pscustomobject]@{ Target = "@user-skill\$rel"; BackupFile = $file.FullName; Existed = $true; CacheTargets = @() }
        }
    }

    return $entries
}

function Get-BackupVersion([string]$directory) {
    $manifestPath = Join-Path $directory "manifest.json"
    if (Test-Path -LiteralPath $manifestPath) {
        try {
            $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
            if ($manifest.version) { return [string]$manifest.version }
        } catch { }
    }
    $infoPath = Join-Path $directory "BACKUP_INFO.txt"
    if (Test-Path -LiteralPath $infoPath) {
        $line = Select-String -LiteralPath $infoPath -Pattern '^(Version=|WorkBuddy version:)' | Select-Object -First 1
        if ($line) { return $line.Line.Trim() }
    }
    return ""
}

if ($All -and $Template) {
    throw "-All and -Template cannot be used together."
}

if ($Original -and $BackupPath) {
    throw "-Original cannot be combined with -BackupPath. Choose a specific backup explicitly instead."
}

if (-not $All -and -not $Template -and -not $BackupPath) {
    $All = $true
}

function Get-BackupTarget([string]$directory) {
    $manifest = Join-Path $directory "manifest.json"
    if (-not (Test-Path -LiteralPath $manifest)) { return "" }
    try {
        $j = Get-Content -LiteralPath $manifest -Raw | ConvertFrom-Json
        if ($j.target) { return [string]$j.target }
    } catch { }
    return ""
}

function Test-BackupTargetMatch([string]$directory) {
    if (-not $Target) { return $true }
    $t = Get-BackupTarget $directory
    if (-not $t) { return $true }   # legacy backups have no target field
    return ($t -eq $Target)
}

if ($List) {
    if (-not (Test-Path -LiteralPath $BackupRoot)) {
        Write-Host "No backup directory found: $BackupRoot"
        exit 0
    }
    Get-ChildItem -LiteralPath $BackupRoot -Directory |
        Sort-Object Name -Descending |
        ForEach-Object {
            $entries = @(Get-BackupEntries $_.FullName)
            if ($entries.Count -eq 0) { return }
            $version = Get-BackupVersion $_.FullName
            $cacheCount = 0
            foreach ($e in $entries) { $cacheCount += @($e.CacheTargets).Count }
            $sample = ($entries | Select-Object -First 3 | ForEach-Object { $_.Target }) -join ", "
            if ($entries.Count -gt 3) { $sample += ", ..." }
            $tName = Get-BackupTarget $_.FullName
            $tLabel = if ($tName) { $tName } else { "legacy" }
            Write-Host ("{0}  files={1}  cache={2}  {3}  target={4}" -f $_.Name, $entries.Count, $cacheCount, $version, $tLabel)
            Write-Host ("    {0}" -f $sample)
        }
    exit 0
}

$wb = Resolve-WorkBuddyPath $WorkBuddyPath
$resourceRoot = Get-ResourceRoot $wb
$cacheRoot = Get-PluginCacheRoot $PluginCacheRoot
$userSkillsRoot = if ($UserSkillsRoot) { [IO.Path]::GetFullPath($UserSkillsRoot) } else { [IO.Path]::Combine($ConfigDir, "skills") }

$targetFilter = ""
if ($Template) { $targetFilter = Resolve-TargetRelative $Template }

$backupDirs = @(Get-ChildItem -LiteralPath $BackupRoot -Directory -ErrorAction SilentlyContinue)
if ($Original) {
    $backupDirs = @($backupDirs | Sort-Object Name)
} else {
    $backupDirs = @($backupDirs | Sort-Object Name -Descending)
}

$selectedEntries = @()
$selectedBackup = ""

if ($BackupPath) {
    if (-not (Test-Path -LiteralPath $BackupPath)) {
        throw "Backup path not found: $BackupPath"
    }
    $resolved = (Resolve-Path -LiteralPath $BackupPath).Path
    if (Test-Path -LiteralPath $resolved -PathType Leaf) {
        $inferred = ""
        if ($resolved -match '(?i)\\templates\\(.+)$') {
            $inferred = "templates\" + $Matches[1]
        } elseif ($resolved -match '(?i)\\fragments\\([^\\]+)$') {
            $inferred = "plugins\workbuddy-builtin\prompt-common\fragments\" + $Matches[1]
        } elseif ($resolved -match '(?i)\\welcome-mode\\([^\\]+)\.tpl$') {
            $inferred = "plugins\workbuddy-builtin\welcomemode\" + $Matches[1] + "\prompt.tpl"
        } elseif ([IO.Path]::GetFileName($resolved) -eq "workbuddy-prompt.tpl") {
            $inferred = "templates\workbuddy-prompt.tpl"
        }
        if ($targetFilter -and $inferred -and $targetFilter -ne $inferred) {
            throw "Backup file '$resolved' does not match target '$targetFilter'."
        }
        if (-not $targetFilter) {
            if (-not $inferred) { throw "Cannot infer target from backup file: $resolved. Pass -Template." }
            $targetFilter = $inferred
        }
        $selectedEntries = @([pscustomobject]@{ Target = $targetFilter; BackupFile = $resolved; Existed = $true; CacheTargets = @() })
        $selectedBackup = Split-Path -Parent $resolved
    } else {
        $selectedBackup = $resolved
        $entries = @(Get-BackupEntries $selectedBackup)
        if ($entries.Count -eq 0) { throw "No restorable files found in backup: $selectedBackup" }
        if ($targetFilter) {
            $selectedEntries = @($entries | Where-Object { $_.Target -eq $targetFilter })
        } else {
            $selectedEntries = @($entries)
        }
    }
} elseif ($targetFilter) {
    $found = $false
    foreach ($dir in $backupDirs) {
        if (-not (Test-BackupTargetMatch $dir.FullName)) { continue }
        $entries = @(Get-BackupEntries $dir.FullName)
        $match = @($entries | Where-Object { $_.Target -eq $targetFilter })
        if ($match.Count -gt 0) {
            $selectedEntries = $match
            $selectedBackup = $dir.FullName
            $found = $true
            break
        }
    }
    if (-not $found) { throw "No backup found for target: $targetFilter" }
} else {
    $selectedBackup = ""
    if ($Original) {
        $seen = @{}
        foreach ($dir in $backupDirs) {
            if (-not (Test-BackupTargetMatch $dir.FullName)) { continue }
            foreach ($entry in @(Get-BackupEntries $dir.FullName)) {
                if (-not $seen.ContainsKey($entry.Target)) {
                    $seen[$entry.Target] = $entry
                }
            }
        }
        if ($seen.Count -eq 0) { throw "No backups found in $BackupRoot" }
        $selectedEntries = @($seen.Values)
    } else {
        foreach ($dir in $backupDirs) {
            if (-not (Test-BackupTargetMatch $dir.FullName)) { continue }
            $entries = @(Get-BackupEntries $dir.FullName)
            if ($entries.Count -gt 0) {
                $selectedEntries = $entries
                $selectedBackup = $dir.FullName
                break
            }
        }
        if ($selectedEntries.Count -eq 0) { throw "No backups found in $BackupRoot" }
    }
}

$prepared = @()
foreach ($entry in $selectedEntries) {
    if ($entry.Target -like '@user-skill\*') {
        $userRel = $entry.Target.Substring('@user-skill\'.Length)
        $targetPath = [IO.Path]::GetFullPath((Join-Path $userSkillsRoot $userRel))
        $userRootFull = [IO.Path]::GetFullPath($userSkillsRoot)
        if (-not $targetPath.StartsWith($userRootFull, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Target escapes the user skills directory: $($entry.Target)"
        }
    } else {
        $targetPath = [IO.Path]::GetFullPath((Join-Path $resourceRoot $entry.Target))
        $rootFull = [IO.Path]::GetFullPath($resourceRoot)
        if (-not $targetPath.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Target escapes the resources directory: $($entry.Target)"
        }
    }

    $backupFile = if ($entry.BackupFile) { [IO.Path]::GetFullPath($entry.BackupFile) } else { "" }
    $cacheRestores = @()
    foreach ($ct in @($entry.CacheTargets)) {
        if (-not $cacheRoot) { continue }
        $cacheRestores += [pscustomobject]@{
            Path = [IO.Path]::GetFullPath((Join-Path $cacheRoot $ct.Relative))
            Relative = $ct.Relative
            Existed = $ct.Existed
            BackupFile = if ($ct.BackupFile) { [IO.Path]::GetFullPath($ct.BackupFile) } else { "" }
            OriginalHash = $ct.OriginalHash
            NewHash = $ct.NewHash
        }
    }

    $prepared += [pscustomobject]@{
        Target = $entry.Target
        TargetPath = $targetPath
        BackupFile = $backupFile
        Existed = $entry.Existed
        BackupHash = if ($backupFile -and (Test-FileLong $backupFile)) { Get-FileHashLong $backupFile } else { "" }
        CurrentHash = if (Test-FileLong $targetPath) {
            Get-FileHashLong $targetPath
        } else {
            "(missing)"
        }
        CacheRestores = $cacheRestores
    }
}

Write-Host "WorkBuddy:   $wb"
Write-Host "Resources:   $resourceRoot"
Write-Host "PluginCache: $cacheRoot"
Write-Host "Backup set:  $selectedBackup"
Write-Host ""

$totalCache = 0
foreach ($item in $prepared) { $totalCache += @($item.CacheRestores).Count }
Write-Host ("Entries: {0}  cache restores: {1}" -f $prepared.Count, $totalCache)
foreach ($item in $prepared) {
    $state = if ($item.BackupFile) { "restore" } else { "remove (was new)" }
    $label = if ($item.Target -like '@user-skill\*') { "[skill]  " } else { "[install]" }
    Write-Host ("  {0} {1}  {2}" -f $label, $item.Target, $state)
    foreach ($cr in @($item.CacheRestores)) {
        $cacheState = if ($cr.BackupFile) { "restore" } else { "remove (was new)" }
        Write-Host ("              -> {0}  {1}" -f $cr.Relative, $cacheState)
    }
}
Write-Host ""

if ($DryRun) {
    Write-Host "[DryRun] No files were written."
    exit 0
}

if (-not (Test-Admin) -and -not $NoElevate) {
    Write-Host "Requesting administrator privileges..."
    $argLine = "-NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    if ($WorkBuddyPath) { $argLine += " -WorkBuddyPath `"$WorkBuddyPath`"" }
    if ($Target) { $argLine += " -Target `"$Target`"" }
    if ($ConfigDir) { $argLine += " -ConfigDir `"$ConfigDir`"" }
    if ($All) { $argLine += " -All" }
    if ($Original) { $argLine += " -Original" }
    if ($Template) { $argLine += " -Template `"$Template`"" }
    if ($PluginCacheRoot) { $argLine += " -PluginCacheRoot `"$PluginCacheRoot`"" }
    if ($selectedBackup -and -not $Original) { $argLine += " -BackupPath `"$selectedBackup`"" }
    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $argLine
    exit 0
}

$restored = 0
foreach ($item in $prepared) {
    if ($item.BackupFile -and (Test-FileLong $item.BackupFile)) {
        $parent = [IO.Path]::GetDirectoryName($item.TargetPath)
        if (-not (Test-Path -LiteralPath $parent)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        Copy-FileLong $item.BackupFile $item.TargetPath
        $after = Get-FileHashLong $item.TargetPath
        if ($after -ne $item.BackupHash) {
            throw "Hash mismatch after restore: $($item.Target) target=$after expected=$($item.BackupHash)"
        }
        $restored++
    } elseif ($item.Existed -eq $false -and (Test-FileLong $item.TargetPath)) {
        $current = Get-FileHashLong $item.TargetPath
        if ($current -eq $item.CurrentHash) {
            Write-Host "  skip remove (no install copy expected): $($item.Target)"
        }
    }

    foreach ($cr in @($item.CacheRestores)) {
        if (-not (Test-DirLong ([IO.Path]::GetDirectoryName($cr.Path)))) {
            Write-Host ("  skip (missing plugin dir): {0}" -f $cr.Relative)
            continue
        }
        if ($cr.BackupFile -and (Test-FileLong $cr.BackupFile)) {
            Copy-FileLong $cr.BackupFile $cr.Path
            $after = Get-FileHashLong $cr.Path
            $expected = Get-FileHashLong $cr.BackupFile
            if ($after -ne $expected) {
                throw "Hash mismatch after cache restore: $($cr.Relative) target=$after expected=$expected"
            }
            $restored++
        } elseif ($cr.Existed -eq $false) {
            if (Test-FileLong $cr.Path) {
                $current = Get-FileHashLong $cr.Path
                if ($cr.NewHash -and $current -eq $cr.NewHash) {
                    Remove-Item -LiteralPath (Get-LongPath $cr.Path) -Force -Confirm:$false
                    Write-Host ("  removed (was created by apply): {0}" -f $cr.Relative)
                } else {
                    Write-Host ("  keep (modified since apply): {0}" -f $cr.Relative)
                }
            }
        }
    }
}

Write-Host ""
Write-Host "Restored successfully: $restored file(s)."
Write-Host "Restart WorkBuddy for the change to take effect."
