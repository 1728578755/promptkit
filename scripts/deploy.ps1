[CmdletBinding()]
param(
    # --- actions (one at a time; default = deploy) ---
    [switch]$Restore,
    [switch]$Verify,
    [switch]$Pull,
    [switch]$Asar,
    [switch]$AsarRestore,
    [switch]$CliPatch,
    [switch]$ListTargets,
    [switch]$List,

    # --- scope ---
    [string]$Target = "all",
    [switch]$Elevate,
    [switch]$DryRun,
    [switch]$SkipVerify,
    [switch]$Detailed,
    [switch]$NoRelaunch,

    # --- deploy options (forwarded to apply.ps1) ---
    [switch]$All,
    [string]$Template = "",
    [string]$Source = "",
    [string]$Skill = "",
    [switch]$UseCustom,
    [switch]$IncludeTemplates,
    [switch]$NoInstall,
    [switch]$NoPluginCache,
    [switch]$NoRegisterSkills,
    [ValidateSet("All", "Latest")]
    [string]$CacheVersions = "",
    [string]$WorkBuddyPath = "",
    [string]$PluginCacheRoot = "",
    [string]$ConfigDir = "",
    [string]$Overlay = "",
    [switch]$SkipProductOverrides,
    [switch]$SkipAsarPatch,
    [switch]$SkipCliPatch,

    # --- restore options (forwarded to restore.ps1) ---
    [string]$BackupPath = "",
    [switch]$Original,

    # --- verify options (forwarded to verify.ps1) ---
    [switch]$ShowSystemPrompt,
    [string]$Expect = "",
    [string]$Absent = "",
    [switch]$ListTraces,
    [string]$TraceFile = "",

    # --- pull options (forwarded to pull.ps1) ---
    [string]$Scope = "",
    [string]$Layout = "",
    [switch]$Force
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $ScriptRoot "..")).Path
$ps = Join-Path $PSHOME "powershell.exe"
if (-not (Test-Path -LiteralPath $ps)) {
    $cmd = Get-Command powershell.exe -ErrorAction SilentlyContinue
    if ($cmd) { $ps = $cmd.Source } else { $ps = "powershell.exe" }
}

. (Join-Path $ScriptRoot "target.ps1")

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}

$isAdmin = Test-Admin

# 把已解析的路径交给子脚本（apply/restore/verify/pull），子进程不再重复扫盘，
# 也不会因为环境差异解析到另一套安装。
function Set-TargetEnv($profile) {
    $key = ($profile.Name -replace '[^A-Za-z0-9]', '_').ToUpperInvariant()
    foreach ($pair in @(@("INSTALL", $profile.InstallPath), @("CONFIG", $profile.ConfigDir))) {
        $name = "Env:PROMPTKIT_TARGET_{0}_{1}" -f $key, $pair[0]
        if ($pair[1]) { Set-Item -Path $name -Value $pair[1] -ErrorAction SilentlyContinue }
        else { Remove-Item -Path $name -ErrorAction SilentlyContinue }
    }
}

# One action wins; order matters only for accidental combinations.
$action = "deploy"
if ($ListTargets) { $action = "list" }
elseif ($AsarRestore) { $action = "asar-restore" }
elseif ($CliPatch) { $action = "cli-patch" }
elseif ($Asar) { $action = "asar" }
elseif ($Restore) { $action = "restore" }
elseif ($Verify) { $action = "verify" }
elseif ($Pull) { $action = "pull" }


# --- elevation: ask once, then every target is written from the same elevated run ---
$needsAdmin = ((($action -eq "asar") -or ($action -eq "asar-restore") -or ($action -eq "cli-patch")) -and (-not $DryRun))
if (($Elevate -or $needsAdmin) -and -not $isAdmin) {
    $fwd = @()
    foreach ($k in @($PSBoundParameters.Keys)) {
        if ($k -eq 'Elevate') { continue }
        $v = $PSBoundParameters[$k]
        if ($v -is [switch]) { if ($v.IsPresent) { $fwd += "-$k" } }
        else { $fwd += "-$k"; $fwd += ('"{0}"' -f $v) }
    }
    if ($fwd -notcontains "-Elevate") { $fwd += "-Elevate" }
    $argLine = ('-NoProfile -ExecutionPolicy Bypass -File "{0}" ' -f $PSCommandPath) + ($fwd -join ' ')
    Write-Host "Requesting administrator privileges once (writes every detected install mirror)..." -ForegroundColor Yellow
    try {
        $p = Start-Process -FilePath $ps -Verb RunAs -ArgumentList $argLine -Wait -PassThru
        exit $p.ExitCode
    } catch {
        throw "Elevation was cancelled or blocked. Re-run from an administrator terminal, or drop -Elevate to update the cache only."
    }
}

# --- python: target's own bundled runtime first, never a hard-coded path ---
function Get-PythonExe([string]$configDir) {
    if ($env:WORKBUDDY_PYTHON) { return $env:WORKBUDDY_PYTHON }
    foreach ($cand in @("python", "python3", "py")) {
        $c = Get-Command $cand -ErrorAction SilentlyContinue
        if ($c -and $c.Source -notmatch 'WindowsApps') { return $c.Source }
    }
    $roots = @()
    if ($configDir) { $roots += (Join-Path $configDir "binaries\python\versions") }
    if ($env:USERPROFILE) { $roots += (Join-Path $env:USERPROFILE "miniforge3") }
    foreach ($root in $roots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }
        $hit = Get-ChildItem -LiteralPath $root -Filter "python.exe" -Recurse -Depth 2 -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notmatch 'WindowsApps' } | Select-Object -First 1
        if ($hit) { return $hit.FullName }
    }
    return $null
}

function Invoke-Python([string]$pyExe, [string[]]$pyArgs) {
    $prevEnc = [Console]::OutputEncoding
    $prevIo = $env:PYTHONIOENCODING
    try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
    $env:PYTHONIOENCODING = "utf-8"
    try {
        & $pyExe -X utf8 @pyArgs 2>&1 | ForEach-Object { Write-Host ([string]$_) }
    } finally {
        try { [Console]::OutputEncoding = $prevEnc } catch { }
        if ($null -eq $prevIo) { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue } else { $env:PYTHONIOENCODING = $prevIo }
    }
}

# Run a child script and echo its output through Write-Host. Without this the child's
# Write-Host text is swallowed when the parent console is redirected (CI, piping, IDE),
# and the summary ends up interleaved with blank padding lines.
function Invoke-Child([string[]]$argv) {
    $out = & $ps @argv 2>&1
    $code = $LASTEXITCODE
    foreach ($line in @($out)) {
        if ($null -eq $line) { continue }
        if ($line -is [System.Management.Automation.ErrorRecord]) {
            Write-Host $line.ToString() -ForegroundColor Red
        } else {
            Write-Host ([string]$line)
        }
    }
    return $code
}

function New-Result([string]$name, [string]$label, [string]$act) {
    return [pscustomobject]@{ Name = $name; Label = $label; Action = $act; Status = "SKIP"; Exit = 0; Note = "" }
}

function Add-Note($result, [string]$text) {
    if ($result.Note) { $result.Note = $result.Note + "; " + $text } else { $result.Note = $text }
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

function Assert-SkillSources {
    foreach ($name in $required) {
        $path = Join-Path $ProjectRoot "plugins\workbuddy-builtin\skills\$name\SKILL.md"
        if (-not (Test-Path -LiteralPath $path)) { throw "missing builtin skill source: $name ($path)" }
    }
}

function Invoke-ProductOverrides($profile, $result) {
    if ($SkipProductOverrides) { return }
    $ovDir = Join-Path $ProjectRoot "overrides\tools"
    if (-not (Test-Path -LiteralPath $ovDir)) { return }   # not shipped in the public package
    $ovFiles = @(Get-ChildItem -LiteralPath $ovDir -Filter "*.compact.json" -ErrorAction SilentlyContinue)
    if ($ovFiles.Count -eq 0) { return }
    if (-not $profile.InstallPath) { Add-Note $result "product.json skipped (no install)"; return }
    $py = Get-PythonExe $profile.ConfigDir
    if (-not $py) { Add-Note $result "product.json skipped (python not found)"; return }
    $ovArgs = @((Join-Path $ScriptRoot "apply-product-overrides.py"))
    foreach ($f in $ovFiles) { $ovArgs += @("--overrides", $f.FullName) }
    $ovArgs += @("--install", $profile.InstallPath)
    if ($DryRun) { $ovArgs += "--dry-run" }
    Invoke-Python $py $ovArgs
    if ($LASTEXITCODE -ne 0) {
        if ($isAdmin) { throw "product overrides failed for $($profile.Name) (exit $LASTEXITCODE)" }
        Add-Note $result "product.json needs -Elevate"
    }
}

function Invoke-AsarPatch($profile, $result) {
    if ($SkipAsarPatch -or $DryRun) { return }
    $py = Get-PythonExe $profile.ConfigDir
    $script = Join-Path $ScriptRoot "patch-asar.py"
    if (-not (Test-Path -LiteralPath $script)) { return }  # not shipped in the public package
    if (-not $profile.InstallPath) { return }
    $asar = Join-Path $profile.InstallPath "resources\app.asar"
    if (-not (Test-Path -LiteralPath $asar)) { return }
    if (-not $py) { Add-Note $result "app.asar skipped (python not found)"; return }
    $prevIo = $env:PYTHONIOENCODING
    $env:PYTHONIOENCODING = "utf-8"
    $asarOut = @()
    $asarArgs = @("-X", "utf8", $script, "--apply", "--asar", $asar)
    # 拉起统一由本脚本末尾做：patch-asar.py 自己也拉起的话，它启动的进程会赶在
    # uc-seen seed / verify 之前起来，等于让新进程读一个还没收口的状态。
    $asarArgs += "--no-launch"
    try {
        $asarOut = @(& $py @asarArgs 2>&1 | ForEach-Object { [string]$_ })
        $asarCode = $LASTEXITCODE
    } finally {
        if ($null -eq $prevIo) { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue } else { $env:PYTHONIOENCODING = $prevIo }
    }
    foreach ($line in $asarOut) { Write-Host $line }
    $asarText = $asarOut -join "`n"
    switch ($asarCode) {
        0 {
            if ($asarText -match 'already patched') { Add-Note $result "app.asar already patched" }
            else { Add-Note $result "app.asar patched" }
        }
        2 {
            Add-Note $result "app.asar anchors not found - this build changed; nothing written"
            $result.Status = "FAIL"
        }
        3 { Add-Note $result "app.asar needs -Elevate"; $result.Status = "FAIL" }
        4 {
            Add-Note $result "app.asar NOT written (app running; close WorkBuddy and rerun)"
            $result.Status = "FAIL"
        }
        default {
            if ($isAdmin) { throw "patch-asar.py failed for $($profile.Name) (exit $asarCode)" }
            Add-Note $result "app.asar exit $asarCode"
            $result.Status = "FAIL"
        }
    }

    # 收口：打完再读一遍真实状态，任何一条没 patched / integrity 或 exe 清单对不上，都不许算成功
    if ($asarCode -eq 0 -and -not $SkipAsarPatch) {
        $stArgs = @("-X", "utf8", $script, "--status", "--asar", $asar)
        $st = @(& $py @stArgs 2>&1 | ForEach-Object { [string]$_ })
        $stBad = @($st | Where-Object {
                $_ -match '^patch:\s+\S+\s+(raw|missing)\b' -or
                $_ -match '^integ:\s+MISMATCH' -or
                $_ -match '^exe sync:\s+MISMATCH' -or
                $_ -match '^exe:\s+不存在'
            })
        if ($stBad.Count -gt 0) {
            foreach ($b in $stBad) { Write-Host ("  " + $b) -ForegroundColor Red }
            Add-Note $result "app.asar 未达预期（patched/integ/exe 见上）"
            $result.Status = "FAIL"
        } else {
            $nPatched = @($st | Where-Object { $_ -match '^patch:\s+\S+\s+patched\b' }).Count
            Add-Note $result ("app.asar verified: {0} patches + integrity + exe manifest" -f $nPatched)
        }
    }
}

function Invoke-UcSeenSeed($profile, $result) {
    # user-context「只发一次」的磁盘标记：给补丁上线前就存在的会话补一次，
    # 否则它们重启后第一条消息仍会被整段注入。补丁每次 compose 都读盘，补完立即生效。
    if ($SkipAsarPatch -or $DryRun) { return }
    $py = Get-PythonExe $profile.ConfigDir
    $script = Join-Path $ScriptRoot "uc-seen.py"
    if (-not $py -or -not (Test-Path -LiteralPath $script)) { return }
    $seedArgs = @("-X", "utf8", $script, "--seed")
    if ($profile.ConfigDir) { $seedArgs += @("--config-dir", $profile.ConfigDir) }
    $prevIo = $env:PYTHONIOENCODING
    $env:PYTHONIOENCODING = "utf-8"
    $out = @()
    try {
        $out = @(& $py @seedArgs 2>&1 | ForEach-Object { [string]$_ })
    } finally {
        if ($null -eq $prevIo) { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue } else { $env:PYTHONIOENCODING = $prevIo }
    }
    foreach ($line in $out) { Write-Host $line }
    $summary = @($out | Where-Object { $_ -match '^seeded:' })
    if ($summary.Count -gt 0) { Add-Note $result ("uc-seen " + $summary[0]) }
    else { Add-Note $result "uc-seen seed skipped" }
}

function Invoke-DeployTarget($profile) {
    Set-TargetEnv $profile
    Set-WorkBuddyPython $profile
    $result = New-Result $profile.Name $profile.Label "deploy"
    if (-not $profile.Installed -and -not $profile.ConfigExists) {
        Add-Note $result "not installed"
        return $result
    }
    $writeInstall = (-not $NoInstall) -and $isAdmin
    $applyArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $ScriptRoot "apply.ps1"),
                   "-NoElevate", "-Target", $profile.Name)
    if ($All -or (-not $Template -and -not $Source -and -not $Skill)) { $applyArgs += "-All" }
    if ($Template) { $applyArgs += @("-Template", $Template) }
    if ($Source) { $applyArgs += @("-Source", $Source) }
    if ($Skill) { $applyArgs += @("-Skill", $Skill) }
    if ($WorkBuddyPath) { $applyArgs += @("-WorkBuddyPath", $WorkBuddyPath) }
    if ($PluginCacheRoot) { $applyArgs += @("-PluginCacheRoot", $PluginCacheRoot) }
    if ($ConfigDir) { $applyArgs += @("-ConfigDir", $ConfigDir) }
    if ($Overlay) { $applyArgs += @("-Overlay", $Overlay) }
    if ($CacheVersions) { $applyArgs += @("-CacheVersions", $CacheVersions) }
    if ($UseCustom) { $applyArgs += "-UseCustom" }
    if ($IncludeTemplates) { $applyArgs += "-IncludeTemplates" }
    if ($NoPluginCache) { $applyArgs += "-NoPluginCache" }
    if ($NoRegisterSkills) { $applyArgs += "-NoRegisterSkills" }
    if ($DryRun) { $applyArgs += "-DryRun" }
    if (-not $writeInstall) { $applyArgs += "-NoInstall" }

    $code = Invoke-Child $applyArgs
    if ($code -ne 0) { throw "apply.ps1 failed for $($profile.Name) (exit $code)" }

    # 缓存层收口：空缓存 = 什么都没写进去（新电脑最常见），必须报出来而不是打印 OK。
    if (-not $NoPluginCache -and -not $DryRun) {
        $cs = Get-CacheTemplateStats $profile
        if (-not $cs.Exists -or $cs.Files -eq 0) {
            Add-Note $result ("plugin cache empty ({0}) - start WorkBuddy once, enter any mode, quit completely (tray), then rerun" -f $cs.Root)
            $result.Status = "FAIL"
        } elseif ($cs.Hits -gt 0) {
            Add-Note $result ("cache templates still carry {0} removed-block hit(s)" -f $cs.Hits)
            $result.Status = "FAIL"
        } else {
            Add-Note $result ("cache templates trimmed: {0} files, removed_hits=0" -f $cs.Files)
        }
    }

    Invoke-ProductOverrides $profile $result
    if (-not $SkipCliPatch) {
        if (-not $isAdmin) { Add-Note $result "cli trim needs -Elevate (Program Files)" }
        else {
            $cli = Invoke-CliPatchTarget $profile
            if ($cli.Note) { Add-Note $result ("cli: " + $cli.Note) }
            if ($cli.Status -eq "FAIL") { $result.Status = "FAIL" }
        }
    }
    Invoke-AsarPatch $profile $result
    Invoke-UcSeenSeed $profile $result

    if (-not $DryRun -and -not $SkipVerify) {
        $verifyArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $ScriptRoot "verify.ps1"),
                        "-Target", $profile.Name, "-CheckIncludes")
        if ($ConfigDir) { $verifyArgs += @("-ConfigDir", $ConfigDir) }
        if ($PluginCacheRoot) { $verifyArgs += @("-PluginCacheRoot", $PluginCacheRoot) }
        $verifyExit = Invoke-Child $verifyArgs
        if ($verifyExit -eq 1) {
            if (-not $isAdmin) { Add-Note $result "verify: install side unavailable (cache verified)" }
            else { throw "verify.ps1 reported a failure for $($profile.Name)" }
        } elseif ($verifyExit -eq 2) {
            Add-Note $result "verify: marker not covered by captured prefix"
        }
    }

    if ($profile.Installed) {
        $installSkills = Join-Path $profile.InstallPath "resources\app.asar.unpacked\resources\plugins\workbuddy-builtin\skills"
        $missing = @()
        foreach ($name in $required) {
            if (-not (Test-Path -LiteralPath (Join-Path $installSkills "$name\SKILL.md"))) { $missing += $name }
        }
        if ($missing.Count -gt 0) {
            Add-Note $result ("skills inactive {0}/{1}: {2}" -f $missing.Count, $required.Count, ($missing -join ','))
        } else {
            Add-Note $result ("skills active {0}/{0}" -f $required.Count)
        }
    } elseif ($writeInstall) {
        Add-Note $result "no install mirror, cache only"
    }
    if (-not $writeInstall) { Add-Note $result "cache only" }
    $result.Status = "OK"
    return $result
}

function Invoke-RestoreTarget($profile) {
    Set-TargetEnv $profile
    $result = New-Result $profile.Name $profile.Label "restore"
    $restoreArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $ScriptRoot "restore.ps1"),
                     "-NoElevate", "-Target", $profile.Name)
    if ($BackupPath) { $restoreArgs += @("-BackupPath", $BackupPath) }
    elseif ($Template) { $restoreArgs += @("-Template", $Template) }
    else { $restoreArgs += "-All" }
    if ($Original) { $restoreArgs += "-Original" }
    if ($List) { $restoreArgs += "-List" }
    if ($DryRun) { $restoreArgs += "-DryRun" }
    if ($ConfigDir) { $restoreArgs += @("-ConfigDir", $ConfigDir) }
    if ($PluginCacheRoot) { $restoreArgs += @("-PluginCacheRoot", $PluginCacheRoot) }
    if ($WorkBuddyPath) { $restoreArgs += @("-WorkBuddyPath", $WorkBuddyPath) }

    $code = Invoke-Child $restoreArgs
    if ($code -ne 0) { throw "restore.ps1 failed for $($profile.Name) (exit $code)" }
    $result.Status = "OK"
    if ($List) { Add-Note $result "backups listed" }
    elseif ($DryRun) { Add-Note $result "dry-run: plan only" }
    elseif ($Original) { Add-Note $result "original baseline restored" }
    else { Add-Note $result "latest backup restored" }
    return $result
}

function Invoke-VerifyTarget($profile) {
    Set-TargetEnv $profile
    $result = New-Result $profile.Name $profile.Label "verify"
    $verifyArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $ScriptRoot "verify.ps1"),
                    "-Target", $profile.Name, "-CheckIncludes")
    if ($ShowSystemPrompt) { $verifyArgs += "-ShowSystemPrompt" }
    if ($Expect) { $verifyArgs += @("-Expect", $Expect) }
    if ($Absent) { $verifyArgs += @("-Absent", $Absent) }
    if ($ListTraces) { $verifyArgs += "-ListTraces" }
    if ($TraceFile) { $verifyArgs += @("-TraceFile", $TraceFile) }
    if ($ConfigDir) { $verifyArgs += @("-ConfigDir", $ConfigDir) }
    if ($PluginCacheRoot) { $verifyArgs += @("-PluginCacheRoot", $PluginCacheRoot) }
    $code = Invoke-Child $verifyArgs
    if ($code -eq 0) { $result.Status = "OK" }
    elseif ($code -eq 2) { $result.Status = "WARN"; Add-Note $result "marker not covered by captured prefix" }
    else { throw "verify.ps1 failed for $($profile.Name) (exit $code)" }
    return $result
}

function Invoke-PullTarget($profile) {
    Set-TargetEnv $profile
    $result = New-Result $profile.Name $profile.Label "pull"
    $pullArgs = @("-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $ScriptRoot "pull.ps1"), "-Target", $profile.Name)
    if ($Scope) { $pullArgs += @("-Scope", $Scope) }
    if ($Layout) { $pullArgs += @("-Layout", $Layout) }
    if ($Force) { $pullArgs += "-Force" }
    if ($ConfigDir) { $pullArgs += @("-ConfigDir", $ConfigDir) }
    if ($PluginCacheRoot) { $pullArgs += @("-PluginCacheRoot", $PluginCacheRoot) }
    $code = Invoke-Child $pullArgs
    if ($code -ne 0) { throw "pull.ps1 failed for $($profile.Name) (exit $code)" }
    $result.Status = "OK"
    return $result
}

function Invoke-AsarTarget($profile, [string]$mode) {
    Set-TargetEnv $profile
    $result = New-Result $profile.Name $profile.Label ("asar-" + $mode)
    if (-not $profile.Installed) { Add-Note $result "not installed"; return $result }
    $script = Join-Path $ScriptRoot "patch-asar.py"
    if (-not (Test-Path -LiteralPath $script)) { Add-Note $result "patch-asar.py not present"; return $result }
    $py = Get-PythonExe $profile.ConfigDir
    if (-not $py) { throw "python not found for $($profile.Name); set WORKBUDDY_PYTHON" }
    $asar = Join-Path $profile.InstallPath "resources\app.asar"
    if (-not (Test-Path -LiteralPath $asar)) { throw "app.asar not found under $($profile.InstallPath)" }

    if ($mode -eq "apply" -and -not $DryRun) {
        $exe = if ($profile.ExeName) { [IO.Path]::GetFileNameWithoutExtension($profile.ExeName) } else { "WorkBuddy" }
        $running = @(Get-Process -Name $exe -ErrorAction SilentlyContinue)
        if ($running.Count -gt 0) {
            Add-Note $result ("app running ({0} processes) - 脚本不会去杀它，请自己完全退出后重跑" -f $running.Count)
            $result.Status = "FAIL"
            return $result
        }
    }

    $args = @($script, "--asar", $asar)
    if ($DryRun) { $args += "--dry-run" } else { $args += "--$mode" }
    # 同 Invoke-AsarPatch：拉起统一由本脚本末尾做，-NoRelaunch 在那里生效。
    $args += "--no-launch"
    Invoke-Python $py $args
    $code = $LASTEXITCODE
    if ($code -eq 2) {
        Add-Note $result "anchors not found in this build - nothing written"
        $result.Status = "SKIP"
        return $result
    }
    if ($code -ne 0) {
        Add-Note $result "patch-asar.py exit $code"
        $result.Status = "FAIL"
        return $result
    }
    $result.Status = "OK"
    return $result
}


function Invoke-CliPatchTarget($profile) {
    Set-WorkBuddyPython $profile
    Set-TargetEnv $profile
    $result = New-Result $profile.Name $profile.Label "cli-patch"
    if (-not $profile.Installed) { Add-Note $result "not installed"; return $result }
    $script = Join-Path $ScriptRoot "patch-cli-trim.py"
    if (-not (Test-Path -LiteralPath $script)) {
        Add-Note $result "patch-cli-trim.py not present"
        $result.Status = "FAIL"
        return $result
    }
    $py = Get-PythonExe $profile.ConfigDir
    if (-not $py) {
        Add-Note $result "python not found for $($profile.Name)"
        $result.Status = "FAIL"
        return $result
    }
    # 不按文件名（1849.*.mjs）找 chunk：WorkBuddy 升级会改 id/哈希，别的电脑也可能
    # 装在 D:\ 或是 AI 版。把 dist 目录交给 Python，由内容（modelInput.instructions +
    # modelInput.input）定位；找不到就算 FAIL，绝不静默 SKIP。
    $unpacked = Join-Path $profile.InstallPath "resources\app.asar.unpacked"
    $distDir = Join-Path $unpacked "cli\dist"
    if (-not (Test-Path -LiteralPath $distDir)) { $distDir = $unpacked }
    if (-not (Test-Path -LiteralPath $distDir)) {
        Add-Note $result "app.asar.unpacked not found under $($profile.InstallPath)"
        $result.Status = "FAIL"
        return $result
    }
    $prevIo = $env:PYTHONIOENCODING
    $env:PYTHONIOENCODING = "utf-8"
    $out = @()
    try {
        $cliArgs = @("-X", "utf8", $script, "--apply", "--file", $distDir)
        if ($DryRun) { $cliArgs += "--dry-run" }
        $out = @(& $py @cliArgs 2>&1 | ForEach-Object { [string]$_ })
        $code = $LASTEXITCODE
    } finally {
        if ($null -eq $prevIo) { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue } else { $env:PYTHONIOENCODING = $prevIo }
    }
    foreach ($line in $out) { Write-Host $line }
    if ($code -ne 0) {
        # 锚点对不上（别的构建/别的混淆）不等于没救：轻量修法只需要
        # "在 chunk 开头插一段 fetch 清理"，不依赖任何锚点、也不需要 Python。
        $lite = Join-Path $ScriptRoot "wb-fix-lite.ps1"
        if (Test-Path -LiteralPath $lite) {
            Write-Host "anchor patch failed - falling back to the anchor-free fetch scrub" -ForegroundColor Yellow
            $lo = @(& $ps -NoProfile -ExecutionPolicy Bypass -File $lite -Install $profile.InstallPath 2>&1 | ForEach-Object { [string]$_ })
            foreach ($line in $lo) { Write-Host $line }
            if (($lo -join "`n") -match '已写入|已有轻量补丁') {
                $result.Status = "OK"
                Add-Note $result "cli: anchor patch failed, anchor-free fetch scrub applied"
                return $result
            }
        }
        Add-Note $result "cli patch exit $code"
        $result.Status = "FAIL"
        return $result
    }
    $st = @(& $py -X utf8 $script --status --file $distDir 2>&1 | ForEach-Object { [string]$_ })
    if (@($st | Where-Object { $_ -match '^status:\s+PATCHED' }).Count -eq 0) {
        Add-Note $result "cli patch did not verify"
        $result.Status = "FAIL"
        return $result
    }
    # 已经起着的 prewarm 助手还握着旧 chunk；只停 kind=prewarm + status=idle 的进程
    # （按 PID），不碰主程序、也不碰当前聊天。下一次新聊天就会加载已打补丁的运行时。
    $refresh = Join-Path $ScriptRoot "refresh-cli-prewarm.py"
    if ((Test-Path -LiteralPath $refresh) -and -not $DryRun) {
        $pr = @(& $py -X utf8 $refresh --apply --config-dir $profile.ConfigDir 2>&1 | ForEach-Object { [string]$_ })
        $stopped = @($pr | Where-Object { $_ -match '^stopped:' })
        if ($stopped.Count -gt 0) { Add-Note $result ("prewarm " + $stopped[0]) }
    }
    $result.Status = "OK"
    Add-Note $result "model boundary trimmed; open a NEW chat to load it"
    return $result
}

function Set-WorkBuddyPython($profile) {
    # 新电脑常常没有系统 Python；WorkBuddy 自带一个，install 目录里就有，
    # 优先用它，避免 deploy 因为 "python not found" 直接跳过所有补丁。
    if ($env:WORKBUDDY_PYTHON) { return }
    if (-not $profile.InstallPath) { return }
    foreach ($rel in @("resources\vendor\python\python.exe",
                       "resources\app.asar.unpacked\resources\vendor\python\python.exe")) {
        $p = Join-Path $profile.InstallPath $rel
        if (Test-Path -LiteralPath $p) {
            $env:WORKBUDDY_PYTHON = $p
            Write-Host ("python: {0} (bundled with WorkBuddy)" -f $p) -ForegroundColor DarkGray
            return
        }
    }
}

function Get-CacheTemplateStats($profile) {
    # 缓存模板是"是否真的生效"的第一层证据：文件数 + 八个被删块的出现次数。
    $root = Join-Path $profile.ConfigDir "plugins\cache\workbuddy-builtin"
    $stats = [pscustomobject]@{ Root = $root; Exists = $false; Files = 0; Hits = 0 }
    if (-not (Test-Path -LiteralPath $root)) { return $stats }
    $stats.Exists = $true
    $tags = @("tool_usage_policy", "result_presentation", "final_answer_instructions",
              "task_management", "agent_skills", "quality_policy", "agent_loop", "regional_conventions")
    $files = @(Get-ChildItem -LiteralPath $root -Filter prompt.tpl -Recurse -Depth 3 -ErrorAction SilentlyContinue)
    $stats.Files = $files.Count
    foreach ($f in $files) {
        $text = [IO.File]::ReadAllText($f.FullName)
        foreach ($tag in $tags) { $stats.Hits += ([regex]::Matches($text, "<" + $tag)).Count }
    }
    return $stats
}

function Wait-ForAppClose($profiles) {
    # asar 只有应用完全退出才能写。这里不杀进程，只提示并等最多 60 秒；
    # 直接按回车 / 输入被重定向（自动化）就立刻继续，只打 CLI 层——模型边界本来就靠 CLI 层。
    $names = @()
    foreach ($p in $profiles) {
        if (-not $p.Installed) { continue }
        $exe = $p.ExeName
        if (-not $exe) { continue }
        $n = [IO.Path]::GetFileNameWithoutExtension($exe)
        if ($n) { $names += $n }
    }
    $names = @($names | Select-Object -Unique)
    $running = @()
    foreach ($n in $names) {
        if (@(Get-Process -Name $n -ErrorAction SilentlyContinue).Count -gt 0) { $running += $n }
    }
    if ($running.Count -eq 0) { return }
    if ([Console]::IsInputRedirected) {
        Write-Host ("WorkBuddy is running ({0}); asar will be skipped, CLI layer still applied." -f ($running -join ", ")) -ForegroundColor Yellow
        return
    }
    Write-Host ""
    Write-Host ("检测到正在运行：{0}" -f ($running -join ", ")) -ForegroundColor Yellow
    Write-Host "asar 层需要应用完全退出（含托盘）。退出后回到这里按回车；直接回车=只打 CLI 层（模型边界已足够）。" -ForegroundColor Yellow
    for ($i = 0; $i -lt 120; $i++) {
        if ([Console]::KeyAvailable) { $null = [Console]::ReadKey($true); return }
        $alive = $false
        foreach ($n in $running) {
            if (@(Get-Process -Name $n -ErrorAction SilentlyContinue).Count -gt 0) { $alive = $true }
        }
        if (-not $alive) { Write-Host "应用已退出，继续写 asar。" -ForegroundColor Green; return }
        Start-Sleep -Milliseconds 500
    }
}

function Start-WorkBuddyIfClosed($profiles) {
    # 「脚本结束要自动拉起」的唯一落点：所有写盘动作结束时都走这里。
    # 只启动，从不结束进程；-NoRelaunch 关掉。
    if ($NoRelaunch) { Write-Host "App not started (-NoRelaunch)." -ForegroundColor DarkGray; return $false }
    $running = @()
    foreach ($p in $profiles) {
        if (-not $p.Installed -or -not $p.ExeName) { continue }
        $exe = $p.ExeName
        if (-not [IO.Path]::IsPathRooted($exe)) { $exe = Join-Path $p.InstallPath $exe }
        if (-not (Test-Path -LiteralPath $exe)) { continue }
        $name = [IO.Path]::GetFileNameWithoutExtension($exe)
        if (@(Get-Process -Name $name -ErrorAction SilentlyContinue).Count -gt 0) {
            # continue 而不是 return：一套安装在跑，不代表另一套也不用启动
            $running += $exe
            continue
        }
        Start-Process -FilePath "explorer.exe" -ArgumentList ('"{0}"' -f $exe)
        Write-Host ("App started: {0}" -f $exe) -ForegroundColor Green
        return $true
    }
    if ($running.Count -gt 0) {
        Write-Host ("App already running: {0}" -f ($running -join ", ")) -ForegroundColor DarkGray
    }
    return $false
}

function Invoke-StackVerify($profiles) {
    # 收口验收：把每一层的真实状态打出来。模型说“还在”不算证据，脚本读文件/读 trace 才算。
    $script = Join-Path $ScriptRoot "verify-stack.py"
    if (-not (Test-Path -LiteralPath $script)) {
        Write-Host "verify-stack.py not present - acceptance skipped" -ForegroundColor Yellow
        return 0
    }
    $py = $null
    foreach ($p in $profiles) {
        if ($p.ConfigDir) { $py = Get-PythonExe $p.ConfigDir; if ($py) { break } }
    }
    if (-not $py) { $py = Get-PythonExe $null }
    if (-not $py) {
        Write-Host "python not found - acceptance skipped" -ForegroundColor Yellow
        return 0
    }
    $report = Join-Path $ProjectRoot "reports\verify-latest.txt"
    $dir = Split-Path -Parent $report
    if (-not (Test-Path -LiteralPath $dir)) { New-Item -ItemType Directory -Path $dir -Force | Out-Null }
    $prev = $env:PYTHONIOENCODING
    $prevEnc = [Console]::OutputEncoding
    $env:PYTHONIOENCODING = "utf-8"
    try {
        # PowerShell 5.1 抓原生命令 stdout 用的是控制台编码；不切成 UTF-8，
        # 报告里的中文结论会变成乱码，用户就更没法判断了。
        try { [Console]::OutputEncoding = [Text.Encoding]::UTF8 } catch { }
        $out = @(& $py -X utf8 $script 2>&1 | ForEach-Object { [string]$_ })
        $code = $LASTEXITCODE
    } finally {
        try { [Console]::OutputEncoding = $prevEnc } catch { }
        if ($null -eq $prev) { Remove-Item Env:PYTHONIOENCODING -ErrorAction SilentlyContinue } else { $env:PYTHONIOENCODING = $prev }
    }
    foreach ($line in $out) {
        $color = "Gray"
        if ($line -match '^RESULT\s*:\s*PASS' -or $line -match '^结论\s*:\s*EFFECTIVE') { $color = "Green" }
        elseif ($line -match '^RESULT\s*:\s*FAIL' -or $line -match '^结论\s*:\s*(NOT EFFECTIVE|UNPROVEN)') { $color = "Red" }
        elseif ($line -match 'RAW$' -or $line -match 'FAIL' -or $line -match 'MISSING') { $color = "Red" }
        elseif ($line -match 'WARN') { $color = "Yellow" }
        Write-Host $line -ForegroundColor $color
    }
    try { Set-Content -LiteralPath $report -Value $out -Encoding UTF8 } catch { }
    Write-Host ("acceptance report: {0}" -f $report) -ForegroundColor DarkGray
    return $code
}

# --- list ---
if ($action -eq "list") {
    try { $profiles = @(Resolve-TargetProfiles -Names $Target -ScriptRoot $ScriptRoot -InstallOverride $WorkBuddyPath -ConfigOverride $ConfigDir) }
    catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
    Write-Host "== Targets ==" -ForegroundColor Cyan
    foreach ($p in $profiles) {
        Write-Host ("* {0,-14} {1}" -f $p.Name, $p.Label)
        Write-Host ("    install:  {0}  {1}" -f $(if ($p.InstallPath) { $p.InstallPath } else { "(none)" }), $(if ($p.Installed) { "(found)" } else { "(NOT FOUND)" }))
        Write-Host ("    config:   {0}  {1}" -f $p.ConfigDir, $(if ($p.ConfigExists) { "(found)" } else { "(missing)" }))
        Write-Host ("    cache:    {0}" -f $p.CacheRoot)
        if ($p.Overlay) { Write-Host ("    overlay:  {0}" -f $p.Overlay) }
    }
    exit 0
}

# --- resolve the target list once, then run the same action for every install ---
try { $profiles = @(Resolve-TargetProfiles -Names $Target -ScriptRoot $ScriptRoot -InstallOverride $WorkBuddyPath -ConfigOverride $ConfigDir) }
catch { Write-Host $_.Exception.Message -ForegroundColor Red; exit 1 }
Write-Host ("== {0}: {1} target(s): {2} ==" -f $action, $profiles.Count, (($profiles | ForEach-Object { $_.Name }) -join ', ')) -ForegroundColor Cyan
if ($action -eq "deploy") {
    Assert-SkillSources
    $scope = if ($Template -or $Source -or $Skill) { "selected" } else { "all files" }
    Write-Host ("   scope={0}; install mirror={1}; cache=write" -f $scope, $(if ($isAdmin -and -not $NoInstall) { "write" } else { "skip (-Elevate to write)" })) -ForegroundColor DarkGray
}

if (($action -eq "deploy") -and (-not $SkipAsarPatch) -and (-not $DryRun)) {
    Wait-ForAppClose $profiles
}

$results = @()
foreach ($profile in $profiles) {
    Write-Host ""
    Write-Host ("---- {0} ({1}) ----" -f $profile.Name, $profile.Label) -ForegroundColor Cyan
    $r = New-Result $profile.Name $profile.Label $action
    try {
        switch ($action) {
            "deploy" { $r = Invoke-DeployTarget $profile }
            "restore" { $r = Invoke-RestoreTarget $profile }
            "verify" { $r = Invoke-VerifyTarget $profile }
            "pull" { $r = Invoke-PullTarget $profile }
            "asar" { $r = Invoke-AsarTarget $profile "apply" }
            "asar-restore" { $r = Invoke-AsarTarget $profile "restore" }
            "cli-patch" { $r = Invoke-CliPatchTarget $profile }
        }
    } catch {
        $r.Status = "FAIL"
        $r.Exit = 1
        Add-Note $r $_.Exception.Message
        Write-Host ("!! {0}" -f $_.Exception.Message) -ForegroundColor Red
    }
    $results += $r
}

Write-Host ""
Write-Host "== Summary ==" -ForegroundColor Cyan
$results = @($results | Where-Object { $_ -and $_.PSObject.Properties['Status'] })
foreach ($r in $results) {
    $color = switch ($r.Status) { "OK" { "Green" } "WARN" { "Yellow" } "FAIL" { "Red" } default { "DarkGray" } }
    $line = "{0,-14} {1,-12} {2,-6} {3}" -f $r.Name, $r.Action, $r.Status, $r.Note
    Write-Host $line -ForegroundColor $color
}

$failed = @($results | Where-Object { $_.Status -eq "FAIL" }).Count

# 逐层验收：cli 的 version/status、asar 的每个补丁、最新 trace 的标记计数，一次打出来。
# 只对写盘动作跑，-DryRun 不跑；验收红也算失败，避免“看起来成功”。
$writeActions = @("deploy", "cli-patch", "asar", "asar-restore", "restore")
if (($writeActions -contains $action) -and -not $DryRun) {
    Write-Host ""
    $verifyCode = Invoke-StackVerify $profiles
    if ($verifyCode -ne 0) { $failed += 1 }
}

# 结束即拉起：写盘类动作（deploy / cli-patch / asar / asar-restore / restore）跑完就启动应用；
# 失败也启动——脚本结束时就该能看到结果，而不是再手动点一次图标。
# 只读动作（list / verify / pull）与 -DryRun 不启动。
$launchActions = @("deploy", "cli-patch", "asar", "asar-restore", "restore")
if (($launchActions -contains $action) -and -not $DryRun) {
    Start-WorkBuddyIfClosed $profiles | Out-Null
}

if ($failed -gt 0) {
    Write-Host ("{0} target(s) failed." -f $failed) -ForegroundColor Red
    exit 1
}
if ($action -eq "deploy") {
    Write-Host "Open a new chat session in each app to pick up the new prompt." -ForegroundColor Green
}
exit 0
