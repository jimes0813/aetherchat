const test = require('node:test');
const assert = require('node:assert/strict');
const {
  detectInstallChannel,
  isChromiumSandboxDegraded,
  describeInstallChannel,
} = require('./install-channel.cjs');

test('unpackaged electron is the development channel', () => {
  assert.equal(detectInstallChannel({ packaged: false, execPath: '/opt/AetherChat/aetherchat-frontend' }), 'development');
  assert.equal(isChromiumSandboxDegraded('development', false), false);
});

test('system Deb path is sandboxed', () => {
  const channel = detectInstallChannel({
    packaged: true,
    execPath: '/opt/AetherChat/aetherchat-frontend',
    noSandbox: false,
  });
  assert.equal(channel, 'system-deb');
  assert.equal(isChromiumSandboxDegraded(channel, false), false);
  assert.equal(describeInstallChannel(channel), '系统 Deb（/opt/AetherChat）');
});

test('user unpacked and --no-sandbox are explicit degraded modes', () => {
  const unpacked = detectInstallChannel({
    packaged: true,
    execPath: '/home/jimes0813/AetherChat-linux/frontend/release/linux-arm64-unpacked/aetherchat-frontend',
    noSandbox: true,
  });
  assert.equal(unpacked, 'user-unpacked');
  assert.equal(isChromiumSandboxDegraded(unpacked, true), true);

  const localShare = detectInstallChannel({
    packaged: true,
    execPath: '/home/user/.local/share/aetherchat/current/aetherchat-frontend',
    noSandbox: true,
  });
  assert.equal(localShare, 'user-unpacked');
});

test('AppImage installs are degraded even without the flag', () => {
  const channel = detectInstallChannel({
    packaged: true,
    execPath: '/tmp/.mount_AetherXXXX/aetherchat-frontend',
    appImageEnv: '/home/user/.local/share/aetherchat/AetherChat.AppImage',
  });
  assert.equal(channel, 'user-appimage');
  assert.equal(isChromiumSandboxDegraded(channel, false), true);
});
