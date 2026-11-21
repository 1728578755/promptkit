@echo off
setlocal
set "RC=0"
if "%~1"=="" goto :oneclick
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" %*
set "RC=%ERRORLEVEL%"
goto :done

:oneclick
echo [WorkBuddy] One-click FULL deploy to every detected installation.
echo   writes: prompt cache + install mirror + runtime-templates + product.json + app.asar
echo   one UAC prompt; close WorkBuddy first so app.asar and the exe can be written.
echo   other actions: deploy.cmd -Restore ^| -Verify ^| -ListTargets ^| -DryRun
echo.
powershell.exe -NoProfile -ExecutionPolicy Bypass -File "%~dp0deploy.ps1" -Elevate
set "RC=%ERRORLEVEL%"

:done
echo.
rem Pause only for an interactive double-click (no arguments). A scripted call such as
rem `deploy.cmd -Target workbuddy` would otherwise block forever waiting for a keypress.
if "%~1"=="" pause
rem Never swallow the result: a failed deploy must not look like a successful one.
exit /b %RC%
