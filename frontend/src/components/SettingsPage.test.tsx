import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SettingsPage } from './SettingsPage';
import { SETTINGS_GROUPS, SETTINGS_TABS } from './settingsTabs';

describe('SettingsPage', () => {
  it('lists every settings tab and switches on click', () => {
    const onChangeTab = vi.fn();
    render(
      <SettingsPage activeTab="settings" onChangeTab={onChangeTab} onBackToChat={vi.fn()}>
        <div>设置内容</div>
      </SettingsPage>,
    );

    expect(screen.getByTestId('settings-page')).toBeTruthy();
    expect(screen.getByTestId('settings-content').className).toContain('overflow-y-auto');
    for (const key of SETTINGS_TABS) {
      const label = key === 'settings'
        ? '设置'
        : key === 'models'
          ? '模型'
          : key === 'runs'
            ? '运行'
            : null;
      if (label) expect(screen.getByRole('tab', { name: label })).toBeTruthy();
    }
    expect(screen.getAllByRole('tab')).toHaveLength(SETTINGS_TABS.length);
    expect(screen.getByText('对话与模型')).toBeTruthy();
    expect(screen.getByText('知识与记忆')).toBeTruthy();
    expect(screen.getByText('代理与自动化')).toBeTruthy();
    expect(screen.getByText('高级')).toBeTruthy();
    expect(screen.getByRole('tablist', { name: '设置分组' })).toBeTruthy();
    expect(screen.getByRole('button', { name: '返回对话' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '治理（实验）' })).toBeTruthy();

    fireEvent.click(screen.getByRole('tab', { name: '模型' }));
    expect(onChangeTab).toHaveBeenCalledWith('models');
    fireEvent.click(screen.getByRole('tab', { name: '运行' }));
    expect(onChangeTab).toHaveBeenCalledWith('runs');
  });

  it('keeps settings group labels as stable English keys', () => {
    expect(SETTINGS_GROUPS.map((group) => group.label)).toEqual([
      'chatAndModels',
      'knowledgeAndMemory',
      'agentsAndAutomation',
      'advanced',
    ]);
  });
});
