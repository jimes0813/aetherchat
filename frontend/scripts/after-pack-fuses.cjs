'use strict';

/**
 * Flip Electron fuses after pack for all platforms (Linux/macOS/Windows).
 * afterSign remains Windows-only for Authenticode; fuses apply here so unsigned
 * Linux RC builds still get production fuse flips.
 */
const path = require('node:path');
const fs = require('node:fs');

async function flipProductionFuses(context) {
  const { flipFuses, FuseVersion, FuseV1Options } = require('@electron/fuses');
  const { electronPlatformName, appOutDir, packager } = context;
  const productName = (packager && packager.appInfo && packager.appInfo.productFilename)
    || (context.packager && context.packager.appInfo && context.packager.appInfo.productFilename)
    || 'AetherChat';

  let executablePath;
  if (electronPlatformName === 'darwin') {
    executablePath = path.join(appOutDir, `${productName}.app`);
  } else if (electronPlatformName === 'win32') {
    executablePath = path.join(appOutDir, `${productName}.exe`);
  } else {
    // linux: binary name matches productName / executableName
    const candidates = [
      path.join(appOutDir, productName),
      path.join(appOutDir, 'aetherchat-frontend'),
      path.join(appOutDir, productName.toLowerCase()),
    ];
    executablePath = candidates.find((candidate) => fs.existsSync(candidate));
    if (!executablePath) {
      throw new Error(`after-pack-fuses: Electron binary not found in ${appOutDir}`);
    }
  }

  await flipFuses(executablePath, {
    version: FuseVersion.V1,
    resetAdHocDarwinSignature: electronPlatformName === 'darwin',
    [FuseV1Options.RunAsNode]: false,
    [FuseV1Options.EnableCookieEncryption]: true,
    [FuseV1Options.EnableNodeOptionsEnvironmentVariable]: false,
    [FuseV1Options.EnableNodeCliInspectArguments]: false,
    [FuseV1Options.EnableEmbeddedAsarIntegrityValidation]: true,
    [FuseV1Options.OnlyLoadAppFromAsar]: true,
  });
}

exports.default = async function afterPack(context) {
  await flipProductionFuses(context);
};

module.exports.flipProductionFuses = flipProductionFuses;
