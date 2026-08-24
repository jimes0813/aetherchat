'use strict';

const DEFAULT_UPDATE_STARTUP_DELAY_MS = 30 * 1000;
const DEFAULT_UPDATE_INTERVAL_MS = 6 * 60 * 60 * 1000;
const STABLE_UPDATE_MANIFEST_URL = 'https://github.com/jimes0813/AetherChat/releases/latest/download/update-manifest.signed.json';
const WINDOWS_RC_UPDATE_MANIFEST_URL = 'https://github.com/jimes0813/AetherChat/releases/download/windows-ci-latest/update-manifest.signed.json';

function resolveUpdateManifestUrl({ override, currentVersion, platform }) {
  const configured = String(override || '').trim();
  if (configured) return configured;
  if (platform === 'win32' && String(currentVersion || '').includes('-')) {
    return WINDOWS_RC_UPDATE_MANIFEST_URL;
  }
  return STABLE_UPDATE_MANIFEST_URL;
}

function createAutoUpdateManager({
  enabled,
  downloadUpdate,
  installUpdate,
  onStatus = () => {},
  startupDelayMs = DEFAULT_UPDATE_STARTUP_DELAY_MS,
  intervalMs = DEFAULT_UPDATE_INTERVAL_MS,
  setTimeoutImpl = setTimeout,
  clearTimeoutImpl = clearTimeout,
  setIntervalImpl = setInterval,
  clearIntervalImpl = clearInterval,
}) {
  if (typeof downloadUpdate !== 'function' || typeof installUpdate !== 'function') {
    throw new TypeError('Auto update manager requires download and install functions');
  }

  let running = false;
  let installing = false;
  let inFlight = null;
  let startupTimer = null;
  let intervalTimer = null;
  let stopGeneration = 0;

  const emit = (phase, detail = {}) => {
    try {
      onStatus({ phase, at: new Date().toISOString(), ...detail });
    } catch {
      // Status reporting must never break update checks.
    }
  };

  const check = (reason = 'manual') => {
    if (!enabled) return Promise.resolve({ status: 'disabled' });
    if (installing) return Promise.resolve({ status: 'installing' });
    if (inFlight) return inFlight;
    if (reason !== 'manual' && !running) return Promise.resolve({ status: 'cancelled' });

    const checkGeneration = stopGeneration;
    inFlight = (async () => {
      emit('checking', { reason });
      try {
        const update = await downloadUpdate();
        if (checkGeneration !== stopGeneration) {
          emit('cancelled', { reason });
          return { status: 'cancelled' };
        }
        installing = true;
        emit('downloaded', {
          reason,
          version: String(update?.manifest?.version || ''),
          sha256: String(update?.sha256 || ''),
        });
        try {
          await installUpdate(update);
        } catch (error) {
          installing = false;
          throw error;
        }
        emit('installing', {
          reason,
          version: String(update?.manifest?.version || ''),
        });
        return { status: 'installing', update };
      } catch (error) {
        if (error?.code === 'UPDATE_NOT_AVAILABLE') {
          emit('up-to-date', { reason });
          return { status: 'up-to-date' };
        }
        emit('error', {
          reason,
          error: String(error?.message || error),
        });
        return { status: 'error', error };
      } finally {
        inFlight = null;
      }
    })();
    return inFlight;
  };

  const start = () => {
    if (!enabled || running) return false;
    running = true;
    startupTimer = setTimeoutImpl(() => {
      startupTimer = null;
      void check('startup');
    }, startupDelayMs);
    intervalTimer = setIntervalImpl(() => {
      void check('periodic');
    }, intervalMs);
    return true;
  };

  const stop = () => {
    running = false;
    stopGeneration += 1;
    if (startupTimer !== null) clearTimeoutImpl(startupTimer);
    if (intervalTimer !== null) clearIntervalImpl(intervalTimer);
    startupTimer = null;
    intervalTimer = null;
  };

  return {
    check,
    start,
    stop,
    getState: () => ({
      enabled: Boolean(enabled),
      running,
      checking: Boolean(inFlight),
      installing,
    }),
  };
}

module.exports = {
  DEFAULT_UPDATE_INTERVAL_MS,
  DEFAULT_UPDATE_STARTUP_DELAY_MS,
  STABLE_UPDATE_MANIFEST_URL,
  WINDOWS_RC_UPDATE_MANIFEST_URL,
  createAutoUpdateManager,
  resolveUpdateManifestUrl,
};
