@echo off
cd /d "%~dp0"

echo Stopping existing ngrok/streamlit...
taskkill /f /im ngrok.exe >nul 2>&1
taskkill /f /im streamlit.exe >nul 2>&1
timeout /t 2 /nobreak > nul

echo Starting ngrok...
start "ngrok-tunnel" /MIN cmd /k "C:\Program Files\nodejs\ngrok.exe http --url=cni-division.ngrok.app 8503"
timeout /t 3 /nobreak > nul

echo Starting Streamlit on port 8503...
"C:\Users\Administrator\AppData\Local\Microsoft\WindowsApps\python.exe" -m streamlit run ��������.py --server.port 8503 --browser.gatherUsageStats false --server.headless true
pause
