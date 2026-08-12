@echo off
chcp 65001 >nul
cd /d "%~dp0"
title 小面 CapCut · GPU 状态
echo ============================================
echo   小面 CapCut · 查看 GPU / ASR 状态
echo ============================================
echo.

set "ROOT=%~dp0"
if exist "%~dp0..\..\tools\logs" (
  rem pack\portable layout -> app root is ..\..
  for %%I in ("%~dp0..\..") do set "ROOT=%%~fI\"
)

echo [device_hint]
if exist "%ROOT%tools\device_hint.txt" (
  type "%ROOT%tools\device_hint.txt"
) else (
  echo  missing tools\device_hint.txt
)
echo.

echo [gpu_status]
if exist "%ROOT%tools\logs\gpu_status.txt" (
  type "%ROOT%tools\logs\gpu_status.txt"
) else (
  echo  missing tools\logs\gpu_status.txt  （请先运行一次「启动小面.bat」）
)
echo.

echo [nvidia-smi]
where nvidia-smi >nul 2>nul
if errorlevel 1 (
  echo  未找到 nvidia-smi（可能没装 NVIDIA 驱动）
) else (
  nvidia-smi -L
  nvidia-smi --query-gpu=index,name,driver_version,memory.total,memory.free --format=csv
)
echo.

echo [python cuda 探测]
set "PY=%ROOT%.venv\Scripts\python.exe"
if not exist "%PY%" set "PY=%ROOT%tools\python\python.exe"
if exist "%PY%" (
  "%PY%" "%ROOT%pack\portable\_probe_cuda.py" 2>nul
  if errorlevel 1 (
    "%PY%" -c "import ctranslate2; print('ctranslate2_cuda_count=', ctranslate2.get_cuda_device_count())"
  )
) else (
  echo  未找到 python.exe
)
echo.

echo 日志目录：
echo   %ROOT%tools\logs\
echo.
echo 说明：
echo   空白电脑：有 NVIDIA 驱动会自动尝试 GPU；驱动正常但 python cuda_count=0
echo   通常是 CUDA 依赖没装好。可再运行「首次安装配置.bat」修复。
echo   device=cuda      → 配置为使用 GPU
echo   device=cpu       → 回退 CPU
echo   gpu_index        → 多卡时自动/手动选择
echo   指定某张卡：tools\gpu_index.txt 写入 0/1/2
echo.
pause
