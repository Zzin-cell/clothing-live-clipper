@echo off
cd /d "%~dp0.."
set "PATH=%LOCALAPPDATA%\ffmpeg\bin;%~dp0..\tools\ffmpeg\bin;%PATH%"
set "PYTHONPATH=%CD%\src"
if not defined CLIPPER_ASR_DEVICE set "CLIPPER_ASR_DEVICE=cuda"
if not defined CLIPPER_ASR_COMPUTE_TYPE set "CLIPPER_ASR_COMPUTE_TYPE=float16"
if exist "%USERPROFILE%\AppData\grok\models\whisper-medium\model.bin" (
  set "CLIPPER_LOCAL_WHISPER_MODEL=%USERPROFILE%\AppData\grok\models\whisper-medium"
)
echo Starting Web on http://127.0.0.1:8787/
python -m uvicorn clipper.web:app --host 127.0.0.1 --port 8787
pause
