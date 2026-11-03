@echo off
rem Checks the PC: Intel GPU, model speed, fonts, disk space, passwords set.
cd /d "%~dp0.."
"runtime\python\python.exe" "tools\check_system.py" %*
echo.
pause
