@echo off
chcp 65001 >nul
cd /d "%~dp0"
title stop xiaomian
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell.exe"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_service.ps1"
echo 已尝试停止 8787 端口上的服务。
timeout /t 2 >nul
exit /b 0
