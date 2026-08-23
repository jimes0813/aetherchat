import { useMemo, useState } from 'react';
import { useDialogFocus } from './useDialogFocus';
import { fetchJson } from '../api/client';
import { useI18n } from '../i18n';
import type { ModelConfig } from '../types';
import {
  buildLlamaManagedPayload,
  explainLocalModelFailure,
  startAndVerifyLocalModel,
  validateLlamaManagedForm,
  validateLoopbackBaseUrl,
} from './localModelRuntime';
import { API_BASE, isConfiguredModel } from './shared';

export const FIRST_RUN_DISMISS_KEY = 'aetherchat.first_run.dismissed.v1';

export const DEFAULT_LLAMA_SERVER = '';
export const DEFAULT_GGUF_PATH = '';
export const DEFAULT_LOCAL_BASE_URL = 'http://127.0.0.1:8080/v1';

export type FirstRunWizardProps = {
  models: ModelConfig[];
  onConfigured: (model: ModelConfig) => void;
  onDismiss: () => void;
  apiBase?: string;
};

type Preset = {
  id: string;
  label: string;
  name: string;
  type: 'openai_compat' | 'local';
  model: string;
  base_url: string;
  persona: string;
  local_runtime?: 'external' | 'llama_cpp';
  requires_api_key: boolean;
  model_path?: string;
  runtime_executable?: string;
  runtime_args?: string;
};

const PRESETS: Preset[] = [
  {
    id: 'kimi',
    label: 'Kimi K3（Moonshot 国区）',
    name: 'kimi',
    type: 'openai_compat',
    model: 'kimi-k3',
    base_url: 'https://api.moonshot.cn/v1',
    persona: 'Researcher',
    requires_api_key: true,
  },
  {
    id: 'deepseek',
    label: 'DeepSeek',
    name: 'deepseek',
    type: 'openai_compat',
    model: 'deepseek-chat',
    base_url: 'https://api.deepseek.com',
    persona: 'Coder/Reasoner',
    requires_api_key: true,
  },
  {
    id: 'local-llama',
    label: '本机 llama.cpp 自动托管（推荐）',
    name: 'local-llama',
    type: 'local',
    model: '',
    base_url: DEFAULT_LOCAL_BASE_URL,
    persona: '本地中文推理模型',
    local_runtime: 'llama_cpp',
    requires_api_key: false,
    model_path: '',
    runtime_executable: '',
    runtime_args: '--n-gpu-layers\n-1\n--ctx-size\n8192\n--flash-attn\non',
  },
  {
    id: 'local-external',
    label: '本机已运行的 OpenAI 兼容服务',
    name: 'local-openai',
    type: 'local',
    model: 'local-model',
    base_url: DEFAULT_LOCAL_BASE_URL,
    persona: '本地中文推理模型',
    local_runtime: 'external',
    requires_api_key: false,
  },
  {
    id: 'custom',
    label: '自定义 OpenAI 兼容接口',
    name: 'custom-openai',
    type: 'openai_compat',
    model: 'gpt-4o-mini',
    base_url: 'https://api.openai.com/v1',
    persona: 'Assistant',
    requires_api_key: true,
  },
];

const PROVIDER_PRIVACY_URLS: Record<string, string> = {
  kimi: 'https://platform.kimi.com/docs/agreement/userprivacy',
  deepseek: 'https://cdn.deepseek.com/policies/zh-CN/deepseek-privacy-policy.html',
};

type Step = 1 | 2 | 3;
type SetupPath = 'local-managed' | 'local-service' | 'cloud';

export function shouldShowFirstRunWizard(
  models: ModelConfig[],
  storage: Pick<Storage, 'getItem'> = globalThis.localStorage,
): boolean {
  if (typeof storage?.getItem !== 'function') return false;
  if (storage.getItem(FIRST_RUN_DISMISS_KEY) === '1') return false;
  return !models.some(isConfiguredModel);
}

export function dismissFirstRunWizard(
  storage: Pick<Storage, 'setItem'> = globalThis.localStorage,
): void {
  storage.setItem(FIRST_RUN_DISMISS_KEY, '1');
}

export function FirstRunWizard({
  models,
  onConfigured,
  onDismiss,
  apiBase = API_BASE,
}: FirstRunWizardProps) {
  const { t, ui } = useI18n();
  const [step, setStep] = useState<Step>(1);
  const [setupPath, setSetupPath] = useState<SetupPath>('local-managed');
  const [presetId, setPresetId] = useState('local-llama');
  const [apiKey, setApiKey] = useState('');
  const [modelId, setModelId] = useState('');
  const [baseUrl, setBaseUrl] = useState(DEFAULT_LOCAL_BASE_URL);
  const [name, setName] = useState('local-llama');
  const [modelPath, setModelPath] = useState(DEFAULT_GGUF_PATH);
  const [runtimeExecutable, setRuntimeExecutable] = useState(DEFAULT_LLAMA_SERVER);
  const [runtimeArgs, setRuntimeArgs] = useState(
    PRESETS.find((item) => item.id === 'local-llama')?.runtime_args || '',
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [verifySample, setVerifySample] = useState('');
  const [statusLine, setStatusLine] = useState('');
  const feedbackIds = [statusLine && 'first-run-status', error && 'first-run-error']
    .filter(Boolean)
    .join(' ') || undefined;

  const preset = useMemo(
    () => PRESETS.find((item) => item.id === presetId) || PRESETS[0],
    [presetId],
  );
  const configuredCount = models.filter(isConfiguredModel).length;
  const isLocal = preset.type === 'local';
  const isLlamaManaged = isLocal && preset.local_runtime === 'llama_cpp';
  const visiblePresets = PRESETS.filter((item) => (
    setupPath === 'local-managed'
      ? item.id === 'local-llama'
      : setupPath === 'local-service'
        ? item.id === 'local-external'
        : item.type !== 'local'
  ));

  const applyPreset = (id: string) => {
    const next = PRESETS.find((item) => item.id === id) || PRESETS[0];
    setPresetId(next.id);
    setName(next.name);
    setModelId(next.model);
    setBaseUrl(next.base_url);
    setModelPath(next.model_path || '');
    setRuntimeExecutable(next.runtime_executable || '');
    setRuntimeArgs(next.runtime_args || '');
    setError('');
    setVerifySample('');
    setStatusLine('');
  };

  const continueFromPath = () => {
    applyPreset(
      setupPath === 'local-managed'
        ? 'local-llama'
        : setupPath === 'local-service'
          ? 'local-external'
          : 'kimi',
    );
    setStep(2);
  };

  const selectLocalPath = async (kind: 'gguf' | 'runtime') => {
    const selected = await window.aetherRuntime?.selectLocalModelPath?.(kind);
    if (!selected) return;
    if (kind === 'gguf') setModelPath(selected);
    else setRuntimeExecutable(selected);
  };

  const openProviderPrivacyPolicy = () => {
    const url = PROVIDER_PRIVACY_URLS[preset.id];
    if (!url) return;
    if (window.aetherRuntime?.openExternal) {
      void window.aetherRuntime.openExternal(url);
      return;
    }
    window.open(url, '_blank', 'noopener,noreferrer');
  };

  const saveAndVerify = async () => {
    if (preset.requires_api_key && !apiKey.trim()) {
      setError('请先粘贴 API 密钥');
      return;
    }
    if (!name.trim() || !baseUrl.trim() || (!isLlamaManaged && !modelId.trim())) {
      setError('模型名称、模型 ID 和接口地址不能为空');
      return;
    }
    const ggufName = modelPath.trim().split(/[/\\]/).pop() || '';
    const resolvedModel = modelId.trim() || ggufName.replace(/\.gguf$/i, '');
    if (isLlamaManaged) {
      const formError = validateLlamaManagedForm({
        name,
        model: resolvedModel,
        base_url: baseUrl,
        model_path: modelPath,
        runtime_executable: runtimeExecutable,
        runtime_args: runtimeArgs,
      });
      if (formError) {
        setError(formError);
        return;
      }
    } else if (isLocal) {
      const baseError = validateLoopbackBaseUrl(baseUrl);
      if (baseError) {
        setError(baseError);
        return;
      }
    }

    setBusy(true);
    setError('');
    setVerifySample('');
    setStatusLine(isLocal ? '正在保存本地模型配置…' : '正在保存云端模型配置…');
    try {
      let payload: Record<string, unknown>;
      if (isLlamaManaged) {
        payload = buildLlamaManagedPayload({
          name,
          model: resolvedModel,
          base_url: baseUrl,
          model_path: modelPath,
          runtime_executable: runtimeExecutable,
          runtime_args: runtimeArgs,
        });
      } else {
        payload = {
          name: name.trim(),
          type: preset.type,
          model: modelId.trim(),
          base_url: baseUrl.trim(),
          persona: preset.persona,
          participation_policy: 'always',
        };
        if (preset.requires_api_key) {
          payload.api_key = apiKey.trim();
        }
        if (isLocal) {
          payload.local_runtime = preset.local_runtime || 'external';
          payload.api_key = apiKey.trim() || 'local-model';
        }
      }

      const created = await fetchJson<ModelConfig>(`${apiBase}/models/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      });
      onConfigured(created);

      if (created.type === 'local') {
        const result = await startAndVerifyLocalModel(
          apiBase,
          created,
          fetchJson,
          (phase) => setStatusLine(
            phase === 'start'
              ? (isLlamaManaged ? '正在启动 llama-server…' : '正在检查本地服务…')
              : '正在验证真实对话…',
          ),
        );
        if (!result.ok) {
          const guide = result.guide || explainLocalModelFailure(result.message, result.runtime);
          const steps = guide.steps.length ? `\n建议：${guide.steps.join('；')}` : '';
          const tail = guide.logTail ? `\n日志：${guide.logTail}` : '';
          setError(`${guide.summary}${steps}${tail}`);
          setStep(2);
          return;
        }
        setVerifySample((result.sample || 'OK').slice(0, 80));
        setStatusLine(result.message);
        setStep(3);
        return;
      }

      setStatusLine('正在验证云端连通…');
      const verified = await fetchJson<{ ok?: boolean; message?: string; sample?: string }>(
        `${apiBase}/models/${encodeURIComponent(created.name)}/verify`,
        { method: 'POST' },
      );
      if (!verified?.ok) {
        setError(verified?.message || '连通测试未通过，请检查密钥与接口地址');
        setStep(2);
        return;
      }
      setVerifySample((verified.sample || 'OK').slice(0, 80));
      setStatusLine(verified.message || '验证通过');
      setStep(3);
    } catch (err) {
      setError(err instanceof Error ? err.message : String(err));
    } finally {
      setBusy(false);
    }
  };

  const finish = () => {
    dismissFirstRunWizard();
    onDismiss();
  };
  const dialogFocus = useDialogFocus<HTMLDivElement>(true, finish);

  return (
    <div
      ref={dialogFocus.containerRef}
      tabIndex={-1}
      onKeyDown={dialogFocus.onKeyDown}
      className="aether-modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="first-run-title"
    >
      <section className="mx-auto mt-[6vh] max-h-[88vh] w-full max-w-xl overflow-auto rounded-2xl border border-[#d8cec4] bg-white p-6 text-[#2f2925] shadow-lg">
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[#7a695d]">
          {t('firstTimeSetup3', { step })}
        </p>
        <h2 id="first-run-title" className="mt-2 text-xl font-semibold">
          {step === 1 && t('welcomeToAetherChat')}
          {step === 2 && t('configureAChatModel')}
          {step === 3 && t('readyToBegin')}
        </h2>

        {step === 1 && (
          <div className="mt-4 space-y-3 text-sm leading-6 text-[#5f554d]">
            <p>{t('chooseModelLocation')}</p>
            <div role="radiogroup" aria-label={t('modelLocation')} className="grid gap-2">
              {([
                ['local-managed', t('fullyLocalManaged'), t('fullyLocalManagedDetail')],
                ['local-service', t('existingLocalService'), t('existingLocalServiceDetail')],
                ['cloud', t('cloudApi'), t('cloudApiDetail')],
              ] as Array<[SetupPath, string, string]>).map(([id, label, detail]) => (
                <button
                  key={id}
                  type="button"
                  role="radio"
                  aria-checked={setupPath === id}
                  onClick={() => setSetupPath(id)}
                  className={`rounded-xl border p-3 text-left ${setupPath === id ? 'border-[#8d5f35] bg-[#fff8f0]' : 'border-[#d8cec4] bg-[#fbfaf7]'}`}
                >
                  <span className="block font-semibold text-[#3f352d]">{label}</span>
                  <span className="mt-1 block text-xs leading-5 text-[#6d6259]">{detail}</span>
                </button>
              ))}
            </div>
            <p className="text-xs text-[#6d6259]">
              {t('modelsAreConfiguredManagedMode', { count: configuredCount })}
            </p>
          </div>
        )}

        {step === 2 && (
          <div className="mt-4 space-y-3 text-sm">
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-[#6d6259]">{t('preset')}</span>
              <select
                className="w-full rounded-lg border border-[#d8cec4] bg-[#fbfaf7] px-3 py-2"
                value={presetId}
                onChange={(event) => applyPreset(event.target.value)}
              >
                {visiblePresets.map((item) => (
                  <option key={item.id} value={item.id}>{ui(item.label)}</option>
                ))}
              </select>
            </label>
            {!isLocal && (
              <div role="note" className="rounded-xl border border-[#e3c596] bg-[#fff8e8] p-3 text-xs leading-5 text-[#6d4c1f]">
                {t('cloudDataDisclosure')}
                {PROVIDER_PRIVACY_URLS[preset.id] && (
                  <button type="button" onClick={openProviderPrivacyPolicy} className="mt-2 block font-semibold underline underline-offset-2">
                    {t('readProviderPrivacyPolicy')}
                  </button>
                )}
              </div>
            )}
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-[#6d6259]">{t('configurationName')}</span>
              <input
                aria-describedby={feedbackIds}
                className="w-full rounded-lg border border-[#d8cec4] bg-[#fbfaf7] px-3 py-2"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-[#6d6259]">{t('modelID')}</span>
              <input
                aria-describedby={feedbackIds}
                className="w-full rounded-lg border border-[#d8cec4] bg-[#fbfaf7] px-3 py-2"
                value={modelId}
                onChange={(event) => setModelId(event.target.value)}
                placeholder={isLocal ? t('mayMatchTheGGUFFile') : undefined}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-[#6d6259]">{t('endpointBaseURL')}</span>
              <input
                aria-describedby={feedbackIds}
                className="w-full rounded-lg border border-[#d8cec4] bg-[#fbfaf7] px-3 py-2"
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
              />
            </label>

            {preset.requires_api_key && (
              <label className="block">
                <span className="mb-1 block text-xs font-medium text-[#6d6259]">{t('apiKey')}</span>
                <input
                  aria-describedby={feedbackIds}
                  type="password"
                  autoComplete="new-password"
                  className="w-full rounded-lg border border-[#d8cec4] bg-[#fbfaf7] px-3 py-2"
                  value={apiKey}
                  onChange={(event) => setApiKey(event.target.value)}
                  placeholder="sk-..."
                />
              </label>
            )}

            {isLlamaManaged && (
              <div className="space-y-3 rounded-xl border border-[#dcebd7] bg-[#f4faf1] p-3">
                <p className="text-xs leading-5 text-[#326022]">
                  {t('managedLlamaCppAetherChatStarts')}<code>llama-server</code>{t('thenRunsARealChat')}
                </p>
                <label className="block">
                  <span className="mb-1 block text-xs font-medium text-[#6d6259]">{t('ggufModelPath')}</span>
                  <div className="flex gap-2">
                    <input
                      aria-describedby={feedbackIds}
                      className="min-w-0 flex-1 rounded-lg border border-[#d8cec4] bg-white px-3 py-2 font-mono text-xs"
                      value={modelPath}
                      onChange={(event) => setModelPath(event.target.value)}
                      placeholder="/path/to/model.gguf"
                    />
                    {window.aetherRuntime?.selectLocalModelPath && <button type="button" onClick={() => void selectLocalPath('gguf')} className="rounded-lg border border-[#bfa98f] bg-white px-3 text-xs">{t('browse')}</button>}
                  </div>
                </label>
                <label className="block">
                  <span className="mb-1 block text-xs font-medium text-[#6d6259]">{t('llamaServerPath')}</span>
                  <div className="flex gap-2">
                    <input
                      aria-describedby={feedbackIds}
                      className="min-w-0 flex-1 rounded-lg border border-[#d8cec4] bg-white px-3 py-2 font-mono text-xs"
                      value={runtimeExecutable}
                      onChange={(event) => setRuntimeExecutable(event.target.value)}
                      placeholder="/path/to/llama-server"
                    />
                    {window.aetherRuntime?.selectLocalModelPath && <button type="button" onClick={() => void selectLocalPath('runtime')} className="rounded-lg border border-[#bfa98f] bg-white px-3 text-xs">{t('browse')}</button>}
                  </div>
                </label>
                <label className="block">
                  <span className="mb-1 block text-xs font-medium text-[#6d6259]">{t('startupArgumentsOnePerLine')}</span>
                  <textarea
                    aria-describedby={feedbackIds}
                    className="min-h-20 w-full rounded-lg border border-[#d8cec4] bg-white px-3 py-2 font-mono text-xs"
                    value={runtimeArgs}
                    onChange={(event) => setRuntimeArgs(event.target.value)}
                    placeholder={'--n-gpu-layers\n-1'}
                  />
                </label>
              </div>
            )}

            {isLocal && !isLlamaManaged && (
              <p className="rounded-lg bg-[#eef6ea] px-3 py-2 text-xs leading-5 text-[#326022]">
                {t('externalLocalServiceModeStart')}
              </p>
            )}

            {statusLine && (
              <p id="first-run-status" role="status" aria-live="polite" className="text-xs text-[#6d6258]">{statusLine}</p>
            )}
          </div>
        )}

        {step === 3 && (
          <div className="mt-4 space-y-3 text-sm leading-6 text-[#5f554d]">
            <p>
              {t('theConnectivityCheckPassedSend')}
            </p>
            {verifySample && (
              <p className="rounded-lg bg-[#eef6ea] px-3 py-2 text-xs text-[#326022]">
                {t('sampleResponse', { sample: verifySample })}
              </p>
            )}
            {statusLine && (
              <p id="first-run-status" role="status" aria-live="polite" className="text-xs text-[#6d6258]">{statusLine}</p>
            )}
          </div>
        )}

        {error && (
          <p id="first-run-error" role="alert" className="mt-4 rounded-lg border border-[#e2b8b0] bg-[#fff5f2] px-3 py-2 text-xs text-[#8a4036]">
            {ui(error)}
          </p>
        )}

        <div className="mt-6 flex flex-wrap justify-end gap-2">
          {step === 1 && (
            <>
              <button
                type="button"
                className="rounded-lg border border-[#cfc4b7] px-3 py-2 text-sm text-[#6d6259]"
                onClick={finish}
              >
                {t('notNow')}
              </button>
              <button
                type="button"
                data-dialog-initial-focus
                className="rounded-lg bg-[#8d5f35] px-3 py-2 text-sm text-white"
                onClick={continueFromPath}
              >
                {t('startSetup')}
              </button>
            </>
          )}
          {step === 2 && (
            <>
              <button
                type="button"
                className="rounded-lg border border-[#cfc4b7] px-3 py-2 text-sm text-[#6d6259]"
                onClick={() => setStep(1)}
                disabled={busy}
              >
                {t('back')}
              </button>
              <button
                type="button"
                className="rounded-lg bg-[#8d5f35] px-3 py-2 text-sm text-white disabled:opacity-60"
                onClick={() => void saveAndVerify()}
                disabled={busy}
              >
                {busy ? t('savingAndVerifying') : isLlamaManaged ? t('saveStartAndVerify') : t('saveAndVerify')}
              </button>
            </>
          )}
          {step === 3 && (
            <button
              type="button"
              className="rounded-lg bg-[#326022] px-3 py-2 text-sm text-white"
              onClick={finish}
            >
              {t('enterWorkspace')}
            </button>
          )}
        </div>
      </section>
    </div>
  );
}
