import { act, renderHook, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { useSessionStore } from '../stores/sessionStore';
import { useVoiceSession } from './useVoiceSession';

type AudioProcessHandler = ((event: {
  inputBuffer: { sampleRate: number; getChannelData: () => Float32Array };
}) => void) | null;

type DeferredResponse = {
  promise: Promise<Response>;
  resolve: (response: Response) => void;
};

const initialState = useSessionStore.getState();
let audioProcess: AudioProcessHandler = null;
let createdAudio = 0;

function deferredResponse(): DeferredResponse {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((done) => { resolve = done; });
  return { promise, resolve };
}

function wavResponse() {
  return new Response(new Uint8Array(44), {
    status: 200,
    headers: { 'content-type': 'audio/wav' },
  });
}

function installAudioEnvironment() {
  const track = { stop: vi.fn(), onended: null as (() => void) | null };
  const stream = {
    getTracks: () => [track],
    getAudioTracks: () => [track],
  } as unknown as MediaStream;
  Object.defineProperty(navigator, 'mediaDevices', {
    configurable: true,
    value: { getUserMedia: vi.fn().mockResolvedValue(stream) },
  });

  class FakeAudioContext {
    state = 'running';
    destination = {};
    resume = vi.fn().mockResolvedValue(undefined);
    close = vi.fn().mockResolvedValue(undefined);
    createMediaStreamSource = () => ({ connect: vi.fn(), disconnect: vi.fn() });
    createScriptProcessor = () => ({
      connect: vi.fn(),
      disconnect: vi.fn(),
      get onaudioprocess() { return audioProcess; },
      set onaudioprocess(value: AudioProcessHandler) { audioProcess = value; },
    });
    createGain = () => ({ connect: vi.fn(), gain: { value: 1 } });
  }
  class FakeAudio {
    onended: (() => void) | null = null;
    onerror: (() => void) | null = null;
    play = vi.fn().mockResolvedValue(undefined);
    pause = vi.fn();
    removeAttribute = vi.fn();
    load = vi.fn();

    constructor(_url: string) {
      createdAudio += 1;
    }
  }
  vi.stubGlobal('AudioContext', FakeAudioContext);
  vi.stubGlobal('Audio', FakeAudio);
  Object.defineProperty(URL, 'createObjectURL', { configurable: true, value: vi.fn(() => 'blob:voice') });
  Object.defineProperty(URL, 'revokeObjectURL', { configurable: true, value: vi.fn() });
}

async function speakOneUtterance() {
  const voiced = new Float32Array(4096).fill(0.05);
  const silent = new Float32Array(4096);
  await act(async () => {
    for (let index = 0; index < 6; index += 1) {
      audioProcess?.({ inputBuffer: { sampleRate: 48000, getChannelData: () => voiced } });
    }
    for (let index = 0; index < 14; index += 1) {
      audioProcess?.({ inputBuffer: { sampleRate: 48000, getChannelData: () => silent } });
    }
  });
}

describe('useVoiceSession live ordering', () => {
  beforeEach(() => {
    window.localStorage.clear();
    useSessionStore.setState(initialState, true);
    audioProcess = null;
    createdAudio = 0;
    installAudioEnvironment();
  });

  it('serializes streamed TTS and speaks only the first responding model', async () => {
    const firstSpeech = deferredResponse();
    const secondSpeech = deferredResponse();
    const ttsRequests: RequestInit[] = [];
    vi.stubGlobal('fetch', vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/voice/status')) {
        return Promise.resolve(new Response(JSON.stringify({
          stt: { ready: true, label: 'Whisper' },
          tts: {
            ready: true,
            label: 'Piper',
            piper: { ready: true, label: 'Piper' },
            cosyvoice: { ready: false, startable: true },
          },
        }), { headers: { 'content-type': 'application/json' } }));
      }
      if (url.includes('/voice/stt')) {
        return Promise.resolve(new Response(JSON.stringify({ text: '请继续' }), {
          headers: { 'content-type': 'application/json' },
        }));
      }
      if (url.endsWith('/voice/tts')) {
        ttsRequests.push(init || {});
        return ttsRequests.length === 1 ? firstSpeech.promise : secondSpeech.promise;
      }
      throw new Error(`Unexpected URL: ${url}`);
    }));
    const onSend = vi.fn(() => true);
    const { result } = renderHook(() => useVoiceSession({ onSend }));

    await act(async () => { await result.current.start(); });
    await speakOneUtterance();
    await waitFor(() => expect(onSend).toHaveBeenCalledWith('请继续', ['all'], []));

    act(() => {
      useSessionStore.getState().addDelta('alpha', '第一句。第二句。尾声');
      useSessionStore.getState().addDelta('beta', '不应朗读。');
    });
    await waitFor(() => expect(ttsRequests).toHaveLength(1));
    expect(JSON.parse(String(ttsRequests[0].body))).toMatchObject({ text: '第一句。' });

    firstSpeech.resolve(wavResponse());
    await waitFor(() => expect(ttsRequests).toHaveLength(2));
    expect(JSON.parse(String(ttsRequests[1].body))).toMatchObject({ text: '第二句。' });
    expect(ttsRequests.every((request) => !String(request.body).includes('不应朗读'))).toBe(true);

    secondSpeech.resolve(wavResponse());
  });

  it('aborts in-flight synthesis so interrupted audio cannot resume later', async () => {
    const speech = deferredResponse();
    let speechSignal: AbortSignal | null = null;
    vi.stubGlobal('fetch', vi.fn((input: string | URL | Request, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/voice/status')) {
        return Promise.resolve(new Response(JSON.stringify({
          stt: { ready: true },
          tts: { ready: true, piper: { ready: true }, cosyvoice: { ready: false } },
        }), { headers: { 'content-type': 'application/json' } }));
      }
      if (url.includes('/voice/stt')) {
        return Promise.resolve(new Response(JSON.stringify({ text: '打断测试' }), {
          headers: { 'content-type': 'application/json' },
        }));
      }
      if (url.endsWith('/voice/tts')) {
        speechSignal = init?.signal || null;
        return speech.promise;
      }
      throw new Error(`Unexpected URL: ${url}`);
    }));
    const onInterrupt = vi.fn();
    const { result } = renderHook(() => useVoiceSession({ onSend: () => true, onInterrupt }));

    await act(async () => { await result.current.start(); });
    await speakOneUtterance();
    act(() => { useSessionStore.getState().addDelta('alpha', '正在生成。还有后续'); });
    await waitFor(() => expect(speechSignal).not.toBeNull());

    act(() => { result.current.interrupt(); });
    expect((speechSignal as unknown as AbortSignal).aborted).toBe(true);
    expect(onInterrupt).toHaveBeenCalledOnce();

    speech.resolve(wavResponse());
    await act(async () => { await Promise.resolve(); });
    expect(createdAudio).toBe(0);
  });
});
