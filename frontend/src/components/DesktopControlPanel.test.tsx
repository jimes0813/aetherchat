import { render, screen } from '@testing-library/react';
import { describe, expect, it } from 'vitest';
import { DesktopControlPanel } from './DesktopControlPanel';

describe('DesktopControlPanel', () => {
  it('stacks preview, timeline and console for the settings overlay', () => {
    render(<DesktopControlPanel />);
    const panel = screen.getByTestId('computer-use-panel');
    expect(panel.className).toContain('aether-computer-use');
    expect(panel.querySelector('.aether-computer-stage')).toBeTruthy();
    expect(panel.querySelector('.aether-computer-preview')).toBeTruthy();
    expect(panel.querySelector('.aether-computer-timeline')).toBeTruthy();
    expect(panel.querySelector('.aether-computer-console')).toBeTruthy();
    expect(panel.querySelector('[class*="xl:grid-cols"]')).toBeNull();
    expect(panel.querySelector('[class*="lg:grid-cols"]')).toBeNull();
    expect(screen.getByText('本地电脑')).toBeTruthy();
  });
});
