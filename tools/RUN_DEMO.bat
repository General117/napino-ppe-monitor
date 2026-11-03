@echo off
rem Developer PC only (not shipped): runs the monitor on the recorded video (config\demo_override.toml);
rem alert emails are saved to <data>\outbox, not sent.
cd /d "%~dp0.."
"runtime\python\python.exe" "app\run.py" --config "config\demo_override.toml"
pause
