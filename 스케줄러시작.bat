@echo off
cd /d "%~dp0"
echo Starting scheduler...
echo Log: logs\scheduler.log
start /b pythonw "%~dp0scheduler.py"
echo Done.
pause
