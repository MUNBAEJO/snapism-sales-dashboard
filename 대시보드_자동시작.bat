@echo off
cd /d "%~dp0"
echo Stopping existing ngrok/streamlit...
taskkill /f /im ngrok.exe >nul 2>&1
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8503 ^| findstr LISTENING') do taskkill /f /t /pid %%a >nul 2>&1
ping -n 3 127.0.0.1 >nul
echo Starting ngrok...
start "ngrok-tunnel" /MIN cmd /k "C:\Program Files\nodejs\ngrok.exe http --url=cni-division.ngrok.app 8503"
ping -n 4 127.0.0.1 >nul
echo Starting Streamlit on port 8503...
"C:\Users\Administrator\AppData\Local\Microsoft\WindowsApps\python.exe" "run_dashboard.py"
