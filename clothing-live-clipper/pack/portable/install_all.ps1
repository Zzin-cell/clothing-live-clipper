# First-time setup for 小面 CapCut portable package (Windows, little skill required)
$ErrorActionPreference = "Continue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8

$PackRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
# Support both layouts:
#   AppRoot/pack/portable/*.ps1  → AppRoot is ../..
#   AppRoot/*.ps1 (or AppRoot/portable) → AppRoot is ..
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
if (-not (Test-Path $Req)) {
  $Req = Join-Path $AppRoot "requirements.txt"
}

New-Item -ItemType Directory -Force -Path $Logs, $FfmpegBin, $ModelsRoot | Out-Null
$logFile = Join-Path $Logs ("install_{0:yyyyMMdd_HHmmss}.log" -f (Get-Date))
function Log($m) {
  $line = "[{0}] {1}" -f (Get-Date -Format "HH:mm:ss"), $m
  Write-Host $line
  Add-Content -Path $logFile -Value $line -Encoding UTF8
}

Log "AppRoot=$AppRoot"

# ---- 1) Python ----
$py = $null
foreach ($c in @("py -3.12", "py -3.11", "py -3", "python")) {
  try {
    $v = Invoke-Expression "$c -c `"import sys; print(sys.version)`"" 2>$null
    if ($LASTEXITCODE -eq 0 -and $v) { $py = $c; Log "Found Python via: $c ($v)"; break }
  } catch {}
}
if (-not $py) {
  Log "ERROR: 未检测到 Python 3.11+。请先安装 Python 并勾选 Add to PATH。"
  Log "下载: https://www.python.org/downloads/windows/"
  exit 1
}

# Create venv
if (-not (Test-Path $PyExe)) {
  Log "创建虚拟环境 .venv ..."
  if ($py -like "py *") {
    Invoke-Expression "$py -m venv `"$PyVenv`""
  } else {
    & python -m venv $PyVenv
  }
}
if (-not (Test-Path $PyExe)) {
  Log "ERROR: 虚拟环境创建失败"
  exit 1
}
Log "venv OK: $PyExe"

# ---- 2) pip packages ----
Log "升级 pip ..."
& $PyExe -m pip install -U pip setuptools wheel 2>&1 | Out-Null
if (Test-Path $Req) {
  Log "安装 requirements.txt ..."
  & $PyExe -m pip install -r $Req
  if ($LASTEXITCODE -ne 0) { Log "WARN: requirements 部分失败，继续尝试核心包" }
}
Log "安装 faster-whisper / uvicorn ..."
& $PyExe -m pip install "faster-whisper>=1.0" "uvicorn[standard]>=0.27" fastapi python-multipart pydantic python-dotenv httpx
if ($LASTEXITCODE -ne 0) {
  Log "ERROR: 核心依赖安装失败"
  exit 1
}

# optional CUDA wheels (best effort)
Log "尝试安装 NVIDIA CUDA 运行库（无显卡会跳过失败）..."
& $PyExe -m pip install nvidia-cublas-cu12 nvidia-cudnn-cu12 2>&1 | Out-Null

# ---- 3) ffmpeg portable ----
$ff = Join-Path $FfmpegBin "ffmpeg.exe"
if (-not (Test-Path $ff)) {
  Log "下载便携 ffmpeg ..."
  $zip = Join-Path $env:TEMP "ffmpeg-essentials-xm.zip"
  $urls = @(
    "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
    "https://github.com/GyanD/codexffmpeg/releases/download/7.1.1/ffmpeg-7.1.1-essentials_build.zip"
  )
  $ok = $false
  foreach ($u in $urls) {
    try {
      Log "  GET $u"
      Invoke-WebRequest -Uri $u -OutFile $zip -UseBasicParsing
      $ok = $true
      break
    } catch {
      Log "  fail: $($_.Exception.Message)"
    }
  }
  if (-not $ok) {
    Log "ERROR: ffmpeg 下载失败，请联网后重试"
    exit 1
  }
  $extract = Join-Path $Tools "ffmpeg\_extract"
  if (Test-Path $extract) { Remove-Item -Recurse -Force $extract }
  Expand-Archive -Path $zip -DestinationPath $extract -Force
  $found = Get-ChildItem -Path $extract -Recurse -Filter ffmpeg.exe | Select-Object -First 1
  if (-not $found) {
    Log "ERROR: 压缩包内找不到 ffmpeg.exe"
    exit 1
  }
  Copy-Item -Force $found.FullName (Join-Path $FfmpegBin "ffmpeg.exe")
  $probe = Join-Path $found.Directory.FullName "ffprobe.exe"
  if (Test-Path $probe) { Copy-Item -Force $probe (Join-Path $FfmpegBin "ffprobe.exe") }
  Log "ffmpeg OK"
} else {
  Log "ffmpeg 已存在"
}

# ---- 4) whisper model (small by default for first-run size) ----
$modelDir = Join-Path $ModelsRoot "whisper-small"
$modelBin = Join-Path $modelDir "model.bin"
if (-not (Test-Path $modelBin)) {
  Log "下载语音模型 whisper-small（首次较慢，需联网）..."
  New-Item -ItemType Directory -Force -Path $modelDir | Out-Null
  $files = @("config.json", "tokenizer.json", "vocabulary.txt", "model.bin")
  $mirrors = @("https://hf-mirror.com", "https://huggingface.co")
  $repo = "Systran/faster-whisper-small"
  foreach ($name in $files) {
    $dest = Join-Path $modelDir $name
    if ((Test-Path $dest) -and ((Get-Item $dest).Length -gt 1000)) {
      Log "  skip $name"
      continue
    }
    $got = $false
    foreach ($m in $mirrors) {
      $url = "$m/$repo/resolve/main/$name"
      try {
        Log "  GET $name from $m"
        $tmp = "$dest.part"
        # use curl if present for big files
        $curl = Get-Command curl.exe -ErrorAction SilentlyContinue
        if ($curl) {
          & curl.exe -L --fail --retry 5 --retry-delay 2 --connect-timeout 30 -o $tmp $url
          if ($LASTEXITCODE -eq 0 -and (Test-Path $tmp)) {
            Move-Item -Force $tmp $dest
            $got = $true
            break
          }
        } else {
          Invoke-WebRequest -Uri $url -OutFile $tmp -UseBasicParsing
          Move-Item -Force $tmp $dest
          $got = $true
          break
        }
      } catch {
        Log "  fail $m : $($_.Exception.Message)"
      }
    }
    if (-not $got) {
      Log "ERROR: 模型文件下载失败 $name"
      exit 1
    }
  }
  Log "模型 OK: $modelDir"
} else {
  Log "模型已存在: $modelDir"
}

# Prefer medium if user already placed it
if (Test-Path (Join-Path $ModelsRoot "whisper-medium\model.bin")) {
  Log "检测到 whisper-medium，启动时将优先使用"
}

# ---- 5) Desktop shortcut ----
try {
  $desktop = [Environment]::GetFolderPath("Desktop")
  # Prefer package-root launcher if present
  $startBat = Join-Path $AppRoot "启动小面.bat"
  if (-not (Test-Path $startBat)) { $startBat = Join-Path $PackRoot "启动小面.bat" }
  $lnkPath = Join-Path $desktop "小面CapCut.lnk"
  $w = New-Object -ComObject WScript.Shell
  $sc = $w.CreateShortcut($lnkPath)
  $sc.TargetPath = $startBat
  $sc.WorkingDirectory = $AppRoot
  $sc.WindowStyle = 7  # minimized
  $sc.Description = "小面 CapCut 服装切片"
  $sc.Save()
  Log "桌面快捷方式: $lnkPath"
} catch {
  Log "WARN: 桌面快捷方式创建失败: $($_.Exception.Message)"
}

# ---- 6) marker ----
$marker = Join-Path $Tools "install_ok.txt"
@"
installed_at=$([DateTime]::Now.ToString('s'))
app_root=$AppRoot
python=$PyExe
ffmpeg=$ff
model=$modelDir
"@ | Set-Content -Path $marker -Encoding UTF8

Log "INSTALL_OK"
Write-Host ""
Write-Host "下一步：双击「启动小面.bat」→ 浏览器打开后，在右侧填写 LLM API → 保存并启用。"
exit 0
