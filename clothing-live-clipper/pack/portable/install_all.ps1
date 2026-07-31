# First-time + repair installer for 小面 CapCut portable package
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
function SoftFail([string]$m) { Log "WARN: $m" }
function HardFail([string]$m) {
  Log "ERROR: $m"
  @"
time=$([DateTime]::Now.ToString('s'))
error=$m
log=$logFile
hint=请确认：1) 已安装 Python3.11+ 并勾选 PATH 2) 可上网 3) 杀毒未拦截
"@ | Set-Content -Path $fixFile -Encoding UTF8
  exit 1
}

function Invoke-Retry {
  param(
    [scriptblock]$Action,
    [string]$Name,
    [int]$Times = 3,
    [int]$DelaySec = 2
  )
  for ($i = 1; $i -le $Times; $i++) {
    try {
      Log "$Name (尝试 $i/$Times)..."
      & $Action
      if ($LASTEXITCODE -eq 0 -or $null -eq $LASTEXITCODE) { return $true }
      SoftFail "$Name 退出码 $LASTEXITCODE"
    } catch {
      SoftFail "$Name 异常: $($_.Exception.Message)"
    }
    Start-Sleep -Seconds $DelaySec
  }
  return $false
}

function Download-File {
  param([string[]]$Urls, [string]$Dest)
  $tmp = "$Dest.part"
  foreach ($url in $Urls) {
    try {
      Log "  下载: $url"
      $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
      if ($curl) {
        & curl.exe -L --fail --retry 5 --retry-delay 2 --connect-timeout 30 --max-time 3600 -o $tmp $url
        if ($LASTEXITCODE -eq 0 -and (Test-Path $tmp) -and ((Get-Item $tmp).Length -gt 1000)) {
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
      SoftFail "下载失败 $($_.Exception.Message)"
    } finally {
      if (Test-Path $tmp) { Remove-Item $tmp -Force -ErrorAction SilentlyContinue }
    }
  }
  return $false
}

function Test-PythonLauncher([string]$cmd) {
  try {
    $code = 'import sys; v=sys.version_info; print("%d.%d"% (v.major,v.minor)); raise SystemExit(0 if v.major==3 and v.minor>=10 else 2)'
    $out = & cmd.exe /c "$cmd -c `"$code`"" 2>$null
    if ($LASTEXITCODE -eq 0 -and $out) { return $out.ToString().Trim() }
  } catch {}
  return $null
}

function Ensure-Python {
  $candidates = @(
    "py -3.12", "py -3.11", "py -3.10", "py -3",
    "python", "python3"
  )
  foreach ($c in $candidates) {
    $ver = Test-PythonLauncher $c
    if ($ver) {
      Log "发现 Python $ver via [$c]"
      return $c
    }
  }
  # try common install paths
  $paths = @(
    "$env:LOCALAPPDATA\Programs\Python\Python312\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python311\python.exe",
    "$env:LOCALAPPDATA\Programs\Python\Python310\python.exe",
    "C:\Python312\python.exe",
    "C:\Python311\python.exe"
  )
  foreach ($p in $paths) {
    if (Test-Path $p) {
      Log "发现 Python 路径: $p"
      return "`"$p`""
    }
  }
  return $null
}

function Ensure-Venv([string]$pyCmd) {
  $needRebuild = $false
  if (-not (Test-Path $PyExe)) { $needRebuild = $true }
  else {
    try {
      & $PyExe -c "import sys; print(sys.version)" | Out-Null
      if ($LASTEXITCODE -ne 0) { $needRebuild = $true }
    } catch { $needRebuild = $true }
  }
  if ($needRebuild) {
    Log "创建/修复虚拟环境 .venv ..."
    if (Test-Path $PyVenv) {
      SoftFail "删除损坏的 .venv 后重建"
      Remove-Item -Recurse -Force $PyVenv -ErrorAction SilentlyContinue
    }
    if ($pyCmd -like "py *") {
      Invoke-Expression "$pyCmd -m venv `"$PyVenv`""
    } else {
      # quoted path or python
      Invoke-Expression "& $pyCmd -m venv `"$PyVenv`""
    }
  }
  if (-not (Test-Path $PyExe)) { return $false }
  # ensure pip
  & $PyExe -m ensurepip --upgrade 2>&1 | Out-Null
  return $true
}

function Pip-Install([string[]]$Args) {
  $mirrors = @(
    @(),
    @("-i", "https://pypi.tuna.tsinghua.edu.cn/simple", "--trusted-host", "pypi.tuna.tsinghua.edu.cn"),
    @("-i", "https://mirrors.aliyun.com/pypi/simple/", "--trusted-host", "mirrors.aliyun.com"),
    @("-i", "https://pypi.org/simple")
  )
  foreach ($m in $mirrors) {
    $all = @("-m", "pip", "install", "--disable-pip-version-check") + $m + $Args
    Log "pip install $($Args -join ' ') $($m -join ' ')"
    & $PyExe @all
    if ($LASTEXITCODE -eq 0) { return $true }
    SoftFail "pip 失败 exit=$LASTEXITCODE，切换镜像重试"
  }
  return $false
}

function Ensure-Deps {
  Log "升级 pip/setuptools/wheel..."
  [void](Pip-Install @("-U", "pip", "setuptools", "wheel"))
  if (Test-Path $Req) {
    Log "安装 requirements.txt"
    [void](Pip-Install @("-r", $Req))
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
  if (-not (Pip-Install $core)) {
    SoftFail "一次装失败，逐个安装核心包"
    foreach ($p in $core) {
      if (-not (Pip-Install @($p))) { SoftFail "包失败: $p" }
    }
  }
  # verify imports
  $check = & $PyExe -c "import fastapi,uvicorn,pydantic; import faster_whisper; print('IMPORT_OK')" 2>&1
  if ("$check" -notmatch "IMPORT_OK") {
    SoftFail "导入校验失败: $check — 尝试修复"
    [void](Pip-Install @("--force-reinstall", "--no-cache-dir") + $core)
    $check2 = & $PyExe -c "import fastapi,uvicorn; import faster_whisper; print('IMPORT_OK')" 2>&1
    if ("$check2" -notmatch "IMPORT_OK") { return $false }
  }
  # optional CUDA libs
  Log "尝试 CUDA 运行库（无 GPU 可忽略失败）"
  [void](Pip-Install @("nvidia-cublas-cu12", "nvidia-cudnn-cu12"))
  return $true
}

function Ensure-Ffmpeg {
  $ff = Join-Path $FfmpegBin "ffmpeg.exe"
  if (Test-Path $ff) {
    try {
      $v = & $ff -version 2>&1 | Select-Object -First 1
      Log "ffmpeg 已存在: $v"
      return $true
    } catch {
      SoftFail "ffmpeg 损坏，重新下载"
      Remove-Item $ff -Force -ErrorAction SilentlyContinue
    }
  }
  # copy from local install if present
  $local = Join-Path $env:LOCALAPPDATA "ffmpeg\bin\ffmpeg.exe"
  if (Test-Path $local) {
    Log "从本机复制 ffmpeg"
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
    SoftFail "解压失败，重试 Expand-Archive"
    Start-Sleep 1
    Expand-Archive -Path $zip -DestinationPath $extract -Force
  }
  $found = Get-ChildItem -Path $extract -Recurse -Filter ffmpeg.exe -ErrorAction SilentlyContinue | Select-Object -First 1
  if (-not $found) { return $false }
  Copy-Item -Force $found.FullName $ff
  $probe = Join-Path $found.Directory.FullName "ffprobe.exe"
  if (Test-Path $probe) { Copy-Item -Force $probe (Join-Path $FfmpegBin "ffprobe.exe") }
  Log "ffmpeg 安装成功"
  return (Test-Path $ff)
}

function Ensure-Model {
  # prefer existing medium
  foreach ($n in @("whisper-medium", "whisper-small", "whisper-tiny")) {
    $bin = Join-Path $ModelsRoot "$n\model.bin"
    if ((Test-Path $bin) -and ((Get-Item $bin).Length -gt 10MB)) {
      Log "模型已存在: $n"
      return $true
    }
  }
  $name = "whisper-small"
  $repo = "Systran/faster-whisper-small"
  $modelDir = Join-Path $ModelsRoot $name
  New-Item -ItemType Directory -Force -Path $modelDir | Out-Null
  $files = @("config.json", "tokenizer.json", "vocabulary.txt", "model.bin")
  $mirrors = @("https://hf-mirror.com", "https://huggingface.co", "https://hf-mirror.com")
  foreach ($f in $files) {
    $dest = Join-Path $modelDir $f
    if ((Test-Path $dest) -and ((Get-Item $dest).Length -gt 1000)) {
      Log "  skip $f"
      continue
    }
    $urls = @()
    foreach ($m in $mirrors) { $urls += "$m/$repo/resolve/main/$f" }
    $ok = $false
    for ($t = 1; $t -le 3 -and -not $ok; $t++) {
      Log "  下载 $f 尝试 $t"
      $ok = Download-File -Urls $urls -Dest $dest
      if (-not $ok) { Start-Sleep -Seconds (2 * $t) }
    }
    if (-not $ok) {
      SoftFail "模型文件失败 $f，尝试 tiny 兜底"
      # tiny fallback
      $name = "whisper-tiny"
      $repo = "Systran/faster-whisper-tiny"
      $modelDir = Join-Path $ModelsRoot $name
      New-Item -ItemType Directory -Force -Path $modelDir | Out-Null
      $dest2 = Join-Path $modelDir $f
      $urls2 = @()
      foreach ($m in $mirrors) { $urls2 += "$m/$repo/resolve/main/$f" }
      if (-not (Download-File -Urls $urls2 -Dest $dest2)) { return $false }
    }
  }
  $finalBin = $null
  foreach ($n in @("whisper-small", "whisper-tiny", "whisper-medium")) {
    $b = Join-Path $ModelsRoot "$n\model.bin"
    if (Test-Path $b) { $finalBin = $b; break }
  }
  return [bool]$finalBin
}

function Ensure-DesktopShortcut {
  try {
    $desktop = [Environment]::GetFolderPath("Desktop")
    $startBat = Join-Path $AppRoot "启动小面.bat"
    if (-not (Test-Path $startBat)) { $startBat = Join-Path $PackRoot "启动小面.bat" }
    $lnkPath = Join-Path $desktop "小面CapCut.lnk"
    $w = New-Object -ComObject WScript.Shell
    $sc = $w.CreateShortcut($lnkPath)
    $sc.TargetPath = $startBat
    $sc.WorkingDirectory = $AppRoot
    $sc.WindowStyle = 7
    $sc.Description = "小面 CapCut 服装切片（自动安装并启动）"
    $sc.Save()
    Log "桌面快捷方式: $lnkPath"
  } catch {
    SoftFail "桌面快捷方式失败: $($_.Exception.Message)"
  }
}

function Write-DeviceHint {
  $hint = Join-Path $Tools "device_hint.txt"
  $device = "cuda"
  $ctype = "float16"
  try {
    $r = & $PyExe -c "import ctranslate2; print('ct2_ok')" 2>&1
    # probe nvidia-smi
    $smi = Get-Command nvidia-smi.exe -ErrorAction SilentlyContinue
    if (-not $smi) {
      $device = "cpu"
      $ctype = "int8"
      Log "未检测到 nvidia-smi，默认 CPU 听写"
    } else {
      $q = & nvidia-smi.exe -L 2>&1
      if ($LASTEXITCODE -ne 0 -or "$q" -match "failed|not found|unable") {
        $device = "cpu"; $ctype = "int8"
        Log "nvidia-smi 异常，默认 CPU"
      } else {
        Log "检测到 GPU: $($q | Select-Object -First 1)"
      }
    }
  } catch {
    $device = "cpu"; $ctype = "int8"
  }
  "device=$device`ncompute_type=$ctype`n" | Set-Content -Path $hint -Encoding UTF8
}

# ---------------- main ----------------
Log "AppRoot=$AppRoot"
Log "自动排查并修复安装..."

$pyCmd = Ensure-Python
if (-not $pyCmd) {
  HardFail "未检测到 Python 3.10+。请安装 Python 并勾选 Add python.exe to PATH。下载: https://www.python.org/downloads/windows/"
}

if (-not (Ensure-Venv $pyCmd)) {
  SoftFail "venv 第一次失败，重试"
  Start-Sleep 1
  if (-not (Ensure-Venv $pyCmd)) { HardFail "虚拟环境创建失败" }
}
Log "venv OK: $PyExe"

if (-not (Ensure-Deps)) {
  SoftFail "依赖异常，重建 venv 后再装一次"
  Remove-Item -Recurse -Force $PyVenv -ErrorAction SilentlyContinue
  if (-not (Ensure-Venv $pyCmd)) { HardFail "重建 venv 失败" }
  if (-not (Ensure-Deps)) { HardFail "Python 依赖安装失败（已换镜像重试）。请检查网络/代理/杀毒" }
}
Log "依赖 OK"

if (-not (Ensure-Ffmpeg)) {
  SoftFail "ffmpeg 第一次失败，重试"
  if (-not (Ensure-Ffmpeg)) { HardFail "ffmpeg 安装失败，请联网后重试" }
}
Log "ffmpeg OK"

if (-not (Ensure-Model)) {
  SoftFail "模型下载失败，最后再试 tiny"
  if (-not (Ensure-Model)) { HardFail "语音模型下载失败，请联网或手动把 model 放到 models\whisper-small\" }
}
Log "模型 OK"

Ensure-DesktopShortcut
Write-DeviceHint

# final health
$health = & $PyExe -c "import fastapi,uvicorn,faster_whisper; print('OK')" 2>&1
if ("$health" -notmatch "OK") { HardFail "最终健康检查失败: $health" }
if (-not (Test-Path (Join-Path $FfmpegBin "ffmpeg.exe"))) { HardFail "最终检查：缺 ffmpeg" }

$marker = Join-Path $Tools "install_ok.txt"
@"
installed_at=$([DateTime]::Now.ToString('s'))
app_root=$AppRoot
python=$PyExe
ffmpeg=$(Join-Path $FfmpegBin 'ffmpeg.exe')
log=$logFile
"@ | Set-Content -Path $marker -Encoding UTF8

Log "INSTALL_OK"
Write-Host ""
Write-Host "安装/修复完成。可直接使用「启动小面.bat」。"
Write-Host "下一步：浏览器右侧填写 LLM API → 保存并启用。"
exit 0
