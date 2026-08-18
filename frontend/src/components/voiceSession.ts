export type VoicePhase =
  | 'idle'
  | 'listening'
  | 'transcribing'
  | 'thinking'
  | 'speaking';

export type VoiceEvent =
  | { type: 'start' }
  | { type: 'commit' }
  | { type: 'transcript'; text: string }
  | { type: 'empty_transcript' }
  | { type: 'sent' }
  | { type: 'first_audio' }
  | { type: 'waiting_audio' }
  | { type: 'playback_done' }
  | { type: 'barge_in' }
  | { type: 'approval' }
  | { type: 'error' }
  | { type: 'stop' };

export const DEFAULT_SPEECH_THRESHOLD = 0.018;
export const DEFAULT_SILENCE_MS = 1100;
export const MIN_SPEECH_MS = 420;
export const BARGE_IN_HOLD_MS = 380;
export const SAMPLE_RATE = 16000;
export const PRE_ROLL_MS = 320;
export const MAX_UTTERANCE_MS = 30_000;

const FILLER_RE = /^(?:嗯|啊|呃|那个|就是|然后|uh+|um+|ah+)[，,。.\s]*/i;
const NOISE_RE = /\[(?:blank_audio|silence|music|noise|inaudible)[^\]]*\]|\((?:silence|blank_audio|music|noise)\)/gi;

export function rmsLevel(samples: ArrayLike<number>): number {
  if (!samples.length) return 0;
  let sum = 0;
  for (let index = 0; index < samples.length; index += 1) {
    const value = samples[index] ?? 0;
    sum += value * value;
  }
  return Math.sqrt(sum / samples.length);
}

export function downsample(input: Float32Array, fromRate: number, toRate: number): Float32Array {
  if (fromRate === toRate || fromRate <= 0 || toRate <= 0 || input.length === 0) return input;
  const ratio = fromRate / toRate;
  const length = Math.max(1, Math.round(input.length / ratio));
  const output = new Float32Array(length);
  if (ratio < 1) {
    for (let index = 0; index < length; index += 1) {
      const source = Math.min(input.length - 1, index * ratio);
      const left = Math.floor(source);
      const right = Math.min(input.length - 1, left + 1);
      const fraction = source - left;
      output[index] = (input[left] ?? 0) * (1 - fraction) + (input[right] ?? 0) * fraction;
    }
    return output;
  }

  // Average every covered source interval instead of taking one sample. This
  // inexpensive low-pass step avoids aliasing speech harmonics when Chromium's
  // usual 48 kHz input is reduced to Whisper's 16 kHz format.
  for (let index = 0; index < length; index += 1) {
    const start = index * ratio;
    const end = Math.min(input.length, (index + 1) * ratio);
    let weighted = 0;
    let weight = 0;
    for (let source = Math.floor(start); source < Math.ceil(end); source += 1) {
      const overlap = Math.max(0, Math.min(end, source + 1) - Math.max(start, source));
      if (overlap > 0) {
        weighted += (input[source] ?? 0) * overlap;
        weight += overlap;
      }
    }
    output[index] = weight > 0 ? weighted / weight : 0;
  }
  return output;
}

export function floatTo16BitPcm(input: Float32Array): Int16Array {
  const output = new Int16Array(input.length);
  for (let index = 0; index < input.length; index += 1) {
    const sample = Math.max(-1, Math.min(1, input[index] ?? 0));
    output[index] = sample < 0 ? sample * 0x8000 : sample * 0x7fff;
  }
  return output;
}

export function encodeWav(pcm: Int16Array, sampleRate = SAMPLE_RATE): Blob {
  const buffer = new ArrayBuffer(44 + pcm.byteLength);
  const view = new DataView(buffer);
  const writeString = (offset: number, value: string) => {
    for (let index = 0; index < value.length; index += 1) {
      view.setUint8(offset + index, value.charCodeAt(index));
    }
  };
  writeString(0, 'RIFF');
  view.setUint32(4, 36 + pcm.byteLength, true);
  writeString(8, 'WAVE');
  writeString(12, 'fmt ');
  view.setUint32(16, 16, true);
  view.setUint16(20, 1, true);
  view.setUint16(22, 1, true);
  view.setUint32(24, sampleRate, true);
  view.setUint32(28, sampleRate * 2, true);
  view.setUint16(32, 2, true);
  view.setUint16(34, 16, true);
  writeString(36, 'data');
  view.setUint32(40, pcm.byteLength, true);
  new Int16Array(buffer, 44).set(pcm);
  return new Blob([buffer], { type: 'audio/wav' });
}

export function shouldCommitUtterance(input: {
  speechMs: number;
  silenceMs: number;
  silenceTimeout: number;
}): boolean {
  return input.speechMs >= MIN_SPEECH_MS && input.silenceMs >= input.silenceTimeout;
}

export function shouldBargeIn(input: {
  speaking: boolean;
  speechHoldMs: number;
}): boolean {
  return input.speaking && input.speechHoldMs >= BARGE_IN_HOLD_MS;
}

export function shouldResetShortUtterance(input: {
  speechMs: number;
  silenceMs: number;
  silenceTimeout: number;
}): boolean {
  return input.speechMs > 0
    && input.speechMs < MIN_SPEECH_MS
    && input.silenceMs >= input.silenceTimeout;
}

export function cleanTranscript(text: string): string {
  let next = (text || '').replace(NOISE_RE, ' ').replace(/\s+/g, ' ').trim();
  for (let guard = 0; guard < 6; guard += 1) {
    const stripped = next.replace(FILLER_RE, '').trim();
    if (stripped === next) break;
    next = stripped;
  }
  return next.replace(/^[，,。.?？!！\s]+|[，,。.?？!！\s]+$/g, '');
}

export function splitSpeakableSentences(buffer: string): { ready: string[]; rest: string } {
  const { ready, rest } = takeSpeakableSentences(buffer);
  return { ready, rest };
}

export function takeSpeakableSentences(buffer: string): {
  ready: string[];
  rest: string;
  consumedLength: number;
} {
  const pieces = buffer.split(/(?<=[。！？!?；;\n])/);
  if (pieces.length <= 1) {
    if (buffer.trim().length >= 42 && /[,，、]/.test(buffer)) {
      return { ready: [buffer.trim()], rest: '', consumedLength: buffer.length };
    }
    return { ready: [], rest: buffer, consumedLength: 0 };
  }
  const ready = pieces.slice(0, -1).map((item) => item.trim()).filter(Boolean);
  const rest = pieces[pieces.length - 1] ?? '';
  return { ready, rest, consumedLength: buffer.length - rest.length };
}

export function markdownToSpeech(text: string): string {
  let next = text.replace(/```[\s\S]*?```/g, '。下面是一段代码。');
  next = next.replace(/`([^`]+)`/g, '$1');
  next = next.replace(/!\[[^\]]*]\([^)]+\)/g, '');
  next = next.replace(/\[([^\]]+)]\([^)]+\)/g, '$1');
  next = next.replace(/^#{1,6}\s+/gm, '');
  next = next.replace(/[*_~]+/g, '');
  next = next.replace(/https?:\/\/\S+/g, '链接');
  return next.replace(/\s+/g, ' ').trim();
}

export function reduceVoicePhase(phase: VoicePhase, event: VoiceEvent): VoicePhase {
  if (event.type === 'stop' || event.type === 'error') return 'idle';
  if (event.type === 'start' || event.type === 'barge_in' || event.type === 'playback_done' || event.type === 'empty_transcript' || event.type === 'approval') {
    return 'listening';
  }
  if (event.type === 'commit') return 'transcribing';
  if (event.type === 'sent' || event.type === 'transcript' || event.type === 'waiting_audio') return 'thinking';
  if (event.type === 'first_audio') return 'speaking';
  return phase;
}

export function microphoneErrorHint(error: unknown): string {
  const name = error instanceof DOMException ? error.name : '';
  if (name === 'NotAllowedError' || name === 'PermissionDeniedError') {
    return '麦克风权限被拒绝。可在系统设置里允许 AetherChat 使用麦克风。';
  }
  if (name === 'NotFoundError' || name === 'DevicesNotFoundError') {
    return '没有检测到麦克风。当前机器只有播放设备时，请连接蓝牙耳机或 USB 麦。';
  }
  if (name === 'NotReadableError') {
    return '麦克风被其他应用占用，请关闭后再试。';
  }
  return error instanceof Error ? error.message : '无法打开麦克风';
}
