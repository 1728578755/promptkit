@echo off
setlocal EnableExtensions
chcp 65001 >nul
title WorkBuddy prompt doctor
rem Read-only. No admin, no writes to the install, safe to run any time.
rem Window stays open at the end (set WB_NO_PAUSE=1 when calling from scripts).

set "HERE=%~dp0"
set "PY="

if defined WORKBUDDY_PYTHON if exist "%WORKBUDDY_PYTHON%" set "PY=%WORKBUDDY_PYTHON%"

if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)
if not defined PY (
    where py >nul 2>nul
    if not errorlevel 1 set "PY=py"
)

rem WorkBuddy ships its own Python. Its exact location is not assumed:
rem read the "current" pointer, then look at every version entry.
set "PYROOT=%USERPROFILE%\.workbuddy\binaries\python\versions"
if not defined PY if exist "%PYROOT%" (
    set "VER="
    if exist "%PYROOT%\current" set /p VER=<"%PYROOT%\current"
    if defined VER if exist "%PYROOT%\%VER%\python.exe" set "PY=%PYROOT%\%VER%\python.exe"
)
if not defined PY if exist "%PYROOT%" (
    for %%D in ("%PYROOT%\*") do (
        if not defined PY if exist "%%~fD\python.exe" set "PY=%%~fD\python.exe"
    )
)

if defined PY goto :runpy
goto :nopython

:runpy
echo Running doctor with: %PY%
echo.
"%PY%" -X utf8 "%HERE%doctor.py" --write-report %*
set "RC=%errorlevel%"
echo.
echo ==========================================================
echo  report : %HERE%..\reports\doctor-latest.txt
echo  exit   : %RC%   (0 = EFFECTIVE / UNPROVEN, 1 = NOT EFFECTIVE)
echo ==========================================================
goto :done

:nopython
echo ==========================================================
echo  [WARN] No Python found - using the PowerShell fallback.
echo.
echo  Python is optional: doctor.py gives the deeper report, the
echo  fallback still names every install and every missing patch.
echo ----------------------------------------------------------
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%HERE%doctor-basic.ps1" %*
set "RC=%errorlevel%"
goto :done

:done
if defined WB_NO_PAUSE exit /b %RC%
echo.
pause
exit /b %RC%
