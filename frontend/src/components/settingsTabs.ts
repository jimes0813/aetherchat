export const SETTINGS_TABS = [
  ['设置', 'settings'],
  ['语音', 'voice'],
  ['模型', 'models'],
  ['运行', 'runs'],
  ['记忆', 'memory'],
  ['资料', 'knowledge'],
  ['任务', 'tasks'],
  ['自动化', 'automations'],
  ['工件', 'artifacts'],
  ['合并', 'changes'],
  ['工具', 'tools'],
  ['插件', 'plugins'],
  ['连接', 'connections'],
  ['电脑', 'computer'],
  ['规范', 'instructions'],
  ['治理', 'governance'],
] as const;

export type SettingsTabId = (typeof SETTINGS_TABS)[number][0];

export function isSettingsTab(value: string | null | undefined): value is SettingsTabId {
  return Boolean(value && SETTINGS_TABS.some(([id]) => id === value));
}
