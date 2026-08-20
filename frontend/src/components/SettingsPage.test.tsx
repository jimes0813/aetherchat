import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SettingsPage } from './SettingsPage';
import { SETTINGS_NAV_TABS, WORKSPACE_TABS } from './settingsTabs';

describe('SettingsPage', () => {
  it('lists Codex-style preference tabs and switches on click', () => {
    const onChangeTab = vi.fn();
    render(
      <SettingsPage activeTab="settings" onChangeTab={onChangeTab} onBackToChat={vi.fn()}>
        <div>设置内容</div>
      </SettingsPage>,
    );

    expect(screen.getByTestId('settings-page')).toBeTruthy();
    expect(screen.getByTestId('settings-content').className).toContain('overflow-y-auto');
    expect(screen.getByRole('tab', { name: '通用' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '模型' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '个性化' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '电脑使用' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '高级' })).toBeTruthy();
    expect(screen.queryByRole('tab', { name: '运行' })).toBeNull();
    expect(screen.queryByRole('tab', { name: '任务' })).toBeNull();
    expect(screen.queryByRole('tab', { name: '文件' })).toBeNull();
    expect(screen.queryByText('对话与模型')).toBeNull();
    expect(screen.getAllByRole('tab')).toHaveLength(SETTINGS_NAV_TABS.length);
    expect(screen.getByRole('tablist', { name: '设置分组' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '返回对话' })).toBeTruthy();

    fireEvent.click(screen.getByRole('tab', { name: '模型' }));
    expect(onChangeTab).toHaveBeenCalledWith('models');
    fireEvent.click(screen.getByRole('tab', { name: '通用' }));
    expect(onChangeTab).toHaveBeenCalledWith('settings');
  });

  it('surfaces a workspace panel in the nav only while it is open', () => {
    render(
      <SettingsPage activeTab="runs" onChangeTab={vi.fn()} onBackToChat={vi.fn()}>
        <div>运行内容</div>
      </SettingsPage>,
    );
    expect(screen.getByRole('tab', { name: '运行' })).toBeTruthy();
    expect(screen.getAllByRole('tab')).toHaveLength(SETTINGS_NAV_TABS.length + 1);
  });

  it('keeps workbench surfaces out of the default Settings nav', () => {
    expect([...WORKSPACE_TABS]).toEqual([
      'knowledge',
      'artifacts',
      'runs',
      'tasks',
      'automations',
      'tools',
      'changes',
    ]);
    expect([...SETTINGS_NAV_TABS]).toEqual([
      'settings',
      'models',
      'voice',
      'instructions',
      'memory',
      'computer',
      'connections',
      'plugins',
      'governance',
    ]);
  });
});
