@echo off
cd /d "%~dp0"
rem Streamlit(8503) only - ngrok/crawlers untouched
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8503 ^| findstr LISTENING') do taskkill /f /t /pid %%a >nul 2>&1
ping -n 3 127.0.0.1 >nul
start "snapism-dashboard" /MIN "C:\Users\Administrator\AppData\Local\Microsoft\WindowsApps\python.exe" "run_dashboard.py"
