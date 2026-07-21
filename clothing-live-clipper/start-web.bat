@echo off
cd /d "%~dp0"
set "PATH=%LOCALAPPDATA%\ffmpeg\bin;%~dp0tools\ffmpeg\bin;%PATH%"
set "PYTHONPATH=%~dp0src"
rem Prefer GPU + small model (RTX 4060). Fallback handled in code if CUDA fails.
if not defined CLIPPER_ASR_DEVICE set "CLIPPER_ASR_DEVICE=cuda"
if not defined CLIPPER_ASR_COMPUTE_TYPE set "CLIPPER_ASR_COMPUTE_TYPE=float16"
if not defined CLIPPER_ASR_QUALITY set "CLIPPER_ASR_QUALITY=high"
if not defined CLIPPER_ASR_BEAM_SIZE set "CLIPPER_ASR_BEAM_SIZE=3"
if not defined CLIPPER_ASR_BEST_OF set "CLIPPER_ASR_BEST_OF=3"
if exist "%~dp0models\whisper-small\model.bin" (
  set "CLIPPER_LOCAL_WHISPER_MODEL=%~dp0models\whisper-small"
)
if exist "%USERPROFILE%\AppData\grok\models\whisper-small\model.bin" (
  if not defined CLIPPER_LOCAL_WHISPER_MODEL set "CLIPPER_LOCAL_WHISPER_MODEL=%USERPROFILE%\AppData\grok\models\whisper-small"
)
if exist "%~dp0models\whisper-tiny\model.bin" (
  if not defined CLIPPER_LOCAL_WHISPER_MODEL set "CLIPPER_LOCAL_WHISPER_MODEL=%~dp0models\whisper-tiny"
)
echo ============================================
echo  Clothing Live Clipper Web
echo  http://127.0.0.1:8787/
echo  Upload video = auto local ASR + cut
echo  No Agent chat required
echo ============================================
where ffmpeg >nul 2>&1 && (
  echo ffmpeg: OK
) || (
  echo ffmpeg: NOT FOUND
)
echo.
echo Keep this window open. Press Ctrl+C to stop.
echo.
python -m uvicorn clipper.web:app --host 127.0.0.1 --port 8787
pause
