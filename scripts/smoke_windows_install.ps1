$ErrorActionPreference = "Stop"
$Installer = (Resolve-Path $args[0]).Path
$SentinelDir = Join-Path $env:APPDATA "AetherChat"
$Sentinel = Join-Path $SentinelDir "release-smoke-sentinel.txt"
New-Item -ItemType Directory -Force $SentinelDir | Out-Null
Set-Content -NoNewline $Sentinel "preserve-user-data"

$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\AetherChat"
$Arguments = "/S /D=$InstallDir"
$First = Start-Process -FilePath $Installer -ArgumentList $Arguments -Wait -PassThru
if ($First.ExitCode -ne 0) { throw "Silent install failed with $($First.ExitCode)" }
if (-not (Test-Path (Join-Path $InstallDir "AetherChat.exe"))) { throw "Installed executable is missing" }

$Upgrade = Start-Process -FilePath $Installer -ArgumentList $Arguments -Wait -PassThru
if ($Upgrade.ExitCode -ne 0) { throw "Silent upgrade failed with $($Upgrade.ExitCode)" }
if ((Get-Content -Raw $Sentinel) -ne "preserve-user-data") { throw "Upgrade modified user data" }

$Uninstaller = Get-ChildItem $InstallDir -Filter "Uninstall*.exe" | Select-Object -First 1
if (-not $Uninstaller) { throw "Uninstaller is missing" }
$Removed = Start-Process -FilePath $Uninstaller.FullName -ArgumentList "/S" -Wait -PassThru
if ($Removed.ExitCode -ne 0) { throw "Silent uninstall failed with $($Removed.ExitCode)" }
if ((Get-Content -Raw $Sentinel) -ne "preserve-user-data") { throw "Uninstall removed user data" }
Write-Host "Windows install/upgrade/uninstall smoke test passed"
