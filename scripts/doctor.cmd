@echo off
setlocal
chcp 65001 >nul
rem Read-only diagnostic. No admin, no writes to the install, safe to run any time.
rem Every layer is printed with a state, including the ones that are missing or
rem not in effect, so "why is it still there" has an answer.
set "PY="
where python >nul 2>nul
if %errorlevel%==0 set "PY=python"
if not defined PY (
    where py >nul 2>nul
    if %errorlevel%==0 set "PY=py"
)
if not defined PY (
    echo [FAIL] python not found. Install Python 3 or set WORKBUDDY_PYTHON.
    exit /b 2
)
"%PY%" -X utf8 "%~dp0doctor.py" --write-report %*
set "RC=%errorlevel%"
echo.
echo report: %~dp0..\reports\doctor-latest.txt
exit /b %RC%
