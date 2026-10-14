import { describe, expect, it } from 'vitest';
import {
  cleanTranscript,
  downsample,
  encodeWav,
  markdownToSpeech,
  reduceVoicePhase,
  shouldBargeIn,
  shouldCommitUtterance,
  shouldResetShortUtterance,
  splitSpeakableSentences,
  takeSpeakableSentences,
} from './voiceSession';

describe('voice session helpers', () => {
  it('waits through a ChatGPT-like pause before committing', () => {
    expect(shouldCommitUtterance({ speechMs: 200, silenceMs: 1200, silenceTimeout: 1100 })).toBe(false);
    expect(shouldCommitUtterance({ speechMs: 500, silenceMs: 800, silenceTimeout: 1100 })).toBe(false);
    expect(shouldCommitUtterance({ speechMs: 500, silenceMs: 1100, silenceTimeout: 1100 })).toBe(true);
  });

  it('requires a held phrase before barge-in so thinking pauses are safe', () => {
    expect(shouldBargeIn({ speaking: true, speechHoldMs: 120 })).toBe(false);
    expect(shouldBargeIn({ speaking: true, speechHoldMs: 400 })).toBe(true);
    expect(shouldBargeIn({ speaking: false, speechHoldMs: 800 })).toBe(false);
  });

  it('cleans fillers and whisper noise tags', () => {
    expect(cleanTranscript('[BLANK_AUDIO]')).toBe('');
    expect(cleanTranscript('嗯，那个，帮我改这段 TypeScript')).toBe('帮我改这段 TypeScript');
  });

  it('speaks complete sentences and holds the tail', () => {
    expect(splitSpeakableSentences('先看日志。然后再')).toEqual({
      ready: ['先看日志。'],
      rest: '然后再',
    });
    expect(takeSpeakableSentences('先看日志。 然后再')).toEqual({
      ready: ['先看日志。'],
      rest: ' 然后再',
      consumedLength: 5,
    });
  });

  it('drops short noise after a full silence window', () => {
    expect(shouldResetShortUtterance({ speechMs: 120, silenceMs: 1100, silenceTimeout: 1100 })).toBe(true);
    expect(shouldResetShortUtterance({ speechMs: 500, silenceMs: 1100, silenceTimeout: 1100 })).toBe(false);
  });

  it('low-pass averages samples while reducing 48 kHz audio to 16 kHz', () => {
    const result = downsample(new Float32Array([1, -1, 1, -1, 1, -1]), 48000, 16000);
    expect(Array.from(result)).toEqual([expect.closeTo(1 / 3, 5), expect.closeTo(-1 / 3, 5)]);
  });

  it('does not read code fences verbatim', () => {
    expect(markdownToSpeech('结论如下\n```ts\nconst x = 1\n```')).toContain('一段代码');
    expect(markdownToSpeech('结论如下\n```ts\nconst x = 1\n```')).not.toContain('const x');
  });

  it('encodes a valid wav header', () => {
    const blob = encodeWav(new Int16Array([0, 1, -1]), 16000);
    expect(blob.type).toBe('audio/wav');
    expect(blob.size).toBe(50);
  });

  it('moves through the live conversation state machine', () => {
    let phase = reduceVoicePhase('idle', { type: 'start' });
    expect(phase).toBe('listening');
    phase = reduceVoicePhase(phase, { type: 'commit' });
    expect(phase).toBe('transcribing');
    phase = reduceVoicePhase(phase, { type: 'sent' });
    expect(phase).toBe('thinking');
    phase = reduceVoicePhase(phase, { type: 'first_audio' });
    expect(phase).toBe('speaking');
    phase = reduceVoicePhase(phase, { type: 'barge_in' });
    expect(phase).toBe('listening');
  });
});

it('maps hard failures to the error phase', () => {
  expect(reduceVoicePhase('listening', { type: 'error' })).toBe('error');
  expect(reduceVoicePhase('speaking', { type: 'stop' })).toBe('idle');
});
