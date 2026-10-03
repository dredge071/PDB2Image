@echo off
rem Launch the flat_trace web tool and open the browser.
rem Override the interpreter with:  set PY=D:\path\to\python.exe
cd /d %~dp0
if not defined PY set PY=C:\Python314\python.exe

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
