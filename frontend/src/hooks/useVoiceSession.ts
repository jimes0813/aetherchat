import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchBlob, fetchJson } from '../api/client';
import { API_BASE } from '../components/shared';
import {
  BARGE_IN_HOLD_MS,
  DEFAULT_SILENCE_MS,
  DEFAULT_SPEECH_THRESHOLD,
  MAX_UTTERANCE_MS,
  PRE_ROLL_MS,
  SAMPLE_RATE,
  cleanTranscript,
  downsample,
  encodeWav,
  floatTo16BitPcm,
  markdownToSpeech,
  microphoneErrorHint,
  reduceVoicePhase,
  rmsLevel,
  shouldBargeIn,
  shouldCommitUtterance,
  shouldResetShortUtterance,
  takeSpeakableSentences,
  type VoicePhase,
} from '../components/voiceSession';
import { useSessionStore } from '../stores/sessionStore';

export type VoiceTtsEngine = 'auto' | 'piper' | 'cosyvoice';

type VoiceEngineStatus = {
  ready?: boolean;
  stt?: { ready?: boolean; label?: string };
  tts?: {
    ready?: boolean;
    label?: string;
    active?: string | null;
    piper?: { ready?: boolean; label?: string };
    cosyvoice?: { ready?: boolean; label?: string; startable?: boolean };
  };
  hints?: string[];
};

type UseVoiceSessionOptions = {
  onSend: (content: string, targets: string[], attachmentIds: string[]) => boolean | void;
  onInterrupt?: () => void;
  hasApproval?: boolean;
};

type VoicePreferences = {
  silenceMs: number;
  ttsEngine: VoiceTtsEngine;
};

const VOICE_PREFERENCES_KEY = 'aetherchat-voice-preferences-v1';

function loadVoicePreferences(): VoicePreferences {
  const fallback = { silenceMs: DEFAULT_SILENCE_MS, ttsEngine: 'auto' as const };
  try {
    const parsed = JSON.parse(window.localStorage.getItem(VOICE_PREFERENCES_KEY) || '{}') as Partial<VoicePreferences>;
    const silenceMs = Number(parsed.silenceMs);
    const ttsEngine = parsed.ttsEngine;
    return {
      silenceMs: Number.isFinite(silenceMs)
        ? Math.min(1600, Math.max(700, Math.round(silenceMs / 100) * 100))
        : fallback.silenceMs,
      ttsEngine: ttsEngine === 'piper' || ttsEngine === 'cosyvoice' ? ttsEngine : 'auto',
    };
  } catch {
    return fallback;
  }
}

function saveVoicePreferences(preferences: VoicePreferences) {
  try {
    window.localStorage.setItem(VOICE_PREFERENCES_KEY, JSON.stringify(preferences));
  } catch {
    // Voice remains usable when storage is unavailable or full.
  }
}

function isAbortError(error: unknown) {
  return error instanceof DOMException && error.name === 'AbortError';
}

function ttsSelection(status: VoiceEngineStatus, engine: VoiceTtsEngine) {
  const piper = status.tts?.piper;
  const cosy = status.tts?.cosyvoice;
  if (engine === 'piper') {
    return { ready: Boolean(piper?.ready), label: piper?.label || '华严 · 本地 Piper' };
  }
  if (engine === 'cosyvoice') {
    return { ready: Boolean(cosy?.ready), label: cosy?.label || 'Jarvis · CosyVoice 2' };
  }
  return { ready: Boolean(status.tts?.ready), label: status.tts?.label || '本地语音' };
}

export function useVoiceSession({
  onSend,
  onInterrupt,
  hasApproval = false,
}: UseVoiceSessionOptions) {
  const initialPreferencesRef = useRef<VoicePreferences | null>(null);
  if (!initialPreferencesRef.current) initialPreferencesRef.current = loadVoicePreferences();
  const initialPreferences = initialPreferencesRef.current;

  const [open, setOpen] = useState(false);
  const [phase, setPhase] = useState<VoicePhase>('idle');
  const [muted, setMuted] = useState(false);
  const [showTranscript, setShowTranscript] = useState(false);
  const [error, setError] = useState('');
  const [level, setLevel] = useState(0);
  const [userTurns, setUserTurns] = useState<string[]>([]);
  const [assistantText, setAssistantText] = useState('');
  const [sttReady, setSttReady] = useState(false);
  const [ttsReady, setTtsReady] = useState(false);
  const [sttLabel, setSttLabel] = useState('Whisper');
  const [ttsLabel, setTtsLabel] = useState('Piper');
  const [ttsEngine, setTtsEngineState] = useState<VoiceTtsEngine>(initialPreferences.ttsEngine);
  const [cosyReady, setCosyReady] = useState(false);
  const [cosyStartable, setCosyStartable] = useState(false);
  const [silenceMs, setSilenceMsState] = useState(initialPreferences.silenceMs);

  const phaseRef = useRef<VoicePhase>('idle');
  const mutedRef = useRef(false);
  const openRef = useRef(false);
  const lifecycleRef = useRef(0);
  const startingRef = useRef(false);
  const silenceMsRef = useRef(initialPreferences.silenceMs);
  const ttsEngineRef = useRef<VoiceTtsEngine>(initialPreferences.ttsEngine);
  const ttsReadyRef = useRef(false);
  const speechMsRef = useRef(0);
  const quietMsRef = useRef(0);
  const utteranceMsRef = useRef(0);
  const preRollMsRef = useRef(0);
  const bargeMsRef = useRef(0);
  const pcmRef = useRef<Float32Array[]>([]);
  const preRollRef = useRef<Float32Array[]>([]);
  const bargeFramesRef = useRef<Float32Array[]>([]);
  const committingRef = useRef(false);
  const statusControllerRef = useRef<AbortController | null>(null);
  const sttControllerRef = useRef<AbortController | null>(null);

  const spokenPrefixRef = useRef('');
  const lastAssistantRef = useRef('');
  const awaitingAssistantRef = useRef(false);
  const assistantStreamingRef = useRef(false);
  const activeVoiceModelRef = useRef<string | null>(null);
  const blockedVoiceModelsRef = useRef(new Set<string>());
  const baselineMessageIdsRef = useRef(new Set<string>());

  const queueRef = useRef<Blob[]>([]);
  const pendingSpeechRef = useRef(0);
  const playingRef = useRef(false);
  const audioRef = useRef<HTMLAudioElement | null>(null);
  const audioUrlRef = useRef('');
  const speechGenerationRef = useRef(0);
  const synthesisTailRef = useRef<Promise<void>>(Promise.resolve());
  const speechControllersRef = useRef(new Set<AbortController>());

  const streamRef = useRef<MediaStream | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);

  const move = useCallback((event: Parameters<typeof reduceVoicePhase>[1]) => {
    const next = reduceVoicePhase(phaseRef.current, event);
    phaseRef.current = next;
    setPhase(next);
    return next;
  }, []);

  const clearCurrentAudio = useCallback(() => {
    const audio = audioRef.current;
    audioRef.current = null;
    if (audio) {
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
    }
    if (audioUrlRef.current) {
      URL.revokeObjectURL(audioUrlRef.current);
      audioUrlRef.current = '';
    }
  }, []);

  const cancelSpeech = useCallback((resetTracking: boolean) => {
    speechGenerationRef.current += 1;
    speechControllersRef.current.forEach((controller) => controller.abort());
    speechControllersRef.current.clear();
    synthesisTailRef.current = Promise.resolve();
    queueRef.current = [];
    pendingSpeechRef.current = 0;
    playingRef.current = false;
    clearCurrentAudio();
    if (resetTracking) {
      spokenPrefixRef.current = '';
      lastAssistantRef.current = '';
    }
  }, [clearCurrentAudio]);

  const settleSpeechState = useCallback(() => {
    if (!openRef.current || playingRef.current) return;
    if (pendingSpeechRef.current > 0 || assistantStreamingRef.current) {
      if (phaseRef.current === 'speaking') move({ type: 'waiting_audio' });
      return;
    }
    if (phaseRef.current === 'thinking' || phaseRef.current === 'speaking') {
      move({ type: 'playback_done' });
    }
  }, [move]);

  const playNext = useCallback(() => {
    if (!openRef.current || playingRef.current) return;
    const next = queueRef.current.shift();
    if (!next) {
      settleSpeechState();
      return;
    }

    playingRef.current = true;
    if (phaseRef.current !== 'speaking') move({ type: 'first_audio' });
    const url = URL.createObjectURL(next);
    const audio = new Audio(url);
    let finished = false;
    audioRef.current = audio;
    audioUrlRef.current = url;

    const finish = () => {
      if (finished) return;
      finished = true;
      if (audioRef.current === audio) clearCurrentAudio();
      pendingSpeechRef.current = Math.max(0, pendingSpeechRef.current - 1);
      playingRef.current = false;
      playNext();
    };
    audio.onended = finish;
    audio.onerror = finish;
    void audio.play().catch(finish);
  }, [clearCurrentAudio, move, settleSpeechState]);

  const enqueueSpeech = useCallback((sentence: string) => {
    const spoken = markdownToSpeech(sentence);
    if (!spoken || !openRef.current) return;
    if (!ttsReadyRef.current) {
      setError((current) => current || '朗读引擎尚未就绪，回复仍会显示为文字。');
      return;
    }

    const generation = speechGenerationRef.current;
    pendingSpeechRef.current += 1;
    const synthesize = async () => {
      if (generation !== speechGenerationRef.current || !openRef.current) return;
      const controller = new AbortController();
      speechControllersRef.current.add(controller);
      try {
        const blob = await fetchBlob(`${API_BASE}/voice/tts`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text: spoken, engine: ttsEngineRef.current }),
          signal: controller.signal,
        });
        if (generation !== speechGenerationRef.current || !openRef.current) return;
        if (blob.size < 44) throw new Error('朗读引擎返回了空音频');
        queueRef.current.push(blob);
        setError((current) => current.startsWith('朗读') ? '' : current);
        playNext();
      } catch (speechError) {
        if (generation !== speechGenerationRef.current || isAbortError(speechError)) return;
        pendingSpeechRef.current = Math.max(0, pendingSpeechRef.current - 1);
        setError(`朗读暂时不可用：${speechError instanceof Error ? speechError.message : String(speechError)}`);
        settleSpeechState();
      } finally {
        speechControllersRef.current.delete(controller);
      }
    };
    synthesisTailRef.current = synthesisTailRef.current.then(synthesize, synthesize);
  }, [playNext, settleSpeechState]);

  const consumeAssistantDelta = useCallback((fullText: string) => {
    if (!openRef.current || !awaitingAssistantRef.current) return;
    lastAssistantRef.current = fullText;
    setAssistantText(fullText);

    if (!fullText.startsWith(spokenPrefixRef.current)) {
      // A provider rewrote an already-streamed prefix. Drop stale queued audio
      // and restart from the revised text instead of mixing two answer versions.
      cancelSpeech(true);
      lastAssistantRef.current = fullText;
    }
    const buffer = fullText.slice(spokenPrefixRef.current.length);
    const { ready, consumedLength } = takeSpeakableSentences(buffer);
    if (consumedLength > 0) {
      spokenPrefixRef.current += buffer.slice(0, consumedLength);
    }
    ready.forEach(enqueueSpeech);
  }, [cancelSpeech, enqueueSpeech]);

  const flushAssistant = useCallback((fullText: string) => {
    if (!fullText.startsWith(spokenPrefixRef.current)) {
      cancelSpeech(true);
    }
    const leftover = fullText.slice(spokenPrefixRef.current.length).trim();
    spokenPrefixRef.current = fullText;
    lastAssistantRef.current = fullText;
    setAssistantText(fullText);
    assistantStreamingRef.current = false;
    if (leftover) enqueueSpeech(leftover);
    settleSpeechState();
  }, [cancelSpeech, enqueueSpeech, settleSpeechState]);

  const resetCaptureBuffers = useCallback(() => {
    pcmRef.current = [];
    preRollRef.current = [];
    bargeFramesRef.current = [];
    speechMsRef.current = 0;
    quietMsRef.current = 0;
    utteranceMsRef.current = 0;
    preRollMsRef.current = 0;
    bargeMsRef.current = 0;
  }, []);

  const abandonAssistantTurn = useCallback(() => {
    awaitingAssistantRef.current = false;
    assistantStreamingRef.current = false;
    activeVoiceModelRef.current = null;
    blockedVoiceModelsRef.current.clear();
  }, []);

  const commitUtterance = useCallback(async () => {
    if (committingRef.current || mutedRef.current || !openRef.current) return;
    const chunks = pcmRef.current;
    resetCaptureBuffers();
    if (!chunks.length) return;

    const generation = lifecycleRef.current;
    const controller = new AbortController();
    sttControllerRef.current = controller;
    committingRef.current = true;
    move({ type: 'commit' });

    const total = chunks.reduce((sum, chunk) => sum + chunk.length, 0);
    const merged = new Float32Array(total);
    let offset = 0;
    chunks.forEach((chunk) => {
      merged.set(chunk, offset);
      offset += chunk.length;
    });
    const wav = encodeWav(floatTo16BitPcm(merged), SAMPLE_RATE);
    try {
      const body = new FormData();
      body.append('file', wav, 'utterance.wav');
      const result = await fetchJson<{ text?: string }>(`${API_BASE}/voice/stt?language=zh`, {
        method: 'POST',
        body,
        signal: controller.signal,
      });
      if (generation !== lifecycleRef.current || !openRef.current) return;
      const text = cleanTranscript(result.text || '');
      if (!text) {
        move({ type: 'empty_transcript' });
        return;
      }

      setError('');
      setUserTurns((current) => [...current.slice(-6), text]);
      move({ type: 'transcript', text });
      cancelSpeech(true);
      setAssistantText('');
      awaitingAssistantRef.current = true;
      assistantStreamingRef.current = true;
      activeVoiceModelRef.current = null;
      const store = useSessionStore.getState();
      blockedVoiceModelsRef.current = new Set(Object.keys(store.streams));
      baselineMessageIdsRef.current = new Set(store.messages.map((message) => message.id));

      const sent = onSend(text, ['all'], []);
      if (sent === false) {
        abandonAssistantTurn();
        setError('后端未连接，这句话还没发出去。');
        move({ type: 'error' });
        return;
      }
      move({ type: 'sent' });
    } catch (sttError) {
      if (generation !== lifecycleRef.current || isAbortError(sttError)) return;
      setError(sttError instanceof Error ? sttError.message : '识别失败');
      move({ type: 'empty_transcript' });
    } finally {
      if (sttControllerRef.current === controller) {
        sttControllerRef.current = null;
        committingRef.current = false;
      }
    }
  }, [abandonAssistantTurn, cancelSpeech, move, onSend, resetCaptureBuffers]);

  const handleFrame = useCallback((frame: Float32Array, frameMs: number) => {
    if (!openRef.current || mutedRef.current) {
      setLevel(0);
      return;
    }
    const levelNow = rmsLevel(frame);
    setLevel(levelNow);
    const voiced = levelNow >= DEFAULT_SPEECH_THRESHOLD;

    if (phaseRef.current === 'speaking' || phaseRef.current === 'thinking') {
      if (voiced) {
        bargeMsRef.current += frameMs;
        bargeFramesRef.current.push(frame.slice());
        while (bargeMsRef.current > BARGE_IN_HOLD_MS + PRE_ROLL_MS && bargeFramesRef.current.length > 1) {
          const removed = bargeFramesRef.current.shift();
          bargeMsRef.current -= removed ? (removed.length / SAMPLE_RATE) * 1000 : 0;
        }
      } else {
        bargeMsRef.current = 0;
        bargeFramesRef.current = [];
      }
      if (shouldBargeIn({ speaking: true, speechHoldMs: bargeMsRef.current })) {
        const openingFrames = bargeFramesRef.current;
        const openingMs = bargeMsRef.current;
        abandonAssistantTurn();
        cancelSpeech(true);
        onInterrupt?.();
        resetCaptureBuffers();
        pcmRef.current = openingFrames;
        speechMsRef.current = openingMs;
        utteranceMsRef.current = openingMs;
        move({ type: 'barge_in' });
      }
      return;
    }
    if (phaseRef.current !== 'listening' || committingRef.current) return;

    const copy = frame.slice();
    if (speechMsRef.current <= 0) {
      preRollRef.current.push(copy);
      preRollMsRef.current += frameMs;
      while (preRollMsRef.current > PRE_ROLL_MS && preRollRef.current.length > 1) {
        const removed = preRollRef.current.shift();
        preRollMsRef.current -= removed ? (removed.length / SAMPLE_RATE) * 1000 : 0;
      }
      if (!voiced) return;
      pcmRef.current = [...preRollRef.current];
      utteranceMsRef.current = preRollMsRef.current;
      speechMsRef.current = frameMs;
      quietMsRef.current = 0;
      preRollRef.current = [];
      preRollMsRef.current = 0;
    } else {
      pcmRef.current.push(copy);
      utteranceMsRef.current += frameMs;
      if (voiced) {
        speechMsRef.current += frameMs;
        quietMsRef.current = 0;
      } else {
        quietMsRef.current += frameMs;
      }
    }

    if (shouldResetShortUtterance({
      speechMs: speechMsRef.current,
      silenceMs: quietMsRef.current,
      silenceTimeout: silenceMsRef.current,
    })) {
      resetCaptureBuffers();
      return;
    }
    if (shouldCommitUtterance({
      speechMs: speechMsRef.current,
      silenceMs: quietMsRef.current,
      silenceTimeout: silenceMsRef.current,
    }) || (speechMsRef.current >= 420 && utteranceMsRef.current >= MAX_UTTERANCE_MS)) {
      void commitUtterance();
    }
  }, [abandonAssistantTurn, cancelSpeech, commitUtterance, move, onInterrupt, resetCaptureBuffers]);

  const teardownCapture = useCallback(async () => {
    if (processorRef.current) processorRef.current.onaudioprocess = null;
    processorRef.current?.disconnect();
    sourceRef.current?.disconnect();
    processorRef.current = null;
    sourceRef.current = null;
    streamRef.current?.getTracks().forEach((track) => {
      track.onended = null;
      track.stop();
    });
    streamRef.current = null;
    if (contextRef.current) {
      await contextRef.current.close().catch(() => undefined);
      contextRef.current = null;
    }
  }, []);

  const stop = useCallback(async () => {
    lifecycleRef.current += 1;
    startingRef.current = false;
    openRef.current = false;
    setOpen(false);
    statusControllerRef.current?.abort();
    statusControllerRef.current = null;
    sttControllerRef.current?.abort();
    sttControllerRef.current = null;
    committingRef.current = false;
    abandonAssistantTurn();
    cancelSpeech(true);
    await teardownCapture();
    resetCaptureBuffers();
    move({ type: 'stop' });
    setLevel(0);
  }, [abandonAssistantTurn, cancelSpeech, move, resetCaptureBuffers, teardownCapture]);

  const startCapture = useCallback(async (generation: number) => {
    if (!navigator.mediaDevices?.getUserMedia) {
      throw new Error('当前系统不支持麦克风采集');
    }
    const stream = await navigator.mediaDevices.getUserMedia({
      audio: {
        channelCount: 1,
        echoCancellation: true,
        noiseSuppression: true,
        autoGainControl: true,
        sampleRate: { ideal: 48000 },
      },
    });
    if (generation !== lifecycleRef.current || !openRef.current) {
      stream.getTracks().forEach((track) => track.stop());
      throw new DOMException('Voice session stopped', 'AbortError');
    }

    streamRef.current = stream;
    const context = new AudioContext({ latencyHint: 'interactive' });
    contextRef.current = context;
    if (context.state === 'suspended') await context.resume();
    if (generation !== lifecycleRef.current || !openRef.current) {
      await teardownCapture();
      throw new DOMException('Voice session stopped', 'AbortError');
    }

    const source = context.createMediaStreamSource(stream);
    const processor = context.createScriptProcessor(4096, 1, 1);
    sourceRef.current = source;
    processorRef.current = processor;
    const silent = context.createGain();
    silent.gain.value = 0;
    processor.onaudioprocess = (event) => {
      const input = event.inputBuffer.getChannelData(0);
      const resampled = downsample(input, event.inputBuffer.sampleRate, SAMPLE_RATE);
      const frameDuration = (resampled.length / SAMPLE_RATE) * 1000;
      handleFrame(resampled, frameDuration);
    };
    source.connect(processor);
    processor.connect(silent);
    silent.connect(context.destination);

    stream.getAudioTracks().forEach((track) => {
      track.onended = () => {
        if (generation !== lifecycleRef.current || !openRef.current) return;
        setError('麦克风连接已断开，请结束后重新进入语音对话。');
        setLevel(0);
        move({ type: 'error' });
      };
    });
  }, [handleFrame, move, teardownCapture]);

  const start = useCallback(async () => {
    if (openRef.current || startingRef.current) return;
    startingRef.current = true;
    const generation = lifecycleRef.current + 1;
    lifecycleRef.current = generation;
    setError('');
    setUserTurns([]);
    setAssistantText('');
    setMuted(false);
    mutedRef.current = false;
    openRef.current = true;
    setOpen(true);
    resetCaptureBuffers();
    cancelSpeech(true);
    move({ type: 'start' });

    const statusController = new AbortController();
    statusControllerRef.current = statusController;
    try {
      const status = await fetchJson<VoiceEngineStatus>(`${API_BASE}/voice/status`, {
        signal: statusController.signal,
      });
      if (generation !== lifecycleRef.current || !openRef.current) return;
      setSttReady(Boolean(status.stt?.ready));
      const selectedTts = ttsSelection(status, ttsEngineRef.current);
      ttsReadyRef.current = selectedTts.ready;
      setTtsReady(selectedTts.ready);
      setCosyReady(Boolean(status.tts?.cosyvoice?.ready));
      setCosyStartable(Boolean(status.tts?.cosyvoice?.startable));
      if (status.stt?.label) setSttLabel(status.stt.label);
      setTtsLabel(selectedTts.label);
      if (!status.stt?.ready) {
        setError((status.hints || []).join('；') || '本机识别引擎未就绪');
      }
    } catch (statusError) {
      if (generation !== lifecycleRef.current || isAbortError(statusError)) return;
      setError(statusError instanceof Error ? statusError.message : '无法读取语音引擎状态');
    } finally {
      if (statusControllerRef.current === statusController) statusControllerRef.current = null;
    }

    try {
      if (generation !== lifecycleRef.current || !openRef.current) return;
      await startCapture(generation);
    } catch (captureError) {
      if (generation !== lifecycleRef.current || isAbortError(captureError)) return;
      setError(microphoneErrorHint(captureError));
      await stop();
    } finally {
      if (generation === lifecycleRef.current) startingRef.current = false;
    }
  }, [cancelSpeech, move, resetCaptureBuffers, startCapture, stop]);

  useEffect(() => {
    if (!open) return undefined;
    let previousStreams = { ...useSessionStore.getState().streams };
    return useSessionStore.subscribe((state) => {
      const currentStreams = state.streams;
      blockedVoiceModelsRef.current.forEach((model) => {
        if (!(model in currentStreams)) blockedVoiceModelsRef.current.delete(model);
      });
      if (!awaitingAssistantRef.current) {
        previousStreams = { ...currentStreams };
        return;
      }

      const newAssistantMessages = state.messages.filter((message) => (
        !baselineMessageIdsRef.current.has(message.id) && message.author_model !== 'user'
      ));
      let activeModel = activeVoiceModelRef.current;
      if (!activeModel) {
        const changed = Object.entries(currentStreams).find(([model, text]) => (
          Boolean(text)
          && text !== previousStreams[model]
          && !blockedVoiceModelsRef.current.has(model)
        ));
        const completed = newAssistantMessages[0];
        activeModel = changed?.[0] || completed?.author_model || null;
        activeVoiceModelRef.current = activeModel;
        if (activeModel) assistantStreamingRef.current = true;
      }

      if (activeModel) {
        const completed = newAssistantMessages.find((message) => message.author_model === activeModel);
        const currentText = currentStreams[activeModel];
        if (currentText && currentText !== previousStreams[activeModel]) {
          consumeAssistantDelta(currentText);
        }
        if (completed) {
          awaitingAssistantRef.current = false;
          activeVoiceModelRef.current = null;
          flushAssistant(completed.content || currentText || lastAssistantRef.current);
        }
      }
      previousStreams = { ...currentStreams };
    });
  }, [consumeAssistantDelta, flushAssistant, open]);

  useEffect(() => {
    if (open && hasApproval) {
      cancelSpeech(false);
      if (phaseRef.current === 'speaking' || phaseRef.current === 'thinking') {
        move({ type: 'approval' });
      }
    }
  }, [cancelSpeech, hasApproval, move, open]);

  useEffect(() => {
    const onHidden = () => {
      if (document.hidden && openRef.current) void stop();
    };
    document.addEventListener('visibilitychange', onHidden);
    return () => document.removeEventListener('visibilitychange', onHidden);
  }, [stop]);

  useEffect(() => () => { void stop(); }, [stop]);

  const toggleMute = useCallback(() => {
    const next = !mutedRef.current;
    mutedRef.current = next;
    setMuted(next);
    resetCaptureBuffers();
    setLevel(0);
  }, [resetCaptureBuffers]);

  const interrupt = useCallback(() => {
    if (!openRef.current) return;
    abandonAssistantTurn();
    cancelSpeech(true);
    onInterrupt?.();
    resetCaptureBuffers();
    move({ type: 'barge_in' });
  }, [abandonAssistantTurn, cancelSpeech, move, onInterrupt, resetCaptureBuffers]);

  const setSilenceMs = useCallback((value: number) => {
    const next = Math.min(1600, Math.max(700, Math.round(value / 100) * 100));
    silenceMsRef.current = next;
    setSilenceMsState(next);
    saveVoicePreferences({ silenceMs: next, ttsEngine: ttsEngineRef.current });
  }, []);

  const setTtsEngine = useCallback((value: VoiceTtsEngine) => {
    ttsEngineRef.current = value;
    setTtsEngineState(value);
    saveVoicePreferences({ silenceMs: silenceMsRef.current, ttsEngine: value });
  }, []);

  const startCosyVoice = useCallback(async () => {
    setError('');
    const result = await fetchJson<{ label?: string }>(`${API_BASE}/voice/tts/cosyvoice/start`, { method: 'POST' });
    setCosyReady(true);
    if (ttsEngineRef.current !== 'piper') {
      ttsReadyRef.current = true;
      setTtsReady(true);
      setTtsLabel(result.label || 'Jarvis · CosyVoice 2');
    }
    return result;
  }, []);

  return {
    open,
    phase,
    muted,
    showTranscript,
    error,
    level,
    userTurns,
    assistantText,
    sttReady,
    ttsReady,
    sttLabel,
    ttsLabel,
    silenceMs,
    ttsEngine,
    cosyReady,
    cosyStartable,
    setSilenceMs,
    setTtsEngine,
    startCosyVoice,
    start,
    stop,
    toggleMute,
    toggleTranscript: () => setShowTranscript((value) => !value),
    interrupt,
  };
}
