# Streamlit (8503) watchdog - relaunch the dashboard when it is down.
#   default : health-check; launch only if down (called periodically by the loop)
#   -Force  : kill then relaunch
# Intended to be called from watchdog_loop.ps1 (a free-standing process, NOT a
# Task Scheduler task), so a plain Start-Process child survives - no WMI needed.
# ASCII only + $PSScriptRoot (no hardcoded Korean path) -> PS 5.1 safe.
param([switch]$Force)

$ErrorActionPreference = 'SilentlyContinue'
$proj = $PSScriptRoot
$py   = 'C:\Users\Administrator\AppData\Local\Microsoft\WindowsApps\python.exe'
$log  = Join-Path $proj 'logs\watchdog.log'
New-Item -ItemType Directory -Force -Path (Split-Path $log) | Out-Null
function Stamp($m) { Add-Content -Path $log -Value ("{0}  {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $m) }

# 1) health check (IPv4)
$ok = $false
try { $ok = (Invoke-WebRequest 'http://127.0.0.1:8503/_stcore/health' -UseBasicParsing -TimeoutSec 5).StatusCode -eq 200 } catch {}
if ($ok -and -not $Force) { exit 0 }

# 2) kill existing 8503 listeners
$pids = Get-NetTCPConnection -State Listen -LocalPort 8503 -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique
foreach ($p in $pids) { Stop-Process -Id $p -Force -ErrorAction SilentlyContinue }
if ($pids) { Start-Sleep -Seconds 2 }

# 3) launch (Start-Process; survives because the caller is not a Task Scheduler task)
$proc = Start-Process -FilePath $py -ArgumentList 'run_dashboard.py' -WorkingDirectory $proj -WindowStyle Hidden -PassThru
$reason = 'DOWN'
if ($Force) { $reason = 'FORCE' }
Stamp ("{0} -> launch pid={1}" -f $reason, $proc.Id)
exit 0
