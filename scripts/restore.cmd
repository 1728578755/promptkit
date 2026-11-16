@echo off
setlocal
echo [WorkBuddy] Auto-detecting install path and restoring the latest backup set...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0restore.ps1" %*
echo.
pause
