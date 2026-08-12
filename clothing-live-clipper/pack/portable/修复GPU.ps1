# One-click GPU repair for portable package (blank PC / Hyper-V guest)
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
$PyVenv = Join-Path $AppRoot ".venv\Scripts\python.exe"
$PyEmbed = Join-Path $Tools "python\python.exe"
$PyExe = $null
if (Test-Path $PyVenv) { $PyExe = $PyVenv }
elseif (Test-Path $PyEmbed) { $PyExe = $PyEmbed }

New-Item -ItemType Directory -Force -Path $Logs | Out-Null
$report = Join-Path $Logs ("gpu_repair_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))
function Log([string]$m) {
  $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $m
  Write-Host $line
  Add-Content -Path $report -Value $line -Encoding UTF8
}

Write-Host "============================================"
Write-Host " Xiaomian CapCut - GPU repair"
Write-Host "============================================"
Write-Host ("AppRoot = " + $AppRoot)
Write-Host ""

Log "STEP1 nvidia-smi"
$smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
$hasSmi = $false
if (-not $smi) {
  Log "FAIL: nvidia-smi not found"
  Log "HINT: Device Manager GPU is not enough. Need NVIDIA driver + compute path (often fails on Hyper-V GPU-PV)."
} else {
  try {
    $list = & nvidia-smi.exe -L 2>&1
    Log ("nvidia-smi -L => " + (($list | Out-String).Trim()))
    $q = & nvidia-smi.exe --query-gpu=index,name,driver_version,memory.total --format=csv 2>&1
    Log ("nvidia-smi query => " + (($q | Out-String).Trim()))
    if ($LASTEXITCODE -eq 0) { $hasSmi = $true }
  } catch {
    Log ("nvidia-smi error: " + $_.Exception.Message)
  }
}

Log "STEP2 package python"
if (-not $PyExe) {
  Log "FAIL: no .venv or tools/python/python.exe"
  Log "HINT: run 首次安装配置.bat first"
  Write-Host ""
  Write-Host "Need package python first. Run 首次安装配置.bat online, then retry."
  Write-Host ("log=" + $report)
  exit 1
}
Log ("python = " + $PyExe)

Log "STEP3 probe before repair"
$probe = Join-Path $PackRoot "_probe_cuda.py"
$before = -1
if (Test-Path $probe) {
  $out = & $PyExe $probe 2>&1 | Out-String
  Log $out.Trim()
  if ($out -match "ctranslate2_cuda_count\s*=\s*(\d+)") { $before = [int]$Matches[1] }
} else {
  $out = & $PyExe -c "import ctranslate2; print(ctranslate2.get_cuda_device_count())" 2>&1
  Log ("cuda_count raw = " + $out)
  if ("$out" -match "(\d+)") { $before = [int]$Matches[1] }
}

if ($before -gt 0) {
  Log "GPU already usable by python; still refresh wheels"
}

Log "STEP4 reinstall CUDA and ASR packages"
$pkgs = @(
  "nvidia-cublas-cu12",
  "nvidia-cudnn-cu12",
  "nvidia-cuda-runtime-cu12",
  "nvidia-cuda-nvrtc-cu12",
  "ctranslate2",
  "faster-whisper"
)
$mirrors = @(
  @("-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "--trusted-host", "pypi.tuna.tsinghua.edu.cn"),
  @("-i", "https://mirrors.aliyun.com/pypi/simple/", "--trusted-host", "mirrors.aliyun.com"),
  @()
)
$installOk = $false
foreach ($m in $mirrors) {
  $pipArgs = @("-m", "pip", "install", "--disable-pip-version-check", "--upgrade", "--force-reinstall") + $m + $pkgs
  Log ("pip " + ($pipArgs -join " "))
  & $PyExe @pipArgs
  if ($LASTEXITCODE -eq 0) {
    $installOk = $true
    break
  }
  Log ("pip failed exit=" + $LASTEXITCODE)
}
if (-not $installOk) {
  Log "FAIL: could not reinstall CUDA/ASR packages"
}

Log "STEP5 probe after repair"
$after = -1
if (Test-Path $probe) {
  $out2 = & $PyExe $probe 2>&1 | Out-String
  Log $out2.Trim()
  if ($out2 -match "ctranslate2_cuda_count\s*=\s*(\d+)") { $after = [int]$Matches[1] }
}

$device = "cpu"
$ctype = "int8"
$gpuIndex = 0
if ($hasSmi -and $after -gt 0) {
  $device = "cuda"
  $ctype = "float16"
} elseif ($hasSmi) {
  $device = "cuda"
  $ctype = "float16"
  Log "WARN: nvidia-smi OK but python cuda_count still 0"
  Log "HINT: Hyper-V often shows GPU in Device Manager but CUDA compute is unavailable. Prefer host OS, or Windows Server DDA full GPU assignment."
}

$manual = Join-Path $Tools "gpu_index.txt"
if (Test-Path $manual) {
  $raw = (Get-Content $manual -ErrorAction SilentlyContinue | Select-Object -First 1)
  if ("$raw" -match "(\d+)") { $gpuIndex = [int]$Matches[1] }
}

$hint = Join-Path $Tools "device_hint.txt"
$utf8NoBom = New-Object System.Text.UTF8Encoding $false
[System.IO.File]::WriteAllText($hint, ("device={0}`ncompute_type={1}`ngpu_index={2}`n" -f $device, $ctype, $gpuIndex), $utf8NoBom)
Log ("wrote device_hint device=$device compute=$ctype gpu_index=$gpuIndex")

$status = Join-Path $Logs "gpu_status.txt"
$statusText = @(
  ("time=" + (Get-Date).ToString("s")),
  ("device=" + $device),
  ("compute_type=" + $ctype),
  ("gpu_index=" + $gpuIndex),
  ("python=" + $PyExe),
  ("ctranslate2_cuda_count=" + $after),
  ("nvidia_smi=" + ($(if ($hasSmi) { "ok" } else { "missing" }))),
  "note=run_查看GPU状态_or_restart_app"
) -join "`n"
[System.IO.File]::WriteAllText($status, $statusText + "`n", $utf8NoBom)

Write-Host ""
Write-Host "============================================"
if ($after -gt 0) {
  Write-Host ("RESULT: GPU usable by Xiaomian (cuda_count=" + $after + ")")
  Write-Host "Next: 停止小面.bat -> 启动小面.bat -> upload a video"
  $code = 0
} elseif ($hasSmi) {
  Write-Host "RESULT: driver/nvidia-smi OK, but Python still cannot use CUDA"
  Write-Host "This is common on Hyper-V (display/GPU-PV != CUDA compute)"
  Write-Host "Recommend: run portable package on HOST, not VM"
  $code = 2
} else {
  Write-Host "RESULT: NVIDIA compute unavailable in this OS/VM"
  Write-Host "Device Manager showing RTX is not enough; need working nvidia-smi"
  $code = 1
}
Write-Host ("log=" + $report)
Write-Host "============================================"
exit $code
