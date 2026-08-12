function isBrokenSocksProxy(value) {
  const normalized = String(value || '').trim().toLowerCase();
  return (
    normalized.startsWith('socks://')
    || normalized.startsWith('socks5://')
    || normalized.startsWith('socks4://')
  );
}

/**
 * Prepare env for the Python backend child process.
 * OpenAI/httpx rejects socks:// ALL_PROXY schemes with:
 *   Unknown scheme for proxy URL URL('socks://...')
 */
function sanitizeBackendProxyEnv(env) {
  const next = { ...env };
  for (const key of ['ALL_PROXY', 'all_proxy']) {
    if (isBrokenSocksProxy(next[key])) {
      delete next[key];
    }
  }
  return next;
}

module.exports = {
  isBrokenSocksProxy,
  sanitizeBackendProxyEnv,
};
