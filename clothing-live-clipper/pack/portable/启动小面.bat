@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 小面 CapCut
echo ============================================
echo   小面 CapCut
echo   首次会自动安装配置，之后直接启动
echo ============================================
echo.

REM 未安装则自动跑完整配置（Python venv / 依赖 / ffmpeg / 模型 / 桌面快捷方式）
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0ensure_ready.ps1"
if errorlevel 1 (
  echo.
  echo [失败] 自动安装未完成。请联网后重试，或双击「首次安装配置.bat」查看详情。
  pause
  exit /b 1
)

echo 正在后台启动服务（无需保持本窗口）...
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0start_service.ps1"
if errorlevel 1 (
  echo.
  echo 启动失败。日志：tools\logs\start_error.txt 与 tools\logs\uvicorn.err.log
  pause
  exit /b 1
)

REM 成功时窗口可自动关；给小白 2 秒看到提示
timeout /t 2 >nul
exit /b 0
