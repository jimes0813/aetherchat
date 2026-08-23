import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { SettingsPage } from './SettingsPage';
import { SETTINGS_NAV, visibleSettingsNav } from './settingsTabs';

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
    expect(screen.getByRole('tab', { name: '项目' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '自动化' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '能力' })).toBeTruthy();
    expect(screen.getByRole('tab', { name: '运行与安全' })).toBeTruthy();
    expect(screen.queryByRole('tab', { name: '开发者工具' })).toBeNull();
    expect(screen.queryByRole('tab', { name: '运行' })).toBeNull();
    expect(screen.queryByRole('tab', { name: '资料' })).toBeNull();
    expect(screen.queryByText('对话与模型')).toBeNull();
    expect(screen.getAllByRole('tab')).toHaveLength(visibleSettingsNav(false).length);

    fireEvent.click(screen.getByRole('tab', { name: '项目' }));
    expect(onChangeTab).toHaveBeenCalledWith('memory');
    fireEvent.click(screen.getByRole('tab', { name: '能力' }));
    expect(onChangeTab).toHaveBeenCalledWith('models');
  });

  it('keeps the matching category selected when a nested panel is open', () => {
    render(
      <SettingsPage activeTab="runs" onChangeTab={vi.fn()} onBackToChat={vi.fn()}>
        <div>运行内容</div>
      </SettingsPage>,
    );
    expect(screen.getByRole('tab', { name: '运行与安全' })).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('button', { name: '运行' })).toHaveAttribute('aria-current', 'page');
    expect(screen.getAllByRole('tab')).toHaveLength(visibleSettingsNav(false).length);
  });

  it('maps nested workspace panels onto integrated settings categories', () => {
    expect(SETTINGS_NAV.map((group) => [group.id, ...group.panels])).toEqual([
      ['general', 'settings'],
      ['project', 'memory', 'knowledge', 'artifacts', 'changes', 'tasks'],
      ['automation', 'automations'],
      ['capabilities', 'models', 'voice', 'plugins', 'connections', 'computer'],
      ['runtime', 'runs', 'instructions'],
      ['developer', 'tools', 'governance'],
    ]);
  });

  it('reveals internal controls only in developer mode', () => {
    render(
      <SettingsPage activeTab="tools" developerMode onChangeTab={vi.fn()} onBackToChat={vi.fn()}>
        <div>开发者内容</div>
      </SettingsPage>,
    );
    expect(screen.getByRole('tab', { name: '开发者工具' })).toHaveAttribute('aria-selected', 'true');
  });
});
