# Build AgentPresence-Windows-Setup.exe, the double-click Windows installer.
# Runs on Windows with Python 3 and Inno Setup 6 (iscc) installed.
#
#   powershell -File windows\build_installer.ps1 [-Out dist]
#
# The installer carries its own Python (the embeddable distribution) and the
# psutil wheel, so it needs nothing else on the PC and works offline.
param([string]$Out = "dist")
$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Root = Split-Path $PSScriptRoot -Parent
$PyVer = "3.12.10"  # last 3.12 release with an embeddable zip
$Ver = (Select-String -Path "$Root\pyproject.toml" -Pattern '^version = "(.*)"').Matches[0].Groups[1].Value
if (-not $Ver) { throw "no version in pyproject.toml" }
$Build = Join-Path $Root "build\windows"
$App = Join-Path $Build "app"
$Py = Join-Path $App "python"
if (Test-Path $Build) { Remove-Item $Build -Recurse -Force }
New-Item -ItemType Directory -Force -Path $Py, (Join-Path $Root $Out) | Out-Null

Write-Host "==> Python $PyVer (embeddable, 64-bit)"
$Zip = Join-Path $Build "python-embed.zip"
Invoke-WebRequest "https://www.python.org/ftp/python/$PyVer/python-$PyVer-embed-amd64.zip" -OutFile $Zip -UseBasicParsing
Expand-Archive $Zip -DestinationPath $Py
$Tag = ($PyVer -split '\.')[0..1] -join ''
# Let it find psutil in Lib\site-packages.
Set-Content -Path (Join-Path $Py "python$Tag._pth") -Encoding ascii -Value @("python$Tag.zip", ".", "Lib\site-packages", "import site")

Write-Host "==> psutil"
$Wheels = Join-Path $Build "wheels"
python -m pip download -q --disable-pip-version-check psutil --only-binary=:all: --no-deps `
  --platform win_amd64 --python-version ($PyVer -replace '\.\d+$', '') --implementation cp -d $Wheels
if ($LASTEXITCODE) { throw "pip download failed" }
$Whl = Get-ChildItem $Wheels -Filter "psutil-*.whl" | Select-Object -First 1
Copy-Item $Whl.FullName (Join-Path $Build "psutil.zip")
Expand-Archive (Join-Path $Build "psutil.zip") -DestinationPath (Join-Path $Py "Lib\site-packages")
& (Join-Path $Py "python.exe") -c "import psutil, sys; print('psutil', psutil.__version__, 'on', sys.version.split()[0])"
if ($LASTEXITCODE) { throw "bundled Python can't import psutil" }

Write-Host "==> app files"
Copy-Item "$Root\ai_presence.py", "$Root\LICENSE" $App
Set-Content -Path (Join-Path $App "agent-presence.cmd") -Encoding ascii `
  -Value '@"%~dp0python\python.exe" "%~dp0ai_presence.py" %*'

Write-Host "==> icon"
python -m pip install -q --disable-pip-version-check pillow
python -c "from PIL import Image; Image.open(r'$Root\macos\AppIcon.png').convert('RGBA').save(r'$Build\AgentPresence.ico', sizes=[(16,16),(24,24),(32,32),(48,48),(64,64),(128,128),(256,256)])"
if ($LASTEXITCODE) { throw "icon conversion failed" }

Write-Host "==> installer $Ver"
$Iscc = (Get-Command iscc -ErrorAction SilentlyContinue).Source
if (-not $Iscc) { $Iscc = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe" }
& $Iscc /Q "/DAppVersion=$Ver" "/DBuildDir=$Build" "/DOutDir=$(Join-Path $Root $Out)" (Join-Path $PSScriptRoot "AgentPresence.iss")
if ($LASTEXITCODE) { throw "iscc failed" }
Get-Item (Join-Path $Root "$Out\AgentPresence-Windows-Setup.exe") | Format-Table Name, Length
