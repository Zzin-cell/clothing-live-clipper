# Auto install / repair if not healthy (called by 启动小面)
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

function Healthy {
  $py = Join-Path $AppRoot ".venv\Scripts\python.exe"
  $ff = Join-Path $AppRoot "tools\ffmpeg\bin\ffmpeg.exe"
  $modelOk = $false
  foreach ($n in @("whisper-medium", "whisper-small", "whisper-tiny")) {
    $b = Join-Path $AppRoot "models\$n\model.bin"
    if ((Test-Path $b) -and ((Get-Item $b).Length -gt 1MB)) { $modelOk = $true; break }
  }
  if (-not (Test-Path $py)) { return $false }
  if (-not (Test-Path $ff)) { return $false }
  if (-not $modelOk) { return $false }
  try {
    $r = & $py -c "import fastapi,uvicorn,faster_whisper; print('OK')" 2>&1
    if ("$r" -notmatch "OK") { return $false }
  } catch { return $false }
  return $true
}

$need = -not (Healthy)
if ($need) {
  Write-Host "============================================"
  Write-Host " 检测到环境未就绪或不完整"
  Write-Host " 正在自动安装/排查/修复（请联网）..."
  Write-Host "============================================"
  Write-Host ""
  $installer = Join-Path $PackRoot "install_all.ps1"
  & powershell -NoProfile -ExecutionPolicy Bypass -File $installer
  if ($LASTEXITCODE -ne 0) {
    Write-Host "自动安装/修复失败。"
    Write-Host "请打开 tools\logs\ 查看最新 install_*.log 与 last_repair.txt"
    # one more blind repair attempt after short wait
    Start-Sleep -Seconds 2
    Write-Host "再自动重试一次修复..."
    & powershell -NoProfile -ExecutionPolicy Bypass -File $installer
    if ($LASTEXITCODE -ne 0) { exit 1 }
  }
  if (-not (Healthy)) {
    Write-Host "修复后仍不健康，请查看日志。"
    exit 1
  }
  Write-Host "自动安装/修复完成。"
  Write-Host ""
} else {
  # light heal: desktop shortcut missing
  $desk = [Environment]::GetFolderPath("Desktop")
  $lnk = Join-Path $desk "小面CapCut.lnk"
  if (-not (Test-Path $lnk)) {
    try {
      $startBat = Join-Path $AppRoot "启动小面.bat"
      if (-not (Test-Path $startBat)) { $startBat = Join-Path $PackRoot "启动小面.bat" }
      $w = New-Object -ComObject WScript.Shell
      $sc = $w.CreateShortcut($lnk)
      $sc.TargetPath = $startBat
      $sc.WorkingDirectory = $AppRoot
      $sc.WindowStyle = 7
      $sc.Description = "小面 CapCut"
      $sc.Save()
      Write-Host "已补建桌面快捷方式"
    } catch {}
  }
}

exit 0
