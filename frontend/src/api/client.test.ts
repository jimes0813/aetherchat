import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, fetchJson, setApiAuthToken, withApiAuth } from './client';

describe('API client', () => {
  afterEach(() => {
    setApiAuthToken('');
    vi.unstubAllGlobals();
  });

  it('adds local authorization and a request id without replacing caller headers', () => {
    setApiAuthToken('local-token');
    const result = withApiAuth({ headers: { 'X-Request-ID': 'caller-request' } });
    const headers = new Headers(result.headers);

    expect(headers.get('Authorization')).toBe('Bearer local-token');
    expect(headers.get('X-Request-ID')).toBe('caller-request');
  });

  it('normalizes nested backend failures into stable structured fields', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      status: 'not_ready',
      request_id: 'body-request',
      error: { code: 'database_schema_mismatch', message: 'Core database schema is incompatible' },
    }), {
      status: 503,
      headers: {
        'content-type': 'application/json',
        'X-Request-ID': 'header-request',
      },
    })));

    const error = await fetchJson('/health/ready').catch((value) => value) as ApiError;

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      status: 503,
      code: 'database_schema_mismatch',
      requestId: 'header-request',
    });
    expect(error.message).toContain('诊断 ID：header-request');
  });
});
