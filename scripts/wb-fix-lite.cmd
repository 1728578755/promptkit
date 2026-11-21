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

set "HERE=%~dp0"
set "WBFIX_ARGS=%*"

if /i "%~1"=="-DryRun" goto :run
if /i "%~1"=="/DryRun" goto :run

set "ADMIN="
for /f "tokens=*" %%A in ('whoami /groups 2^>nul ^| findstr /C:"S-1-16-12288"') do set "ADMIN=1"
if defined ADMIN goto :run

echo This needs administrator rights to write into Program Files.
echo A new window will open and STAY OPEN - read the result there.
powershell -NoProfile -ExecutionPolicy Bypass -Command "$a=@('-NoExit','-NoProfile','-ExecutionPolicy','Bypass','-File','%HERE%wb-fix-lite.ps1'); if ($env:WBFIX_ARGS) { $a += ($env:WBFIX_ARGS -split ' ') }; Start-Process powershell -Verb RunAs -ArgumentList $a"
exit /b

:run
powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%wb-fix-lite.ps1" %*
set "RC=%errorlevel%"
echo.
echo ==========================================================
echo  exit=%RC%
echo  A NEW chat or a restart is required to load the change.
echo ==========================================================
if defined WB_NO_PAUSE exit /b %RC%
pause
exit /b %RC%
