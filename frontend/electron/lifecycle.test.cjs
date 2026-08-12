'use strict';

const assert = require('node:assert/strict');
const { EventEmitter } = require('node:events');
const test = require('node:test');
const { registerTerminationHandlers, terminateChildProcess } = require('./lifecycle.cjs');

test('maps SIGTERM and SIGINT to one graceful application quit', () => {
  const processTarget = new EventEmitter();
  const signals = [];
  const unregister = registerTerminationHandlers(processTarget, (signal) => signals.push(signal));

  processTarget.emit('SIGTERM');
  processTarget.emit('SIGTERM');
  processTarget.emit('SIGINT');

  assert.deepEqual(signals, ['SIGTERM']);
  assert.equal(processTarget.listenerCount('SIGTERM'), 1);
  unregister();
  assert.equal(processTarget.listenerCount('SIGTERM'), 0);
  assert.equal(processTarget.listenerCount('SIGINT'), 0);
});

test('waits for the backend to exit after SIGTERM', async () => {
  const child = new EventEmitter();
  child.exitCode = null;
  child.signalCode = null;
  const signals = [];
  child.kill = (signal) => {
    signals.push(signal);
    if (signal === 'SIGTERM') setImmediate(() => child.emit('exit', 0, null));
    return true;
  };

  assert.equal(await terminateChildProcess(child, 100), 'graceful');
  assert.deepEqual(signals, ['SIGTERM']);
});

test('forces a backend that ignores the graceful shutdown deadline', async () => {
  const child = new EventEmitter();
  child.exitCode = null;
  child.signalCode = null;
  const signals = [];
  child.kill = (signal) => {
    signals.push(signal);
    return true;
  };

  assert.equal(await terminateChildProcess(child, 1), 'forced');
  assert.deepEqual(signals, ['SIGTERM', 'SIGKILL']);
});
