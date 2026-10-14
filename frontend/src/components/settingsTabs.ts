import type { TranslationKey } from '../i18n';

export const SETTINGS_NAV = [
  { id: 'general', label: 'general', panels: ['settings'] },
  { id: 'project', label: 'projectWorkspace', panels: ['memory', 'knowledge', 'artifacts', 'changes', 'tasks'] },
  { id: 'automation', label: 'automations', panels: ['automations'], advancedOnly: true },
  {
    id: 'capabilities',
    label: 'capabilities',
    panels: ['models', 'voice', 'plugins', 'connections', 'computer'],
    simplePanels: ['models'],
  },
  { id: 'runtime', label: 'runtimeAndSecurity', panels: ['runs', 'instructions'] },
  { id: 'developer', label: 'developerTools', panels: ['tools', 'governance'], developerOnly: true },
] as const;

export const SETTINGS_NAV_TABS = SETTINGS_NAV.map((group) => group.panels[0]);
export const SETTINGS_TABS = SETTINGS_NAV.flatMap((group) => [...group.panels]);

export type SettingsTabId = (typeof SETTINGS_TABS)[number];
export type SettingsNavId = (typeof SETTINGS_NAV)[number]['id'];
export type WorkspaceUiMode = 'simple' | 'advanced';

export function visibleSettingsNav(
  developerMode = false,
  workspaceMode: WorkspaceUiMode = 'simple',
) {
  return SETTINGS_NAV
    .filter((group) => {
      if ('developerOnly' in group && group.developerOnly && !developerMode) return false;
      if ('advancedOnly' in group && group.advancedOnly && workspaceMode === 'simple') return false;
      return true;
    })
    .map((group) => {
      if (
        workspaceMode === 'simple'
        && 'simplePanels' in group
        && Array.isArray(group.simplePanels)
      ) {
        return { ...group, panels: group.simplePanels };
      }
      return group;
    });
}

export function isAdvancedPanel(panel: SettingsTabId): boolean {
  for (const group of SETTINGS_NAV) {
    if (!group.panels.includes(panel as never)) continue;
    if ('advancedOnly' in group && group.advancedOnly) return true;
    if ('simplePanels' in group && Array.isArray(group.simplePanels)) {
      return !group.simplePanels.includes(panel as never);
    }
    if ('developerOnly' in group && group.developerOnly) return true;
  }
  return false;
}

export function navGroupForPanel(panel: SettingsTabId) {
  return SETTINGS_NAV.find((group) => (group.panels as readonly string[]).includes(panel)) ?? SETTINGS_NAV[0];
}

export function panelsForTab(panel: SettingsTabId): readonly SettingsTabId[] {
  return [panel];
}

export function settingsNavLabelKey(panel: SettingsTabId): TranslationKey {
  return navGroupForPanel(panel).label;
}

export function isSettingsTab(value: string | null | undefined): value is SettingsTabId {
  return Boolean(value && SETTINGS_TABS.some((id) => id === value));
}
