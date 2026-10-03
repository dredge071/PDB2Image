@echo off
setlocal enabledelayedexpansion
rem Launch the flat_trace web tool and open the browser.
rem Interpreter resolution order:
rem   PY env var  >  FLAT_TRACE_PYTHON
rem   >  a pymol-env next to this repo, IF it has flask (single-env setup)
rem   >  python on PATH
cd /d %~dp0
if not defined PY if defined FLAT_TRACE_PYTHON set PY=%FLAT_TRACE_PYTHON%
if not defined PY (
    set "CAND=%~dp0..\pymol-env\python.exe"
    if exist "!CAND!" (
        "!CAND!" -c "import flask" >nul 2>&1 && set PY=!CAND!
    )
)
if not defined PY set PY=python.exe

rem already running?
netstat -ano | findstr /C:":5000 " | findstr /C:"LISTENING" >nul
if not errorlevel 1 (
    echo flat_trace web tool is already running at http://127.0.0.1:5000
    start http://127.0.0.1:5000
    pause
    exit /b 0
)

start "flat_trace-web" /min "%PY%" app.py
rem wait for the server to come up, then open the browser
timeout /t 3 /nobreak >nul
start http://127.0.0.1:5000
echo Started. Close via stop_web.bat (or just run it - the server window stays minimized).
