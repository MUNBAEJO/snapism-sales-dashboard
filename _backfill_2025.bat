@echo off
REM 포토이즘 2025년 매출 백필 — 예약작업(photoism_backfill_2025)이 20분마다 호출한다.
REM 한 번 실행 = 3일치 청크 1개(30개국). 진행 상태는 logs/backfill_state.json 이 들고 있어
REM 다음 호출이 알아서 이어서 받는다. 다 받으면 스스로 ingest 까지 돌리고 이후 호출은 그냥 끝난다.
cd /d "%~dp0"
"C:\Users\Administrator\AppData\Local\Microsoft\WindowsApps\python.exe" photoism_backfill.py
exit /b %ERRORLEVEL%
