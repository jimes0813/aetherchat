// frontend/src/stores/sessionStore.ts
import { create } from 'zustand';
import { persist } from 'zustand/middleware';
import { Artifact, Conversation, MergeProposal, Message, ModelConfig, Provider, Task, ToolItem, ToolRun, WorkspaceSettings } from '../types';

const providerColors: Record<string, string> = {
  claude: 'violet',
  deepseek: 'cyan',
  grok: 'amber',
  qwen: 'emerald',
  kimi: 'sky',
  gpt: 'rose',
};

const providerFromModel = (model: ModelConfig, current?: Provider): Provider => ({
  name: model.name,
  role: model.persona || model.model,
  color: current?.color || providerColors[model.name.toLowerCase()] || 'slate',
  status: model.type === 'local' || model.has_api_key
    ? current?.status === 'disabled' ? 'idle' : current?.status || 'idle'
    : 'disabled',
  persona: model.persona || model.type,
  participation_policy: model.participation_policy,
});

interface SessionState {
  sessionId: string;
  conversations: Conversation[];
  activePanel: '记忆' | '资料' | '任务' | '工件' | '合并' | '工具' | '插件' | '连接' | '电脑' | '模型' | '运行' | '设置' | null;
  selectedArtifactPath: string;
  messages: Message[];
  streams: Record<string, string>;
  tasks: Task[];
  artifacts: Artifact[];
  mergeProposals: MergeProposal[];
  tools: ToolItem[];
  toolRuns: ToolRun[];
  settings: WorkspaceSettings;
  providers: Provider[];
  modelConfigs: ModelConfig[];
  budget: { total: number; remaining: number };
  connection: 'offline' | 'connecting' | 'online';
  selectConversation: (id: string) => void;
  createConversation: (title?: string) => string;
  setConversations: (conversations: Conversation[]) => void;
  setMessages: (messages: Message[]) => void;
  setActivePanel: (panel: SessionState['activePanel']) => void;
  setSelectedArtifactPath: (path: string) => void;
  addMessage: (msg: Message) => void;
  setTasks: (tasks: Task[]) => void;
  updateTask: (task: Task) => void;
  setArtifacts: (artifacts: Artifact[]) => void;
  setMergeProposals: (proposals: MergeProposal[]) => void;
  updateMergeProposal: (proposal: MergeProposal) => void;
  setTools: (tools: ToolItem[]) => void;
  upsertTool: (tool: ToolItem) => void;
  removeTool: (toolId: string) => void;
  setToolRuns: (runs: ToolRun[]) => void;
  addToolRun: (run: ToolRun) => void;
  setModelConfigs: (models: ModelConfig[]) => void;
  upsertModelConfig: (model: ModelConfig) => void;
  removeModelConfig: (name: string) => void;
  updateSettings: (settings: Partial<WorkspaceSettings>) => void;
  addDelta: (model: string, delta: string) => void;
  resetStream: (model: string) => void;
  finishStream: (model: string) => void;
  setBudget: (remaining: number) => void;
  setConnection: (connection: SessionState['connection']) => void;
}

export const useSessionStore = create<SessionState>()(persist((set) => ({
  sessionId: '11111111-1111-4111-8111-111111111111',
  conversations: [],
  activePanel: null,
  selectedArtifactPath: 'README.md',
  messages: [],
  streams: {},
  tasks: [],
  artifacts: [],
  mergeProposals: [],
  tools: [],
  toolRuns: [],
  settings: {
    mode: '项目协作',
    mergeRule: '两票通过',
    sandbox: 'Docker 严格沙箱',
    language: '中文优先',
    uiLocale: 'zh-CN',
  },
  providers: [],
  modelConfigs: [],
  budget: { total: 10, remaining: 10 },
  connection: 'offline',
  selectConversation: (sessionId) => set({
    sessionId,
    messages: [],
    streams: {},
    tasks: [],
    artifacts: [],
    mergeProposals: [],
    budget: { total: 10, remaining: 10 },
  }),
  createConversation: (title) => {
    const id = crypto.randomUUID();
    const conversation: Conversation = {
      id,
      title: title?.trim() || '新会话',
      summary: '尚未开始对话',
      updatedAt: '刚刚',
    };
    set((state) => ({
      sessionId: id,
      conversations: [conversation, ...state.conversations],
      messages: [],
      streams: {},
      tasks: [],
      artifacts: [],
      mergeProposals: [],
      budget: { total: 10, remaining: 10 },
    }));
    return id;
  },
  setConversations: (conversations) => set({ conversations }),
  setMessages: (messages) => set({ messages, streams: {} }),
  setActivePanel: (activePanel) => set({ activePanel }),
  setSelectedArtifactPath: (selectedArtifactPath) => set({ selectedArtifactPath }),
  addMessage: (msg) => set((state) => ({
    messages: state.messages.some((item) => item.id === msg.id) ? state.messages : [...state.messages, msg],
  })),
  setTasks: (tasks) => set({ tasks }),
  updateTask: (task) => set((state) => ({
    tasks: state.tasks.some(t => t.id === task.id)
      ? state.tasks.map(t => t.id === task.id ? task : t)
      : [...state.tasks, task],
  })),
  setArtifacts: (artifacts) => set((state) => ({
    artifacts,
    selectedArtifactPath: artifacts.some((artifact) => artifact.path === state.selectedArtifactPath)
      ? state.selectedArtifactPath
      : artifacts[0]?.path ?? state.selectedArtifactPath,
  })),
  setMergeProposals: (mergeProposals) => set({ mergeProposals }),
  updateMergeProposal: (proposal) => set((state) => ({
    mergeProposals: state.mergeProposals.some((item) => item.id === proposal.id)
      ? state.mergeProposals.map((item) => item.id === proposal.id ? proposal : item)
      : [proposal, ...state.mergeProposals],
  })),
  setTools: (tools) => set({ tools }),
  upsertTool: (tool) => set((state) => ({
    tools: state.tools.some((item) => item.id === tool.id)
      ? state.tools.map((item) => item.id === tool.id ? tool : item)
      : [tool, ...state.tools],
  })),
  removeTool: (toolId) => set((state) => ({ tools: state.tools.filter((tool) => tool.id !== toolId) })),
  setToolRuns: (toolRuns) => set({ toolRuns }),
  addToolRun: (run) => set((state) => ({ toolRuns: [run, ...state.toolRuns].slice(0, 50) })),
  setModelConfigs: (modelConfigs) => set((state) => ({
    modelConfigs,
    providers: modelConfigs.map((model) => providerFromModel(
      model,
      state.providers.find((provider) => provider.name === model.name),
    )),
  })),
  upsertModelConfig: (model) => set((state) => ({
    modelConfigs: state.modelConfigs.some((item) => item.name === model.name)
      ? state.modelConfigs.map((item) => item.name === model.name ? model : item)
      : [model, ...state.modelConfigs],
    providers: state.providers.some((item) => item.name === model.name)
      ? state.providers.map((provider) => (
        provider.name === model.name ? providerFromModel(model, provider) : provider
      ))
      : [...state.providers, providerFromModel(model)],
  })),
  removeModelConfig: (name) => set((state) => ({
    modelConfigs: state.modelConfigs.filter((model) => model.name !== name),
    providers: state.providers.filter((provider) => provider.name !== name),
  })),
  updateSettings: (settings) => set((state) => ({ settings: { ...state.settings, ...settings } })),
  addDelta: (model, delta) => set((state) => ({
    streams: { ...state.streams, [model]: `${state.streams[model] ?? ''}${delta}` },
    providers: state.providers.map((provider) => (
      provider.name === model ? { ...provider, status: 'thinking' } : provider
    )),
  })),
  resetStream: (model) => set((state) => {
    const streams = { ...state.streams };
    delete streams[model];
    return { streams };
  }),
  finishStream: (model) => set((state) => {
    const nextStreams = { ...state.streams };
    delete nextStreams[model];
    return {
      streams: nextStreams,
      providers: state.providers.map((provider) => (
        provider.name === model ? { ...provider, status: 'done' } : provider
      )),
    };
  }),
  setBudget: (remaining) => set((state) => ({ budget: { ...state.budget, remaining } })),
  setConnection: (connection) => set({ connection }),
}), {
  name: 'aetherchat-workspace',
  version: 1,
  partialize: (state) => ({
    settings: state.settings,
    sessionId: state.sessionId,
    conversations: state.conversations,
    selectedArtifactPath: state.selectedArtifactPath,
  }),
}));
