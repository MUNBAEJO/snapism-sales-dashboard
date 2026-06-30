@echo off
cd /d "%~dp0"
python sm_weekly.py >> logs\sm_weekly_task.log 2>&1
