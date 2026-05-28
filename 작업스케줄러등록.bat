@echo off
chcp 65001 > nul
echo.
echo  ====================================
echo   Windows 작업 스케줄러 등록
echo   매일 오전 9:00 자동 실행
echo   (꺼져 있었으면 켜지자마자 즉시 실행)
echo  ====================================
echo.

cd /d "%~dp0"

REM Python 경로 확인
for /f "delims=" %%i in ('where python 2^>nul') do set PYTHON_PATH=%%i
if "%PYTHON_PATH%"=="" (
    echo  [오류] Python을 찾을 수 없습니다.
    pause
    exit /b 1
)

set TASK_NAME=스내피즘_매출_자동수집
set SCRIPT_PATH=%~dp0crawler.py
set WORKING_DIR=%~dp0
set XML_FILE=%TEMP%\snapism_task.xml

echo  Python 경로: %PYTHON_PATH%
echo  스크립트: %SCRIPT_PATH%
echo.

REM 기존 작업 삭제
schtasks /delete /tn "%TASK_NAME%" /f > nul 2>&1

REM XML 생성 (StartWhenAvailable = 놓쳤으면 켜지자마자 즉시 실행)
(
echo ^<?xml version="1.0" encoding="UTF-16"?^>
echo ^<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task"^>
echo   ^<Triggers^>
echo     ^<CalendarTrigger^>
echo       ^<StartBoundary^>2026-01-01T09:00:00^</StartBoundary^>
echo       ^<Enabled^>true^</Enabled^>
echo       ^<ScheduleByDay^>
echo         ^<DaysInterval^>1^</DaysInterval^>
echo       ^</ScheduleByDay^>
echo     ^</CalendarTrigger^>
echo   ^</Triggers^>
echo   ^<Settings^>
echo     ^<StartWhenAvailable^>true^</StartWhenAvailable^>
echo     ^<ExecutionTimeLimit^>PT15M^</ExecutionTimeLimit^>
echo     ^<MultipleInstancesPolicy^>IgnoreNew^</MultipleInstancesPolicy^>
echo     ^<DisallowStartIfOnBatteries^>false^</DisallowStartIfOnBatteries^>
echo     ^<StopIfGoingOnBatteries^>false^</StopIfGoingOnBatteries^>
echo   ^</Settings^>
echo   ^<Actions Context="Author"^>
echo     ^<Exec^>
echo       ^<Command^>"%PYTHON_PATH%"^</Command^>
echo       ^<Arguments^>"%SCRIPT_PATH%"^</Arguments^>
echo       ^<WorkingDirectory^>%WORKING_DIR%^</WorkingDirectory^>
echo     ^</Exec^>
echo   ^</Actions^>
echo   ^<Principals^>
echo     ^<Principal id="Author"^>
echo       ^<LogonType^>InteractiveToken^</LogonType^>
echo       ^<RunLevel^>HighestAvailable^</RunLevel^>
echo     ^</Principal^>
echo   ^</Principals^>
echo ^</Task^>
) > "%XML_FILE%"

REM XML로 작업 등록
schtasks /create /tn "%TASK_NAME%" /xml "%XML_FILE%" /f
del "%XML_FILE%" > nul 2>&1

if %errorlevel% equ 0 (
    echo.
    echo  ====================================
    echo   등록 완료!
    echo.
    echo   - 매일 오전 9시 자동 실행
    echo   - PC가 꺼져 있었다면 켜지자마자
    echo     즉시 실행됩니다 (놓친 날 자동 보충)
    echo.
    echo   확인: 작업 스케줄러 > 스내피즘_매출_자동수집
    echo  ====================================
) else (
    echo.
    echo  [오류] 등록 실패. 관리자 권한으로 실행해주세요.
    echo  이 파일을 우클릭 ^> "관리자 권한으로 실행"
)
echo.
pause
