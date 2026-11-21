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
    foreach ($letter in @("C", "D", "E", "F", "G", "H")) {
        $drive = "${letter}:\"
        if (-not (Test-Path -LiteralPath $drive)) { continue }
        $bases = @("Program Files", "Program Files (x86)", "Apps", "Programs", "Software") |
            ForEach-Object { Join-Path $drive $_ }
        $users = Join-Path $drive "Users"
        if (Test-Path -LiteralPath $users) {
            Get-ChildItem -LiteralPath $users -Directory | Select-Object -First 20 | ForEach-Object {
                $bases += (Join-Path $_.FullName "AppData\Local\Programs")
            }
        }
        foreach ($b in $bases) {
            if (-not (Test-Path -LiteralPath $b)) { continue }
            Get-ChildItem -LiteralPath $b -Directory -Depth 1 |
                Where-Object { $_.Name -match "WorkBuddy" -or (Test-Path -Path (Join-Path $_.FullName "WorkBuddy*.exe")) } |
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

# 返回 "PASS" / "WARN" / "FAIL"：FAIL 写进 $script:Problems，WARN 写进 $script:Warnings。
# 只打了免锚点轻量出口清理（wb-fix-lite）时是 WARN，不是 FAIL —— 与 doctor.py 同一口径。
function Show-Install([string]$root) {
    Write-Host ""
    Write-Host "install : $root"
    $healthy = $true
    $asar = Join-Path $root "resources\app.asar"
    if (-not (Test-Path -LiteralPath $asar)) {
        Write-Host "  asar  : 不存在 $asar" -ForegroundColor Red
        $script:Problems += "asar $($root): 文件不存在"
        return "FAIL"
    }
    $size = (Get-Item -LiteralPath $asar).Length
    $raw = Test-Marker $asar 't.currentTime&&(r.current_time=t.currentTime)'
    $patched = Test-Marker $asar ('0' + (' ' * 12) + '&&(r.current_time=t.currentTime)')
    $state = if ($patched -and -not $raw) { "patched" } elseif ($raw) { "RAW" } else { "anchor-missing" }
    $color = if ($state -eq "patched") { "Green" } else { "Red" }
    Write-Host ("  asar  : {0:N0} bytes  current-time-cloud={1}" -f $size, $state) -ForegroundColor $color
    if ($state -ne "patched") {
        $healthy = $false
        $script:Problems += "asar $($root): current-time-cloud=$state"
    }

    $dist = Join-Path $root "resources\app.asar.unpacked\cli\dist"
    if (-not (Test-Path -LiteralPath $dist)) {
        Write-Host "  cli   : app.asar.unpacked\cli\dist 不存在" -ForegroundColor Red
        $script:Problems += "cli $($root): app.asar.unpacked\cli\dist 不存在"
        return "FAIL"
    }
    $found = 0
    foreach ($c in (Get-ChildItem -LiteralPath $dist -Filter *.mjs -Recurse -Depth 4 | Where-Object { $_.Length -lt 24MB })) {
        $s = [IO.File]::ReadAllText($c.FullName)
        if (-not ($s.Contains("modelInput.instructions") -and $s.Contains("modelInput.input"))) { continue }
        $found++
        $helper = $s.Contains("__wbTrimV3")
        $scrub = $s.Contains("__wbScrubFetch")
        $blank = $s.Contains("__wbBlank")
        # 只打了免锚点轻量出口清理（wb-fix-lite）：有 __wbTrimLite，但没有完整 helper 的写法。
        # 这不是"没打补丁"，而是"只打了出口清理"——报 WARN 而不是 FAIL，和 doctor.py 一致。
        $liteOnly = $s.Contains("__wbTrimLite") -and -not ($helper -and $scrub -and $blank)
        $anchors = ([regex]::Matches($s, [regex]::Escape('systemInstructions:__wbStripLegacyAny('))).Count
        $inputs = ([regex]::Matches($s, [regex]::Escape('input:__wbCleanRuntimeInput('))).Count
        # 不数"打了几处"（不同构建的分支数不同）：看还有没有没包上的调用点。
        $rawIns = ([regex]::Matches($s, '(?<![A-Za-z0-9_$])systemInstructions:(?!__wbStripLegacyAny\()[A-Za-z$_][\w$]*\.modelInput\.instructions,')).Count
        $rawIn = ([regex]::Matches($s, '(?<![A-Za-z0-9_$])input:(?!__wbCleanRuntimeInput\()[A-Za-z$_][\w$]*\.modelInput\.input,')).Count
        $ok = $helper -and $scrub -and $blank -and ($anchors -ge 1) -and ($inputs -ge 1) -and ($rawIns -eq 0) -and ($rawIn -eq 0)
        $verdict = if ($ok) { "PATCHED" } elseif ($liteOnly) { "LITE ONLY" } else { "NOT FULLY PATCHED" }
        $c2 = if ($ok) { "Green" } elseif ($liteOnly) { "Yellow" } else { "Red" }
        Write-Host ("  cli   : {0}  {1:N0} bytes  修改={2}" -f $c.Name, $c.Length, $c.LastWriteTime) -ForegroundColor $c2
        Write-Host ("          helper={0} scrub={1} blank={2} instructions={3} input={4} raw={5}/{6} => {7}" -f `
            $helper, $scrub, $blank, $anchors, $inputs, $rawIns, $rawIn, $verdict) -ForegroundColor $c2
        if (-not $ok -and -not $liteOnly) {
            $healthy = $false
            $script:Problems += "cli $($root) $($c.Name): helper=$helper scrub=$scrub blank=$blank raw=$rawIns/$rawIn"
        } elseif (-not $ok -and $liteOnly) {
            $script:Warnings += "cli $($root) $($c.Name): 仅轻量出口清理（fetch-scrub 未打）"
        }
    }
    if ($found -eq 0) {
        Write-Host "  cli   : 没有承载 modelInput 的 chunk" -ForegroundColor Red
        $script:Problems += "cli $($root): 没有承载 modelInput 的 chunk"
        return "FAIL"
    }
    if (-not $healthy) { return "FAIL" }
    if (@($script:Warnings | Where-Object { $_ -like "cli $root *" }).Count -gt 0) { return "WARN" }
    return "PASS"
}

function Show-Templates([string]$cfgRoot) {
    if (-not (Test-Path -LiteralPath $cfgRoot)) { return }
    $root = Join-Path $cfgRoot "plugins\cache\workbuddy-builtin"
    Write-Host ""
    Write-Host "templates : $root"
    if (-not (Test-Path -LiteralPath $root)) {
        Write-Host "  缓存目录不存在（客户端还没冷启动过）" -ForegroundColor Red
        Write-Host "  → 先启动一次客户端、进入任意模式、再从托盘完全退出，然后重跑 deploy.cmd" -ForegroundColor Yellow
        $script:Problems += "templates $($cfgRoot): 缓存目录不存在（冷启动缺失）"
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
            $script:Problems += "templates $($cfgRoot): welcomemode 缺 prompt.tpl ($($missingModes -join ','))"
        }
    } else {
        Write-Host "  welcomemode : 没有任何 welcomemode-* 插件缓存" -ForegroundColor Red
        $script:Problems += "templates $($cfgRoot): 没有任何 welcomemode-* 插件缓存"
    }

    $tpls = @(Get-ChildItem -LiteralPath $root -Filter prompt.tpl -Recurse -Depth 3 -ErrorAction SilentlyContinue)
    if ($tpls.Count -eq 0) {
        Write-Host "  没有任何 prompt.tpl（客户端还没冷启动过）" -ForegroundColor Red
        $script:Problems += "templates $($cfgRoot): 没有任何 prompt.tpl"
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
        if (-not $ok) { $script:Problems += "templates $($cfgRoot) ${ver}: removed_hits=$($v.Hits)" }
    }
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
$configs = @(Get-ConfigDirs)
Write-Host ("config  : {0}" -f $(if ($configs.Count) { $configs -join ", " } else { "NOT FOUND" }))
if ($configs.Count -eq 0) {
    Write-Host "  一个 .workbuddy* 数据目录都没有：客户端还没启动过（缓存层无从检查）" -ForegroundColor Red
    $script:Problems += "config: 没有任何 .workbuddy* 数据目录"
}
foreach ($c in $configs) { Show-Templates $c }

$installs = if ($Install.Count -gt 0) { $Install } else { Find-Installs }
if (-not $installs -or $installs.Count -eq 0) {
    Write-Host ""
    Write-Host "install : NOT FOUND" -ForegroundColor Red
    Write-Host "  找过：WORKBUDDY_INSTALL / 卸载注册表 / 运行中进程 / 开始菜单快捷方式 /"
    Write-Host "        <盘>:\Program Files|Program Files (x86)|Apps|Programs|Software|Users\*\AppData\Local\Programs"
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
$failingInstalls = @($installs | Where-Object { $installState[$_] -eq "FAIL" })
# 缓存层的问题不与安装绑定（数据目录可能同时被多套安装读取），先算全局。
$globalProblems = @($script:Problems | Where-Object { $_ -notmatch '^(cli|asar) ' })

Write-Host ""
Write-Host "[每套安装的结论]"
foreach ($r in $installs) {
    $tag = if ($activeInstalls -contains $r) { "当前使用" } elseif ($activeInstalls.Count -gt 0) { "未在使用" } else { "" }
    $state = $installState[$r]
    if ($state -eq "FAIL") {
        # 与下面的汇总结论同一规则：未在使用的安装降级为 WARN；判断不出来的（一套都没跑、
        # 也不是全部失败）同样只能 WARN。
        if ($installs.Count -eq 1 -or ($activeInstalls.Count -gt 0 -and ($activeInstalls -contains $r))) { $state = "FAIL" }
        else { $state = "WARN" }
    }
    Write-Host ("  {0,-6} {1}  {2}" -f $state, $r, $tag)
}

# 未在使用的安装没打全 = WARN（不把全局结论拖成 NOT EFFECTIVE）；当前使用的 / 唯一一套失败才是 FAIL。
$hard = @()
$warnInstalls = @()
foreach ($r in $failingInstalls) {
    if ($activeInstalls.Count -gt 0) {
        if ($activeInstalls -contains $r) { $hard += $r } else { $warnInstalls += $r }
    } elseif ($failingInstalls.Count -ge $installs.Count -or $installs.Count -eq 1) {
        $hard += $r
    } else {
        $warnInstalls += $r
    }
}
$hardProblems = @($globalProblems) + @($script:Problems | Where-Object {
        $line = $_
        $isHard = $false
        foreach ($r in $hard) { if ($line.EndsWith($r) -or $line.Contains(" $r") -or $line.Contains("$r ")) { $isHard = $true } }
        $isHard
    })

if ($hard.Count -gt 0 -or $globalProblems.Count -gt 0) {
    Write-Host ""
    Write-Host ("结论 : NOT EFFECTIVE（当前使用的安装没打全 / 缓存层缺失 {0} 项）" -f ($hardProblems.Count)) -ForegroundColor Red
    foreach ($p in $hardProblems) { Write-Host ("  - {0}" -f $p) -ForegroundColor Red }
    exit 1
}
if ($warnInstalls.Count -gt 0 -or $script:Warnings.Count -gt 0) {
    Write-Host ""
    if ($warnInstalls.Count -gt 0) {
        Write-Host ("结论 : 当前使用的安装已生效（WARN：{0} 套未在使用的安装没打全，不影响使用：{1}）" -f $warnInstalls.Count, ($warnInstalls -join ", ")) -ForegroundColor Yellow
    } else {
        Write-Host "结论 : 当前使用的安装已生效（有 WARN，见下）。" -ForegroundColor Yellow
    }
    foreach ($w in $script:Warnings) { Write-Host ("  - {0}" -f $w) -ForegroundColor Yellow }
    exit 0
}
Write-Host ""
Write-Host "结论 : 缓存层与 CLI / asar 层均未发现缺失（详细计数见上）。" -ForegroundColor Green
exit 0
