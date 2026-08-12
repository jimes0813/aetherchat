import type { ModelConfig } from '../types';

export type LocalRuntimeStatus = {
  ready?: boolean;
  managed?: boolean;
  started?: boolean;
  message?: string;
  type?: string;
  base_url?: string | null;
  pid?: number | null;
  code?: string | null;
  hints?: string[] | null;
  log_path?: string | null;
  log_tail?: string | null;
};

export type LocalFailureGuide = {
  summary: string;
  steps: string[];
  logPath?: string;
  logTail?: string;
  code?: string;
};

export type LocalVerifyResult = {
  ok?: boolean;
  message?: string;
  sample?: string;
  model?: string;
  base_url?: string | null;
  runtime?: LocalRuntimeStatus | null;
};

export function isLocalManagedModel(model: ModelConfig): boolean {
  return model.type === 'local' && (model.local_runtime === 'llama_cpp' || model.local_runtime === 'external');
}

export function isLocalLlamaManaged(model: ModelConfig): boolean {
  return model.type === 'local' && model.local_runtime === 'llama_cpp';
}

const LOOPBACK_BASE_URL = /^https?:\/\/(127\.0\.0\.1|localhost|\[::1\])(:\d+)?(\/.*)?$/i;

export type LlamaManagedForm = {
  name: string;
  model: string;
  base_url: string;
  model_path: string;
  runtime_executable: string;
  runtime_args?: string;
};

export function validateLoopbackBaseUrl(baseUrl: string): string | null {
  const value = baseUrl.trim();
  if (!value) return '接口 Base URL 不能为空';
  if (!LOOPBACK_BASE_URL.test(value)) {
    return '自动启动仅支持本机回环地址（127.0.0.1 / localhost）';
  }
  try {
    const parsed = new URL(value);
    if (!parsed.port) return '本地模型接口地址必须包含端口，例如 http://127.0.0.1:8080/v1';
    if (parsed.protocol !== 'http:' && parsed.protocol !== 'https:') {
      return '接口地址必须是 http(s)';
    }
  } catch {
    return '接口 Base URL 格式无效';
  }
  return null;
}

export function validateLlamaManagedForm(form: LlamaManagedForm): string | null {
  if (!form.name.trim()) return '配置名称不能为空';
  if (!form.model.trim()) return '模型 ID 不能为空';
  const baseError = validateLoopbackBaseUrl(form.base_url);
  if (baseError) return baseError;
  const modelPath = form.model_path.trim();
  if (!modelPath) return '请填写 GGUF 模型绝对路径';
  if (!modelPath.endsWith('.gguf')) return '模型文件路径应以 .gguf 结尾';
  if (!modelPath.startsWith('/')) return '请使用绝对路径（以 / 开头）';
  const executable = form.runtime_executable.trim();
  if (!executable) return '请填写 llama-server 绝对路径';
  if (!executable.startsWith('/')) return 'llama-server 请使用绝对路径';
  if (!executable.endsWith('llama-server')) {
    return '本地运行程序文件名必须是 llama-server';
  }
  const args = (form.runtime_args || '')
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean);
  const blocked = new Set(['--host', '--port', '--model', '-m']);
  if (args.some((item) => blocked.has(item))) {
    return '附加参数不能覆盖 --model / --host / --port';
  }
  return null;
}

export function buildLlamaManagedPayload(form: LlamaManagedForm): Record<string, unknown> {
  const runtime_args = (form.runtime_args || '')
    .split('\n')
    .map((item) => item.trim())
    .filter(Boolean);
  return {
    name: form.name.trim(),
    type: 'local',
    model: form.model.trim(),
    base_url: form.base_url.trim(),
    api_key: 'local-model',
    persona: '本地中文推理模型',
    participation_policy: 'always',
    local_runtime: 'llama_cpp',
    model_path: form.model_path.trim(),
    runtime_executable: form.runtime_executable.trim(),
    runtime_args,
    startup_timeout: 600,
    max_tokens: 8192,
    token_limit: 128000,
  };
}

export function formatLocalRuntimeLabel(
  status: LocalRuntimeStatus | null | undefined,
  busy: '' | 'status' | 'start' | 'verify' = '',
): string {
  if (busy === 'start') return '正在启动本地服务…';
  if (busy === 'verify') return '正在验证真实对话…';
  if (busy === 'status') return '正在查询运行状态…';
  if (!status) return '尚未查询运行状态';
  if (status.ready) {
    if (status.started || status.managed) {
      return `本地服务已就绪${status.pid ? `（pid ${status.pid}）` : ''}`;
    }
    return '本地接口已就绪（外部服务）';
  }
  return status.message?.trim() || '本地服务未就绪';
}

/**
 * Turn raw start/verify/API errors into short summary + next steps for the UI.
 */
export function explainLocalModelFailure(
  raw: string,
  status?: LocalRuntimeStatus | null,
): LocalFailureGuide {
  const text = (raw || status?.message || '本地模型失败').trim();
  const lower = text.toLowerCase();
  const steps: string[] = [];
  let code = status?.code || undefined;
  let logPath = status?.log_path || undefined;
  let logTail = status?.log_tail || undefined;

  const logMatch = text.match(/(?:完整日志|日志)[：:]\s*(\S+\.log)/);
  if (logMatch) logPath = logPath || logMatch[1];
  const tailMatch = text.match(/日志摘录[：:]\s*(.+?)(?:。完整日志|。建议|$)/);
  if (tailMatch) logTail = logTail || tailMatch[1].trim();

  if (status?.hints?.length) {
    steps.push(...status.hints.filter(Boolean));
  }

  const suggest = (hint: string) => {
    if (!steps.includes(hint)) steps.push(hint);
  };

  if (/不存在|path_not_found|未配置|path_missing/i.test(text) || code === 'path_not_found' || code === 'path_missing') {
    code = code || 'path_not_found';
    suggest('在「模型」面板核对 GGUF 与 llama-server 绝对路径');
    suggest('路径需以 / 开头，且文件确实存在');
  } else if (/不可执行|not_executable|chmod/i.test(text) || code === 'not_executable') {
    code = code || 'not_executable';
    suggest('对 llama-server 执行 chmod +x');
  } else if (/端口已被占用|port_in_use|EADDRINUSE|占用/i.test(text) || code === 'port_in_use_incompatible') {
    code = code || 'port_in_use';
    suggest('换一个空闲端口，或停掉占用当前端口的进程');
    suggest('用 ss -ltnp 或 lsof -i :端口 定位占用者');
  } else if (/外部|external_not_running|未运行：127/i.test(text) || code === 'external_not_running') {
    code = code || 'external_not_running';
    suggest('先启动本机 OpenAI 兼容服务，再点「启动并验证」');
    suggest('或改为 llama.cpp 托管模式由应用自动拉起');
  } else if (/退出码|process_exited|启动失败/i.test(text) || code === 'process_exited') {
    code = code || 'process_exited';
    suggest('查看启动日志中的 CUDA / 模型加载错误');
    suggest('尝试降低 --n-gpu-layers 或上下文长度');
  } else if (/秒内未就绪|timeout|startup_timeout/i.test(text) || code === 'startup_timeout') {
    code = code || 'startup_timeout';
    suggest('大模型加载较慢时可提高 startup_timeout 后重试');
    suggest('确认 GPU 驱动可用；必要时减少 GPU 层数');
  } else if (/回环|non_loopback|localhost/i.test(text) || code === 'non_loopback_base_url') {
    code = code || 'non_loopback_base_url';
    suggest('Base URL 必须是 http://127.0.0.1:端口/v1');
  } else if (/真实对话|verify|检测未通过|检测失败/i.test(text)) {
    code = code || 'verify_failed';
    suggest('确认 /v1/models 与 chat completions 接口可用');
    suggest('检查模型 ID 是否与服务端暴露名称一致');
  } else if (/http_503|503/i.test(lower)) {
    suggest('后端返回启动失败，请阅读下方摘要中的建议与日志摘录');
  }

  if (logPath) suggest(`完整日志：${logPath}`);
  if (!steps.length) {
    suggest('打开「模型」面板检查路径、端口与 runtime 类型后重试');
  }

  // Prefer the first sentence as summary for long as_detail strings.
  const summary = text.split('。建议：')[0].split('。完整日志')[0].slice(0, 280);

  return { summary, steps, logPath, logTail, code };
}

export function localModelReadyForChat(model: ModelConfig, status?: LocalRuntimeStatus | null): boolean {
  if (model.type !== 'local') return model.has_api_key;
  if (status?.ready) return true;
  // external runtime may already be up without a status probe yet
  return model.local_runtime === 'external' && Boolean(model.base_url);
}

type JsonFetcher = <T>(url: string, init?: RequestInit) => Promise<T>;

export async function fetchLocalRuntimeStatus(
  apiBase: string,
  name: string,
  fetchJson: JsonFetcher,
): Promise<LocalRuntimeStatus> {
  return fetchJson<LocalRuntimeStatus>(
    `${apiBase}/models/${encodeURIComponent(name)}/runtime`,
  );
}

export async function startLocalModel(
  apiBase: string,
  name: string,
  fetchJson: JsonFetcher,
): Promise<LocalRuntimeStatus> {
  return fetchJson<LocalRuntimeStatus>(
    `${apiBase}/models/${encodeURIComponent(name)}/start`,
    { method: 'POST' },
  );
}

export async function verifyLocalModel(
  apiBase: string,
  name: string,
  fetchJson: JsonFetcher,
): Promise<LocalVerifyResult> {
  return fetchJson<LocalVerifyResult>(
    `${apiBase}/models/${encodeURIComponent(name)}/verify`,
    { method: 'POST' },
  );
}

export type StartVerifyResult = {
  ok: boolean;
  message: string;
  runtime?: LocalRuntimeStatus | null;
  sample?: string;
  guide?: LocalFailureGuide;
};

/**
 * Preferred local path: start (if managed) → verify real chat → return combined status text.
 */
export async function startAndVerifyLocalModel(
  apiBase: string,
  model: ModelConfig,
  fetchJson: JsonFetcher,
  onPhase?: (phase: 'start' | 'verify') => void,
): Promise<StartVerifyResult> {
  let runtime: LocalRuntimeStatus | null = null;
  try {
    if (isLocalLlamaManaged(model)) {
      onPhase?.('start');
      runtime = await startLocalModel(apiBase, model.name, fetchJson);
      if (!runtime.ready) {
        const message = runtime.message || '本地服务启动后仍未就绪';
        return {
          ok: false,
          message,
          runtime,
          guide: explainLocalModelFailure(message, runtime),
        };
      }
    } else if (model.type === 'local') {
      onPhase?.('start');
      runtime = await fetchLocalRuntimeStatus(apiBase, model.name, fetchJson);
      if (!runtime.ready && model.local_runtime === 'external') {
        // Still attempt verify so users get chat-level errors; keep status for UI.
      }
    }

    onPhase?.('verify');
    const verified = await verifyLocalModel(apiBase, model.name, fetchJson);
    const sample = verified.sample ? `，样本：${verified.sample.slice(0, 80)}` : '';
    if (!verified.ok) {
      const message = `${verified.message || '真实对话检测未通过'}${sample}`;
      const combinedRuntime = (verified.runtime || runtime) as LocalRuntimeStatus | null;
      return {
        ok: false,
        message,
        runtime: combinedRuntime,
        sample: verified.sample,
        guide: explainLocalModelFailure(message, combinedRuntime),
      };
    }
    return {
      ok: true,
      message: `${verified.message || '验证通过'}${sample}`,
      runtime: verified.runtime || runtime,
      sample: verified.sample,
    };
  } catch (error) {
    const message = error instanceof Error ? error.message : String(error);
    return {
      ok: false,
      message,
      runtime,
      guide: explainLocalModelFailure(message, runtime),
    };
  }
}
