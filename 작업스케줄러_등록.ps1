param([string]$Dir = "")

# 경로를 먼저 확보 (비관리자 실행 시 $PSScriptRoot 유효)
if (-not $Dir) { $Dir = $PSScriptRoot }
if (-not $Dir) { $Dir = Split-Path -Parent $MyInvocation.MyCommand.Path }

# 관리자 권한 없으면 경로를 인수로 넘겨 재실행
if (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]"Administrator")) {
    $ps1 = $MyInvocation.MyCommand.Path
    Start-Process PowerShell -Verb RunAs -ArgumentList "-ExecutionPolicy Bypass -File `"$ps1`" -Dir `"$Dir`""
    Exit
}

$batPath  = Join-Path $Dir "백필_자동실행.bat"
$taskName = "포토이즘백필"

Write-Host "작업 경로: $batPath"

# 기존 작업 삭제
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$action   = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$batPath`"" -WorkingDirectory $Dir
$trigger  = New-ScheduledTaskTrigger -RepetitionInterval (New-TimeSpan -Minutes 20) -Once -At (Get-Date).AddMinutes(1)
$settings = New-ScheduledTaskSettingsSet `
    -ExecutionTimeLimit (New-TimeSpan -Minutes 19) `
    -MultipleInstances IgnoreNew `
    -StartWhenAvailable

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Settings $settings `
    -RunLevel Highest `
    -Force

Write-Host ""
Write-Host "등록 완료: $taskName" -ForegroundColor Green
Write-Host "   20분마다 자동 실행됩니다."
Write-Host ""
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State, TaskPath
Read-Host "엔터를 눌러 닫기"
