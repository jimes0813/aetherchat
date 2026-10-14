import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchBlob } from '../api/client';
import { API_BASE } from '../components/shared';
import { markdownToSpeech } from '../components/voiceSession';

export type TtsPlaybackPhase = 'idle' | 'loading' | 'speaking' | 'error';

const AUTO_SPEAK_KEY = 'aetherchat-voice-auto-speak-v1';

export function loadAutoSpeakPreference(): boolean {
  try {
    return window.localStorage.getItem(AUTO_SPEAK_KEY) === '1';
  } catch {
    return false;
  }
}

export function saveAutoSpeakPreference(value: boolean) {
  try {
    window.localStorage.setItem(AUTO_SPEAK_KEY, value ? '1' : '0');
  } catch {
    // ignore
  }
}

export function useTtsPlayback() {
  const [phase, setPhase] = useState<TtsPlaybackPhase>('idle');
  const [error, setError] = useState('');
  const [autoSpeak, setAutoSpeakState] = useState(() => loadAutoSpeakPreference());
  const [speakingMessageId, setSpeakingMessageId] = useState<string | null>(null);

  const audioRef = useRef<HTMLAudioElement | null>(null);
  const urlRef = useRef('');
  const controllerRef = useRef<AbortController | null>(null);
  const generationRef = useRef(0);
  const lastAutoSpokenIdRef = useRef<string | null>(null);

  const clearAudio = useCallback(() => {
    const audio = audioRef.current;
    audioRef.current = null;
    if (audio) {
      audio.onended = null;
      audio.onerror = null;
      audio.pause();
      audio.removeAttribute('src');
      audio.load();
    }
    if (urlRef.current) {
      URL.revokeObjectURL(urlRef.current);
      urlRef.current = '';
    }
  }, []);

  const stop = useCallback(() => {
    generationRef.current += 1;
    controllerRef.current?.abort();
    controllerRef.current = null;
    clearAudio();
    setSpeakingMessageId(null);
    setPhase('idle');
  }, [clearAudio]);

  const speak = useCallback(async (text: string, messageId?: string) => {
    const spoken = markdownToSpeech(text);
    if (!spoken) {
      setError('没有可朗读的文本');
      setPhase('error');
      return;
    }
    stop();
    const generation = generationRef.current;
    const controller = new AbortController();
    controllerRef.current = controller;
    setError('');
    setPhase('loading');
    setSpeakingMessageId(messageId || null);
    try {
      const blob = await fetchBlob(`${API_BASE}/voice/tts`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ text: spoken, engine: 'auto' }),
        signal: controller.signal,
      });
      if (generation !== generationRef.current) return;
      if (blob.size < 44) throw new Error('朗读引擎返回了空音频');
      const url = URL.createObjectURL(blob);
      const audio = new Audio(url);
      audioRef.current = audio;
      urlRef.current = url;
      setPhase('speaking');
      audio.onended = () => {
        if (generation !== generationRef.current) return;
        clearAudio();
        setSpeakingMessageId(null);
        setPhase('idle');
      };
      audio.onerror = () => {
        if (generation !== generationRef.current) return;
        clearAudio();
        setSpeakingMessageId(null);
        setError('音频播放失败');
        setPhase('error');
      };
      await audio.play();
    } catch (playError) {
      if (generation !== generationRef.current) return;
      if (playError instanceof DOMException && playError.name === 'AbortError') return;
      setSpeakingMessageId(null);
      setError(playError instanceof Error ? playError.message : '朗读失败');
      setPhase('error');
    }
  }, [clearAudio, stop]);

  const toggleAutoSpeak = useCallback(() => {
    setAutoSpeakState((current) => {
      const next = !current;
      saveAutoSpeakPreference(next);
      return next;
    });
  }, []);

  const maybeAutoSpeak = useCallback((messageId: string, text: string) => {
    if (!autoSpeak) return;
    if (!messageId || lastAutoSpokenIdRef.current === messageId) return;
    lastAutoSpokenIdRef.current = messageId;
    void speak(text, messageId);
  }, [autoSpeak, speak]);

  useEffect(() => () => { stop(); }, [stop]);

  return {
    phase,
    error,
    autoSpeak,
    speakingMessageId,
    speak,
    stop,
    toggleAutoSpeak,
    setAutoSpeak: (value: boolean) => {
      saveAutoSpeakPreference(value);
      setAutoSpeakState(value);
    },
    maybeAutoSpeak,
    isSpeaking: phase === 'speaking' || phase === 'loading',
  };
}
