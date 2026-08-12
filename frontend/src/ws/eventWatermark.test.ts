import { describe, expect, it } from 'vitest';
import { EventWatermark } from './eventWatermark';

const event = (eventId: string, sequence: number, streamId?: string) => ({
  contract_version: '1',
  event_id: eventId,
  sequence,
  stream_id: streamId,
  type: 'delta',
});

describe('EventWatermark', () => {
  it('keeps legacy messages compatible during the versioned rollout', () => {
    const watermark = new EventWatermark();

    expect(watermark.accept({ type: 'system', content: 'Connected' })).toBe(true);
    expect(watermark.snapshot()).toEqual({ highestSequence: 0, trackedEventIds: 0 });
  });

  it('accepts increasing events and rejects duplicate or stale delivery', () => {
    const watermark = new EventWatermark();

    expect(watermark.accept(event('event-1', 1))).toBe(true);
    expect(watermark.accept(event('event-2', 3))).toBe(true);
    expect(watermark.accept(event('event-2', 4))).toBe(false);
    expect(watermark.accept(event('event-late', 2))).toBe(false);
    expect(watermark.snapshot()).toEqual({ highestSequence: 3, trackedEventIds: 2 });
  });

  it('rejects malformed versioned events instead of mutating the UI', () => {
    const watermark = new EventWatermark();

    expect(watermark.accept({ contract_version: '1', type: 'delta' })).toBe(false);
    expect(watermark.accept(event('zero', 0))).toBe(false);
    expect(watermark.snapshot()).toEqual({ highestSequence: 0, trackedEventIds: 0 });
  });

  it('resets connection-scoped state before a reconnect stream starts', () => {
    const watermark = new EventWatermark(2);
    expect(watermark.accept(event('old-1', 1))).toBe(true);
    expect(watermark.accept(event('old-2', 2))).toBe(true);

    watermark.reset();

    expect(watermark.accept(event('new-1', 1))).toBe(true);
    expect(watermark.snapshot()).toEqual({ highestSequence: 1, trackedEventIds: 1 });
  });

  it('restores a logical stream watermark and rejects another stream', () => {
    const watermark = new EventWatermark(8, 4, 'stream-1');

    expect(watermark.accept(event('event-5', 5, 'stream-1'))).toBe(true);
    expect(watermark.accept(event('event-6', 6, 'stream-2'))).toBe(false);
    expect(watermark.snapshot()).toEqual({ highestSequence: 5, trackedEventIds: 1 });

    watermark.reset(0, 'stream-2');
    expect(watermark.accept(event('stream-2-event-1', 1, 'stream-2'))).toBe(true);
    expect(watermark.snapshot()).toEqual({ highestSequence: 1, trackedEventIds: 1 });
  });
});
