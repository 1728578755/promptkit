[CmdletBinding()]
param(
    [string]$WorkBuddyPath = "",
    [string]$Target = "",
    [string]$ConfigDir = "",
    [string]$Overlay = "",
    [string]$Template = "",
    [string]$Source = "",
    [switch]$All,
    [switch]$DryRun,
    [switch]$NoElevate,
    [switch]$UseCustom,
    [string]$PluginCacheRoot = "",
    [ValidateSet("All", "Latest")]
    [string]$CacheVersions = "All",
    [switch]$NoPluginCache,
    [switch]$IncludeTemplates,
    [string]$Skill = "",
    [switch]$NoInstall,
    [switch]$NoRegisterSkills
)

$ErrorActionPreference = "Stop"
$ScriptRoot = $PSScriptRoot
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $ScriptRoot "..")).Path
$CustomRoot = Join-Path $ProjectRoot "custom"
$BackupRoot = Join-Path $ProjectRoot "backups"

. (Join-Path $ScriptRoot "target.ps1")
$targetProfile = Resolve-TargetProfile -Name $Target -ScriptRoot $ScriptRoot `
    -ConfigDirOverride $ConfigDir -InstallOverride $WorkBuddyPath -CacheRootOverride $PluginCacheRoot -OverlayOverride $Overlay
if (-not $WorkBuddyPath) { $WorkBuddyPath = $targetProfile.InstallPath }
if (-not $ConfigDir) { $ConfigDir = $targetProfile.ConfigDir }
if (-not $PluginCacheRoot -and $targetProfile.CacheRoot) { $PluginCacheRoot = $targetProfile.CacheRoot }
if (-not $Overlay) { $Overlay = $targetProfile.Overlay }

$SourceRoots = @()
if ($Overlay -and (Test-Path -LiteralPath $Overlay)) { $SourceRoots += $Overlay }
$SourceRoots += $ProjectRoot
if ($UseCustom) { $SourceRoots = @($CustomRoot) + $SourceRoots }

# Log any uncaught exception so an elevated window does not vanish without a trace
trap {
    $stamp = Get-Date -Format "yyyyMMdd-HHmmss"
    $logDir = Join-Path $ProjectRoot "backups"
    if (-not (Test-Path -LiteralPath $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
    $errFile = Join-Path $logDir "apply-error.log"
    $bound = ($PSBoundParameters.GetEnumerator() | ForEach-Object { '-' + $_.Key + ' ' + $_.Value }) -join ' '
    $msg = @(
        ('==== ' + $stamp + ' ====')
        ('Args:    ' + $bound)
        ('Type:    ' + $_.Exception.GetType().FullName)
        ('Message: ' + $_.Exception.Message)
        ('At:      ' + $_.InvocationInfo.PositionMessage)
        'Stack:'
        ([string]$_.ScriptStackTrace)
        ''
    ) -join "`r`n"
    try { [IO.File]::AppendAllText($errFile, $msg, (New-Object System.Text.UTF8Encoding($false))) } catch { }
    Write-Host ''
    Write-Host '!! FAILED' -ForegroundColor Red
    Write-Host $msg -ForegroundColor Red
    Write-Host ('Error log: ' + $errFile) -ForegroundColor Yellow
    exit 1
}

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

function Write-TextNoBom([string]$path, [string]$text) {
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [IO.File]::WriteAllText((Get-LongPath $path), $text, $encoding)
}

function Test-Admin {
    $identity = [Security.Principal.WindowsIdentity]::GetCurrent()
    $principal = New-Object Security.Principal.WindowsPrincipal($identity)
    return $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
}



function Get-ResourceRoot([string]$wb) {
    return (Join-Path $wb "resources\app.asar.unpacked\resources")
}

function Get-PluginCacheRoot([string]$explicitRoot) {
    if ($explicitRoot) { return [IO.Path]::GetFullPath($explicitRoot) }
    if ($ConfigDir) { return [IO.Path]::GetFullPath((Join-Path $ConfigDir "plugins\cache\workbuddy-builtin")) }
    $configDir = $env:WORKBUDDY_CONFIG_DIR
    if (-not $configDir) {
        if (-not $env:USERPROFILE) { throw "USERPROFILE is not set; pass -PluginCacheRoot explicitly." }
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
    if ($p -like 'templates\*' -or $p -like 'plugins\*') { return $p }
    if ($p -like 'builtin-plugins\*' -or $p -like 'skills\*' -or $p -like 'prompt-common\*' -or $p -like 'welcomemode\*' -or $p -like 'interactionmode\*') {
        return "plugins\workbuddy-builtin\$p"
    }
    # project-root welcome-mode\<mode>.tpl -> plugins\workbuddy-builtin\welcomemode\<mode>\prompt.tpl
    if ($p -like 'welcome-mode\*.tpl') {
        $mode = [IO.Path]::GetFileNameWithoutExtension($p)
        return "plugins\workbuddy-builtin\welcomemode\$mode\prompt.tpl"
    }
    # project-root runtime-templates\<name>.tpl -> resources\templates\<name>.tpl
    if ($p -like 'runtime-templates\*.tpl') {
        return "templates\$([IO.Path]::GetFileName($p))"
    }
    # project-root fragments\<name>.md -> plugins\workbuddy-builtin\prompt-common\fragments\<name>.md
    if ($p -like 'fragments\*.md') {
        return "plugins\workbuddy-builtin\prompt-common\fragments\$([IO.Path]::GetFileName($p))"
    }
    if ($p -like '*.tpl') { return "templates\$p" }
    if ($p -like '*.md') { return "plugins\workbuddy-builtin\prompt-common\fragments\$p" }
    throw "Cannot infer target path from '$value'. Pass a relative path under resources, e.g. templates\workbuddy-ask-prompt.tpl or builtin-plugins\workbuddy-sheetagent\agents\sheet-agent.md."
}

# Maps an install-side target (relative to resources\) onto the user plugin cache layout.
# Install layout          ->  Cache layout
#   plugins\...\skills\<n>\<rest>          skill-<n>\<version>\<rest>
#   plugins\...\builtin-plugins\<n>\<rest> <n>\<version>\<rest>
#   plugins\...\interactionmode\<m>\<rest> interactionmode-<m>\<version>\<rest>
#   plugins\...\welcomemode\<m>\prompt.tpl welcomemode-<m>\<version>\prompt.tpl
#   plugins\...\<n>\<rest>                 <n>\<version>\<rest>
function Get-CachePluginLayout([string]$targetRelative) {
    $p = Normalize-RelativePath $targetRelative
    $prefix = 'plugins\workbuddy-builtin\'
    if (-not $p.StartsWith($prefix, [StringComparison]::OrdinalIgnoreCase)) { return $null }
    $rel = $p.Substring($prefix.Length)

    if ($rel -like 'welcomemode\*\prompt.tpl') {
        $parts = $rel.Split('\')
        if ($parts.Length -ne 3) { return $null }
        return [pscustomobject]@{ Plugin = "welcomemode-$($parts[1])"; Sub = "prompt.tpl" }
    }
    if ($rel -like 'interactionmode\*') {
        $parts = $rel.Split('\')
        if ($parts.Length -lt 3) { return $null }
        $sub = ($parts[2..($parts.Length - 1)]) -join '\'
        return [pscustomobject]@{ Plugin = "interactionmode-$($parts[1])"; Sub = $sub }
    }
    if ($rel -like 'skills\*') {
        $rest = $rel.Substring('skills\'.Length)
        $idx = $rest.IndexOf('\')
        if ($idx -lt 1) { return $null }
        return [pscustomobject]@{ Plugin = "skill-$($rest.Substring(0, $idx))"; Sub = $rest.Substring($idx + 1) }
    }
    if ($rel -like 'builtin-plugins\*') {
        $rest = $rel.Substring('builtin-plugins\'.Length)
        $idx = $rest.IndexOf('\')
        if ($idx -lt 1) { return $null }
        return [pscustomobject]@{ Plugin = $rest.Substring(0, $idx); Sub = $rest.Substring($idx + 1) }
    }
    $idx = $rel.IndexOf('\')
    if ($idx -lt 1) { return $null }
    return [pscustomobject]@{ Plugin = $rel.Substring(0, $idx); Sub = $rel.Substring($idx + 1) }
}

function Get-VersionSortKey([string]$name) {
    $m = [regex]::Match($name, '^\d+(\.\d+){0,3}')
    if ($m.Success) {
        try { return [version]$m.Value } catch { }
    }
    return [version]"0.0.0.0"
}

function Get-BuiltinBundleVersion([string]$cacheRoot) {
    foreach ($probe in @('prompt-common', 'welcomemode-work', 'skill-library')) {
        $dir = Join-Path $cacheRoot $probe
        if (-not (Test-DirLong $dir)) { continue }
        $v = Get-ChildItem -LiteralPath $dir -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending | Select-Object -First 1
        if ($v) { return $v.Name }
    }
    return ""
}

# 新建内置技能时补缓存目录与 .codebuddy-plugin/plugin.json（否则 apply 没有可写的版本目录）
function Ensure-BuiltinSkillCaches([string]$projectRoot, [string]$cacheRoot, [string]$resourceRoot, [string]$onlySkill) {
    $created = @()
    if ($NoPluginCache) { return $created }
    $skillsDir = Join-Path $projectRoot "plugins\workbuddy-builtin\skills"
    if (-not (Test-Path -LiteralPath $skillsDir)) { return $created }
    $ver = Get-BuiltinBundleVersion $cacheRoot
    if (-not $ver) { return $created }
    foreach ($dir in @(Get-ChildItem -LiteralPath $skillsDir -Directory -ErrorAction SilentlyContinue)) {
        if ($onlySkill -and $dir.Name -ne $onlySkill) { continue }
        $pluginName = "skill-$($dir.Name)"
        $pluginDir = Join-Path $cacheRoot $pluginName
        if (Test-DirLong $pluginDir) { continue }
        # 只自举"项目里新增、安装目录也没有"的技能；官方已有但未进缓存的（如平台门控）不动
        $installSkillDir = Join-Path $resourceRoot "plugins\workbuddy-builtin\skills\$($dir.Name)"
        if (Test-Path -LiteralPath $installSkillDir) { continue }
        $created += [pscustomobject]@{ Plugin = $pluginName; Version = $ver; Skill = $dir.Name }
        if ($DryRun) { continue }
        $metaDir = Join-Path (Join-Path $pluginDir $ver) ".codebuddy-plugin"
        New-Item -ItemType Directory -Path $metaDir -Force | Out-Null
        $meta = [pscustomobject]@{
            name = $pluginName
            version = $ver
            description = "WorkBuddy bundled skill resource: $($dir.Name)."
            author = [pscustomobject]@{ name = "WorkBuddy" }
            category = "skill"
            keywords = @("workbuddy", "builtin-skill", $dir.Name)
            workbuddy = [pscustomobject]@{
                kind = "builtin-skill"
                legacyResourceName = $dir.Name
                bundleSegments = @("plugins", "workbuddy-builtin", "skills", $dir.Name)
            }
        }
        Write-TextNoBom (Join-Path $metaDir "plugin.json") ($meta | ConvertTo-Json -Depth 6)
    }
    return $created
}

# 把新建的内置技能登记进 installed_plugins.json（缺失才追加，写前备份）
function Register-BuiltinSkills([string[]]$plugins, [string]$cacheRoot, [string]$ver) {
    $added = @()
    if ($plugins.Count -eq 0) { return $added }
    $registryDir = if ($ConfigDir) { $ConfigDir } else { Get-DefaultConfigDir -InstallPath $WorkBuddyPath -Id $targetProfile.Name }
    $path = Join-Path $registryDir "plugins\installed_plugins.json"
    if (-not (Test-Path -LiteralPath $path)) { return $added }
    $json = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    if ($null -eq $json.plugins) { return $added }
    $existing = @($json.plugins.PSObject.Properties.Name)
    foreach ($p in $plugins) {
        $key = "$p@workbuddy-builtin"
        if ($existing -contains $key) { continue }
        $entry = [pscustomobject]@{
            scope = "user"
            installPath = (Join-Path (Join-Path $cacheRoot $p) $ver)
            version = $ver
            installedAt = (Get-Date).ToUniversalTime().ToString("o")
            lastUpdated = (Get-Date).ToUniversalTime().ToString("o")
        }
        $json.plugins | Add-Member -NotePropertyName $key -NotePropertyValue @($entry)
        $added += $key
    }
    if ($added.Count -gt 0 -and -not $DryRun) {
        Copy-Item -LiteralPath $path -Destination "$path.bak-$(Get-Date -Format yyyyMMdd-HHmmss)" -Force
        Write-TextNoBom $path ($json | ConvertTo-Json -Depth 10)
    }
    return $added
}

function Get-CacheTargets([string]$targetRelative, [string]$cacheRoot) {
    if ($NoPluginCache) { return @() }
    $layout = Get-CachePluginLayout $targetRelative
    if (-not $layout) { return @() }
    $pluginDir = Join-Path $cacheRoot $layout.Plugin
    if (-not (Test-DirLong $pluginDir)) { return @() }

    $versions = @(Get-ChildItem -LiteralPath $pluginDir -Directory -ErrorAction SilentlyContinue |
        Sort-Object -Property @{ Expression = { Get-VersionSortKey $_.Name } } -Descending)
    if ($versions.Count -eq 0) { return @() }
    if ($CacheVersions -eq 'Latest') { $versions = @($versions | Select-Object -First 1) }

    $result = @()
    foreach ($v in $versions) {
        $full = Join-Path (Join-Path $pluginDir $v.Name) $layout.Sub
        $result += [pscustomobject]@{
            Path = [IO.Path]::GetFullPath($full)
            Relative = "$($layout.Plugin)\$($v.Name)\$($layout.Sub)"
            Plugin = $layout.Plugin
            Version = $v.Name
        }
    }
    return $result
}

function Get-SourceCandidates([string]$targetRelative) {
    $fileName = [IO.Path]::GetFileName($targetRelative)
    $dir = Normalize-RelativePath ([IO.Path]::GetDirectoryName($targetRelative))
    $candidates = @()

    foreach ($root in $SourceRoots) {
        if ($targetRelative -like 'plugins\workbuddy-builtin\builtin-plugins\*' -or $targetRelative -like 'plugins\workbuddy-builtin\skills\*') {
            $rel = $targetRelative.Substring(('plugins\workbuddy-builtin\').Length)
            $candidates += Join-Path $root "plugins\workbuddy-builtin\$rel"
            $candidates += Join-Path $root "plugin-prompts\$rel"
            $candidates += Join-Path $root $fileName
        } elseif ($dir -eq 'templates') {
            $base = $fileName -replace '\.tpl$', ''
            $candidates += Join-Path $root "$base.custom.tpl"
            $candidates += Join-Path $root $fileName
            $candidates += Join-Path $root "templates\$fileName"
        } elseif ($targetRelative -like 'plugins\workbuddy-builtin\prompt-common\fragments\*') {
            $candidates += Join-Path $root "plugins\workbuddy-builtin\prompt-common\fragments\$fileName"
            $candidates += Join-Path $root "fragments\$fileName"
            $candidates += Join-Path $root $fileName
        } elseif ($targetRelative -like 'plugins\workbuddy-builtin\welcomemode\*\prompt.tpl') {
            $mode = Split-Path (Split-Path $targetRelative -Parent) -Leaf
            $candidates += Join-Path $root "plugins\workbuddy-builtin\welcomemode\$mode\prompt.tpl"
            $candidates += Join-Path $root "welcome-mode\$mode.tpl"
            $candidates += Join-Path $root "welcome-mode\$mode\prompt.tpl"
        } elseif ($targetRelative -like 'plugins\workbuddy-builtin\interactionmode\*') {
            $rel = $targetRelative.Substring(('plugins\workbuddy-builtin\').Length)
            $candidates += Join-Path $root "plugins\workbuddy-builtin\$rel"
            $candidates += Join-Path $root "plugin-prompts\$rel"
            $candidates += Join-Path $root $fileName
        } elseif ($targetRelative -like 'plugins\workbuddy-builtin\*') {
            $rel = $targetRelative.Substring(('plugins\workbuddy-builtin\').Length)
            $candidates += Join-Path $root "plugins\workbuddy-builtin\$rel"
            $candidates += Join-Path $root "plugin-prompts\$rel"
            $candidates += Join-Path $root $fileName
        } else {
            $candidates += Join-Path $root $fileName
        }
    }
    return $candidates
}

function Resolve-SourcePath([string]$targetRelative, [string]$explicitSource) {
    if ($explicitSource) {
        if (-not (Test-FileLong $explicitSource)) {
            throw "Source not found: $explicitSource"
        }
        return [IO.Path]::GetFullPath($explicitSource)
    }
    foreach ($candidate in (Get-SourceCandidates $targetRelative)) {
        if (Test-FileLong $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }
    throw "No source file found for '$targetRelative'. Tried: $((Get-SourceCandidates $targetRelative) -join ', ')"
}

function Get-CustomMappings {
    $inputs = @()

    foreach ($root in $SourceRoots) {
        if (-not (Test-Path -LiteralPath $root)) { continue }

        foreach ($file in @(Get-ChildItem -LiteralPath $root -File -Filter "*.tpl" -ErrorAction SilentlyContinue)) {
            $inputs += [pscustomobject]@{ Base = $root; File = $file }
        }
        $templatesDir = Join-Path $root "templates"
        if (Test-Path -LiteralPath $templatesDir) {
            foreach ($file in @(Get-ChildItem -LiteralPath $templatesDir -File -Filter "*.tpl" -ErrorAction SilentlyContinue)) {
                $inputs += [pscustomobject]@{ Base = $root; File = $file }
            }
        }
        $fragmentsDir = Join-Path $root "fragments"
        if (Test-Path -LiteralPath $fragmentsDir) {
            foreach ($file in @(Get-ChildItem -LiteralPath $fragmentsDir -File -Filter "*.md" -ErrorAction SilentlyContinue)) {
                $inputs += [pscustomobject]@{ Base = $root; File = $file }
            }
        }
        $welcomeDir = Join-Path $root "welcome-mode"
        if (Test-Path -LiteralPath $welcomeDir) {
            foreach ($file in @(Get-ChildItem -LiteralPath $welcomeDir -File -Filter "*.tpl" -ErrorAction SilentlyContinue)) {
                $inputs += [pscustomobject]@{ Base = $root; File = $file }
            }
        }
        # runtime-templates: 真正被运行时读取的 templates\*.tpl（user-context / 模式切换提醒），默认部署
        $runtimeDir = Join-Path $root "runtime-templates"
        if (Test-Path -LiteralPath $runtimeDir) {
            foreach ($file in @(Get-ChildItem -LiteralPath $runtimeDir -File -Filter "*.tpl" -ErrorAction SilentlyContinue)) {
                $inputs += [pscustomobject]@{ Base = $root; File = $file }
            }
        }
        $pluginsDir = Join-Path $root "plugins\workbuddy-builtin"
        if (Test-Path -LiteralPath $pluginsDir) {
            foreach ($file in @(Get-ChildItem -LiteralPath $pluginsDir -File -Recurse -ErrorAction SilentlyContinue |
                Where-Object { $_.Extension -in '.md', '.txt', '.tpl' -and $_.Name -ne 'README.md' -and -not $_.Name.StartsWith('_') })) {
                $inputs += [pscustomobject]@{ Base = $root; File = $file }
            }
        }
        $pluginPromptsDir = Join-Path $root "plugin-prompts"
        if (Test-Path -LiteralPath $pluginPromptsDir) {
            foreach ($file in @(Get-ChildItem -LiteralPath $pluginPromptsDir -File -Recurse -ErrorAction SilentlyContinue |
                Where-Object { $_.Extension -in '.md', '.txt', '.tpl' -and $_.Name -ne 'README.md' -and -not $_.Name.StartsWith('_') })) {
                $inputs += [pscustomobject]@{ Base = $root; File = $file }
            }
        }
    }

    $byTarget = @{}
    foreach ($input in $inputs) {
        $file = $input.File
        $basePath = (Resolve-Path -LiteralPath $input.Base).Path
        $rel = Normalize-RelativePath $file.FullName.Substring($basePath.Length + 1)
        $target = ""

        if ($rel -like 'runtime-templates\*') {
            # 这些 tpl 会被运行时读取（user-context / craft_mode 提醒），不受 -IncludeTemplates 门控
            $target = "templates\$($file.Name)"
        } elseif ($rel -like 'templates\*') {
            # templates\*.tpl 只被 [PromptRenderer] 的旁路渲染读取，不进入模型请求（见 README「实际生效链路」）
            if (-not $IncludeTemplates) { continue }
            $target = $rel
        } elseif ($rel -like 'fragments\*') {
            $target = "plugins\workbuddy-builtin\prompt-common\fragments\$($file.Name)"
        } elseif ($rel -like 'welcome-mode\*') {
            $mode = [IO.Path]::GetFileNameWithoutExtension($file.Name)
            $target = "plugins\workbuddy-builtin\welcomemode\$mode\prompt.tpl"
        } elseif ($rel -like 'plugins\workbuddy-builtin\*') {
            $target = $rel
        } elseif ($rel -like 'plugin-prompts\*') {
            $sub = $rel.Substring(('plugin-prompts\').Length)
            if ($sub -notmatch '\') { continue }
            if ($sub -like 'templates\*' -or $sub -like 'plugins\*') {
                $target = $sub
            } else {
                $target = "plugins\workbuddy-builtin\$sub"
            }
        } elseif ($rel -like '*.custom.tpl') {
            if (-not $IncludeTemplates) { continue }
            $name = $file.Name -replace '\.custom\.tpl$', '.tpl'
            $target = "templates\$name"
        } elseif ($rel -like '*.tpl') {
            if (-not $IncludeTemplates) { continue }
            $target = "templates\$($file.Name)"
        } elseif ($rel -like '*.md') {
            $target = "plugins\workbuddy-builtin\prompt-common\fragments\$($file.Name)"
        }

        if ($target -and -not $byTarget.ContainsKey($target)) {
            $byTarget[$target] = [pscustomobject]@{
                Target = $target
                Source = $file.FullName
            }
        }
    }

    if ($byTarget.Count -eq 0) {
        throw "No applicable prompt files found under: $($SourceRoots -join ', ')"
    }
    return @($byTarget.Values | Sort-Object Target)
}

function Infer-TargetFromSource([string]$path) {
    $normalized = Normalize-RelativePath $path
    # Prepend a separator so relative paths (welcome-mode\work.tpl) still match
    $probe = '\' + $normalized.TrimStart('\')
    if ($probe -match '(?i)\\plugins\\workbuddy-builtin\\(.+)$') {
        return "plugins\workbuddy-builtin\" + ($Matches[1] -replace '/', '\')
    }
    if ($probe -match '(?i)\\plugin-prompts\\(.+)$') {
        return "plugins\workbuddy-builtin\" + ($Matches[1] -replace '/', '\')
    }
    if ($probe -match '(?i)\\runtime-templates\\([^\\]+\.tpl)$') {
        return "templates\" + $Matches[1]
    }
    if ($probe -match '(?i)\\templates\\(.+\.tpl)$') {
        return "templates\" + $Matches[1]
    }
    if ($probe -match '(?i)\\fragments\\(.+\.md)$') {
        return "plugins\workbuddy-builtin\prompt-common\fragments\" + $Matches[1]
    }
    if ($probe -match '(?i)\\welcome-mode\\([^\\]+)\.tpl$') {
        return "plugins\workbuddy-builtin\welcomemode\" + $Matches[1] + "\prompt.tpl"
    }
    $name = [IO.Path]::GetFileName($path)
    if ($name -like '*.custom.tpl') {
        return "templates\" + ($name -replace '\.custom\.tpl$', '.tpl')
    }
    if ($name -like '*.tpl') {
        return "templates\$name"
    }
    if ($name -like '*.md') {
        return "plugins\workbuddy-builtin\prompt-common\fragments\$name"
    }
    throw "Cannot infer a target from source '$path'. Pass -Template explicitly."
}

if ($All -and $Source) {
    throw "-Source cannot be combined with -All. Use custom\ relative layout instead."
}

if ($Source -and -not $Template -and -not $All) {
    $Template = Infer-TargetFromSource $Source
}

if (-not $All -and -not $Template) {
    $All = $true
}

$cacheRoot = Get-PluginCacheRoot $PluginCacheRoot
$wb = $null
if ($Target -and -not $targetProfile.Installed) {
    if (-not $NoInstall) {
        throw "Target '$($targetProfile.Name)' install folder not found: '$($targetProfile.InstallPath)'. Pass -WorkBuddyPath '<folder containing WorkBuddy*.exe>' to pin it."
    }
    Write-Host "Target '$($targetProfile.Name)' install folder not found; continuing with cache-only (-NoInstall)." -ForegroundColor Yellow
} else {
    $wb = if ($WorkBuddyPath) { $WorkBuddyPath } elseif ($targetProfile.Installed) { $targetProfile.InstallPath } else { $null }
    if (-not $wb -and -not $NoInstall) {
        throw "Install folder not found. Pass -WorkBuddyPath '<folder containing WorkBuddy*.exe>', or -NoInstall for cache-only."
    }
    if (-not $wb) {
        Write-Host "Install folder not found; continuing with cache-only (-NoInstall)." -ForegroundColor Yellow
    }
}

if ($wb) {
    $resourceRoot = Get-ResourceRoot $wb
    $versionFile = Join-Path $wb "version"
    $version = if (Test-Path -LiteralPath $versionFile) {
        (Get-Content -LiteralPath $versionFile -Raw).Trim()
    } else {
        "unknown"
    }
} else {
    if (-not (Test-Path -LiteralPath $cacheRoot)) {
        throw "WorkBuddy plugin cache not found: $cacheRoot. Pass -PluginCacheRoot or start WorkBuddy once."
    }
    $version = Get-BuiltinBundleVersion $cacheRoot
    if (-not $version) {
        throw "WorkBuddy plugin cache has no version directory: $cacheRoot. Start WorkBuddy once, then retry."
    }
    $resourceRoot = Join-Path $cacheRoot "__install__"
}

if ($All) {
    $mappings = Get-CustomMappings
} elseif ($Template) {
    $target = Resolve-TargetRelative $Template
    $mappings = @([pscustomobject]@{
        Target = $target
        Source = (Resolve-SourcePath $target $Source)
    })
} else {
    throw "Specify -Template <relative path or file name> or -All."
}

$createdSkills = @(Ensure-BuiltinSkillCaches $ProjectRoot $cacheRoot $resourceRoot $Skill)
$registeredSkills = @()
if ($createdSkills.Count -gt 0 -and -not $NoRegisterSkills) {
    $registeredSkills = @(Register-BuiltinSkills @($createdSkills | ForEach-Object { $_.Plugin }) $cacheRoot $createdSkills[0].Version)
}

if ($Skill) {
    $mappings = @($mappings | Where-Object { $_.Target -like "plugins\workbuddy-builtin\skills\$Skill\*" })
    if ($mappings.Count -eq 0) {
        throw "No prompt files found for builtin skill '$Skill' under plugins\workbuddy-builtin\skills\$Skill"
    }
}

$prepared = @()
$skipped = @()
foreach ($mapping in $mappings) {
    $targetPath = Join-Path $resourceRoot $mapping.Target
    $targetFull = [IO.Path]::GetFullPath($targetPath)
    $rootFull = [IO.Path]::GetFullPath($resourceRoot)
    if (-not $targetFull.StartsWith($rootFull, [StringComparison]::OrdinalIgnoreCase)) {
        throw "Target escapes the resources directory: $($mapping.Target)"
    }

    $sourcePath = [IO.Path]::GetFullPath($mapping.Source)
    $cacheTargets = @()
    foreach ($ct in @(Get-CacheTargets $mapping.Target $cacheRoot)) {
        $cacheTargets += [pscustomobject]@{
            Path = $ct.Path
            Relative = $ct.Relative
            Exists = (Test-FileLong $ct.Path)
            OriginalHash = if (Test-FileLong $ct.Path) { Get-FileHashLong $ct.Path } else { "" }
            OriginalSize = if (Test-FileLong $ct.Path) { ([IO.FileInfo]::new((Get-LongPath $ct.Path))).Length } else { 0 }
        }
    }

    $installExists = if ($NoInstall) { $false } else { Test-FileLong $targetFull }
    $installParent = [IO.Path]::GetDirectoryName($targetFull)
    $canCreate = (Test-Path -LiteralPath $installParent)
    if (-not $canCreate) {
        # 允许在已存在的上一级目录下新建一层（例如新增内置技能 skills\<name>\）
        $grandParent = [IO.Path]::GetDirectoryName($installParent)
        if ($grandParent -and (Test-Path -LiteralPath $grandParent)) { $canCreate = $true }
    }
    if (-not $installExists -and $cacheTargets.Count -eq 0 -and -not $canCreate -and -not $NoInstall) {
        throw "Target file not found and its directory does not exist: $targetFull"
    }
    if (-not $installExists -and -not $canCreate) {
        $skipped += $mapping.Target
    }

    $prepared += [pscustomobject]@{
        Target = $mapping.Target
        TargetPath = $targetFull
        TargetExists = $installExists
        CanCreate = $canCreate
        Source = $sourcePath
        OriginalHash = if ($installExists) { Get-FileHashLong $targetFull } else { "" }
        NewHash = Get-FileHashLong $sourcePath
        OriginalSize = if ($installExists) { ([IO.FileInfo]::new((Get-LongPath $targetFull))).Length } else { 0 }
        CacheTargets = $cacheTargets
    }
}

$wbLabel = if ($wb) { $wb } else { "(not detected; cache-only)" }
Write-Host ("Target:      {0} ({1})" -f $targetProfile.Name, $targetProfile.Label)
Write-Host "ConfigDir:   $ConfigDir"
Write-Host "WorkBuddy:   $wbLabel"
Write-Host "Version:     $version"
Write-Host "Resources:   $resourceRoot"
Write-Host "PluginCache: $cacheRoot"
if ($Overlay) { Write-Host "Overlay:     $Overlay" }
if ($NoPluginCache) { Write-Host "PluginCache: disabled (-NoPluginCache)" }
Write-Host ("Cache mode:  {0} version dir(s) per plugin" -f $CacheVersions)
Write-Host ""

$installCount = @($prepared | Where-Object { $_.TargetExists -or $_.CanCreate }).Count
$cacheTotal = 0
foreach ($item in $prepared) { $cacheTotal += @($item.CacheTargets).Count }

Write-Host ("Mappings:      {0}" -f $prepared.Count)
if ($NoInstall) {
    Write-Host "Install writes:0 (disabled -NoInstall)"
} else {
    Write-Host ("Install writes:{0}" -f $installCount)
}
if ($createdSkills.Count -gt 0) {
    Write-Host ("New skills:    {0} cache dir(s) created, {1} registered" -f $createdSkills.Count, $registeredSkills.Count)
    foreach ($c in $createdSkills) { Write-Host ("  [new]     {0}\{1}" -f $c.Plugin, $c.Version) }
}
Write-Host ("Cache copies:  {0}" -f $cacheTotal)
Write-Host ""

if ($PSBoundParameters.ContainsKey('Verbose')) {
    foreach ($item in $prepared) {
        $caches = @($item.CacheTargets)
        if ($caches.Count -eq 0) {
            Write-Host ("  [install] {0}" -f $item.Target)
            continue
        }
        Write-Host ("  [dual]    {0}" -f $item.Target)
        foreach ($ct in $caches) {
            Write-Host ("              -> {0} (exists={1})" -f $ct.Relative, $ct.Exists)
        }
    }
    Write-Host ""
} else {
    Write-Host "(run with -Verbose for per-file mapping)"
    Write-Host ""
}
if ($skipped.Count -gt 0 -and $wb) {
    Write-Host ""
    Write-Host "Install copy missing (cache-only targets): $($skipped -join ', ')"
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
    if ($Overlay) { $argLine += " -Overlay `"$Overlay`"" }
    if ($All) { $argLine += " -All" }
    if ($Template) { $argLine += " -Template `"$Template`"" }
    if ($Source) { $argLine += " -Source `"$Source`"" }
    if ($UseCustom) { $argLine += " -UseCustom" }
    if ($PluginCacheRoot) { $argLine += " -PluginCacheRoot `"$PluginCacheRoot`"" }
    if ($CacheVersions) { $argLine += " -CacheVersions $CacheVersions" }
    if ($NoPluginCache) { $argLine += " -NoPluginCache" }
    if ($IncludeTemplates) { $argLine += " -IncludeTemplates" }
    if ($Skill) { $argLine += " -Skill `"$Skill`"" }
    if ($NoInstall) { $argLine += " -NoInstall" }
    if ($NoRegisterSkills) { $argLine += " -NoRegisterSkills" }
    Start-Process -FilePath "powershell.exe" -Verb RunAs -ArgumentList $argLine
    exit 0
}

$stamp = Get-Date -Format "yyyyMMdd-HHmmss"
$backupDir = Join-Path $BackupRoot $stamp
$suffix = 2
while (Test-Path -LiteralPath $backupDir) {
    $backupDir = Join-Path $BackupRoot "$stamp-$suffix"
    $suffix++
}
$filesRoot = Join-Path $backupDir "files"
$cacheFilesRoot = Join-Path $backupDir "files-cache"
New-Item -ItemType Directory -Path $filesRoot -Force | Out-Null

$entries = @()
$cacheCount = 0
foreach ($item in $prepared) {
    if ($item.TargetExists) {
        $backupFile = Join-Path $filesRoot $item.Target
        Copy-FileLong $item.TargetPath $backupFile
    }

    $cacheEntries = @()
    foreach ($ct in @($item.CacheTargets)) {
        if ($ct.Exists) {
            $cacheBackup = Join-Path $cacheFilesRoot $ct.Relative
            Copy-FileLong $ct.Path $cacheBackup
            $cacheCount++
        }
        $cacheEntries += [pscustomobject]@{
            path = $ct.Relative
            existed = $ct.Exists
            originalHash = $ct.OriginalHash
            newHash = $item.NewHash
            originalSize = $ct.OriginalSize
        }
    }

    $entries += [pscustomobject]@{
        target = $item.Target
        source = $item.Source
        targetExisted = $item.TargetExists
        originalHash = $item.OriginalHash
        newHash = $item.NewHash
        originalSize = $item.OriginalSize
        cacheTargets = $cacheEntries
    }
}

$manifest = [pscustomobject]@{
    target = $targetProfile.Name
    targetLabel = $targetProfile.Label
    configDir = $ConfigDir
    version = $version
    workBuddyPath = $wb
    pluginCacheRoot = $cacheRoot
    createdAt = (Get-Date -Format o)
    status = "pending"
    entries = $entries
}
$manifestPath = Join-Path $backupDir "manifest.json"
Write-TextNoBom $manifestPath ($manifest | ConvertTo-Json -Depth 8)

$infoText = @"
Target=$($targetProfile.Name)
TargetLabel=$($targetProfile.Label)
ConfigDir=$ConfigDir
WorkBuddyPath=$wb
Version=$version
PluginCacheRoot=$cacheRoot
CreatedAt=$(Get-Date -Format o)
Files=$($entries.Count)
CacheFiles=$cacheCount
"@
Write-TextNoBom (Join-Path $backupDir "BACKUP_INFO.txt") $infoText

$written = 0
foreach ($item in $prepared) {
    if (-not $NoInstall -and ($item.TargetExists -or $item.CanCreate)) {
        $installDir = [IO.Path]::GetDirectoryName($item.TargetPath)
        if (-not (Test-Path -LiteralPath $installDir)) { New-Item -ItemType Directory -Path $installDir -Force | Out-Null }
        Copy-FileLong $item.Source $item.TargetPath
        $after = Get-FileHashLong $item.TargetPath
        if ($after -ne $item.NewHash) {
            throw "Hash mismatch after copy: $($item.Target) target=$after expected=$($item.NewHash)"
        }
        $written++
    }

    foreach ($ct in @($item.CacheTargets)) {
        $cacheRootFull = [IO.Path]::GetFullPath($cacheRoot)
        $ctFull = [IO.Path]::GetFullPath($ct.Path)
        if (-not $ctFull.StartsWith($cacheRootFull, [StringComparison]::OrdinalIgnoreCase)) {
            throw "Cache target escapes the plugin cache directory: $($ct.Path)"
        }
        Copy-FileLong $item.Source $ct.Path
        $after = Get-FileHashLong $ct.Path
        if ($after -ne $item.NewHash) {
            throw "Hash mismatch after copy: $($ct.Relative) target=$after expected=$($item.NewHash)"
        }
        $written++
    }
}


$manifest.status = "applied"
Write-TextNoBom $manifestPath ($manifest | ConvertTo-Json -Depth 8)

Write-Host ""
Write-Host "Applied successfully: $written file(s) ($($prepared.Count) prompt target(s), $cacheCount cache file(s) backed up)."
Write-Host "Backup: $backupDir"
Write-Host "No restart needed: open a new chat session to pick up the new prompt."