const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const path = require('node:path');
const test = require('node:test');
const {
  launchWindowsInstaller,
  verifyWindowsInstallerSignature,
} = require('./update-launcher.cjs');

test('launches Windows updates with electron-builder upgrade semantics', async () => {
  const child = new EventEmitter();
  let unrefCalled = false;
  child.unref = () => { unrefCalled = true; };
  let invocation;
  const spawnImpl = (...args) => {
    invocation = args;
    process.nextTick(() => child.emit('spawn'));
    return child;
  };
  const installerPath = path.resolve('AetherChat-Setup-0.2.0.exe');

  await launchWindowsInstaller(installerPath, { spawnImpl });

  assert.deepEqual(invocation, [
    installerPath,
    ['--updated'],
    { detached: true, stdio: 'ignore', windowsHide: false },
  ]);
  assert.equal(unrefCalled, true);
});

test('launches an automatic update silently and restarts the installed app', async () => {
  const child = new EventEmitter();
  child.unref = () => {};
  let invocation;
  const spawnImpl = (...args) => {
    invocation = args;
    process.nextTick(() => child.emit('spawn'));
    return child;
  };
  const installerPath = path.resolve('AetherChat-Setup-0.3.0.exe');

  await launchWindowsInstaller(installerPath, {
    silent: true,
    forceRunAfter: true,
    spawnImpl,
  });

  assert.deepEqual(invocation, [
    installerPath,
    ['--updated', '/S', '--force-run'],
    { detached: true, stdio: 'ignore', windowsHide: false },
  ]);
});

test('requires a valid Windows Authenticode signature before automatic install', async () => {
  const installerPath = path.resolve('AetherChat-Setup-0.3.0.exe');
  let invocation;
  const execFileImpl = (...args) => {
    invocation = args;
    args.at(-1)(null, 'CN=SignPath Foundation', '');
  };
  assert.deepEqual(
    await verifyWindowsInstallerSignature(installerPath, { execFileImpl }),
    { signerSubject: 'CN=SignPath Foundation' },
  );
  assert.equal(invocation[0], 'powershell.exe');
  assert.ok(invocation[1].includes('-NonInteractive'));
  assert.equal(invocation[2].env.AETHERCHAT_UPDATE_INSTALLER, installerPath);

  const invalidSignature = (...args) => args.at(-1)(new Error('exit 1'), '', 'status=NotSigned');
  await assert.rejects(
    verifyWindowsInstallerSignature(installerPath, { execFileImpl: invalidSignature }),
    /Authenticode verification failed: status=NotSigned/,
  );
});

test('rejects unsafe installer paths and spawn failures', async () => {
  await assert.rejects(
    launchWindowsInstaller('relative-installer.exe'),
    /absolute \.exe path/,
  );
  await assert.rejects(
    launchWindowsInstaller(path.resolve('installer.txt')),
    /absolute \.exe path/,
  );
  const spawnImpl = () => { throw new Error('spawn failed'); };
  await assert.rejects(
    launchWindowsInstaller(path.resolve('installer.exe'), { spawnImpl }),
    /spawn failed/,
  );
});
