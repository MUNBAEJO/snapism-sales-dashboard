@echo off
chcp 65001 > nul
echo.
echo  ====================================
echo   CSV 데이터 누적 처리 중...
echo  ====================================
echo.
echo  [ 사용 방법 ]
echo  1. 어드민에서 다운받은 CSV 파일을
echo     "raw" 폴더에 넣어주세요.
echo  2. 이 파일을 더블클릭하면 자동으로
echo     누적 데이터에 합산됩니다.
echo.
cd /d "%~dp0"
python ingest.py
echo.
echo  ====================================
echo   완료! 대시보드를 새로고침(F5)하세요.
echo  ====================================
echo.
pause
