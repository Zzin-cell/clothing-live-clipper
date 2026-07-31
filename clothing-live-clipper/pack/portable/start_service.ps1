# Start web service in background; auto-heal common failures
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
$pidFile = Join-Path $Tools "uvicorn.pid"

function Fail($msg) {
  $msg | Set-Content -Path $errStart -Encoding UTF8
  Write-Host $msg
  exit 1
}
function Log($m) { Write-Host $m }

function Free-Port8787 {
  try {
    Get-NetTCPConnection -LocalPort 8787 -ErrorAction SilentlyContinue |
      ForEach-Object {
        try { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } catch {}
      }
  } catch {}
  if (Test-Path $pidFile) {
    $old = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($old) { try { Stop-Process -Id ([int]$old) -Force -ErrorAction SilentlyContinue } catch {} }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
  }
  Start-Sleep -Milliseconds 400
}

function Resolve-Model {
  foreach ($n in @("whisper-medium", "whisper-small", "whisper-tiny")) {
    $p = Join-Path $ModelsRoot "$n\model.bin"
    if ((Test-Path $p) -and ((Get-Item $p).Length -gt 1MB)) {
      return (Join-Path $ModelsRoot $n)
    }
  }
  return $null
}

function Read-DeviceHint {
  $device = "cuda"
  $ctype = "float16"
  $hint = Join-Path $Tools "device_hint.txt"
  if (Test-Path $hint) {
    Get-Content $hint | ForEach-Object {
      if ($_ -match "^device=(.+)$") { $device = $Matches[1].Trim() }
      if ($_ -match "^compute_type=(.+)$") { $ctype = $Matches[1].Trim() }
    }
  }
  # live probe nvidia-smi
  $smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
  if (-not $smi) { $device = "cpu"; $ctype = "int8" }
  return @{ device = $device; compute = $ctype }
}

function Start-Uvicorn([hashtable]$dev) {
  $env:PATH = "$FfmpegBin;" + $env:PATH
  $env:PYTHONPATH = $Src
  $env:CLIPPER_LOCAL_WHISPER_MODEL = (Resolve-Model)
  $env:CLIPPER_ASR_DEVICE = $dev.device
  $env:CLIPPER_ASR_COMPUTE_TYPE = $dev.compute
  if (-not $env:CLIPPER_PLAYBACK_SPEED) { $env:CLIPPER_PLAYBACK_SPEED = "1.4" }

  # clear old logs
  "" | Set-Content $outLog -Encoding UTF8
  "" | Set-Content $errLog -Encoding UTF8

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
  if ($p) { $p.Id | Set-Content $pidFile -Encoding ASCII }
  return $p
}

function Wait-Ready([int]$Seconds = 20) {
  $n = [Math]::Max(4, [int]($Seconds * 2))
  for ($i = 0; $i -lt $n; $i++) {
    Start-Sleep -Milliseconds 500
    try {
      $r = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8787/" -TimeoutSec 2
      if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { return $true }
    } catch {}
    # process died?
    if (Test-Path $pidFile) {
      $id = Get-Content $pidFile
      if ($id -and -not (Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue)) { return $false }
    }
  }
  return $false
}

# -------- preflight / auto-heal --------
if (-not (Test-Path $PyExe) -or -not (Test-Path (Join-Path $FfmpegBin "ffmpeg.exe")) -or -not (Resolve-Model)) {
  Log "环境不完整，自动修复安装..."
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PackRoot "install_all.ps1")
  if ($LASTEXITCODE -ne 0) { Fail "自动修复安装失败，见 tools\logs\" }
}

# import heal
$imp = & $PyExe -c "import fastapi,uvicorn,faster_whisper; print('OK')" 2>&1
if ("$imp" -notmatch "OK") {
  Log "依赖损坏，自动重装依赖..."
  & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PackRoot "install_all.ps1")
  if ($LASTEXITCODE -ne 0) { Fail "依赖修复失败: $imp" }
}

Free-Port8787
$dev = Read-DeviceHint
Log "启动服务 http://127.0.0.1:8787/  device=$($dev.device) compute=$($dev.compute)"
Log "模型: $(Resolve-Model)"

$proc = Start-Uvicorn $dev
if (-not $proc) { Fail "无法创建 uvicorn 进程" }

if (-not (Wait-Ready 18)) {
  $tail = ""
  if (Test-Path $errLog) { $tail = (Get-Content $errLog -Raw -ErrorAction SilentlyContinue) }
  Log "首次启动未就绪，分析错误并自愈..."
  Log $tail

  $retryCpu = $false
  if ($tail -match "CUDA|cuda|cublas|cudnn|GPU|nvrtc|no kernel image") {
    Log "检测到 GPU/CUDA 问题 → 自动回退 CPU"
    $retryCpu = $true
  }
  if ($tail -match "Address already in use|10048|only one usage") {
    Log "端口占用 → 清理 8787 后重试"
    Free-Port8787
  }
  if ($tail -match "No module named|ModuleNotFoundError") {
    Log "缺包 → 重装依赖"
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PackRoot "install_all.ps1") | Out-Null
  }
  if ($tail -match "ffmpeg|WinError 2") {
    Log "ffmpeg 问题 → 重装 ffmpeg/环境"
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PackRoot "install_all.ps1") | Out-Null
  }

  Free-Port8787
  if ($retryCpu) {
    $dev = @{ device = "cpu"; compute = "int8" }
    "device=cpu`ncompute_type=int8`n" | Set-Content (Join-Path $Tools "device_hint.txt") -Encoding UTF8
  }
  $proc2 = Start-Uvicorn $dev
  if (-not $proc2) { Fail "自愈后仍无法启动进程。日志: $errLog" }
  if (-not (Wait-Ready 25)) {
    $tail2 = ""
    if (Test-Path $errLog) { $tail2 = Get-Content $errLog -Tail 40 -ErrorAction SilentlyContinue | Out-String }
    Fail "服务仍未就绪。已自动尝试修复。请把 tools\logs\uvicorn.err.log 发给维护者。`n$tail2"
  }
}

Start-Process "http://127.0.0.1:8787/"
Log "OK 服务已在后台运行，可关闭本窗口。"
Log "停止：双击 停止小面.bat"
exit 0
