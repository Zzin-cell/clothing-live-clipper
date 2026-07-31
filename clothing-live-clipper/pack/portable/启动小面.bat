@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 小面 CapCut
echo 正在启动小面（无需保持本窗口）...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_service.ps1"
if errorlevel 1 (
  echo.
  echo 启动失败。可先运行「首次安装配置.bat」，或查看 tools\logs\start_error.txt
  pause
  exit /b 1
)
exit /b 0
