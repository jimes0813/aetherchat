import { useMemo, useState } from 'react';
import { fetchJson } from '../api/client';
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

export const DEFAULT_LLAMA_SERVER = '/home/jimes0813/llama.cpp-cuda/build/bin/llama-server';
export const DEFAULT_GGUF_PATH =
  '/home/jimes0813/Qwen/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf';
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
    name: 'Qwen',
    type: 'local',
    model: 'Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf',
    base_url: DEFAULT_LOCAL_BASE_URL,
    persona: '本地中文推理模型',
    local_runtime: 'llama_cpp',
    requires_api_key: false,
    model_path: DEFAULT_GGUF_PATH,
    runtime_executable: DEFAULT_LLAMA_SERVER,
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

type Step = 1 | 2 | 3;

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
  const [step, setStep] = useState<Step>(1);
  const [presetId, setPresetId] = useState(PRESETS[0].id);
  const [apiKey, setApiKey] = useState('');
  const [modelId, setModelId] = useState(PRESETS[0].model);
  const [baseUrl, setBaseUrl] = useState(PRESETS[0].base_url);
  const [name, setName] = useState(PRESETS[0].name);
  const [modelPath, setModelPath] = useState(DEFAULT_GGUF_PATH);
  const [runtimeExecutable, setRuntimeExecutable] = useState(DEFAULT_LLAMA_SERVER);
  const [runtimeArgs, setRuntimeArgs] = useState(
    PRESETS.find((item) => item.id === 'local-llama')?.runtime_args || '',
  );
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [verifySample, setVerifySample] = useState('');
  const [statusLine, setStatusLine] = useState('');

  const preset = useMemo(
    () => PRESETS.find((item) => item.id === presetId) || PRESETS[0],
    [presetId],
  );
  const configuredCount = models.filter(isConfiguredModel).length;
  const isLocal = preset.type === 'local';
  const isLlamaManaged = isLocal && preset.local_runtime === 'llama_cpp';

  const applyPreset = (id: string) => {
    const next = PRESETS.find((item) => item.id === id) || PRESETS[0];
    setPresetId(next.id);
    setName(next.name);
    setModelId(next.model);
    setBaseUrl(next.base_url);
    setModelPath(next.model_path || DEFAULT_GGUF_PATH);
    setRuntimeExecutable(next.runtime_executable || DEFAULT_LLAMA_SERVER);
    setRuntimeArgs(next.runtime_args || '');
    setError('');
    setVerifySample('');
    setStatusLine('');
  };

  const saveAndVerify = async () => {
    if (preset.requires_api_key && !apiKey.trim()) {
      setError('请先粘贴 API 密钥');
      return;
    }
    if (!name.trim() || !modelId.trim() || !baseUrl.trim()) {
      setError('模型名称、模型 ID 和接口地址不能为空');
      return;
    }
    if (isLlamaManaged) {
      const formError = validateLlamaManagedForm({
        name,
        model: modelId,
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
          model: modelId,
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

  return (
    <div
      className="aether-modal-backdrop"
      role="dialog"
      aria-modal="true"
      aria-labelledby="first-run-title"
    >
      <section className="mx-auto mt-[6vh] max-h-[88vh] w-full max-w-xl overflow-auto rounded-2xl border border-[#d8cec4] bg-white p-6 text-[#2f2925] shadow-lg">
        <p className="text-xs font-semibold uppercase tracking-[0.16em] text-[#7a695d]">
          首次设置 · {step}/3
        </p>
        <h2 id="first-run-title" className="mt-2 text-xl font-semibold">
          {step === 1 && '欢迎使用 AetherChat'}
          {step === 2 && '配置一个可对话的模型'}
          {step === 3 && '已就绪，可以开始'}
        </h2>

        {step === 1 && (
          <div className="mt-4 space-y-3 text-sm leading-6 text-[#5f554d]">
            <p>三步完成主路径：选择模型 → 保存并验证 → 开始聊天。</p>
            <ol className="list-decimal space-y-1 pl-5">
              <li>云端：Kimi / DeepSeek</li>
              <li>本机：llama.cpp 自动托管，或已运行的兼容服务</li>
              <li>自动做真实连通测试，通过后即可发消息</li>
            </ol>
            <p className="text-xs text-[#6d6259]">
              当前已配置模型：{configuredCount} 个。托管模式只需填 GGUF 与 llama-server 路径。
            </p>
          </div>
        )}

        {step === 2 && (
          <div className="mt-4 space-y-3 text-sm">
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-[#6d6259]">预设</span>
              <select
                className="w-full rounded-lg border border-[#d8cec4] bg-[#fbfaf7] px-3 py-2"
                value={presetId}
                onChange={(event) => applyPreset(event.target.value)}
              >
                {PRESETS.map((item) => (
                  <option key={item.id} value={item.id}>{item.label}</option>
                ))}
              </select>
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-[#6d6259]">配置名称</span>
              <input
                className="w-full rounded-lg border border-[#d8cec4] bg-[#fbfaf7] px-3 py-2"
                value={name}
                onChange={(event) => setName(event.target.value)}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-[#6d6259]">模型 ID</span>
              <input
                className="w-full rounded-lg border border-[#d8cec4] bg-[#fbfaf7] px-3 py-2"
                value={modelId}
                onChange={(event) => setModelId(event.target.value)}
                placeholder={isLocal ? '可与 GGUF 文件名一致' : undefined}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs font-medium text-[#6d6259]">接口 Base URL</span>
              <input
                className="w-full rounded-lg border border-[#d8cec4] bg-[#fbfaf7] px-3 py-2"
                value={baseUrl}
                onChange={(event) => setBaseUrl(event.target.value)}
              />
            </label>

            {preset.requires_api_key && (
              <label className="block">
                <span className="mb-1 block text-xs font-medium text-[#6d6259]">API 密钥</span>
                <input
                  type="password"
                  autoComplete="off"
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
                  llama.cpp 托管：AetherChat 会启动 <code>llama-server</code>，再做真实对话验证。路径需为本机绝对路径。
                </p>
                <label className="block">
                  <span className="mb-1 block text-xs font-medium text-[#6d6259]">GGUF 模型路径</span>
                  <input
                    className="w-full rounded-lg border border-[#d8cec4] bg-white px-3 py-2 font-mono text-xs"
                    value={modelPath}
                    onChange={(event) => setModelPath(event.target.value)}
                    placeholder="/path/to/model.gguf"
                  />
                </label>
                <label className="block">
                  <span className="mb-1 block text-xs font-medium text-[#6d6259]">llama-server 路径</span>
                  <input
                    className="w-full rounded-lg border border-[#d8cec4] bg-white px-3 py-2 font-mono text-xs"
                    value={runtimeExecutable}
                    onChange={(event) => setRuntimeExecutable(event.target.value)}
                    placeholder="/path/to/llama-server"
                  />
                </label>
                <label className="block">
                  <span className="mb-1 block text-xs font-medium text-[#6d6259]">启动参数（每行一个，可选）</span>
                  <textarea
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
                外部本地服务模式：请先自行启动兼容服务，再点保存并验证。
              </p>
            )}

            {statusLine && (
              <p className="text-xs text-[#6d6258]">{statusLine}</p>
            )}
          </div>
        )}

        {step === 3 && (
          <div className="mt-4 space-y-3 text-sm leading-6 text-[#5f554d]">
            <p>
              连通测试通过。可直接发第一条消息；本地托管模型也可随时在「模型」面板使用「启动并验证」重新拉起。
            </p>
            {verifySample && (
              <p className="rounded-lg bg-[#eef6ea] px-3 py-2 text-xs text-[#326022]">
                样本回复：{verifySample}
              </p>
            )}
            {statusLine && (
              <p className="text-xs text-[#6d6258]">{statusLine}</p>
            )}
          </div>
        )}

        {error && (
          <p role="alert" className="mt-4 rounded-lg border border-[#e2b8b0] bg-[#fff5f2] px-3 py-2 text-xs text-[#8a4036]">
            {error}
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
                稍后再说
              </button>
              <button
                type="button"
                className="rounded-lg bg-[#8d5f35] px-3 py-2 text-sm text-white"
                onClick={() => setStep(2)}
              >
                开始配置
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
                上一步
              </button>
              <button
                type="button"
                className="rounded-lg bg-[#8d5f35] px-3 py-2 text-sm text-white disabled:opacity-60"
                onClick={() => void saveAndVerify()}
                disabled={busy}
              >
                {busy ? '正在保存并验证…' : isLlamaManaged ? '保存并启动验证' : '保存并验证'}
              </button>
            </>
          )}
          {step === 3 && (
            <button
              type="button"
              className="rounded-lg bg-[#326022] px-3 py-2 text-sm text-white"
              onClick={finish}
            >
              进入工作台
            </button>
          )}
        </div>
      </section>
    </div>
  );
}
