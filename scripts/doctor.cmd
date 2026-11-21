@echo off
setlocal EnableExtensions
chcp 65001 >nul
title WorkBuddy prompt doctor
rem Read-only diagnostic. No admin, no writes to the install, safe to run any time.
rem The window stays open at the end (set WB_NO_PAUSE=1 when calling from scripts).

set "HERE=%~dp0"
set "PY="

if defined WORKBUDDY_PYTHON if exist "%WORKBUDDY_PYTHON%" set "PY=%WORKBUDDY_PYTHON%"

if not defined PY (
    where python >nul 2>nul
    if not errorlevel 1 set "PY=python"
)

rem WorkBuddy ships its own python; use it when PATH has none
if not defined PY (
    if exist "%USERPROFILE%\.workbuddy\binaries\python\versions\current\python.exe" set "PY=%USERPROFILE%\.workbuddy\binaries\python\versions\current\python.exe"
)
if not defined PY (
    for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do (
        if not defined PY if exist "%%~D\python.exe" set "PY=%%~D\python.exe"
    )
)

if not defined PY (
    where py >nul 2>nul
    if not errorlevel 1 set "PY=py"
)

if not defined PY goto :nopython

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
echo  [FAIL] No Python found - doctor cannot run.
echo.
echo  This alone explains "no result page": the script exits before
echo  it can print anything useful.
echo.
echo  Pick one:
echo    1. install Python 3 and tick "Add to PATH", then rerun this file;
echo    2. set WORKBUDDY_PYTHON to a python.exe path;
echo    3. WorkBuddy bundles one here:
echo       %USERPROFILE%\.workbuddy\binaries\python\versions
echo.
echo  Raw facts that need no Python:
echo ----------------------------------------------------------
for %%R in ("C:\Program Files\WorkBuddy" "D:\Program Files\WorkBuddyAI" "%LOCALAPPDATA%\Programs\WorkBuddy") do (
    if exist "%%~R\resources\app.asar" (
        echo install: %%~R
        dir /b "%%~R\resources\app.asar.unpacked\cli\dist\lazy-lite-wb" 2>nul
    )
)
echo ----------------------------------------------------------
set "RC=3"
goto :done

:done
if defined WB_NO_PAUSE exit /b %RC%
echo.
pause
exit /b %RC%
