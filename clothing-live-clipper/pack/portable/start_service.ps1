# Start web service in background; auto-heal common failures
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
$PyExe = Join-Path $AppRoot ".venv\Scripts\python.exe"
$BundledPy = Join-Path $Tools "python\python.exe"
$Src = Join-Path $AppRoot "clothing-live-clipper\src"
if (-not (Test-Path $Src)) { $Src = Join-Path $AppRoot "src" }
$CodeRoot = Split-Path -Parent $Src

New-Item -ItemType Directory -Force -Path $Logs | Out-Null
$outLog = Join-Path $Logs "uvicorn.out.log"
$errLog = Join-Path $Logs "uvicorn.err.log"
$errStart = Join-Path $Logs "start_error.txt"
$pidFile = Join-Path $Tools "uvicorn.pid"

function Fail([string]$msg) {
  Set-Content -Path $errStart -Value $msg -Encoding UTF8
  Write-Host $msg
  exit 1
}
function Log([string]$m) { Write-Host $m }

function Resolve-PythonExe {
  # Prefer package venv; fall back to bundled portable python.exe
  $cands = @(
    (Join-Path $AppRoot ".venv\Scripts\python.exe"),
    $BundledPy,
    (Join-Path $AppRoot "python\python.exe")
  )
  $marker = Join-Path $Tools "use_bundled_python.txt"
  if (Test-Path $marker) {
    $m = (Get-Content $marker -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ($m -and (Test-Path $m)) { $cands = @($m) + $cands }
  }
  foreach ($p in $cands) {
    if ($p -and (Test-Path $p)) {
      try {
        $r = & $p -c "import sys; print(sys.version)" 2>&1
        if ($LASTEXITCODE -eq 0) { return $p }
      } catch {}
    }
  }
  return $null
}
$resolvedPy = Resolve-PythonExe
if ($resolvedPy) { $PyExe = $resolvedPy }

function Free-Port8787 {
  try {
    Get-NetTCPConnection -LocalPort 8787 -ErrorAction SilentlyContinue | ForEach-Object {
      try { Stop-Process -Id $_.OwningProcess -Force -ErrorAction SilentlyContinue } catch {}
    }
  } catch {}
  if (Test-Path $pidFile) {
    $old = Get-Content $pidFile -ErrorAction SilentlyContinue
    if ($old) {
      try { Stop-Process -Id ([int]$old) -Force -ErrorAction SilentlyContinue } catch {}
    }
    Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
  }
  Start-Sleep -Milliseconds 400
}

function Resolve-Model([string]$device = "cuda") {
  # Product policy:
  #   GPU → medium first, then small/tiny
  #   CPU → small first, then tiny/medium (medium last; too slow on CPU)
  if ($device -eq "cpu") {
    $order = @("whisper-small", "whisper-tiny", "whisper-medium")
  } else {
    $order = @("whisper-medium", "whisper-small", "whisper-tiny")
  }
  foreach ($n in $order) {
    $p = Join-Path $ModelsRoot ($n + "\model.bin")
    if ((Test-Path $p) -and ((Get-Item $p).Length -gt 1MB)) {
      return (Join-Path $ModelsRoot $n)
    }
  }
  return $null
}

function Read-DeviceHint {
  # Blank PC auto policy:
  # - NVIDIA driver present  → cuda + float16
  # - no NVIDIA / probe fail → cpu + int8
  # - multi-GPU             → free-memory largest (or tools\gpu_index.txt)
  $device = "cuda"
  $ctype = "float16"
  $gpuIndex = 0
  $hint = Join-Path $Tools "device_hint.txt"
  if (Test-Path $hint) {
    # Strip UTF-8 BOM if present so `device=` still matches
    Get-Content $hint -Encoding UTF8 | ForEach-Object {
      $line = $_ -replace "^\uFEFF", ""
      if ($line -match "^device=(.+)$") { $device = $Matches[1].Trim() }
      if ($line -match "^compute_type=(.+)$") { $ctype = $Matches[1].Trim() }
      if ($line -match "^gpu_index=(.+)$") {
        $v = $Matches[1].Trim()
        if ($v -match "^\d+$") { $gpuIndex = [int]$v }
      }
    }
  }
  # manual override file always wins when present
  $manual = Join-Path $Tools "gpu_index.txt"
  if (Test-Path $manual) {
    $raw = (Get-Content $manual -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ("$raw" -match "(\d+)") { $gpuIndex = [int]$Matches[1] }
  }
  $smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
  if (-not $smi) {
    $device = "cpu"
    $ctype = "int8"
    Log "No NVIDIA driver (nvidia-smi missing) → ASR CPU"
  } else {
    # Prefer CUDA on blank NVIDIA PCs even if hint was stale cpu from old package
    if ($device -ne "cuda") {
      Log "device_hint was $device but nvidia-smi exists → auto switch to cuda"
      $device = "cuda"
      if (-not $ctype -or $ctype -eq "int8") { $ctype = "float16" }
    }
    # auto-pick GPU if multi-card and index not forced
    if (-not (Test-Path $manual)) {
      try {
        $csv = & nvidia-smi.exe --query-gpu=index,memory.free --format=csv,noheader,nounits 2>$null
        $bestIdx = $gpuIndex
        $bestFree = -1
        $count = 0
        foreach ($line in $csv) {
          if ("$line" -match "^\s*(\d+)\s*,\s*(\d+)") {
            $count++
            $idx = [int]$Matches[1]
            $free = [int]$Matches[2]
            if ($free -gt $bestFree) {
              $bestFree = $free
              $bestIdx = $idx
            }
          }
        }
        if ($count -gt 1 -and $bestFree -ge 0) {
          $gpuIndex = $bestIdx
          Log ("Multi-GPU: auto-select index=$gpuIndex free_mib=$bestFree")
        } elseif ($count -ge 1) {
          Log ("NVIDIA GPU count=$count using index=$gpuIndex")
        }
      } catch {}
    }
    # double-check ctranslate2 can actually see CUDA inside venv
    try {
      if (Test-Path $PyExe) {
        $cnt = Get-PythonCudaCount
        if ($cnt -eq 0) {
          Log "WARNING: nvidia-smi ok but ctranslate2 cuda_count=0"
          if (Repair-CudaWheels) {
            $cnt2 = Get-PythonCudaCount
            Log ("CUDA count after repair: " + $cnt2)
            if ($cnt2 -le 0) {
              Log "CUDA still 0 after wheel repair → keep device=cuda (runtime may fallback CPU on first ASR)"
            }
          }
        } elseif ($cnt -gt 0) {
          Log ("CUDA devices visible to python: " + $cnt)
        }
      }
    } catch {}
  }
  return @{ device = $device; compute = $ctype; gpu_index = $gpuIndex }
}

function Get-PythonCudaCount {
  if (-not (Test-Path $PyExe)) { return -1 }
  $probe = Join-Path $PackRoot "_probe_cuda.py"
  try {
    if (Test-Path $probe) {
      $out = & $PyExe $probe 2>&1 | Out-String
      if ($out -match "ctranslate2_cuda_count\s*=\s*(\d+)") {
        return [int]$Matches[1]
      }
      if ($out -match "RESULT\s*=\s*GPU_OK") { return 1 }
    }
  } catch {}
  return -1
}

function Repair-CudaWheels {
  # Hyper-V / blank PC: driver present but pip CUDA wheels missing/broken.
  if (-not (Test-Path $PyExe)) { return $false }
  $marker = Join-Path $Tools "cuda_repair_attempted.txt"
  if (Test-Path $marker) {
    $ageH = 0
    try { $ageH = ((Get-Date) - (Get-Item $marker).LastWriteTime).TotalHours } catch {}
    if ($ageH -lt 6) {
      Log "CUDA repair skipped (already tried within 6h)"
      return $false
    }
  }
  Log "nvidia-smi OK but python CUDA=0 → auto-install CUDA wheels..."
  $pkgs = @(
    "nvidia-cublas-cu12",
    "nvidia-cudnn-cu12",
    "nvidia-cuda-runtime-cu12",
    "nvidia-cuda-nvrtc-cu12"
  )
  $mirrors = @(
    @(),
    @("-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "--trusted-host", "pypi.tuna.tsinghua.edu.cn"),
    @("-i", "https://mirrors.aliyun.com/pypi/simple/", "--trusted-host", "mirrors.aliyun.com")
  )
  $ok = $false
  foreach ($m in $mirrors) {
    $args = @("-m", "pip", "install", "--disable-pip-version-check", "--upgrade") + $m + $pkgs
    Log ("pip CUDA repair " + ($m -join " "))
    & $PyExe @args
    if ($LASTEXITCODE -eq 0) { $ok = $true; break }
  }
  $utf8NoBom = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($marker, ((Get-Date).ToString("s") + " ok=" + $ok + "`n"), $utf8NoBom)
  return $ok
}

function Write-GpuStatus($dev) {
  $statusPath = Join-Path $Logs "gpu_status.txt"
  $gpuIndex = 0
  if ($dev.ContainsKey("gpu_index")) { $gpuIndex = [int]$dev.gpu_index }
  $modelPath = Resolve-Model $dev.device
  $lines = @(
    ("time=" + [DateTime]::Now.ToString("s")),
    ("device=" + $dev.device),
    ("compute_type=" + $dev.compute),
    ("gpu_index=" + $gpuIndex),
    ("python=" + $PyExe),
    ("model=" + $modelPath),
    ("ffmpeg=" + (Join-Path $FfmpegBin "ffmpeg.exe")),
    "policy=gpu_medium_cpu_small",
    "hyperv_hint=guest_needs_nvidia_driver_and_pip_cuda_wheels"
  )
  try {
    $cnt = Get-PythonCudaCount
    $lines += ("ctranslate2_cuda_count=" + $cnt)
    if ($dev.device -eq "cuda" -and $cnt -eq 0) {
      $lines += "note=driver_ok_but_python_cuda_0_run_首次安装配置_or_wait_auto_repair"
    }
  } catch {
    $lines += "ctranslate2_cuda_count=error"
  }
  $smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
  if ($smi) {
    try {
      $g = & nvidia-smi.exe -L 2>&1 | Select-Object -First 1
      $lines += ("nvidia_smi=" + (("$g") | Out-String).Trim())
    } catch {
      $lines += "nvidia_smi=error"
    }
  } else {
    $lines += "nvidia_smi=missing"
  }
  $utf8NoBom = New-Object System.Text.UTF8Encoding $false
  [System.IO.File]::WriteAllText($statusPath, (($lines -join "`n") + "`n"), $utf8NoBom)
  # keep device_hint in sync without BOM
  $hintPath = Join-Path $Tools "device_hint.txt"
  [System.IO.File]::WriteAllText(
    $hintPath,
    ("device=" + $dev.device + "`ncompute_type=" + $dev.compute + "`ngpu_index=" + $gpuIndex + "`n"),
    $utf8NoBom
  )
  Log ("GPU status written: " + $statusPath)
}

function Get-AllGpuIds {
  # Team mode: use all NVIDIA GPUs (e.g. 0,1 on 2x RTX3060) unless user overrides.
  $manual = Join-Path $Tools "gpu_ids.txt"
  if (Test-Path $manual) {
    $raw = (Get-Content $manual -Raw -ErrorAction SilentlyContinue)
    if ($raw) {
      $ids = @()
      foreach ($p in (($raw -replace "[;`r`n]", ",") -split ",")) {
        if ($p.Trim() -match "^\d+$") { $ids += [int]$p.Trim() }
      }
      if ($ids.Count -gt 0) { return ($ids -join ",") }
    }
  }
  $single = Join-Path $Tools "gpu_index.txt"
  if (Test-Path $single) {
    $raw = (Get-Content $single -ErrorAction SilentlyContinue | Select-Object -First 1)
    if ("$raw" -match "(\d+)") { return $Matches[1] }
  }
  if ($env:CLIPPER_ASR_GPU_IDS) { return $env:CLIPPER_ASR_GPU_IDS }
  $smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
  if (-not $smi) { return "0" }
  try {
    $csv = & nvidia-smi.exe --query-gpu=index --format=csv,noheader,nounits 2>$null
    $ids = @()
    foreach ($line in $csv) {
      if ("$line" -match "(\d+)") { $ids += [int]$Matches[1] }
    }
    if ($ids.Count -gt 0) { return (($ids | Select-Object -First 4) -join ",") }
  } catch {}
  return "0"
}

function Ensure-EmbedPythonPath {
  # Embedded CPython uses python*._pth and IGNORES PYTHONPATH in isolated mode.
  # Inject clothing-live-clipper\src so `import clipper` works with tools\python\python.exe.
  $embedRoot = Join-Path $Tools "python"
  if (-not (Test-Path $embedRoot)) { return }
  if (-not (Test-Path $Src)) { return }
  $srcAbs = (Resolve-Path $Src).Path
  $pthFiles = Get-ChildItem -Path $embedRoot -Filter "python*._pth" -ErrorAction SilentlyContinue
  foreach ($f in $pthFiles) {
    try {
      $lines = @()
      if (Test-Path $f.FullName) {
        $lines = Get-Content -Path $f.FullName -ErrorAction SilentlyContinue
      }
      $out = New-Object System.Collections.Generic.List[string]
      $hasSrc = $false
      $hasSite = $false
      foreach ($line in $lines) {
        $t = ("$line").Trim()
        if ($t -eq $srcAbs -or $t -eq $Src) { $hasSrc = $true }
        if ($t -eq "import site") { $hasSite = $true }
        # drop commented import site; we'll ensure active one
        if ($t -match '^#\s*import\s+site$') { continue }
        $out.Add("$line") | Out-Null
      }
      if (-not $hasSrc) { $out.Add($srcAbs) | Out-Null }
      if (-not $hasSite) { $out.Add("import site") | Out-Null }
      $utf8NoBom = New-Object System.Text.UTF8Encoding $false
      [System.IO.File]::WriteAllLines($f.FullName, $out.ToArray(), $utf8NoBom)
      Log ("Embed pth patched for clipper src: " + $f.Name)
    } catch {
      Log ("WARN: cannot patch embed pth: " + $_.Exception.Message)
    }
  }
}

function Start-Uvicorn($dev) {
  $env:PATH = "$FfmpegBin;" + $env:PATH
  $env:PYTHONPATH = $Src
  # Critical for tools\python embed layout (blank PC / preinstalled runtime)
  Ensure-EmbedPythonPath
  $modelPath = Resolve-Model $dev.device
  $gpuIndex = 0
  if ($dev.ContainsKey("gpu_index")) { $gpuIndex = [int]$dev.gpu_index }
  $gpuIds = Get-AllGpuIds
  # If model path is set, worker uses it directly. Policy already chose medium/small.
  $env:CLIPPER_LOCAL_WHISPER_MODEL = $modelPath
  $env:CLIPPER_ASR_DEVICE = $dev.device
  $env:CLIPPER_ASR_COMPUTE_TYPE = $dev.compute
  $env:CLIPPER_ASR_GPU_INDEX = "$gpuIndex"
  $env:CLIPPER_ASR_GPU_IDS = "$gpuIds"
  $env:CLIPPER_ASR_QUALITY = "auto"
  # LAN stability defaults: FIFO queue + serial ASR + render slot. Override via env if needed.
  if (-not $env:CLIPPER_QUEUE_MODE) { $env:CLIPPER_QUEUE_MODE = "stable" }
  if (-not $env:CLIPPER_MAX_CONCURRENT_JOBS) {
    if ($env:CLIPPER_QUEUE_MODE -eq "throughput") { $env:CLIPPER_MAX_CONCURRENT_JOBS = "4" }
    else { $env:CLIPPER_MAX_CONCURRENT_JOBS = "3" }
  }
  if (-not $env:CLIPPER_ASR_SLOTS) { $env:CLIPPER_ASR_SLOTS = "1" }
  if (-not $env:CLIPPER_LLM_SLOTS) { $env:CLIPPER_LLM_SLOTS = "1" }
  if (-not $env:CLIPPER_RENDER_SLOTS) { $env:CLIPPER_RENDER_SLOTS = "1" }
  if (-not $env:CLIPPER_WARM_EXTRACT_SLOTS) { $env:CLIPPER_WARM_EXTRACT_SLOTS = "1" }
  if (-not $env:CLIPPER_WARM_FRONT_N) { $env:CLIPPER_WARM_FRONT_N = "2" }
  if (-not $env:CLIPPER_LLM_PARALLEL) { $env:CLIPPER_LLM_PARALLEL = "1" }
  # Do NOT force CUDA_VISIBLE_DEVICES hard-hide other GPUs unless user set it.
  if (-not $env:CLIPPER_PLAYBACK_SPEED) { $env:CLIPPER_PLAYBACK_SPEED = "1.4" }
  # Bind host: 0.0.0.0 for LAN clients (VM browsers), 127.0.0.1 local only
  $bindHost = "127.0.0.1"
  if ($env:CLIPPER_BIND_HOST) { $bindHost = $env:CLIPPER_BIND_HOST }
  elseif (Test-Path (Join-Path $Tools "lan_mode.txt")) { $bindHost = "0.0.0.0" }
  # Make status easy to spot in black window
  Write-Host ""
  Write-Host "============================================"
  Write-Host (" ASR device      : " + $dev.device)
  Write-Host (" ASR compute     : " + $dev.compute)
  Write-Host (" ASR GPU ids     : " + $gpuIds)
  Write-Host (" Whisper model   : " + $modelPath)
  Write-Host (" Queue mode      : " + $env:CLIPPER_QUEUE_MODE)
  Write-Host (" Active job slots: " + $env:CLIPPER_MAX_CONCURRENT_JOBS)
  Write-Host (" ASR/LLM/Render  : " + $env:CLIPPER_ASR_SLOTS + "/" + $env:CLIPPER_LLM_SLOTS + "/" + $env:CLIPPER_RENDER_SLOTS)
  Write-Host (" Warm front N    : " + $env:CLIPPER_WARM_FRONT_N)
  Write-Host (" Bind host       : " + $bindHost)
  Write-Host (" Python          : " + $PyExe)
  if ($dev.device -eq "cuda") {
    Write-Host " Model policy    : GPU → medium (multi-GPU parallel ASR)"
  } else {
    Write-Host " Model policy    : CPU → small (fallback tiny)"
  }
  Write-Host "============================================"
  Write-Host ""
  Write-GpuStatus $dev

  Set-Content -Path $outLog -Value "" -Encoding UTF8
  Set-Content -Path $errLog -Value "" -Encoding UTF8

  $argList = @(
    "-m", "uvicorn", "clipper.web:app",
    "--host", $bindHost,
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
  if ($p) { Set-Content -Path $pidFile -Value $p.Id -Encoding ASCII }
  return $p
}

function Wait-Ready([int]$Seconds = 20) {
  $n = [Math]::Max(4, [int]($Seconds * 2))
  # Health-check localhost even when service binds 0.0.0.0 for LAN clients.
  $urls = @(
    "http://127.0.0.1:8787/",
    "http://localhost:8787/"
  )
  for ($i = 0; $i -lt $n; $i++) {
    Start-Sleep -Milliseconds 500
    foreach ($u in $urls) {
      try {
        $r = Invoke-WebRequest -UseBasicParsing $u -TimeoutSec 2
        if ($r.StatusCode -ge 200 -and $r.StatusCode -lt 500) { return $true }
      } catch {}
    }
    if (Test-Path $pidFile) {
      $id = Get-Content $pidFile
      if ($id -and -not (Get-Process -Id ([int]$id) -ErrorAction SilentlyContinue)) { return $false }
    }
  }
  return $false
}

# Precheck model existence with provisional device (may refine after Read-DeviceHint)
if (-not (Test-Path $PyExe) -or -not (Test-Path (Join-Path $FfmpegBin "ffmpeg.exe")) -or -not (Resolve-Model "cuda")) {
  Log "Environment incomplete. Auto repair install..."
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PackRoot "install_all.ps1")
  if ($LASTEXITCODE -ne 0) { Fail "Auto repair install failed. See tools\logs\" }
  $resolvedPy = Resolve-PythonExe
  if ($resolvedPy) { $PyExe = $resolvedPy }
}

if (-not (Test-Path $PyExe)) {
  Fail "python.exe not found. Full pack should include tools\python\python.exe or a .venv."
}

$imp = & $PyExe -c "import fastapi,uvicorn,faster_whisper; print('OK')" 2>&1
if (("$imp") -notmatch "OK") {
  Log "Dependencies broken. Auto reinstall..."
  & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PackRoot "install_all.ps1")
  if ($LASTEXITCODE -ne 0) { Fail ("Dependency repair failed: " + $imp) }
  $resolvedPy = Resolve-PythonExe
  if ($resolvedPy) { $PyExe = $resolvedPy }
  $imp2 = & $PyExe -c "import fastapi,uvicorn,faster_whisper; print('OK')" 2>&1
  if (("$imp2") -notmatch "OK") { Fail ("Dependency still broken: " + $imp2) }
}

Free-Port8787
$dev = Read-DeviceHint
$chosenModel = Resolve-Model $dev.device
Log ("Start service http://127.0.0.1:8787/ device=" + $dev.device + " compute=" + $dev.compute)
Log ("Model policy: " + ($(if ($dev.device -eq "cpu") { "CPU→small" } else { "GPU→medium" })))
Log ("Model: " + $chosenModel)
if (-not $chosenModel) { Fail "No whisper model found under models\ (need medium/small/tiny)" }

$proc = Start-Uvicorn $dev
if (-not $proc) { Fail "Cannot start uvicorn process" }

if (-not (Wait-Ready 18)) {
  $tail = ""
  if (Test-Path $errLog) { $tail = Get-Content $errLog -Raw -ErrorAction SilentlyContinue }
  Log "First start not ready. Analyze and self-heal..."
  if ($tail) { Log $tail }

  $useCpu = $false
  if ($tail -match "CUDA|cuda|cublas|cudnn|GPU|nvrtc|no kernel image") {
    Log "GPU/CUDA issue detected -> fallback CPU"
    $useCpu = $true
  }
  if ($tail -match "Address already in use|10048|only one usage") {
    Log "Port busy -> free 8787"
    Free-Port8787
  }
  if ($tail -match "No module named|ModuleNotFoundError") {
    Log "Missing module -> reinstall deps"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PackRoot "install_all.ps1") | Out-Null
  }
  if ($tail -match "ffmpeg|WinError 2") {
    Log "ffmpeg issue -> reinstall"
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $PackRoot "install_all.ps1") | Out-Null
  }

  Free-Port8787
  if ($useCpu) {
    $dev = @{ device = "cpu"; compute = "int8"; gpu_index = 0 }
    $hintPath = Join-Path $Tools "device_hint.txt"
    $utf8NoBom = New-Object System.Text.UTF8Encoding $false
    [System.IO.File]::WriteAllText($hintPath, "device=cpu`ncompute_type=int8`ngpu_index=0`n", $utf8NoBom)
  }
  $proc2 = Start-Uvicorn $dev
  if (-not $proc2) { Fail ("Still cannot start process. Log: " + $errLog) }
  if (-not (Wait-Ready 25)) {
    $tail2 = ""
    if (Test-Path $errLog) { $tail2 = (Get-Content $errLog -Tail 40 -ErrorAction SilentlyContinue | Out-String) }
    Fail ("Service still not ready after auto heal.`n" + $tail2)
  }
}

Start-Process "http://127.0.0.1:8787/"
Log "OK service is running in background. You may close this window."
Log "Stop service: run stop bat."
exit 0
