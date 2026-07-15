@echo off
cd /d "%~dp0"
rem ngrok runs as a Windows service (ngrok.yml, auto-start) - do NOT touch it here.
rem Start the watchdog loop; it launches and keeps the 8503 dashboard alive.
start "snapism-watchdog" /MIN powershell.exe -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0watchdog_loop.ps1"
rem Start the scheduler watchdog loop; it keeps scheduler.py (daily collection) alive if it crashes.
start "snapism-scheduler-watchdog" /MIN powershell.exe -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File "%~dp0scheduler_watchdog_loop.ps1"
