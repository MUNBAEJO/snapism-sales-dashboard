@echo off
REM 포토이즘 2025년 백필 예약작업 등록 — 관리자 권한이 필요해 따로 둔다.
REM 사용법: 이 파일을 마우스 오른쪽 클릭 → "관리자 권한으로 실행"
REM (예약작업 등록은 UAC 승격이 필요해서 일반 실행으로는 'Access is denied' 가 난다)
cd /d "%~dp0"

echo [1/2] 예약작업 등록: photoism_backfill_2025
schtasks /Create /TN "photoism_backfill_2025" /XML "%~dp0_backfill_2025_task.xml" /F
if errorlevel 1 (
  echo.
  echo [실패] 관리자 권한으로 실행했는지 확인하세요.
  pause
  exit /b 1
)

echo.
echo [2/2] 등록 확인
schtasks /Query /TN "photoism_backfill_2025" /FO LIST

echo.
echo 완료. 매일 00:00~08:00 사이 20분마다 3일치씩 받아요.
echo 진행 상황:  python photoism_backfill.py --status
pause
