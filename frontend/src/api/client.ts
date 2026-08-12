let apiAuthToken = '';

export function createRequestId() {
  return globalThis.crypto?.randomUUID?.()
    ?? `web-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId?: string;

  constructor({
    message,
    status,
    code,
    requestId,
  }: {
    message: string;
    status: number;
    code: string;
    requestId?: string;
  }) {
    const diagnostics = [
      code ? `错误码：${code}` : '',
      requestId ? `诊断 ID：${requestId}` : '',
    ].filter(Boolean).join('；');
    super(diagnostics ? `${message}（${diagnostics}）` : message);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
  }
}

export function setApiAuthToken(token: string) {
  apiAuthToken = String(token || '');
}

export function withApiAuth(init: RequestInit = {}): RequestInit {
  const headers = new Headers(init.headers);
  if (apiAuthToken) headers.set('Authorization', `Bearer ${apiAuthToken}`);
  if (!headers.has('X-Request-ID')) headers.set('X-Request-ID', createRequestId());
  return { ...init, headers };
}

function objectValue(value: unknown): Record<string, unknown> | null {
  return value !== null && typeof value === 'object'
    ? value as Record<string, unknown>
    : null;
}

function nonEmptyString(value: unknown) {
  return typeof value === 'string' && value.trim() ? value.trim() : '';
}

function parseError(data: unknown, response: Response) {
  const body = objectValue(data);
  const nested = objectValue(body?.error);
  const requestId = response.headers.get('X-Request-ID')
    || nonEmptyString(body?.request_id)
    || undefined;
  const code = nonEmptyString(nested?.code)
    || nonEmptyString(body?.code)
    || `http_${response.status}`;
  const message = nonEmptyString(nested?.message)
    || nonEmptyString(body?.detail)
    || nonEmptyString(body?.message)
    || (typeof data === 'string' ? nonEmptyString(data) : '')
    || `HTTP ${response.status}`;
  return { message, status: response.status, code, requestId };
}

export async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, withApiAuth(init));
  const contentType = response.headers.get('content-type') ?? '';
  const data = contentType.includes('application/json')
    ? await response.json().catch(() => ({}))
    : await response.text().catch(() => '');
  if (!response.ok) throw new ApiError(parseError(data, response));
  return data as T;
}
