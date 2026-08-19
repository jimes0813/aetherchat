export const SETTINGS_GROUPS = [
  {
    id: 'chat',
    label: '对话与模型',
    tabs: ['settings', 'voice', 'models'] as const,
  },
  {
    id: 'knowledge',
    label: '知识与记忆',
    tabs: ['memory', 'knowledge', 'artifacts'] as const,
  },
  {
    id: 'agent',
    label: '代理与自动化',
    tabs: ['runs', 'tasks', 'automations', 'tools', 'plugins', 'instructions'] as const,
  },
  {
    id: 'advanced',
    label: '高级',
    tabs: ['connections', 'computer', 'changes', 'governance'] as const,
  },
] as const;

export const SETTINGS_TABS = SETTINGS_GROUPS.flatMap((group) => [...group.tabs]);

export type SettingsTabId = (typeof SETTINGS_TABS)[number];

export function isSettingsTab(value: string | null | undefined): value is SettingsTabId {
  return Boolean(value && SETTINGS_TABS.some((id) => id === value));
}
