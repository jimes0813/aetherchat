// frontend/src/ws/client.ts
import type { WSMessage } from '../types';
import {
  loadEventStreamState,
  newEventStreamState,
  persistEventStreamState,
} from './eventStreamState';
import { EventWatermark } from './eventWatermark';

const CURSOR_RESET_CLOSE_CODE = 4009;

export function connectWS(
  sessionId: string,
  onMessage: (data: WSMessage) => void,
  token?: string,
  apiBase = 'http://localhost:8000',
) {
  const createRequestId = () => globalThis.crypto?.randomUUID?.()
    ?? `ws-${Date.now()}-${Math.random().toString(16).slice(2)}`;
  let ws: WebSocket | null = null;
  let closedByClient = false;
  let reconnectTimer: number | undefined;
  let reconnectAttempts = 0;
  let streamState = loadEventStreamState(sessionId);
  const eventWatermark = new EventWatermark(
    2048,
    streamState.cursor,
    streamState.streamId,
  );
  const url = new URL(apiBase, window.location.href);
  if (url.protocol === 'http:') url.protocol = 'ws:';
  if (url.protocol === 'https:') url.protocol = 'wss:';
  url.pathname = `${url.pathname.replace(/\/$/, '')}/ws/v1/sessions/${encodeURIComponent(sessionId)}`;
  url.search = '';
  url.hash = '';

  const open = () => {
    if (closedByClient) return;
    url.searchParams.set('stream_id', streamState.streamId);
    url.searchParams.set('cursor', String(eventWatermark.snapshot().highestSequence));
    let socket: WebSocket;
    try {
      const protocols = token ? ['aetherchat', `aetherchat.auth.${token}`] : undefined;
      socket = new WebSocket(url.toString(), protocols);
    } catch (error) {
      console.error('WS connection error', error);
      onMessage({ type: 'system', content: 'Disconnected' });
      reconnectTimer = window.setTimeout(open, 1000);
      return;
    }
    ws = socket;

    socket.onmessage = (event) => {
      if (socket !== ws || closedByClient) return;
      try {
        const msg: unknown = JSON.parse(event.data);
        if (eventWatermark.accept(msg)) {
          const cursor = eventWatermark.snapshot().highestSequence;
          if (cursor !== streamState.cursor) {
            streamState = { ...streamState, cursor };
            persistEventStreamState(sessionId, streamState);
          }
          onMessage(msg as WSMessage);
        }
      } catch (e) {
        console.error('WS parse error', e);
      }
    };

    socket.onopen = () => {
      if (socket !== ws || closedByClient) return;
      reconnectAttempts = 0;
      onMessage({ type: 'system', content: 'Connected' });
    };

    socket.onerror = (e) => {
      console.error('WS error', e);
    };

    socket.onclose = (event) => {
      if (socket !== ws || closedByClient) return;
      ws = null;
      if (event.code === CURSOR_RESET_CLOSE_CODE) {
        streamState = newEventStreamState(sessionId);
        eventWatermark.reset(0, streamState.streamId);
        onMessage({
          type: 'stream_resync_required',
          content: 'Event cursor expired; refreshing the session snapshot',
        });
      }
      onMessage({ type: 'system', content: 'Disconnected' });
      const delay = Math.min(5000, 500 + reconnectAttempts * 500);
      reconnectAttempts += 1;
      reconnectTimer = window.setTimeout(open, delay);
    };
  };

  open();

  return {
    sendAction: (action: any) => {
      if (ws?.readyState !== WebSocket.OPEN) return false;
      try {
        ws.send(JSON.stringify({
          ...action,
          request_id: action?.request_id || createRequestId(),
        }));
        return true;
      } catch (error) {
        console.error('WS send error', error);
        return false;
      }
    },
    close: () => {
      closedByClient = true;
      if (reconnectTimer) window.clearTimeout(reconnectTimer);
      ws?.close();
      ws = null;
    },
    isOpen: () => ws?.readyState === WebSocket.OPEN
  };
}
