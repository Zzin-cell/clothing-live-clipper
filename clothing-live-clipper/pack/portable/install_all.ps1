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


function Test-VcRedistInstalled {
  $keys = @(
    "HKLM:\SOFTWARE\Microsoft\VisualStudio\14.0\VC\Runtimes\x64",
    "HKLM:\SOFTWARE\WOW6432Node\Microsoft\VisualStudio\14.0\VC\Runtimes\x64"
  )
  foreach ($k in $keys) {
    try {
      if (Test-Path $k) {
        $inst = (Get-ItemProperty -Path $k -ErrorAction SilentlyContinue).Installed
        if ($inst -eq 1) { return $true }
      }
    } catch {}
  }
  $sys = Join-Path $env:WINDIR "System32"
  foreach ($dll in @("vcruntime140.dll", "vcruntime140_1.dll", "msvcp140.dll")) {
    if (-not (Test-Path (Join-Path $sys $dll))) { return $false }
  }
  return $true
}

function Ensure-VcRedist {
  if (Test-VcRedistInstalled) {
    Log "Visual C++ Redistributable (x64) detected"
    return $true
  }
  Log "Visual C++ Redistributable missing — required by ctranslate2/faster-whisper"
  $bundledCandidates = @(
    (Join-Path $Tools "vc_redist.x64.exe"),
    (Join-Path $AppRoot "tools\vc_redist.x64.exe"),
    (Join-Path $AppRoot "pack\cache\vc_redist.x64.exe")
  )
  $dest = $null
  foreach ($c in $bundledCandidates) {
    if ((Test-Path $c) -and ((Get-Item $c).Length -gt 1MB)) {
      $dest = $c
      Log ("Using bundled VC++ installer: " + $c)
      break
    }
  }
  if (-not $dest) {
    $dest = Join-Path $Tools "vc_redist.x64.exe"
    if (-not (Download-File -Urls @(
      "https://aka.ms/vs/17/release/vc_redist.x64.exe",
      "https://aka.ms/vs/16/release/vc_redist.x64.exe"
    ) -Dest $dest)) {
      SoftFail "vc_redist missing and download failed"
      return $false
    }
  }
  try {
    $p = Start-Process -FilePath $dest -ArgumentList "/install","/quiet","/norestart" -Wait -PassThru
    Log ("vc_redist exit=" + $p.ExitCode)
  } catch {
    SoftFail ("vc_redist install failed: " + $_.Exception.Message)
    return $false
  }
  if (Test-VcRedistInstalled) {
    Log "Visual C++ Redistributable installed OK"
    return $true
  }
  SoftFail "VC++ may need reboot after install"
  return $false
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

function Enable-EmbeddedPythonSite {
  # Official embed package disables import site by default ("...._pth" has "#import site").
  # We must enable site so pip / venv / stdlib packages work.
  $embedRoot = Join-Path $Tools "python"
  if (-not (Test-Path $embedRoot)) { return $false }
  $pthFiles = Get-ChildItem -Path $embedRoot -Filter "python*._pth" -ErrorAction SilentlyContinue
  foreach ($f in $pthFiles) {
    try {
      $txt = Get-Content -Path $f.FullName -Raw -ErrorAction SilentlyContinue
      if (-not $txt) { continue }
      $new = $txt -replace "(?m)^\s*#\s*import\s+site\s*$", "import site"
      if ($new -notmatch "(?m)^\s*import\s+site\s*$") {
        if (-not $new.EndsWith("`n")) { $new += "`r`n" }
        $new += "import site`r`n"
      }
      if ($new -ne $txt) {
        Set-Content -Path $f.FullName -Value $new -Encoding ASCII
        Log ("Enabled import site in " + $f.Name)
      }
    } catch {
      SoftFail ("cannot patch embed pth: " + $_.Exception.Message)
    }
  }
  return $true
}

function Ensure-EmbeddedPip([string]$embedPy) {
  if (-not (Test-Path $embedPy)) { return $false }
  try {
    $chk = & $embedPy -c "import pip; print('PIP_OK')" 2>&1
    if (("$chk") -match "PIP_OK") { return $true }
  } catch {}
  $getPip = Join-Path $env:TEMP "xiaomian_get-pip.py"
  $urls = @(
    "https://bootstrap.pypa.io/get-pip.py",
    "https://mirrors.aliyun.com/pypi/get-pip.py"
  )
  $okDl = $false
  foreach ($u in $urls) {
    try {
      Log ("GET get-pip.py " + $u)
      $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
      if ($curl) {
        & curl.exe -L --fail --retry 3 --connect-timeout 20 --max-time 180 -o $getPip $u
        if (($LASTEXITCODE -eq 0) -and (Test-Path $getPip) -and ((Get-Item $getPip).Length -gt 1000)) {
          $okDl = $true
          break
        }
      } else {
        Invoke-WebRequest -Uri $u -OutFile $getPip -UseBasicParsing
        if ((Test-Path $getPip) -and ((Get-Item $getPip).Length -gt 1000)) {
          $okDl = $true
          break
        }
      }
    } catch {
      SoftFail ("get-pip download fail: " + $_.Exception.Message)
    }
  }
  if (-not $okDl) { return $false }
  try {
    & $embedPy $getPip --disable-pip-version-check
    if ($LASTEXITCODE -ne 0) { return $false }
    $chk2 = & $embedPy -c "import pip; print('PIP_OK')" 2>&1
    return (("$chk2") -match "PIP_OK")
  } catch {
    return $false
  }
}

function Get-PythonCommand {
  # 0) Bundled portable Python (for blank PCs without system Python)
  $embedCandidates = @(
    (Join-Path $Tools "python\python.exe"),
    (Join-Path $AppRoot "python\python.exe"),
    (Join-Path $AppRoot "runtime\python\python.exe")
  )
  foreach ($p in $embedCandidates) {
    $ver = Test-PythonExe $p
    if ($ver) {
      [void](Enable-EmbeddedPythonSite)
      # ensure pip present (needed for -m venv + later ensurepip fallbacks)
      [void](Ensure-EmbeddedPip $p)
      Log ("Found bundled portable Python " + $ver + ": " + $p)
      return @{ Kind = "path"; Cmd = $p; Source = "bundled" }
    }
  }

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
            return @{ Kind = "path"; Cmd = $p; Source = "system" }
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
            return @{ Kind = "path"; Cmd = "$exe".Trim(); Source = "system" }
          }
          return @{ Kind = "py"; Arg = $arg; Source = "system" }
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
        return @{ Kind = "path"; Cmd = $cmd.Source; Source = "system" }
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
      return @{ Kind = "path"; Cmd = $p; Source = "system" }
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
    $created = $false
    if ($pyInfo.Kind -eq "path") {
      # 1) normal venv
      & $pyInfo.Cmd -m venv $PyVenv 2>&1 | Out-Null
      if (Test-Path $PyExe) { $created = $true }
      # 2) embed Python often lacks ensurepip; create without pip then bootstrap
      if (-not $created) {
        Log "retry venv --without-pip (embed friendly)"
        & $pyInfo.Cmd -m venv --without-pip $PyVenv 2>&1 | Out-Null
        if (Test-Path $PyExe) {
          $created = $true
          $getPip = Join-Path $env:TEMP "xiaomian_get-pip.py"
          if (-not (Test-Path $getPip)) {
            [void](Download-File -Urls @(
              "https://bootstrap.pypa.io/get-pip.py",
              "https://mirrors.aliyun.com/pypi/get-pip.py"
            ) -Dest $getPip)
          }
          if (Test-Path $getPip) {
            & $PyExe $getPip --disable-pip-version-check 2>&1 | Out-Null
          }
        }
      }
      # 3) virtualenv package
      if (-not $created) {
        Log "venv module failed; try pip virtualenv"
        & $pyInfo.Cmd -m pip install --disable-pip-version-check virtualenv 2>&1 | Out-Null
        & $pyInfo.Cmd -m virtualenv $PyVenv 2>&1 | Out-Null
        if (Test-Path $PyExe) { $created = $true }
      }
      # 4) last resort: use bundled embed python in-place (no .venv)
      if (-not $created -and $pyInfo.Source -eq "bundled") {
        Log "WARN: cannot create .venv from embed; will use bundled python.exe in-place"
        $script:PyExe = $pyInfo.Cmd
        $marker = Join-Path $Tools "use_bundled_python.txt"
        Set-Content -Path $marker -Value $pyInfo.Cmd -Encoding UTF8
        return $true
      }
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


function Get-WheelHouse {
  $cands = @(
    (Join-Path $Tools "wheels"),
    (Join-Path $AppRoot "tools\wheels"),
    (Join-Path $AppRoot "pack\wheels")
  )
  foreach ($d in $cands) {
    if (Test-Path $d) {
      $n = @(Get-ChildItem -Path $d -File -ErrorAction SilentlyContinue | Where-Object { $_.Extension -in @(".whl", ".gz", ".zip") }).Count
      if ($n -gt 0) { return $d }
    }
  }
  return $null
}

function Install-PipPackages {
  param([string[]]$PackageArgs)
  $wheelHouse = Get-WheelHouse
  $attemptSets = @()
  if ($wheelHouse) {
    $attemptSets += ,@(
      "--no-index",
      "--find-links", $wheelHouse
    )
  }
  $attemptSets += ,@()
  $attemptSets += ,@(
    "-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "--trusted-host", "pypi.tuna.tsinghua.edu.cn"
  )
  $attemptSets += ,@(
    "-i", "https://mirrors.aliyun.com/pypi/simple/", "--trusted-host", "mirrors.aliyun.com"
  )
  $attemptSets += ,@(
    "-i", "https://pypi.org/simple"
  )
  foreach ($m in $attemptSets) {
    $all = @("-m", "pip", "install", "--disable-pip-version-check") + $m + $PackageArgs
    $mode = if ($m -contains "--no-index") { "OFFLINE" } else { "ONLINE" }
    Log ("pip[$mode] " + ($PackageArgs -join " ") + " " + ($m -join " "))
    & $PyExe @all
    if ($LASTEXITCODE -eq 0) { return $true }
    SoftFail ("pip failed exit=" + $LASTEXITCODE + ", try next source")
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
  # CUDA wheels are required on blank NVIDIA PCs without system CUDA toolkit.
  # faster-whisper/ctranslate2 needs these pip DLLs to actually see the GPU.
  Log "Install CUDA wheels for GPU ASR (blank PC friendly)"
  $cudaPkgs = @(
    "nvidia-cublas-cu12",
    "nvidia-cudnn-cu12",
    "nvidia-cuda-runtime-cu12",
    "nvidia-cuda-nvrtc-cu12"
  )
  if (-not (Install-PipPackages -PackageArgs $cudaPkgs)) {
    SoftFail "CUDA wheel batch failed; try one-by-one"
    foreach ($p in $cudaPkgs) {
      if (-not (Install-PipPackages -PackageArgs @($p))) {
        SoftFail ("CUDA package failed: " + $p)
      }
    }
  }
  # Verify GPU visibility (driver may exist while python still sees 0)
  try {
    $smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if ($smi) {
      $probe = & $PyExe -c @"
import os, site
from pathlib import Path
cands=[]
for sp in site.getsitepackages()+[site.getusersitepackages()]:
    root=Path(sp)/'nvidia'
    if not root.exists():
        continue
    for child in root.iterdir():
        for leaf in ('bin','lib'):
            d=child/leaf
            if d.exists(): cands.append(str(d))
if cands:
    os.environ['PATH']=os.pathsep.join(cands)+os.pathsep+os.environ.get('PATH','')
    if hasattr(os,'add_dll_directory'):
        for d in cands:
            try: os.add_dll_directory(d)
            except Exception: pass
import ctranslate2
print('CUDA_COUNT', int(ctranslate2.get_cuda_device_count() or 0))
print('NVIDIA_DIRS', len(cands))
"@ 2>&1
      Log ("GPU probe: " + (($probe | Out-String).Trim()))
      if (("$probe") -match "CUDA_COUNT 0") {
        SoftFail "Driver present but python cuda_count=0. ASR will try GPU then fallback CPU. Reinstall CUDA wheels or update NVIDIA driver."
      }
    } else {
      Log "No nvidia-smi; blank PC without NVIDIA driver → CPU ASR"
    }
  } catch {
    SoftFail ("GPU probe skipped: " + $_.Exception.Message)
  }
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
  # Keep whatever is prebundled. For network install on blank PC:
  # prefer downloading small first (works for both CPU and GPU), then tiny.
  # medium is huge; only download if user already expected full pack offline quality.
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

function Get-PreferredGpuIndex {
  # Manual override first (blank PC users can put a single number in tools\gpu_index.txt)
  $manual = Join-Path $Tools "gpu_index.txt"
  if (Test-Path $manual) {
    $raw = (Get-Content $manual -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ("$raw" -match "(\d+)") { return [int]$Matches[1] }
  }
  if ($env:CLIPPER_ASR_GPU_INDEX -and ("$env:CLIPPER_ASR_GPU_INDEX" -match "^\d+$")) {
    return [int]$env:CLIPPER_ASR_GPU_INDEX
  }
  # Auto: pick GPU with most free memory (helps multi-GPU / shared laptop GPUs)
  $smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
  if (-not $smi) { return 0 }
  try {
    $csv = & nvidia-smi.exe --query-gpu=index,memory.free --format=csv,noheader,nounits 2>$null
    $bestIdx = 0
    $bestFree = -1
    foreach ($line in $csv) {
      if ("$line" -match "^\s*(\d+)\s*,\s*(\d+)") {
        $idx = [int]$Matches[1]
        $free = [int]$Matches[2]
        if ($free -gt $bestFree) {
          $bestFree = $free
          $bestIdx = $idx
        }
      }
    }
    if ($bestFree -ge 0) {
      Log ("Auto-selected GPU index=$bestIdx free_mem_mib=$bestFree")
      return $bestIdx
    }
  } catch {}
  return 0
}

function Write-DeviceHint {
  # Blank PC: auto enable GPU when NVIDIA driver is present; else CPU.
  $hint = Join-Path $Tools "device_hint.txt"
  $device = "cuda"
  $ctype = "float16"
  $gpuIndex = 0
  $smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
  if (-not $smi) {
    $device = "cpu"
    $ctype = "int8"
    Log "No nvidia-smi, prefer CPU ASR (blank PC without NVIDIA driver/GPU)"
  } else {
    $q = & nvidia-smi.exe -L 2>&1
    if ($LASTEXITCODE -ne 0) {
      $device = "cpu"
      $ctype = "int8"
      Log "nvidia-smi failed, prefer CPU ASR"
    } else {
      $lines = @($q | Where-Object { "$_" -match "GPU\s+\d+" })
      Log ("NVIDIA GPU(s) detected: " + $lines.Count)
      foreach ($ln in $lines) { Log ("  " + (("$ln") | Out-String).Trim()) }
      $gpuIndex = Get-PreferredGpuIndex
      # Verify CUDA libs visible to package python (after deps install)
      if (Test-Path $PyExe) {
        try {
          $cnt = & $PyExe -c "import ctranslate2; print(int(ctranslate2.get_cuda_device_count() or 0))" 2>$null
          if ("$cnt" -match "^\s*0\s*$") {
            Log "WARNING: driver OK but ctranslate2 cannot see CUDA yet; will still try GPU first and fallback CPU on load fail"
          } else {
            Log ("ctranslate2_cuda_count=" + (("$cnt") -replace "\s", ""))
          }
        } catch {
          Log "ctranslate2 CUDA probe skipped"
        }
      }
      Log ("ASR will auto-use GPU index=$gpuIndex compute=float16")
    }
  }
  # Use UTF8 *without BOM* so PowerShell Get-Content regex can match `device=`
  # (BOM would make the first key look like `\ufeffdevice=` and fail to load)
  $content = "device=$device`ncompute_type=$ctype`ngpu_index=$gpuIndex`n"
  $utf8NoBom = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($hint, $content, $utf8NoBom)
  Log ("Wrote device_hint: device=$device compute=$ctype gpu_index=$gpuIndex")
}

# ---- main ----
Log ("AppRoot=" + $AppRoot)
Log "Auto diagnose and repair install..."

$pyInfo = Get-PythonCommand
if (-not $pyInfo) {
  HardFail "Python 3.10+ not found. Full pack should include tools\python\python.exe. Or install Python and Add to PATH: https://www.python.org/downloads/windows/"
}
if ($pyInfo.Source -eq "bundled") {
  Log "Using bundled portable Python (no system Python required)"
}

if (-not (Ensure-Venv $pyInfo)) {
  SoftFail "venv create failed once, retry"
  Start-Sleep 1
  if (-not (Ensure-Venv $pyInfo)) { HardFail "venv create failed" }
}
Log ("venv OK: " + $PyExe)

[void](Ensure-VcRedist)

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

# Final GPU report for blank PC / Hyper-V guests
try {
  Write-Host ""
  Write-Host "========== GPU REPORT =========="
  $smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
  if ($smi) {
    $g = & nvidia-smi.exe -L 2>&1 | Select-Object -First 3
    Write-Host ("nvidia-smi: " + (($g | Out-String).Trim()))
  } else {
    Write-Host "nvidia-smi: MISSING (no NVIDIA driver in this OS/VM)"
  }
  if (Test-Path $PyExe) {
    $probe = Join-Path $PackRoot "_probe_cuda.py"
    if (Test-Path $probe) {
      $po = & $PyExe $probe 2>&1 | Out-String
      Write-Host $po.Trim()
    } else {
      $cnt = & $PyExe -c "import ctranslate2; print(ctranslate2.get_cuda_device_count())" 2>&1
      Write-Host ("ctranslate2_cuda_count=" + $cnt)
    }
  }
  $hint = Join-Path $Tools "device_hint.txt"
  if (Test-Path $hint) {
    Write-Host "device_hint:"
    Get-Content $hint | ForEach-Object { Write-Host ("  " + $_) }
  }
  Write-Host "policy: GPU→medium / CPU→small"
  Write-Host "If cuda_count=0 but nvidia-smi works: re-run this installer online, or check Hyper-V GPU passthrough."
  Write-Host "================================"
} catch {
  SoftFail ("GPU report failed: " + $_.Exception.Message)
}

Log "INSTALL_OK"
Write-Host ""
Write-Host "Install/repair finished. Use Start bat to open the app."
Write-Host "Next: fill LLM API in the web UI, then upload a video."
Write-Host "GPU check: run 查看GPU状态.bat"
exit 0
