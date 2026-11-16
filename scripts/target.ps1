# Shared target resolver for WorkBuddy / WorkBuddy AI installations.
# Dot-source this file: . "$PSScriptRoot\target.ps1"
#
# Nothing about install paths is hard-coded. Targets are DISCOVERED from
#   install side : uninstall registry, App Paths, running processes, <drive>:\Program Files*\WorkBuddy*
#   data side    : %USERPROFILE%\.workbuddy*  (and WORKBUDDY_CONFIG_DIR)
#   pairing      : <data>\.skill-list-cache.json -> scopeKey entry
#                  "...\<install>\resources\app.asar.unpacked\resources\plugins\workbuddy-builtin\skills"
# scripts\targets.json only carries optional overrides (pin a path, overlay, label).

function Expand-TargetToken([string]$value) {
    if ([string]::IsNullOrWhiteSpace($value)) { return "" }
    $v = $value.Trim().Trim('"')
    $v = [Environment]::ExpandEnvironmentVariables($v)
    if ($v -eq '~') { $v = $env:USERPROFILE }
    if ($v.StartsWith('~\') -or $v.StartsWith('~/')) { $v = Join-Path $env:USERPROFILE $v.Substring(2) }
    return $v
}

function Get-TargetConfigPath([string]$scriptRoot) {
    return (Join-Path $scriptRoot "targets.json")
}

function Get-TargetConfig([string]$scriptRoot) {
    $path = Get-TargetConfigPath $scriptRoot
    if (-not (Test-Path -LiteralPath $path)) {
        return [pscustomobject]@{ default = ""; autodetect = $true; targets = [pscustomobject]@{} }
    }
    return (Get-Content -LiteralPath $path -Raw -Encoding UTF8 | ConvertFrom-Json)
}

function Test-TargetInstallRoot([string]$path, [string]$exeName) {
    if ([string]::IsNullOrWhiteSpace($path)) { return $false }
    if ([IO.Directory]::Exists($path)) {
        if ([IO.Directory]::Exists((Join-Path $path "resources\app.asar.unpacked\resources"))) { return $true }
        if ([IO.File]::Exists((Join-Path $path "resources\app.asar"))) { return $true }
    }
    if ($exeName -and [IO.File]::Exists((Join-Path $path $exeName))) { return $true }
    return $false
}

function Get-TargetId([string]$installPath, [string]$exeName) {
    $leaf = if ($installPath) { Split-Path -Leaf $installPath } else { "" }
    if (-not $leaf -and $exeName) { $leaf = [IO.Path]::GetFileNameWithoutExtension($exeName) }
    $id = ($leaf -replace '[^A-Za-z0-9]+', '-').Trim('-').ToLowerInvariant()
    if (-not $id) { $id = "workbuddy" }
    if ($id -match '^(workbuddy)(ai|cn)$') { $id = "$($Matches[1])-$($Matches[2])" }
    return $id
}

function Get-TargetExeName([string]$installPath) {
    if (-not [IO.Directory]::Exists($installPath)) { return "" }
    foreach ($name in @("WorkBuddyAI.exe", "WorkBuddy.exe")) {
        if ([IO.File]::Exists((Join-Path $installPath $name))) { return $name }
    }
    return ""
}

function Get-InstallCandidates {
    $candidates = New-Object System.Collections.Generic.List[string]

    foreach ($key in @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\WorkBuddy.exe",
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\WorkBuddyAI.exe",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\WorkBuddy.exe",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\WorkBuddyAI.exe"
    )) {
        try {
            $item = Get-ItemProperty -Path $key -ErrorAction SilentlyContinue
            if ($item) {
                $exe = $item.'(default)'; if (-not $exe) { $exe = $item.Path }
                if ($exe) { $candidates.Add((Split-Path ($exe.Trim('"')) -Parent)) }
            }
        } catch { }
    }

    foreach ($root in @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )) {
        try {
            Get-ItemProperty -Path $root -ErrorAction SilentlyContinue |
                Where-Object { $_.DisplayName -like "*WorkBuddy*" } |
                ForEach-Object {
                    if ($_.InstallLocation) { $candidates.Add($_.InstallLocation.Trim('"')) }
                    if ($_.DisplayIcon) {
                        $icon = ($_.DisplayIcon -replace ',\d+$', '').Trim('"')
                        if ($icon -like '*.exe') { $candidates.Add((Split-Path $icon -Parent)) }
                    }
                    if ($_.UninstallString) {
                        $u = $_.UninstallString.Trim()
                        if ($u.StartsWith('"')) {
                            $end = $u.IndexOf('"', 1); if ($end -gt 0) { $u = $u.Substring(1, $end - 1) }
                        } else { $u = ($u -split ' ')[0] }
                        if ($u -like '*.exe') { $candidates.Add((Split-Path $u -Parent)) }
                    }
                }
        } catch { }
    }

    try {
        Get-Process -ErrorAction SilentlyContinue |
            Where-Object { $_.ProcessName -like "WorkBuddy*" -and $_.Path } |
            ForEach-Object { $candidates.Add((Split-Path $_.Path -Parent)) }
    } catch { }

    try {
        Get-Command "WorkBuddy.exe", "WorkBuddyAI.exe" -ErrorAction SilentlyContinue |
            ForEach-Object { $candidates.Add((Split-Path $_.Source -Parent)) }
    } catch { }

    # Program Files on every fixed drive, plus sibling folders such as "WorkBuddy Beta"
    $drives = @()
    try {
        $drives = @([IO.DriveInfo]::GetDrives() | Where-Object { $_.IsReady -and $_.DriveType -eq [IO.DriveType]::Fixed } | ForEach-Object { $_.RootDirectory.FullName })
    } catch { }
    foreach ($drive in $drives) {
        foreach ($pf in @("Program Files", "Program Files (x86)")) {
            $base = Join-Path $drive $pf
            if (-not [IO.Directory]::Exists($base)) { continue }
            foreach ($n in @("WorkBuddy", "WorkBuddyAI", "WorkBuddy AI")) {
                $candidates.Add((Join-Path $base $n))
            }
            try {
                Get-ChildItem -LiteralPath $base -Directory -Filter "WorkBuddy*" -ErrorAction SilentlyContinue |
                    ForEach-Object { $candidates.Add($_.FullName) }
            } catch { }
        }
    }

    $seen = @{}
    $result = @()
    foreach ($c in $candidates) {
        if ([string]::IsNullOrWhiteSpace($c)) { continue }
        $full = [IO.Path]::GetFullPath($c.Trim('"'))
        if ($seen.ContainsKey($full.ToLowerInvariant())) { continue }
        $seen[$full.ToLowerInvariant()] = $true
        $exe = Get-TargetExeName $full
        if (Test-TargetInstallRoot $full $exe) {
            $result += [pscustomobject]@{ InstallPath = $full; ExeName = $exe }
        }
    }
    return $result
}

function Get-DataDirCandidates {
    $dirs = New-Object System.Collections.Generic.List[string]
    if ($env:WORKBUDDY_CONFIG_DIR) { $dirs.Add((Expand-TargetToken $env:WORKBUDDY_CONFIG_DIR)) }
    if ($env:USERPROFILE) {
        try {
            Get-ChildItem -LiteralPath $env:USERPROFILE -Directory -Filter ".workbuddy*" -Force -ErrorAction SilentlyContinue |
                ForEach-Object { $dirs.Add($_.FullName) }
        } catch { }
    }
    return @($dirs | Where-Object { $_ -and [IO.Directory]::Exists($_) } | Select-Object -Unique)
}

function Get-InstallPathFromDataDir([string]$configDir) {
    $cacheFile = Join-Path $configDir ".skill-list-cache.json"
    if (Test-Path -LiteralPath $cacheFile) {
        try {
            $j = Get-Content -LiteralPath $cacheFile -Raw | ConvertFrom-Json
            foreach ($s in @($j.scopeKey)) {
                if ($s -is [string] -and $s -match '^(.*?)\\resources\\app\.asar\.unpacked\\resources\\plugins\\') {
                    return $Matches[1]
                }
                if ($s -is [string] -and $s -match '^(.*?)\\resources\\app\.asar\.unpacked$') {
                    return $Matches[1]
                }
            }
        } catch { }
    }
    $settings = Join-Path $configDir "settings.json"
    if (Test-Path -LiteralPath $settings) {
        try {
            $raw = Get-Content -LiteralPath $settings -Raw
            $m = [regex]::Match($raw, '([A-Za-z]:\\\\[^"]*?)\\\\resources\\\\app\.asar\.unpacked')
            if ($m.Success) { return ($m.Groups[1].Value -replace '\\\\', '\') }
        } catch { }
    }
    return ""
}

function Get-DiscoveredTargets {
    $installs = @(Get-InstallCandidates)
    $dataDirs = @(Get-DataDirCandidates)
    $result = @()

    foreach ($cfgDir in $dataDirs) {
        $install = Get-InstallPathFromDataDir $cfgDir
        if (-not $install) { continue }
        if (-not (Test-TargetInstallRoot $install "")) { continue }
        $exe = Get-TargetExeName $install
        $result += [pscustomobject]@{
            Id = (Get-TargetId $install $exe)
            Label = (Split-Path -Leaf $install)
            InstallPath = $install
            ExeName = $exe
            ConfigDir = $cfgDir
        }
    }

    # installs with no paired data dir still count
    foreach ($i in $installs) {
        if (@($result | Where-Object { $_.InstallPath -eq $i.InstallPath }).Count -gt 0) { continue }
        $id = Get-TargetId $i.InstallPath $i.ExeName
        $guess = Join-Path $env:USERPROFILE $(if ($id -eq "workbuddy-ai") { ".workbuddy-ai" } elseif ($id -eq "workbuddy") { ".workbuddy" } else { ".$id" })
        $result += [pscustomobject]@{
            Id = $id
            Label = (Split-Path -Leaf $i.InstallPath)
            InstallPath = $i.InstallPath
            ExeName = $i.ExeName
            ConfigDir = $guess
        }
    }

    return @($result | Sort-Object Id -Unique)
}

function Resolve-TargetProfile {
    param(
        [string]$Name = "",
        [string]$ScriptRoot = $PSScriptRoot,
        [string]$ConfigDirOverride = "",
        [string]$InstallOverride = "",
        [string]$CacheRootOverride = "",
        [string]$OverlayOverride = ""
    )

    $cfg = Get-TargetConfig $ScriptRoot
    $configured = @()
    if ($cfg.targets) { $configured = @($cfg.targets.PSObject.Properties | ForEach-Object { [pscustomobject]@{ Name = $_.Name; Value = $_.Value } }) }
    $discovered = @(Get-DiscoveredTargets)

    $name2 = $Name
    if (-not $name2) {
        if ($cfg.default) { $name2 = $cfg.default }
        elseif ($discovered.Count -eq 1) { $name2 = $discovered[0].Id }
    }
    if (-not $name2) {
        $ids = (@($discovered | ForEach-Object { $_.Id }) + @($configured | ForEach-Object { $_.Name }) | Select-Object -Unique) -join ', '
        throw "Multiple WorkBuddy installations found; pass -Target <id>. Detected: $ids"
    }

    $conf = $configured | Where-Object { $_.Name -eq $name2 } | Select-Object -First 1
    $disc = $discovered | Where-Object { $_.Id -eq $name2 } | Select-Object -First 1
    if (-not $conf -and -not $disc) {
        $ids = (@($discovered | ForEach-Object { $_.Id }) + @($configured | ForEach-Object { $_.Name }) | Select-Object -Unique) -join ', '
        throw "Unknown target '$name2'. Detected: $ids"
    }

    $c = if ($conf) { $conf.Value } else { $null }
    $install = if ($InstallOverride) { Expand-TargetToken $InstallOverride }
               elseif ($c -and $c.installPath) { Expand-TargetToken $c.installPath }
               elseif ($disc) { $disc.InstallPath }
               else { "" }
    $exeName = if ($c -and $c.exeName) { $c.exeName }
               elseif ($disc) { $disc.ExeName }
               else { "WorkBuddy.exe" }
    if ($install -and -not (Test-TargetInstallRoot $install $exeName)) { $install = "" }
    if (-not $install -and $disc) { $install = $disc.InstallPath; if (-not $exeName -and $disc.ExeName) { $exeName = $disc.ExeName } }

    $configDir = if ($ConfigDirOverride) { Expand-TargetToken $ConfigDirOverride }
                 elseif ($c -and $c.configDir) { Expand-TargetToken $c.configDir }
                 elseif ($disc) { $disc.ConfigDir }
                 else { "" }
    if (-not $configDir) {
        $guess = Join-Path $env:USERPROFILE ".$name2"
        if ([IO.Directory]::Exists($guess)) { $configDir = $guess }
    }
    if (-not $configDir) { $configDir = Join-Path $env:USERPROFILE ".workbuddy" }

    $cacheRoot = if ($CacheRootOverride) { Expand-TargetToken $CacheRootOverride }
                 else { Join-Path $configDir "plugins\cache\workbuddy-builtin" }

    $overlay = if ($OverlayOverride) { Expand-TargetToken $OverlayOverride }
               elseif ($c -and $c.overlay) { Expand-TargetToken $c.overlay }
               else { "" }
    if ($overlay -and -not [IO.Path]::IsPathRooted($overlay)) { $overlay = Join-Path (Split-Path $ScriptRoot -Parent) $overlay }

    return [pscustomobject]@{
        Name = $name2
        Label = if ($c -and $c.label) { $c.label } elseif ($disc) { $disc.Label } else { $name2 }
        InstallPath = $install
        ExeName = $exeName
        ConfigDir = $configDir
        CacheRoot = $cacheRoot
        Overlay = $overlay
        Installed = [bool]($install -and (Test-TargetInstallRoot $install $exeName))
        ConfigExists = [IO.Directory]::Exists($configDir)
    }
}

function Get-TargetNames([string]$ScriptRoot = $PSScriptRoot) {
    $cfg = Get-TargetConfig $ScriptRoot
    $ids = @()
    if ($cfg.targets) { $ids += @($cfg.targets.PSObject.Properties.Name) }
    $ids += @(Get-DiscoveredTargets | ForEach-Object { $_.Id })
    return @($ids | Select-Object -Unique)
}
