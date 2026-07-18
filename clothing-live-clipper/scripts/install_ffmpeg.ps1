$ErrorActionPreference = "Stop"

$destRoot = Join-Path $env:LOCALAPPDATA "ffmpeg"
$zipPath = Join-Path $env:TEMP "ffmpeg-release-essentials.zip"
# BtbN builds mirror often works; try gyan essentials first then fallback
$urls = @(
  "https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip",
  "https://github.com/GyanD/codexffmpeg/releases/download/7.1.1/ffmpeg-7.1.1-essentials_build.zip"
)

New-Item -ItemType Directory -Force -Path $destRoot | Out-Null

$downloaded = $false
foreach ($url in $urls) {
  try {
    Write-Host "Downloading: $url"
    Invoke-WebRequest -Uri $url -OutFile $zipPath -UseBasicParsing
    $downloaded = $true
    break
  } catch {
    Write-Host "Download failed: $($_.Exception.Message)"
  }
}

if (-not $downloaded) {
  throw "Could not download ffmpeg zip from known mirrors"
}

Write-Host "Extracting to $destRoot"
if (Test-Path (Join-Path $destRoot "_extract")) {
  Remove-Item -Recurse -Force (Join-Path $destRoot "_extract")
}
Expand-Archive -Path $zipPath -DestinationPath (Join-Path $destRoot "_extract") -Force

$ff = Get-ChildItem -Path (Join-Path $destRoot "_extract") -Recurse -Filter ffmpeg.exe |
  Select-Object -First 1
if (-not $ff) { throw "ffmpeg.exe not found in archive" }

$binDir = $ff.Directory.FullName
Write-Host "Found ffmpeg at $binDir"

# Stable junction/copy location
$stableBin = Join-Path $destRoot "bin"
if (Test-Path $stableBin) { Remove-Item -Recurse -Force $stableBin }
New-Item -ItemType Directory -Force -Path $stableBin | Out-Null
Copy-Item -Force (Join-Path $binDir "ffmpeg.exe") (Join-Path $stableBin "ffmpeg.exe")
if (Test-Path (Join-Path $binDir "ffprobe.exe")) {
  Copy-Item -Force (Join-Path $binDir "ffprobe.exe") (Join-Path $stableBin "ffprobe.exe")
}
if (Test-Path (Join-Path $binDir "ffplay.exe")) {
  Copy-Item -Force (Join-Path $binDir "ffplay.exe") (Join-Path $stableBin "ffplay.exe")
}

# User PATH
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not $userPath) { $userPath = "" }
$parts = $userPath -split ";" | Where-Object { $_ -and ($_.Trim() -ne "") }
if ($parts -notcontains $stableBin) {
  $newPath = ($parts + $stableBin) -join ";"
  [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
  Write-Host "Added to user PATH: $stableBin"
} else {
  Write-Host "Already on user PATH: $stableBin"
}

# Current process PATH
if ($env:Path -notlike "*$stableBin*") {
  $env:Path = "$stableBin;$env:Path"
}

Write-Host "Verifying..."
& (Join-Path $stableBin "ffmpeg.exe") -version | Select-Object -First 1
Write-Host "INSTALL_OK $stableBin"
