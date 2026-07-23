@echo off
setlocal
cd /d %~dp0

for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)" 2^>nul') do set "PYTHON_EXE=%%P"
if not defined PYTHON_EXE (
  echo Python 3 was not found. Install Python and the backend requirements first.
  exit /b 1
)

echo Building local FastAPI backend...
pushd ..\backend
"%PYTHON_EXE%" -m PyInstaller --noconfirm --clean aetherchat-backend.spec
if errorlevel 1 (
  popd
  exit /b 1
)
popd

echo Staging backend for Electron...
if exist backend rmdir /s /q backend
xcopy /e /i /y ..\backend\dist\aetherchat-backend backend >nul
if errorlevel 1 exit /b 1

if not exist node_modules (
  echo Installing frontend dependencies...
  call npm ci
  if errorlevel 1 exit /b 1
)

echo Building Windows installer...
call npm run dist:win
if errorlevel 1 exit /b 1
echo Done. Installer is in the release directory.
endlocal
