@echo off
chcp 65001 >nul
cd /d "%~dp0"
echo =============================================
echo  포토이즘 과거 데이터 백필 시작
echo =============================================
echo.
echo 수집할 기간을 입력하세요 (예: 2026-01-01)
echo.
set /p START_DATE="시작 날짜 (YYYY-MM-DD): "
set /p END_DATE="종료 날짜 (YYYY-MM-DD): "
echo.
echo 시작: %START_DATE% ~ 종료: %END_DATE%
echo 10분마다 3일치씩 자동 수집됩니다.
echo.
python photoism_backfill.py %START_DATE% %END_DATE%
echo.
echo 초기화 완료. 이제 [백필_자동실행.bat] 을 작업 스케줄러에 등록하세요.
echo.
pause
