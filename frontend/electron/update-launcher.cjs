const { spawn } = require('child_process');
const path = require('path');

function launchWindowsInstaller(installerPath, spawnImpl = spawn) {
  if (!path.isAbsolute(installerPath) || path.extname(installerPath).toLowerCase() !== '.exe') {
    return Promise.reject(new Error('Windows update installer path must be an absolute .exe path'));
  }
  return new Promise((resolve, reject) => {
    let child;
    try {
      child = spawnImpl(installerPath, ['--updated'], {
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

module.exports = { launchWindowsInstaller };
