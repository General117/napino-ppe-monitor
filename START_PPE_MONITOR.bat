@echo off
rem ==========================================================================================
rem  Napino Auto - PPE Compliance Monitor
rem  Starts the monitor and opens the dashboard (http://localhost:8080).
rem  To stop: press Ctrl+C in this window (answer Y if asked "Terminate batch job"), or close it.
rem ==========================================================================================
title Napino PPE Compliance Monitor - press Ctrl+C to stop
cd /d "%~dp0"
set "PY=%~dp0runtime\python\python.exe"
if not exist "%PY%" (
    echo [ERROR] Runtime not found: %PY%
    echo The package is incomplete - copy the whole live_environment folder again.
    pause
    exit /b 1
)
echo Starting the PPE Compliance Monitor - dashboard: http://localhost:8080
echo Press Ctrl+C in this window to stop.
echo.
"%PY%" "%~dp0app\run.py" %*
echo.
echo The monitor has stopped.
pause
