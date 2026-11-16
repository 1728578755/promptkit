@echo off
setlocal
echo [WorkBuddy] Auto-detecting install path and restoring the original baseline...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restore.ps1" -All -Original %*
echo.
pause
