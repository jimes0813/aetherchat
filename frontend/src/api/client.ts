let apiAuthToken = '';

export function createRequestId() {
  return globalThis.crypto?.randomUUID?.()
    ?? `web-${Date.now()}-${Math.random().toString(16).slice(2)}`;
}

export class ApiError extends Error {
  readonly status: number;
  readonly code: string;
  readonly requestId?: string;
  readonly hints: string[];

  constructor({
    message,
    status,
    code,
    requestId,
    hints = [],
  }: {
    message: string;
    status: number;
    code: string;
    requestId?: string;
    hints?: string[];
  }) {
    const guidance = hints.filter((item) => item.trim()).join('；');
    const diagnostics = [
      code ? `错误码：${code}` : '',
      requestId ? `诊断 ID：${requestId}` : '',
    ].filter(Boolean).join('；');
    const described = guidance ? `${message}：${guidance}` : message;
    super(diagnostics ? `${described}（${diagnostics}）` : described);
    this.name = 'ApiError';
    this.status = status;
    this.code = code;
    this.requestId = requestId;
    this.hints = hints;
  }
}

export function setApiAuthToken(token: string) {
  apiAuthToken = String(token || '');
}

export function asArray<T>(value: unknown): T[] {
  return Array.isArray(value) ? value as T[] : [];
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
  const detail = objectValue(body?.detail);
  const requestId = response.headers.get('X-Request-ID')
    || nonEmptyString(body?.request_id)
    || undefined;
  const code = nonEmptyString(nested?.code)
    || nonEmptyString(detail?.code)
    || nonEmptyString(body?.code)
    || `http_${response.status}`;
  const message = nonEmptyString(nested?.message)
    || nonEmptyString(detail?.message)
    || nonEmptyString(body?.detail)
    || nonEmptyString(body?.message)
    || (typeof data === 'string' ? nonEmptyString(data) : '')
    || `HTTP ${response.status}`;
  const rawHints = nested?.hints ?? detail?.hints ?? body?.hints;
  const hints = Array.isArray(rawHints)
    ? rawHints.map(nonEmptyString).filter(Boolean)
    : [];
  return { message, status: response.status, code, requestId, hints };
}

async function responseData(response: Response): Promise<unknown> {
  const contentType = response.headers.get('content-type') ?? '';
  return contentType.includes('application/json')
    ? response.json().catch(() => ({}))
    : response.text().catch(() => '');
}

export async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, withApiAuth(init));
  const data = await responseData(response);
  if (!response.ok) throw new ApiError(parseError(data, response));
  return data as T;
}

export async function fetchBlob(url: string, init?: RequestInit): Promise<Blob> {
  const response = await fetch(url, withApiAuth(init));
  if (!response.ok) {
    throw new ApiError(parseError(await responseData(response), response));
  }
  return response.blob();
}
