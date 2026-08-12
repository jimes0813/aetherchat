const test = require('node:test');
const assert = require('node:assert/strict');
const { isBrokenSocksProxy, sanitizeBackendProxyEnv } = require('./backend-env.cjs');

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
