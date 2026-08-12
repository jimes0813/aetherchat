export interface EventStreamState {
  streamId: string;
  cursor: number;
}

const STORAGE_PREFIX = 'aetherchat.ws.v1.stream';
const STREAM_ID_PATTERN = /^[A-Za-z0-9][A-Za-z0-9._:-]{0,127}$/;

const createStreamId = () => globalThis.crypto?.randomUUID?.()
  ?? `stream-${Date.now()}-${Math.random().toString(16).slice(2)}`;

const defaultStorage = (): Storage | null => {
  try {
    return typeof window === 'undefined' ? null : window.sessionStorage;
  } catch {
    return null;
  }
};

export const eventStreamStorageKey = (sessionId: string) => (
  `${STORAGE_PREFIX}:${encodeURIComponent(sessionId)}`
);

const validState = (value: unknown): value is EventStreamState => {
  if (typeof value !== 'object' || value === null || Array.isArray(value)) return false;
  const candidate = value as Partial<EventStreamState>;
  return (
    typeof candidate.streamId === 'string'
    && STREAM_ID_PATTERN.test(candidate.streamId)
    && Number.isSafeInteger(candidate.cursor)
    && Number(candidate.cursor) >= 0
  );
};

export function persistEventStreamState(
  sessionId: string,
  state: EventStreamState,
  storage: Storage | null = defaultStorage(),
): void {
  if (!storage || !validState(state)) return;
  try {
    storage.setItem(eventStreamStorageKey(sessionId), JSON.stringify(state));
  } catch {
    // A denied or full sessionStorage must not prevent a live connection.
  }
}

export function newEventStreamState(
  sessionId: string,
  options: {
    storage?: Storage | null;
    idFactory?: () => string;
  } = {},
): EventStreamState {
  const idFactory = options.idFactory ?? createStreamId;
  const generated = String(idFactory()).trim();
  const state = {
    streamId: STREAM_ID_PATTERN.test(generated) ? generated : createStreamId(),
    cursor: 0,
  };
  persistEventStreamState(sessionId, state, options.storage ?? defaultStorage());
  return state;
}

export function loadEventStreamState(
  sessionId: string,
  options: {
    storage?: Storage | null;
    idFactory?: () => string;
  } = {},
): EventStreamState {
  const storage = options.storage ?? defaultStorage();
  if (storage) {
    try {
      const raw = storage.getItem(eventStreamStorageKey(sessionId));
      if (raw) {
        const parsed: unknown = JSON.parse(raw);
        if (validState(parsed)) return { ...parsed };
      }
    } catch {
      // Replace malformed or inaccessible state with a fresh logical stream.
    }
  }
  return newEventStreamState(sessionId, { ...options, storage });
}
