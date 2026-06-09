# Register "photoism_daily" Windows scheduled task (runs photoism_crawler.py daily 09:05)
# Mirrors snapism_daily. Self-elevates via UAC.
param([string]$Dir = "")

if (-not $Dir) { $Dir = $PSScriptRoot }
if (-not $Dir) { $Dir = Split-Path -Parent $MyInvocation.MyCommand.Path }

# Self-elevate to Administrator (pass project dir to elevated instance)
if (-NOT ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole([Security.Principal.WindowsBuiltInRole]"Administrator")) {
    $ps1 = $MyInvocation.MyCommand.Path
    Start-Process PowerShell -Verb RunAs -ArgumentList "-ExecutionPolicy Bypass -File `"$ps1`" -Dir `"$Dir`""
    Exit
}

$taskName = "photoism_daily"
$py = "C:\Users\Administrator\AppData\Local\Microsoft\WindowsApps\python.exe"
if (-not (Test-Path $py)) {
    $py = (Get-Command python.exe).Source
}

Write-Host "Project dir : $Dir"
Write-Host "Python      : $py"
Write-Host "Crawler     : photoism_crawler.py"
Write-Host ""

# Remove existing task if any
Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue

$action    = New-ScheduledTaskAction -Execute $py -Argument "photoism_crawler.py" -WorkingDirectory $Dir
$trigger   = New-ScheduledTaskTrigger -Daily -At 9:05AM
$principal = New-ScheduledTaskPrincipal -UserId "Administrator" -LogonType Interactive -RunLevel Highest
$settings  = New-ScheduledTaskSettingsSet `
    -StartWhenAvailable `
    -MultipleInstances IgnoreNew `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) `
    -RestartCount 2 `
    -RestartInterval (New-TimeSpan -Minutes 10)

Register-ScheduledTask `
    -TaskName $taskName `
    -Action $action `
    -Trigger $trigger `
    -Principal $principal `
    -Settings $settings `
    -Force

Write-Host ""
Write-Host "DONE: '$taskName' registered (daily 09:05, retry x2, reboot-safe)." -ForegroundColor Green
Write-Host ""
Get-ScheduledTask -TaskName $taskName | Select-Object TaskName, State | Format-Table -AutoSize
$info = Get-ScheduledTaskInfo -TaskName $taskName
Write-Host ("Next run: " + $info.NextRunTime)
Write-Host ""
Read-Host "Press Enter to close"
