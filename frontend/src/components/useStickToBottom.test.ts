import { describe, expect, it } from 'vitest';
import { isNearBottom } from './useStickToBottom';

describe('isNearBottom', () => {
  it('treats the last 96px as following the live tail', () => {
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 910, clientHeight: 80 })).toBe(true);
    expect(isNearBottom({ scrollHeight: 1000, scrollTop: 100, clientHeight: 400 })).toBe(false);
  });
});
