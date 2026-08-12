'use strict';

const assert = require('node:assert/strict');
const http = require('node:http');
const test = require('node:test');
const {
  classifyLivenessResponse,
  classifyReadinessResponse,
  probeBackendReadiness,
  waitForBackendReadiness,
} = require('./backend-readiness.cjs');

test('classifies liveness separately from database readiness', () => {
  assert.equal(classifyLivenessResponse({
    statusCode: 200,
    headers: {},
    payload: { status: 'ok', service: 'aetherchat' },
  }).alive, true);
  assert.deepEqual(classifyReadinessResponse({
    statusCode: 200,
    headers: {},
    payload: { status: 'ready', ready: true, service: 'aetherchat' },
  }), {
    ready: true,
    code: '',
    requestId: '',
    terminal: false,
  });
});

test('preserves stable schema errors and the backend diagnostic id', () => {
  assert.deepEqual(classifyReadinessResponse({
    statusCode: 503,
    headers: { 'x-request-id': 'request-schema-1' },
    payload: {
      status: 'not_ready',
      ready: false,
      service: 'aetherchat',
      error: { code: 'database_schema_mismatch', message: 'not exposed by desktop' },
    },
  }), {
    ready: false,
    code: 'database_schema_mismatch',
    requestId: 'request-schema-1',
    terminal: true,
  });
});

test('readiness probe sends and returns a correlated request id', async (context) => {
  let receivedRequestId = '';
  const server = http.createServer((request, response) => {
    receivedRequestId = String(request.headers['x-request-id'] || '');
    response.writeHead(503, {
      'content-type': 'application/json',
      'x-request-id': receivedRequestId,
    });
    response.end(JSON.stringify({
      status: 'not_ready',
      ready: false,
      service: 'aetherchat',
      error: { code: 'database_unavailable', message: 'Core database is unavailable' },
    }));
  });
  await new Promise((resolve) => server.listen(0, '127.0.0.1', resolve));
  context.after(() => server.close());
  const address = server.address();
  assert.ok(address && typeof address !== 'string');

  const result = await probeBackendReadiness(address.port);

  assert.match(receivedRequestId, /^desktop-/);
  assert.equal(result.requestId, receivedRequestId);
  assert.equal(result.code, 'database_unavailable');
  assert.equal(result.terminal, false);
});

test('stops immediately for terminal failures and uses a stable timeout code', async () => {
  let calls = 0;
  const terminal = await waitForBackendReadiness({
    port: 8000,
    timeoutMs: 5000,
    probe: async () => {
      calls += 1;
      return { ready: false, code: 'database_schema_mismatch', requestId: 'schema-2', terminal: true };
    },
  });
  assert.equal(calls, 1);
  assert.equal(terminal.code, 'database_schema_mismatch');

  const timedOut = await waitForBackendReadiness({
    port: 8000,
    timeoutMs: 2,
    intervalMs: 1,
    probe: async () => ({ ready: false, code: 'backend_unreachable', requestId: 'startup-1', terminal: false }),
  });
  assert.equal(timedOut.code, 'backend_start_timeout');
  assert.equal(timedOut.requestId, 'startup-1');
});
