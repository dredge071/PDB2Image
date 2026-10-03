@echo off
rem Stop the flat_trace web tool and any pipeline subprocess it started.
set FOUND=0
for /f "tokens=5" %%a in ('netstat -ano ^| findstr /C:":5000 " ^| findstr /C:"LISTENING"') do (
    taskkill /PID %%a /T /F >nul 2>&1
    set FOUND=1
)
if %FOUND%==1 (
    echo flat_trace web tool stopped.
) else (
    echo Not running - nothing to stop.
)
pause
