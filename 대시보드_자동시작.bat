@echo off
cd /d "C:\Users\Administrator\Desktop\스내피즘 매출데이터"

start "ngrok-tunnel" /MIN cmd /k "ngrok http --url=snapism-dashboard.ngrok.app 8503"

timeout /t 3 /nobreak > nul

python -m streamlit run 스내피즘.py --server.port 8503 --browser.gatherUsageStats false --server.headless true

pause
