import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { WSMessage } from '../types';
import { connectWS } from './client';

class MockWebSocket {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: MockWebSocket[] = [];

  readonly url: string;
  readyState = MockWebSocket.CONNECTING;
  onopen: ((event: Event) => void) | null = null;
  onmessage: ((event: MessageEvent) => void) | null = null;
  onerror: ((event: Event) => void) | null = null;
  onclose: ((event: CloseEvent) => void) | null = null;

  protocols: string[] | undefined;

  constructor(url: string | URL, protocols?: string | string[]) {
    this.url = String(url);
    this.protocols = typeof protocols === 'string' ? [protocols] : protocols;
    MockWebSocket.instances.push(this);
  }

  open(): void {
    this.readyState = MockWebSocket.OPEN;
    this.onopen?.(new Event('open'));
  }

  emit(payload: unknown): void {
    this.onmessage?.(new MessageEvent('message', {
      data: JSON.stringify(payload),
    }));
  }

  serverClose(code: number): void {
    this.readyState = MockWebSocket.CLOSED;
    this.onclose?.({ code } as CloseEvent);
  }

  send(): void {}

  close(): void {
    this.serverClose(1000);
  }
}

describe('connectWS persistent cursor reconnect', () => {
  beforeEach(() => {
    vi.useFakeTimers();
    window.sessionStorage.clear();
    MockWebSocket.instances = [];
    vi.stubGlobal('WebSocket', MockWebSocket as unknown as typeof WebSocket);
  });

  afterEach(() => {
    vi.useRealTimers();
    vi.unstubAllGlobals();
  });

  it('keeps the stream and cursor across reconnect, then rotates after reset code', async () => {
    const messages: WSMessage[] = [];
    const client = connectWS('session-1', (message) => messages.push(message));
    await Promise.resolve();
    const first = MockWebSocket.instances[0];
    first.open();
    const firstUrl = new URL(first.url);
    const firstStreamId = firstUrl.searchParams.get('stream_id');
    expect(firstStreamId).toBeTruthy();
    expect(firstUrl.searchParams.get('cursor')).toBe('0');

    first.emit({
      type: 'ack',
      contract_version: '1',
      event_id: 'event-1',
      sequence: 1,
      session_id: 'session-1',
      stream_id: firstStreamId,
      occurred_at: '2026-08-11T08:00:00Z',
    });
    first.serverClose(1006);
    vi.advanceTimersByTime(500);
    await Promise.resolve();

    const second = MockWebSocket.instances[1];
    const secondUrl = new URL(second.url);
    expect(secondUrl.searchParams.get('stream_id')).toBe(firstStreamId);
    expect(secondUrl.searchParams.get('cursor')).toBe('1');

    second.open();
    second.serverClose(4009);
    vi.advanceTimersByTime(500);
    await Promise.resolve();

    const thirdUrl = new URL(MockWebSocket.instances[2].url);
    expect(thirdUrl.searchParams.get('stream_id')).not.toBe(firstStreamId);
    expect(thirdUrl.searchParams.get('cursor')).toBe('0');
    expect(messages.some((message) => message.type === 'stream_resync_required')).toBe(true);
    client.close();
  });

  it('resolves a token getter at connect and does not keep a string copy in options', async () => {
    const getToken = vi.fn(async () => 'secret-token');
    const client = connectWS('session-token', () => undefined, getToken);
    await Promise.resolve();
    expect(getToken).toHaveBeenCalledTimes(1);
    expect(MockWebSocket.instances[0].protocols).toEqual([
      'aetherchat',
      'aetherchat.auth.secret-token',
    ]);
    client.close();
  });
});
