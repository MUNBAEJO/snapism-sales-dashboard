@echo off
chcp 65001 > nul
echo.
echo  ====================================
echo   스내피즘 스케줄러 시작
echo   (창이 닫혀도 백그라운드에서 실행)
echo  ====================================
echo.
cd /d "%~dp0"

REM pythonw = 창 없이 백그라운드 실행
start /b pythonw "%~dp0scheduler.py"

echo  스케줄러가 백그라운드에서 시작됐습니다.
echo  매일 09:00에 자동으로 데이터를 수집합니다.
echo.
echo  로그 확인: logs\scheduler.log
echo.
pause
