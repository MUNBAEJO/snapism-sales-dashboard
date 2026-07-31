$startup = [System.Environment]::GetFolderPath('Startup')
$batPath = 'C:\Users\Administrator\Desktop\스내피즘 매출데이터\대시보드_자동시작.bat'
$lnkPath = Join-Path $startup 'SnapismDashboard.lnk'

$shell = New-Object -ComObject WScript.Shell
$sc = $shell.CreateShortcut($lnkPath)
$sc.TargetPath = $batPath
$sc.WorkingDirectory = 'C:\Users\Administrator\Desktop\스내피즘 매출데이터'
$sc.WindowStyle = 7
$sc.Save()

Write-Host "등록 완료: $lnkPath"
