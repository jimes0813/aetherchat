'use strict';

function registerTerminationHandlers(processTarget, requestQuit) {
  let quitRequested = false;
  const handlers = new Map();

  for (const signal of ['SIGTERM', 'SIGINT']) {
    const handler = () => {
      if (quitRequested) return;
      quitRequested = true;
      requestQuit(signal);
    };
    handlers.set(signal, handler);
    processTarget.on(signal, handler);
  }

  return () => {
    for (const [signal, handler] of handlers) {
      processTarget.removeListener(signal, handler);
    }
  };
}

function terminateChildProcess(child, timeoutMs = 5000) {
  if (!child || child.exitCode !== null || child.signalCode !== null) {
    return Promise.resolve('already_stopped');
  }

  return new Promise((resolve) => {
    let settled = false;
    let timer = null;
    const finish = (outcome) => {
      if (settled) return;
      settled = true;
      if (timer) clearTimeout(timer);
      child.removeListener('exit', onExit);
      resolve(outcome);
    };
    const onExit = () => finish('graceful');
    child.once('exit', onExit);

    try {
      if (!child.kill('SIGTERM')) {
        finish('unavailable');
        return;
      }
    } catch {
      finish('unavailable');
      return;
    }

    timer = setTimeout(() => {
      try {
        child.kill('SIGKILL');
      } catch {
        // The process may have exited between the timeout and the signal.
      }
      finish('forced');
    }, timeoutMs);
  });
}

module.exports = { registerTerminationHandlers, terminateChildProcess };
