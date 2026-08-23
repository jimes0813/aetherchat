import type { TranslationKey } from '../i18n';

export const SETTINGS_NAV = [
  { id: 'general', label: 'general', panels: ['settings'] },
  { id: 'project', label: 'projectWorkspace', panels: ['memory', 'knowledge', 'artifacts', 'changes', 'tasks'] },
  { id: 'automation', label: 'automations', panels: ['automations'] },
  { id: 'capabilities', label: 'capabilities', panels: ['models', 'voice', 'plugins', 'connections', 'computer'] },
  { id: 'runtime', label: 'runtimeAndSecurity', panels: ['runs', 'instructions'] },
  { id: 'developer', label: 'developerTools', panels: ['tools', 'governance'], developerOnly: true },
] as const;

export const SETTINGS_NAV_TABS = SETTINGS_NAV.map((group) => group.panels[0]);
export const SETTINGS_TABS = SETTINGS_NAV.flatMap((group) => [...group.panels]);

export type SettingsTabId = (typeof SETTINGS_TABS)[number];
export type SettingsNavId = (typeof SETTINGS_NAV)[number]['id'];

export function visibleSettingsNav(developerMode = false) {
  return SETTINGS_NAV.filter((group) => !('developerOnly' in group) || developerMode);
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
