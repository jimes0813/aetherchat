import { describe, expect, it } from 'vitest';
import { MODE_TO_SETTING, SETTING_TO_MODE } from './collaborationModes';

describe('collaborationModes', () => {
  it('round-trips the three workspace modes', () => {
    expect(SETTING_TO_MODE[MODE_TO_SETTING.casual]).toBe('casual');
    expect(SETTING_TO_MODE[MODE_TO_SETTING.roundtable]).toBe('roundtable');
    expect(SETTING_TO_MODE[MODE_TO_SETTING.project]).toBe('project');
  });
});
