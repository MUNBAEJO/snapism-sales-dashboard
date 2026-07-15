# scheduler watchdog loop - every 120s call scheduler_watchdog.ps1 (launch scheduler if down).
# Mirrors watchdog_loop.ps1 (dashboard). Single-instance guard via a session mutex.
# ASCII only + $PSScriptRoot. Started by the boot bat AND can be started manually.
$ErrorActionPreference = 'SilentlyContinue'
$proj   = $PSScriptRoot
$single = Join-Path $proj 'scheduler_watchdog.ps1'
$log    = Join-Path $proj 'logs\scheduler_watchdog.log'
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
function Stamp($m) { Add-Content -Path $log -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) }

$mtx = New-Object System.Threading.Mutex($false, 'snapism_scheduler_watchdog_loop')
if (-not $mtx.WaitOne(0)) { Stamp ("loop-skip dup pid={0}" -f $PID); exit 0 }

Stamp ("loop-start pid={0}" -f $PID)
try {
  while ($true) {
    try { & $single } catch { Stamp ("loop-error {0}" -f $_.Exception.Message) }
    Start-Sleep -Seconds 120
  }
} finally { $mtx.ReleaseMutex() }
