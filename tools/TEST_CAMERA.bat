@echo off
rem Tests the NVR connection and saves data\camera_test.jpg with the zones drawn.
cd /d "%~dp0.."
"runtime\python\python.exe" "tools\test_camera.py" %*
echo.
pause
