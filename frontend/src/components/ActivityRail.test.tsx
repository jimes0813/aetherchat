import { fireEvent, render, screen } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ACTIVITY_RAIL_ADVANCED_KEY, ActivityRail } from './chrome';

describe('ActivityRail progressive disclosure', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('shows primary panels first and reveals advanced after More', () => {
    const setActivePanel = vi.fn();
    render(<ActivityRail activePanel={null} setActivePanel={setActivePanel} />);

    expect(screen.getByLabelText('模型')).toBeTruthy();
    expect(screen.getByLabelText('运行')).toBeTruthy();
    expect(screen.getByLabelText('设置')).toBeTruthy();
    expect(screen.queryByLabelText('插件')).toBeNull();

    fireEvent.click(screen.getByRole('button', { name: '更多面板' }));
    expect(screen.getByLabelText('插件')).toBeTruthy();
    expect(localStorage.getItem(ACTIVITY_RAIL_ADVANCED_KEY)).toBe('1');

    fireEvent.click(screen.getByLabelText('插件'));
    expect(setActivePanel).toHaveBeenCalledWith('插件');
  });

  it('keeps advanced visible while an advanced panel is selected', () => {
    render(<ActivityRail activePanel="自动化" setActivePanel={vi.fn()} />);
    expect(screen.getByLabelText('自动化')).toBeTruthy();
    expect(screen.getByLabelText('插件')).toBeTruthy();
  });
});
