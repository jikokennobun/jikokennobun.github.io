@echo off
setlocal

set "SCRIPT_DIR=%~dp0"
set "PY_SCRIPT=%SCRIPT_DIR%research_automation.py"

if defined PYTHON (
  "%PYTHON%" -c "import sys" >nul 2>nul
  if not errorlevel 1 (
    "%PYTHON%" "%PY_SCRIPT%" %*
    exit /b %ERRORLEVEL%
  )
)

where python >nul 2>nul
if not errorlevel 1 (
  python -c "import sys" >nul 2>nul
  if not errorlevel 1 (
    python "%PY_SCRIPT%" %*
    exit /b %ERRORLEVEL%
  )
)

where py >nul 2>nul
if not errorlevel 1 (
  py -3 -c "import sys" >nul 2>nul
  if not errorlevel 1 (
    py -3 "%PY_SCRIPT%" %*
    exit /b %ERRORLEVEL%
  )
)

set "BUNDLED_PY=%USERPROFILE%\.cache\codex-runtimes\codex-primary-runtime\dependencies\python\python.exe"
if exist "%BUNDLED_PY%" (
  "%BUNDLED_PY%" "%PY_SCRIPT%" %*
  exit /b %ERRORLEVEL%
)

echo Python was not found. Install Python 3, set PYTHON, or run this inside a Codex workspace with the bundled runtime. 1>&2
exit /b 1
