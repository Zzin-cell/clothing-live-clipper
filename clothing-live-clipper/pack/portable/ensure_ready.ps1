# Auto install / repair if environment is not healthy
$ErrorActionPreference = "Continue"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$PackRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$parentName = Split-Path -Leaf (Split-Path -Parent $PackRoot)
$selfName = Split-Path -Leaf $PackRoot
if ($selfName -eq "portable" -and $parentName -eq "pack") {
  $AppRoot = (Resolve-Path (Join-Path $PackRoot "..\..")).Path
} else {
  $AppRoot = (Resolve-Path (Join-Path $PackRoot "..")).Path
}

function Test-EnvHealthy {
  $py = Join-Path $AppRoot ".venv\Scripts\python.exe"
  $ff = Join-Path $AppRoot "tools\ffmpeg\bin\ffmpeg.exe"
  $modelOk = $false
  foreach ($n in @("whisper-medium", "whisper-small", "whisper-tiny")) {
    $b = Join-Path $AppRoot "models\$n\model.bin"
    if ((Test-Path $b) -and ((Get-Item $b).Length -gt 1MB)) {
      $modelOk = $true
      break
    }
  }
  if (-not (Test-Path $py)) { return $false }
  if (-not (Test-Path $ff)) { return $false }
  if (-not $modelOk) { return $false }
  try {
    $r = & $py -c "import fastapi,uvicorn,faster_whisper; print('OK')" 2>&1
    if (("$r") -notmatch "OK") { return $false }
  } catch {
    return $false
  }
  return $true
}

function Ensure-DesktopLink {
  try {
    $desk = [Environment]::GetFolderPath("Desktop")
    $lnk = Join-Path $desk "XiaomianCapCut.lnk"
    $lnkCn = Join-Path $desk "小面CapCut.lnk"
    if ((Test-Path $lnk) -or (Test-Path $lnkCn)) { return }
    $startBat = Join-Path $AppRoot "启动小面.bat"
    if (-not (Test-Path $startBat)) {
      $startBat = Join-Path $PackRoot "启动小面.bat"
    }
    if (-not (Test-Path $startBat)) { return }
    $w = New-Object -ComObject WScript.Shell
    $target = $lnkCn
    $sc = $w.CreateShortcut($target)
    $sc.TargetPath = $startBat
    $sc.WorkingDirectory = $AppRoot
    $sc.WindowStyle = 7
    $sc.Description = "Xiaomian CapCut"
    $sc.Save()
    Write-Host "Desktop shortcut created."
  } catch {
    Write-Host "WARN: desktop shortcut skipped"
  }
}

$need = -not (Test-EnvHealthy)
if ($need) {
  Write-Host "============================================"
  Write-Host " Environment not ready. Auto install/repair..."
  Write-Host " Keep network online. This may take minutes."
  Write-Host "============================================"
  Write-Host ""
  $installer = Join-Path $PackRoot "install_all.ps1"
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
  if ($LASTEXITCODE -ne 0) {
    Write-Host "Install failed once. Retrying once more..."
    Start-Sleep -Seconds 2
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File $installer
    if ($LASTEXITCODE -ne 0) {
      Write-Host "Auto install/repair failed. See tools\logs\"
      exit 1
    }
  }
  if (-not (Test-EnvHealthy)) {
    Write-Host "Still unhealthy after install. See tools\logs\"
    exit 1
  }
  Write-Host "Auto install/repair done."
  Write-Host ""
}

Ensure-DesktopLink
exit 0
