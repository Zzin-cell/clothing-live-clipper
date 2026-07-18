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
echo === start uvicorn ===
start "clipper-web-8787" cmd /k "set PATH=%FFBIN%;%PATH%&& set PYTHONPATH=src&& cd /d %~dp0\..&& python -m uvicorn clipper.web:app --host 127.0.0.1 --port 8787"
ping -n 4 127.0.0.1 >nul
echo === health ===
curl -s http://127.0.0.1:8787/api/health
echo.
endlocal
