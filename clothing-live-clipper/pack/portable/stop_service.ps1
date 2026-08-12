# Stop Xiaomian CapCut background service and release log file locks.
$ErrorActionPreference = "SilentlyContinue"
try { [Console]::OutputEncoding = [System.Text.Encoding]::UTF8 } catch {}

$PackRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$parentName = Split-Path -Leaf (Split-Path -Parent $PackRoot)
$selfName = Split-Path -Leaf $PackRoot
if ($selfName -eq "portable" -and $parentName -eq "pack") {
  $AppRoot = (Resolve-Path (Join-Path $PackRoot "..\..")).Path
} else {
  $AppRoot = (Resolve-Path (Join-Path $PackRoot "..")).Path
}
$Tools = Join-Path $AppRoot "tools"
$pidFile = Join-Path $Tools "uvicorn.pid"
$Logs = Join-Path $Tools "logs"

function Stop-PidSafe([int]$ProcessId) {
  if ($ProcessId -le 0) { return }
  try { Stop-Process -Id $ProcessId -Force -ErrorAction SilentlyContinue } catch {}
}

# 1) pid file
if (Test-Path $pidFile) {
  $old = Get-Content $pidFile -ErrorAction SilentlyContinue
  if ($old) {
    try { Stop-PidSafe ([int]$old) } catch {}
  }
  Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
}

# 2) anything listening on 8787
try {
  Get-NetTCPConnection -LocalPort 8787 -ErrorAction SilentlyContinue | ForEach-Object {
    Stop-PidSafe ([int]$_.OwningProcess)
  }
} catch {}

# 3) any uvicorn/python started from this package path (releases uvicorn.err.log locks)
try {
  $root = [regex]::Escape($AppRoot)
  Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
    $_.CommandLine -and (
      $_.CommandLine -like "*uvicorn*clipper.web*" -or
      $_.CommandLine -match $root
    )
  } | ForEach-Object {
    if ($_.Name -match "python|uvicorn|powershell") {
      Write-Host ("stop pid=" + $_.ProcessId + " " + $_.Name)
      Stop-PidSafe ([int]$_.ProcessId)
    }
  }
} catch {}

Start-Sleep -Milliseconds 500

# 4) if logs still locked, one more sweep
foreach ($name in @("uvicorn.err.log", "uvicorn.out.log")) {
  $lp = Join-Path $Logs $name
  if (-not (Test-Path $lp)) { continue }
  try {
    $fs = [System.IO.File]::Open($lp, "Open", "ReadWrite", "None")
    $fs.Close()
  } catch {
    Write-Host ("log still busy: " + $name + " ; retry kill")
    try {
      Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.CommandLine -and ($_.CommandLine -like "*uvicorn*clipper.web*")
      } | ForEach-Object { Stop-PidSafe ([int]$_.ProcessId) }
    } catch {}
    Start-Sleep -Milliseconds 400
  }
}

Write-Host "stopped"
exit 0
