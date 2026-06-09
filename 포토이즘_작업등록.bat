@echo off
chcp 65001 > nul
cd /d "%~dp0"
powershell -ExecutionPolicy Bypass -File "%~dp0포토이즘_작업등록.ps1"
