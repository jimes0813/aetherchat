import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi } from 'vitest';
import { VoiceModeOverlay } from './VoiceModeOverlay';

describe('VoiceModeOverlay', () => {
  it('opens a ChatGPT-style live session and can mute or close', () => {
    const onClose = vi.fn();
    const onToggleMute = vi.fn();
    render(
      <VoiceModeOverlay
        open
        phase="listening"
        muted={false}
        showTranscript={false}
        error=""
        level={0.1}
        userTurns={[]}
        assistantText=""
        sttLabel="Whisper Small · 本地"
        ttsLabel="华严 · 本地 Piper"
        onClose={onClose}
        onToggleMute={onToggleMute}
        onToggleTranscript={vi.fn()}
        onInterrupt={vi.fn()}
      />,
    );
    expect(screen.getByRole('dialog', { name: '语音对话' })).toBeTruthy();
    expect(screen.getByText('我在听')).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '静音' }));
    expect(onToggleMute).toHaveBeenCalled();
    fireEvent.click(screen.getByRole('button', { name: '结束语音对话' }));
    expect(onClose).toHaveBeenCalled();
  });

  it('tapping the orb while speaking interrupts', () => {
    const onInterrupt = vi.fn();
    render(
      <VoiceModeOverlay
        open
        phase="speaking"
        muted={false}
        showTranscript
        error=""
        level={0.2}
        userTurns={['帮我看看这段报错']}
        assistantText="先看日志。"
        sttLabel="Whisper"
        ttsLabel="Piper"
        onClose={vi.fn()}
        onToggleMute={vi.fn()}
        onToggleTranscript={vi.fn()}
        onInterrupt={onInterrupt}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '打断回复' }));
    expect(onInterrupt).toHaveBeenCalled();
    expect(screen.getByTestId('voice-transcript').textContent).toContain('帮我看看这段报错');
  });

  it('traps Escape on the dialog', () => {
    const onClose = vi.fn();
    render(
      <VoiceModeOverlay
        open
        phase="listening"
        muted={false}
        showTranscript={false}
        error=""
        level={0}
        userTurns={[]}
        assistantText=""
        sttLabel="Whisper"
        ttsLabel="Piper"
        onClose={onClose}
        onToggleMute={vi.fn()}
        onToggleTranscript={vi.fn()}
        onInterrupt={vi.fn()}
      />,
    );
    fireEvent.keyDown(screen.getByRole('dialog'), { key: 'Escape' });
    expect(onClose).toHaveBeenCalled();
  });
});
