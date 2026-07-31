@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 小面 CapCut - 首次安装配置
echo ============================================
echo   小面 CapCut · 首次安装配置
echo   自动检查 Python / 依赖 / ffmpeg / 模型
echo ============================================
echo.

powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install_all.ps1"
if errorlevel 1 (
  echo.
  echo [失败] 安装未完成，请根据上方错误排查后重试。
  pause
  exit /b 1
)

echo.
echo [完成] 安装成功。可双击「启动小面.bat」，或使用桌面上的「小面CapCut」快捷方式。
pause
exit /b 0
