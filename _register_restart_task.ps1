# 매일 09:30 Streamlit 대시보드 재시작 작업 등록 (관리자 권한 필요)
# 경로는 런타임에 $PSScriptRoot 로 얻어 한글 인코딩 문제를 피한다.
$ErrorActionPreference = 'Stop'
$proj = $PSScriptRoot
$bat  = Join-Path $proj 'restart_dashboard.bat'

$action    = New-ScheduledTaskAction -Execute 'cmd.exe' -Argument ('/c "' + $bat + '"') -WorkingDirectory $proj
$trigger   = New-ScheduledTaskTrigger -Daily -At 09:30
$principal = New-ScheduledTaskPrincipal -UserId 'Administrator' -LogonType Interactive -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew

Register-ScheduledTask -TaskName 'snapism_restart' -Action $action -Trigger $trigger `
  -Principal $principal -Settings $settings `
  -Description 'Daily 09:30 Streamlit dashboard restart (cache refresh). ngrok/crawlers untouched.' -Force | Out-Null

Write-Host 'snapism_restart registered OK'
