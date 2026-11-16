@echo off
setlocal
echo [WorkBuddy] Pulling current prompts into a new snapshot directory...
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0pull.ps1" %*
echo.
pause
