[CmdletBinding()]
param(
    [string]$WorkBuddyPath = "",
    [string]$PluginCacheRoot = "",
    [string]$Target = "",
    [string]$ConfigDir = "",
    [string]$TraceFile = "",
    [int]$LookbackMinutes = 1440,
    [int]$MaxCandidates = 20,
    [string]$Expect = "",
    [string]$Absent = "content_policy",
    [switch]$ListTraces,
    [switch]$ShowSystemPrompt,
    [switch]$CheckIncludes,
    [int]$HeadChars = 800,
    [string]$UserSkillsRoot = ""
)

$ErrorActionPreference = "Stop"

. (Join-Path $PSScriptRoot "target.ps1")
$targetProfile = Resolve-TargetProfile -Name $Target -ScriptRoot $PSScriptRoot -ConfigDirOverride $ConfigDir -InstallOverride $WorkBuddyPath -CacheRootOverride $PluginCacheRoot
if (-not $WorkBuddyPath) { $WorkBuddyPath = $targetProfile.InstallPath }
if (-not $ConfigDir) { $ConfigDir = $targetProfile.ConfigDir }
if (-not $PluginCacheRoot -and $targetProfile.CacheRoot) { $PluginCacheRoot = $targetProfile.CacheRoot }

function Test-WorkBuddyRoot([string]$path) {
    if ([string]::IsNullOrWhiteSpace($path)) { return $false }
    $p = $path.Trim().Trim('"').Trim()
    if ([string]::IsNullOrWhiteSpace($p)) { return $false }
    $resource = Join-Path $p "resources\app.asar.unpacked\resources"
    $asar = Join-Path $p "resources\app.asar"
    $exe = Join-Path $p "WorkBuddy.exe"
    if (Test-Path -LiteralPath $resource) { return $true }
    if (Test-Path -LiteralPath $asar) { return $true }
    if (Test-Path -LiteralPath $exe) { return $true }
    return $false
}

function Resolve-WorkBuddyPath([string]$explicitPath) {
    $candidates = New-Object System.Collections.Generic.List[string]
    if ($explicitPath) { $candidates.Add($explicitPath) }

    $common = @()
    if ($env:ProgramFiles) { $common += (Join-Path $env:ProgramFiles "WorkBuddy") }
    if (${env:ProgramFiles(x86)}) { $common += (Join-Path ${env:ProgramFiles(x86)} "WorkBuddy") }
    if ($env:LOCALAPPDATA) {
        $common += (Join-Path $env:LOCALAPPDATA "Programs\WorkBuddy")
        $common += (Join-Path $env:LOCALAPPDATA "WorkBuddy")
    }
    if ($env:APPDATA) { $common += (Join-Path $env:APPDATA "WorkBuddy") }
    foreach ($p in $common) { if ($p) { $candidates.Add($p) } }

    foreach ($root in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if (-not $root) { continue }
        try {
            Get-ChildItem -LiteralPath $root -Directory -Filter "WorkBuddy*" -ErrorAction SilentlyContinue |
                ForEach-Object { $candidates.Add($_.FullName) }
        } catch { }
    }

    try {
        $proc = Get-Process -Name "WorkBuddy" -ErrorAction SilentlyContinue | Select-Object -First 1
        if ($proc -and $proc.Path) { $candidates.Add((Split-Path $proc.Path -Parent)) }
    } catch { }

    foreach ($key in @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\WorkBuddy.exe",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\WorkBuddy.exe"
    )) {
        try {
            $item = Get-ItemProperty -Path $key -ErrorAction SilentlyContinue
            if ($item) {
                $exe = $item.'(default)'
                if (-not $exe) { $exe = $item.Path }
                if ($exe) { $candidates.Add((Split-Path ($exe.Trim('"')) -Parent)) }
            }
        } catch { }
    }

    $registryRoots = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    foreach ($root in $registryRoots) {
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
                            $end = $u.IndexOf('"', 1)
                            if ($end -gt 0) { $u = $u.Substring(1, $end - 1) }
                        } else {
                            $u = ($u -split ' ')[0]
                        }
                        if ($u -like '*.exe') { $candidates.Add((Split-Path $u -Parent)) }
                    }
                }
        } catch { }
    }

    try {
        $cmd = Get-Command "WorkBuddy.exe" -ErrorAction SilentlyContinue
        if ($cmd -and $cmd.Source) { $candidates.Add((Split-Path $cmd.Source -Parent)) }
    } catch { }

    foreach ($candidate in $candidates) {
        if (Test-WorkBuddyRoot $candidate) {
            return [IO.Path]::GetFullPath($candidate.Trim('"'))
        }
    }
    throw "WorkBuddy not found; pass -WorkBuddyPath."
}

function Resolve-CacheRoot([string]$explicitPath) {
    if ($explicitPath) { return $explicitPath }
    if ($ConfigDir) { return (Join-Path $ConfigDir "plugins\cache\workbuddy-builtin") }
    $configDir = $env:WORKBUDDY_CONFIG_DIR
    if (-not $configDir) { $configDir = Get-DefaultConfigDir -InstallPath $WorkBuddyPath -Id $targetProfile.Name }
    return (Join-Path $configDir "plugins\cache\workbuddy-builtin")
}

function Get-Sha256([string]$path) {
    if ([string]::IsNullOrWhiteSpace($path)) { return "" }
    if (-not (Test-Path -LiteralPath $path)) { return "" }
    try {
        $sha = [Security.Cryptography.SHA256]::Create()
        $fs = [IO.File]::OpenRead($path)
        try { return ([BitConverter]::ToString($sha.ComputeHash($fs))).Replace('-', '') }
        finally { $fs.Dispose(); $sha.Dispose() }
    } catch { return "" }
}

function Get-SizeOf([string]$path) {
    if ([string]::IsNullOrWhiteSpace($path)) { return -1 }
    if (Test-Path -LiteralPath $path) { return (Get-Item -LiteralPath $path).Length }
    return -1
}

# Resolve every {% include "plugin/sub/path" %} found in the plugin cache.
# The app loads the newest version directory of each plugin, so only that one is
# checked; an include resolves against the same version, else the target plugin's
# newest version (cross-plugin version drift is normal after an app update).
function Get-VersionDirKey([string]$name) {
    $m = [regex]::Match($name, '^\d+(\.\d+){0,3}')
    if ($m.Success) {
        try { return [version]$m.Value } catch { }
    }
    return [version]"0.0.0.0"
}

function Test-IncludeResolution([string]$cacheRoot) {
    $missing = @()
    $checked = 0
    $latest = @{}
    $staleSkipped = 0
    if (-not (Test-Path -LiteralPath $cacheRoot)) {
        return [pscustomobject]@{ Checked = 0; Missing = @("cache root not found: $cacheRoot"); StaleSkipped = 0 }
    }
    foreach ($pluginDir in @(Get-ChildItem -LiteralPath $cacheRoot -Directory -ErrorAction SilentlyContinue)) {
        $vers = @(Get-ChildItem -LiteralPath $pluginDir.FullName -Directory -ErrorAction SilentlyContinue |
            Sort-Object -Property @{ Expression = { Get-VersionDirKey $_.Name } } -Descending)
        if ($vers.Count -eq 0) { continue }
        $latest[$pluginDir.Name] = $vers[0].Name
        $staleSkipped += ($vers.Count - 1)
    }
    foreach ($pluginName in $latest.Keys) {
        $verName = $latest[$pluginName]
        $verDir = Join-Path (Join-Path $cacheRoot $pluginName) $verName
        $files = @(Get-ChildItem -LiteralPath $verDir -File -Recurse -Include *.tpl, *.md -ErrorAction SilentlyContinue)
        foreach ($file in $files) {
            $text = [IO.File]::ReadAllText($file.FullName)
            foreach ($m in [regex]::Matches($text, '\{%-?\s*include\s+"([^"]+)"')) {
                $checked++
                $inc = $m.Groups[1].Value -replace '/', '\'
                $parts = $inc.Split('\')
                if ($parts.Length -lt 2) { $missing += ("{0} -> {1} (bad form)" -f $file.Name, $inc); continue }
                $tPlugin = $parts[0]
                $tSub = ($parts[1..($parts.Length - 1)]) -join '\'
                $candidates = @(
                    (Join-Path (Join-Path (Join-Path $cacheRoot $tPlugin) $verName) $tSub)
                )
                if ($latest.ContainsKey($tPlugin) -and $latest[$tPlugin] -ne $verName) {
                    $candidates += (Join-Path (Join-Path (Join-Path $cacheRoot $tPlugin) $latest[$tPlugin]) $tSub)
                }
                $found = $false
                foreach ($c in $candidates) { if (Test-Path -LiteralPath $c) { $found = $true; break } }
                if (-not $found) {
                    $missing += ("{0}\{1}\{2} -> {3}" -f $pluginName, $verName, $file.Name, $inc)
                }
            }
        }
    }
    return [pscustomobject]@{ Checked = $checked; Missing = $missing; StaleSkipped = $staleSkipped }
}

function Get-TraceCandidates([string]$root, [int]$minutes) {
    if (-not (Test-Path -LiteralPath $root)) { return @() }
    $cutoff = (Get-Date).AddMinutes(-1 * $minutes)
    return @(Get-ChildItem -LiteralPath $root -Recurse -File -Filter "*.jsonl" -ErrorAction SilentlyContinue |
        Where-Object { $_.LastWriteTime -ge $cutoff } |
        Sort-Object LastWriteTime -Descending)
}

# Extract the first system-prompt prefix from a trace JSONL file.
# The trace stores the model request in span.toolInput, starting with [{"content":"...
# It is truncated at about 20,000 chars, which is enough: the beginning of the
# system prompt carries the mode/probe markers we need.
function Read-TraceSystemPrompt([string]$path) {
    $info = [pscustomobject]@{
        Path = $path
        StartedAt = ""
        SessionId = ""
        AgentName = ""
        SystemHead = ""
        GenerationCount = 0
        Found = $false
    }
    $reader = New-Object System.IO.StreamReader($path, [System.Text.Encoding]::UTF8)
    try {
        while ($null -ne ($line = $reader.ReadLine())) {
            if ($line.Length -lt 20) { continue }
            if (($line -notmatch '"type"\s*:\s*"trace_start"') -and ($line -notmatch '"type"\s*:\s*"span_end"')) { continue }
            try { $obj = $line | ConvertFrom-Json } catch { continue }
            if ($obj.type -eq "trace_start") {
                if ($obj.trace) {
                    $info.StartedAt = [string]$obj.trace.startedAt
                    $info.SessionId = [string]$obj.trace.sessionId
                    $info.AgentName = [string]$obj.trace.agentName
                }
                continue
            }
            if ($obj.type -ne "span_end" -or -not $obj.span) { continue }
            if ($obj.span.name -ne "generation") { continue }
            $info.GenerationCount++
            $ti = [string]$obj.span.toolInput
            if (-not $ti.StartsWith('[{"content"')) { continue }
            $m = [regex]::Match($ti, '^\s*\[\{"content":"([\s\S]*)$')
            if (-not $m.Success) { continue }
            $info.SystemHead = $m.Groups[1].Value
            $info.Found = $true
            break
        }
    } finally { $reader.Dispose() }
    return $info
}

function Get-MarkerCount([string]$text, [string]$marker) {
    if ([string]::IsNullOrEmpty($text)) { return 0 }
    return ([regex]::Matches($text, [regex]::Escape($marker))).Count
}

function Find-NewestTraceWithPrompt([string]$root, [int]$minutes, [int]$maxCandidates) {
    $candidates = Get-TraceCandidates $root $minutes
    if ($candidates.Count -eq 0) { return $null }
    $checked = 0
    foreach ($f in $candidates) {
        if ($checked -ge $maxCandidates) { break }
        $checked++
        if ($f.Length -lt 200) { continue }
        $info = Read-TraceSystemPrompt $f.FullName
        if ($info.Found) { return $info }
    }
    return $null
}

$wb = $null
try {
    $wb = Resolve-WorkBuddyPath $WorkBuddyPath
} catch {
    Write-Output "WorkBuddy install folder not found; verifying cache only."
}
$res = if ($wb) { Join-Path $wb "resources\app.asar.unpacked\resources" } else { "" }
$cache = Resolve-CacheRoot $PluginCacheRoot
$project = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
$traceRoot = Join-Path $ConfigDir "traces"

Write-Output ("== Target: {0} ({1}) ==" -f $targetProfile.Name, $targetProfile.Label)
Write-Output ("  install:   {0}{1}" -f $WorkBuddyPath, $(if ($wb) { "" } else { "  (not found)" }))
Write-Output ("  config:    {0}" -f $ConfigDir)
Write-Output ("  cache:     {0}" -f $cache)

$includeFailed = $false
if ($CheckIncludes) {
    $r = Test-IncludeResolution $cache
    Write-Output ""
    Write-Output "== Include resolution (plugin cache) =="
    Write-Output ("  includes checked: {0}, missing: {1} (stale version dirs skipped: {2})" -f $r.Checked, $r.Missing.Count, $r.StaleSkipped)
    foreach ($m in @($r.Missing | Select-Object -First 20)) { Write-Output ("  MISSING {0}" -f $m) }
    if ($r.Missing.Count -gt 0) { $includeFailed = $true }
}

if ($TraceFile) {
    if (-not (Test-Path -LiteralPath $TraceFile)) { throw "Trace file not found: $TraceFile" }
    $fullTrace = [IO.Path]::GetFullPath($TraceFile)
    $info = Read-TraceSystemPrompt $fullTrace
    $traces = @([IO.FileInfo]::new($fullTrace))
} else {
    $traces = Get-TraceCandidates $traceRoot $LookbackMinutes
    $info = Find-NewestTraceWithPrompt $traceRoot $LookbackMinutes $MaxCandidates
}

Write-Output "== Trace files (newest first) =="
if ($traces.Count -eq 0) {
    Write-Output "  (no trace files modified in the last $LookbackMinutes minutes under $traceRoot)"
} else {
    $i = 0
    foreach ($t in $traces) {
        if ($i -ge 8) {
            Write-Output ("  ... and {0} more" -f ($traces.Count - 8))
            break
        }
        Write-Output ("  {0}  {1,9} bytes  {2}" -f $t.LastWriteTime.ToString("yyyy-MM-dd HH:mm:ss"), $t.Length, $t.FullName)
        $i++
    }
}

if ($ListTraces) { exit 0 }

if ($null -eq $info -or -not $info.Found) {
    Write-Output ""
    Write-Output "== System prompt =="
    Write-Output "  NOT FOUND. Traces only retain toolInput/toolOutput for model calls."
    Write-Output "  Create a new WorkBuddy chat, send one message, then run this script again."
    exit 2
}

$head = $info.SystemHead
Write-Output ""
Write-Output "== Actual system prompt (from trace, prefix) =="
Write-Output ("  trace:    {0}" -f $info.Path)
Write-Output ("  started:  {0}" -f $info.StartedAt)
Write-Output ("  session:  {0}" -f $info.SessionId)
Write-Output ("  agent:    {0}" -f $info.AgentName)
Write-Output ("  captured: {0} chars (trace truncates toolInput at about 20k)" -f $head.Length)
$staleTrace = $false
$cachePromptForAge = ""
$ageDir = Join-Path $cache "welcomemode-work"
if (Test-Path -LiteralPath $ageDir) {
    $newest = @(Get-ChildItem -LiteralPath $ageDir -Directory -ErrorAction SilentlyContinue |
        Sort-Object -Property @{ Expression = { Get-VersionDirKey $_.Name } } -Descending | Select-Object -First 1)
    if ($newest.Count -gt 0) { $cachePromptForAge = Join-Path $newest[0].FullName "prompt.tpl" }
}
if ($cachePromptForAge -and (Test-Path -LiteralPath $cachePromptForAge) -and (Test-Path -LiteralPath $info.Path)) {
    # apply copies files with File.Copy, which keeps the source mtime; the real deploy
    # time lives in the newest matching backup manifest.
    $deployTime = $null
    $backupRoot = Join-Path (Split-Path $PSScriptRoot -Parent) "backups"
    if (Test-Path -LiteralPath $backupRoot) {
        foreach ($dir in @(Get-ChildItem -LiteralPath $backupRoot -Directory -ErrorAction SilentlyContinue)) {
            $manifestFile = Join-Path $dir.FullName "manifest.json"
            if (-not (Test-Path -LiteralPath $manifestFile)) { continue }
            try {
                $m = Get-Content -LiteralPath $manifestFile -Raw | ConvertFrom-Json
                if ($m.target -and $m.target -ne $targetProfile.Name) { continue }
                if (-not $m.target) { continue }
                $created = [datetime]::Parse($m.createdAt, [Globalization.CultureInfo]::InvariantCulture)
                if (-not $deployTime -or $created -gt $deployTime) { $deployTime = $created }
            } catch { }
        }
    }
    if (-not $deployTime) { $deployTime = (Get-Item -LiteralPath $cachePromptForAge).LastWriteTime }
    $traceTime = (Get-Item -LiteralPath $info.Path).LastWriteTime
    $staleTrace = ($traceTime -lt $deployTime)
    Write-Output ("  deployed:  {0} (last apply for this target)" -f $deployTime.ToString("yyyy-MM-dd HH:mm:ss"))
    if ($staleTrace) {
        Write-Output "  STALE:     this request predates the deployed prompt; open a new chat in this app and rerun."
    }
}
Write-Output ""
Write-Output "  marker counts in the captured prefix:"
foreach ($marker in @(
    'This conversation is powered by',
    'You are CodeBuddy Code',
    'search_routing',
    '<tool_use>',
    '<personal_files_safety>',
    'content_policy',
    'never exposes machinery',
    '[WBv2]',
    '[PROBE-A]',
    '[PROBE-B-WELCOME]',
    '[WM-WELCOME]',
    '[WM-TEMPLATE]',
    '[PC-MEM-OK]',
    '[IM-CRAFT-OK]'
)) {
    Write-Output ("    {0,-34} {1}" -f $marker, (Get-MarkerCount $head $marker))
}

if ($ShowSystemPrompt) {
    $pretty = $head -replace '\\r\\n', "`r`n" -replace '\\n', "`n" -replace '\\t', "`t" -replace '\\"', '"'
    Write-Output ""
    Write-Output ("== System prompt head ({0} chars) ==" -f $HeadChars)
    if ($pretty.Length -gt $HeadChars) { Write-Output $pretty.Substring(0, $HeadChars) } else { Write-Output $pretty }
}

Write-Output ""
Write-Output "== File consistency (project -> install mirror -> cache [LIVE]) =="
foreach ($mode in @("work", "code", "design")) {
    $p = Join-Path $project ("welcome-mode\{0}.tpl" -f $mode)
    $inst = if ($res) { Join-Path $res ("plugins\workbuddy-builtin\welcomemode\{0}\prompt.tpl" -f $mode) } else { "" }
    $pHash = Get-Sha256 $p
    $iHash = Get-Sha256 $inst
    $pSize = Get-SizeOf $p
    $iSize = Get-SizeOf $inst
    $state = if (-not $res) { "install n/a (cache-only)" } elseif ($pHash -and $pHash -eq $iHash) { "MATCH" } elseif ($iHash) { "MISMATCH (run apply)" } else { "install missing" }
    Write-Output ("  welcomemode-{0,-7} project={1,6}  install={2,6}  {3}" -f $mode, $pSize, $iSize, $state)
    $cDir = Join-Path $cache ("welcomemode-{0}" -f $mode)
    if (Test-Path -LiteralPath $cDir) {
        foreach ($v in @(Get-ChildItem -LiteralPath $cDir -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending)) {
            $cHash = Get-Sha256 (Join-Path $v.FullName "prompt.tpl")
            $cState = if ($pHash -and $pHash -eq $cHash) { "MATCH" } else { "MISMATCH" }
            Write-Output ("    cache {0,-52} {1}" -f $v.Name, $cState)
        }
    }
}
$tp = Join-Path $project "archive\20261114-unused\templates\workbuddy-prompt.tpl"
$ti = if ($res) { Join-Path $res "templates\workbuddy-prompt.tpl" } else { "" }
$templateState = if (-not $res) { "install n/a" } elseif ((Get-Sha256 $tp) -and ((Get-Sha256 $tp) -eq (Get-Sha256 $ti))) { "MATCH" } else { "MISMATCH" }
if (Test-Path -LiteralPath $tp) {
    Write-Output ("  templates\workbuddy-prompt.tpl  (DECOY, not sent)  archive={0,6}  install={1,6}" -f (Get-SizeOf $tp), (Get-SizeOf $ti))
}

Write-Output ""
Write-Output "== Builtin skills (project -> install -> cache) =="
$skillSrc = Join-Path $project "plugins\workbuddy-builtin\skills"
if (Test-Path -LiteralPath $skillSrc) {
    foreach ($dir in @(Get-ChildItem -LiteralPath $skillSrc -Directory -ErrorAction SilentlyContinue)) {
        $name = $dir.Name
        $skillFile = Join-Path $dir.FullName "SKILL.md"
        if (-not (Test-Path -LiteralPath $skillFile)) { continue }
        $srcHash = Get-Sha256 $skillFile
        $srcSize = Get-SizeOf $skillFile
        $instHash = if ($res) { Get-Sha256 (Join-Path $res ("plugins\workbuddy-builtin\skills\{0}\SKILL.md" -f $name)) } else { "" }
        $cachePlugin = Join-Path $cache ("skill-{0}" -f $name)
        if (-not (Test-Path -LiteralPath $cachePlugin)) {
            Write-Output ("  {0,-34} project={1,6}  cache=MISSING  not a cached skill" -f $name, $srcSize)
            continue
        }
        foreach ($v in @(Get-ChildItem -LiteralPath $cachePlugin -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending)) {
            $cHash = Get-Sha256 (Join-Path $v.FullName "SKILL.md")
            $state = if ($srcHash -and $srcHash -eq $cHash) { "MATCH" } else { "MISMATCH (run apply)" }
            $instState = if (-not $res) { "install n/a" } elseif ($srcHash -and $srcHash -eq $instHash) { "install MATCH" } elseif ($instHash) { "install MISMATCH" } else { "install missing" }
            Write-Output ("  {0,-34} project={1,6}  cache={2,6}  {3}, {4}" -f $name, $srcSize, (Get-SizeOf (Join-Path $v.FullName "SKILL.md")), $state, $instState)
        }
    }
}

Write-Output ""
Write-Output "== Source classification =="
$probeWelcome = Get-MarkerCount $head '[PROBE-B-WELCOME]'
$probeTemplate = (Get-MarkerCount $head '[WBv2]') + (Get-MarkerCount $head '[PROBE-A]')
$instWelcome = if ($res) { Join-Path $res "plugins\workbuddy-builtin\welcomemode\work\prompt.tpl" } else { "" }
$cacheWelcome = ""
$cDir = Join-Path $cache "welcomemode-work"
if (Test-Path -LiteralPath $cDir) {
    $latest = @(Get-ChildItem -LiteralPath $cDir -Directory | Sort-Object Name -Descending | Select-Object -First 1)
    if ($latest.Count -gt 0) { $cacheWelcome = Join-Path $latest[0].FullName "prompt.tpl" }
}
$instHasProbe = 0
if ($instWelcome -and (Test-Path -LiteralPath $instWelcome)) { $instHasProbe = Get-MarkerCount ([IO.File]::ReadAllText($instWelcome)) '[PROBE-B-WELCOME]' }
$cacheHasProbe = 0
if ($cacheWelcome -and (Test-Path -LiteralPath $cacheWelcome)) { $cacheHasProbe = Get-MarkerCount ([IO.File]::ReadAllText($cacheWelcome)) '[PROBE-B-WELCOME]' }
if ($probeTemplate -gt 0) {
    Write-Output "  templates\*.tpl markers are present -> the old desktop renderer path is being used."
} elseif ($probeWelcome -gt 0) {
    if (($cacheHasProbe -gt 0) -and ($instHasProbe -eq 0)) {
        Write-Output "  proven: the actual prompt came from the plugin CACHE (welcomemode-work/<version>/prompt.tpl)."
    } elseif (($cacheHasProbe -gt 0) -and ($instHasProbe -gt 0)) {
        Write-Output "  welcome-mode probe present in both install and cache; content is identical, cannot distinguish."
    } else {
        Write-Output "  welcome-mode probe present, but neither install nor cache currently has it (stale file?)."
    }
} elseif ((Get-MarkerCount $head 'search_routing') -gt 0) {
    Write-Output "  looks like the welcome-mode prompt (search_routing present), no probe to identify the copy."
} else {
    Write-Output "  source not identified from the captured prefix."
}

Write-Output ""
Write-Output "== Legacy metric (do NOT use this to judge effectiveness) =="
$logDir = Join-Path (Join-Path $ConfigDir "logs") (Get-Date -Format "yyyy-MM-dd")
if (Test-Path -LiteralPath $logDir) {
    $renders = @(Get-ChildItem -LiteralPath $logDir -Filter *.log -File -ErrorAction SilentlyContinue |
        Select-String -Pattern '\[PromptRenderer\] Rendered \((\d+) chars, mode=([a-z-]*), kind=([a-z-]*), welcomeMode=([a-z]*)' -AllMatches |
        ForEach-Object { $_.Matches } |
        ForEach-Object { [pscustomobject]@{ Chars = [int]$_.Groups[1].Value; Kind = $_.Groups[3].Value } })
    if ($renders.Count -gt 0) {
        $last = $renders[-1]
        Write-Output ("  last [PromptRenderer]: {0} chars, kind={1}" -f $last.Chars, $last.Kind)
        Write-Output "  This number is produced by templates\*.tpl and is NOT the model system prompt."
        Write-Output "  It can stay unchanged while welcome-mode changes are live; use the trace above."
    }
}

$inconclusive = $false
$failed = $false
if ($Expect) {
    $n = Get-MarkerCount $head $Expect
    Write-Output ""
    if ($n -gt 0) {
        Write-Output ("FOUND: {0} ({1} occurrence(s) in the captured prefix)" -f $Expect, $n)
    } else {
        Write-Output ("NOT_FOUND_IN_PREFIX: {0}" -f $Expect)
        Write-Output "  The prefix is about 20k chars; a marker near the end of the prompt may simply not be captured."
        Write-Output "  This is NOT proof that the marker is absent — resend with the marker near the top, or verify by behavior."
        $inconclusive = $true
    }
}
if ($Absent) {
    $n = Get-MarkerCount $head $Absent
    if ($n -gt 0 -and $staleTrace) {
        Write-Output ""
        Write-Output ("STALE_TRACE: {0} still appears {1} time(s), but this request predates the deployed prompt." -f $Absent, $n)
        Write-Output "  Not a failure: open a new chat in this app, send one message, rerun this script."
        $inconclusive = $true
    } elseif ($n -gt 0) {
        Write-Output ("FAIL: forbidden marker present: {0} ({1} occurrence(s))" -f $Absent, $n)
        $failed = $true
    } else {
        Write-Output ("NOT_COVERED: {0} was not found in the captured prefix." -f $Absent)
        Write-Output "  Absence in the prefix does not prove absence from the full prompt."
    }
}

if ($failed -or $includeFailed) { exit 1 }
if ($inconclusive) { exit 2 }

exit 0
