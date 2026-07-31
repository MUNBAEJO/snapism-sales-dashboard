@echo off
cd /d "%~dp0"
echo Starting dashboard at http://localhost:8503
"C:\Users\Administrator\AppData\Local\Microsoft\WindowsApps\python.exe" -m streamlit run 스내피즘.py --server.port 8503 --browser.gatherUsageStats false --server.headless false
pause
