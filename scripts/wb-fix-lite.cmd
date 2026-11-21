@echo off
setlocal EnableExtensions
chcp 65001 >nul
title WorkBuddy last-mile fix (no Python, no Node)
rem Only needs Windows PowerShell. No Python, no Node, no fixed install path,
rem no fixed anchor in the runtime chunk: it prepends a fetch scrub instead.

set "HERE=%~dp0"

net session >nul 2>nul
if errorlevel 1 (
    echo Requesting administrator (needed to write into Program Files)...
    powershell -NoProfile -ExecutionPolicy Bypass -Command "Start-Process -FilePath '%~f0' -ArgumentList '%*' -Verb RunAs"
    exit /b
)

powershell -NoProfile -ExecutionPolicy Bypass -File "%HERE%wb-fix-lite.ps1" %*
set "RC=%errorlevel%"

echo.
echo ==========================================================
echo  done. exit=%RC%
echo  A NEW chat (or a restart) is required to load the change.
echo ==========================================================
pause
exit /b %RC%
