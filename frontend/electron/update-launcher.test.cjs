const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const path = require('node:path');
const test = require('node:test');
const { launchWindowsInstaller } = require('./update-launcher.cjs');

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

  await launchWindowsInstaller(installerPath, spawnImpl);

  assert.deepEqual(invocation, [
    installerPath,
    ['--updated'],
    { detached: true, stdio: 'ignore', windowsHide: false },
  ]);
  assert.equal(unrefCalled, true);
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
  const spawnImpl = () => {
    throw new Error('spawn failed');
  };
  await assert.rejects(
    launchWindowsInstaller(path.resolve('installer.exe'), spawnImpl),
    /spawn failed/,
  );
});
