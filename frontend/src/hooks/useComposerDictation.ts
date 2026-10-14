import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchJson } from '../api/client';
import { API_BASE } from '../components/shared';
import {
  DEFAULT_SPEECH_THRESHOLD,
  SAMPLE_RATE,
  cleanTranscript,
  downsample,
  encodeWav,
  floatTo16BitPcm,
  microphoneErrorHint,
  rmsLevel,
} from '../components/voiceSession';

export type DictationPhase = 'idle' | 'listening' | 'processing' | 'error';

type UseComposerDictationOptions = {
  onTranscript: (text: string) => void;
  language?: string;
};

type SpeechRecognitionLike = {
  lang: string;
  continuous: boolean;
  interimResults: boolean;
  onresult: ((event: { resultIndex: number; results: ArrayLike<{ isFinal?: boolean; 0: { transcript: string } }> }) => void) | null;
  onerror: ((event: { error?: string }) => void) | null;
  onend: (() => void) | null;
  start: () => void;
  stop: () => void;
  abort: () => void;
};

function getSpeechRecognitionCtor(): (new () => SpeechRecognitionLike) | null {
  const root = window as Window & {
    SpeechRecognition?: new () => SpeechRecognitionLike;
    webkitSpeechRecognition?: new () => SpeechRecognitionLike;
  };
  return root.SpeechRecognition || root.webkitSpeechRecognition || null;
}

export function useComposerDictation({
  onTranscript,
  language = 'zh-CN',
}: UseComposerDictationOptions) {
  const [phase, setPhase] = useState<DictationPhase>('idle');
  const [partial, setPartial] = useState('');
  const [error, setError] = useState('');
  const [level, setLevel] = useState(0);

  const phaseRef = useRef<DictationPhase>('idle');
  const holdingRef = useRef(false);
  const streamRef = useRef<MediaStream | null>(null);
  const contextRef = useRef<AudioContext | null>(null);
  const processorRef = useRef<ScriptProcessorNode | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const pcmRef = useRef<Float32Array[]>([]);
  const recognitionRef = useRef<SpeechRecognitionLike | null>(null);
  const partialRef = useRef('');
  const generationRef = useRef(0);

  const setPhaseBoth = useCallback((next: DictationPhase) => {
    phaseRef.current = next;
    setPhase(next);
  }, []);

  const teardownCapture = useCallback(async () => {
    if (processorRef.current) processorRef.current.onaudioprocess = null;
    processorRef.current?.disconnect();
    sourceRef.current?.disconnect();
    processorRef.current = null;
    sourceRef.current = null;
    streamRef.current?.getTracks().forEach((track) => track.stop());
    streamRef.current = null;
    if (contextRef.current) {
      await contextRef.current.close().catch(() => undefined);
      contextRef.current = null;
    }
    if (recognitionRef.current) {
      try { recognitionRef.current.onresult = null; recognitionRef.current.onerror = null; recognitionRef.current.onend = null; recognitionRef.current.abort(); } catch { /* ignore */ }
      recognitionRef.current = null;
    }
    setLevel(0);
  }, []);

  const finalize = useCallback(async () => {
    const generation = generationRef.current;
    const chunks = pcmRef.current;
    pcmRef.current = [];
    const livePartial = cleanTranscript(partialRef.current);
    partialRef.current = '';
    setPartial('');

    if (!chunks.length && !livePartial) {
      setPhaseBoth('idle');
      return;
    }

    setPhaseBoth('processing');
    if (!chunks.length && livePartial) {
      onTranscript(livePartial);
      setPhaseBoth('idle');
      return;
    }

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
      body.append('file', wav, 'dictation.wav');
      const result = await fetchJson<{ text?: string }>(`${API_BASE}/voice/stt?language=zh`, {
        method: 'POST',
        body,
      });
      if (generation !== generationRef.current) return;
      const text = cleanTranscript(result.text || '') || livePartial;
      if (!text) {
        setError('没有听清，请按住麦克风再说一次。');
        setPhaseBoth('error');
        return;
      }
      setError('');
      onTranscript(text);
      setPhaseBoth('idle');
    } catch (sttError) {
      if (generation !== generationRef.current) return;
      if (livePartial) {
        onTranscript(livePartial);
        setError('');
        setPhaseBoth('idle');
        return;
      }
      setError(sttError instanceof Error ? sttError.message : '识别失败');
      setPhaseBoth('error');
    }
  }, [onTranscript, setPhaseBoth]);

  const startListening = useCallback(async () => {
    if (phaseRef.current === 'listening' || phaseRef.current === 'processing') return;
    generationRef.current += 1;
    const generation = generationRef.current;
    setError('');
    setPartial('');
    partialRef.current = '';
    pcmRef.current = [];

    if (!navigator.mediaDevices?.getUserMedia) {
      setError('当前系统不支持麦克风采集');
      setPhaseBoth('error');
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      if (generation !== generationRef.current || !holdingRef.current) {
        stream.getTracks().forEach((track) => track.stop());
        return;
      }
      streamRef.current = stream;
      const context = new AudioContext({ latencyHint: 'interactive' });
      contextRef.current = context;
      if (context.state === 'suspended') await context.resume();
      const source = context.createMediaStreamSource(stream);
      const processor = context.createScriptProcessor(4096, 1, 1);
      sourceRef.current = source;
      processorRef.current = processor;
      const silent = context.createGain();
      silent.gain.value = 0;
      processor.onaudioprocess = (event) => {
        const input = event.inputBuffer.getChannelData(0);
        const resampled = downsample(input, event.inputBuffer.sampleRate, SAMPLE_RATE);
        const levelNow = rmsLevel(resampled);
        setLevel(levelNow);
        if (levelNow >= DEFAULT_SPEECH_THRESHOLD * 0.5 || pcmRef.current.length > 0) {
          pcmRef.current.push(resampled.slice());
        }
      };
      source.connect(processor);
      processor.connect(silent);
      silent.connect(context.destination);

      const Ctor = getSpeechRecognitionCtor();
      if (Ctor) {
        try {
          const recognition = new Ctor();
          recognition.lang = language.startsWith('zh') ? 'zh-CN' : language;
          recognition.continuous = true;
          recognition.interimResults = true;
          recognition.onresult = (event) => {
            let interim = '';
            let finalText = '';
            for (let index = event.resultIndex; index < event.results.length; index += 1) {
              const piece = event.results[index];
              const transcript = piece?.[0]?.transcript || '';
              if (piece?.isFinal) finalText += transcript;
              else interim += transcript;
            }
            const next = cleanTranscript(`${finalText} ${interim}`);
            partialRef.current = next;
            setPartial(next);
          };
          recognition.onerror = () => {
            // Backend STT remains the source of truth; browser partials are best-effort.
          };
          recognition.onend = () => undefined;
          recognitionRef.current = recognition;
          recognition.start();
        } catch {
          recognitionRef.current = null;
        }
      }

      setPhaseBoth('listening');
    } catch (captureError) {
      setError(microphoneErrorHint(captureError));
      setPhaseBoth('error');
      holdingRef.current = false;
      await teardownCapture();
    }
  }, [language, setPhaseBoth, teardownCapture]);

  const stopListening = useCallback(async () => {
    holdingRef.current = false;
    await teardownCapture();
    await finalize();
  }, [finalize, teardownCapture]);

  const beginHold = useCallback(() => {
    holdingRef.current = true;
    void startListening();
  }, [startListening]);

  const endHold = useCallback(() => {
    if (!holdingRef.current && phaseRef.current !== 'listening') return;
    void stopListening();
  }, [stopListening]);

  const toggle = useCallback(() => {
    if (phaseRef.current === 'listening') {
      void stopListening();
      return;
    }
    holdingRef.current = true;
    void startListening();
  }, [startListening, stopListening]);

  const cancel = useCallback(async () => {
    generationRef.current += 1;
    holdingRef.current = false;
    pcmRef.current = [];
    partialRef.current = '';
    setPartial('');
    await teardownCapture();
    setPhaseBoth('idle');
  }, [setPhaseBoth, teardownCapture]);

  useEffect(() => () => { void cancel(); }, [cancel]);

  return {
    phase,
    partial,
    error,
    level,
    beginHold,
    endHold,
    toggle,
    cancel,
    isActive: phase === 'listening' || phase === 'processing',
  };
}
