@echo off
setlocal
set "FFBIN=%LOCALAPPDATA%\ffmpeg\bin"
set "PATH=%FFBIN%;%PATH%"

echo === ffmpeg ===
where ffmpeg
ffmpeg -version 2>nul | findstr /i "version"

echo === stop old server on 8787 ===
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":8787" ^| findstr LISTENING') do (
  echo killing PID %%a
  taskkill /F /PID %%a >nul 2>&1
)

cd /d "%~dp0\.."
set PYTHONPATH=src
rem GPU first: prefer medium + denoise for accuracy.
if not defined CLIPPER_ASR_DEVICE set "CLIPPER_ASR_DEVICE=cuda"
if not defined CLIPPER_ASR_COMPUTE_TYPE set "CLIPPER_ASR_COMPUTE_TYPE=float16"
if not defined CLIPPER_ASR_QUALITY set "CLIPPER_ASR_QUALITY=high"
if not defined CLIPPER_ASR_DENOISE set "CLIPPER_ASR_DENOISE=1"
if exist "%USERPROFILE%\AppData\grok\models\whisper-medium\model.bin" (
  if not defined CLIPPER_LOCAL_WHISPER_MODEL set "CLIPPER_LOCAL_WHISPER_MODEL=%USERPROFILE%\AppData\grok\models\whisper-medium"
)
if exist "%USERPROFILE%\AppData\grok\models\whisper-small\model.bin" (
  if not defined CLIPPER_LOCAL_WHISPER_MODEL set "CLIPPER_LOCAL_WHISPER_MODEL=%USERPROFILE%\AppData\grok\models\whisper-small"
)
if not defined CLIPPER_ASR_BEAM_SIZE set "CLIPPER_ASR_BEAM_SIZE=5"
if not defined CLIPPER_ASR_BEST_OF set "CLIPPER_ASR_BEST_OF=5"
echo === start uvicorn ===
start "clipper-web-8787" cmd /k "set PATH=%FFBIN%;%PATH%&& set PYTHONPATH=src&& set CLIPPER_ASR_DEVICE=%CLIPPER_ASR_DEVICE%&& set CLIPPER_ASR_COMPUTE_TYPE=%CLIPPER_ASR_COMPUTE_TYPE%&& set CLIPPER_ASR_QUALITY=%CLIPPER_ASR_QUALITY%&& set CLIPPER_ASR_BEAM_SIZE=%CLIPPER_ASR_BEAM_SIZE%&& set CLIPPER_ASR_BEST_OF=%CLIPPER_ASR_BEST_OF%&& set CLIPPER_ASR_DENOISE=%CLIPPER_ASR_DENOISE%&& if defined CLIPPER_LOCAL_WHISPER_MODEL set CLIPPER_LOCAL_WHISPER_MODEL=%CLIPPER_LOCAL_WHISPER_MODEL%&& cd /d %~dp0\..&& python -m uvicorn clipper.web:app --host 127.0.0.1 --port 8787"
ping -n 4 127.0.0.1 >nul
echo === health ===
curl -s http://127.0.0.1:8787/api/health
echo.
endlocal
