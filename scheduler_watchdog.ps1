# scheduler.py watchdog - relaunch the collection scheduler when it is not running.
#   default : check; launch only if scheduler.py is NOT running (called by the loop)
#   -Force  : stop existing scheduler.py then relaunch
# The scheduler has a single-instance socket guard, so a rare double-launch is harmless
# (the 2nd instance exits immediately). ASCII only + $PSScriptRoot -> PS 5.1 safe.
param([switch]$Force)

$ErrorActionPreference = 'SilentlyContinue'
$proj = $PSScriptRoot
$py   = 'C:\Users\Administrator\AppData\Local\Microsoft\WindowsApps\python.exe'
$pyw  = Join-Path (Split-Path $py) 'pythonw.exe'
if (-not (Test-Path $pyw)) { $pyw = $py }
$log  = Join-Path $proj 'logs\scheduler_watchdog.log'
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
function Stamp($m) { Add-Content -Path $log -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) }

# 1) liveness = the scheduler's single-instance guard port (127.0.0.1:47615) is LISTENING.
#    (pythonw 의 CommandLine 은 WMI에 안 잡힐 때가 있어 프로세스 매칭은 불안정 → 포트로 확인.)
$owner = $null
$conn  = Get-NetTCPConnection -State Listen -LocalPort 47615 -ErrorAction SilentlyContinue
if ($conn) { $owner = ($conn | Select-Object -ExpandProperty OwningProcess -Unique) }
if ($owner -and -not $Force) { exit 0 }

# 2) -Force: stop whoever holds the guard port first
if ($Force -and $owner) {
  foreach ($op in $owner) { Stop-Process -Id $op -Force -ErrorAction SilentlyContinue }
  Start-Sleep -Seconds 1
}

# 3) launch (detached, hidden). Start-Process survives because the caller is a free-standing loop.
$proc = Start-Process -FilePath $pyw -ArgumentList 'scheduler.py' -WorkingDirectory $proj -WindowStyle Hidden -PassThru
$reason = 'DOWN'
if ($Force) { $reason = 'FORCE' }
Stamp ("{0} -> launch scheduler pid={1}" -f $reason, $proc.Id)
exit 0
