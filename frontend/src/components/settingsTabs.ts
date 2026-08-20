import type { TranslationKey } from '../i18n';

export const SETTINGS_NAV = [
  { id: 'general', label: 'general', panels: ['settings'] },
  { id: 'models', label: 'models', panels: ['models'] },
  { id: 'voice', label: 'voice', panels: ['voice'] },
  { id: 'personalization', label: 'personalization', panels: ['instructions'] },
  { id: 'memory', label: 'memory', panels: ['memory', 'knowledge'] },
  { id: 'files', label: 'files', panels: ['artifacts', 'changes'] },
  { id: 'agent', label: 'agentsAndAutomation', panels: ['tasks', 'automations', 'runs'] },
  { id: 'integrations', label: 'integrations', panels: ['tools', 'plugins', 'connections'] },
  { id: 'computer', label: 'computerUse', panels: ['computer'] },
  { id: 'advanced', label: 'advanced', panels: ['governance'] },
] as const;

export const SETTINGS_NAV_TABS = SETTINGS_NAV.map((group) => group.panels[0]);
export const SETTINGS_TABS = SETTINGS_NAV.flatMap((group) => [...group.panels]);

export type SettingsTabId = (typeof SETTINGS_TABS)[number];
export type SettingsNavId = (typeof SETTINGS_NAV)[number]['id'];

export function navGroupForPanel(panel: SettingsTabId) {
  return SETTINGS_NAV.find((group) => (group.panels as readonly string[]).includes(panel)) ?? SETTINGS_NAV[0];
}

export function panelsForTab(panel: SettingsTabId): readonly SettingsTabId[] {
  return navGroupForPanel(panel).panels as readonly SettingsTabId[];
}

export function settingsNavLabelKey(panel: SettingsTabId): TranslationKey {
  return navGroupForPanel(panel).label;
}

export function isSettingsTab(value: string | null | undefined): value is SettingsTabId {
  return Boolean(value && SETTINGS_TABS.some((id) => id === value));
}
