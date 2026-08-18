import { afterEach, describe, expect, it, vi } from 'vitest';
import { ApiError, asArray, fetchBlob, fetchJson, setApiAuthToken, withApiAuth } from './client';

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

  it('normalizes malformed collection payloads to an empty array', () => {
    expect(asArray<number>([1, 2])).toEqual([1, 2]);
    expect(asArray<number>({})).toEqual([]);
    expect(asArray<number>(null)).toEqual([]);
  });

  it('preserves FastAPI detail codes and recovery hints', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response(JSON.stringify({
      detail: {
        code: 'cosyvoice_exited',
        message: 'CosyVoice 启动失败',
        hints: ['检查本机日志'],
      },
    }), {
      status: 422,
      headers: { 'content-type': 'application/json' },
    })));

    const error = await fetchBlob('/voice/tts', { method: 'POST' }).catch((value) => value) as ApiError;

    expect(error).toMatchObject({
      status: 422,
      code: 'cosyvoice_exited',
      hints: ['检查本机日志'],
    });
    expect(error.message).toContain('CosyVoice 启动失败：检查本机日志');
  });
});
