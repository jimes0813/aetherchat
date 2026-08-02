const { spawn } = require('child_process');

const ALLOWED_BROWSERS = new Set(['default', 'firefox']);

function normalizeExternalNavigation(value) {
  const payload = typeof value === 'string' ? { url: value } : (value || {});
  const url = new URL(String(payload.url || ''));
  if (!/^https?:$/.test(url.protocol)) {
    throw new Error('Only HTTP(S) links are allowed');
  }
  const browser = String(payload.browser || 'default').trim().toLowerCase();
  if (!ALLOWED_BROWSERS.has(browser)) {
    throw new Error('Browser must be default or firefox');
  }
  return { url: url.toString(), browser };
}

function waitForSpawn(command, args, spawnProcess) {
  return new Promise((resolve, reject) => {
    let child;
    try {
      child = spawnProcess(command, args, {
        detached: true,
        stdio: 'ignore',
      });
    } catch (error) {
      reject(error);
      return;
    }
    child.once('error', reject);
    child.once('spawn', () => {
      child.unref?.();
      resolve();
    });
  });
}

async function openExternalNavigation(value, dependencies = {}) {
  const { url, browser } = normalizeExternalNavigation(value);
  const platform = dependencies.platform || process.platform;
  const spawnProcess = dependencies.spawnProcess || spawn;
  const shellOpenExternal = dependencies.shellOpenExternal;
  let launcher = 'system_default';

  if (browser === 'firefox') {
    launcher = 'firefox';
    if (platform === 'darwin') {
      await waitForSpawn('open', ['-a', 'Firefox', url], spawnProcess);
    } else {
      await waitForSpawn(
        platform === 'win32' ? 'firefox.exe' : 'firefox',
        ['--new-tab', url],
        spawnProcess,
      );
    }
  } else {
    if (typeof shellOpenExternal !== 'function') {
      throw new Error('System browser launcher is unavailable');
    }
    await shellOpenExternal(url);
  }

  return {
    success: true,
    kind: 'external_navigation',
    url,
    browser,
    launcher,
    verification: 'launcher_accepted',
    accepted_at: new Date().toISOString(),
  };
}

module.exports = {
  normalizeExternalNavigation,
  openExternalNavigation,
};
