@echo off
rem Diagnoses the camera connection: RTSP port, channel numbering, NVR error messages.
rem Asks for the NVR password (not stored). Optional argument: channel number.
cd /d "%~dp0.."
"runtime\python\python.exe" "tools\diagnose_camera.py" %*
echo.
pause
