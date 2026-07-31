# Start web service in background (no need to keep PowerShell window open)
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$PackRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$parentName = Split-Path -Leaf (Split-Path -Parent $PackRoot)
$selfName = Split-Path -Leaf $PackRoot
if ($selfName -eq "portable" -and $parentName -eq "pack") {
  $AppRoot = (Resolve-Path (Join-Path $PackRoot "..\..")).Path
} else {
  $AppRoot = (Resolve-Path (Join-Path $PackRoot "..")).Path
}
$Tools = Join-Path $AppRoot "tools"
$Logs = Join-Path $Tools "logs"
$FfmpegBin = Join-Path $Tools "ffmpeg\bin"
$ModelsRoot = Join-Path $AppRoot "models"
$PyExe = Join-Path $AppRoot ".venv\Scripts\python.exe"
$Src = Join-Path $AppRoot "clothing-live-clipper\src"
if (-not (Test-Path $Src)) { $Src = Join-Path $AppRoot "src" }
$CodeRoot = Split-Path -Parent $Src

New-Item -ItemType Directory -Force -Path $Logs | Out-Null
$outLog = Join-Path $Logs "uvicorn.out.log"
$errLog = Join-Path $Logs "uvicorn.err.log"
$errStart = Join-Path $Logs "start_error.txt"

function Fail($msg) {
  $msg | Set-Content -Path $errStart -Encoding UTF8
  Write-Host $msg
  exit 1
}

if (-not (Test-Path $PyExe)) {
  Fail "未找到虚拟环境，请先双击运行「首次安装配置.bat」"
}
if (-not (Test-Path (Join-Path $FfmpegBin "ffmpeg.exe"))) {
  Fail "未找到 ffmpeg，请先双击运行「首次安装配置.bat」"
}

# model preference: medium > small > tiny
$model = $null
foreach ($n in @("whisper-medium", "whisper-small", "whisper-tiny")) {
  $p = Join-Path $ModelsRoot "$n\model.bin"
  if (Test-Path $p) { $model = Join-Path $ModelsRoot $n; break }
}
if (-not $model) {
  Fail "未找到 Whisper 模型，请先运行「首次安装配置.bat」下载模型"
}

# free port 8787 if stale
try {
  Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue |
    ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }
} catch {}

# also kill previous uvicorn for this package if pid file exists
$pidFile = Join-Path $Tools "uvicorn.pid"
if (Test-Path $pidFile) {
  $old = Get-Content $pidFile -ErrorAction SilentlyContinue
  if ($old) {
    try { Stop-Process -Id ([int]$old) -Force -ErrorAction SilentlyContinue } catch {}
  }
}

$env:PATH = "$FfmpegBin;" + $env:PATH
$env:PYTHONPATH = $Src
$env:CLIPPER_LOCAL_WHISPER_MODEL = $model
# auto device: try cuda; faster-whisper will error if unavailable — default float32 cpu friendly
if (-not $env:CLIPPER_ASR_DEVICE) {
  $env:CLIPPER_ASR_DEVICE = "cuda"
}
if (-not $env:CLIPPER_ASR_COMPUTE_TYPE) {
  $env:CLIPPER_ASR_COMPUTE_TYPE = "float16"
}
if (-not $env:CLIPPER_PLAYBACK_SPEED) {
  $env:CLIPPER_PLAYBACK_SPEED = "1.4"
}

# fallback: if no CUDA, restart with cpu? check via python once
try {
  $cudaCheck = & $PyExe -c "import torch; print(torch.cuda.is_available())" 2>$null
  if ("$cudaCheck".Trim() -ne "True") {
    # torch may not be installed; leave cuda, faster-whisper often works with cuda+ctranslate2
  }
} catch {}

Write-Host "启动服务 http://127.0.0.1:8787/ （后台运行，可关闭本窗口）"
Write-Host "模型: $model"

$argList = @(
  "-m", "uvicorn", "clipper.web:app",
  "--host", "127.0.0.1",
  "--port", "8787",
  "--log-level", "info"
)

$p = Start-Process -FilePath $PyExe `
  -ArgumentList $argList `
  -WorkingDirectory $CodeRoot `
  -WindowStyle Hidden `
  -RedirectStandardOutput $outLog `
  -RedirectStandardError $errLog `
  -PassThru

if (-not $p) { Fail "无法启动 uvicorn 进程" }
$p.Id | Set-Content -Path $pidFile -Encoding ASCII

# wait ready
$ready = $false
for ($i = 0; $i -lt 30; $i++) {
  Start-Sleep -Milliseconds 500
  try {
    $r = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8787/" -TimeoutSec 2
    if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { $ready = $true; break }
  } catch {}
}

if (-not $ready) {
  $tail = ""
  if (Test-Path $errLog) { $tail = Get-Content $errLog -Tail 30 -ErrorAction SilentlyContinue | Out-String }
  Fail "服务未能在 15 秒内就绪。请看日志: $errLog`n$tail"
}

# open browser once
Start-Process "http://127.0.0.1:8787/"
Write-Host "OK 服务已启动。可关闭本黑窗口，不影响服务。"
Write-Host "停止服务请运行：停止小面.bat"
exit 0
