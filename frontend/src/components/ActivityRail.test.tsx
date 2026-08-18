import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { ActivityRail } from './chrome';

describe('ActivityRail', () => {
  it('only exposes chat and settings on the rail', () => {
    const setActivePanel = vi.fn();
    const onOpenChat = vi.fn();
    const onOpenSettings = vi.fn();
    render(
      <ActivityRail
        activePanel={null}
        setActivePanel={setActivePanel}
        onOpenChat={onOpenChat}
        onOpenSettings={onOpenSettings}
      />,
    );

    expect(screen.getByLabelText('对话')).toBeTruthy();
    expect(screen.getByLabelText('设置')).toBeTruthy();
    expect(screen.queryByLabelText('模型')).toBeNull();
    expect(screen.queryByLabelText('运行')).toBeNull();
    expect(screen.queryByLabelText('更多面板')).toBeNull();
    expect(screen.queryByLabelText('插件')).toBeNull();

    fireEvent.click(screen.getByLabelText('设置'));
    expect(onOpenSettings).toHaveBeenCalled();
    expect(setActivePanel).toHaveBeenCalledWith('设置');

    const rail = screen.getByLabelText('设置').closest('aside');
    expect(rail?.lastElementChild).toBe(screen.getByLabelText('设置'));
  });
});
