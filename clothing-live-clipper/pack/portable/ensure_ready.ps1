# Auto install if not ready (called by 启动小面)
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

$marker = Join-Path $AppRoot "tools\install_ok.txt"
$py = Join-Path $AppRoot ".venv\Scripts\python.exe"
$ff = Join-Path $AppRoot "tools\ffmpeg\bin\ffmpeg.exe"
$modelOk = $false
foreach ($n in @("whisper-medium", "whisper-small", "whisper-tiny")) {
  if (Test-Path (Join-Path $AppRoot "models\$n\model.bin")) { $modelOk = $true; break }
}

$need = $false
if (-not (Test-Path $marker)) { $need = $true }
if (-not (Test-Path $py)) { $need = $true }
if (-not (Test-Path $ff)) { $need = $true }
if (-not $modelOk) { $need = $true }

if ($need) {
  Write-Host "============================================"
  Write-Host " 首次使用：正在自动安装配置（仅第一次）..."
  Write-Host " 请保持网络畅通，可能需要几分钟"
  Write-Host "============================================"
  Write-Host ""
  $installer = Join-Path $PackRoot "install_all.ps1"
  & powershell -NoProfile -ExecutionPolicy Bypass -File $installer
  if ($LASTEXITCODE -ne 0) {
    Write-Host "自动安装失败。请查看 tools\logs 后重试。"
    exit 1
  }
  Write-Host ""
  Write-Host "自动安装完成，继续启动服务..."
  Write-Host ""
}

exit 0
