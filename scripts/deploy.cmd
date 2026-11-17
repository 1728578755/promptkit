@echo off
setlocal
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" %*
echo.
rem Pause only for an interactive double-click (no arguments). A scripted call such as
rem `deploy.cmd -Target workbuddy` would otherwise block forever waiting for a keypress.
if "%~1"=="" pause
