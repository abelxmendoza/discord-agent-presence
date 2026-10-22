# One-line install (PowerShell):
#   irm https://raw.githubusercontent.com/abelxmendoza/discord-agent-presence/main/install.ps1 | iex
$ErrorActionPreference = "Stop"
$Dest = if ($env:AGENT_PRESENCE_HOME) { $env:AGENT_PRESENCE_HOME } else { Join-Path $env:LOCALAPPDATA "discord-agent-presence" }
$Zip  = "https://github.com/abelxmendoza/discord-agent-presence/archive/refs/heads/main.zip"

# Find Python 3.9+ (the py launcher first, then python on PATH)
$Py = $null
foreach ($c in @("py -3", "python")) {
  try { & ([scriptblock]::Create("$c -c `"import sys; assert sys.version_info >= (3, 9)`"")) 2>$null; if ($LASTEXITCODE -eq 0) { $Py = $c; break } } catch {}
}
if (-not $Py) { Write-Host "Python 3.9+ is required: https://www.python.org/downloads/ (tick 'Add to PATH')"; exit 1 }

# Download the latest code (no git needed)
$Tmp = Join-Path $env:TEMP "agent-presence-$([guid]::NewGuid()).zip"
Invoke-WebRequest $Zip -OutFile $Tmp -UseBasicParsing
$Unz = Join-Path $env:TEMP "agent-presence-src"
if (Test-Path $Unz) { Remove-Item $Unz -Recurse -Force }
Expand-Archive $Tmp -DestinationPath $Unz
New-Item -ItemType Directory -Force -Path $Dest | Out-Null
Copy-Item (Join-Path $Unz "discord-agent-presence-main\*") $Dest -Recurse -Force
Remove-Item $Tmp, $Unz -Recurse -Force

# Private virtualenv with psutil
& ([scriptblock]::Create("$Py -m venv `"$Dest\.venv`""))
& "$Dest\.venv\Scripts\python.exe" -m pip install -q --upgrade pip psutil

# agent-presence command
Set-Content -Path (Join-Path $Dest "agent-presence.cmd") -Value "@`"%~dp0.venv\Scripts\python.exe`" `"%~dp0ai_presence.py`" %*"
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (($UserPath -split ";") -notcontains $Dest) {
  [Environment]::SetEnvironmentVariable("Path", "$UserPath;$Dest", "User")
  Write-Host "Added $Dest to your PATH (open a new terminal to use agent-presence)."
}

& "$Dest\.venv\Scripts\python.exe" "$Dest\ai_presence.py" setup
