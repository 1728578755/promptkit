@echo off
setlocal
set "RC=0"
rem One-click restore: put every detected installation back to its latest backup.
rem This is a thin wrapper over deploy.ps1 -Restore, so both entries share one implementation.
echo [WorkBuddy] Restoring the latest backup of every detected installation...
echo   list backups  : restore.cmd -List
echo   original base : restore.cmd -Original
echo   one target    : restore.cmd -Target workbuddy-ai
echo   full restore  : restore.cmd -Elevate   (also writes the install mirror, one UAC)
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" -Restore %*
set "RC=%ERRORLEVEL%"
echo.
rem Pause only for an interactive double-click (no arguments).
if "%~1"=="" pause
exit /b %RC%
