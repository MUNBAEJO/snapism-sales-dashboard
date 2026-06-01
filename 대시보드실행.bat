@echo off
chcp 65001 > nul
echo.
echo  ====================================
echo   스내피즘 매출 대시보드 시작 중...
echo  ====================================
echo.
echo  잠시 후 브라우저가 자동으로 열립니다.
echo  (열리지 않으면 http://localhost:8501 접속)
echo.
echo  대시보드를 종료하려면 이 창을 닫으세요.
echo.
cd /d "%~dp0"
python -m streamlit run 스내피즘.py ^
  --server.port 8503 ^
  --browser.gatherUsageStats false ^
  --server.headless false
pause
