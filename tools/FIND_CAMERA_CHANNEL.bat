@echo off
rem Saves one snapshot per NVR channel to data\channel_scan to find SMT Gallery 2.
cd /d "%~dp0.."
"runtime\python\python.exe" "tools\find_camera_channel.py" %*
echo.
pause
