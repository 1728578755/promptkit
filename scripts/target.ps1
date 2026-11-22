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

# ---- 磁盘发现规则（对齐清单）------------------------------------------------
# 三处实现必须一致：本文件的 Get-InstallCandidates、_install.py 的 scan_locations()、
# doctor-basic.ps1 的 Find-Installs。
#   * 盘符：C..K（跳过不存在的盘）
#   * 盘根 <盘>:\：只看一层子目录，且只认名字含 WorkBuddy 的目录（便携版）；
#     其下再深一层（有界深度 2）
#   * 常见安装根：Program Files / Program Files (x86) / Apps / Programs / Software
#     （每个再深 1 层）
#   * 用户级：%LOCALAPPDATA%\Programs、%LOCALAPPDATA%\WorkBuddy（没有 Programs 这层）、
#     <盘>:\Users\<用户>\AppData\Local\Programs
# 只收"看着像 WorkBuddy"的候选（Test-LooksLikeWorkBuddy，判据与 _install._looks_like_workbuddy 相同）。
$script:DiscoveryDriveLetters = @("C", "D", "E", "F", "G", "H", "I", "J", "K")
$script:DiscoveryInstallSubdirs = @("Program Files", "Program Files (x86)", "Apps", "Programs", "Software")

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

# 扫描出来的目录必须真的像 WorkBuddy：只靠 resources\app.asar 会把别家 Electron 应用也收进来。
# 判据与 _install.py 的 _looks_like_workbuddy() 对齐：目录名 / WorkBuddy*.exe / product.json。
function Test-LooksLikeWorkBuddy([string]$path) {
    if ([string]::IsNullOrWhiteSpace($path)) { return $false }
    if ((Split-Path -Leaf $path) -like "*WorkBuddy*") { return $true }
    if (Get-ProductExe $path) { return $true }
    $product = Join-Path $path "resources\app.asar.unpacked\cli\product.json"
    if (Test-Path -LiteralPath $product) {
        try {
            $head = [IO.File]::ReadAllText($product)
            if ($head.Length -gt 4000) { $head = $head.Substring(0, 4000) }
            if ($head -match '(?i)workbuddy') { return $true }
        } catch { }
    }
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

# App Paths 的值不一定是 exe：可能是目录、也可能是分号分隔的 PATH 串（安装器写坏时很常见）。
#   "C:\...\WorkBuddy.exe"            -> 父目录
#   "C:\...\WorkBuddy"                -> 目录本身（误当 exe 做 Split-Path 会退到 C:\...\ 上一层）
#   "C:\A;C:\B\WorkBuddy.exe"         -> 拆分后逐个判断，取存在的目录
# 返回目录列表；空串/无法识别返回空列表。
function Get-DirsFromAppPathValue([string]$value) {
    $out = @()
    if ([string]::IsNullOrWhiteSpace($value)) { return $out }
    $v = $value.Trim().Trim('"')
    if (-not $v) { return $out }
    $parts = if ($v -like '*.exe*' -and $v.Contains(';')) { $v -split ';' } else { @($v) }
    foreach ($part in $parts) {
        $p = $part.Trim().Trim('"')
        if (-not $p) { continue }
        if ($p -like '*.exe') {
            $dir = Split-Path $p -Parent
            if ($dir) { $out += $dir }
            continue
        }
        # 不是 exe：只要真的存在（或不像路径但父目录存在）就当目录收下。
        if ([IO.Directory]::Exists($p)) { $out += $p }
        elseif ($p.Contains('\')) {
            $dir = Split-Path $p -Parent
            if ($dir -and [IO.Directory]::Exists($dir)) { $out += $dir }
        }
    }
    return $out
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

    # 0) 显式线索：WORKBUDDY_INSTALL（与 _install.py 的 _pins() 同源）
    if ($env:WORKBUDDY_INSTALL) { & $add (Expand-TargetToken $env:WORKBUDDY_INSTALL) }

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
                        # 值可能是目录 / 分号分隔串，不能无条件 Split-Path -Parent（见 Get-DirsFromAppPathValue）
                        foreach ($dir in @(Get-DirsFromAppPathValue ([string]$exe))) { & $add $dir }
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

    # 4) 开始菜单 / 桌面快捷方式 —— 安装器没登记时最可靠的线索
    try {
        $shell = New-Object -ComObject WScript.Shell
        $lnkDirs = @(
            (Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs"),
            (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"),
            [Environment]::GetFolderPath("Desktop"),
            [Environment]::GetFolderPath("CommonDesktopDirectory")
        )
        foreach ($d in $lnkDirs) {
            if (-not (Test-Path -LiteralPath $d)) { continue }
            Get-ChildItem -LiteralPath $d -Filter *.lnk -Recurse -Depth 2 -ErrorAction SilentlyContinue |
                ForEach-Object {
                    try {
                        $t = $shell.CreateShortcut($_.FullName).TargetPath
                        if ($t -like "*WorkBuddy*.exe") { & $add (Split-Path $t -Parent) }
                    } catch { }
                }
        }
    } catch { }

    # 5) 兜底扫描 —— 绿色版/便携版/拷贝版没有注册表项。来源与 _install.install_roots_deep()
    #    对齐：盘根、Program Files / Program Files (x86) / Apps / Programs / Software、
    #    %LocalAppData%\Programs（含其它用户的同名目录）。只收"看着像 WorkBuddy"的目录，
    #    结果与前面几路合并去重（最后统一走 Test-TargetInstallRoot）。
    $scanBases = New-Object System.Collections.Generic.List[string]
    if ($env:LOCALAPPDATA) {
        # %LOCALAPPDATA%\Programs（常规用户级安装）
        $scanBases.Add((Join-Path $env:LOCALAPPDATA "Programs"))
        # %LOCALAPPDATA%\WorkBuddy（没有 \Programs 这一层，安装器直接放这里）
        $scanBases.Add((Join-Path $env:LOCALAPPDATA "WorkBuddy"))
    }
    foreach ($letter in $script:DiscoveryDriveLetters) {
        $drive = "${letter}:\"
        if (-not (Test-Path -LiteralPath $drive)) { continue }
        foreach ($sub in $script:DiscoveryInstallSubdirs) {
            $scanBases.Add((Join-Path $drive $sub))
        }
        $users = Join-Path $drive "Users"
        if (Test-Path -LiteralPath $users) {
            Get-ChildItem -LiteralPath $users -Directory -ErrorAction SilentlyContinue |
                Select-Object -First 20 | ForEach-Object {
                    $scanBases.Add((Join-Path $_.FullName "AppData\Local\Programs"))
                }
        }
    }
    foreach ($b in $scanBases) {
        if (-not (Test-Path -LiteralPath $b)) { continue }
        Get-ChildItem -LiteralPath $b -Directory -Recurse -Depth 1 -ErrorAction SilentlyContinue |
            Where-Object { Test-LooksLikeWorkBuddy $_.FullName } |
            ForEach-Object { & $add $_.FullName }
    }
    # 盘根本身：便携版可能直接放在 <盘>:\WorkBuddy（只扫一层，且只认名字像 WorkBuddy 的目录）；
    # 再往下一层（<盘>:\<vendor>\WorkBuddy）有界扫一次，对齐 _install.py 的 <盘>:\ 规则。
    foreach ($letter in $script:DiscoveryDriveLetters) {
        $drive = "${letter}:\"
        if (-not (Test-Path -LiteralPath $drive)) { continue }
        Get-ChildItem -LiteralPath $drive -Directory -ErrorAction SilentlyContinue |
            ForEach-Object {
                if ($_.Name -like "*WorkBuddy*") { & $add $_.FullName; return }
                Get-ChildItem -LiteralPath $_.FullName -Directory -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -like "*WorkBuddy*" } |
                    ForEach-Object { & $add $_.FullName }
            }
    }

    $seen = @{}
    $result = @()
    foreach ($c in $candidates) {
        if ([string]::IsNullOrWhiteSpace($c)) { continue }
        # 注册表/快捷方式里的脏值（引号、多余空格、非法字符）会让 GetFullPath 直接抛，
        # 这里逐条兜住：坏掉的候选跳过，不影响其它安装。
        try { $full = [IO.Path]::GetFullPath($c.Trim().Trim('"')) } catch { continue }
        if (-not $full) { continue }
        if ($seen.ContainsKey($full.ToLowerInvariant())) { continue }
        $seen[$full.ToLowerInvariant()] = $true
        $exe = Get-TargetExeName $full
        if (Test-TargetInstallRoot $full $exe) {
            $result += [pscustomobject]@{ InstallPath = $full; ExeName = $exe }
        }
    }

    # 合并去重：安装目录的子目录（resources\、share-target\ 里也有 WorkBuddy*.exe）
    # 不是独立安装。只保留"父目录不是另一个候选"的那些根。
    $roots = @($result | ForEach-Object { $_.InstallPath.TrimEnd('\').ToLowerInvariant() })
    $result = @($result | Where-Object {
            $p = $_.InstallPath.TrimEnd('\').ToLowerInvariant()
            -not ($roots | Where-Object { $_ -ne $p -and $p.StartsWith($_ + '\') })
        })

    # 真安装根优先：同一路径只保留一条（exe 名取根目录下的 WorkBuddy*.exe）
    $byPath = @{}
    foreach ($r in $result) {
        $k = $r.InstallPath.TrimEnd('\').ToLowerInvariant()
        if (-not $byPath.ContainsKey($k)) { $byPath[$k] = $r }
    }
    $result = @($byPath.Values | Sort-Object InstallPath)
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

# 从任意字符串里截出安装根：命中 "<install>\resources\app.asar.unpacked" 前缀就取前面那段。
# scopeKey 里存的是 JSON 数组字符串，反斜杠是转义过的，所以先把 \\ 还原成 \ 再匹配。
function Get-InstallRootFromScopeString([string]$value) {
    if ([string]::IsNullOrWhiteSpace($value)) { return "" }
    $s = $value.Trim()
    if ($s.StartsWith('"') -and $s.EndsWith('"') -and $s.Length -ge 2) { $s = $s.Substring(1, $s.Length - 2) }
    $s = $s -replace '\\\\', '\'
    # 只有盘符路径才可能是安装根（排除 skill 名、插件 id、空串之类的元素）
    if ($s -notmatch '^[A-Za-z]:\\') { return "" }
    $m = [regex]::Match($s, '^(.*?)\\resources\\app\.asar\.unpacked(\\|$)')
    if ($m.Success -and $m.Groups[1].Value) { return $m.Groups[1].Value.TrimEnd('\') }
    return ""
}

# 递归解析 scopeKey：可能是 1) 单路径字符串（旧格式）；2) JSON 数组字符串（实测格式，
# 元素里还有嵌套的 JSON 数组字符串）；3) 已解析出来的数组/对象。返回第一个命中的安装根。
function Resolve-ScopeKeyInstallRoot($value, [int]$depth = 0) {
    if ($null -eq $value -or $depth -gt 4) { return "" }
    if ($value -is [string]) {
        $direct = Get-InstallRootFromScopeString $value
        if ($direct) { return $direct }
        $t = $value.Trim()
        if ($t.StartsWith('[') -or $t.StartsWith('{')) {
            try { $parsed = $t | ConvertFrom-Json } catch { return "" }
            return (Resolve-ScopeKeyInstallRoot $parsed ($depth + 1))
        }
        return ""
    }
    if ($value -is [System.Collections.IEnumerable]) {
        foreach ($item in $value) {
            $hit = Resolve-ScopeKeyInstallRoot $item ($depth + 1)
            if ($hit) { return $hit }
        }
    }
    return ""
}

function Get-InstallPathFromDataDir {
    param([string]$ConfigDir, [string]$Encoding = "")
    $configDir = $ConfigDir
    if ([string]::IsNullOrWhiteSpace($configDir)) { return "" }
    $cacheFile = Join-Path $configDir ".skill-list-cache.json"
    if (Test-Path -LiteralPath $cacheFile) {
        try {
            # -Encoding UTF8：文件是无 BOM UTF-8，PS 5.1 的 Get-Content -Raw 默认按 ANSI(GBK) 读，
            # 中文描述会变乱码，ConvertFrom-Json 直接抛异常（异常又被 catch 吞掉 = 永远返回 ""）。
            $gcArgs = @{ LiteralPath = $cacheFile; Raw = $true }
            if ($Encoding) { $gcArgs.Encoding = $Encoding } else { $gcArgs.Encoding = "UTF8" }
            $j = Get-Content @gcArgs | ConvertFrom-Json
            $hit = Resolve-ScopeKeyInstallRoot $j.scopeKey
            if ($hit) { return $hit }
        } catch { }
    }
    # 客户端自己的插件市场登记：workbuddy-builtin 的 source.path / installLocation 就是
    # <install>\resources\app.asar.unpacked\resources\plugins\workbuddy-builtin。
    # 有的数据目录（实测 .workbuddy）没有 .skill-list-cache.json，但有这个文件。
    $market = Join-Path $configDir "plugins\known_marketplaces.json"
    if (Test-Path -LiteralPath $market) {
        try {
            $mj = Get-Content -LiteralPath $market -Raw -Encoding UTF8 | ConvertFrom-Json
            $hits = @()
            foreach ($prop in $mj.PSObject.Properties) {
                foreach ($v in @($prop.Value.source.path, $prop.Value.installLocation)) {
                    $hit = Get-InstallRootFromScopeString ([string]$v)
                    if ($hit) { $hits += $hit }
                }
            }
            foreach ($hit in $hits) { if (Test-TargetInstallRoot $hit "") { return $hit } }
            if ($hits.Count -gt 0) { return $hits[0] }
        } catch { }
    }
    $settings = Join-Path $configDir "settings.json"
    if (Test-Path -LiteralPath $settings) {
        try {
            $raw = Get-Content -LiteralPath $settings -Raw -Encoding UTF8
            $m = [regex]::Match($raw, '([A-Za-z]:\\\\[^"]*?)\\\\resources\\\\app\.asar\.unpacked')
            if ($m.Success) { return ($m.Groups[1].Value -replace '\\\\', '\') }
            $m = [regex]::Match($raw, '([A-Za-z]:\\[^"]*?)\\resources\\app\.asar\.unpacked')
            if ($m.Success) { return $m.Groups[1].Value }
        } catch { }
    }
    return ""
}

# 产品自报的数据目录名：<install>\...\cli\product.json 的 dataFolderName（实测 .workbuddy / .workbuddy-ai）。
# 这是最权威的配对依据——安装目录名可以是任意改名（D:\Portable\WB），product.json 不会说谎。
# name 字段实测为空，不能用；endpoint 只说明连哪套服务，也不如 dataFolderName 直接。
function Get-DataFolderName([string]$installPath) {
    if ([string]::IsNullOrWhiteSpace($installPath)) { return "" }
    $rels = @(
        "resources\app.asar.unpacked\cli\product.json",
        "resources\app.asar.unpacked\node_modules\@genie\agent-harness\cli\product.json"
    )
    foreach ($rel in $rels) {
        $p = Join-Path $installPath $rel
        if (-not (Test-Path -LiteralPath $p)) { continue }
        try {
            $raw = [IO.File]::ReadAllText($p, [Text.Encoding]::UTF8)
            $j = $raw | ConvertFrom-Json
            $folder = [string]$j.dataFolderName
            if ($folder) { return $folder.Trim().TrimEnd('\', '/') }
        } catch { }
    }
    return ""
}

# 数据目录按安装反查，不写死产品名：
#   1) 哪个数据目录的 .skill-list-cache.json 指向这套安装（最直接的证据）
#   2) product.json 的 dataFolderName（新环境没有 skill-list-cache 时的权威依据）
#   3) 没有配对时由 Get-DefaultConfigDir 退回 <USERPROFILE>\.<id>（安装目录叶名推导，兜底）
function Get-PairedConfigDir([string]$installPath) {
    if ([string]::IsNullOrWhiteSpace($installPath)) { return "" }
    $target = $installPath.TrimEnd('\')
    foreach ($dir in @(Get-DataDirCandidates)) {
        $paired = Get-InstallPathFromDataDir $dir
        if ($paired -and $paired.TrimEnd('\') -ieq $target) { return $dir }
    }
    $folder = Get-DataFolderName $installPath
    if ($folder) {
        foreach ($dir in @(Get-DataDirCandidates)) {
            if ((Split-Path -Leaf $dir) -ieq $folder) { return $dir }
        }
        $abs = if ([IO.Path]::IsPathRooted($folder)) { $folder }
               elseif ($env:USERPROFILE) { Join-Path $env:USERPROFILE $folder }
               else { "" }
        if ($abs -and [IO.Directory]::Exists($abs)) { return $abs }
    }
    return ""
}

function Get-DefaultConfigDir {
    param([string]$InstallPath = "", [string]$Id = "")
    $paired = Get-PairedConfigDir $InstallPath
    if ($paired) { return $paired }
    # 新环境（还没有 .skill-list-cache.json）：按 product.json 的 dataFolderName 推导，
    # 而不是按安装目录叶名猜——D:\Portable\WB 反推出 .wb 是错的，product.json 里写着 .workbuddy。
    $folder = Get-DataFolderName $InstallPath
    if ($folder) {
        if ([IO.Path]::IsPathRooted($folder)) { return $folder }
        if ($env:USERPROFILE) { return (Join-Path $env:USERPROFILE $folder) }
    }
    # 兜底：<USERPROFILE>\.<id>。Id 可能来自 -Target 的 selector（"workbuddy@C:\..."）或带路径，
    # 截掉 @ 之后的部分再拼，避免生成 ".workbuddy@C:\..." 这种非法目录名。
    $idClean = if ($Id) { ($Id -split '@')[0].Trim() } else { "" }
    if ($idClean -and $env:USERPROFILE) { return (Join-Path $env:USERPROFILE ".$idClean") }
    return ""
}

# 已存在、但 .skill-list-cache.json 还没写过的数据目录：按"唯一一个像数据目录的 .workbuddy*"配对。
# 新电脑上客户端刚启动、还没进过任何模式时缓存就是这样（有 sessions/traces/settings.json，
# 但没有 .skill-list-cache.json），不配对就会退回不存在的 .<id>，部署写到空气里。
#
# 只在"本机确实只有这一套安装"时配对：多套安装时无法判断这个数据目录属于哪一套，
# 套给每一套会把缓存写到别的产品的数据目录里（正是"多安装互相污染"的成因）。
function Get-LooseConfigDir([string]$InstallPath = "") {
    if ([string]::IsNullOrWhiteSpace($InstallPath)) { return "" }
    $installs = @(Get-InstallCandidates)
    if ($installs.Count -ne 1) { return "" }
    if ($installs[0].InstallPath.TrimEnd('\') -ine $InstallPath.TrimEnd('\')) { return "" }
    $cands = @()
    foreach ($dir in @(Get-DataDirCandidates)) {
        if (Test-ClientDataDir $dir) { $cands += $dir }
    }
    if ($cands.Count -eq 1) { return $cands[0] }
    return ""
}

function Test-ClientDataDir([string]$path) {
    if (-not (Test-Path -LiteralPath $path -PathType Container)) { return $false }
    foreach ($rel in @("plugins", "sessions", "traces", "settings.json")) {
        if (Test-Path -LiteralPath (Join-Path $path $rel)) { return $true }
    }
    return $false
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
        if (-not (Test-ClientDataDir $guess)) {
            $loose = Get-LooseConfigDir $i.InstallPath
            if ($loose) { $guess = $loose }
        }
        $result += [pscustomobject]@{
            Id = $id
            Label = (Split-Path -Leaf $i.InstallPath)
            InstallPath = $i.InstallPath
            ExeName = $i.ExeName
            ConfigDir = $guess
        }
    }

    # 同一 Id 的多套安装（C:\Program Files\WorkBuddy 与 D:\WorkBuddy 叶名相同）必须全部保留：
    # 静默丢掉任何一套，用户就会以为部署覆盖了全部安装。重复的标 DuplicateId=true，
    # 下游用 Selector（Id 唯一时就是 Id，重复时是 Id@InstallPath）区分。
    $idCount = @{}
    foreach ($r in $result) {
        $k = ([string]$r.Id).ToLowerInvariant()
        $idCount[$k] = 1 + $(if ($idCount.ContainsKey($k)) { $idCount[$k] } else { 0 })
    }
    foreach ($r in $result) {
        $dup = ($idCount[([string]$r.Id).ToLowerInvariant()] -gt 1)
        $r | Add-Member -NotePropertyName DuplicateId -NotePropertyValue $dup -Force
        $sel = if ($dup) { "$($r.Id)@$($r.InstallPath)" } else { [string]$r.Id }
        $r | Add-Member -NotePropertyName Selector -NotePropertyValue $sel -Force
    }

    $script:DiscoveredTargetsCache = @($result | Sort-Object Id, InstallPath)
    return $script:DiscoveredTargetsCache
}

# -Target 选择器解析：既接受 id（"workbuddy"），也接受 Id+InstallPath 消歧形式
#   "workbuddy@C:\Program Files\WorkBuddy"   或直接给安装路径 "C:\Program Files\WorkBuddy"
# 同一 Id 有多套安装时，裸 id 会命中多条 —— 由调用方报错要求消歧，绝不静默取第一条。
function Select-DiscoveredTargets($discovered, [string]$name) {
    if ([string]::IsNullOrWhiteSpace($name)) { return @() }
    $n = $name.Trim()
    $at = $n.IndexOf('@')
    if ($at -gt 0) {
        $idPart = $n.Substring(0, $at).Trim()
        $pathPart = $n.Substring($at + 1).Trim().Trim('"')
        return @($discovered | Where-Object {
                $_.Id -ieq $idPart -and
                $pathPart -and
                ($_.InstallPath.TrimEnd('\') -ieq $pathPart.TrimEnd('\') -or
                 $_.InstallPath.TrimEnd('\').ToLowerInvariant().StartsWith($pathPart.TrimEnd('\').ToLowerInvariant() + '\'))
            })
    }
    if ($n -match '^[A-Za-z]:[\\/]') {
        return @($discovered | Where-Object {
                $_.InstallPath.TrimEnd('\') -ieq $n.TrimEnd('\') -or
                $_.InstallPath.TrimEnd('\').ToLowerInvariant().StartsWith($n.TrimEnd('\').ToLowerInvariant() + '\')
            })
    }
    return @($discovered | Where-Object { $_.Id -ieq $n })
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
    # 同 Id 多套安装时必须按 InstallPath 消歧：-Target "workbuddy@D:\WorkBuddy"、-Target "D:\WorkBuddy"，
    # 或直接给 -WorkBuddyPath。裸 id 命中多条就明确报错，不静默挑一条。
    $matches = @(Select-DiscoveredTargets $discovered $name2)
    $disc = if ($matches.Count -eq 1) { $matches[0] } else { $null }
    if ($matches.Count -gt 1 -and -not $InstallOverride) {
        $sel = ($matches | ForEach-Object { $_.Selector }) -join ', '
        throw ("Multiple installations share the id '$name2': $sel. " +
               "Pass -Target '<id>@<install path>' (or -WorkBuddyPath '<install path>') to pick one.")
    }
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
        # Id 只用干净的安装 id（selector 可能是 "id@path"，不能拿来拼 .<id>）
        $idForConfig = if ($disc -and $disc.Id) { $disc.Id } else { $name2 }
        $configDir = Get-DefaultConfigDir -InstallPath $install -Id $idForConfig
    }
    if (-not (Test-ClientDataDir $configDir)) {
        # 反查/推导出来的目录不存在时，退回"唯一一个像数据目录的 .workbuddy*"
        $loose = Get-LooseConfigDir $install
        if ($loose) { $configDir = $loose }
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

    # 同 Id 多套安装时 Name 用 Selector（id@install），父进程传给子脚本的 -Target 仍能唯一定位；
    # 无重复时保持原 id，向后兼容。
    $profileName = if ($disc -and $disc.DuplicateId) { $disc.Selector } else { $name2 }

    return [pscustomobject]@{
        Name = $profileName
        Id = if ($disc -and $disc.Id) { $disc.Id } else { $name2 }
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
    # 同 Id 多套安装时用 Selector（id@install）而不是裸 id，否则 Resolve-TargetProfile 会消歧失败、
    # "all" 会漏掉其中一套。
    $ids += @(Get-DiscoveredTargets | ForEach-Object { if ($_.DuplicateId) { $_.Selector } else { $_.Id } })
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
