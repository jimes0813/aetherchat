import { useEffect, type KeyboardEvent } from 'react';
import { AudioLines, Mic, MicOff, X } from 'lucide-react';
import { useI18n } from '../i18n';
import { useDialogFocus } from './useDialogFocus';
import type { VoiceInputMode, VoicePhase } from './voiceSession';
import { voicePhaseLabel } from './voiceSession';

type VoiceModeOverlayProps = {
  open: boolean;
  phase: VoicePhase;
  muted: boolean;
  showTranscript: boolean;
  error: string;
  level: number;
  userTurns: string[];
  assistantText: string;
  sttLabel: string;
  ttsLabel: string;
  inputMode?: VoiceInputMode;
  onClose: () => void;
  onToggleMute: () => void;
  onToggleTranscript: () => void;
  onInterrupt: () => void;
  onHoldStart?: () => void;
  onHoldEnd?: () => void;
  onRetry?: () => void;
};

function phaseLabel(phase: VoicePhase, muted: boolean, inputMode: VoiceInputMode, ui: (text: string) => string) {
  if (phase === 'error') return ui('语音遇到问题');
  if (inputMode === 'push_to_talk' && phase === 'listening') return ui('按住说话 · 松手发送');
  if (inputMode === 'push_to_talk' && (phase === 'idle' || phase === 'listening')) {
    return muted ? ui('已静音') : ui('按住光球说话');
  }
  return ui(voicePhaseLabel(phase, muted));
}

export function VoiceModeOverlay({
  open,
  phase,
  muted,
  showTranscript,
  error,
  level,
  userTurns,
  assistantText,
  sttLabel,
  ttsLabel,
  inputMode = 'conversation',
  onClose,
  onToggleMute,
  onToggleTranscript,
  onInterrupt,
  onHoldStart,
  onHoldEnd,
  onRetry,
}: VoiceModeOverlayProps) {
  const { ui } = useI18n();
  const dialogFocus = useDialogFocus<HTMLDivElement>(open, onClose);

  useEffect(() => {
    if (!open) return undefined;
    const onKey = (event: globalThis.KeyboardEvent) => {
      if (event.key.toLowerCase() === 'm' && !event.metaKey && !event.ctrlKey) {
        event.preventDefault();
        onToggleMute();
      } else if (event.key === ' ') {
        event.preventDefault();
        if (phase === 'speaking' || phase === 'thinking') onInterrupt();
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [onInterrupt, onToggleMute, open, phase]);

  const onDialogKeyDown = (event: KeyboardEvent<HTMLDivElement>) => {
    dialogFocus.onKeyDown(event);
  };

  if (!open) return null;

  const scale = 1 + Math.min(0.28, level * 6);
  const orbClass = [
    'aether-voice-orb',
    phase === 'speaking' ? 'is-speaking' : '',
    phase === 'thinking' || phase === 'transcribing' ? 'is-thinking' : '',
    phase === 'listening' && !muted ? 'is-listening' : '',
    phase === 'error' ? 'is-muted' : '',
    muted ? 'is-muted' : '',
  ].filter(Boolean).join(' ');

  const handleOrb = () => {
    if (phase === 'error') {
      onRetry?.();
      return;
    }
    if (phase === 'speaking' || phase === 'thinking') onInterrupt();
    else onToggleTranscript();
  };

  return (
    <div
      ref={dialogFocus.containerRef}
      tabIndex={-1}
      onKeyDown={onDialogKeyDown}
      className="aether-voice-overlay"
      role="dialog"
      aria-modal="true"
      aria-label={ui('语音对话')}
    >
      <div className="aether-voice-top">
        <div>
          <div className="aether-voice-kicker">{inputMode === 'push_to_talk' ? ui('按住说话') : ui('实时语音')}</div>
          <div className="aether-voice-voice">{ttsLabel}</div>
        </div>
        <button
          type="button"
          data-dialog-initial-focus
          className="aether-voice-icon-btn"
          onClick={onClose}
          aria-label={ui('结束语音对话')}
        >
          <X size={18} />
        </button>
      </div>

      <button
        type="button"
        className={orbClass}
        style={{ transform: `scale(${scale})` }}
        onClick={handleOrb}
        onMouseDown={() => { if (inputMode === 'push_to_talk') onHoldStart?.(); }}
        onMouseUp={() => { if (inputMode === 'push_to_talk') onHoldEnd?.(); }}
        onMouseLeave={() => { if (inputMode === 'push_to_talk') onHoldEnd?.(); }}
        onTouchStart={(event) => { if (inputMode === 'push_to_talk') { event.preventDefault(); onHoldStart?.(); } }}
        onTouchEnd={() => { if (inputMode === 'push_to_talk') onHoldEnd?.(); }}
        aria-label={
          phase === 'error' ? ui('重试语音')
            : phase === 'speaking' ? ui('打断回复')
              : inputMode === 'push_to_talk' ? ui('按住说话')
                : ui('显示或隐藏转写')
        }
      >
        <span className="aether-voice-orb-core" />
        <span className="aether-voice-orb-ring" />
        <AudioLines className="aether-voice-orb-mark" size={28} />
      </button>

      <div className="aether-voice-status" aria-live="polite">
        {error || phaseLabel(phase, muted, inputMode, ui)}
      </div>
      <div className="aether-voice-hint">
        {phase === 'error'
          ? ui('连接麦克风或检查语音引擎后点光球重试')
          : showTranscript
            ? ui('再点光球可收起转写')
            : inputMode === 'push_to_talk'
              ? ui('按住光球说话，松手发送；点右上角可结束')
              : ui('点光球查看转写，说话即可打断')}
      </div>

      {showTranscript && (
        <div className="aether-voice-transcript" data-testid="voice-transcript">
          {userTurns.slice(-3).map((turn, index) => (
            <p key={`${turn}-${index}`} className="aether-voice-turn is-user">{turn}</p>
          ))}
          {assistantText && <p className="aether-voice-turn is-assistant">{assistantText}</p>}
          {!userTurns.length && !assistantText && (
            <p className="aether-voice-turn is-empty">{ui('开始说话后，转写会出现在这里。')}</p>
          )}
        </div>
      )}

      <div className="aether-voice-dock">
        <button
          type="button"
          className={`aether-voice-dock-btn ${muted ? 'is-active' : ''}`}
          onClick={onToggleMute}
          aria-pressed={muted}
          aria-label={muted ? ui('取消静音') : ui('静音')}
        >
          {muted ? <MicOff size={18} /> : <Mic size={18} />}
          <span>{muted ? ui('已静音') : ui('静音')}</span>
        </button>
        <div className="aether-voice-engine">{sttLabel}</div>
        <button type="button" className="aether-voice-dock-btn" onClick={onClose}>
          <X size={18} />
          <span>{ui('结束')}</span>
        </button>
      </div>
    </div>
  );
}
