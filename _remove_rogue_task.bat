@echo off
chcp 949 > nul
REM 옛 백필 예약작업 '포토이즘백필' 삭제 - 관리자 권한 필요.
REM 이 작업은 2026-06-05 에 만든 것으로, 20분마다 무한 반복이라 밤낮없이 백필을 돌린다.
REM 현행 'photoism_backfill_2025'(야간 전용)와 중복이므로 지운다.
echo 삭제할 작업: 포토이즘백필
schtasks /Delete /TN "포토이즘백필" /F
if errorlevel 1 (
  echo.
  echo [실패] 관리자 권한으로 실행했는지 확인하세요.
  pause
  exit /b 1
)
echo.
echo 완료. 이제 백필은 'photoism_backfill_2025'(매일 00:00~08:00)만 돌아요.
echo 남은 작업 확인:
schtasks /Query /TN "photoism_backfill_2025" /FO LIST | findstr /C:"작업" /C:"다음"
pause
