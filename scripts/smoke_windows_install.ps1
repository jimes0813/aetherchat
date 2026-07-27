$ErrorActionPreference = "Stop"
$Installer = (Resolve-Path $args[0]).Path
$SentinelDir = Join-Path $env:APPDATA "AetherChat"
$Sentinel = Join-Path $SentinelDir "release-smoke-sentinel.txt"

$InstallDir = Join-Path $env:LOCALAPPDATA "Programs\AetherChat"

function Stop-InstalledProcesses {
  param([Parameter(Mandatory)][string]$Root)

  $Deadline = [DateTime]::UtcNow.AddSeconds(20)
  $QuietPolls = 0
  do {
    $Processes = @(
      Get-CimInstance Win32_Process |
        Where-Object {
          $_.ExecutablePath -and
          $_.ExecutablePath.StartsWith($Root, [StringComparison]::OrdinalIgnoreCase)
        }
    )
    if ($Processes.Count -eq 0) {
      $QuietPolls += 1
      if ($QuietPolls -ge 4) { return }
    } else {
      $QuietPolls = 0
      $Processes | ForEach-Object {
        Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue
      }
    }
    Start-Sleep -Milliseconds 500
  } while ([DateTime]::UtcNow -lt $Deadline)

  throw "Installed processes did not stop before the smoke-test deadline"
}

$Arguments = "/S /D=$InstallDir"
$First = Start-Process -FilePath $Installer -ArgumentList $Arguments -Wait -PassThru
if ($First.ExitCode -ne 0) { throw "Silent install failed with $($First.ExitCode)" }
if (-not (Test-Path (Join-Path $InstallDir "AetherChat.exe"))) { throw "Installed executable is missing" }
Stop-InstalledProcesses -Root $InstallDir

# Create user data only after the fresh-install check so later failures are
# attributable specifically to upgrade or uninstall behavior.
New-Item -ItemType Directory -Force -Path $SentinelDir | Out-Null
Set-Content -NoNewline -Path $Sentinel -Value "preserve-user-data"
if ((Get-Content -Raw $Sentinel) -ne "preserve-user-data") {
  throw "Unable to create user-data sentinel"
}

$UpgradeArguments = "--updated /S /D=$InstallDir"
$Upgrade = Start-Process -FilePath $Installer -ArgumentList $UpgradeArguments -Wait -PassThru
if ($Upgrade.ExitCode -ne 0) { throw "Silent upgrade failed with $($Upgrade.ExitCode)" }
Stop-InstalledProcesses -Root $InstallDir
if (-not (Test-Path $Sentinel) -or (Get-Content -Raw $Sentinel) -ne "preserve-user-data") {
  throw "Upgrade modified user data at $Sentinel"
}

$Uninstaller = Get-ChildItem $InstallDir -Filter "Uninstall*.exe" | Select-Object -First 1
if (-not $Uninstaller) { throw "Uninstaller is missing" }
$Removed = Start-Process -FilePath $Uninstaller.FullName -ArgumentList "/S" -Wait -PassThru
if ($Removed.ExitCode -ne 0) { throw "Silent uninstall failed with $($Removed.ExitCode)" }
if (-not (Test-Path $Sentinel) -or (Get-Content -Raw $Sentinel) -ne "preserve-user-data") {
  throw "Uninstall removed user data at $Sentinel"
}
Write-Host "Windows install/upgrade/uninstall smoke test passed"
