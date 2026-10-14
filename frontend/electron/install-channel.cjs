'use strict';

function detectInstallChannel({
  packaged = false,
  execPath = '',
  appImageEnv = '',
  noSandbox = false,
} = {}) {
  if (!packaged) return 'development';
  const exe = String(execPath || '').replace(/\\/g, '/');
  if (exe.startsWith('/opt/AetherChat')) return 'system-deb';
  if (appImageEnv || /\.AppImage$/i.test(exe)) return 'user-appimage';
  if (
    exe.includes('/linux-arm64-unpacked/')
    || exe.includes('/linux-unpacked/')
    || exe.includes('/.local/share/aetherchat/')
    || noSandbox
  ) {
    return 'user-unpacked';
  }
  if (/AetherChat\.exe$/i.test(exe) || exe.includes('/Programs/AetherChat')) {
    return 'windows-nsis';
  }
  return 'packaged';
}

function isChromiumSandboxDegraded(channel, noSandbox = false) {
  // Only report degraded when Chromium was actually launched with --no-sandbox.
  // Channel alone (user-unpacked / AppImage) no longer implies sandbox off.
  void channel;
  return Boolean(noSandbox);
}

function describeInstallChannel(channel) {
  switch (channel) {
    case 'development':
      return '开发运行';
    case 'system-deb':
      return '系统 Deb（/opt/AetherChat）';
    case 'user-appimage':
      return '用户 AppImage';
    case 'user-unpacked':
      return '用户解包（本地 RC）';
    case 'windows-nsis':
      return 'Windows 安装';
    default:
      return '打包安装';
  }
}

module.exports = {
  detectInstallChannel,
  isChromiumSandboxDegraded,
  describeInstallChannel,
};
