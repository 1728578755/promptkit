# Shared target resolver for WorkBuddy / WorkBuddy AI installations.
# Dot-source this file: . "$PSScriptRoot\target.ps1"
#
# Nothing about install paths is hard-coded, and nothing is found by walking the disk.
# A target is resolved from, in order:
#   explicit -WorkBuddyPath / -ConfigDir
#   targets.json pins
#   PROMPTKIT_TARGET_<ID>_INSTALL / _CONFIG  (set by deploy.ps1 for child scripts)
#   the installed-app registration: uninstall entries (DisplayIcon / UninstallString /
#     InstallLocation) and App Paths, both 64- and 32-bit views, HKLM and HKCU
#   data-dir pairing: <data>\.skill-list-cache.json -> scopeKey entry
#     "...\<install>\resources\app.asar.unpacked\resources\plugins\workbuddy-builtin\skills"
#   the executable of a running process (the path the app is actually running from)
# If none of these knows the folder, there is no guessing: pass -WorkBuddyPath.
# scripts\targets.json only carries optional overrides (pin a path, overlay, label).

# 父进程（deploy.ps1）把解析结果通过环境变量传给子脚本，子进程就不必再扫一遍盘。
function Get-TargetEnvOverride([string]$id, [string]$kind) {
    if ([string]::IsNullOrWhiteSpace($id)) { return "" }
    $key = "PROMPTKIT_TARGET_" + (($id -replace '[^A-Za-z0-9]', '_').ToUpperInvariant()) + "_" + $kind
    return [Environment]::GetEnvironmentVariable($key)
}

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
        if (Get-ProductExe $path) { return $true }
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

# 主程序按通配匹配，不写死文件名：WorkBuddy.exe / WorkBuddyAI.exe / "WorkBuddy Beta.exe" 都算。
function Get-ProductExe([string]$installPath) {
    if ([string]::IsNullOrWhiteSpace($installPath)) { return "" }
    if (-not [IO.Directory]::Exists($installPath)) { return "" }
    try {
        $hit = Get-ChildItem -LiteralPath $installPath -Filter "WorkBuddy*.exe" -File -ErrorAction SilentlyContinue |
            Sort-Object Name | Select-Object -First 1
        if ($hit) { return $hit.Name }
    } catch { }
    return ""
}

function Get-TargetExeName([string]$installPath) {
    return (Get-ProductExe $installPath)
}

function Get-InstallCandidates {
    if ($null -ne $script:InstallCandidatesCache) { return $script:InstallCandidatesCache }
    $candidates = New-Object System.Collections.Generic.List[string]
    $add = { param($p) if ($p) { $candidates.Add([string]$p) } }

    # 只认"系统里登记过的安装"，不扫盘、不猜目录：
    #   1) 已安装程序（卸载登记项）：DisplayIcon / UninstallString / InstallLocation
    #   2) App Paths：安装器登记的可执行文件路径
    #   3) 正在运行的进程：exe 实际所在目录
    # 每一路都只是线索，最后统一用 Test-TargetInstallRoot 验证（resources\app.asar* 或 WorkBuddy*.exe）。

    # 1) App Paths —— 遍历整棵子树，只按 exe 名通配，不写死 WorkBuddy.exe / WorkBuddyAI.exe
    foreach ($base in @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"
    )) {
        try {
            Get-ChildItem -Path $base -ErrorAction SilentlyContinue |
                Where-Object { $_.PSChildName -like "WorkBuddy*.exe" } |
                ForEach-Object {
                    try {
                        $item = Get-ItemProperty -Path $_.PSPath -ErrorAction SilentlyContinue
                        $exe = $item.'(default)'; if (-not $exe) { $exe = $item.Path }
                        if ($exe) { & $add (Split-Path ($exe.Trim('"')) -Parent) }
                    } catch { }
                }
        } catch { }
    }

    # 2) 卸载登记项 —— DisplayName / Publisher / InstallLocation 任一含 WorkBuddy
    foreach ($root in @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )) {
        try {
            Get-ItemProperty -Path $root -ErrorAction SilentlyContinue |
                Where-Object {
                    ($_.DisplayName -like "*WorkBuddy*") -or
                    ($_.Publisher -like "*WorkBuddy*") -or
                    ($_.InstallLocation -like "*WorkBuddy*")
                } |
                ForEach-Object {
                    if ($_.InstallLocation) { & $add $_.InstallLocation.Trim('"') }
                    if ($_.DisplayIcon) {
                        $icon = ($_.DisplayIcon -replace ',\d+$', '').Trim('"')
                        if ($icon -like '*.exe') { & $add (Split-Path $icon -Parent) }
                    }
                    if ($_.UninstallString) {
                        $u = $_.UninstallString.Trim()
                        if ($u.StartsWith('"')) {
                            $end = $u.IndexOf('"', 1); if ($end -gt 0) { $u = $u.Substring(1, $end - 1) }
                        } else { $u = ($u -split ' ')[0] }
                        if ($u -like '*.exe') { & $add (Split-Path $u -Parent) }
                    }
                }
        } catch { }
    }

    # 3) 正在运行的进程 —— 先按进程名筛（便宜），只对命中项读 Path（读全部进程的 Path 会慢到十几秒）
    try {
        Get-Process -ErrorAction SilentlyContinue |
            Where-Object { $_.ProcessName -like "WorkBuddy*" } |
            ForEach-Object {
                try {
                    if ($_.Path) { & $add (Split-Path $_.Path -Parent) }
                } catch { }
            }
    } catch { }

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
    $script:InstallCandidatesCache = $result
    return $result
}



function Get-DataDirCandidates {
    if ($null -ne $script:DataDirCandidatesCache) { return $script:DataDirCandidatesCache }
    $dirs = New-Object System.Collections.Generic.List[string]
    if ($env:WORKBUDDY_CONFIG_DIR) { $dirs.Add((Expand-TargetToken $env:WORKBUDDY_CONFIG_DIR)) }
    if ($env:USERPROFILE) {
        try {
            Get-ChildItem -LiteralPath $env:USERPROFILE -Directory -Filter ".workbuddy*" -Force -ErrorAction SilentlyContinue |
                ForEach-Object { $dirs.Add($_.FullName) }
        } catch { }
    }
    $script:DataDirCandidatesCache = @($dirs | Where-Object { $_ -and [IO.Directory]::Exists($_) } | Select-Object -Unique)
    return $script:DataDirCandidatesCache
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

# 数据目录按安装反查，不写死产品名：先看哪个 .workbuddy* 的 .skill-list-cache.json 指向这套安装，
# 再退回 <USERPROFILE>\.<id>（id 由安装目录名推导：WorkBuddy → workbuddy，WorkBuddyAI → workbuddy-ai）。
function Get-PairedConfigDir([string]$installPath) {
    if ([string]::IsNullOrWhiteSpace($installPath)) { return "" }
    foreach ($dir in @(Get-DataDirCandidates)) {
        $paired = Get-InstallPathFromDataDir $dir
        if ($paired -and $paired.TrimEnd('\') -ieq $installPath.TrimEnd('\')) { return $dir }
    }
    return ""
}

function Get-DefaultConfigDir {
    param([string]$InstallPath = "", [string]$Id = "")
    $paired = Get-PairedConfigDir $InstallPath
    if ($paired) { return $paired }
    if ($Id -and $env:USERPROFILE) { return (Join-Path $env:USERPROFILE ".$Id") }
    return ""
}

function Get-DiscoveredTargets {
    if ($null -ne $script:DiscoveredTargetsCache) { return $script:DiscoveredTargetsCache }
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
        $guess = Get-DefaultConfigDir -InstallPath $i.InstallPath -Id $id
        $result += [pscustomobject]@{
            Id = $id
            Label = (Split-Path -Leaf $i.InstallPath)
            InstallPath = $i.InstallPath
            ExeName = $i.ExeName
            ConfigDir = $guess
        }
    }

    $script:DiscoveredTargetsCache = @($result | Sort-Object Id -Unique)
    return $script:DiscoveredTargetsCache
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
        # 显式给了路径就按它建一个临时目标（便携版 / 未注册的安装），不再猜目录
        $explicitInstall = if ($InstallOverride) { Expand-TargetToken $InstallOverride } else { "" }
        $explicitConfig = if ($ConfigDirOverride) { Expand-TargetToken $ConfigDirOverride } else { "" }
        if ($explicitInstall -or $explicitConfig) {
            $disc = [pscustomobject]@{
                Id = $name2
                Label = if ($explicitInstall) { Split-Path -Leaf $explicitInstall } else { $name2 }
                InstallPath = $explicitInstall
                ExeName = if ($explicitInstall) { Get-ProductExe $explicitInstall } else { "" }
                ConfigDir = $explicitConfig
            }
        }
    }
    if (-not $conf -and -not $disc) {
        $ids = (@($discovered | ForEach-Object { $_.Id }) + @($configured | ForEach-Object { $_.Name }) | Select-Object -Unique) -join ', '
        throw ("Unknown target '$name2'. Registered installations: $ids. " +
               "If this copy was installed without registering (portable build), pass -WorkBuddyPath '<folder containing WorkBuddy*.exe>', " +
               "or pin it once in scripts\targets.json.")
    }

    $c = if ($conf) { $conf.Value } else { $null }
    $envInstall = Get-TargetEnvOverride $name2 "INSTALL"
    $envConfig = Get-TargetEnvOverride $name2 "CONFIG"
    $install = if ($InstallOverride) { Expand-TargetToken $InstallOverride }
               elseif ($c -and $c.installPath) { Expand-TargetToken $c.installPath }
               elseif ($envInstall) { Expand-TargetToken $envInstall }
               elseif ($disc) { $disc.InstallPath }
               else { "" }
    $exeName = if ($c -and $c.exeName) { $c.exeName }
               elseif ($disc) { $disc.ExeName }
               else { "" }
    if ($install -and -not (Test-TargetInstallRoot $install $exeName)) { $install = "" }
    if (-not $install -and $disc) { $install = $disc.InstallPath; if (-not $exeName -and $disc.ExeName) { $exeName = $disc.ExeName } }
    if ($install -and -not $exeName) { $exeName = Get-ProductExe $install }

    $configDir = if ($ConfigDirOverride) { Expand-TargetToken $ConfigDirOverride }
                 elseif ($c -and $c.configDir) { Expand-TargetToken $c.configDir }
                 elseif ($envConfig) { Expand-TargetToken $envConfig }
                 elseif ($disc) { $disc.ConfigDir }
                 else { "" }
    if (-not $configDir) {
        $configDir = Get-DefaultConfigDir -InstallPath $install -Id $name2
    }
    if (-not $configDir) {
        throw "Cannot determine the data directory for target '$name2'. Pass -ConfigDir explicitly."
    }

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

# 解析一批目标：-Names "all"（或 "*"、空）= 所有已发现且可用的安装，逐个返回 profile；
# 否则接受逗号分隔的 id 列表。调用方据此一次改多套安装。
function Resolve-TargetProfiles {
    param(
        [string]$Names = "all",
        [string]$ScriptRoot = $PSScriptRoot,
        [string]$InstallOverride = "",
        [string]$ConfigOverride = ""
    )

    $list = @()
    $wantsAll = (-not $Names) -or $Names -eq "all" -or $Names -eq "*"
    # 显式给了路径却没有点名目标：按"custom"这一套处理，而不是把路径套到所有安装上
    if ($wantsAll -and ($InstallOverride -or $ConfigOverride)) {
        $Names = "custom"
        $wantsAll = $false
    }
    if ($wantsAll) {
        foreach ($id in @(Get-TargetNames $ScriptRoot)) {
            try {
                $p = Resolve-TargetProfile -Name $id -ScriptRoot $ScriptRoot
                if ($p.Installed -or $p.ConfigExists) { $list += $p }
            } catch { }
        }
        $list = @($list | Sort-Object Name)
        if ($list.Count -eq 0) {
            throw "No installation found. Install WorkBuddy / WorkBuddy AI, or pass -Target <id> with -WorkBuddyPath / -ConfigDir."
        }
        return $list
    }

    foreach ($id in ($Names -split '[,;]' | ForEach-Object { $_.Trim() } | Where-Object { $_ })) {
        $list += (Resolve-TargetProfile -Name $id -ScriptRoot $ScriptRoot -InstallOverride $InstallOverride -ConfigDirOverride $ConfigOverride)
    }
    return @($list)
}
