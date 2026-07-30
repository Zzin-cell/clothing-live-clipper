$ErrorActionPreference = "Continue"
$root = "C:\Users\MR\AppData\grok\clothing-live-clipper"
Set-Location $root
$env:PYTHONPATH = Join-Path $root "src"
$env:PATH = "$env:LOCALAPPDATA\ffmpeg\bin;" + $env:PATH
if (-not $env:CLIPPER_ASR_DEVICE) { $env:CLIPPER_ASR_DEVICE = "cuda" }
if (-not $env:CLIPPER_ASR_COMPUTE_TYPE) { $env:CLIPPER_ASR_COMPUTE_TYPE = "float16" }
$whisper = Join-Path $env:USERPROFILE "AppData\grok\models\whisper-medium\model.bin"
if (Test-Path $whisper) {
  $env:CLIPPER_LOCAL_WHISPER_MODEL = Join-Path $env:USERPROFILE "AppData\grok\models\whisper-medium"
}

# kill existing 8787 listeners
Get-NetTCPConnection -LocalPort 8787 -State Listen -ErrorAction SilentlyContinue |
  ForEach-Object { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue }

$arg = "-NoProfile -Command `"cd '$root'; `$env:PYTHONPATH='$($env:PYTHONPATH)'; `$env:PATH='$($env:PATH)'; python -m uvicorn clipper.web:app --host 127.0.0.1 --port 8787`""
Start-Process -FilePath "powershell.exe" -ArgumentList $arg -WorkingDirectory $root
Start-Sleep -Seconds 5
try {
  $r = Invoke-WebRequest -UseBasicParsing "http://127.0.0.1:8787/" -TimeoutSec 8
  Write-Output ("HTTP " + $r.StatusCode)
} catch {
  Write-Output ("ERR " + $_.Exception.Message)
}
Get-NetTCPConnection -LocalPort 8787 -ErrorAction SilentlyContinue |
  Select-Object OwningProcess, State | Format-Table -AutoSize
Start-Process "http://127.0.0.1:8787/"
