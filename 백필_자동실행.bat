@echo off
chcp 949 > nul
REM ★옛 예약작업 '포토이즘백필'(2026-06-05 등록, 20분마다 무한)이 이 파일을 부른다.
REM   그 작업이 아직 안 지워져 낮에도 백필이 돌던 문제 → 여기에도 야간 가드를 둔다.
REM   작업을 제대로 지우려면 _remove_rogue_task.bat 을 관리자 권한으로 실행할 것.
cd /d "%~dp0"
if /i "%~1"=="--now" goto RUN
set "T=%TIME: =0%"
set /a HH=1%T:~0,2%-100
if %HH% GEQ 8 exit /b 0

:RUN
"C:\Users\Administrator\AppData\Local\Microsoft\WindowsApps\python.exe" photoism_backfill.py
exit /b %ERRORLEVEL%
