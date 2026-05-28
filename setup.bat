@echo off
chcp 65001 > nul
echo.
echo  ====================================
echo   스내피즘 대시보드 - 최초 설치
echo  ====================================
echo.
echo  필요한 패키지를 설치합니다...
echo.
pip install -r requirements.txt
echo.
echo  ====================================
echo   설치 완료!
echo   이제 "대시보드실행.bat" 을 더블클릭하면
echo   브라우저에서 대시보드가 열립니다.
echo  ====================================
echo.
pause
