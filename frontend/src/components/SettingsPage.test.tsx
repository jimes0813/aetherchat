import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SettingsPage } from './SettingsPage';
import { SETTINGS_NAV } from './settingsTabs';

describe('SettingsPage', () => {
  it('opens as a dialog and lists integrated Codex-style categories', () => {
    const onChangeTab = vi.fn();
    render(
      <SettingsPage activeTab="settings" onChangeTab={onChangeTab} onBackToChat={vi.fn()}>
        <div>设置内容</div>
      </SettingsPage>,
    );

    expect(screen.getByRole('dialog', { name: '设置' })).toBeTruthy();
    expect(screen.getByTestId('settings-page')).toBeTruthy();
    expect(screen.getByRole('tab', { name: '通用' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '模型' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '个性化' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '记忆' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '文件' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '代理与自动化' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '集成' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '电脑使用' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '高级' })).toBeTruthy();
    expect(screen.queryByRole('tab', { name: '运行' })).toBeNull();
    expect(screen.queryByRole('tab', { name: '资料' })).toBeNull();
    expect(screen.queryByText('对话与模型')).toBeNull();
    expect(screen.getAllByRole('tab')).toHaveLength(SETTINGS_NAV.length);

    fireEvent.click(screen.getByRole('tab', { name: '模型' }));
    expect(onChangeTab).toHaveBeenCalledWith('models');
    fireEvent.click(screen.getByRole('tab', { name: '记忆' }));
    expect(onChangeTab).toHaveBeenCalledWith('memory');
    fireEvent.click(screen.getByRole('tab', { name: '代理与自动化' }));
    expect(onChangeTab).toHaveBeenCalledWith('tasks');
    fireEvent.click(screen.getByRole('tab', { name: '集成' }));
    expect(onChangeTab).toHaveBeenCalledWith('tools');
  });

  it('keeps the matching category selected when a nested panel is open', () => {
    render(
      <SettingsPage activeTab="runs" onChangeTab={vi.fn()} onBackToChat={vi.fn()}>
        <div>运行内容</div>
      </SettingsPage>,
    );
    expect(screen.getByRole('tab', { name: '代理与自动化' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.queryByRole('tab', { name: '运行' })).toBeNull();
    expect(screen.getAllByRole('tab')).toHaveLength(SETTINGS_NAV.length);
  });

  it('maps nested workspace panels onto integrated settings categories', () => {
    expect(SETTINGS_NAV.map((group) => [group.id, ...group.panels])).toEqual([
      ['general', 'settings'],
      ['models', 'models'],
      ['voice', 'voice'],
      ['personalization', 'instructions'],
      ['memory', 'memory', 'knowledge'],
      ['files', 'artifacts', 'changes'],
      ['agent', 'tasks', 'automations', 'runs'],
      ['integrations', 'tools', 'plugins', 'connections'],
      ['computer', 'computer'],
      ['advanced', 'governance'],
    ]);
  });
});
