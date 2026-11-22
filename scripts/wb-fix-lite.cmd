@echo off
setlocal EnableExtensions
chcp 65001 >nul
title WorkBuddy last-mile fix
rem No Python, no Node, no fixed install path, no fixed anchor.
rem Batch rules that bit us before:
rem   * never put "(" or ")" inside an echo that lives in an if-block
rem   * never rely on "net session" for the admin check
rem   * keep the elevated window open (-NoExit + pause)
rem   * dry-run must never trigger UAC
rem
rem F-08: the old one-liner built a PowerShell array and handed it to
rem Start-Process -Verb RunAs. PS 5.1 joins array elements with spaces and does
rem NOT quote elements containing spaces, so "C:\Users\John Doe\Desktop\x"
rem arrived as two arguments. Now the .ps1 is started with -File and the raw
rem "%*" (already quoted by the caller); its -SelfElevate branch builds ONE
rem pre-quoted argument string. No -Command string parsing anywhere.
rem
rem F-08 (cross-account UAC): capture the *original* user's folders before
rem elevation. Under "over-the-shoulder" UAC the elevated process sees the
rem administrator's %USERPROFILE%/%LOCALAPPDATA%, not the caller's.

set "HERE=%~dp0"
set "WBFIX_ARGS=%*"

rem Read-only / no-write modes must never trigger UAC. -? used to fall through
rem to the elevated apply path, which turned "show me help" into a real write.
if /i "%~1"=="-DryRun" goto :run
if /i "%~1"=="/DryRun" goto :run
if /i "%~1"=="-DumpInjectedJs" goto :run
if /i "%~1"=="-Restore" goto :run
if /i "%~1"=="-?" goto :help
if /i "%~1"=="/?" goto :help
if /i "%~1"=="-h" goto :help
if /i "%~1"=="--help" goto :help

set "ADMIN="
for /f "tokens=*" %%A in ('whoami /groups 2^>nul ^| findstr /C:"S-1-16-12288"') do set "ADMIN=1"
if defined ADMIN goto :run

echo This needs administrator rights to write into Program Files.
echo A new window will open and STAY OPEN - read the result there.
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%wb-fix-lite.ps1" -SelfElevate -OrigUserProfile "%USERPROFILE%" -OrigLocalAppData "%LOCALAPPDATA%" -OrigAppData "%APPDATA%"
rem Propagate a launch failure (UAC cancelled) instead of always reporting 0.
exit /b %errorlevel%

:help
echo wb-fix-lite - dependency-free last-mile fix (no Python, no Node, no anchors^)
echo.
echo   wb-fix-lite.cmd                     apply (elevates when needed^)
echo   wb-fix-lite.cmd -DryRun             show what would change, write nothing, no UAC
echo   wb-fix-lite.cmd -Restore            remove the lite patch
echo   wb-fix-lite.cmd -Install "PATH"     target one install root (folder with resources\app.asar^)
echo   wb-fix-lite.cmd -DumpInjectedJs     print the JS that would be injected
echo.
echo Exit codes: 0 written/restored and verified; 1 failed; 2 nothing to do; 3 no install found.
echo Backups go to %%TEMP%%\wb-fix-lite-backup\^<stamp^>\ - never into the install folder.
exit /b 0

:run
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%wb-fix-lite.ps1" -OrigUserProfile "%USERPROFILE%" -OrigLocalAppData "%LOCALAPPDATA%" -OrigAppData "%APPDATA%" %*
set "RC=%errorlevel%"
echo.
echo ==========================================================
echo  exit=%RC%
echo  A NEW chat or a restart is required to load the change.
echo ==========================================================
if defined WB_NO_PAUSE exit /b %RC%
pause
exit /b %RC%
