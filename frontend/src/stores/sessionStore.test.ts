import { beforeEach, describe, expect, it } from 'vitest';
import { useSessionStore } from './sessionStore';
import type { Message, ModelConfig, Provider, ReasoningStep } from '../types';

const makeMessage = (id: string, overrides: Partial<Message> = {}): Message => ({
  id,
  session_id: 's-1',
  author_model: 'claude',
  role: 'assistant',
  content: `content-${id}`,
  created_at: '2026-01-01T00:00:00Z',
  ...overrides,
});

const makeModel = (name: string, overrides: Partial<ModelConfig> = {}): ModelConfig => ({
  name,
  type: 'openai_compat',
  model: `${name}-model`,
  api_key_ref: 'ref',
  has_api_key: true,
  max_tokens: 4096,
  participation_policy: 'always',
  token_limit: 128000,
  created_at: '2026-01-01T00:00:00Z',
  updated_at: '2026-01-01T00:00:00Z',
  ...overrides,
});

const initialState = useSessionStore.getState();

beforeEach(() => {
  window.localStorage.clear();
  useSessionStore.setState(initialState, true);
});

describe('sessionStore: addMessage 去重', () => {
  it('追加新消息', () => {
    useSessionStore.getState().addMessage(makeMessage('m1'));
    expect(useSessionStore.getState().messages.map((m) => m.id)).toEqual(['m1']);
  });

  it('相同 id 的消息不会重复插入', () => {
    const store = useSessionStore.getState();
    store.addMessage(makeMessage('m1'));
    store.addMessage(makeMessage('m1', { content: 'changed' }));
    const { messages } = useSessionStore.getState();
    expect(messages).toHaveLength(1);
    expect(messages[0].content).toBe('content-m1');
  });

  it('不同 id 的消息按序追加', () => {
    const store = useSessionStore.getState();
    store.addMessage(makeMessage('m1'));
    store.addMessage(makeMessage('m2'));
    expect(useSessionStore.getState().messages.map((m) => m.id)).toEqual(['m1', 'm2']);
  });
});

describe('sessionStore: setMessages 历史/实时合并（竞态保护）', () => {
  it('保留历史返回之后才到达的实时消息', () => {
    // 模拟：历史请求发出后，WS 先推来了一条新消息
    useSessionStore.getState().addMessage(makeMessage('live-1'));
    // 较慢的历史响应到达，不含 live-1
    useSessionStore.getState().setMessages([makeMessage('h1'), makeMessage('h2')]);

    const ids = useSessionStore.getState().messages.map((m) => m.id);
    expect(ids).toEqual(['h1', 'h2', 'live-1']);
  });

  it('历史中已包含的消息不会被实时副本覆盖/重复', () => {
    useSessionStore.getState().addMessage(makeMessage('dup'));
    useSessionStore.getState().setMessages([makeMessage('dup', { content: 'from-history' })]);

    const { messages } = useSessionStore.getState();
    expect(messages).toHaveLength(1);
    expect(messages[0].content).toBe('from-history');
  });

  it('setMessages 同时清空 streams 与 reasoningStreams', () => {
    const store = useSessionStore.getState();
    store.addDelta('claude', 'partial');
    store.addReasoningStep('claude', { phase: 'analysis', detail: 'thinking' });
    store.setMessages([]);

    const state = useSessionStore.getState();
    expect(state.streams).toEqual({});
    expect(state.reasoningStreams).toEqual({});
  });
});

describe('sessionStore: addDelta', () => {
  it('为模型初始化并追加流式增量', () => {
    const store = useSessionStore.getState();
    store.addDelta('claude', 'Hello');
    store.addDelta('claude', ' world');
    expect(useSessionStore.getState().streams.claude).toBe('Hello world');
  });

  it('不同模型的流互不影响', () => {
    const store = useSessionStore.getState();
    store.addDelta('claude', 'A');
    store.addDelta('gpt', 'B');
    const { streams } = useSessionStore.getState();
    expect(streams.claude).toBe('A');
    expect(streams.gpt).toBe('B');
  });

  it('输出时将对应 provider 状态置为 thinking', () => {
    useSessionStore.getState().setModelConfigs([makeModel('claude'), makeModel('gpt')]);
    useSessionStore.getState().addDelta('claude', 'x');

    const providers = useSessionStore.getState().providers;
    expect(providers.find((p) => p.name === 'claude')?.status).toBe('thinking');
    expect(providers.find((p) => p.name === 'gpt')?.status).toBe('idle');
  });
});

describe('sessionStore: addReasoningStep 有界与去重', () => {
  const step = (detail: string, phase: ReasoningStep['phase'] = 'analysis'): ReasoningStep => ({ phase, detail });

  it('追加步骤并可在读取时取回', () => {
    const store = useSessionStore.getState();
    store.addReasoningStep('claude', step('s1'));
    store.addReasoningStep('claude', step('s2'));
    expect(useSessionStore.getState().reasoningStreams.claude.map((s) => s.detail)).toEqual(['s1', 's2']);
  });

  it('连续重复（同 phase 同 detail）的步骤被去重', () => {
    const store = useSessionStore.getState();
    store.addReasoningStep('claude', step('same'));
    store.addReasoningStep('claude', step('same'));
    expect(useSessionStore.getState().reasoningStreams.claude).toHaveLength(1);
  });

  it('detail 相同但 phase 不同不算重复', () => {
    const store = useSessionStore.getState();
    store.addReasoningStep('claude', step('same', 'analysis'));
    store.addReasoningStep('claude', step('same', 'tool'));
    expect(useSessionStore.getState().reasoningStreams.claude).toHaveLength(2);
  });

  it('每个模型的推理步骤上限为 32 条（slice(-32)）', () => {
    const store = useSessionStore.getState();
    for (let i = 0; i < 40; i += 1) {
      store.addReasoningStep('claude', step(`step-${i}`));
    }
    const steps = useSessionStore.getState().reasoningStreams.claude;
    expect(steps).toHaveLength(32);
    expect(steps[0].detail).toBe('step-8');
    expect(steps[31].detail).toBe('step-39');
  });

  it('reset=true 时丢弃此前步骤重新开始', () => {
    const store = useSessionStore.getState();
    store.addReasoningStep('claude', step('old-1'));
    store.addReasoningStep('claude', step('old-2'));
    store.addReasoningStep('claude', step('fresh'), true);

    const steps = useSessionStore.getState().reasoningStreams.claude;
    expect(steps.map((s) => s.detail)).toEqual(['fresh']);
  });

  it('不同模型的推理流互不影响', () => {
    const store = useSessionStore.getState();
    store.addReasoningStep('claude', step('c1'));
    store.addReasoningStep('gpt', step('g1'));
    const { reasoningStreams } = useSessionStore.getState();
    expect(reasoningStreams.claude.map((s) => s.detail)).toEqual(['c1']);
    expect(reasoningStreams.gpt.map((s) => s.detail)).toEqual(['g1']);
  });
});

describe('sessionStore: addToolRun 有界（slice(0, 50)）', () => {
  const makeRun = (id: string) => ({
    id,
    session_id: 's-1',
    tool_name: 'tool',
    caller: 'claude',
    arguments: {},
    output: '',
    status: 'success' as const,
    created_at: '2026-01-01T00:00:00Z',
  });

  it('新记录插到最前', () => {
    const store = useSessionStore.getState();
    store.addToolRun(makeRun('r1'));
    store.addToolRun(makeRun('r2'));
    expect(useSessionStore.getState().toolRuns.map((r) => r.id)).toEqual(['r2', 'r1']);
  });

  it('最多保留 50 条，丢弃最旧的', () => {
    const store = useSessionStore.getState();
    for (let i = 0; i < 55; i += 1) {
      store.addToolRun(makeRun(`run-${i}`));
    }
    const runs = useSessionStore.getState().toolRuns;
    expect(runs).toHaveLength(50);
    expect(runs[0].id).toBe('run-54');
    expect(runs[49].id).toBe('run-5');
  });
});

describe('sessionStore: resetStream / finishStream', () => {
  it('resetStream 只删除指定模型的文本流', () => {
    const store = useSessionStore.getState();
    store.addDelta('claude', 'A');
    store.addDelta('gpt', 'B');
    store.resetStream('claude');

    const { streams } = useSessionStore.getState();
    expect(streams.claude).toBeUndefined();
    expect(streams.gpt).toBe('B');
  });

  it('finishStream 同时清理文本流与推理流，并将 provider 置为 done', () => {
    const store = useSessionStore.getState();
    store.setModelConfigs([makeModel('claude'), makeModel('gpt')]);
    store.addDelta('claude', 'partial');
    store.addDelta('gpt', 'other');
    store.addReasoningStep('claude', { phase: 'final', detail: 'done' });
    store.finishStream('claude');

    const state = useSessionStore.getState();
    expect(state.streams.claude).toBeUndefined();
    expect(state.reasoningStreams.claude).toBeUndefined();
    expect(state.streams.gpt).toBe('other');
    expect(state.providers.find((p) => p.name === 'claude')?.status).toBe('done');
    expect(state.providers.find((p) => p.name === 'gpt')?.status).toBe('thinking');
  });
});

describe('sessionStore: 会话切换与创建', () => {
  it('按会话隔离草稿，新会话默认没有旧草稿', () => {
    const store = useSessionStore.getState();
    store.setComposerDraft('session-1', '旧会话草稿');
    store.selectConversation('session-2');
    expect(useSessionStore.getState().composerDrafts['session-2']).toBeUndefined();
    store.setComposerDraft('session-2', '新会话草稿');
    expect(useSessionStore.getState().composerDrafts).toMatchObject({
      'session-1': '旧会话草稿',
      'session-2': '新会话草稿',
    });
  });

  it('selectConversation 切换 sessionId 并清空会话级状态', () => {
    const store = useSessionStore.getState();
    store.addMessage(makeMessage('m1'));
    store.addDelta('claude', 'stream');
    store.addReasoningStep('claude', { phase: 'analysis', detail: 'x' });
    store.setTasks([{ id: 't1' } as never]);
    store.setSelectedArtifactPath('stale/from-old-session.md');
    store.setBudget(3);

    store.selectConversation('session-2');

    const state = useSessionStore.getState();
    expect(state.sessionId).toBe('session-2');
    expect(state.messages).toEqual([]);
    expect(state.streams).toEqual({});
    expect(state.reasoningStreams).toEqual({});
    expect(state.tasks).toEqual([]);
    expect(state.artifacts).toEqual([]);
    expect(state.selectedArtifactPath).toBe('');
    expect(state.mergeProposals).toEqual([]);
    expect(state.budget).toEqual({ total: 10, remaining: 10 });
  });

  it('createConversation 新建会话置顶、返回 id 并切换过去', () => {
    useSessionStore.getState().setSelectedArtifactPath('stale/from-old-session.md');
    useSessionStore.getState().setConversations([
      { id: 'old', title: '旧会话', summary: '', updatedAt: '' },
    ]);
    const id = useSessionStore.getState().createConversation('  我的会话  ');

    const state = useSessionStore.getState();
    expect(id).toBeTruthy();
    expect(state.sessionId).toBe(id);
    expect(state.conversations[0].id).toBe(id);
    expect(state.conversations[0].title).toBe('我的会话');
    expect(state.conversations[1].id).toBe('old');
    expect(state.messages).toEqual([]);
    expect(state.selectedArtifactPath).toBe('');
  });

  it('createConversation 空标题回退为「新会话」', () => {
    useSessionStore.getState().createConversation('   ');
    expect(useSessionStore.getState().conversations[0].title).toBe('新会话');
  });
});

describe('sessionStore: 工件选择恢复', () => {
  it('空列表会清除过期选择，避免对新会话发出 404 请求', () => {
    useSessionStore.getState().setSelectedArtifactPath('deleted.md');
    useSessionStore.getState().setArtifacts([]);
    expect(useSessionStore.getState().selectedArtifactPath).toBe('');
  });

  it('选择已过期时切换到服务端返回的第一个可用工件', () => {
    useSessionStore.getState().setSelectedArtifactPath('deleted.md');
    useSessionStore.getState().setArtifacts([
      { id: 'a1', session_id: 's1', path: 'README.md', kind: 'doc', current_hash: 'abc' },
    ]);
    expect(useSessionStore.getState().selectedArtifactPath).toBe('README.md');
  });
});

describe('sessionStore: providers 派生', () => {
  it('setModelConfigs 根据模型生成 provider 并保留既有状态', () => {
    useSessionStore.setState({
      providers: [{ name: 'claude', role: 'old', color: 'pink', status: 'error' } as Provider],
    });
    useSessionStore.getState().setModelConfigs([makeModel('claude'), makeModel('unknown-model')]);

    const providers = useSessionStore.getState().providers;
    const claude = providers.find((p) => p.name === 'claude');
    expect(claude?.color).toBe('pink');
    expect(claude?.status).toBe('error');
    expect(providers.find((p) => p.name === 'unknown-model')?.color).toBe('slate');
  });

  it('无 API key 且非本地的模型 provider 为 disabled', () => {
    useSessionStore.getState().setModelConfigs([makeModel('gpt', { has_api_key: false })]);
    expect(useSessionStore.getState().providers[0].status).toBe('disabled');
  });

  it('本地模型即使无 API key 也不被禁用', () => {
    useSessionStore.getState().setModelConfigs([makeModel('local-x', { type: 'local', has_api_key: false })]);
    expect(useSessionStore.getState().providers[0].status).toBe('idle');
  });
});
