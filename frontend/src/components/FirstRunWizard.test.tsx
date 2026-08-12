import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ModelConfig } from '../types';
import {
  FIRST_RUN_DISMISS_KEY,
  FirstRunWizard,
  dismissFirstRunWizard,
  shouldShowFirstRunWizard,
} from './FirstRunWizard';

const unconfigured: ModelConfig = {
  name: 'claude',
  type: 'anthropic',
  model: 'claude-3-5-sonnet',
  api_key_ref: 'ANTHROPIC_API_KEY',
  max_tokens: 8192,
  participation_policy: 'always',
  token_limit: 128000,
  has_api_key: false,
  created_at: '2026-08-12T00:00:00Z',
  updated_at: '2026-08-12T00:00:00Z',
};

const configured: ModelConfig = {
  ...unconfigured,
  name: 'kimi',
  type: 'openai_compat',
  model: 'kimi-k3',
  has_api_key: true,
};

describe('FirstRunWizard helpers', () => {
  beforeEach(() => {
    localStorage.clear();
  });

  it('shows when no configured model and not dismissed', () => {
    expect(shouldShowFirstRunWizard([unconfigured])).toBe(true);
    expect(shouldShowFirstRunWizard([configured])).toBe(false);
    dismissFirstRunWizard();
    expect(shouldShowFirstRunWizard([unconfigured])).toBe(false);
    expect(localStorage.getItem(FIRST_RUN_DISMISS_KEY)).toBe('1');
  });
});

describe('FirstRunWizard UI', () => {
  beforeEach(() => {
    localStorage.clear();
    vi.restoreAllMocks();
  });

  it('walks through three steps and saves a verified model', async () => {
    const created: ModelConfig = {
      ...configured,
      name: 'kimi',
      has_api_key: true,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/models/') && init?.method === 'POST') {
        return {
          ok: true,
          headers: { get: () => 'application/json' },
          json: async () => created,
        };
      }
      if (url.includes('/models/kimi/verify')) {
        return {
          ok: true,
          headers: { get: () => 'application/json' },
          json: async () => ({ ok: true, message: '真实对话检测通过', sample: 'OK' }),
        };
      }
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    const onConfigured = vi.fn();
    const onDismiss = vi.fn();
    render(
      <FirstRunWizard
        models={[unconfigured]}
        onConfigured={onConfigured}
        onDismiss={onDismiss}
        apiBase="http://127.0.0.1:8000"
      />,
    );

    expect(screen.getByRole('heading', { name: '欢迎使用 AetherChat' })).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '开始配置' }));
    expect(screen.getByRole('heading', { name: '配置一个可对话的模型' })).toBeTruthy();

    fireEvent.change(screen.getByPlaceholderText('sk-...'), {
      target: { value: 'sk-test-key' },
    });
    fireEvent.click(screen.getByRole('button', { name: '保存并验证' }));

    await waitFor(() => {
      expect(onConfigured).toHaveBeenCalledWith(created);
      expect(screen.getByRole('heading', { name: '已就绪，可以开始' })).toBeTruthy();
    });
    expect(screen.getByText(/样本回复：OK/)).toBeTruthy();

    fireEvent.click(screen.getByRole('button', { name: '进入工作台' }));
    expect(onDismiss).toHaveBeenCalled();
    expect(localStorage.getItem(FIRST_RUN_DISMISS_KEY)).toBe('1');
  });

  it('saves llama managed preset then starts and verifies', async () => {
    const created: ModelConfig = {
      ...configured,
      name: 'Qwen',
      type: 'local',
      model: 'Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf',
      base_url: 'http://127.0.0.1:8080/v1',
      local_runtime: 'llama_cpp',
      model_path: '/home/jimes0813/Qwen/Qwen3.6-35B-A3B-Uncensored-HauhauCS-Aggressive-Q4_K_P.gguf',
      runtime_executable: '/home/jimes0813/llama.cpp-cuda/build/bin/llama-server',
      has_api_key: true,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/models/') && init?.method === 'POST') {
        const body = JSON.parse(String(init.body || '{}')) as Record<string, unknown>;
        expect(body.local_runtime).toBe('llama_cpp');
        expect(body.model_path).toContain('.gguf');
        expect(body.runtime_executable).toContain('llama-server');
        return {
          ok: true,
          headers: { get: () => 'application/json' },
          json: async () => created,
        };
      }
      if (url.includes('/start') && init?.method === 'POST') {
        return {
          ok: true,
          headers: { get: () => 'application/json' },
          json: async () => ({ ready: true, managed: true, started: true, pid: 4242 }),
        };
      }
      if (url.includes('/verify')) {
        return {
          ok: true,
          headers: { get: () => 'application/json' },
          json: async () => ({ ok: true, message: '真实对话检测通过', sample: '本地托管OK' }),
        };
      }
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    const onConfigured = vi.fn();
    render(
      <FirstRunWizard
        models={[unconfigured]}
        onConfigured={onConfigured}
        onDismiss={() => undefined}
        apiBase="http://127.0.0.1:8000"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '开始配置' }));
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'local-llama' } });
    expect(screen.getByText(/llama.cpp 托管/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '保存并启动验证' }));

    await waitFor(() => {
      expect(onConfigured).toHaveBeenCalledWith(created);
      expect(screen.getByRole('heading', { name: '已就绪，可以开始' })).toBeTruthy();
    });
    expect(screen.getByText(/样本回复：本地托管OK/)).toBeTruthy();
    expect(fetchMock.mock.calls.some(([url]) => String(url).includes('/start'))).toBe(true);
  });

  it('rejects llama managed form when paths are incomplete', async () => {
    render(
      <FirstRunWizard
        models={[unconfigured]}
        onConfigured={() => undefined}
        onDismiss={() => undefined}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '开始配置' }));
    fireEvent.change(screen.getByRole('combobox'), { target: { value: 'local-llama' } });
    // GGUF path uses dedicated placeholder; model ID also ends with .gguf
    const ggufInput = screen.getByPlaceholderText('/path/to/model.gguf');
    fireEvent.change(ggufInput, { target: { value: '' } });
    fireEvent.click(screen.getByRole('button', { name: '保存并启动验证' }));
    const alert = await screen.findByRole('alert');
    expect(alert.textContent).toMatch(/GGUF|路径/);
  });

  it('supports local external service preset without API key', async () => {
    const created: ModelConfig = {
      ...configured,
      name: 'local-openai',
      type: 'local',
      model: 'local-model',
      base_url: 'http://127.0.0.1:8080/v1',
      local_runtime: 'external',
      has_api_key: true,
    };
    const fetchMock = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.endsWith('/models/') && init?.method === 'POST') {
        return {
          ok: true,
          headers: { get: () => 'application/json' },
          json: async () => created,
        };
      }
      if (url.includes('/runtime')) {
        return {
          ok: true,
          headers: { get: () => 'application/json' },
          json: async () => ({ ready: true, managed: false, started: false }),
        };
      }
      if (url.includes('/verify')) {
        return {
          ok: true,
          headers: { get: () => 'application/json' },
          json: async () => ({ ok: true, message: '真实对话检测通过', sample: '本地OK' }),
        };
      }
      throw new Error(`unexpected fetch ${url}`);
    });
    vi.stubGlobal('fetch', fetchMock);

    const onConfigured = vi.fn();
    render(
      <FirstRunWizard
        models={[unconfigured]}
        onConfigured={onConfigured}
        onDismiss={() => undefined}
        apiBase="http://127.0.0.1:8000"
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '开始配置' }));
    const select = screen.getByRole('combobox');
    fireEvent.change(select, { target: { value: 'local-external' } });
    expect(screen.getByText(/外部本地服务模式/)).toBeTruthy();
    fireEvent.click(screen.getByRole('button', { name: '保存并验证' }));
    await waitFor(() => {
      expect(onConfigured).toHaveBeenCalledWith(created);
      expect(screen.getByRole('heading', { name: '已就绪，可以开始' })).toBeTruthy();
    });
  });

  it('allows skipping setup for later', () => {
    const onDismiss = vi.fn();
    render(
      <FirstRunWizard
        models={[unconfigured]}
        onConfigured={() => undefined}
        onDismiss={onDismiss}
      />,
    );
    fireEvent.click(screen.getByRole('button', { name: '稍后再说' }));
    expect(onDismiss).toHaveBeenCalled();
    expect(localStorage.getItem(FIRST_RUN_DISMISS_KEY)).toBe('1');
  });
});
