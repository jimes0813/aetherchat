const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const test = require('node:test');

const {
  normalizeExternalNavigation,
  openExternalNavigation,
} = require('./external-navigation.cjs');

test('external navigation accepts only HTTP(S) and known browsers', () => {
  assert.deepEqual(
    normalizeExternalNavigation({
      url: 'https://www.youtube.com',
      browser: 'firefox',
    }),
    {
      url: 'https://www.youtube.com/',
      browser: 'firefox',
    },
  );
  assert.throws(
    () => normalizeExternalNavigation('file:///etc/passwd'),
    /Only HTTP\(S\)/,
  );
  assert.throws(
    () => normalizeExternalNavigation({ url: 'https://example.com', browser: 'unknown' }),
    /Browser must be/,
  );
});

test('default navigation waits for the Electron shell handoff', async () => {
  const opened = [];
  const receipt = await openExternalNavigation('https://example.com', {
    platform: 'linux',
    shellOpenExternal: async (url) => opened.push(url),
  });

  assert.deepEqual(opened, ['https://example.com/']);
  assert.equal(receipt.success, true);
  assert.equal(receipt.launcher, 'system_default');
  assert.equal(receipt.verification, 'launcher_accepted');
});

test('Firefox navigation records success only after the process spawn event', async () => {
  const calls = [];
  let child;
  const promise = openExternalNavigation({
    url: 'https://www.youtube.com',
    browser: 'firefox',
  }, {
    platform: 'linux',
    spawnProcess: (command, args, options) => {
      child = new EventEmitter();
      child.unref = () => { child.unrefCalled = true; };
      calls.push({ command, args, options });
      return child;
    },
  });

  let settled = false;
  void promise.finally(() => { settled = true; });
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(settled, false);
  child.emit('spawn');
  const receipt = await promise;

  assert.equal(calls[0].command, 'firefox');
  assert.deepEqual(calls[0].args, ['--new-tab', 'https://www.youtube.com/']);
  assert.deepEqual(calls[0].options, { detached: true, stdio: 'ignore' });
  assert.equal(child.unrefCalled, true);
  assert.equal(receipt.browser, 'firefox');
  assert.equal(receipt.launcher, 'firefox');
});
