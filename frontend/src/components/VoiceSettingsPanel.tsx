import { useCallback, useEffect, useState } from 'react';
import { AudioLines, Mic, RefreshCw } from 'lucide-react';
import { fetchJson } from '../api/client';
import { useI18n } from '../i18n';
import { API_BASE } from './shared';
import { microphoneErrorHint, type VoiceInputMode } from './voiceSession';

type VoiceStatus = {
  ready?: boolean;
  privacy?: string;
  stt?: { ready?: boolean; label?: string; local_ready?: boolean; network_ready?: boolean };
  tts?: {
    ready?: boolean;
    label?: string;
    piper?: { ready?: boolean; label?: string };
    cosyvoice?: { ready?: boolean; label?: string; startable?: boolean };
    network?: { ready?: boolean; label?: string };
  };
  network?: { ready?: boolean; label?: string; provider?: string | null };
  hints?: string[];
};

type VoiceSettingsPanelProps = {
  silenceMs: number;
  onSilenceMs: (value: number) => void;
  ttsEngine: 'auto' | 'piper' | 'cosyvoice' | 'network';
  onTtsEngine: (value: 'auto' | 'piper' | 'cosyvoice' | 'network') => void;
  inputMode?: VoiceInputMode;
  onInputMode?: (value: VoiceInputMode) => void;
  autoSpeak?: boolean;
  onAutoSpeak?: (value: boolean) => void;
  onStartLive: () => void;
  onStartCosyVoice?: () => Promise<unknown>;
};

export function VoiceSettingsPanel({
  silenceMs,
  onSilenceMs,
  ttsEngine,
  onTtsEngine,
  inputMode = 'conversation',
  onInputMode,
  autoSpeak = false,
  onAutoSpeak,
  onStartLive,
  onStartCosyVoice,
}: VoiceSettingsPanelProps) {
  const { t, ui } = useI18n();
  const [status, setStatus] = useState<VoiceStatus | null>(null);
  const [statusError, setStatusError] = useState('');
  const [deviceLabel, setDeviceLabel] = useState('');
  const [startingCosy, setStartingCosy] = useState(false);

  const refreshStatus = useCallback(async () => {
    try {
      const data = await fetchJson<VoiceStatus>(`${API_BASE}/voice/status`);
      setStatus(data);
      setStatusError('');
      return data;
    } catch (error) {
      setStatusError(error instanceof Error ? error.message : t('couldNotReadTheVoice'));
      return null;
    }
  }, [t]);

  useEffect(() => {
    let cancelled = false;
    fetchJson<VoiceStatus>(`${API_BASE}/voice/status`)
      .then((data) => {
        if (!cancelled) {
          setStatus(data);
          setStatusError('');
        }
      })
      .catch((error) => {
        if (!cancelled) setStatusError(error instanceof Error ? error.message : t('couldNotReadTheVoice'));
      });
    return () => { cancelled = true; };
  }, [t]);

  const probeMic = async () => {
    setDeviceLabel('');
    let stream: MediaStream | null = null;
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        setDeviceLabel(t('thisSystemCannotCaptureMicrophone'));
        return;
      }
      const listed = await navigator.mediaDevices.enumerateDevices();
      const inputs = listed.filter((device) => device.kind === 'audioinput');
      if (inputs.length === 0) {
        setDeviceLabel(ui('没有检测到麦克风。本机若只有 HDMI/扬声器输出，请连接 USB 或蓝牙麦克风后再试；朗读回复仍可使用。'));
        return;
      }
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const label = stream.getAudioTracks()[0]?.label || inputs[0]?.label || t('openedTheDefaultMicrophone');
      setDeviceLabel(label);
    } catch (error) {
      setDeviceLabel(ui(microphoneErrorHint(error)));
    } finally {
      stream?.getTracks().forEach((track) => track.stop());
    }
  };

  return (
    <div className="aether-tab-panel aether-panel-scroll min-h-0 flex-1 overflow-auto p-4">
      <div className="mb-4 flex items-center gap-2 text-sm font-semibold">
        <AudioLines size={16} />
        {t('voiceConversation')}
      </div>
      <p className="mb-4 text-xs leading-5 text-[#7a7065]">
        {ui('对标 ChatGPT：输入框按住麦克风听写、消息可朗读、实时语音可打断；本地 Whisper/Piper 优先，网络智谱/OpenAI 软回退。')}
      </p>
      <section className="mb-4 rounded-xl border border-[#ded6ca] bg-[#fbfaf7] p-3 text-xs text-[#574a3e]" aria-live="polite">
        <div className="flex items-center justify-between gap-2">
          <div className="font-semibold">{t('speechRecognition')}</div>
          <button
            type="button"
            onClick={() => { void refreshStatus(); }}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[#6d6258] hover:bg-white"
          >
            <RefreshCw size={12} />
            {t('refreshStatus')}
          </button>
        </div>
        <div className="mt-1 text-[#7a7065]">
          {status?.stt?.ready ? t('readyStatus') : t('notFound')}
          {status?.stt?.label ? ` · ${status.stt.label}` : ''}
        </div>
        <div className="mt-1 text-[#7a7065]">
          {status?.network?.ready
            ? ui(`网络语音可用 · ${status.network.label || status.network.provider || ''}`)
            : ui('网络语音未配置（可在模型设置里放入 OpenAI / 智谱密钥）')}
        </div>
        <div className="mt-3 font-semibold">{t('speechPlayback')}</div>
        <div className="mt-1 text-[#7a7065]">
          {status?.tts?.piper?.ready ? t('huayanPiperIsReady') : t('huayanPiperWasNotFound')}
        </div>
        <div className="mt-1 text-[#7a7065]">
          {status?.tts?.cosyvoice?.ready ? t('jarvisCosyVoiceIsReady') : t('jarvisCosyVoiceIsNotRunning')}
        </div>
        <div className="mt-1 text-[#7a7065]">
          {status?.tts?.network?.ready ? (status.tts.network.label || ui('网络朗读就绪')) : ui('网络朗读未就绪')}
        </div>
        {status?.privacy && (
          <div className="mt-2 text-[#7a7065]">
            {status.privacy === 'audio_stays_local' ? ui('隐私：当前优先本机，音频可留在设备内') : ui('隐私：网络引擎启用时，音频会离开本机')}
          </div>
        )}
        {(status?.hints?.length || statusError) && (
          <p className="mt-2 text-[#8a4b2b]">{statusError || status?.hints?.join('；')}</p>
        )}
        <label className="mt-3 block font-semibold">
          {t('defaultVoice')}
          <select
            value={ttsEngine}
            onChange={(event) => onTtsEngine(event.target.value as 'auto' | 'piper' | 'cosyvoice' | 'network')}
            className="mt-1 h-8 w-full rounded-md border border-[#ded6ca] bg-white px-2 font-normal"
          >
            <option value="auto">{t('autoUseJarvisWhenIt')}</option>
            <option value="cosyvoice">{t('jarvisCosyVoice2')}</option>
            <option value="piper">{t('huayanPiper')}</option>
            <option value="network">{ui('网络朗读（OpenAI / 智谱）')}</option>
          </select>
        </label>
        {onInputMode && (
          <label className="mt-3 block font-semibold">
            {ui('实时语音模式')}
            <select
              value={inputMode}
              onChange={(event) => onInputMode(event.target.value as VoiceInputMode)}
              className="mt-1 h-8 w-full rounded-md border border-[#ded6ca] bg-white px-2 font-normal"
            >
              <option value="conversation">{ui('连续对话（开口即说）')}</option>
              <option value="push_to_talk">{ui('按住说话')}</option>
            </select>
          </label>
        )}
        {onAutoSpeak && (
          <label className="mt-3 flex items-center gap-2 font-semibold">
            <input
              type="checkbox"
              checked={autoSpeak}
              onChange={(event) => onAutoSpeak(event.target.checked)}
            />
            <span>{ui('自动朗读助手回复（文字对话）')}</span>
          </label>
        )}
        {onStartCosyVoice && status?.tts?.cosyvoice?.startable && !status.tts.cosyvoice.ready && (
          <button
            type="button"
            disabled={startingCosy}
            onClick={() => {
              setStartingCosy(true);
              setStatusError('');
              void onStartCosyVoice()
                .then(() => refreshStatus())
                .catch((error) => setStatusError(error instanceof Error ? error.message : t('couldNotStartTheNatural')))
                .finally(() => setStartingCosy(false));
            }}
            className="mt-3 inline-flex h-8 items-center rounded-md bg-[#8d5f35] px-3 font-medium text-white disabled:opacity-60"
          >
            {startingCosy ? t('loadingTheNaturalVoice') : t('startJarvisNaturalVoice')}
          </button>
        )}
      </section>
      <label className="mb-4 block text-xs font-semibold text-[#7a7065]">
        {t('pauseAfterYouFinish')}
        <input
          type="range"
          min={700}
          max={1600}
          step={100}
          value={silenceMs}
          onChange={(event) => onSilenceMs(Number(event.target.value))}
          className="aether-range mt-2"
          aria-valuemin={700}
          aria-valuemax={1600}
          aria-valuenow={silenceMs}
          aria-valuetext={`${silenceMs}ms`}
        />
        <span className="mt-1 block font-normal">{t('waitMsOfSilenceBefore', { ms: silenceMs })}</span>
      </label>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => { void probeMic(); }}
          className="inline-flex h-8 items-center gap-1 rounded-md border border-[#b8a999] px-3 text-xs"
        >
          <Mic size={13} />
          {t('checkMicrophone')}
        </button>
        <button
          type="button"
          onClick={onStartLive}
          className="inline-flex h-8 items-center rounded-md bg-[#2f2a24] px-3 text-xs font-medium text-white"
        >
          {t('startVoiceChat')}
        </button>
      </div>
      {deviceLabel && <p className="mt-3 text-xs text-[#6d6258]">{deviceLabel}</p>}
    </div>
  );
}
