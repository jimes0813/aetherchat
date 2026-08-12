'use strict';

const http = require('http');
const { randomUUID } = require('crypto');

const MAX_RESPONSE_BYTES = 1024 * 1024;
const TERMINAL_READINESS_CODES = new Set(['database_schema_mismatch']);

function normalizedRequestId(headers = {}, payload = null, fallback = '') {
  const headerValue = headers['x-request-id'] || headers['X-Request-ID'];
  const bodyValue = payload && typeof payload === 'object' ? payload.request_id : '';
  return String(headerValue || bodyValue || fallback || '').trim();
}

function errorCode(payload, fallback) {
  if (!payload || typeof payload !== 'object') return fallback;
  const nested = payload.error && typeof payload.error === 'object' ? payload.error.code : '';
  return String(nested || payload.code || fallback).trim() || fallback;
}

function classifyLivenessResponse(response, fallbackRequestId = '') {
  const payload = response?.payload;
  const alive = Boolean(
    response?.statusCode === 200
    && payload?.status === 'ok'
    && payload?.service === 'aetherchat',
  );
  return {
    alive,
    code: alive ? '' : 'backend_unreachable',
    requestId: normalizedRequestId(response?.headers, payload, fallbackRequestId),
  };
}

function classifyReadinessResponse(response, fallbackRequestId = '') {
  const payload = response?.payload;
  const ready = Boolean(
    response?.statusCode === 200
    && payload?.ready === true
    && payload?.status === 'ready'
    && payload?.service === 'aetherchat',
  );
  const code = ready ? '' : errorCode(payload, 'backend_not_ready');
  return {
    ready,
    code,
    requestId: normalizedRequestId(response?.headers, payload, fallbackRequestId),
    terminal: !ready && TERMINAL_READINESS_CODES.has(code),
  };
}

function requestJson(port, endpoint, timeoutMs = 1000) {
  const requestId = `desktop-${randomUUID()}`;
  return new Promise((resolve) => {
    let settled = false;
    const finish = (result) => {
      if (settled) return;
      settled = true;
      resolve({ ...result, clientRequestId: requestId });
    };
    const request = http.request({
      hostname: '127.0.0.1',
      port,
      path: endpoint,
      method: 'GET',
      headers: {
        Accept: 'application/json',
        'X-Request-ID': requestId,
      },
      timeout: timeoutMs,
    }, (response) => {
      const chunks = [];
      let size = 0;
      response.on('data', (chunk) => {
        size += chunk.length;
        if (size > MAX_RESPONSE_BYTES) {
          request.destroy(new Error('Backend health response exceeded the size limit'));
          return;
        }
        chunks.push(chunk);
      });
      response.on('end', () => {
        let payload = null;
        try {
          payload = JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}');
        } catch {
          // Invalid responses are classified through the stable fallback code.
        }
        finish({
          statusCode: response.statusCode || 0,
          headers: response.headers,
          payload,
        });
      });
    });
    request.on('error', () => finish({ statusCode: 0, headers: {}, payload: null }));
    request.on('timeout', () => request.destroy(new Error('Backend health request timed out')));
    request.end();
  });
}

async function probeBackendLiveness(port, timeoutMs = 1000) {
  const response = await requestJson(port, '/health', timeoutMs);
  return classifyLivenessResponse(response, response.clientRequestId);
}

async function probeBackendReadiness(port, timeoutMs = 1000) {
  const response = await requestJson(port, '/health/ready', timeoutMs);
  if (response.statusCode === 0) {
    return {
      ready: false,
      code: 'backend_unreachable',
      requestId: response.clientRequestId,
      terminal: false,
    };
  }
  return classifyReadinessResponse(response, response.clientRequestId);
}

async function waitForBackendReadiness({
  port,
  timeoutMs = 15000,
  intervalMs = 300,
  probe = probeBackendReadiness,
} = {}) {
  const startedAt = Date.now();
  let lastBackendResponse = null;
  let lastResult = null;

  do {
    lastResult = await probe(port, Math.min(1000, Math.max(1, timeoutMs)));
    if (lastResult.ready || lastResult.terminal) return lastResult;
    if (lastResult.code !== 'backend_unreachable') lastBackendResponse = lastResult;
    if (Date.now() - startedAt >= timeoutMs) break;
    await new Promise((resolve) => setTimeout(resolve, intervalMs));
  } while (Date.now() - startedAt < timeoutMs);

  if (lastBackendResponse) return lastBackendResponse;
  return {
    ready: false,
    code: 'backend_start_timeout',
    requestId: lastResult?.requestId || '',
    terminal: false,
  };
}

module.exports = {
  classifyLivenessResponse,
  classifyReadinessResponse,
  probeBackendLiveness,
  probeBackendReadiness,
  waitForBackendReadiness,
};
