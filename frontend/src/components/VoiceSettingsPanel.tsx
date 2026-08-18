import { useCallback, useEffect, useState } from 'react';
import { AudioLines, Mic, RefreshCw } from 'lucide-react';
import { fetchJson } from '../api/client';
import { useI18n } from '../i18n';
import { API_BASE } from './shared';
import { microphoneErrorHint } from './voiceSession';

type VoiceStatus = {
  ready?: boolean;
  stt?: { ready?: boolean; label?: string };
  tts?: {
    ready?: boolean;
    label?: string;
    piper?: { ready?: boolean; label?: string };
    cosyvoice?: { ready?: boolean; label?: string; startable?: boolean };
  };
  hints?: string[];
};

type VoiceSettingsPanelProps = {
  silenceMs: number;
  onSilenceMs: (value: number) => void;
  ttsEngine: 'auto' | 'piper' | 'cosyvoice';
  onTtsEngine: (value: 'auto' | 'piper' | 'cosyvoice') => void;
  onStartLive: () => void;
  onStartCosyVoice?: () => Promise<unknown>;
};

export function VoiceSettingsPanel({
  silenceMs,
  onSilenceMs,
  ttsEngine,
  onTtsEngine,
  onStartLive,
  onStartCosyVoice,
}: VoiceSettingsPanelProps) {
  const { ui } = useI18n();
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
      setStatusError(error instanceof Error ? error.message : ui('无法读取语音引擎'));
      return null;
    }
  }, [ui]);

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
        if (!cancelled) setStatusError(error instanceof Error ? error.message : ui('无法读取语音引擎'));
      });
    return () => { cancelled = true; };
  }, [ui]);

  const probeMic = async () => {
    setDeviceLabel('');
    let stream: MediaStream | null = null;
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        setDeviceLabel(ui('当前系统不支持麦克风采集'));
        return;
      }
      const listed = await navigator.mediaDevices.enumerateDevices();
      const inputs = listed.filter((device) => device.kind === 'audioinput');
      if (inputs.length === 0) {
        setDeviceLabel(ui('没有检测到麦克风。当前机器只有播放设备时，请连接蓝牙耳机或 USB 麦。'));
        return;
      }
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      const label = stream.getAudioTracks()[0]?.label || inputs[0]?.label || ui('已打开默认麦克风');
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
        {ui('语音对答')}
      </div>
      <p className="mb-4 text-xs leading-5 text-[#7a7065]">
        {ui('对标 ChatGPT Live：点一下进入连续对话，说完自动发送，开口即可打断。音频只在本机识别和朗读。')}
      </p>
      <section className="mb-4 rounded-xl border border-[#ded6ca] bg-[#fbfaf7] p-3 text-xs text-[#574a3e]" aria-live="polite">
        <div className="flex items-center justify-between gap-2">
          <div className="font-semibold">{ui('识别引擎')}</div>
          <button
            type="button"
            onClick={() => { void refreshStatus(); }}
            className="inline-flex items-center gap-1 rounded-md px-2 py-1 text-[#6d6258] hover:bg-white"
          >
            <RefreshCw size={12} />
            {ui('刷新状态')}
          </button>
        </div>
        <div className="mt-1 text-[#7a7065]">
          {status?.stt?.ready ? ui('就绪') : ui('未找到')}
          {status?.stt?.label ? ` · ${status.stt.label}` : ''}
        </div>
        <div className="mt-3 font-semibold">{ui('朗读引擎')}</div>
        <div className="mt-1 text-[#7a7065]">
          {status?.tts?.piper?.ready ? ui('华严（Piper）已就绪') : ui('华严（Piper）未找到')}
        </div>
        <div className="mt-1 text-[#7a7065]">
          {status?.tts?.cosyvoice?.ready ? ui('Jarvis（CosyVoice）已就绪') : ui('Jarvis（CosyVoice）未运行')}
        </div>
        {(status?.hints?.length || statusError) && (
          <p className="mt-2 text-[#8a4b2b]">{statusError || status?.hints?.join('；')}</p>
        )}
        <label className="mt-3 block font-semibold">
          {ui('默认音色')}
          <select
            value={ttsEngine}
            onChange={(event) => onTtsEngine(event.target.value as 'auto' | 'piper' | 'cosyvoice')}
            className="mt-1 h-8 w-full rounded-md border border-[#ded6ca] bg-white px-2 font-normal"
          >
            <option value="auto">{ui('自动：有 Jarvis 就用 Jarvis')}</option>
            <option value="cosyvoice">{ui('Jarvis · CosyVoice 2')}</option>
            <option value="piper">{ui('华严 · Piper')}</option>
          </select>
        </label>
        {onStartCosyVoice && status?.tts?.cosyvoice?.startable && !status.tts.cosyvoice.ready && (
          <button
            type="button"
            disabled={startingCosy}
            onClick={() => {
              setStartingCosy(true);
              setStatusError('');
              void onStartCosyVoice()
                .then(() => refreshStatus())
                .catch((error) => setStatusError(error instanceof Error ? error.message : ui('无法启动自然音色')))
                .finally(() => setStartingCosy(false));
            }}
            className="mt-3 inline-flex h-8 items-center rounded-md bg-[#8d5f35] px-3 font-medium text-white disabled:opacity-60"
          >
            {startingCosy ? ui('正在加载自然音色…') : ui('启动 Jarvis 自然音色')}
          </button>
        )}
      </section>
      <label className="mb-4 block text-xs font-semibold text-[#7a7065]">
        {ui('说完后等待')}
        <input
          type="range"
          min={700}
          max={1600}
          step={100}
          value={silenceMs}
          onChange={(event) => onSilenceMs(Number(event.target.value))}
          className="mt-2 w-full"
          aria-valuetext={`${silenceMs}ms`}
        />
        <span className="mt-1 block font-normal">{ui('{ms} 毫秒静音后再发送，避免抢话。', { ms: silenceMs })}</span>
      </label>
      <div className="flex flex-wrap gap-2">
        <button
          type="button"
          onClick={() => { void probeMic(); }}
          className="inline-flex h-8 items-center gap-1 rounded-md border border-[#b8a999] px-3 text-xs"
        >
          <Mic size={13} />
          {ui('检测麦克风')}
        </button>
        <button
          type="button"
          onClick={onStartLive}
          className="inline-flex h-8 items-center rounded-md bg-[#2f2a24] px-3 text-xs font-medium text-white"
        >
          {ui('开始语音对话')}
        </button>
      </div>
      {deviceLabel && <p className="mt-3 text-xs text-[#6d6258]">{deviceLabel}</p>}
    </div>
  );
}
