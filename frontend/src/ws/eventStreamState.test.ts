import { beforeEach, describe, expect, it } from 'vitest';
import {
  eventStreamStorageKey,
  loadEventStreamState,
  newEventStreamState,
  persistEventStreamState,
} from './eventStreamState';

describe('event stream state', () => {
  beforeEach(() => {
    window.sessionStorage.clear();
  });

  it('creates and restores a tab-scoped stream cursor', () => {
    const created = loadEventStreamState('session-1', {
      storage: window.sessionStorage,
      idFactory: () => 'stream-1',
    });
    expect(created).toEqual({ streamId: 'stream-1', cursor: 0 });

    persistEventStreamState(
      'session-1',
      { ...created, cursor: 17 },
      window.sessionStorage,
    );
    expect(loadEventStreamState('session-1', {
      storage: window.sessionStorage,
      idFactory: () => 'unused-stream',
    })).toEqual({ streamId: 'stream-1', cursor: 17 });
  });

  it('replaces malformed stored state instead of trusting its cursor', () => {
    window.sessionStorage.setItem(
      eventStreamStorageKey('session-2'),
      JSON.stringify({ streamId: 'invalid stream', cursor: -1 }),
    );

    expect(loadEventStreamState('session-2', {
      storage: window.sessionStorage,
      idFactory: () => 'stream-2',
    })).toEqual({ streamId: 'stream-2', cursor: 0 });
  });

  it('rotates to a fresh stream when the server requires snapshot resync', () => {
    const first = newEventStreamState('session-3', {
      storage: window.sessionStorage,
      idFactory: () => 'stream-old',
    });
    const rotated = newEventStreamState('session-3', {
      storage: window.sessionStorage,
      idFactory: () => 'stream-new',
    });

    expect(first.streamId).toBe('stream-old');
    expect(rotated).toEqual({ streamId: 'stream-new', cursor: 0 });
    expect(loadEventStreamState('session-3', {
      storage: window.sessionStorage,
    })).toEqual(rotated);
  });
});
