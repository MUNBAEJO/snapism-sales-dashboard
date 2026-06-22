@echo off
cd /d "%~dp0"
rem Daily restart (snapism_restart 09:30): kill 8503; watchdog loop revives within ~2 min.
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8503 ^| findstr LISTENING') do taskkill /f /t /pid %%a >nul 2>&1
