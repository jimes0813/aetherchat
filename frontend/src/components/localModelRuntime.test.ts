import { describe, expect, it, vi } from 'vitest';
import type { ModelConfig } from '../types';
import {
  buildLlamaManagedPayload,
  explainLocalModelFailure,
  formatLocalRuntimeLabel,
  isLocalLlamaManaged,
  localModelReadyForChat,
  startAndVerifyLocalModel,
  validateLlamaManagedForm,
  validateLoopbackBaseUrl,
} from './localModelRuntime';

const localLlama: ModelConfig = {
  name: 'Qwen',
  type: 'local',
  model: 'qwen.gguf',
  base_url: 'http://127.0.0.1:8080/v1',
  api_key_ref: 'MODEL_KEY_QWEN',
  has_api_key: true,
  max_tokens: 8192,
  participation_policy: 'always',
  token_limit: 128000,
  local_runtime: 'llama_cpp',
  model_path: '/models/qwen.gguf',
  runtime_executable: '/bin/llama-server',
  created_at: '2026-08-12T00:00:00Z',
  updated_at: '2026-08-12T00:00:00Z',
};

describe('localModelRuntime helpers', () => {
  it('detects llama managed local models', () => {
    expect(isLocalLlamaManaged(localLlama)).toBe(true);
    expect(isLocalLlamaManaged({ ...localLlama, local_runtime: 'external' })).toBe(false);
  });

  it('formats runtime labels for busy and ready states', () => {
    expect(formatLocalRuntimeLabel(null, 'start')).toContain('启动');
    expect(formatLocalRuntimeLabel({ ready: true, started: true, pid: 12 }, '')).toContain('pid 12');
    expect(formatLocalRuntimeLabel({ ready: false, message: '未监听' }, '')).toBe('未监听');
  });

  it('treats ready local runtime as chat-ready', () => {
    expect(localModelReadyForChat(localLlama, { ready: true })).toBe(true);
    expect(localModelReadyForChat(localLlama, { ready: false })).toBe(false);
  });

  it('starts then verifies llama managed models', async () => {
    const fetchJson = vi.fn(async (url: string, init?: RequestInit) => {
      if (String(url).includes('/start') && init?.method === 'POST') {
        return { ready: true, managed: true, started: true, pid: 99 };
      }
      if (String(url).includes('/verify') && init?.method === 'POST') {
        return { ok: true, message: '真实对话检测通过', sample: 'OK' };
      }
      throw new Error(`unexpected ${url}`);
    });
    const phases: string[] = [];
    const result = await startAndVerifyLocalModel(
      'http://127.0.0.1:8000',
      localLlama,
      fetchJson as never,
      (phase) => phases.push(phase),
    );
    expect(phases).toEqual(['start', 'verify']);
    expect(result.ok).toBe(true);
    expect(result.message).toContain('真实对话检测通过');
    expect(result.runtime?.pid).toBe(99);
  });

  it('stops early when start does not become ready', async () => {
    const fetchJson = vi.fn(async (url: string) => {
      if (String(url).includes('/start')) {
        return { ready: false, message: '端口占用' };
      }
      throw new Error('verify should not run');
    });
    const result = await startAndVerifyLocalModel(
      'http://127.0.0.1:8000',
      localLlama,
      fetchJson as never,
    );
    expect(result.ok).toBe(false);
    expect(result.message).toContain('端口占用');
    expect(fetchJson).toHaveBeenCalledTimes(1);
  });

  it('explains path and port failures with next steps', () => {
    const pathGuide = explainLocalModelFailure('模型文件不存在：/missing/model.gguf');
    expect(pathGuide.steps.some((step) => step.includes('绝对路径') || step.includes('路径'))).toBe(true);

    const portGuide = explainLocalModelFailure(
      '本地模型端口已被占用，但 OpenAI 兼容接口未就绪：http://127.0.0.1:8080/v1/models',
    );
    expect(portGuide.steps.some((step) => /端口|ss |lsof/.test(step))).toBe(true);

    const withStatus = explainLocalModelFailure('启动失败', {
      ready: false,
      code: 'process_exited',
      log_path: '/tmp/qwen.log',
      log_tail: 'CUDA out of memory',
      hints: ['降低 n-gpu-layers'],
    });
    expect(withStatus.logTail).toContain('CUDA');
    expect(withStatus.steps).toContain('降低 n-gpu-layers');
    expect(withStatus.steps.some((step) => step.includes('/tmp/qwen.log'))).toBe(true);
  });

  it('returns guide when start API throws', async () => {
    const fetchJson = vi.fn(async (url: string) => {
      if (String(url).includes('/start')) {
        throw new Error('本地模型进程启动失败（退出码 1）。日志摘录：OOM。完整日志：/tmp/x.log');
      }
      throw new Error(`unexpected ${url}`);
    });
    const result = await startAndVerifyLocalModel(
      'http://127.0.0.1:8000',
      localLlama,
      fetchJson as never,
    );
    expect(result.ok).toBe(false);
    expect(result.guide?.logPath).toContain('/tmp/x.log');
    expect(result.guide?.steps.length).toBeGreaterThan(0);
  });

  it('validates llama managed form fields', () => {
    expect(validateLoopbackBaseUrl('http://127.0.0.1:8080/v1')).toBeNull();
    expect(validateLoopbackBaseUrl('https://api.moonshot.cn/v1')).toContain('回环');
    expect(validateLoopbackBaseUrl('http://127.0.0.1/v1')).toContain('端口');

    expect(validateLlamaManagedForm({
      name: 'Qwen',
      model: 'q.gguf',
      base_url: 'http://127.0.0.1:8080/v1',
      model_path: '/models/q.gguf',
      runtime_executable: '/bin/llama-server',
    })).toBeNull();

    expect(validateLlamaManagedForm({
      name: 'Qwen',
      model: 'q.gguf',
      base_url: 'http://127.0.0.1:8080/v1',
      model_path: 'models/q.gguf',
      runtime_executable: '/bin/llama-server',
    })).toContain('绝对路径');

    expect(validateLlamaManagedForm({
      name: 'Qwen',
      model: 'q.gguf',
      base_url: 'http://127.0.0.1:8080/v1',
      model_path: '/models/q.gguf',
      runtime_executable: '/bin/not-server',
    })).toContain('llama-server');

    const payload = buildLlamaManagedPayload({
      name: 'Qwen',
      model: 'q.gguf',
      base_url: 'http://127.0.0.1:8080/v1',
      model_path: '/models/q.gguf',
      runtime_executable: '/bin/llama-server',
      runtime_args: '--n-gpu-layers\n-1',
    });
    expect(payload.local_runtime).toBe('llama_cpp');
    expect(payload.runtime_args).toEqual(['--n-gpu-layers', '-1']);
  });
});
