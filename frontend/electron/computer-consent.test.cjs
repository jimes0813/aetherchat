const test = require('node:test');
const assert = require('node:assert/strict');
const { createComputerConsentBroker } = require('./computer-consent.cjs');

test('broker starts with empty url until start()', () => {
  const broker = createComputerConsentBroker({
    dialog: { showMessageBox: async () => ({ response: 2 }) },
    BrowserWindow: { getAllWindows: () => [] },
    token: 'x'.repeat(32),
  });
  assert.equal(broker.getUrl(), '');
  assert.equal(broker.secureTokenMatches('x'.repeat(32)), true);
  assert.equal(broker.secureTokenMatches('y'.repeat(32)), false);
});
