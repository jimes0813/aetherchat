export interface EventWatermarkSnapshot {
  highestSequence: number;
  trackedEventIds: number;
}

type VersionedEvent = {
  contract_version?: unknown;
  event_id?: unknown;
  sequence?: unknown;
  stream_id?: unknown;
};

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

/**
 * Guards one logical WebSocket stream against duplicate and stale events.
 * Legacy messages without a contract version remain accepted during rollout.
 */
export class EventWatermark {
  private highestSequence: number;
  private expectedStreamId?: string;
  private readonly eventIds = new Set<string>();
  private readonly eventIdOrder: string[] = [];

  constructor(
    private readonly maxTrackedEventIds = 2048,
    initialHighestSequence = 0,
    expectedStreamId?: string,
  ) {
    if (!Number.isInteger(maxTrackedEventIds) || maxTrackedEventIds < 1) {
      throw new RangeError('maxTrackedEventIds must be a positive integer');
    }
    if (!Number.isSafeInteger(initialHighestSequence) || initialHighestSequence < 0) {
      throw new RangeError('initialHighestSequence must be a non-negative safe integer');
    }
    this.highestSequence = initialHighestSequence;
    this.expectedStreamId = expectedStreamId;
  }

  accept(value: unknown): boolean {
    if (!isRecord(value) || !('contract_version' in value)) return true;

    const event = value as VersionedEvent;
    if (
      typeof event.contract_version !== 'string'
      || !event.contract_version
      || typeof event.event_id !== 'string'
      || !event.event_id
      || !Number.isSafeInteger(event.sequence)
      || Number(event.sequence) < 1
      || (
        this.expectedStreamId !== undefined
        && event.stream_id !== this.expectedStreamId
      )
    ) {
      return false;
    }

    const sequence = Number(event.sequence);
    if (this.eventIds.has(event.event_id) || sequence <= this.highestSequence) {
      return false;
    }

    this.highestSequence = sequence;
    this.eventIds.add(event.event_id);
    this.eventIdOrder.push(event.event_id);
    while (this.eventIdOrder.length > this.maxTrackedEventIds) {
      const expired = this.eventIdOrder.shift();
      if (expired) this.eventIds.delete(expired);
    }
    return true;
  }

  reset(highestSequence = 0, expectedStreamId = this.expectedStreamId): void {
    if (!Number.isSafeInteger(highestSequence) || highestSequence < 0) {
      throw new RangeError('highestSequence must be a non-negative safe integer');
    }
    this.highestSequence = highestSequence;
    this.expectedStreamId = expectedStreamId;
    this.eventIds.clear();
    this.eventIdOrder.length = 0;
  }

  snapshot(): EventWatermarkSnapshot {
    return {
      highestSequence: this.highestSequence,
      trackedEventIds: this.eventIds.size,
    };
  }
}
