const test = require('node:test');
const assert = require('node:assert/strict');
const {
  isBrokenSocksProxy,
  resolveBackendDataDir,
  sanitizeBackendProxyEnv,
} = require('./backend-env.cjs');

test('detects socks absolute proxy schemes', () => {
  assert.equal(isBrokenSocksProxy('socks://127.0.0.1:10808'), true);
  assert.equal(isBrokenSocksProxy('socks5://127.0.0.1:10808'), true);
  assert.equal(isBrokenSocksProxy('http://127.0.0.1:10808'), false);
  assert.equal(isBrokenSocksProxy(''), false);
});

test('strips socks ALL_PROXY while keeping http proxies', () => {
  const cleaned = sanitizeBackendProxyEnv({
    ALL_PROXY: 'socks://127.0.0.1:10808/',
    all_proxy: 'socks5://127.0.0.1:10808/',
    HTTPS_PROXY: 'http://127.0.0.1:10808/',
    NO_PROXY: 'localhost,127.0.0.1',
  });
  assert.equal(cleaned.ALL_PROXY, undefined);
  assert.equal(cleaned.all_proxy, undefined);
  assert.equal(cleaned.HTTPS_PROXY, 'http://127.0.0.1:10808/');
  assert.equal(cleaned.NO_PROXY, 'localhost,127.0.0.1');
});

test('resolveBackendDataDir prefers explicit env', () => {
  const resolved = resolveBackendDataDir({
    userData: '/tmp/userData',
    home: '/home/demo',
    env: { AETHERCHAT_DATA_DIR: '/tmp/explicit' },
    existsSync: () => true,
  });
  assert.equal(resolved, '/tmp/explicit');
});

test('resolveBackendDataDir keeps packed backend-data when sqlite exists', () => {
  const resolved = resolveBackendDataDir({
    userData: '/home/demo/.config/aetherchat-frontend',
    home: '/home/demo',
    platform: 'linux',
    env: {},
    existsSync: (candidate) => candidate.endsWith('backend-data/aetherchat.sqlite3'),
  });
  assert.equal(resolved, '/home/demo/.config/aetherchat-frontend/backend-data');
});

test('resolveBackendDataDir uses XDG share when only that sqlite exists', () => {
  const resolved = resolveBackendDataDir({
    userData: '/home/demo/.config/aetherchat-frontend',
    home: '/home/demo',
    platform: 'linux',
    env: {},
    existsSync: (candidate) => candidate.endsWith('aetherchat/aetherchat.sqlite3'),
  });
  assert.equal(resolved, '/home/demo/.local/share/aetherchat');
});

test('resolveBackendDataDir defaults new installs to XDG share', () => {
  const resolved = resolveBackendDataDir({
    userData: '/home/demo/.config/aetherchat-frontend',
    home: '/home/demo',
    platform: 'linux',
    env: {},
    existsSync: () => false,
  });
  assert.equal(resolved, '/home/demo/.local/share/aetherchat');
});

test('resolveBackendDataDir uses Windows local app data semantics', () => {
  const resolved = resolveBackendDataDir({
    userData: 'C:\\Users\\demo\\AppData\\Roaming\\aetherchat-frontend',
    home: 'C:\\Users\\demo',
    platform: 'win32',
    env: { LOCALAPPDATA: 'C:\\Users\\demo\\AppData\\Local' },
    existsSync: () => false,
  });
  assert.equal(resolved, 'C:\\Users\\demo\\AppData\\Local\\AetherChat');
});
