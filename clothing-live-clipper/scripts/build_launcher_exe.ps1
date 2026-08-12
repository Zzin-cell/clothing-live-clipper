# Build sidecar launcher EXEs only (does not modify existing zip).
# Output: Desktop\小面CapCut-EXE启动版\
$ErrorActionPreference = "Stop"
$Root = Split-Path -Parent (Split-Path -Parent $MyInvocation.MyCommand.Path)
$Scripts = Join-Path $Root "scripts"
$Launcher = Join-Path $Scripts "xiaomian_launcher.py"
$Desk = [Environment]::GetFolderPath("Desktop")
$OutDir = Join-Path $Desk "小面CapCut-EXE启动版"
$Work = Join-Path $env:TEMP "xiaomian_launcher_build"
$Dist = Join-Path $Work "dist"

Write-Host "Root=$Root"
Write-Host "OutDir=$OutDir"

if (-not (Test-Path $Launcher)) { throw "missing $Launcher" }

# ensure pyinstaller
$py = (Get-Command python.exe -ErrorAction SilentlyContinue)
if (-not $py) { throw "python.exe not found on PATH" }
& python -m pip install -q pyinstaller
if ($LASTEXITCODE -ne 0) { throw "pip install pyinstaller failed" }

if (Test-Path $Work) { Remove-Item -Recurse -Force $Work }
New-Item -ItemType Directory -Force -Path $Work | Out-Null

# Build console EXE first (easier diagnostics); name 小面CapCut.exe
$specArgs = @(
  "-m", "PyInstaller",
  "--noconfirm",
  "--clean",
  "--onefile",
  "--console",
  "--name", "小面CapCut",
  "--distpath", $Dist,
  "--workpath", (Join-Path $Work "build"),
  "--specpath", $Work,
  $Launcher
)
Write-Host "Running PyInstaller..."
& python @specArgs
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }

$exe = Join-Path $Dist "小面CapCut.exe"
if (-not (Test-Path $exe)) { throw "exe not produced: $exe" }

# Assemble desktop folder WITHOUT touching 小面CapCut-便携版.zip
if (Test-Path $OutDir) {
  Write-Host "Refreshing $OutDir"
  # only remove launcher-related files if re-run; keep if user copied package
}
New-Item -ItemType Directory -Force -Path $OutDir | Out-Null
Copy-Item -Force $exe (Join-Path $OutDir "小面CapCut.exe")

# stop wrapper bat that calls same exe
$stopBat = @"
@echo off
chcp 65001 >nul
cd /d "%~dp0"
"%~dp0小面CapCut.exe" --stop
if errorlevel 1 pause
"@
Set-Content -Path (Join-Path $OutDir "停止小面.bat") -Value $stopBat -Encoding UTF8

$openBat = @"
@echo off
cd /d "%~dp0"
start "" "http://127.0.0.1:8787/"
"@
Set-Content -Path (Join-Path $OutDir "打开网页.bat") -Value $openBat -Encoding UTF8

$readme = @"
小面 CapCut · EXE 启动版（不替换原 zip）

【重要】
1. 本文件夹里的「小面CapCut.exe」只是启动器，不能单独拷走使用。
2. 需要旁边有完整便携包目录结构：pack\portable + clothing-live-clipper + models + tools …
3. 不会修改桌面上的「小面CapCut-便携版.zip」。

【推荐用法】
A. 把完整便携包解压/复制到本文件夹「里面」（与 小面CapCut.exe 同级），然后双击 EXE。
B. 或把 EXE 复制进「小面CapCut-便携版」根目录（与 启动小面.bat 同级）再双击。

【命令】
  小面CapCut.exe           安装修复 + 启动 + 打开浏览器
  小面CapCut.exe --stop    停止服务
  小面CapCut.exe --open    只开网页
  小面CapCut.exe --no-browser  启动但不打开浏览器

建议路径：D:\xiaomian\
"@
Set-Content -Path (Join-Path $OutDir "先读我-EXE.txt") -Value $readme -Encoding UTF8

Write-Host "DONE exe -> $OutDir"
Get-ChildItem $OutDir | ForEach-Object { Write-Host (" - " + $_.Name) }
