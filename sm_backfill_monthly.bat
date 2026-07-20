@echo off
rem ============================================================
rem  SM PICK backfill - monthly chunks (one-off).
rem  Reason: sm_collect used to skip PICK slot titles ("PW ..."),
rem          so 2026-01-23 ~ 2026-07-19 shoot counts are incomplete.
rem  Each chunk saves on completion -> a failure only loses that month.
rem  sm_collect upserts, so re-running any chunk is safe.
rem ============================================================
cd /d "%~dp0"
set LOG=logs\sm_backfill.log

echo. >> "%LOG%"
echo ================================================== >> "%LOG%"
echo [%date% %time%] SM PICK backfill START >> "%LOG%"
echo ================================================== >> "%LOG%"

call :run 2026-01-23 2026-01-31
call :run 2026-02-01 2026-02-28
call :run 2026-03-01 2026-03-31
call :run 2026-04-01 2026-04-30
call :run 2026-05-01 2026-05-31
call :run 2026-06-01 2026-06-30
call :run 2026-07-01 2026-07-19

echo [%date% %time%] SM PICK backfill ALL DONE >> "%LOG%"
exit /b 0

:run
echo. >> "%LOG%"
echo --- [%date% %time%] chunk %1 ~ %2 --- >> "%LOG%"
python sm_collect.py %1 %2 all 8 >> "%LOG%" 2>&1
if errorlevel 1 (
  echo [%date% %time%] WARN chunk %1 ~ %2 exit=%errorlevel% ^(continue^) >> "%LOG%"
) else (
  echo [%date% %time%] OK   chunk %1 ~ %2 >> "%LOG%"
)
exit /b 0
