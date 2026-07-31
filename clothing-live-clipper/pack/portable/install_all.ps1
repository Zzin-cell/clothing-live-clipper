# First-time + repair installer for Xiaomian CapCut portable package
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
$Tools = Join-Path $AppRoot "tools"
$Logs = Join-Path $Tools "logs"
$FfmpegBin = Join-Path $Tools "ffmpeg\bin"
$ModelsRoot = Join-Path $AppRoot "models"
$PyVenv = Join-Path $AppRoot ".venv"
$PyExe = Join-Path $PyVenv "Scripts\python.exe"
$Req = Join-Path $AppRoot "clothing-live-clipper\requirements.txt"
if (-not (Test-Path $Req)) { $Req = Join-Path $AppRoot "requirements.txt" }

New-Item -ItemType Directory -Force -Path $Logs, $FfmpegBin, $ModelsRoot | Out-Null
$logFile = Join-Path $Logs ("install_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))
$fixFile = Join-Path $Logs "last_repair.txt"

function Log([string]$m) {
  $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $m
  Write-Host $line
  Add-Content -Path $logFile -Value $line -Encoding UTF8
}
function SoftFail([string]$m) { Log ("WARN: " + $m) }
function HardFail([string]$m) {
  Log ("ERROR: " + $m)
  $txt = @(
    ("time=" + [DateTime]::Now.ToString("s")),
    ("error=" + $m),
    ("log=" + $logFile),
    "hint=Install Python 3.11+ with PATH, keep network online, allow antivirus"
  ) -join "`n"
  Set-Content -Path $fixFile -Value $txt -Encoding UTF8
  exit 1
}

function Download-File {
  param([string[]]$Urls, [string]$Dest)
  $tmp = "$Dest.part"
  foreach ($url in $Urls) {
    try {
      Log ("  GET " + $url)
      $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
      if ($curl) {
        & curl.exe -L --fail --retry 5 --retry-delay 2 --connect-timeout 30 --max-time 3600 -o $tmp $url
        if (($LASTEXITCODE -eq 0) -and (Test-Path $tmp) -and ((Get-Item $tmp).Length -gt 1000)) {
          Move-Item -Force $tmp $Dest
          return $true
        }
      } else {
        Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
        if ((Test-Path $tmp) -and ((Get-Item $tmp).Length -gt 1000)) {
          Move-Item -Force $tmp $Dest
          return $true
        }
      }
    } catch {
      SoftFail ("download fail: " + $_.Exception.Message)
    } finally {
      if (Test-Path $tmp) { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    }
  }
  return $false
}

function Test-PythonExe([string]$exe) {
  if (-not $exe) { return $null }
  if (-not (Test-Path $exe)) { return $null }
  try {
    $out = & $exe -c "import sys; v=sys.version_info; print('%d.%d' % (v.major, v.minor)); raise SystemExit(0 if v.major==3 and v.minor>=10 else 2)" 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) { return $out.ToString().Trim() }
  } catch {}
  return $null
}

function Get-PythonCommand {
  # 1) py launcher list (-0p)
  $pyLauncher = Get-Command py.exe -ErrorAction SilentlyContinue
  if ($pyLauncher) {
    try {
      $lines = & py.exe -0p 2>$null
      foreach ($line in $lines) {
        if ($line -match "([A-Za-z]:\\[^\s]+python\.exe)") {
          $p = $Matches[1]
          $ver = Test-PythonExe $p
          if ($ver) {
            Log ("Found Python " + $ver + " via py -0p: " + $p)
            return @{ Kind = "path"; Cmd = $p }
          }
        }
      }
    } catch {}
    foreach ($arg in @("-3.12", "-3.11", "-3.10", "-3")) {
      try {
        $out = & py.exe $arg -c "import sys; v=sys.version_info; print('%d.%d' % (v.major, v.minor)); import sys as s; s.exit(0 if v.major==3 and v.minor>=10 else 2)" 2>$null
        if ($LASTEXITCODE -eq 0 -and $out) {
          $exe = & py.exe $arg -c "import sys; print(sys.executable)" 2>$null
          if ($exe -and (Test-Path "$exe")) {
            Log ("Found Python " + $out.ToString().Trim() + " via py " + $arg)
            return @{ Kind = "path"; Cmd = "$exe".Trim() }
          }
          return @{ Kind = "py"; Arg = $arg }
        }
      } catch {}
    }
  }
  # 2) PATH python
  foreach ($name in @("python.exe", "python3.exe")) {
    $cmd = Get-Command $name -ErrorAction SilentlyContinue
    if ($cmd -and $cmd.Source -and (Test-Path $cmd.Source)) {
      # skip WindowsApps stub if it is zero-size or redirects poorly
      $ver = Test-PythonExe $cmd.Source
      if ($ver) {
        Log ("Found Python " + $ver + " via PATH: " + $cmd.Source)
        return @{ Kind = "path"; Cmd = $cmd.Source }
      }
    }
  }
  # 3) common install locations + WindowsApps real package
  $paths = @()
  $paths += @(
    "$env:LOCALAPPDATA\Programs\Python\Python313\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
    "C:\Python313\python.exe",
    "C:\Python312\python.exe",
    "C:\Python311\python.exe"
  )
  $wa = Join-Path $env:LOCALAPPDATA "Microsoft\WindowsApps"
  if (Test-Path $wa) {
    Get-ChildItem -Path $wa -Filter "PythonSoftwareFoundation.Python.*" -Directory -ErrorAction SilentlyContinue | ForEach-Object {
      $p = Join-Path $_.FullName "python.exe"
      if (Test-Path $p) { $paths += $p }
    }
    # nested package path often used by Store Python
    Get-ChildItem -Path $wa -Recurse -Filter python.exe -ErrorAction SilentlyContinue | Select-Object -First 8 | ForEach-Object {
      $paths += $_.FullName
    }
  }
  # Program Files WindowsApps package (if accessible)
  $pfwa = "C:\Program Files\WindowsApps"
  if (Test-Path $pfwa) {
    try {
      Get-ChildItem -Path $pfwa -Filter "PythonSoftwareFoundation.Python.*" -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        $p = Join-Path $_.FullName "python.exe"
        if (Test-Path $p) { $paths += $p }
      }
    } catch {}
  }
  foreach ($p in $paths) {
    $ver = Test-PythonExe $p
    if ($ver) {
      Log ("Found Python " + $ver + " path: " + $p)
      return @{ Kind = "path"; Cmd = $p }
    }
  }
  return $null
}

function Ensure-Venv($pyInfo) {
  $needRebuild = $false
  if (-not (Test-Path $PyExe)) { $needRebuild = $true }
  else {
    try {
      & $PyExe -c "import sys; print(sys.version)" | Out-Null
      if ($LASTEXITCODE -ne 0) { $needRebuild = $true }
    } catch { $needRebuild = $true }
  }
  if ($needRebuild) {
    Log "Create/repair venv..."
    if (Test-Path $PyVenv) {
      SoftFail "Removing broken venv"
      Remove-Item -Recurse -Force $PyVenv -ErrorAction SilentlyContinue
    }
    if ($pyInfo.Kind -eq "path") {
      & $pyInfo.Cmd -m venv $PyVenv
    } elseif ($pyInfo.Kind -eq "py") {
      & py.exe $pyInfo.Arg -m venv $PyVenv
    } else {
      & python.exe -m venv $PyVenv
    }
  }
  if (-not (Test-Path $PyExe)) { return $false }
  & $PyExe -m ensurepip --upgrade 2>&1 | Out-Null
  return $true
}

function Install-PipPackages {
  param([string[]]$PackageArgs)
  $mirrorSets = @(
    @(),
    @("-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "--trusted-host", "pypi.tuna.tsinghua.edu.cn"),
    @("-i", "https://mirrors.aliyun.com/pypi/simple/", "--trusted-host", "mirrors.aliyun.com"),
    @("-i", "https://pypi.org/simple")
  )
  foreach ($m in $mirrorSets) {
    $all = @("-m", "pip", "install", "--disable-pip-version-check") + $m + $PackageArgs
    Log ("pip " + ($PackageArgs -join " ") + " " + ($m -join " "))
    & $PyExe @all
    if ($LASTEXITCODE -eq 0) { return $true }
    SoftFail ("pip failed exit=" + $LASTEXITCODE + ", try next mirror")
  }
  return $false
}

function Ensure-Deps {
  Log "Upgrade pip tooling..."
  [void](Install-PipPackages -PackageArgs @("-U", "pip", "setuptools", "wheel"))
  if (Test-Path $Req) {
    Log "Install requirements.txt"
    [void](Install-PipPackages -PackageArgs @("-r", $Req))
  }
  $core = @(
    "faster-whisper>=1.0",
    "uvicorn[standard]>=0.27",
    "fastapi>=0.110",
    "python-multipart",
    "pydantic>=2.0",
    "python-dotenv",
    "httpx"
  )
  if (-not (Install-PipPackages -PackageArgs $core)) {
    SoftFail "batch install failed, try one-by-one"
    foreach ($p in $core) {
      if (-not (Install-PipPackages -PackageArgs @($p))) {
        SoftFail ("package failed: " + $p)
      }
    }
  }
  $check = & $PyExe -c "import fastapi,uvicorn,pydantic; import faster_whisper; print('IMPORT_OK')" 2>&1
  if (("$check") -notmatch "IMPORT_OK") {
    SoftFail ("import check failed: " + $check)
    [void](Install-PipPackages -PackageArgs (@("--force-reinstall", "--no-cache-dir") + $core))
    $check2 = & $PyExe -c "import fastapi,uvicorn; import faster_whisper; print('IMPORT_OK')" 2>&1
    if (("$check2") -notmatch "IMPORT_OK") { return $false }
  }
  Log "Optional CUDA wheels (ignore fail if no GPU)"
  [void](Install-PipPackages -PackageArgs @("nvidia-cublas-cu12", "nvidia-cudnn-cu12"))
  return $true
}

function Ensure-Ffmpeg {
  $ff = Join-Path $FfmpegBin "ffmpeg.exe"
  if (Test-Path $ff) {
    try {
      $v = & $ff -version 2>&1 | Select-Object -First 1
      Log ("ffmpeg ok: " + $v)
      return $true
    } catch {
      SoftFail "ffmpeg broken, redownload"
      Remove-Item $ff -Force -ErrorAction SilentlyContinue
    }
  }
  $local = Join-Path $env:LOCALAPPDATA "ffmpeg\bin\ffmpeg.exe"
  if (Test-Path $local) {
    Log "Copy local ffmpeg"
    Copy-Item -Force $local $ff
    $lp = Join-Path $env:LOCALAPPDATA "ffmpeg\bin\ffprobe.exe"
    if (Test-Path $lp) { Copy-Item -Force $lp (Join-Path $FfmpegBin "ffprobe.exe") }
    return $true
  }
  $zip = Join-Path $env:TEMP "ffmpeg-essentials-xm.zip"
  $urls = @(
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/GyanD/codexffmpeg/releases/download/7.1.1/ffmpeg-7.1.1-essentials_build.zip",
    "https://mirror.ghproxy.com/https://github.com/GyanD/codexffmpeg/releases/download/7.1.1/ffmpeg-7.1.1-essentials_build.zip"
  )
  if (-not (Download-File -Urls $urls -Dest $zip)) { return $false }
  $extract = Join-Path $Tools "ffmpeg\_extract"
  if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
  try {
    Expand-Archive -Path $zip -DestinationPath $extract -Force
  } catch {
    SoftFail "expand failed, retry"
    Start-Sleep 1
    Expand-Archive -Path $zip -DestinationPath $extract -Force
  }
  $found = Get-ChildItem -Path $extract -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $found) { return $false }
  Copy-Item -Force $found.FullName $ff
  $probe = Join-Path $found.Directory.FullName "ffprobe.exe"
  if (Test-Path $probe) { Copy-Item -Force $probe (Join-Path $FfmpegBin "ffprobe.exe") }
  Log "ffmpeg installed"
  return (Test-Path $ff)
}

function Ensure-Model {
  foreach ($n in @("whisper-medium", "whisper-small", "whisper-tiny")) {
    $bin = Join-Path $ModelsRoot "$n\model.bin"
    if ((Test-Path $bin) -and ((Get-Item $bin).Length -gt 10MB)) {
      Log ("model exists: " + $n)
      return $true
    }
  }
  $pairs = @(
    @{ Name = "whisper-small"; Repo = "Systran/faster-whisper-small" },
    @{ Name = "whisper-tiny"; Repo = "Systran/faster-whisper-tiny" }
  )
  foreach ($pair in $pairs) {
    $modelDir = Join-Path $ModelsRoot $pair.Name
    New-Item -ItemType Directory -Force -Path $modelDir | Out-Null
    $files = @("config.json", "tokenizer.json", "vocabulary.txt", "model.bin")
    $mirrors = @("https://hf-mirror.com", "https://huggingface.co")
    $allOk = $true
    foreach ($f in $files) {
      $dest = Join-Path $modelDir $f
      if ((Test-Path $dest) -and ((Get-Item $dest).Length -gt 1000)) { continue }
      $urls = @()
      foreach ($m in $mirrors) { $urls += ($m + "/" + $pair.Repo + "/resolve/main/" + $f) }
      $ok = $false
      for ($t = 1; $t -le 3 -and -not $ok; $t++) {
        Log ("  download " + $f + " try " + $t)
        $ok = Download-File -Urls $urls -Dest $dest
        if (-not $ok) { Start-Sleep -Seconds (2 * $t) }
      }
      if (-not $ok) { $allOk = $false; break }
    }
    if ($allOk -and (Test-Path (Join-Path $modelDir "model.bin"))) {
      Log ("model ready: " + $pair.Name)
      return $true
    }
    SoftFail ("model family failed: " + $pair.Name)
  }
  return $false
}

function Ensure-DesktopShortcut {
  try {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $startBat = Join-Path $AppRoot "启动小面.bat"
    if (-not (Test-Path $startBat)) { $startBat = Join-Path $PackRoot "启动小面.bat" }
    if (-not (Test-Path $startBat)) { return }
    $lnkPath = Join-Path $desktop "小面CapCut.lnk"
    $w = New-Object -ComObject WScript.Shell
    $sc = $w.CreateShortcut($lnkPath)
    $sc.TargetPath = $startBat
    $sc.WorkingDirectory = $AppRoot
    $sc.WindowStyle = 7
    $sc.Description = "Xiaomian CapCut"
    $sc.Save()
    Log ("Desktop shortcut: " + $lnkPath)
  } catch {
    SoftFail ("Desktop shortcut failed: " + $_.Exception.Message)
  }
}

function Write-DeviceHint {
  $hint = Join-Path $Tools "device_hint.txt"
  $device = "cuda"
  $ctype = "float16"
  $smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
  if (-not $smi) {
    $device = "cpu"
    $ctype = "int8"
    Log "No nvidia-smi, prefer CPU ASR"
  } else {
    $q = & nvidia-smi.exe -L 2>&1
    if ($LASTEXITCODE -ne 0) {
      $device = "cpu"
      $ctype = "int8"
      Log "nvidia-smi failed, prefer CPU ASR"
    } else {
      Log ("GPU: " + (($q | Select-Object -First 1) | Out-String).Trim())
    }
  }
  Set-Content -Path $hint -Value ("device=" + $device + "`ncompute_type=" + $ctype + "`n") -Encoding UTF8
}

# ---- main ----
Log ("AppRoot=" + $AppRoot)
Log "Auto diagnose and repair install..."

$pyInfo = Get-PythonCommand
if (-not $pyInfo) {
  HardFail "Python 3.10+ not found. Install Python and check Add to PATH. https://www.python.org/downloads/windows/"
}

if (-not (Ensure-Venv $pyInfo)) {
  SoftFail "venv create failed once, retry"
  Start-Sleep 1
  if (-not (Ensure-Venv $pyInfo)) { HardFail "venv create failed" }
}
Log ("venv OK: " + $PyExe)

if (-not (Ensure-Deps)) {
  SoftFail "deps failed, rebuild venv and retry"
  Remove-Item -Recurse -Force $PyVenv -ErrorAction SilentlyContinue
  if (-not (Ensure-Venv $pyInfo)) { HardFail "rebuild venv failed" }
  if (-not (Ensure-Deps)) { HardFail "Python deps install failed after mirror retries" }
}
Log "deps OK"

if (-not (Ensure-Ffmpeg)) {
  SoftFail "ffmpeg failed once, retry"
  if (-not (Ensure-Ffmpeg)) { HardFail "ffmpeg install failed" }
}
Log "ffmpeg OK"

if (-not (Ensure-Model)) {
  SoftFail "model download failed once, retry"
  if (-not (Ensure-Model)) { HardFail "whisper model download failed" }
}
Log "model OK"

Ensure-DesktopShortcut
Write-DeviceHint

$health = & $PyExe -c "import fastapi,uvicorn,faster_whisper; print('OK')" 2>&1
if (("$health") -notmatch "OK") { HardFail ("final health check failed: " + $health) }
if (-not (Test-Path (Join-Path $FfmpegBin "ffmpeg.exe"))) { HardFail "final check: missing ffmpeg" }

$marker = Join-Path $Tools "install_ok.txt"
$markTxt = @(
  ("installed_at=" + [DateTime]::Now.ToString("s")),
  ("app_root=" + $AppRoot),
  ("python=" + $PyExe),
  ("ffmpeg=" + (Join-Path $FfmpegBin "ffmpeg.exe")),
  ("log=" + $logFile)
) -join "`n"
Set-Content -Path $marker -Value $markTxt -Encoding UTF8

Log "INSTALL_OK"
Write-Host ""
Write-Host "Install/repair finished. Use Start bat to open the app."
Write-Host "Next: fill LLM API in the web UI, then upload a video."
exit 0
