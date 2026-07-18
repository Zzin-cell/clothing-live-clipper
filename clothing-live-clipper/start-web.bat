@echo off
cd /d "%~dp0"
set "PATH=%LOCALAPPDATA%\ffmpeg\bin;%PATH%"
set PYTHONPATH=src
echo Starting clothing-live-clipper web at http://127.0.0.1:8787/
where ffmpeg >nul 2>&1 && (
  echo ffmpeg: OK
) || (
  echo ffmpeg: NOT FOUND - render will be skipped. Run scripts\setup_imageio_ffmpeg.py
)
echo Keep this window open. Press Ctrl+C to stop.
echo.
python -m uvicorn clipper.web:app --host 127.0.0.1 --port 8787
pause
