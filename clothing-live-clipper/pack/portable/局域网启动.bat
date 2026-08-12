@echo off
chcp 65001 >nul
cd /d "%~dp0"
title xiaomian · 局域网稳定排队模式
echo ============================================
echo   xiaomian · 局域网 / 小团队稳定模式
echo   监听 0.0.0.0:8787
echo   默认：排队 + 听写串行 + 预热等待（防多人挤崩）
echo ============================================
echo.

set "ROOT=%~dp0"
if exist "%~dp0..\..\tools" (
  for %%I in ("%~dp0..\..") do set "ROOT=%%~fI\"
)

if not exist "%ROOT%tools" mkdir "%ROOT%tools" >nul 2>nul
echo lan> "%ROOT%tools\lan_mode.txt"

REM Stable queue defaults (override by pre-setting env vars if needed)
if not defined CLIPPER_QUEUE_MODE set "CLIPPER_QUEUE_MODE=stable"
if not defined CLIPPER_MAX_CONCURRENT_JOBS set "CLIPPER_MAX_CONCURRENT_JOBS=3"
if not defined CLIPPER_ASR_SLOTS set "CLIPPER_ASR_SLOTS=1"
if not defined CLIPPER_LLM_SLOTS set "CLIPPER_LLM_SLOTS=1"
if not defined CLIPPER_RENDER_SLOTS set "CLIPPER_RENDER_SLOTS=1"
if not defined CLIPPER_WARM_EXTRACT_SLOTS set "CLIPPER_WARM_EXTRACT_SLOTS=1"
if not defined CLIPPER_WARM_FRONT_N set "CLIPPER_WARM_FRONT_N=2"
if not defined CLIPPER_LLM_PARALLEL set "CLIPPER_LLM_PARALLEL=1"

if not defined CLIPPER_ASR_GPU_IDS (
  if exist "%ROOT%tools\gpu_ids.txt" (
    set /p CLIPPER_ASR_GPU_IDS=<"%ROOT%tools\gpu_ids.txt"
  ) else (
    set "CLIPPER_ASR_GPU_IDS=0,1"
  )
)
set "CLIPPER_BIND_HOST=0.0.0.0"

echo queue mode = %CLIPPER_QUEUE_MODE%
echo max active jobs = %CLIPPER_MAX_CONCURRENT_JOBS%
echo asr/llm/render slots = %CLIPPER_ASR_SLOTS%/%CLIPPER_LLM_SLOTS%/%CLIPPER_RENDER_SLOTS%
echo warm front N = %CLIPPER_WARM_FRONT_N%
echo GPU ids = %CLIPPER_ASR_GPU_IDS%
echo.

set "PS=%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe"
if not exist "%PS%" set "PS=powershell.exe"

"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%pack\portable\ensure_ready.ps1"
if errorlevel 1 (
  echo auto install failed
  pause
  exit /b 1
)
"%PS%" -NoProfile -ExecutionPolicy Bypass -File "%ROOT%pack\portable\start_service.ps1"
if errorlevel 1 (
  echo start failed, see tools\logs\
  pause
  exit /b 1
)

echo.
echo 本机访问: http://127.0.0.1:8787/
for /f "tokens=2 delims=:" %%a in ('ipconfig ^| findstr /c:"IPv4"') do (
  for /f "tokens=* delims= " %%b in ("%%a") do echo 局域网访问: http://%%b:8787/
)
echo 防火墙若拦截请放行 TCP 8787
echo 多人上传会自动排队：听写串行，后面的任务预热后等待
echo.
timeout /t 6 >nul
exit /b 0
