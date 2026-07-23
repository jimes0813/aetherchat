$ErrorActionPreference = "Stop"

$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$ProjectDir = Split-Path -Parent $ScriptDir
$BackendDir = Join-Path $ProjectDir "backend"
$Python = if ($env:AETHERCHAT_PYTHON) { $env:AETHERCHAT_PYTHON } else { "python" }

if ($env:AETHERCHAT_REQUIRE_CODE_SIGNING -eq "1" -and -not $env:CSC_LINK) {
  throw "Release build requires CSC_LINK; unsigned release packages are forbidden"
}

Push-Location $BackendDir
try {
  & $Python -m PyInstaller --noconfirm --clean aetherchat-backend.spec
} finally {
  Pop-Location
}

$StagedBackend = Join-Path $ScriptDir "backend"
if (Test-Path $StagedBackend) { Remove-Item -Recurse -Force $StagedBackend }
Copy-Item -Recurse (Join-Path $BackendDir "dist\aetherchat-backend") $StagedBackend

Push-Location $ScriptDir
try {
  if (-not (Test-Path "node_modules")) { npm ci }
  npm run dist:win
  $Installer = Get-ChildItem "release\AetherChat-Setup-*.exe" | Select-Object -First 1
  if (-not $Installer) { throw "NSIS installer was not created" }
  if ($env:AETHERCHAT_REQUIRE_CODE_SIGNING -eq "1") {
    $Signature = Get-AuthenticodeSignature $Installer.FullName
    if ($Signature.Status -ne "Valid") { throw "NSIS installer signature is not valid: $($Signature.Status)" }
  }
  Write-Host "Built $($Installer.FullName)"
} finally {
  Pop-Location
}
