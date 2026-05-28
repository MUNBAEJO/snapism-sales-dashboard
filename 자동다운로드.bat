@echo off
chcp 65001 > nul
echo.
echo  ====================================
echo   스내피즘 매출 수동 다운로드
echo  ====================================
echo.
echo  [사용법]
echo   1) 그냥 Enter  → 어제 데이터 다운로드
echo   2) 날짜 입력   → 2026-04-01
echo   3) 범위 입력   → 2026-04-01 2026-04-30
echo.
set /p INPUT="날짜 입력 (없으면 Enter): "

cd /d "%~dp0"

if "%INPUT%"=="" (
    echo  어제 데이터 다운로드 중...
    python crawler.py
) else (
    echo  %INPUT% 다운로드 중...
    python crawler.py %INPUT%
)

echo.
echo  ====================================
echo   완료! 대시보드를 새로고침(F5)하세요.
echo  ====================================
echo.
pause
