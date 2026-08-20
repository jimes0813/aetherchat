import { render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { VoiceSettingsPanel } from './VoiceSettingsPanel';

describe('VoiceSettingsPanel', () => {
  it('exposes a usable silence range slider', () => {
    const onSilenceMs = vi.fn();
    render(
      <VoiceSettingsPanel
        silenceMs={1100}
        onSilenceMs={onSilenceMs}
        ttsEngine="auto"
        onTtsEngine={vi.fn()}
        onStartLive={vi.fn()}
      />,
    );
    const slider = screen.getByRole('slider');
    expect(slider).toHaveAttribute('type', 'range');
    expect(slider.className).toContain('aether-range');
    expect(slider).toHaveAttribute('min', '700');
    expect(slider).toHaveAttribute('max', '1600');
    expect(slider).toHaveValue('1100');
  });
});
