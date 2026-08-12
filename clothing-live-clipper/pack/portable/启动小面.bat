@echo off
chcp 65001 >nul
cd /d "%~dp0"
title xiaomian
echo ============================================
echo   xiaomian
echo   首次会自动安装配置，之后直接启动
echo ============================================
echo.

set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell.exe"

REM 未安装则自动跑完整配置（Python venv / 依赖 / ffmpeg / 模型 / 桌面快捷方式）
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0ensure_ready.ps1"
if errorlevel 1 (
  echo.
  echo [失败] 自动安装未完成。请查看 tools\logs\ ，或双击「首次安装配置.bat」。
  pause
  exit /b 1
)

echo 正在后台启动服务（无需保持本窗口）...
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_service.ps1"
if errorlevel 1 (
  echo.
  echo 启动失败。日志：tools\logs\start_error.txt 与 tools\logs\uvicorn.err.log
  pause
  exit /b 1
)

timeout /t 2 >nul
exit /b 0
