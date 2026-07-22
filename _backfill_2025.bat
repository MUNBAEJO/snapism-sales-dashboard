@echo off
chcp 949 > nul
REM 포토이즘 2025년 매출 백필 - 예약작업(photoism_backfill_2025)이 20분마다 호출한다.
REM 한 번 실행 = 3일치 청크 1개(30개국). 진행 상태는 logs\backfill_state.json 이 들고 있어
REM 다음 호출이 알아서 이어서 받는다. 다 받으면 스스로 ingest 까지 돌리고 이후 호출은 그냥 끝난다.
REM ★이 파일은 반드시 CP949 + CRLF 로 저장할 것. UTF-8/LF 로 두면 cmd 가 줄을 잘못 끊어
REM   '/TN 은(는) 내부 또는 외부 명령...' 같은 엉뚱한 오류가 난다.
cd /d "%~dp0"

REM ★야간(00:00~07:59) 에만 돈다. 탐색기에서 실수로 더블클릭해도 낮에는 CMS 를 안 건드린다.
REM   (일부러 지금 돌리려면:  _backfill_2025.bat --now )
if /i "%~1"=="--now" goto RUN
set "T=%TIME: =0%"
set /a HH=1%T:~0,2%-100
if %HH% GEQ 8 goto OUTSIDE

:RUN
"C:\Users\Administrator\AppData\Local\Microsoft\WindowsApps\python.exe" photoism_backfill.py
exit /b %ERRORLEVEL%

:OUTSIDE
echo.
echo  [건너뜀] 지금은 %T:~0,5% 라 백필 시간대가 아니에요.
echo           이 작업은 매일 00:00~08:00 에만 자동으로 돌아요.
echo.
echo  - 진행 상황 보기 : python photoism_backfill.py --status
echo  - 지금 굳이 받기 : _backfill_2025.bat --now
echo.
timeout /t 15
exit /b 0
