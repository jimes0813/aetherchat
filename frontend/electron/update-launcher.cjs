const { spawn } = require('child_process');
const { execFile } = require('child_process');
const path = require('path');

function assertWindowsInstallerPath(installerPath) {
  if (!path.isAbsolute(installerPath) || path.extname(installerPath).toLowerCase() !== '.exe') {
    throw new Error('Windows update installer path must be an absolute .exe path');
  }
}

function verifyWindowsInstallerSignature(installerPath, options = {}) {
  try {
    assertWindowsInstallerPath(installerPath);
  } catch (error) {
    return Promise.reject(error);
  }
  const execFileImpl = options.execFileImpl || execFile;
  const command = [
    '$signature = Get-AuthenticodeSignature -LiteralPath $env:AETHERCHAT_UPDATE_INSTALLER;',
    'if ($signature.Status -ne [System.Management.Automation.SignatureStatus]::Valid) {',
    '  [Console]::Error.Write("status=" + $signature.Status); exit 1',
    '};',
    'if (-not $signature.SignerCertificate.Subject) {',
    '  [Console]::Error.Write("signer subject missing"); exit 1',
    '};',
    '[Console]::Out.Write($signature.SignerCertificate.Subject)',
  ].join(' ');
  return new Promise((resolve, reject) => {
    execFileImpl('powershell.exe', [
      '-NoLogo',
      '-NoProfile',
      '-NonInteractive',
      '-Command',
      command,
    ], {
      env: { ...process.env, AETHERCHAT_UPDATE_INSTALLER: installerPath },
      timeout: 30_000,
      windowsHide: true,
      maxBuffer: 16 * 1024,
    }, (error, stdout, stderr) => {
      if (error) {
        const detail = String(stderr || error.message || 'unknown status').trim().slice(0, 500);
        reject(new Error(`Windows update Authenticode verification failed: ${detail}`));
        return;
      }
      const signerSubject = String(stdout || '').trim();
      if (!signerSubject) {
        reject(new Error('Windows update Authenticode signer subject is missing'));
        return;
      }
      resolve({ signerSubject });
    });
  });
}

function launchWindowsInstaller(installerPath, options = {}) {
  try {
    assertWindowsInstallerPath(installerPath);
  } catch (error) {
    return Promise.reject(error);
  }
  const spawnImpl = options.spawnImpl || spawn;
  const args = ['--updated'];
  if (options.silent) args.push('/S');
  if (options.forceRunAfter) args.push('--force-run');
  return new Promise((resolve, reject) => {
    let child;
    try {
      child = spawnImpl(installerPath, args, {
        detached: true,
        stdio: 'ignore',
        windowsHide: false,
      });
    } catch (error) {
      reject(error);
      return;
    }
    child.once('error', reject);
    child.once('spawn', () => {
      child.unref();
      resolve();
    });
  });
}

module.exports = { launchWindowsInstaller, verifyWindowsInstallerSignature };
