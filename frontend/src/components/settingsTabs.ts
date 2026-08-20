export const SETTINGS_GROUPS = [
  {
    id: 'chat',
    label: 'chatAndModels',
    tabs: ['settings', 'voice', 'models'] as const,
  },
  {
    id: 'knowledge',
    label: 'knowledgeAndMemory',
    tabs: ['memory', 'knowledge', 'artifacts'] as const,
  },
  {
    id: 'agent',
    label: 'agentsAndAutomation',
    tabs: ['runs', 'tasks', 'automations', 'tools', 'plugins', 'instructions'] as const,
  },
  {
    id: 'advanced',
    label: 'advanced',
    tabs: ['connections', 'computer', 'changes', 'governance'] as const,
  },
] as const;

export const SETTINGS_TABS = SETTINGS_GROUPS.flatMap((group) => [...group.tabs]);

export type SettingsTabId = (typeof SETTINGS_TABS)[number];

export function isSettingsTab(value: string | null | undefined): value is SettingsTabId {
  return Boolean(value && SETTINGS_TABS.some((id) => id === value));
}
