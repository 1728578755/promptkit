# 无 Python 时的兜底诊断：只用 PowerShell，不写死任何安装路径。
#
# 找到的安装来源：环境变量 WORKBUDDY_INSTALL、卸载注册表、正在运行的进程、
# 开始菜单快捷方式，最后是常见安装根目录的有界扫描。
# 每套安装都打印：asar 的 current-time 锚点状态、chunk 名称/大小/三个补丁标记。
param(
    [string[]]$Install = @()
)

$ErrorActionPreference = "SilentlyContinue"
$ENC = [Text.Encoding]::GetEncoding(28591)

# ---- 磁盘发现规则（对齐清单）------------------------------------------------
# 三处实现必须一致：本文件的 Find-Installs、scripts\_install.py 的 scan_locations()、
# scripts\target.ps1 的 Get-InstallCandidates。
#   * 盘符：C..K（跳过不存在的盘）
#   * 盘根 <盘>:\：只看一层子目录，且只认名字含 WorkBuddy 的目录（便携版）；
#     其下再深一层（有界深度 2）
#   * 常见安装根：Program Files / Program Files (x86) / Apps / Programs / Software
#     （每个再深 1 层）
#   * 用户级：%LOCALAPPDATA%\Programs、%LOCALAPPDATA%\WorkBuddy（没有 Programs 这层）、
#     <盘>:\Users\<用户>\AppData\Local\Programs
# 只收"看着像 WorkBuddy"的候选（名字 / WorkBuddy*.exe / product.json，判据同
# _install._looks_like_workbuddy）。
$script:DiscoveryDriveLetters = @("C", "D", "E", "F", "G", "H", "I", "J", "K")
$script:DiscoveryInstallSubdirs = @("Program Files", "Program Files (x86)", "Apps", "Programs", "Software")

# 产品自报的数据目录名：<install>\...\cli\product.json 的 dataFolderName。
# 与 target.ps1 的 Get-DataFolderName 同一判据（优先 app.asar.unpacked\cli，
# 其次 @genie/agent-harness 布局）。
function Get-DataFolderName([string]$installPath) {
    if (-not $installPath) { return "" }
    foreach ($rel in @(
        "resources\app.asar.unpacked\cli\product.json",
        "resources\app.asar.unpacked\node_modules\@genie\agent-harness\cli\product.json"
    )) {
        $p = Join-Path $installPath $rel
        if (-not (Test-Path -LiteralPath $p)) { continue }
        try {
            $j = [IO.File]::ReadAllText($p, [Text.Encoding]::UTF8) | ConvertFrom-Json
            if ($j.dataFolderName) { return ([string]$j.dataFolderName).Trim().TrimEnd('\', '/') }
        } catch { }
    }
    return ""
}

function Test-LooksLikeWorkBuddy([string]$path) {
    if (-not $path) { return $false }
    if ((Split-Path -Leaf $path) -match "WorkBuddy") { return $true }
    if (Test-Path -LiteralPath (Join-Path $path "WorkBuddy*.exe")) { return $true }
    $product = Join-Path $path "resources\app.asar.unpacked\cli\product.json"
    if (Test-Path -LiteralPath $product) {
        try {
            $head = [IO.File]::ReadAllText($product, [Text.Encoding]::UTF8)
            if ($head.Length -gt 4000) { $head = $head.Substring(0, 4000) }
            if ($head -match '(?i)workbuddy') { return $true }
        } catch { }
    }
    return $false
}

# 大文件里找一段 ASCII 标记：分块读、留重叠，避免把 170MB 的 asar 整个读成字符串
function Test-Marker([string]$path, [string]$needle) {
    if (-not (Test-Path -LiteralPath $path)) { return $false }
    $nb = $ENC.GetBytes($needle)
    $fs = [IO.File]::OpenRead($path)
    try {
        $chunk = 4MB
        $buf = New-Object byte[] ($chunk + $nb.Length)
        $keep = 0
        while ($true) {
            $read = $fs.Read($buf, $keep, $chunk)
            if ($read -le 0) { break }
            $total = $keep + $read
            $text = $ENC.GetString($buf, 0, $total)
            if ($text.Contains($needle)) { return $true }
            $keep = [Math]::Min($nb.Length - 1, $total)
            if ($keep -gt 0) { [Array]::Copy($buf, $total - $keep, $buf, 0, $keep) }
        }
        return $false
    } finally {
        $fs.Close()
    }
}

function Find-Installs {
    $roots = New-Object System.Collections.Generic.List[string]
    if ($env:WORKBUDDY_INSTALL) { $roots.Add($env:WORKBUDDY_INSTALL) }

    # 1) 卸载注册表
    $keys = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    foreach ($k in $keys) {
        Get-ItemProperty $k | Where-Object { $_.DisplayName -match "WorkBuddy" } | ForEach-Object {
            foreach ($raw in @($_.InstallLocation, $_.DisplayIcon, $_.UninstallString)) {
                if (-not $raw) { continue }
                $p = ([string]$raw).Trim()
                if ($p.StartsWith('"')) {
                    $endQ = $p.IndexOf('"', 1)
                    if ($endQ -gt 1) { $p = $p.Substring(1, $endQ - 1) }
                } else {
                    $p = ($p -split ' ')[0]
                }
                $p = $p -replace ',\s*\d+$', ''
                if ($p -and (Test-Path -LiteralPath $p)) {
                    if (Test-Path -LiteralPath $p -PathType Container) { $roots.Add($p) }
                    else {
                        $dir = Split-Path -Parent $p
                        if ($dir) { $roots.Add($dir) }
                    }
                }
            }
        }
    }

    # 2) 正在运行的进程
    Get-CimInstance Win32_Process | Where-Object { $_.Name -match "WorkBuddy" } | ForEach-Object {
        if ($_.ExecutablePath) { $roots.Add((Split-Path -Parent $_.ExecutablePath)) }
    }

    # 3) 开始菜单 / 桌面快捷方式
    $sh = New-Object -ComObject WScript.Shell
    $dirs = @(
        (Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs"),
        (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"),
        [Environment]::GetFolderPath("Desktop"),
        (Join-Path $env:PUBLIC "Desktop")
    )
    foreach ($d in $dirs) {
        if (-not (Test-Path -LiteralPath $d)) { continue }
        Get-ChildItem -LiteralPath $d -Filter *.lnk -Recurse -Depth 2 | ForEach-Object {
            $t = $sh.CreateShortcut($_.FullName).TargetPath
            if ($t -match "WorkBuddy.*\.exe$") { $roots.Add((Split-Path -Parent $t)) }
        }
    }

    # 4) 常见安装根目录的有界扫描（每盘只看一两层，找名字带 WorkBuddy 的目录）
    #    盘符 / 目录集合与 _install.py 的 scan_locations()、target.ps1 的 Get-InstallCandidates 对齐。
    $bases = New-Object System.Collections.Generic.List[string]
    if ($env:LOCALAPPDATA) {
        # %LOCALAPPDATA%\Programs 和 %LOCALAPPDATA%\WorkBuddy（安装器没有 Programs 这层时）
        $bases.Add((Join-Path $env:LOCALAPPDATA "Programs"))
        $bases.Add((Join-Path $env:LOCALAPPDATA "WorkBuddy"))
    }
    foreach ($letter in $script:DiscoveryDriveLetters) {
        $drive = "${letter}:\"
        if (-not (Test-Path -LiteralPath $drive)) { continue }
        foreach ($sub in $script:DiscoveryInstallSubdirs) { $bases.Add((Join-Path $drive $sub)) }
        $users = Join-Path $drive "Users"
        if (Test-Path -LiteralPath $users) {
            Get-ChildItem -LiteralPath $users -Directory | Select-Object -First 20 | ForEach-Object {
                $bases.Add((Join-Path $_.FullName "AppData\Local\Programs"))
            }
        }
    }
    foreach ($b in $bases) {
        if (-not (Test-Path -LiteralPath $b)) { continue }
        Get-ChildItem -LiteralPath $b -Directory -Depth 1 -ErrorAction SilentlyContinue |
            Where-Object { Test-LooksLikeWorkBuddy $_.FullName } |
            ForEach-Object { $roots.Add($_.FullName) }
    }
    # 盘根：<盘>:\*WorkBuddy*（便携版直接放盘根），再深一层（<盘>:\<vendor>\WorkBuddy）。
    # 有界深度 2，只认名字带 WorkBuddy 的目录，不会把整块盘翻一遍。
    foreach ($letter in $script:DiscoveryDriveLetters) {
        $drive = "${letter}:\"
        if (-not (Test-Path -LiteralPath $drive)) { continue }
        Get-ChildItem -LiteralPath $drive -Directory -ErrorAction SilentlyContinue | ForEach-Object {
            if ($_.Name -match "WorkBuddy") { $roots.Add($_.FullName) }
            Get-ChildItem -LiteralPath $_.FullName -Directory -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -match "WorkBuddy" } |
                ForEach-Object { $roots.Add($_.FullName) }
        }
    }

    $out = New-Object System.Collections.Generic.List[string]
    foreach ($r in ($roots | Select-Object -Unique)) {
        if (-not $r) { continue }
        if (-not (Test-Path -LiteralPath (Join-Path $r "resources\app.asar"))) { continue }
        if ($out -notcontains $r) { $out.Add($r) }
    }
    return $out
}

# 安装级问题必须按"哪一套安装"记账，不能只往一个平面列表里塞字符串再靠子串匹配回查：
# C:\Program Files\WorkBuddy 是 C:\Program Files\WorkBuddyAI 的前缀，旧实现
# $line.Contains(" $r") 会让前者的 WARN/FAIL 误命中后者（DIAGNOSIS L356）。
function Add-InstallProblem([string]$root, [string]$text) {
    if (-not $script:InstallProblems.ContainsKey($root)) { $script:InstallProblems[$root] = @() }
    $script:InstallProblems[$root] += $text
    $script:Problems += $text
}

# 返回 "PASS" / "WARN" / "FAIL"：FAIL 写进 $script:Problems 与 $script:InstallProblems[$root]，
# WARN 写进 $script:Warnings。
# 口径与 doctor.py 对齐：
#   * v3 完整（helper+scrub+blank+drop 且调用点全包）       -> PASS
#   * 有 /*__wbTrimLite*/ 且 v3 helper 在位但缺块（残缺 v3）-> FAIL（安装级失败）
#   * 有 /*__wbTrimLite*/ 但没有 v3 helper（合法降级态）    -> WARN（不算生效）
function Show-Install([string]$root) {
    Write-Host ""
    Write-Host "install : $root"
    $healthy = $true
    $asar = Join-Path $root "resources\app.asar"
    if (-not (Test-Path -LiteralPath $asar)) {
        Write-Host "  asar  : 不存在 $asar" -ForegroundColor Red
        Add-InstallProblem $root "asar $($root): 文件不存在"
        return "FAIL"
    }
    $size = (Get-Item -LiteralPath $asar).Length
    Write-Host ("  asar  : {0:N0} bytes" -f $size)
    # 逐条检查 9 个补丁 + integrity + exe 清单。旧实现只查 current-time-cloud 一条，
    # 其余 8 条和完整性完全不可见（见 DIAGNOSIS F-05 第 3 节）。
    $asarProblems = @(Test-AsarPatches $root $asar)
    foreach ($p in $asarProblems) { Add-InstallProblem $root $p }
    if ($asarProblems.Count -gt 0) { $healthy = $false }

    $dist = Join-Path $root "resources\app.asar.unpacked\cli\dist"
    if (-not (Test-Path -LiteralPath $dist)) {
        Write-Host "  cli   : app.asar.unpacked\cli\dist 不存在" -ForegroundColor Red
        Add-InstallProblem $root "cli $($root): app.asar.unpacked\cli\dist 不存在"
        return "FAIL"
    }
    $found = 0
    $chunkWarn = $false
    $chunkFail = $false
    foreach ($c in (Get-ChildItem -LiteralPath $dist -Filter *.mjs -Recurse -Depth 4 | Where-Object { $_.Length -lt 24MB })) {
        $s = [IO.File]::ReadAllText($c.FullName)
        if (-not ($s.Contains("modelInput.instructions") -and $s.Contains("modelInput.input"))) { continue }
        $found++
        # 判据与 doctor.py 的 cli_state()/cli_problems() 对齐：
        #   helper = /*__wbTrimV3*/；scrub = __wbScrubFetch 出现 >= 2 次（定义 + 调用）；
        #   blank/drop 是 v3 helper 的组成部分，缺一个就是残缺 v3（helper-outdated）。
        $helper = $s.Contains("__wbTrimV3")
        $scrub = ([regex]::Matches($s, [regex]::Escape("__wbScrubFetch")).Count) -ge 2
        $blank = $s.Contains("__wbBlank")
        $drop = $s.Contains("__wbDropReminder")
        $lite = $s.Contains("__wbTrimLite")
        $anchors = ([regex]::Matches($s, [regex]::Escape('systemInstructions:__wbStripLegacyAny('))).Count
        $inputs = ([regex]::Matches($s, [regex]::Escape('input:__wbCleanRuntimeInput('))).Count
        # 不数"打了几处"（不同构建的分支数不同）：看还有没有没包上的调用点。
        $rawIns = ([regex]::Matches($s, '(?<![A-Za-z0-9_$])systemInstructions:(?!__wbStripLegacyAny\()[A-Za-z$_][\w$]*\.modelInput\.instructions,')).Count
        $rawIn = ([regex]::Matches($s, '(?<![A-Za-z0-9_$])input:(?!__wbCleanRuntimeInput\()[A-Za-z$_][\w$]*\.modelInput\.input,')).Count
        $full = $helper -and $scrub -and $blank -and $drop -and ($anchors -ge 1) -and ($inputs -ge 1) -and ($rawIns -eq 0) -and ($rawIn -eq 0)
        # 口径对齐 doctor.py 的 layer_cli()（B1）：
        #   * 完整 v3（helper+scrub+blank+drop 且调用点全包）          -> PATCHED / PASS
        #   * 有 /*__wbTrimLite*/ 且 v3 helper 在位但缺块（残缺 v3）    -> FAIL（"v3 补丁残缺: 缺 X"）
        #   * 有 /*__wbTrimLite*/ 但没有 v3 helper（合法轻量降级态）    -> WARN
        #   * 两者都没有 / v3 缺块且没有 lite                          -> FAIL
        $v3Incomplete = $lite -and $helper -and -not ($blank -and $drop -and $scrub)
        $liteOnly = $lite -and (-not $helper)
        $verdict = if ($full) { "PATCHED" } elseif ($v3Incomplete) { "V3 INCOMPLETE" } elseif ($liteOnly) { "LITE ONLY" } else { "NOT FULLY PATCHED" }
        $c2 = if ($full) { "Green" } elseif ($v3Incomplete) { "Red" } elseif ($liteOnly) { "Yellow" } else { "Red" }
        Write-Host ("  cli   : {0}  {1:N0} bytes  修改={2}" -f $c.Name, $c.Length, $c.LastWriteTime) -ForegroundColor $c2
        Write-Host ("          helper={0} scrub={1} blank={2} drop={3} instructions={4} input={5} raw={6}/{7} => {8}" -f `
            $helper, $scrub, $blank, $drop, $anchors, $inputs, $rawIns, $rawIn, $verdict) -ForegroundColor $c2
        if ($v3Incomplete) {
            $missing = @()
            if (-not $scrub) { $missing += "fetch-scrub" }
            if (-not $blank) { $missing += "blank" }
            if (-not $drop) { $missing += "drop" }
            # 结论行标 WARN，但计入安装级失败口径（doctor.py B1：v3 残缺计 FAIL 进 per_install，
            # 当前使用时结论必须是 NOT EFFECTIVE）。
            $chunkWarn = $true
            Add-InstallProblem $root ("cli $($root) $($c.Name): v3 补丁残缺（缺 $($missing -join ',')）: helper=$helper scrub=$scrub blank=$blank drop=$drop")
            if (-not $script:InstallWarnings.ContainsKey($root)) { $script:InstallWarnings[$root] = @() }
            $script:InstallWarnings[$root] = @($script:InstallWarnings[$root]) + @("v3 补丁残缺：缺 $($missing -join ',')")
        } elseif ($liteOnly) {
            $chunkWarn = $true
            $script:Warnings += "cli $($root) $($c.Name): 仅轻量出口清理（fetch-scrub 未打）→ 该安装未完全生效"
            if (-not $script:InstallWarnings.ContainsKey($root)) { $script:InstallWarnings[$root] = @() }
            $script:InstallWarnings[$root] = @($script:InstallWarnings[$root]) + @("cli $($c.Name): 仅轻量出口清理")
        } elseif (-not $full) {
            $chunkFail = $true
            Add-InstallProblem $root "cli $($root) $($c.Name): helper=$helper scrub=$scrub blank=$blank drop=$drop raw=$rawIns/$rawIn"
        }
    }
    if ($found -eq 0) {
        Write-Host "  cli   : 没有承载 modelInput 的 chunk" -ForegroundColor Red
        Add-InstallProblem $root "cli $($root): 没有承载 modelInput 的 chunk"
        return "FAIL"
    }
    if (-not $healthy -or $chunkFail) { return "FAIL" }
    if ($chunkWarn) { return "WARN" }
    return "PASS"
}

# 结论行上 v3 残缺显示 WARN（不是全废，只是没打全），但它必须计入安装级失败口径。
# 返回 "FAIL" / "WARN" / "PASS"：Show-Install 的原始状态 + 是否有归属到该安装的问题。
function Get-InstallSeverity([string]$root) {
    $state = $script:InstallState[$root]
    $hasProblem = ($script:InstallProblems.ContainsKey($root) -and @($script:InstallProblems[$root]).Count -gt 0)
    if ($state -eq "FAIL") { return "FAIL" }
    if ($state -eq "WARN" -or $hasProblem) { return "WARN" }
    return "PASS"
}

# asar 补丁状态：优先调 scripts\patch-asar.py --status（与 doctor.py 同源，9 条 + integrity +
# exe 清单一次给全）；没有 Python 时退回 PowerShell 标记探测（只覆盖能可靠探测的几条）。
# 返回问题列表（空 = 全部 PASS），并把逐条结果打印出来。
function Test-AsarPatches([string]$root, [string]$asar) {
    # 用 List 而不是 @() +=：后者在"0 项 -> 1 项"时会把数组塌成标量，.Count 就不可靠了。
    $problems = New-Object System.Collections.Generic.List[string]
    $py = Get-DoctorPython
    $lines = @()
    if ($py) {
        try {
            $exe = Get-ProductExeForDoctor $root
            $argv = @("-X", "utf8", (Join-Path $PSScriptRoot "patch-asar.py"), "--status", "--asar", $asar)
            if ($exe) { $argv += @("--exe", $exe) }
            $prevEnc = [Console]::OutputEncoding
            $oldIo = $env:PYTHONIOENCODING
            try {
                # 子进程 stdout 按 UTF-8 解码（否则中文一律乱码，按行解析全失效）
                [Console]::OutputEncoding = [Text.Encoding]::UTF8
                $env:PYTHONIOENCODING = "utf-8"
                $lines = @(& $py @argv 2>&1 | ForEach-Object { [string]$_ })
            } finally {
                try { [Console]::OutputEncoding = $prevEnc } catch { }
                if ($null -eq $oldIo) { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue } else { $env:PYTHONIOENCODING = $oldIo }
            }
        } catch {
            $lines = @()
            $py = $null
        }
    }
    if (-not $py -or $lines.Count -eq 0) {
        Write-Host "  asar  : 没有可用的 Python，退回标记探测（只覆盖 current-time / current-time-cloud）" -ForegroundColor Yellow
        $raw = Test-Marker $asar 't.currentTime&&(r.current_time=t.currentTime)'
        $patched = Test-Marker $asar ('0' + (' ' * 12) + '&&(r.current_time=t.currentTime)')
        $state = if ($patched -and -not $raw) { "patched" } elseif ($raw) { "RAW" } else { "anchor-missing" }
        $color = if ($state -eq "patched") { "Green" } else { "Red" }
        Write-Host ("          current-time-cloud={0}" -f $state) -ForegroundColor $color
        if ($state -ne "patched") { $problems.Add("asar $($root): current-time-cloud=$state") }
        $script:Warnings += "asar $($root): 未检查其余 8 条补丁 / integrity / exe 清单（无 Python）"
        return $problems
    }

    $patchRows = @()
    $integ = ""
    $exeSync = ""
    foreach ($line in $lines) {
        if ($line -match '^patch:\s+(\S+)\s+(\S+)') {
            $patchRows += [pscustomobject]@{ Name = $Matches[1]; State = $Matches[2] }
        } elseif ($line -match '^integ:\s+(.*)$') { $integ = $Matches[1].Trim() }
        elseif ($line -match '^exe sync:\s+(.*)$') { $exeSync = $Matches[1].Trim() }
    }
    foreach ($p in $patchRows) {
        $ok = ($p.State -eq "patched")
        Write-Host ("          {0,-20} {1}" -f $p.Name, $p.State) -ForegroundColor $(if ($ok) { "Green" } else { "Red" })
        if (-not $ok) { $problems.Add("asar $($root): $($p.Name)=$($p.State)") }
    }
    if ($integ) {
        $ok = ($integ -eq "MATCH")
        Write-Host ("          {0,-20} {1}" -f "integrity", $(if ($ok) { "MATCH" } else { $integ })) -ForegroundColor $(if ($ok) { "Green" } else { "Red" })
        if (-not $ok) { $problems.Add("asar $($root): integrity=$integ") }
    }
    if ($exeSync) {
        $ok = ($exeSync -like "MATCH*")
        Write-Host ("          {0,-20} {1}" -f "exe manifest", $(if ($ok) { "MATCH" } else { $exeSync })) -ForegroundColor $(if ($ok) { "Green" } else { "Red" })
        if (-not $ok) { $problems.Add("asar $($root): exe manifest=$exeSync") }
    }
    if ($patchRows.Count -eq 0) {
        # patch-asar.py 没认出这个 asar（坏文件 / 不是 WorkBuddy 的包）才算问题。
        # 它自己打印的 "asar 头解析失败: …" 已在上面原样输出，这里只补一条问题项。
        $problems.Add("asar $($root): patch-asar.py --status 没有给出补丁状态（asar 头解析失败或不是 WorkBuddy 的包）")
    }
    return $problems
}

# 找得到 Python 就用（WORKBUDDY_PYTHON / PATH 上的 python / 客户端自带），与 doctor.cmd 的优先级一致。
function Get-DoctorPython {
    if ($script:DoctorPythonResolved) { return $script:DoctorPython }
    $script:DoctorPythonResolved = $true
    $script:DoctorPython = ""
    if ($env:WORKBUDDY_PYTHON -and (Test-Path -LiteralPath $env:WORKBUDDY_PYTHON)) {
        $script:DoctorPython = $env:WORKBUDDY_PYTHON; return $script:DoctorPython
    }
    foreach ($name in @("python", "py")) {
        $cmd = Get-Command $name -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($cmd -and $cmd.Source -and $cmd.Source -notmatch '\\WindowsApps\\') {
            $script:DoctorPython = $cmd.Source; return $script:DoctorPython
        }
    }
    $pyRoot = Join-Path $env:USERPROFILE ".workbuddy\binaries\python\versions"
    if (Test-Path -LiteralPath $pyRoot) {
        $cand = Join-Path $pyRoot "current"
        if (Test-Path -LiteralPath $cand) {
            try {
                $ver = ([IO.File]::ReadAllText($cand)).Trim()
                $p = Join-Path $pyRoot "$ver\python.exe"
                if (Test-Path -LiteralPath $p) { $script:DoctorPython = $p; return $script:DoctorPython }
            } catch { }
        }
        $hit = Get-ChildItem -LiteralPath $pyRoot -Directory -ErrorAction SilentlyContinue |
            ForEach-Object { Join-Path $_.FullName "python.exe" } |
            Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
        if ($hit) { $script:DoctorPython = $hit }
    }
    return $script:DoctorPython
}

function Get-ProductExeForDoctor([string]$root) {
    $hit = Get-ChildItem -LiteralPath $root -Filter "WorkBuddy*.exe" -File -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -notmatch 'ninstall' } | Sort-Object Name | Select-Object -First 1
    if ($hit) { return $hit.FullName }
    return ""
}

function Add-TemplateProblem([string]$cfgRoot, [string]$text) {
    # 有配对 -> 计入该安装（多安装时不再一票否决全局）；无配对 -> 保持全局问题。
    $owner = Get-OwnerInstall $cfgRoot
    if ($owner) {
        Add-InstallProblem $owner $text
    } else {
        $script:Problems += $text
    }
}

function Show-Templates([string]$cfgRoot) {
    if (-not (Test-Path -LiteralPath $cfgRoot)) { return }
    $root = Join-Path $cfgRoot "plugins\cache\workbuddy-builtin"
    Write-Host ""
    Write-Host "templates : $root"
    if (-not (Test-Path -LiteralPath $root)) {
        Write-Host "  缓存目录不存在（客户端还没冷启动过）" -ForegroundColor Red
        Write-Host "  → 先启动一次客户端、进入任意模式、再从托盘完全退出，然后重跑 deploy.cmd" -ForegroundColor Yellow
        Add-TemplateProblem $cfgRoot "templates $($cfgRoot): 缓存目录不存在（冷启动缺失）"
        return
    }
    # 每个 welcomemode-<mode> 都必须有 prompt.tpl；少了就是部署不完整（不是"没有可检查的"）。
    $modes = @(Get-ChildItem -LiteralPath $root -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -like "welcomemode-*" })
    $missingModes = @()
    foreach ($m in $modes) {
        $hit = @(Get-ChildItem -LiteralPath $m.FullName -Filter prompt.tpl -Recurse -Depth 1 -ErrorAction SilentlyContinue)
        if ($hit.Count -eq 0) { $missingModes += $m.Name }
    }
    if ($modes.Count -gt 0) {
        if ($missingModes.Count -eq 0) {
            Write-Host ("  welcomemode : {0} 个插件均含 prompt.tpl" -f $modes.Count) -ForegroundColor Green
        } else {
            Write-Host ("  welcomemode : 缺 prompt.tpl -> {0}" -f ($missingModes -join ", ")) -ForegroundColor Red
            Add-TemplateProblem $cfgRoot "templates $($cfgRoot): welcomemode 缺 prompt.tpl ($($missingModes -join ','))"
        }
    } else {
        Write-Host "  welcomemode : 没有任何 welcomemode-* 插件缓存" -ForegroundColor Red
        Add-TemplateProblem $cfgRoot "templates $($cfgRoot): 没有任何 welcomemode-* 插件缓存"
    }

    $tpls = @(Get-ChildItem -LiteralPath $root -Filter prompt.tpl -Recurse -Depth 3 -ErrorAction SilentlyContinue)
    if ($tpls.Count -eq 0) {
        Write-Host "  没有任何 prompt.tpl（客户端还没冷启动过）" -ForegroundColor Red
        Add-TemplateProblem $cfgRoot "templates $($cfgRoot): 没有任何 prompt.tpl"
        return
    }
    $tags = @("tool_usage_policy", "result_presentation", "final_answer_instructions",
              "task_management", "agent_skills", "quality_policy", "agent_loop", "regional_conventions")
    $byVer = @{}
    foreach ($f in $tpls) {
        $ver = Split-Path -Leaf (Split-Path -Parent $f.FullName)
        if (-not $byVer.ContainsKey($ver)) { $byVer[$ver] = @{ Files = 0; Hits = 0; Detail = @() } }
        $byVer[$ver].Files++
        $text = [IO.File]::ReadAllText($f.FullName)
        foreach ($tag in $tags) {
            $n = ([regex]::Matches($text, "<" + $tag)).Count
            if ($n -gt 0) { $byVer[$ver].Hits += $n; $byVer[$ver].Detail += ("$tag=$n") }
        }
    }
    foreach ($ver in ($byVer.Keys | Sort-Object)) {
        $v = $byVer[$ver]
        $ok = ($v.Hits -eq 0)
        $line = "  {0}  files={1} removed_hits={2} {3}" -f $ver, $v.Files, $v.Hits, ($v.Detail -join ",")
        Write-Host $line -ForegroundColor $(if ($ok) { "Green" } else { "Red" })
        if (-not $ok) { Add-TemplateProblem $cfgRoot "templates $($cfgRoot) ${ver}: removed_hits=$($v.Hits)" }
    }
}

# 模板问题按配置目录归属安装：用 product.json 的 dataFolderName 反查（安装 -> 数据目录名），
# 命中就把问题记到该安装名下；认不出归属的（没有配对 / 多套共用一个名字）保持全局问题。
# 与 target.ps1 的 Get-PairedConfigDir / Get-DataFolderName 同源思路。
function Get-OwnerInstall([string]$cfgRoot) {
    if (-not $cfgRoot) { return "" }
    $leaf = Split-Path -Leaf $cfgRoot
    $hits = @()
    foreach ($r in @($script:InstallsSeen)) {
        if (-not $r) { continue }
        $folder = Get-DataFolderName $r
        if ($folder -and $leaf -ieq $folder) { $hits += $r }
    }
    if ($hits.Count -eq 1) { return $hits[0] }
    return ""
}

# 像不像客户端数据目录：有 plugins / sessions / traces / settings.json 之一。
# 只看名字 .workbuddy* 会把辅助目录（例如只放 connector-keys 的 .workbuddy-key-fallback）
# 当成数据目录，然后误报"缓存冷启动缺失"。判据与 doctor.py 的 _is_client_data_dir() 一致。
function Test-ClientDataDir([string]$path) {
    if (-not (Test-Path -LiteralPath $path -PathType Container)) { return $false }
    foreach ($rel in @("plugins", "sessions", "traces", "settings.json")) {
        if (Test-Path -LiteralPath (Join-Path $path $rel)) { return $true }
    }
    return $false
}

# 配置目录不写死名字：.workbuddy / .workbuddy-ai / 以及任何像数据目录的 .workbuddy* 变体都算，
# 再加上显式 WORKBUDDY_CONFIG_DIR。与 doctor.py 的 config_dirs() 同源思路。
function Get-ConfigDirs {
    $out = New-Object System.Collections.Generic.List[string]
    if ($env:WORKBUDDY_CONFIG_DIR -and (Test-ClientDataDir $env:WORKBUDDY_CONFIG_DIR)) {
        $out.Add($env:WORKBUDDY_CONFIG_DIR)
    }
    foreach ($name in @(".workbuddy", ".workbuddy-ai")) {
        $p = Join-Path $env:USERPROFILE $name
        if (Test-ClientDataDir $p) { $out.Add($p) }
    }
    if ($env:USERPROFILE) {
        Get-ChildItem -LiteralPath $env:USERPROFILE -Directory -Filter ".workbuddy*" -Force -ErrorAction SilentlyContinue |
            ForEach-Object { if (Test-ClientDataDir $_.FullName) { $out.Add($_.FullName) } }
    }
    return @($out | Select-Object -Unique)
}

Write-Host "== WorkBuddy prompt doctor (PowerShell fallback, no Python) =="
Write-Host ("time    : {0}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
$script:Problems = @()
$script:Warnings = @()
$script:InstallProblems = @{}
$script:InstallWarnings = @{}
$script:InstallsSeen = @()
$script:InstallState = @{}
$script:DoctorPythonResolved = $false
$script:DoctorPython = ""
$configs = @(Get-ConfigDirs)
$installs = @(if ($Install.Count -gt 0) { $Install } else { @(Find-Installs) })
$script:InstallsSeen = $installs
Write-Host ("config  : {0}" -f $(if ($configs.Count) { $configs -join ", " } else { "NOT FOUND" }))
if ($configs.Count -eq 0) {
    Write-Host "  一个 .workbuddy* 数据目录都没有：客户端还没启动过（缓存层无从检查）" -ForegroundColor Red
    $script:Problems += "config: 没有任何 .workbuddy* 数据目录"
}
foreach ($c in $configs) { Show-Templates $c }

if (-not $installs -or $installs.Count -eq 0) {
    Write-Host ""
    Write-Host "install : NOT FOUND" -ForegroundColor Red
    Write-Host "  找过：WORKBUDDY_INSTALL / 卸载注册表 / 运行中进程 / 开始菜单快捷方式 /"
    Write-Host "        <盘>:\Program Files|Program Files (x86)|Apps|Programs|Software|Users\*\AppData\Local\Programs|"
    Write-Host "        %LOCALAPPDATA%\WorkBuddy|<盘>:\*WorkBuddy*（深度 2）"
    Write-Host "  装在别处时：scripts\doctor.cmd --install `"<安装根目录>`""
    exit 1
}
$installState = @{}
foreach ($r in $installs) { $installState[$r] = Show-Install $r }

Write-Host ""
$proc = Get-CimInstance Win32_Process | Where-Object { $_.Name -match "WorkBuddy|codebuddy" }
if ($proc) {
    Write-Host "process :"
    foreach ($p in $proc) { Write-Host ("  {0} pid={1} 启动={2}" -f $p.Name, $p.ProcessId, $p.CreationDate) }
} else {
    Write-Host "process : 未运行"
}
Write-Host ""
Write-Host "!! 验证必须新开空白会话：旧会话历史里本来就存着旧块。" -ForegroundColor Yellow

# 当前在用哪一套：有进程在跑 -> 它所在的安装；否则不猜（与 doctor.py 的规则一致）。
$activeInstalls = @()
if ($proc) {
    foreach ($p in $proc) {
        if (-not $p.ExecutablePath) { continue }
        foreach ($r in $installs) {
            if ($p.ExecutablePath.ToLowerInvariant().StartsWith($r.TrimEnd('\').ToLowerInvariant() + "\")) {
                if ($activeInstalls -notcontains $r) { $activeInstalls += $r }
            }
        }
    }
}
# 归属到安装的问题（按安装根精确记账，不做子串匹配——WorkBuddy 是 WorkBuddyAI 的前缀，
# 旧实现 $line.Contains(" $r") 会让前者的失败误算到后者头上）；认不出归属的才是全局问题。
$script:InstallState = $installState
$attributed = @{}
foreach ($r in $installs) {
    if ($script:InstallProblems.ContainsKey($r) -and @($script:InstallProblems[$r]).Count -gt 0) {
        $attributed[$r] = @($script:InstallProblems[$r])
    }
}
$attributedLines = @()
foreach ($r in $attributed.Keys) { $attributedLines += @($attributed[$r]) }
$globalProblems = @($script:Problems | Where-Object { $attributedLines -notcontains $_ })

# 安装级严重度：FAIL = 打不全；WARN = 归属到该安装的问题（v3 残缺 / lite-only / 模板层问题）。
# v3 残缺在结论行标 WARN，但计入失败口径（D5d）。
$severity = @{}
foreach ($r in $installs) { $severity[$r] = Get-InstallSeverity $r }

Write-Host ""
Write-Host "[每套安装的结论]"
foreach ($r in $installs) {
    $tag = if ($activeInstalls -contains $r) { "当前使用" } elseif ($activeInstalls.Count -gt 0) { "未在使用" } else { "" }
    $state = $severity[$r]
    $note = ""
    $notes = @(@($script:InstallWarnings[$r]) | Where-Object { $_ })
    if ($notes.Count -gt 0) { $note = "  （" + ($notes -join "；") + "，未完全生效）" }
    if ($state -eq "FAIL" -and -not ($installs.Count -eq 1 -or ($activeInstalls.Count -gt 0 -and ($activeInstalls -contains $r)))) {
        $state = "WARN"
    }
    Write-Host ("  {0,-6} {1}  {2}{3}" -f $state, $r, $tag, $note)
}

# 失败口径与 doctor.py 的 decide() 一致：
#   有 active     -> 只有 active 里的安装失败才 hard，其余 soft（WARN，不影响退出码）
#   没有 active   -> 全部安装都失败才 hard（无法判断在用哪套，保守判失败）；部分失败 -> soft
#   唯一一套安装  -> 它的失败就是 hard（与 doctor.py 的 active 推断一致）
$hard = @()
$warnInstalls = @()
foreach ($r in $installs) {
    if ($severity[$r] -eq "PASS") { continue }
    if ($activeInstalls.Count -gt 0) {
        if ($activeInstalls -contains $r) { $hard += $r } else { $warnInstalls += $r }
    } elseif ($installs.Count -eq 1) {
        $hard += $r
    } elseif (@($installs | Where-Object { $severity[$_] -ne "PASS" }).Count -ge $installs.Count) {
        $hard += $r
    } else {
        $warnInstalls += $r
    }
}
$hardProblems = @($globalProblems)
foreach ($r in $hard) { if ($attributed.ContainsKey($r)) { $hardProblems += @($attributed[$r]) } }

if ($hard.Count -gt 0 -or $globalProblems.Count -gt 0) {
    Write-Host ""
    Write-Host ("结论 : NOT EFFECTIVE（当前使用的安装没打全 / 缓存层缺失 {0} 项）" -f ($hardProblems.Count)) -ForegroundColor Red
    foreach ($p in $hardProblems) { Write-Host ("  - {0}" -f $p) -ForegroundColor Red }
    if ($warnInstalls.Count -gt 0) {
        Write-Host ("  另有 {0} 套未在使用的安装没打全（WARN，不计入失败）：{1}" -f $warnInstalls.Count, ($warnInstalls -join ", ")) -ForegroundColor Yellow
    }
    exit 1
}
if ($warnInstalls.Count -gt 0 -or $script:Warnings.Count -gt 0) {
    Write-Host ""
    # 措辞与 doctor.py 的 decide() 对齐：
    #   有 active + soft          -> EFFECTIVE（"当前使用的安装已生效；另有 N 套未打全"）
    #   无 active + soft          -> PARTIAL（"本机没有运行中的进程，无法判断你在用哪套"）
    # 两者退出码都是 0（只有 NOT EFFECTIVE 才 1），与 doctor.py 一致。
    if ($warnInstalls.Count -gt 0 -and $activeInstalls.Count -gt 0) {
        Write-Host ("结论 : EFFECTIVE（当前使用的安装已生效；另有 {0} 套未在使用、没打全，不影响使用：{1}）" -f $warnInstalls.Count, ($warnInstalls -join ", ")) -ForegroundColor Yellow
    } elseif ($warnInstalls.Count -gt 0) {
        Write-Host ("结论 : PARTIAL（本机没有运行中的进程，无法判断你在用哪套；有 {0} 套安装没打全：{1}）" -f $warnInstalls.Count, ($warnInstalls -join ", ")) -ForegroundColor Yellow
    } else {
        Write-Host "结论 : EFFECTIVE（有 WARN，见下）。" -ForegroundColor Yellow
    }
    foreach ($w in $script:Warnings) { Write-Host ("  - {0}" -f $w) -ForegroundColor Yellow }
    exit 0
}
Write-Host ""
Write-Host "结论 : 缓存层与 CLI / asar 层均未发现缺失（详细计数见上）。" -ForegroundColor Green
exit 0
