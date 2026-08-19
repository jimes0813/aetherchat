export const SETTINGS_GROUPS = [
  {
    id: 'chat',
    label: '对话与模型',
    tabs: [['设置', 'settings'], ['语音', 'voice'], ['模型', 'models']] as const,
  },
  {
    id: 'knowledge',
    label: '知识与记忆',
    tabs: [['记忆', 'memory'], ['资料', 'knowledge'], ['工件', 'artifacts']] as const,
  },
  {
    id: 'agent',
    label: '代理与自动化',
    tabs: [['运行', 'runs'], ['任务', 'tasks'], ['自动化', 'automations'], ['工具', 'tools'], ['插件', 'plugins'], ['规范', 'instructions']] as const,
  },
  {
    id: 'advanced',
    label: '高级',
    tabs: [['连接', 'connections'], ['电脑', 'computer'], ['合并', 'changes'], ['治理', 'governance']] as const,
  },
] as const;

export const SETTINGS_TABS = SETTINGS_GROUPS.flatMap((group) => [...group.tabs]);

export type SettingsTabId = (typeof SETTINGS_TABS)[number][0];

export function isSettingsTab(value: string | null | undefined): value is SettingsTabId {
  return Boolean(value && SETTINGS_TABS.some(([id]) => id === value));
}
