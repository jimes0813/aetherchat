const path = require('path');

function isBrokenSocksProxy(value) {
  const normalized = String(value || '').trim().toLowerCase();
  return (
    normalized.startsWith('socks://')
    || normalized.startsWith('socks5://')
    || normalized.startsWith('socks4://')
  );
}

/**
 * Prepare env for the Python backend child process.
 * OpenAI/httpx rejects socks:// ALL_PROXY schemes with:
 *   Unknown scheme for proxy URL URL('socks://...')
 */
function sanitizeBackendProxyEnv(env) {
  const next = { ...env };
  for (const key of ['ALL_PROXY', 'all_proxy']) {
    if (isBrokenSocksProxy(next[key])) {
      delete next[key];
    }
  }
  return next;
}

const SQLITE_NAME = 'aetherchat.sqlite3';

function pathForPlatform(platform) {
  return platform === 'win32' ? path.win32 : path.posix;
}

function xdgBackendDataDir({ platform = process.platform, home = '', env = {} } = {}) {
  const platformPath = pathForPlatform(platform);
  if (platform === 'win32') {
    return platformPath.join(env.LOCALAPPDATA || home, 'AetherChat');
  }
  return platformPath.join(
    env.XDG_DATA_HOME || platformPath.join(home, '.local', 'share'),
    'aetherchat',
  );
}

function packagedLegacyBackendDataDir(userData, platform = process.platform) {
  return pathForPlatform(platform).join(userData, 'backend-data');
}

function resolveBackendDataDir({
  userData = '',
  home = '',
  platform = process.platform,
  env = {},
  existsSync = () => false,
} = {}) {
  if (env.AETHERCHAT_DATA_DIR) return env.AETHERCHAT_DATA_DIR;
  const platformPath = pathForPlatform(platform);
  const packaged = packagedLegacyBackendDataDir(userData, platform);
  const xdg = xdgBackendDataDir({ platform, home, env });
  if (existsSync(platformPath.join(packaged, SQLITE_NAME))) return packaged;
  if (existsSync(platformPath.join(xdg, SQLITE_NAME))) return xdg;
  return xdg;
}

module.exports = {
  isBrokenSocksProxy,
  sanitizeBackendProxyEnv,
  packagedLegacyBackendDataDir,
  xdgBackendDataDir,
  resolveBackendDataDir,
};
