@echo off
chcp 65001 >nul
cd /d "%~dp0"
title xiaomian · 修复 GPU
echo 正在尝试修复 GPU 听写（需联网）...
echo.
set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell.exe"
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%~dp0修复GPU.ps1"
echo.
if errorlevel 1 (
  echo 修复未完全成功，请把 tools\logs\gpu_repair_*.log 发出来。
) else (
  echo 若显示 GPU_OK：请先 停止小面 再 启动小面。
)
pause
