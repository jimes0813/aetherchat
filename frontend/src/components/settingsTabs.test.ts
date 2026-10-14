import { describe, expect, it } from 'vitest';
import { isAdvancedPanel, visibleSettingsNav } from './settingsTabs';

describe('visibleSettingsNav workspace modes', () => {
  it('hides automations and computer in simple mode', () => {
    const simple = visibleSettingsNav(false, 'simple');
    const ids = simple.map((group) => group.id);
    expect(ids).not.toContain('automation');
    const capabilities = simple.find((group) => group.id === 'capabilities');
    expect(capabilities?.panels).toEqual(['models']);
  });

  it('shows automations in advanced mode', () => {
    const advanced = visibleSettingsNav(false, 'advanced');
    expect(advanced.some((group) => group.id === 'automation')).toBe(true);
  });

  it('marks computer as advanced panel', () => {
    expect(isAdvancedPanel('computer')).toBe(true);
    expect(isAdvancedPanel('settings')).toBe(false);
    expect(isAdvancedPanel('models')).toBe(false);
  });
});
