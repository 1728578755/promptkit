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

function Show-Install([string]$root) {
    Write-Host ""
    Write-Host "install : $root"
    $asar = Join-Path $root "resources\app.asar"
    $size = (Get-Item -LiteralPath $asar).Length
    $raw = Test-Marker $asar 't.currentTime&&(r.current_time=t.currentTime)'
    $patched = Test-Marker $asar ('0' + (' ' * 12) + '&&(r.current_time=t.currentTime)')
    $state = if ($patched -and -not $raw) { "patched" } elseif ($raw) { "RAW" } else { "anchor-missing" }
    $color = if ($state -eq "patched") { "Green" } else { "Red" }
    Write-Host ("  asar  : {0:N0} bytes  current-time-cloud={1}" -f $size, $state) -ForegroundColor $color

    $dist = Join-Path $root "resources\app.asar.unpacked\cli\dist"
    if (-not (Test-Path -LiteralPath $dist)) {
        Write-Host "  cli   : app.asar.unpacked\cli\dist 不存在" -ForegroundColor Red
        return
    }
    $found = 0
    foreach ($c in (Get-ChildItem -LiteralPath $dist -Filter *.mjs -Recurse -Depth 4 | Where-Object { $_.Length -lt 24MB })) {
        $s = [IO.File]::ReadAllText($c.FullName)
        if (-not ($s.Contains("modelInput.instructions") -and $s.Contains("modelInput.input"))) { continue }
        $found++
        $helper = $s.Contains("__wbTrimV3")
        $scrub = $s.Contains("__wbScrubFetch")
        $blank = $s.Contains("__wbBlank")
        $anchors = ([regex]::Matches($s, [regex]::Escape('systemInstructions:__wbStripLegacyAny('))).Count
        $inputs = ([regex]::Matches($s, [regex]::Escape('input:__wbCleanRuntimeInput('))).Count
        $ok = $helper -and $scrub -and $blank -and ($anchors -eq 2) -and ($inputs -eq 2)
        $c2 = if ($ok) { "Green" } else { "Red" }
        Write-Host ("  cli   : {0}  {1:N0} bytes  修改={2}" -f $c.Name, $c.Length, $c.LastWriteTime) -ForegroundColor $c2
        Write-Host ("          helper={0} scrub={1} blank={2} instructions={3} input={4} => {5}" -f `
            $helper, $scrub, $blank, $anchors, $inputs, $(if ($ok) { "PATCHED" } else { "NOT FULLY PATCHED" })) -ForegroundColor $c2
    }
    if ($found -eq 0) { Write-Host "  cli   : 没有承载 modelInput 的 chunk" -ForegroundColor Red }
}

function Show-Templates([string]$cfgRoot) {
    if (-not (Test-Path -LiteralPath $cfgRoot)) { return }
    $root = Join-Path $cfgRoot "plugins\cache\workbuddy-builtin"
    Write-Host ""
    Write-Host "templates : $root"
    if (-not (Test-Path -LiteralPath $root)) {
        Write-Host "  缓存目录不存在（客户端还没冷启动过）" -ForegroundColor Red
        return
    }
    $tpls = @(Get-ChildItem -LiteralPath $root -Filter prompt.tpl -Recurse -Depth 3 -ErrorAction SilentlyContinue)
    if ($tpls.Count -eq 0) {
        Write-Host "  没有任何 prompt.tpl（客户端还没冷启动过）" -ForegroundColor Red
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
    }
}

Write-Host "== WorkBuddy prompt doctor (PowerShell fallback, no Python) =="
Write-Host ("time    : {0}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
$configs = @()
foreach ($name in @(".workbuddy", ".workbuddy-ai")) {
    $c = Join-Path $env:USERPROFILE $name
    if (Test-Path -LiteralPath $c) { $configs += $c }
}
Write-Host ("config  : {0}" -f $(if ($configs.Count) { $configs -join ", " } else { "NOT FOUND" }))
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
foreach ($r in $installs) { Show-Install $r }

Write-Host ""
$proc = Get-CimInstance Win32_Process | Where-Object { $_.Name -match "WorkBuddy|codebuddy" }
if ($proc) {
    Write-Host "process :"
    foreach ($p in $proc) { Write-Host ("  {0} pid={1} 启动={2}" -f $p.Name, $p.ProcessId, $p.CreationDate) }
} else {
    Write-Host "process : 未运行"
}
exit 0
