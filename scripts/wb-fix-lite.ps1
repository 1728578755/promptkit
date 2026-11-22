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
#   * prepends a self-contained cleanup body at the very top of the chunk
#     (no anchor needed - prepending works on any build)
#   * the cleanup body is EXTRACTED from scripts\patch-cli-trim.py, so there is
#     one single source of truth for the JS (SCRUB + HELPER)
#   * keeps a backup in %TEMP%\wb-fix-lite-backup\<stamp>\ (never in the install
#     dir), is idempotent, and can be reverted with -Restore
#   * exit codes are honest: 0 only when a chunk was really written/restored and
#     verified; 1 = any failure (write error, rollback, no chunk); 2 = nothing to
#     do (dry-run / already patched / no chunk text found)
#
# It does NOT rewrite app.asar (that needs a proper asar rebuild); the exit
# cleanup alone removes <current_time> / <additional_data> shells, the eight
# retired policy blocks and injected user-context reminders from every request
# that leaves the process.
param(
    [switch]$DryRun,
    [switch]$Restore,
    [string[]]$Install = @(),
    [switch]$SelfElevate,
    [switch]$DumpInjectedJs,
    # Debug only: print the Single argument string -SelfElevate would hand to
    # Start-Process (quotes included) and exit without touching UAC.
    [switch]$DumpElevateArgs,
    [bool]$RefreshPrewarm = $true,
    # Captured by wb-fix-lite.cmd BEFORE elevation: under UAC-over-the-shoulder
    # the elevated process's %USERPROFILE%/%LOCALAPPDATA% belong to the admin.
    [string]$OrigUserProfile = "",
    [string]$OrigLocalAppData = "",
    [string]$OrigAppData = ""
)

$ErrorActionPreference = "Stop"

# Elevation switch: wb-fix-lite.cmd calls this without admin rights; -SelfElevate
# re-launches the script elevated and keeps the window open (F-08). Arguments are
# passed as ONE pre-quoted string, never as an array of bare elements, because
# PS 5.1 Start-Process -ArgumentList does not quote elements that contain spaces.
if ($SelfElevate) {
    $argLine = "-NoExit -NoProfile -ExecutionPolicy Bypass -File `"$PSCommandPath`""
    # WBFIX_ARGS is the caller's raw %*: already quoted exactly as typed, so it
    # is appended verbatim (never split on spaces - that used to break -Install).
    if ($env:WBFIX_ARGS) {
        $argLine += " " + $env:WBFIX_ARGS
    } else {
        if ($DryRun) { $argLine += " -DryRun" }
        if ($Restore) { $argLine += " -Restore" }
        if (-not $RefreshPrewarm) { $argLine += " -RefreshPrewarm:`$false" }
        foreach ($i in $Install) {
            $argLine += " -Install `"$i`""
        }
    }
    # The original profile paths are not part of WBFIX_ARGS (the cmd passes them
    # as real parameters), so carry them across the elevation boundary here.
    foreach ($pair in @(@('OrigUserProfile', $OrigUserProfile), @('OrigLocalAppData', $OrigLocalAppData), @('OrigAppData', $OrigAppData))) {
        if ($pair[1]) { $argLine += (" -{0} `"{1}`"" -f $pair[0], $pair[1]) }
    }
    if ($DumpElevateArgs) {
        Write-Output $argLine
        exit 0
    }

    try {
        Start-Process -FilePath "powershell.exe" -ArgumentList $argLine -Verb RunAs | Out-Null
        exit 0
    } catch {
        Write-Host ("[FAIL] 无法提权启动（UAC 被取消？）：{0}" -f $_.Exception.Message) -ForegroundColor Red
        Write-Host "       请右键 PowerShell「以管理员身份运行」，再执行本脚本。" -ForegroundColor Yellow
        exit 1
    }
}

$BEGIN = "/*__wbLite*/"
$END = "/*__wbLiteEnd*/"
$SCRUB_BEGIN = "/*__wbTrimScrub*/"
$SCRUB_END = "/*__wbTrimScrubEnd*/"
# v1 wrote these (and left them behind); only used to recognize / remove them.
$OLD_BEGIN = "/*__wbTrimLite*/"
$OLD_END = "/*__wbTrimLiteEnd*/"
# patch-cli-trim's own markers: if any is present the chunk already has the
# (better, modelInput-level) trim patch and must not get a second copy.
$TRIM_MARKERS = @("/*__wbTrimV3*/", "/*__wbTrimScrub*/", "__wbStripLegacyString")

# $PSScriptRoot / $MyInvocation are only reliable inside a function or at the
# top level of a *file*: when the body is dot-sourced or wrapped, they can be
# empty here. $PSCommandPath is always this file's full path.
$script:AppRoot = $PSScriptRoot
if (-not $script:AppRoot) { $script:AppRoot = Split-Path -Parent $MyInvocation.MyCommand.Path }
if (-not $script:AppRoot -and $PSCommandPath) { $script:AppRoot = Split-Path -Parent $PSCommandPath }
if (-not $script:AppRoot) {
    Write-Host "[FAIL] 无法确定脚本所在目录（PSCommandPath 为空），请用 -File 方式运行本脚本。" -ForegroundColor Red
    exit 1
}
$script:Stamp = Get-Date -Format "yyyyMMdd-HHmmss"

# ---------------------------------------------------------------------------
# JS source: extracted from patch-cli-trim.py (single source of truth, G2)
# ---------------------------------------------------------------------------

function Read-TextFile([string]$Path) {
    return [IO.File]::ReadAllText($Path, [Text.Encoding]::UTF8)
}

# ---------------------------------------------------------------------------
# Tiny reader for the constants in patch-cli-trim.py.
#
# The file used to define SCRUB/HELPER as single r'''...''' literals; since the
# E-group rewrite they are Python concatenations:
#     HELPER = (HELPER_BEGIN + "\n" + CLEANER_JS + "\n" + RUNTIME_JS + ...)
#     SCRUB  = SCRUB_BEGIN + "\n" + SCRUB_JS + "\n" + SCRUB_END
# So: collect every r'''...''' literal, then evaluate NAME = <name|"str"> + ...
# for the requested constant. No Python interpreter is needed (this script must
# run on machines without Python).
# ---------------------------------------------------------------------------

function Get-PyLiterals([string]$Text) {
    $map = @{}
    foreach ($m in [regex]::Matches($Text, "(?s)(?m)^([A-Z][A-Z0-9_]*)\s*=\s*r?'''(.*?)'''")) {
        $map[$m.Groups[1].Value] = $m.Groups[2].Value
    }
    return $map
}

# Bracket depth of a Python expression, ignoring brackets inside string
# literals (b"var x={" and "..." would otherwise look like unbalanced code).
function Measure-PyDepth([string]$Expr) {
    $depth = 0
    $q = [char]0
    for ($i = 0; $i -lt $Expr.Length; $i++) {
        $c = $Expr[$i]
        if ($q -ne [char]0) {
            if ($c -eq '\') { $i++; continue }
            if ($c -eq $q) { $q = [char]0 }
            continue
        }
        if ($c -eq '"' -or $c -eq "'") { $q = $c; continue }
        if ($c -eq '#' -and ($i -eq 0 -or $Expr[$i - 1] -eq ' ' -or $Expr[$i - 1] -eq "`n")) {
            $nl = $Expr.IndexOf("`n", $i)
            if ($nl -lt 0) { break }
            $i = $nl
            continue
        }
        if ($c -eq '(' -or $c -eq '[' -or $c -eq '{') { $depth++ }
        elseif ($c -eq ')' -or $c -eq ']' -or $c -eq '}') { $depth-- }
    }
    return $depth
}

# Collects `NAME = expression` up to the end of the (possibly parenthesised,
# possibly multi-line) expression. Returns @{Name = rawExpressionText}.
# Triple-quoted r''' literals are skipped here (Get-PyLiterals owns them);
# scanning their JS bodies for brackets would swallow the rest of the file.
function Get-PyAssignments([string]$Text) {
    $map = @{}
    $lines = $Text -split "`n"
    $inTriple = $false
    for ($i = 0; $i -lt $lines.Count; $i++) {
        $line = $lines[$i]
        if ($inTriple) {
            if ($line.Contains("'''")) { $inTriple = $false }
            continue
        }
        if ($line -match "r'''") { $inTriple = $true; continue }
        $m = [regex]::Match($line, '^([A-Z][A-Z0-9_]*)\s*=\s*(.*)$')
        if (-not $m.Success) { continue }
        $name = $m.Groups[1].Value
        $buf = New-Object System.Text.StringBuilder
        [void]$buf.Append($m.Groups[2].Value)
        # Keep consuming lines while brackets are unbalanced or the line ends
        # with a continuation operator.
        while ((Measure-PyDepth $buf.ToString()) -gt 0 -or ($buf.ToString().TrimEnd().EndsWith("+"))) {
            if ($i + 1 -ge $lines.Count) { break }
            $i++
            [void]$buf.Append("`n")
            [void]$buf.Append($lines[$i])
        }
        $map[$name] = $buf.ToString()
    }
    return $map
}

function Get-PyStringLiteral([string]$Raw) {
    # One quoted string at the start of the expression; anything after it (a
    # trailing '#' comment, whitespace) is ignored. Only the escapes that occur
    # in these constants are decoded.
    $s = $Raw.TrimStart()
    if ($s.Length -lt 2) { return $null }
    $q = $s[0]
    if (($q -ne '"') -and ($q -ne "'")) { return $null }
    $sb = New-Object System.Text.StringBuilder
    $i = 1
    while ($i -lt $s.Length) {
        $c = $s[$i]
        if ($c -eq '\' -and $i + 1 -lt $s.Length) {
            $n = $s[$i + 1]
            switch ($n) {
                'n' { [void]$sb.Append("`n") }
                'r' { [void]$sb.Append("`r") }
                't' { [void]$sb.Append("`t") }
                '\' { [void]$sb.Append('\') }
                '"' { [void]$sb.Append('"') }
                "'" { [void]$sb.Append("'") }
                default { [void]$sb.Append($n) }
            }
            $i += 2
            continue
        }
        if ($c -eq $q) { return $sb.ToString() }
        [void]$sb.Append($c)
        $i++
    }
    return $null
}

# Splits a Python concatenation on top-level '+' (outside quotes).
function Split-PyConcat([string]$Expr) {
    $parts = New-Object System.Collections.Generic.List[string]
    $cur = New-Object System.Text.StringBuilder
    $quote = [char]0
    $depth = 0
    for ($i = 0; $i -lt $Expr.Length; $i++) {
        $c = $Expr[$i]
        if ($quote -ne [char]0) {
            [void]$cur.Append($c)
            if ($c -eq '\' -and $i + 1 -lt $Expr.Length) { $i++; [void]$cur.Append($Expr[$i]); continue }
            if ($c -eq $quote) { $quote = [char]0 }
            continue
        }
        if ($c -eq '"' -or $c -eq "'") { $quote = $c; [void]$cur.Append($c); continue }
        if ($c -eq '(' -or $c -eq '[' -or $c -eq '{') { $depth++; [void]$cur.Append($c); continue }
        if ($c -eq ')' -or $c -eq ']' -or $c -eq '}') { $depth--; [void]$cur.Append($c); continue }
        if ($c -eq '+' -and $depth -le 0) { $parts.Add($cur.ToString()); [void]$cur.Clear(); continue }
        [void]$cur.Append($c)
    }
    $parts.Add($cur.ToString())
    return $parts
}

# Evaluates NAME = <literal|"string"> [+ ...] recursively.
function Get-PyConstant([string]$Text, [string]$Name) {
    $literals = Get-PyLiterals $Text
    $assign = Get-PyAssignments $Text
    $seen = @{}
    $evalName = $null
    $evalName = {
        param([string]$n)
        if ($seen.ContainsKey($n)) { throw "cyclic Python constant: $n" }
        $seen[$n] = $true
        if ($literals.ContainsKey($n)) { return [string]$literals[$n] }
        if (-not $assign.ContainsKey($n)) { return $null }
        $expr = ([string]$assign[$n]).Trim()
        # strip one pair of wrapping parentheses
        while ($expr.StartsWith("(") -and $expr.EndsWith(")")) {
            $inner = $expr.Substring(1, $expr.Length - 2)
            $d = 0; $ok = $true
            foreach ($ch in $inner.ToCharArray()) {
                if ($ch -eq '(') { $d++ } elseif ($ch -eq ')') { $d--; if ($d -lt 0) { $ok = $false; break } }
            }
            if (-not $ok) { break }
            $expr = $inner.Trim()
        }
        $sb = New-Object System.Text.StringBuilder
        foreach ($partRaw in (Split-PyConcat $expr)) {
            $part = $partRaw.Trim()
            if (-not $part) { continue }
            $lit = Get-PyStringLiteral $part
            if ($null -ne $lit) { [void]$sb.Append($lit); continue }
            if ($part -match '^r?''''''') { continue }   # unreachable safety
            $v = & $evalName $part
            if ($null -eq $v) { throw "cannot resolve Python constant: $part (in $n)" }
            [void]$sb.Append($v)
        }
        return $sb.ToString()
    }
    return (& $evalName $Name)
}

# The exit-cleanup body between /*__wbTrimScrub*/ and /*__wbTrimScrubEnd*/,
# exactly as patch-cli-trim.py carries it (G2: no hardcoded copy here).
# v3: one literal.  v4: SCRUB_BEGIN + SCRUB_JS + SCRUB_END. Both are handled by
# Get-PyConstant; if the assembled text still lacks the markers, fall back to
# the marker span found anywhere in the source.
#
# "Looks like the real block": exactly one start marker, exactly one end marker,
# at the ends, with actual code inside. A truncated span (the failure mode that
# would silently inject half a function) would miss the end marker or the body.
function Test-JsBlockComplete([string]$Js, [string]$Begin, [string]$End) {
    if (-not $Js) { return $false }
    if (-not $Js.StartsWith($Begin)) { return $false }
    if (-not $Js.EndsWith($End)) { return $false }
    if (([regex]::Matches($Js, [regex]::Escape($Begin))).Count -ne 1) { return $false }
    if (([regex]::Matches($Js, [regex]::Escape($End))).Count -ne 1) { return $false }
    if ($Js.Length -le ($Begin.Length + $End.Length + 20)) { return $false }
    return $Js.Contains("function")
}

function Read-ScrubBlock([string]$Path) {
    $text = Read-TextFile $Path
    $raw = $null
    try { $raw = Get-PyConstant $text "SCRUB" } catch { $raw = $null }
    if ($raw) {
        $a = $raw.IndexOf($SCRUB_BEGIN)
        $b = $raw.IndexOf($SCRUB_END)
        if (($a -ge 0) -and ($b -ge $a)) {
            $span = $raw.Substring($a, ($b - $a) + $SCRUB_END.Length)
            if (Test-JsBlockComplete $span $SCRUB_BEGIN $SCRUB_END) { return $span }
        }
        # Evaluated SCRUB lacks/has broken markers: do not trust it, fall back.
    }
    # Fallback: take the JS text between the marker *literals* in the file.
    $a = $text.IndexOf($SCRUB_BEGIN, [StringComparison]::Ordinal)
    while ($a -ge 0) {
        $b = $text.IndexOf($SCRUB_END, $a + $SCRUB_BEGIN.Length, [StringComparison]::Ordinal)
        if ($b -lt 0) { break }
        $span = $text.Substring($a, ($b - $a) + $SCRUB_END.Length)
        if (Test-JsBlockComplete $span $SCRUB_BEGIN $SCRUB_END) { return $span }
        $a = $text.IndexOf($SCRUB_BEGIN, $b, [StringComparison]::Ordinal)
    }
    return ""
}

function Read-HelperBlock([string]$Path) {
    $text = Read-TextFile $Path
    $raw = $null
    try { $raw = Get-PyConstant $text "HELPER" } catch { $raw = $null }
    if ($raw -and $raw.Contains("function")) { return $raw }
    return ""
}

# JSON-safe outlet (G2), used only while patch-cli-trim's SCRUB still runs a
# regex over the serialized body (F-10 Bug C: cross-message edits break JSON).
# It wraps the fetch the SCRUB installed, JSON.parse()s the body, cleans the
# message objects through HELPER's functions, and JSON.stringify()s it back.
# Bodies that are not JSON are passed through untouched. Once the E-group
# rewrite ships a JSON-safe SCRUB, this block is not emitted at all.
$JSON_SAFE_OUTLET_JS = @'
function __wbLiteInstallJsonSafe(){
  try{
    var G=typeof globalThis!=="undefined"?globalThis:global;
    var f=G.fetch;
    if(typeof f!=="function"||f.__wbJsonSafe)return;
    var strip=function(s,keepUC){
      var t=__wbStripLegacyString(s);
      if(!keepUC)t=__wbStripUserContext(t);
      return t;
    };
    var cleanMsgs=function(list){
      if(!Array.isArray(list))return list;
      var i,last=-1,changed=false,out=[];
      for(i=0;i<list.length;i++){if(__wbIsUser(list[i])&&!__wbIsReminderOnly(list[i]))last=i;}
      for(i=0;i<list.length;i++){
        var m=list[i];
        if(__wbIsUser(m)&&__wbIsReminderOnly(m)){changed=true;continue;}
        var x=__wbCleanMessage(m,i===last);
        if(x!==m)changed=true;
        if(x)out.push(x);else changed=true;
      }
      return changed?out:list;
    };
    var cleanBody=function(body){
      if(typeof body!=="string")return body;
      if(body.indexOf("additional-data")<0&&body.indexOf("<current_time")<0&&
         body.indexOf("tool_usage_policy")<0&&body.indexOf("user-context")<0)return body;
      var o;
      try{o=JSON.parse(body);}catch(e){return body;}
      if(!o||typeof o!=="object")return body;
      var changed=false,a,b,c,d;
      if(typeof o.instructions==="string"){a=strip(o.instructions,false);if(a!==o.instructions){o.instructions=a;changed=true;}}
      if(typeof o.system==="string"){b=strip(o.system,false);if(b!==o.system){o.system=b;changed=true;}}
      if(Array.isArray(o.messages)){c=cleanMsgs(o.messages);if(c!==o.messages){o.messages=c;changed=true;}}
      if(Array.isArray(o.input)){d=__wbCleanRuntimeInput(o.input);if(d!==o.input){o.input=d;changed=true;}}
      if(!changed)return body;
      try{return JSON.stringify(o);}catch(e){return body;}
    };
    var w=function(input,init){
      try{
        if(init&&typeof init.body==="string"){
          var nb=cleanBody(init.body);
          if(nb!==init.body)init=Object.assign({},init,{body:nb});
        }
      }catch(e){}
      return f.call(this,input,init);
    };
    w.__wbJsonSafe=true;
    w.__wbLite=true;
    G.fetch=w;
  }catch(e){}
}
'@

# Anchor-free transport hook (F-01). wb-fix-lite exists for the case where
# patch-cli-trim's *anchored* patch (modelInput wrapping / axiosToFetchAdapter
# wrapping) cannot be applied. Model requests then leave through
# Axios -> Node http/https, never through globalThis.fetch, so a fetch-only
# scrub is a no-op. Wrapping node:http(s) needs no anchor in the bundle: it is
# the last hop every Axios adapter must use.
#
# Safety: the body is only decoded/rewritten when the text contains one of the
# markers the cleaner removes; anything that is not valid JSON is passed
# through untouched. Any exception falls back to the original body. Requests
# built with .write() are never touched (only a single complete .end(body)).
$NODE_EGRESS_JS = @'
function __wbLiteInstallEgress(mod){
  // ESM chunks have no `require` and their namespace object is frozen, so the
  // patch must go on the CJS default export (same object the http adapter uses).
  // The namespace itself is also tried, which covers `require("node:http")`.
  try{
    if(!mod)return;
    var targets=[];
    if(mod.default)targets.push(mod.default);
    targets.push(mod);
    var installed=false;
    for(var i=0;i<targets.length;i++){if(__wbLitePatchHttp(targets[i]))installed=true;}
    // A frozen namespace that already delegates to the patched default export
    // is enough; only report failure when nothing took the patch.
    if(installed)__wbLiteEgressState.ok++;
    else if(targets[0]&&targets[0].__wbLiteEgress)__wbLiteEgressState.ok++;
    else __wbLiteEgressState.failed++;
  }catch(e){__wbLiteEgressState.failed++;}
}
function __wbLitePatchHttp(mod){
  try{
    if(!mod||mod.__wbLiteEgress)return false;
    var MARK=function(s){
      return s.indexOf("additional-data")>=0||s.indexOf("<current_time")>=0||
             s.indexOf("tool_usage_policy")>=0||s.indexOf("user-context")>=0;
    };
    var wrap=function(name){
      var orig=mod[name];
      if(typeof orig!=="function"||orig.__wbLiteWrapped)return;
      var w=function(){
        var req=orig.apply(mod,arguments);
        try{
          if(req&&typeof req.end==="function"&&typeof req.write==="function"&&!req.__wbLiteHooked){
            var wrote=false;
            var origWrite=req.write;
            req.write=function(){wrote=true;return origWrite.apply(req,arguments);};
            var origEnd=req.end;
            req.end=function(data){
              try{
                if(!wrote&&data!==undefined&&data!==null&&typeof data!=="function"){
                  var text=null;
                  if(typeof data==="string")text=data;
                  else if(Buffer.isBuffer(data))text=data.toString("utf8");
                  if(text!==null&&MARK(text)){
                    var nb=__wbLiteCleanJsonBody(text);
                    if(nb!==text){
                      var out=Buffer.from(nb,"utf8");
                      // A stale content-length would corrupt the request, so
                      // only swap when the header can be corrected; without a
                      // content-length Node uses chunked encoding (length-free).
                      var hadLen=false;
                      try{hadLen=req.getHeader&&req.getHeader("content-length")!==undefined;}catch(e){}
                      var fixed=false;
                      try{req.setHeader("content-length",String(out.length));fixed=true;}catch(e){}
                      if(!hadLen||fixed){arguments[0]=out;}
                      else{__wbLiteEgressState.skippedLength=(__wbLiteEgressState.skippedLength||0)+1;}
                    }
                  }
                }
              }catch(e){}
              return origEnd.apply(req,arguments);
            };
            req.__wbLiteHooked=true;
          }
        }catch(e){}
        return req;
      };
      w.__wbLiteWrapped=true;
      try{mod[name]=w;}catch(e){return false;}
      return mod[name]===w||(mod[name]&&mod[name].__wbLiteWrapped===true);
    };
    wrap("request");wrap("get");
    wrap("ClientRequest");
    try{mod.__wbLiteEgress=true;}catch(e){}
    return true;
  }catch(e){return false;}
}
function __wbLiteCleanJsonBody(body){
  if(typeof body!=="string")return body;
  var o;
  try{o=JSON.parse(body);}catch(e){return body;}
  if(!o||typeof o!=="object")return body;
  var changed=false,a,b,c;
  try{
    if(typeof o.instructions==="string"){a=__wbLiteStripText(o.instructions,false);if(a!==o.instructions){o.instructions=a;changed=true;}}
    if(typeof o.system==="string"){b=__wbLiteStripText(o.system,false);if(b!==o.system){o.system=b;changed=true;}}
    if(Array.isArray(o.messages)){c=__wbLiteCleanMessageList(o.messages);if(c!==o.messages){o.messages=c;changed=true;}}
    if(Array.isArray(o.input)){
      var d=typeof __wbCleanRuntimeInput==="function"?__wbCleanRuntimeInput(o.input):__wbLiteCleanMessageList(o.input);
      if(d!==o.input){o.input=d;changed=true;}
    }
  }catch(e){return body;}
  if(!changed)return body;
  try{return JSON.stringify(o);}catch(e){return body;}
}
function __wbLiteStripText(s,keepUC){
  try{
    var t=__wbStripLegacyString(s);
    if(!keepUC&&typeof __wbStripUserContext==="function")t=__wbStripUserContext(t);
    return t;
  }catch(e){return s;}
}
function __wbLiteCleanMessageList(list){
  try{
    if(typeof __wbCleanRuntimeInput==="function")return __wbCleanRuntimeInput(list);
  }catch(e){}
  return list;
}
'@

function Get-InjectedJs {
    $patch = Join-Path $script:AppRoot "patch-cli-trim.py"
    if (-not (Test-Path -LiteralPath $patch)) {
        throw "找不到 patch-cli-trim.py：$patch（注入体必须从它提取）"
    }
    $helper = Read-HelperBlock $patch
    $scrub = Read-ScrubBlock $patch
    if (-not $helper) { throw "无法从 patch-cli-trim.py 提取 HELPER（8 个废弃策略块 + user-context 清洗）" }
    if (-not $scrub) { throw "无法从 patch-cli-trim.py 提取 SCRUB（/*__wbTrimScrub*/ 段）" }

    # HELPER is the cleaning library (G3) and SCRUB refers to it. If a future
    # rewrite folds the cleaner into SCRUB itself, don't inject a second copy.
    $needsHelper = ($scrub -notmatch '\bfunction\s+__wbStripLegacyString\b')

    # Layout (everything ends up inside one IIFE, see below):
    #   HELPER            (G3: 8 retired policy blocks + <current_time>/<additional_data> + user-context)
    #   JSON-safe outlet  (only when the extracted SCRUB still cleans the raw
    #                      JSON text: wraps whichever fetch the SCRUB installed)
    #   SCRUB             (G2: the exit-cleanup body, verbatim from patch-cli-trim.py)
    #   bootstrap         (unwrap a v1 lite wrapper; install the JSON-safe outlet)
    # JSON safety is a property of the whole cleaner (v4 keeps it in the HELPER),
    # not of the SCRUB block alone; only wrap when nothing in the body parses JSON.
    $outlet = ""
    if (-not (($helper + $scrub).Contains("JSON.parse"))) { $outlet = $JSON_SAFE_OUTLET_JS }

    $boot = New-Object System.Collections.Generic.List[string]
    $boot.Add('var G=typeof globalThis!=="undefined"?globalThis:global;')
    $boot.Add('try{var f=G.fetch;if(f&&f.__wbLite&&f.__orig)G.fetch=f.__orig;}catch(e){}')
    if ($outlet) { $boot.Add("__wbLiteInstallJsonSafe();") }

    $body = New-Object System.Collections.Generic.List[string]
    if ($needsHelper) { $body.Add($helper) }
    if ($outlet) { $body.Add($outlet) }
    $body.Add($scrub)
    $body.Add($NODE_EGRESS_JS)
    $body.Add(($boot -join "`n"))
    $body.Add(@'
var __wbLiteEgressState={ok:0,failed:0,skippedLength:0};
try{globalThis.__wbLiteEgressState=__wbLiteEgressState;}catch(e){}
try{
  var __wbLiteLoad=function(name,then){
    try{
      if(typeof require==="function"){then(require(name));return;}
    }catch(e){}
    try{
      var p=import(name);
      if(p&&typeof p.then==="function")p.then(then,function(){__wbLiteEgressState.failed++;});
    }catch(e){__wbLiteEgressState.failed++;}
  };
  __wbLiteLoad("node:http",function(m){__wbLiteInstallEgress(m);});
  __wbLiteLoad("node:https",function(m){__wbLiteInstallEgress(m);});
}catch(e){}
'@)
    $js = ($body -join "`n")

    # Everything is renamed to __wbLite* and wrapped in an IIFE:
    #   * no top-level identifier can collide with the chunk or with a helper
    #     patch-cli-trim already injected (duplicate `function` = SyntaxError);
    #   * patch-cli-trim's markers/identifiers disappear, so a later run of it
    #     sees a plain unpatched chunk and injects its own (better) patch
    #     instead of mistaking our closure-local copy for its module-scope one.
    # The marker comments are stripped for the same reason.
    $js = $js -replace '/\*__wbTrimV3\*/', '' -replace '/\*__wbTrimEnd\*/', ''
    $js = $js -replace '/\*__wbTrimScrub\*/', '' -replace '/\*__wbTrimScrubEnd\*/', ''
    # Rename the cleaner helpers only (__wbStripLegacyString is patch-cli-trim's
    # MARK: leaving it would make its next run believe the chunk is half-patched).
    # The __wbScrub* entry points keep their names: they live inside this IIFE,
    # so they cannot collide, and the dump stays greppable as __wbScrubFetch.
    $js = [regex]::Replace($js, '\b__wb(?!Lite|Scrub)([A-Za-z0-9_]+)', '__wbLite$1')

    $lines = New-Object System.Collections.Generic.List[string]
    $lines.Add($BEGIN)
    $lines.Add("// injected by wb-fix-lite.ps1 - body extracted from patch-cli-trim.py")
    $lines.Add("(function(){")
    $lines.Add("try{")
    $lines.Add($js)
    $lines.Add("}catch(e){}")
    $lines.Add("})();")
    $lines.Add($END)
    return ($lines -join "`r`n")
}



# ---------------------------------------------------------------------------
# Discovery (G4): C-K, drive-root <letter>:\*WorkBuddy* (depth 2),
# %LOCALAPPDATA%\WorkBuddy*, Find-Chunks depth 6 with directory pruning,
# and <install>\resources\vendor\python\python.exe as a Python fallback.
# ---------------------------------------------------------------------------

function Test-Asar([string]$root) { Test-Path -LiteralPath (Join-Path $root "resources\app.asar") }

# The original user's folders win over the elevated process's own: when a
# standard user elevates with an administrator's credentials, %USERPROFILE%
# and %LOCALAPPDATA% point at the administrator's profile and the per-user
# WorkBuddy install / .workbuddy dir would be invisible.
function Get-UserProfileDir {
    if ($OrigUserProfile) { return $OrigUserProfile }
    if ($env:USERPROFILE) { return $env:USERPROFILE }
    return $HOME
}
function Get-UserLocalAppData {
    if ($OrigLocalAppData) { return $OrigLocalAppData }
    return $env:LOCALAPPDATA
}
function Get-UserRoamingAppData {
    if ($OrigAppData) { return $OrigAppData }
    return $env:APPDATA
}

function Add-Root($roots, [string]$path) {
    if (-not $path) { return }
    try {
        if (-not (Test-Path -LiteralPath $path -PathType Container)) { return }
        $full = [IO.Path]::GetFullPath($path).TrimEnd('\')
    } catch { return }
    if ($roots -notcontains $full) { $roots.Add($full) }
}

# Walks up to -Depth levels below a drive root looking for directories whose
# name contains "WorkBuddy" (and WorkBuddy*.exe next to them). Returns folders.
function Find-UnderRoot([string]$base, [int]$depth) {
    $hits = New-Object System.Collections.Generic.List[string]
    if (-not (Test-Path -LiteralPath $base)) { return $hits }
    # NOTE: enqueue a TUPLE, never @($path, $lvl). PowerShell unrolls the array
    # when it is written into Queue[object], so the level would always read back
    # as 0 and the depth limit would silently not exist (unbounded disk walk).
    $pending = New-Object System.Collections.Generic.Queue[object]
    $pending.Enqueue([Tuple[string, int]]::new([string]$base, 0))
    while ($pending.Count -gt 0) {
        $cur = $pending.Dequeue()
        $dir = $cur.Item1
        $lvl = $cur.Item2
        try { $children = @(Get-ChildItem -LiteralPath $dir -Directory -ErrorAction Stop) } catch { continue }
        foreach ($c in $children) {
            # $c sits at level $lvl+1 and is checked by THIS loop, so reaching
            # level $depth needs no further descent: a level-$depth directory is
            # still reported, deeper ones are not. (Contrast Find-Chunks, which
            # must ENTER level $maxDepth to read the files inside it.)
            if ($c.Name -match "WorkBuddy") {
                $hits.Add($c.FullName)
                if ($lvl + 1 -lt $depth) { $pending.Enqueue([Tuple[string, int]]::new([string]$c.FullName, $lvl + 1)) }
            } elseif ($lvl + 1 -lt $depth) {
                # one more level only below names that look like an app container
                if ($c.Name -match "^(Program Files|Programs|Apps|Software|Electron|Tools|Games|Portable|Green|应用|软件)$") {
                    $pending.Enqueue([Tuple[string, int]]::new([string]$c.FullName, $lvl + 1))
                }
            }
        }
    }
    return $hits
}

function Find-Installs {
    $roots = New-Object System.Collections.Generic.List[string]
    if ($env:WORKBUDDY_INSTALL) { Add-Root $roots $env:WORKBUDDY_INSTALL }

    $keys = @(
        "HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*",
        "HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*"
    )
    foreach ($k in $keys) {
        Get-ItemProperty $k -ErrorAction SilentlyContinue | Where-Object { $_.DisplayName -match "WorkBuddy" } | ForEach-Object {
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
                if (-not $p) { continue }
                if (Test-Path -LiteralPath $p -PathType Container) { Add-Root $roots $p }
                elseif (Test-Path -LiteralPath $p) { Add-Root $roots (Split-Path -Parent $p) }
            }
        }
    }

    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match "WorkBuddy" } |
        ForEach-Object { if ($_.ExecutablePath) { Add-Root $roots (Split-Path -Parent $_.ExecutablePath) } }

    try {
        $sh = New-Object -ComObject WScript.Shell
        foreach ($d in @(
            (Join-Path $env:ProgramData "Microsoft\Windows\Start Menu\Programs"),
            (Join-Path (Get-UserRoamingAppData) "Microsoft\Windows\Start Menu\Programs"),
            [Environment]::GetFolderPath("Desktop"),
            [Environment]::GetFolderPath("CommonDesktopDirectory")
        )) {
            if (-not (Test-Path -LiteralPath $d)) { continue }
            Get-ChildItem -LiteralPath $d -Filter *.lnk -Recurse -Depth 2 -ErrorAction SilentlyContinue | ForEach-Object {
                $t = $sh.CreateShortcut($_.FullName).TargetPath
                if ($t -match "WorkBuddy.*\.exe$") { Add-Root $roots (Split-Path -Parent $t) }
            }
        }
    } catch { }

    # Current user's per-user installs: %LOCALAPPDATA%\WorkBuddy* (depth 2) and
    # %LOCALAPPDATA%\Programs. Under UAC-over-the-shoulder these are the admin's
    # folders, which is why the cmd passes the original user's paths through.
    $localAppData = Get-UserLocalAppData
    if ($localAppData) {
        foreach ($base in @((Join-Path $localAppData "Programs"), $localAppData)) {
            if (-not (Test-Path -LiteralPath $base)) { continue }
            if ($base -eq $localAppData) {
                Get-ChildItem -LiteralPath $base -Directory -Filter "WorkBuddy*" -ErrorAction SilentlyContinue |
                    ForEach-Object { Add-Root $roots $_.FullName }
            } else {
                Get-ChildItem -LiteralPath $base -Directory -ErrorAction SilentlyContinue |
                    Where-Object { $_.Name -match "WorkBuddy" } |
                    ForEach-Object { Add-Root $roots $_.FullName }
            }
        }
    }
    foreach ($pf in @($env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        if (-not $pf) { continue }
        Get-ChildItem -LiteralPath $pf -Directory -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -match "WorkBuddy" } |
            ForEach-Object { Add-Root $roots $_.FullName }
    }

    # C-K, drive root <letter>:\*WorkBuddy*, depth 2.
    foreach ($letter in @("C", "D", "E", "F", "G", "H", "I", "J", "K")) {
        $drive = "${letter}:\"
        if (-not (Test-Path -LiteralPath $drive)) { continue }
        try {
            Get-ChildItem -LiteralPath $drive -Directory -Filter "*WorkBuddy*" -ErrorAction SilentlyContinue |
                ForEach-Object { Add-Root $roots $_.FullName }
        } catch { }
        foreach ($hit in (Find-UnderRoot $drive 2)) { Add-Root $roots $hit }
        foreach ($sub in @("Program Files", "Program Files (x86)", "Apps", "Programs", "Software", "Tools", "Games")) {
            foreach ($hit in (Find-UnderRoot (Join-Path $drive $sub) 2)) { Add-Root $roots $hit }
        }
    }

    $out = New-Object System.Collections.Generic.List[string]
    foreach ($r in $roots) {
        if (-not (Test-Asar $r)) { continue }
        if ($out -notcontains $r) { $out.Add($r) }
    }
    return $out
}

# Depth 6 below resources\app.asar.unpacked; directory names node_modules /
# renderer / locales / assets are pruned (not recursed into), and only .mjs/.js
# files below 24 MB are content-scanned. Manual BFS because Get-ChildItem
# cannot prune while recursing.
function Find-Chunks([string]$root) {
    $unpacked = Join-Path $root "resources\app.asar.unpacked"
    if (-not (Test-Path -LiteralPath $unpacked)) { return @() }
    $skipDirs = @("node_modules", "renderer", "locales", "assets")
    # Exactly the reach of `Get-ChildItem -Depth 6` (verified against it):
    # directories up to 6 levels deep are listed, so the deepest file scanned is
    # unpacked\<1>\<2>\<3>\<4>\<5>\<6>\file.mjs. $lvl is the depth of $dir, so a
    # child directory sits at $lvl+1 and is only queued while $lvl < 5.
    $maxDepth = 6
    $hits = New-Object System.Collections.Generic.List[string]
    # Tuple, not @($path, $lvl): Queue[object] unrolls an array argument, which
    # made $lvl read back as 0 and the depth cap a no-op.
    $pending = New-Object System.Collections.Generic.Queue[object]
    $pending.Enqueue([Tuple[string, int]]::new([string]$unpacked, 0))
    while ($pending.Count -gt 0) {
        $cur = $pending.Dequeue()
        $dir = $cur.Item1
        $lvl = $cur.Item2
        try { $children = @(Get-ChildItem -LiteralPath $dir -ErrorAction Stop) } catch { continue }
        foreach ($c in $children) {
            if ($c.PSIsContainer) {
                if ($skipDirs -contains $c.Name) { continue }
                if ($lvl + 1 -le $maxDepth) { $pending.Enqueue([Tuple[string, int]]::new([string]$c.FullName, $lvl + 1)) }
                continue
            }
            if ($c.Extension -ne ".mjs" -and $c.Extension -ne ".js") { continue }
            if ($c.Length -ge 24MB) { continue }
            try {
                $s = [IO.File]::ReadAllText($c.FullName)
            } catch {
                # Locked/unreadable candidate: remember it, otherwise the run
                # would claim "no chunk" when the chunk is merely in use.
                $script:AppUnreadable += $c.FullName
                continue
            }
            if ($s.Contains("modelInput.instructions") -and $s.Contains("modelInput.input")) {
                if (-not $hits.Contains($c.FullName)) { $hits.Add($c.FullName) }
            }
        }
    }
    return $hits
}

function Get-BundledPython([string]$root) {
    $p = Join-Path $root "resources\vendor\python\python.exe"
    if (Test-Path -LiteralPath $p) { return $p }
    return ""
}

function Find-PythonExe($installs) {
    if ($env:WORKBUDDY_PYTHON -and (Test-Path -LiteralPath $env:WORKBUDDY_PYTHON)) { return $env:WORKBUDDY_PYTHON }
    foreach ($root in $installs) {
        $p = Get-BundledPython $root
        if ($p) { return $p }
    }
    $c = Get-Command python.exe -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    $c = Get-Command py.exe -ErrorAction SilentlyContinue
    if ($c) { return $c.Source }
    # Last resort: the original user's WorkBuddy-managed runtimes (visible even
    # when the elevated process cannot resolve %USERPROFILE%\.workbuddy).
    $pd = Get-UserProfileDir
    if ($pd) {
        foreach ($name in @(".workbuddy", ".workbuddy-ai")) {
            $base = Join-Path (Join-Path $pd $name) "binaries\python\versions"
            if (-not (Test-Path -LiteralPath $base)) { continue }
            foreach ($v in @(Get-ChildItem -LiteralPath $base -Directory -ErrorAction SilentlyContinue | Sort-Object Name -Descending)) {
                foreach ($rel in @("python.exe", "Scripts\python.exe")) {
                    $p = Join-Path $v.FullName $rel
                    if (Test-Path -LiteralPath $p) { return $p }
                }
            }
        }
    }
    return ""
}

# ---------------------------------------------------------------------------
# Backup (G5): %TEMP%\wb-fix-lite-backup\<stamp>\ - never in the install dir.
# ---------------------------------------------------------------------------

function New-Backup([string]$path, [byte[]]$bytes) {
    $dir = Join-Path (Join-Path ([IO.Path]::GetTempPath()) "wb-fix-lite-backup") $script:Stamp
    New-Item -ItemType Directory -Path $dir -Force | Out-Null
    $safe = ($path -replace '^[A-Za-z]:', '')
    $safe = ($safe -replace '[\\/:*?"<>|]', '_').Trim('_')
    if ($safe.Length -gt 120) { $safe = $safe.Substring($safe.Length - 120) }
    $bak = Join-Path $dir ($safe + ".bak")
    [IO.File]::WriteAllBytes($bak, $bytes)
    Add-Content -LiteralPath (Join-Path $dir "manifest.tsv") -Value ("{0}`t{1}" -f $path, $bak) -Encoding UTF8
    return $bak
}

# manifest.tsv is written so a human can find which backup belongs to which
# chunk; -Restore works from the injected markers themselves, not from it.

# ---------------------------------------------------------------------------
# Patch / restore
# ---------------------------------------------------------------------------

# Marker search inside the first $Limit bytes. Everything we inject is
# prepended, so the marker is always in the head, and decoding that slice as
# ASCII keeps a 1:1 byte->char mapping (markers are pure ASCII; multi-byte
# UTF-8 -> '?' still occupies ONE char per byte, so offsets stay aligned).
function Find-Marker([byte[]]$Bytes, [int]$Limit, [string]$Marker, [int]$Start) {
    $n = [Math]::Min($Limit, $Bytes.Length)
    if ($n -le 0) { return -1 }
    $head = [Text.Encoding]::ASCII.GetString($Bytes, 0, $n)
    return $head.IndexOf($Marker, $Start, [StringComparison]::Ordinal)
}

function Test-BytesEqual([byte[]]$A, [int]$OffA, [byte[]]$B, [int]$OffB, [int]$Len) {
    if ($Len -eq 0) { return $true }
    if ($A.Length -lt ($OffA + $Len) -or $B.Length -lt ($OffB + $Len)) { return $false }
    for ($i = 0; $i -lt $Len; $i++) { if ($A[$OffA + $i] -ne $B[$OffB + $i]) { return $false } }
    return $true
}

# 全程按字节处理：chunk 是多字节 UTF-8，用文本读写会有编码/换行风险。
function Invoke-One([string]$path) {
    $bytes = [IO.File]::ReadAllBytes($path)
    $origLen = $bytes.Length
    $prefix = [Text.Encoding]::UTF8.GetBytes($SNIPPET + "`r`n")
    # Head limit: our block is at the very top; 256 KB is plenty even for a
    # future larger SCRUB and keeps the scan fast on a 12 MB chunk.
    $HEAD_LIMIT = 262144
    $hasLite = (Find-Marker $bytes $HEAD_LIMIT $BEGIN 0) -ge 0
    # v1 of this script used /*__wbTrimLite*/ and left it behind on real
    # installs; recognize it so an upgrade replaces the old body instead of
    # stacking a second one.
    $hasOldLite = (Find-Marker $bytes $HEAD_LIMIT $OLD_BEGIN 0) -ge 0
    # "already handled by patch-cli-trim": any of its markers (v3 helper, v4
    # scrub, or the __wbStripLegacyString entry point) is present. Our own
    # output uses __wbLite* names, so it can never match this by accident.
    $hasTrim = $false
    foreach ($mk in $TRIM_MARKERS) {
        if ((Find-Marker $bytes $HEAD_LIMIT $mk 0) -ge 0) { $hasTrim = $true; break }
    }
    Write-Host ("  chunk : {0}" -f $path)
    if ($hasTrim -and -not $hasLite -and -not $hasOldLite -and -not $Restore) {
        Write-Host "         检测到 patch-cli-trim 已在此 chunk 注入（锚点补丁已生效）" -ForegroundColor DarkGray
    }
    if ($hasOldLite) {
        if ($Restore) {
            Write-Host "         检测到 v1 轻量补丁（/*__wbTrimLite*/），将移除" -ForegroundColor Yellow
        } else {
            Write-Host "         检测到 v1 轻量补丁（/*__wbTrimLite*/），将先移除再写入新版本" -ForegroundColor Yellow
        }
    }

    if ($Restore) {
        if (-not $hasLite -and -not $hasOldLite) {
            Write-Host "         没有轻量补丁，无需还原" -ForegroundColor DarkGray
            return [pscustomobject]@{ Status = "skip"; Reason = "no lite patch" }
        }
        # The body is always prepended (offset 0), so the first BEGIN .. first
        # following END is the whole block, plus one trailing line break.
        $begin = if ($hasLite) { $BEGIN } else { $OLD_BEGIN }
        $end = if ($hasLite) { $END } else { $OLD_END }
        $cut = -1
        $i = Find-Marker $bytes $HEAD_LIMIT $begin 0
        if ($i -ge 0) {
            $e = Find-Marker $bytes $HEAD_LIMIT $end ($i + $begin.Length)
            if ($e -gt $i) { $cut = $e + $end.Length }
        }
        if ($cut -lt 0) {
            Write-Host "         标记不完整，放弃" -ForegroundColor Red
            return [pscustomobject]@{ Status = "fail"; Reason = "incomplete markers" }
        }
        if ($cut + 1 -lt $origLen -and $bytes[$cut] -eq 13 -and $bytes[$cut + 1] -eq 10) { $cut += 2 }
        elseif ($cut -lt $origLen -and $bytes[$cut] -eq 10) { $cut += 1 }
        $back = New-Object byte[] ($origLen - $cut)
        [Array]::Copy($bytes, $cut, $back, 0, $back.Length)
        if ($DryRun) {
            Write-Host ("         [dry-run] 将移除 {0:N0} 字节的轻量补丁（{1:N0} -> {2:N0}）" -f $cut, $origLen, $back.Length)
            return [pscustomobject]@{ Status = "skip"; Reason = "dry-run" }
        }
        try {
            $null = New-Backup $path $bytes
            [IO.File]::WriteAllBytes($path, $back)
        } catch {
            Write-Host ("         写入失败：{0}" -f $_.Exception.Message) -ForegroundColor Red
            Write-Host "         需要管理员权限：请用 scripts\wb-fix-lite.cmd（会自动提权）。" -ForegroundColor Yellow
            return [pscustomobject]@{ Status = "fail"; Reason = "write error" }
        }
        $check = [IO.File]::ReadAllBytes($path)
        $ok = ($check.Length -eq $back.Length) -and (Test-BytesEqual $check 0 $back 0 $back.Length)
        if (-not $ok) {
            Write-Host "         还原后校验失败（文件长度/内容不符）" -ForegroundColor Red
            return [pscustomobject]@{ Status = "fail"; Reason = "verify error" }
        }
        Write-Host ("         已移除轻量补丁：{0:N0} -> {1:N0} 字节" -f $origLen, $back.Length) -ForegroundColor Green
        return [pscustomobject]@{ Status = "ok"; Reason = "restored" }
    }

    if ($hasLite) {
        Write-Host "         已有轻量补丁（无需重复）" -ForegroundColor Green
        return [pscustomobject]@{ Status = "skip"; Reason = "already patched" }
    }
    if ($hasOldLite) {
        # Upgrade path: strip the v1 block and fall through to a fresh inject.
        $i = Find-Marker $bytes $HEAD_LIMIT $OLD_BEGIN 0
        $e = if ($i -ge 0) { Find-Marker $bytes $HEAD_LIMIT $OLD_END ($i + $OLD_BEGIN.Length) } else { -1 }
        if ($e -le $i) {
            Write-Host "         v1 标记不完整，放弃（请先 -Restore 或恢复原始 chunk）" -ForegroundColor Red
            return [pscustomobject]@{ Status = "fail"; Reason = "incomplete v1 markers" }
        }
        $cut = $e + $OLD_END.Length
        if ($cut + 1 -lt $origLen -and $bytes[$cut] -eq 13 -and $bytes[$cut + 1] -eq 10) { $cut += 2 }
        elseif ($cut -lt $origLen -and $bytes[$cut] -eq 10) { $cut += 1 }
        $bytes = $bytes[$cut..($origLen - 1)]
        if (-not $bytes) { $bytes = New-Object byte[] 0 }
        $origLen = $bytes.Length
        Write-Host ("         已剥离 v1 补丁，剩余 {0:N0} 字节；继续写入新版本" -f $origLen) -ForegroundColor DarkGray
    }
    if ($hasTrim) {
        # The chunk already carries the (better, modelInput-level) trim patch.
        # Injecting the lite body on top would only add a second copy.
        if ($hasOldLite) {
            # We stripped v1 in memory but will not write: say so instead of
            # leaving the user believing the stale v1 body was upgraded.
            Write-Host "         该 chunk 已由 patch-cli-trim 处理，不再注入轻量补丁；" -ForegroundColor DarkGray
            Write-Host "         v1 旧补丁仍在磁盘上（未改动）。如要去掉它，先运行 -Restore 再按需重跑。" -ForegroundColor Yellow
            return [pscustomobject]@{ Status = "skip"; Reason = "trim patch present (v1 left in place)" }
        }
        Write-Host "         该 chunk 已由 patch-cli-trim 处理，跳过轻量补丁（避免重复注入）" -ForegroundColor DarkGray
        return [pscustomobject]@{ Status = "skip"; Reason = "trim patch present" }
    }

    if ($DryRun) {
        Write-Host ("         [dry-run] 将写入 {0:N0} -> {1:N0} 字节" -f $origLen, ($origLen + $prefix.Length))
        return [pscustomobject]@{ Status = "skip"; Reason = "dry-run" }
    }

    $out = New-Object byte[] ($origLen + $prefix.Length)
    [Array]::Copy($prefix, 0, $out, 0, $prefix.Length)
    [Array]::Copy($bytes, 0, $out, $prefix.Length, $origLen)

    $bak = ""
    try {
        $bak = New-Backup $path $bytes
        [IO.File]::WriteAllBytes($path, $out)
    } catch {
        Write-Host ("         写入失败：{0}" -f $_.Exception.Message) -ForegroundColor Red
        Write-Host "         这个目录需要管理员权限。请运行 scripts\wb-fix-lite.cmd（会自动提权），" -ForegroundColor Yellow
        Write-Host "         或右键 PowerShell「以管理员身份运行」后重试。" -ForegroundColor Yellow
        return [pscustomobject]@{ Status = "fail"; Reason = "write error" }
    }

    # 校验：前缀完全一致，且原文一字不差地跟在后面
    $check = [IO.File]::ReadAllBytes($path)
    $ok = ($check.Length -eq $out.Length) -and (Test-BytesEqual $check 0 $prefix 0 $prefix.Length) -and (Test-BytesEqual $check $prefix.Length $bytes 0 $origLen)
    if (-not $ok) {
        try { Copy-Item -LiteralPath $bak -Destination $path -Force } catch { }
        Write-Host "         写入后校验失败，已回滚" -ForegroundColor Red
        return [pscustomobject]@{ Status = "fail"; Reason = "verify error" }
    }

    $node = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($node) {
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try { $r = & $node.Source --check $path 2>&1 } finally { $ErrorActionPreference = $prevEap }
        if ($LASTEXITCODE -ne 0) {
            try { Copy-Item -LiteralPath $bak -Destination $path -Force } catch { }
            Write-Host ("         node --check 失败，已回滚：{0}" -f ($r -join " ")) -ForegroundColor Red
            return [pscustomobject]@{ Status = "fail"; Reason = "node --check" }
        }
        Write-Host "         node --check OK" -ForegroundColor Green
    } else {
        Write-Host "         node 不在 PATH：跳过语法检查（已做逐字节首尾校验）" -ForegroundColor Yellow
    }

    Write-Host ("         已写入：{0:N0} -> {1:N0} 字节" -f $origLen, $out.Length) -ForegroundColor Green
    Write-Host ("         备份：{0}" -f $bak) -ForegroundColor DarkGray
    return [pscustomobject]@{ Status = "ok"; Reason = "written" }
}

# ---------------------------------------------------------------------------
# Prewarm refresh (G6)
# ---------------------------------------------------------------------------

function Invoke-PrewarmRefresh {
    $scriptPath = Join-Path $script:AppRoot "refresh-cli-prewarm.py"
    if (-not (Test-Path -LiteralPath $scriptPath)) {
        Write-Host "prewarm 未刷新（未找到 scripts\refresh-cli-prewarm.py），重启 WorkBuddy 后生效。" -ForegroundColor Yellow
        return
    }
    $py = Find-PythonExe $script:AppInstalls
    if (-not $py) {
        Write-Host "prewarm 未刷新（找不到 Python），重启 WorkBuddy 后生效。" -ForegroundColor Yellow
        return
    }
    $dirs = New-Object System.Collections.Generic.List[string]
    $profiles = @((Get-UserProfileDir), $HOME, $env:USERPROFILE) | Select-Object -Unique
    foreach ($pd in $profiles) {
        if (-not $pd) { continue }
        foreach ($name in @(".workbuddy", ".workbuddy-ai")) {
            $d = Join-Path $pd $name
            if ((Test-Path -LiteralPath $d -PathType Container) -and ($dirs -notcontains $d)) { $dirs.Add($d) }
        }
    }
    if ($env:WORKBUDDY_CONFIG_DIR -and (Test-Path -LiteralPath $env:WORKBUDDY_CONFIG_DIR)) {
        if ($dirs -notcontains $env:WORKBUDDY_CONFIG_DIR) { $dirs.Add($env:WORKBUDDY_CONFIG_DIR) }
    }
    if ($dirs.Count -eq 0) {
        Write-Host "prewarm 未刷新（未找到 .workbuddy / .workbuddy-ai），重启 WorkBuddy 后生效。" -ForegroundColor Yellow
        return
    }
    Write-Host ("prewarm: {0}" -f $py) -ForegroundColor DarkGray
    foreach ($d in $dirs) {
        Write-Host ("  config: {0}" -f $d)
        $prevEap = $ErrorActionPreference
        $ErrorActionPreference = "Continue"
        try {
            $out = @(& $py -I $scriptPath --apply --config-dir $d 2>&1 | ForEach-Object { [string]$_ })
            $code = $LASTEXITCODE
        } catch {
            $out = @($_.Exception.Message)
            $code = 1
        } finally {
            $ErrorActionPreference = $prevEap
        }
        foreach ($line in $out) { Write-Host ("    " + $line) }
        if ($code -ne 0) {
            Write-Host "    prewarm 刷新返回非 0（不影响补丁本身）；重启 WorkBuddy 后生效。" -ForegroundColor Yellow
        }
    }
}

# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

if ($DumpInjectedJs) {
    try {
        $SNIPPET = Get-InjectedJs
    } catch {
        Write-Host ("[FAIL] " + $_.Exception.Message) -ForegroundColor Red
        exit 1
    }
    Write-Output $SNIPPET
    exit 0
}

Write-Host "== wb-fix-lite (no Python / no Node / no anchors) =="
Write-Host ("time   : {0}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"))
if ($Restore) { Write-Host "mode   : restore" } elseif ($DryRun) { Write-Host "mode   : dry-run" } else { Write-Host "mode   : apply" }

try {
    $SNIPPET = Get-InjectedJs
    Write-Host ("js     : extracted from patch-cli-trim.py ({0:N0} bytes)" -f $SNIPPET.Length) -ForegroundColor DarkGray
} catch {
    Write-Host ("[FAIL] " + $_.Exception.Message) -ForegroundColor Red
    Write-Host "       注入体必须从 scripts\patch-cli-trim.py 提取；请确认该文件存在。" -ForegroundColor Yellow
    exit 1
}

$installs = @()
$badInstalls = New-Object System.Collections.Generic.List[string]
if ($Install.Count -gt 0) {
    foreach ($i in $Install) {
        if (-not $i) { continue }
        if (-not (Test-Path -LiteralPath $i -PathType Container)) { $badInstalls.Add($i); continue }
        if (-not (Test-Asar $i)) { $badInstalls.Add($i); continue }
        $installs += $i
    }
} else {
    $installs = @(Find-Installs)
}
$script:AppInstalls = $installs

if ($badInstalls.Count -gt 0) {
    foreach ($b in $badInstalls) {
        Write-Host ("install: INVALID  {0}（目录不存在或缺少 resources\app.asar）" -f $b) -ForegroundColor Red
    }
    Write-Host "  pass -Install '<install root>' (folder that holds resources\app.asar), or set WORKBUDDY_INSTALL." -ForegroundColor Yellow
}
if ($installs.Count -eq 0) {
    Write-Host "install: NOT FOUND" -ForegroundColor Red
    if ($badInstalls.Count -eq 0) {
        Write-Host "  pass -Install '<install root>' or set WORKBUDDY_INSTALL." -ForegroundColor Yellow
    }
    exit 3
}

$failed = $false
$skipped = $false
$patched = 0
$restored = 0
$wroteAnything = $false

foreach ($root in $installs) {
    Write-Host ""
    Write-Host ("install: {0}" -f $root)
    $vendorPy = Get-BundledPython $root
    if ($vendorPy) {
        Write-Host ("  python : {0}（vendor，用作 prewarm 兜底）" -f $vendorPy) -ForegroundColor DarkGray
    } else {
        Write-Host "  python : 无 resources\vendor\python\python.exe（本工具不需要 Python）" -ForegroundColor DarkGray
    }
    $script:AppUnreadable = New-Object System.Collections.Generic.List[string]
    $chunks = @(Find-Chunks $root)
    if ($chunks.Count -eq 0) {
        if ($script:AppUnreadable.Count -gt 0) {
            Write-Host "  找到疑似 chunk，但文件被占用/不可读（不是“没有 chunk”）：" -ForegroundColor Red
            foreach ($u in $script:AppUnreadable) { Write-Host ("    " + $u) -ForegroundColor Red }
            Write-Host "  请完全退出 WorkBuddy 后重试。" -ForegroundColor Yellow
        } else {
            Write-Host "  没有承载 modelInput 的 chunk" -ForegroundColor Red
        }
        $failed = $true
        continue
    }
    $installOk = $false
    foreach ($c in $chunks) {
        $r = Invoke-One $c
        if (-not $r) { continue }
        # dry-run: having reached a chunk and printed what would happen is the
        # success condition (nothing is ever written in this mode).
        if ($DryRun) { $installOk = $true }
        switch ($r.Status) {
            "ok" {
                $installOk = $true
                $wroteAnything = $true
                if ($Restore) { $restored++ } else { $patched++ }
            }
            "fail" { $failed = $true }
            default { $skipped = $true }
        }
    }
    if (-not $installOk -and -not $DryRun) { Write-Host "  该安装没有可写入的 chunk（见上）" -ForegroundColor Yellow }
}

if ($failed) {
    Write-Host ""
    Write-Host "done WITH FAILURES. 未全部写入；详情见上。" -ForegroundColor Red
    exit 1
}

if ($wroteAnything) {
    if ($RefreshPrewarm -and -not $DryRun) { Invoke-PrewarmRefresh }
    Write-Host ""
    Write-Host ("done. 写入 {0} 个 chunk，还原 {1} 个；新开一个聊天（或重启应用）才会加载新代码。" -f $patched, $restored) -ForegroundColor Cyan
    exit 0
}

if ($skipped) {
    Write-Host ""
    Write-Host "没有写入任何东西（dry-run / 已是目标状态）。新开一个聊天（或重启应用）才会加载新代码。" -ForegroundColor Cyan
    exit 2
}

Write-Host "done. 没有写入任何东西。" -ForegroundColor Yellow
exit 2
