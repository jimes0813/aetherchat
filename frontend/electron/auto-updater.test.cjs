const assert = require('node:assert/strict');
const test = require('node:test');
const {
  DEFAULT_UPDATE_INTERVAL_MS,
  DEFAULT_UPDATE_STARTUP_DELAY_MS,
  STABLE_UPDATE_MANIFEST_URL,
  WINDOWS_RC_UPDATE_MANIFEST_URL,
  createAutoUpdateManager,
  resolveUpdateManifestUrl,
} = require('./auto-updater.cjs');

const update = {
  manifest: { version: '0.3.0' },
  sha256: 'a'.repeat(64),
  path: 'C:\\updates\\AetherChat-Setup-0.3.0.exe',
};

test('selects rolling Windows RC and stable signed-manifest channels', () => {
  assert.equal(resolveUpdateManifestUrl({
    currentVersion: '0.2.1-rc.4', platform: 'win32', override: '',
  }), WINDOWS_RC_UPDATE_MANIFEST_URL);
  assert.equal(resolveUpdateManifestUrl({
    currentVersion: '0.2.1', platform: 'win32', override: '',
  }), STABLE_UPDATE_MANIFEST_URL);
  assert.equal(resolveUpdateManifestUrl({
    currentVersion: '0.2.1-rc.4', platform: 'linux', override: '',
  }), STABLE_UPDATE_MANIFEST_URL);
  assert.equal(resolveUpdateManifestUrl({
    currentVersion: '0.2.1', platform: 'win32', override: 'https://updates.example.test/custom.json',
  }), 'https://updates.example.test/custom.json');
});

test('checks at startup and on the background interval, then installs once', async () => {
  const scheduled = {};
  const statuses = [];
  let downloads = 0;
  let installs = 0;
  const manager = createAutoUpdateManager({
    enabled: true,
    downloadUpdate: async () => { downloads += 1; return update; },
    installUpdate: async (value) => { installs += 1; assert.equal(value, update); },
    onStatus: (status) => statuses.push(status.phase),
    setTimeoutImpl: (callback, delay) => { scheduled.startup = { callback, delay }; return 1; },
    clearTimeoutImpl: () => {},
    setIntervalImpl: (callback, delay) => { scheduled.periodic = { callback, delay }; return 2; },
    clearIntervalImpl: () => {},
  });

  assert.equal(manager.start(), true);
  assert.equal(manager.start(), false);
  assert.equal(scheduled.startup.delay, DEFAULT_UPDATE_STARTUP_DELAY_MS);
  assert.equal(scheduled.periodic.delay, DEFAULT_UPDATE_INTERVAL_MS);
  scheduled.startup.callback();
  await new Promise((resolve) => setImmediate(resolve));

  assert.equal(downloads, 1);
  assert.equal(installs, 1);
  assert.deepEqual(statuses, ['checking', 'downloaded', 'installing']);
  assert.equal(manager.getState().installing, true);

  scheduled.periodic.callback();
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(downloads, 1);
  manager.stop();
});

test('deduplicates concurrent checks and treats the current version as healthy', async () => {
  let resolveDownload;
  let downloads = 0;
  const manager = createAutoUpdateManager({
    enabled: true,
    downloadUpdate: () => {
      downloads += 1;
      return new Promise((resolve) => { resolveDownload = resolve; });
    },
    installUpdate: async () => {},
  });
  const first = manager.check('manual');
  const second = manager.check('periodic');
  assert.equal(first, second);
  assert.equal(downloads, 1);
  resolveDownload(update);
  await first;

  const currentManager = createAutoUpdateManager({
    enabled: true,
    downloadUpdate: async () => {
      const error = new Error('not newer');
      error.code = 'UPDATE_NOT_AVAILABLE';
      throw error;
    },
    installUpdate: async () => { throw new Error('must not install'); },
  });
  assert.deepEqual(await currentManager.check('manual'), { status: 'up-to-date' });
});

test('retries after download or installer failures and remains inert when disabled', async () => {
  let attempts = 0;
  const manager = createAutoUpdateManager({
    enabled: true,
    downloadUpdate: async () => {
      attempts += 1;
      if (attempts === 1) throw new Error('offline');
      return update;
    },
    installUpdate: async () => { throw new Error('installer blocked'); },
  });
  assert.equal((await manager.check('manual')).status, 'error');
  assert.equal((await manager.check('manual')).status, 'error');
  assert.equal(attempts, 2);
  assert.equal(manager.getState().installing, false);

  let scheduled = false;
  const disabled = createAutoUpdateManager({
    enabled: false,
    downloadUpdate: async () => update,
    installUpdate: async () => {},
    setTimeoutImpl: () => { scheduled = true; },
    setIntervalImpl: () => { scheduled = true; },
  });
  assert.equal(disabled.start(), false);
  assert.deepEqual(await disabled.check(), { status: 'disabled' });
  assert.equal(scheduled, false);
});

test('does not install a download that finishes after application shutdown starts', async () => {
  let resolveDownload;
  let installs = 0;
  const manager = createAutoUpdateManager({
    enabled: true,
    downloadUpdate: () => new Promise((resolve) => { resolveDownload = resolve; }),
    installUpdate: async () => { installs += 1; },
    setTimeoutImpl: () => 1,
    clearTimeoutImpl: () => {},
    setIntervalImpl: () => 2,
    clearIntervalImpl: () => {},
  });
  manager.start();
  const checking = manager.check('startup');
  manager.stop();
  resolveDownload(update);

  assert.deepEqual(await checking, { status: 'cancelled' });
  assert.equal(installs, 0);
});
