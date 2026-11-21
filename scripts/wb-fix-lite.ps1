# wb-fix-lite.ps1 - dependency-free last-mile fix for the current_time block.
#
# Why this file exists: every previous fixer needed Python, Node, a registry
# entry, exact install paths, or exact code anchors ("1849", "g"/"w",
# "t.currentTime&&(r.current_time=..."). On an unknown build any of those can
# be missing, and then nothing happens.
#
# This one needs only Windows PowerShell 5.1:
#   * finds the install by env / registry / running process / shortcut / bounded scan
#   * finds the runtime chunk by CONTENT (modelInput.instructions), never by name
#   * prepends a self-contained fetch scrub at the very top of the chunk
#     (no anchor needed - prepending works on any build)
#   * keeps a backup, is idempotent, and can be reverted with -Restore
#
# It does NOT rewrite app.asar (that needs a proper asar rebuild); the fetch
# scrub alone removes <current_time> / empty additional-data shells from every
# request that leaves the process.
param(
    [switch]$DryRun,
    [switch]$Restore,
    [string[]]$Install = @()
)

$ErrorActionPreference = "Stop"
$BEGIN = "/*__wbTrimLite*/"
$END = "/*__wbTrimLiteEnd*/"

$SNIPPET = @'
/*__wbTrimLite*/
(function(){
  try{
    var G=typeof globalThis!=="undefined"?globalThis:global;
    var f=G.fetch;
    if(typeof f!=="function"||f.__wbLite)return;
    var B=String.fromCharCode(92);
    function blank(s){
      return s.split(B+"n").join("").split(B+"r").join("").split(B+"t").join("").split(B+'"').join("").replace(/\s+/g,"")==="";
    }
    function drop(s,role,emptyOnly){
      var pos=0;
      for(;;){
        var a=s.indexOf(role,pos);
        if(a<0)return s;
        var open=s.lastIndexOf("<system-reminder",a);
        var gt=s.indexOf(">",a);
        if(open<0||gt<0){pos=a+role.length;continue}
        if(s.slice(open,gt).indexOf(role)<0){pos=a+role.length;continue}
        var close=s.indexOf("</system-reminder>",gt);
        if(close<0){pos=a+role.length;continue}
        var end=close+18;
        if(emptyOnly&&!blank(s.slice(gt+1,close))){pos=end;continue}
        s=s.slice(0,open)+s.slice(end);
        pos=open;
      }
    }
    function clean(s){
      s=s.replace(/<current_time>[\s\S]*?<\/current_time>\s*/g,"");
      s=s.replace(/<additional_data>[\s\S]*?<\/additional_data>\s*/g,"");
      return drop(s,"additional-data",true);
    }
    var w=function(input,init){
      try{
        if(init&&typeof init.body==="string"&&(init.body.indexOf("additional-data")>=0||init.body.indexOf("<current_time")>=0)){
          var b=clean(init.body);
          if(b!==init.body)init=Object.assign({},init,{body:b});
        }
      }catch(e){}
      return f.call(this,input,init);
    };
    w.__wbLite=true;
    G.fetch=w;
  }catch(e){}
})();
/*__wbTrimLiteEnd*/
'@

function Test-Asar([string]$root) { Test-Path -LiteralPath (Join-Path $root "resources\app.asar") }

function Find-Installs {
    $roots = New-Object System.Collections.Generic.List[string]
    if ($env:WORKBUDDY_INSTALL) { $roots.Add($env:WORKBUDDY_INSTALL) }
    $keys = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    foreach ($k in $keys) {
        Get-ItemProperty $k -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match "WorkBuddy" } | ForEach-Object {
            foreach ($raw in @($_.InstallLocation, $_.DisplayIcon, $_.UninstallString)) {
                if (-not $raw) { continue }
                $p = ($raw -replace '^"', '') -replace '",.*$', ''
                $p = $p -replace ',\s*\d+$', ''
                if (Test-Path -LiteralPath $p) {
                    if (Test-Path -LiteralPath $p -PathType Container) { $roots.Add($p) }
                    else { $roots.Add((Split-Path -Parent $p)) }
                }
            }
        }
    }
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match "WorkBuddy" } |
        ForEach-Object { if ($_.ExecutablePath) { $roots.Add((Split-Path -Parent $_.ExecutablePath)) } }

    $sh = New-Object -ComObject WScript.Shell
    foreach ($d in @(
        (Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs"),
        (Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"),
        [Environment]::GetFolderPath("Desktop"),
        [Environment]::GetFolderPath("CommonDesktopDirectory")
    )) {
        if (-not (Test-Path -LiteralPath $d)) { continue }
        Get-ChildItem -LiteralPath $d -Filter *.lnk -Recurse -Depth 2 -ErrorAction SilentlyContinue | ForEach-Object {
            $t = $sh.CreateShortcut($_.FullName).TargetPath
            if ($t -match "WorkBuddy.*\.exe$") { $roots.Add((Split-Path -Parent $t)) }
        }
    }
    foreach ($letter in @("C", "D", "E", "F", "G", "H")) {
        $drive = "${letter}:\"
        if (-not (Test-Path -LiteralPath $drive)) { continue }
        $bases = @("Program Files", "Program Files (x86)", "Apps", "Programs", "Software") | ForEach-Object { Join-Path $drive $_ }
        $users = Join-Path $drive "Users"
        if (Test-Path -LiteralPath $users) {
            Get-ChildItem -LiteralPath $users -Directory -ErrorAction SilentlyContinue | Select-Object -First 20 | ForEach-Object {
                $bases += (Join-Path $_.FullName "AppData\Local\Programs")
            }
        }
        foreach ($b in $bases) {
            if (-not (Test-Path -LiteralPath $b)) { continue }
            Get-ChildItem -LiteralPath $b -Directory -Recurse -Depth 1 -ErrorAction SilentlyContinue |
                Where-Object { $_.Name -match "WorkBuddy" } | ForEach-Object { $roots.Add($_.FullName) }
        }
    }
    $out = New-Object System.Collections.Generic.List[string]
    foreach ($r in ($roots | Select-Object -Unique)) {
        if (-not $r) { continue }
        if (-not (Test-Asar $r)) { continue }
        if ($out -notcontains $r) { $out.Add($r) }
    }
    return $out
}

function Find-Chunks([string]$root) {
    $unpacked = Join-Path $root "resources\app.asar.unpacked"
    if (-not (Test-Path -LiteralPath $unpacked)) { return @() }
    $hits = New-Object System.Collections.Generic.List[string]
    Get-ChildItem -LiteralPath $unpacked -Filter *.mjs -Recurse -Depth 5 -ErrorAction SilentlyContinue |
        Where-Object { $_.Length -lt 24MB } | ForEach-Object {
            $s = [IO.File]::ReadAllText($_.FullName)
            if ($s.Contains("modelInput.instructions") -and $s.Contains("modelInput.input")) {
                if (-not $hits.Contains($_.FullName)) { $hits.Add($_.FullName) }
            }
        }
    return $hits
}

function Invoke-One([string]$path) {
    $text = [IO.File]::ReadAllText($path)
    $has = $text.Contains($BEGIN)
    Write-Host ("  chunk : {0}" -f $path)
    if ($Restore) {
        if (-not $has) { Write-Host "         没有轻量补丁，无需还原" -ForegroundColor DarkGray; return }
        $i = $text.IndexOf($BEGIN); $j = $text.IndexOf($END)
        if ($i -lt 0 -or $j -lt 0) { Write-Host "         标记不完整，放弃" -ForegroundColor Red; return }
        $clean = $text.Substring(0, $i) + $text.Substring($j + $END.Length)
        if (-not $DryRun) {
            Copy-Item -LiteralPath $path -Destination ($path + ".lite.bak") -Force
            [IO.File]::WriteAllText($path, $clean)
        }
        Write-Host "         已移除轻量补丁" -ForegroundColor Green
        return
    }
    if ($has) { Write-Host "         已有轻量补丁（无需重复）" -ForegroundColor Green; return }
    $new = $SNIPPET + "`r`n" + $text
    if ($DryRun) { Write-Host ("         [dry-run] 将写入 {0:N0} -> {1:N0} 字节" -f $text.Length, $new.Length); return }

    $bak = $path + ".lite.bak"
    Copy-Item -LiteralPath $path -Destination $bak -Force
    [IO.File]::WriteAllText($path, $new)
    $check = [IO.File]::ReadAllText($path)
    $ok = $check.StartsWith($BEGIN) -and $check.EndsWith($text)
    if (-not $ok) {
        Copy-Item -LiteralPath $bak -Destination $path -Force
        Write-Host "         写入后校验失败，已回滚" -ForegroundColor Red
        return
    }
    Write-Host ("         已写入：{0:N0} -> {1:N0} 字节，备份 {2}" -f $text.Length, $new.Length, (Split-Path -Leaf $bak)) -ForegroundColor Green

    $node = Get-Command node -ErrorAction SilentlyContinue
    if ($node) {
        $r = & $node.Source --check $path 2>&1
        if ($LASTEXITCODE -ne 0) {
            Copy-Item -LiteralPath $bak -Destination $path -Force
            Write-Host ("         node --check 失败，已回滚：{0}" -f ($r -join " ")) -ForegroundColor Red
        } else { Write-Host "         node --check OK" -ForegroundColor Green }
    } else {
        Write-Host "         node 不在 PATH：跳过语法检查（写入前已做首尾一致性校验）" -ForegroundColor Yellow
    }
}

Write-Host "== wb-fix-lite (no Python / no Node / no anchors) =="
Write-Host ("time   : {0}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
if ($Restore) { Write-Host "mode   : restore" } elseif ($DryRun) { Write-Host "mode   : dry-run" } else { Write-Host "mode   : apply" }

$installs = if ($Install.Count -gt 0) { $Install } else { Find-Installs }
if (-not $installs -or $installs.Count -eq 0) {
    Write-Host "install: NOT FOUND" -ForegroundColor Red
    Write-Host "  pass -Install '<install root>' or set WORKBUDDY_INSTALL."
    exit 1
}
foreach ($root in $installs) {
    Write-Host ""
    Write-Host ("install: {0}" -f $root)
    $chunks = @(Find-Chunks $root)
    if ($chunks.Count -eq 0) { Write-Host "  没有承载 modelInput 的 chunk" -ForegroundColor Red; continue }
    foreach ($c in $chunks) { Invoke-One $c }
}
Write-Host ""
Write-Host "done. 新开一个聊天（或重启应用）才会加载新代码。" -ForegroundColor Cyan
exit 0
