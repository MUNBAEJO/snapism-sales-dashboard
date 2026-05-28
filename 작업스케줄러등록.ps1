# 스내피즘 매출 자동수집 - 작업 스케줄러 등록
$scriptPath = "C:\Users\Administrator\Desktop\스내피즘 매출데이터\crawler.py"
$workDir    = "C:\Users\Administrator\Desktop\스내피즘 매출데이터"
$pythonPath = (Get-Command python -ErrorAction Stop).Source
$taskName   = "스내피즘_매출_자동수집"

$action = New-ScheduledTaskAction `
    -Execute $pythonPath `
    -Argument "`"$scriptPath`"" `
    -WorkingDirectory $workDir

$trigger = New-ScheduledTaskTrigger -Daily -At "09:00AM"

$settings = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 15) `
    -MultipleInstances IgnoreNew `
    -DontStopIfGoingOnBatteries `
    -AllowStartIfOnBatteries

Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Force

$task = Get-ScheduledTask -TaskName $taskName
Write-Host ""
Write-Host "=====================================" -ForegroundColor Green
Write-Host " 등록 완료!" -ForegroundColor Green
Write-Host " 작업명: $($task.TaskName)"
Write-Host " 상태:   $($task.State)"
Write-Host " - 매일 오전 9시 자동 실행"
Write-Host " - 재부팅 후 놓쳤으면 즉시 실행"
Write-Host "=====================================" -ForegroundColor Green
Write-Host ""
pause
