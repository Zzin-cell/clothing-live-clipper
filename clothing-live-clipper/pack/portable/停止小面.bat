@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 停止小面服务
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0stop_service.ps1"
echo 已尝试停止 8787 端口上的服务。
timeout /t 2 >nul
exit /b 0
