import type { TranslationKey } from '../i18n';

/** Preference categories shown in Settings, matching Codex's sparse list. */
export const SETTINGS_NAV_TABS = [
  'settings',
  'models',
  'voice',
  'instructions',
  'memory',
  'computer',
  'connections',
  'plugins',
  'governance',
] as const;

/** Workbench surfaces. Reachable from the command palette, not the Settings nav. */
export const WORKSPACE_TABS = [
  'knowledge',
  'artifacts',
  'runs',
  'tasks',
  'automations',
  'tools',
  'changes',
] as const;

export const SETTINGS_TABS = [...SETTINGS_NAV_TABS, ...WORKSPACE_TABS];

export type SettingsTabId = (typeof SETTINGS_TABS)[number];
export type SettingsNavTabId = (typeof SETTINGS_NAV_TABS)[number];
export type WorkspaceTabId = (typeof WORKSPACE_TABS)[number];

const NAV_LABEL: Partial<Record<SettingsTabId, TranslationKey>> = {
  settings: 'general',
  instructions: 'personalization',
  computer: 'computerUse',
  governance: 'advanced',
};

export function settingsNavLabelKey(id: SettingsTabId): TranslationKey {
  return NAV_LABEL[id] ?? id;
}

export function isSettingsTab(value: string | null | undefined): value is SettingsTabId {
  return Boolean(value && SETTINGS_TABS.some((id) => id === value));
}

export function isSettingsNavTab(value: string | null | undefined): value is SettingsNavTabId {
  return Boolean(value && SETTINGS_NAV_TABS.some((id) => id === value));
}

export function isWorkspaceTab(value: string | null | undefined): value is WorkspaceTabId {
  return Boolean(value && WORKSPACE_TABS.some((id) => id === value));
}
