import { useEffect, useMemo, useRef, useState } from 'react';
import {
  Activity,
  Archive,
  Brain,
  Bot,
  ChevronRight,
  CircleDollarSign,
  Camera,
  Cable,
  FileCode2,
  FileText,
  FolderOpen,
  GitBranch,
  GitMerge,
  LayoutList,
  Laptop,
  ListTodo,
  Maximize2,
  MessageSquare,
  MousePointer2,
  Minus,
  Play,
  Plus,
  Puzzle,
  PackageCheck,
  Cpu,
  Radio,
  Search,
  Save,
  Settings,
  ShieldCheck,
  SquareTerminal,
  Trash2,
  Users,
  Wrench,
  X,
} from 'lucide-react';
import { Composer } from './components/Composer';
import { TaskBoard } from './components/TaskBoard';
import { UnifiedFeed } from './components/UnifiedFeed';
import { SafeMarkdown } from './components/SafeMarkdown';
import { useDialogFocus } from './components/useDialogFocus';
import { useI18n } from './i18n';
import { useSessionStore } from './stores/sessionStore';
import { connectWS } from './ws/client';
import type { AgentRun, AgentWorkspaceCandidate, ApprovalRequest, Conversation, GitRepository, KnowledgeAttachment, MCPConnection, MergeProposal, Message, ModelConfig, PluginInvokeResult, PluginItem, SessionMemory, Task, TaskEvent, ToolItem, ToolRun, WorkspaceSettings, WSMessage } from './types';

const API_BASE = window.aetherRuntime?.apiBase ?? import.meta.env.VITE_API_BASE ?? 'http://localhost:8000';
let apiAuthToken = '';

function withApiAuth(init: RequestInit = {}): RequestInit {
  const headers = new Headers(init.headers);
  if (apiAuthToken) headers.set('Authorization', `Bearer ${apiAuthToken}`);
  return { ...init, headers };
}

const SETTING_TO_MODE: Record<WorkspaceSettings['mode'], 'casual' | 'roundtable' | 'project'> = {
  '日常讨论': 'casual',
  '圆桌评审': 'roundtable',
  '项目协作': 'project',
};

const MODE_TO_SETTING: Record<'casual' | 'roundtable' | 'project', WorkspaceSettings['mode']> = {
  casual: '日常讨论',
  roundtable: '圆桌评审',
  project: '项目协作',
};

async function fetchJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, withApiAuth(init));
  const contentType = response.headers.get('content-type') ?? '';
  const data = contentType.includes('application/json')
    ? await response.json().catch(() => ({}))
    : await response.text().catch(() => '');
  if (!response.ok) {
    const detail = typeof data === 'object' && data && 'detail' in data
      ? String((data as { detail?: unknown }).detail)
      : typeof data === 'string' && data
        ? data
        : `HTTP ${response.status}`;
    throw new Error(detail);
  }
  return data as T;
}

type CommandItem = {
  id: string;
  label: string;
  hint: string;
  run: () => void;
};

type StoredSession = {
  id: string;
  title: string;
  summary: string;
  mode: 'casual' | 'roundtable' | 'project';
  created_at: string;
  updated_at: string;
  last_message_at?: string | null;
  message_count: number;
  task_count: number;
  open_task_count: number;
};

type KnowledgeSearchHit = {
  attachment_id: string;
  source_name: string;
  locator: string;
  content: string;
  citation: string;
  score: number;
  match_types: string[];
};

type KnowledgePreview = {
  attachment: KnowledgeAttachment;
  kind: 'text' | 'image';
  chunks: Array<{ locator: string; content: string }>;
  truncated: boolean;
  imageUrl?: string;
};

type ArtifactPreview = {
  path: string;
  mime_type: string;
  mode: 'markdown' | 'isolated_html' | 'code' | 'office_summary' | 'download_only';
  content: string;
  truncated: boolean;
  sandbox?: string | null;
};

type ArtifactVersion = {
  id: string;
  version: number;
  hash: string;
  short_hash: string;
  message: string;
  author: string;
  source_model?: string | null;
  parent_version?: number | null;
  created_at: string;
  content?: string;
  content_encoding: 'utf-8' | 'base64';
};

const formatDateTime = (value?: string | null) => {
  if (!value) return '尚未开始';
  return new Intl.DateTimeFormat(document.documentElement.lang === 'en-US' ? 'en-US' : 'zh-CN', {
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
  }).format(new Date(value));
};

const conversationFromSession = (session: StoredSession): Conversation => ({
  id: session.id,
  title: session.title,
  summary: session.summary || '尚未开始对话',
  updatedAt: formatDateTime(session.last_message_at || session.updated_at),
  createdAt: session.created_at,
  mode: session.mode,
  messageCount: session.message_count,
  taskCount: session.task_count,
  openTaskCount: session.open_task_count,
});

type DesktopDocumentSnapshot = {
  name: string;
  format: string;
  size_bytes: number;
  modified_at: string;
  editor_mode: 'text' | 'structured' | 'pdf_note';
  data: Record<string, unknown>;
  read_only_text?: string;
  page_count?: number;
  truncated?: boolean;
};

type SelectedDesktopDocument = {
  handle: string;
  displayName: string;
  document: DesktopDocumentSnapshot;
};

type DesktopMouseResult = {
  action: 'get' | 'move' | 'click';
  position: { x: number; y: number };
  button?: string | null;
  virtual_desktop: { left: number; top: number; width: number; height: number };
};

type DesktopDocumentSaveResult = {
  saved: boolean;
  displayName: string;
  sizeBytes: number;
  savedAt: string;
  backupCreated: boolean;
  backupDisplayName?: string;
};

type DesktopScreenshotSaveResult = {
  saved: boolean;
  displayName: string;
  locationLabel: string;
  sizeBytes: number;
};

declare global {
  interface Window {
    aetherWindow?: {
      minimize: () => void;
      toggleMaximize: () => void;
      close: () => void;
    };
    aetherRuntime?: {
      apiBase: string;
      backendStatus: 'starting' | 'ready' | 'unavailable';
      appVersion?: string;
      platform?: string;
      getApiToken?: () => Promise<string>;
      openExternal?: (url: string) => Promise<boolean>;
    };
    aetherDesktop?: {
      status: () => Promise<Record<string, unknown>>;
      selectDocument: () => Promise<SelectedDesktopDocument | null>;
      saveDocument: (handle: string, data: Record<string, unknown>) => Promise<DesktopDocumentSaveResult>;
      saveDocumentAs: (handle: string, data: Record<string, unknown>) => Promise<(DesktopDocumentSaveResult & { handle: string }) | null>;
      captureScreen: () => Promise<{ dataUrl: string; width: number; height: number; capturedAt: string }>;
      saveScreenshot: (dataUrl: string) => Promise<DesktopScreenshotSaveResult>;
      mouse: (action: 'get' | 'move' | 'click', options?: { x?: number; y?: number; button?: 'left' | 'right' | 'middle' }) => Promise<DesktopMouseResult>;
    };
    aetherWorkspace?: {
      selectRepository: (sessionId: string) => Promise<GitRepository | null>;
      decideCandidate: (
        workspaceId: string,
        sessionId: string,
        decision: 'apply' | 'reject',
        summary?: { changedFiles?: number; verificationStatus?: string },
      ) => Promise<AgentWorkspaceCandidate | null>;
    };
  }
}

function App() {
  const {
    sessionId,
    conversations,
    providers,
    tasks,
    artifacts,
    modelConfigs,
    budget,
    connection,
    activePanel,
    setActivePanel,
    selectedArtifactPath,
    setSelectedArtifactPath,
    setConnection,
    addMessage,
    setMessages,
    setConversations,
    addDelta,
    resetStream,
    finishStream,
    setBudget,
    setTasks,
    updateTask,
    setArtifacts,
    setMergeProposals,
    updateMergeProposal,
    setTools,
    upsertTool,
    removeTool,
    setToolRuns,
    addToolRun,
    setModelConfigs,
    upsertModelConfig,
    removeModelConfig,
    settings: workspaceSettings,
    updateSettings,
    selectConversation,
  } = useSessionStore();
  const { locale, t, ui } = useI18n();
  const activePanelLabel = activePanel ? ({
    记忆: t('memory'), 资料: t('knowledge'), 任务: t('tasks'), 工件: t('artifacts'), 合并: t('changes'),
    工具: t('tools'), 插件: t('plugins'), 连接: t('connections'), 电脑: t('computer'), 模型: t('models'),
    运行: t('runs'), 设置: t('settings'),
  } as Record<string, string>)[activePanel] : ui('检查器');
  const wsClient = useRef<ReturnType<typeof connectWS> | null>(null);
  const [authToken, setAuthToken] = useState('');
  const [authReady, setAuthReady] = useState(false);
  const [tokenStatus, setTokenStatus] = useState('正在授权');
  const [notice, setNotice] = useState('');
  const [commandOpen, setCommandOpen] = useState(false);
  const [mainView, setMainView] = useState<'chat' | 'history' | 'tasks'>('chat');
  const [taskEvents, setTaskEvents] = useState<TaskEvent[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [agentRuns, setAgentRuns] = useState<AgentRun[]>([]);
  const [gitRepository, setGitRepository] = useState<GitRepository | null>(null);
  const [verificationCommands, setVerificationCommands] = useState('');
  const inspectorDialog = useDialogFocus<HTMLDivElement>(Boolean(activePanel), () => setActivePanel(null));

  useEffect(() => {
    document.documentElement.lang = locale;
    document.title = locale === 'en-US' ? 'AetherChat Desktop' : 'AetherChat 中文协作工作台';
  }, [locale]);

  useEffect(() => {
    if (window.aetherRuntime?.backendStatus === 'unavailable') {
      setNotice('本机后端未能启动；聊天与工作区写入暂不可用，请在“运行”面板查看状态');
    }
  }, []);

  useEffect(() => {
    let cancelled = false;
    const initializeAuth = async () => {
      try {
        const desktopToken = await window.aetherRuntime?.getApiToken?.();
        const data = desktopToken
          ? { access_token: desktopToken }
          : await fetchJson<{ access_token?: string }>(`${API_BASE}/auth/token`, { method: 'POST' });
        if (!data.access_token) throw new Error('后端未返回本机访问令牌');
        if (cancelled) return;
        apiAuthToken = data.access_token;
        setAuthToken(data.access_token);
        setTokenStatus('本机授权');
      } catch (error) {
        if (cancelled) return;
        setTokenStatus('授权失败');
        setNotice(`本机 API 授权失败：${String(error)}`);
      } finally {
        if (!cancelled) setAuthReady(true);
      }
    };
    void initializeAuth();
    return () => { cancelled = true; };
  }, []);

  useEffect(() => {
    if (!notice) return;
    const timer = window.setTimeout(() => setNotice(''), 4500);
    return () => window.clearTimeout(timer);
  }, [notice]);

  const loadTasks = () => {
    fetchJson<Task[]>(`${API_BASE}/tasks/?session_id=${sessionId}`).then(setTasks).catch(() => setTasks([]));
  };

  const loadConversations = async () => {
    try {
      let rows = await fetchJson<StoredSession[]>(`${API_BASE}/sessions/`);
      if (!rows.some((session) => session.id === sessionId)) {
        await fetchJson<StoredSession>(`${API_BASE}/sessions/`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ id: sessionId, title: '新会话', mode: currentMode }),
        });
        rows = await fetchJson<StoredSession[]>(`${API_BASE}/sessions/`);
      }
      setConversations(rows.map(conversationFromSession));
    } catch {
      // 保留本地缓存，后端恢复连接后会再次刷新。
    }
  };

  const loadMessages = () => {
    fetchJson<Message[]>(`${API_BASE}/sessions/${sessionId}/messages`).then(setMessages).catch(() => setMessages([]));
  };

  const loadTaskEvents = () => {
    fetchJson<TaskEvent[]>(`${API_BASE}/sessions/${sessionId}/task-events`).then(setTaskEvents).catch(() => setTaskEvents([]));
  };

  const loadAgentRuns = () => {
    fetchJson<AgentRun[]>(`${API_BASE}/agent-runs?session_id=${encodeURIComponent(sessionId)}`)
      .then(setAgentRuns)
      .catch(() => setAgentRuns([]));
  };

  const loadGitRepositories = () => {
    fetchJson<GitRepository[]>(
      `${API_BASE}/workspaces/repositories?session_id=${encodeURIComponent(sessionId)}`,
    )
      .then((repositories) => {
        const repository = repositories[0] || null;
        setGitRepository(repository);
        setVerificationCommands(
          (repository?.verification_commands || []).join('\n'),
        );
      })
      .catch(() => {
        setGitRepository(null);
        setVerificationCommands('');
      });
  };

  const loadArtifacts = () => {
    fetchJson<ReturnType<typeof useSessionStore.getState>['artifacts']>(`${API_BASE}/artifacts/?session_id=${sessionId}`).then(setArtifacts).catch(() => setArtifacts([]));
  };

  const loadMerges = () => {
    fetchJson<MergeProposal[]>(`${API_BASE}/artifacts/merge/proposals?session_id=${sessionId}`).then(setMergeProposals).catch(() => setMergeProposals([]));
  };

  const loadTools = () => {
    fetchJson<ToolItem[]>(`${API_BASE}/tools/?session_id=${encodeURIComponent(sessionId)}`).then(setTools).catch(() => setTools([]));
    fetchJson<ToolRun[]>(`${API_BASE}/tools/runs?session_id=${encodeURIComponent(sessionId)}`).then(setToolRuns).catch(() => setToolRuns([]));
  };

  const loadModels = () => {
    fetchJson<ModelConfig[]>(`${API_BASE}/models/`).then(setModelConfigs).catch(() => setModelConfigs([]));
  };

  const exportSession = async (fmt: 'markdown' | 'json' = 'markdown') => {
    try {
      const res = await fetch(`${API_BASE}/export/session/${sessionId}?format=${fmt}`, withApiAuth());
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      let url = '';
      if (fmt === 'json') {
        const data = await res.json();
        const blob = new Blob([JSON.stringify(data, null, 2)], { type: 'application/json' });
        url = URL.createObjectURL(blob);
      } else {
        const blob = await res.blob();
        url = URL.createObjectURL(blob);
      }
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = `session_${sessionId}.${fmt === 'json' ? 'json' : 'md'}`;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
      setNotice(`会话已导出为 ${fmt === 'json' ? 'JSON' : 'Markdown'}`);
    } catch (error) {
      setNotice(`导出失败：${String(error)}`);
    }
  };

  const currentMode = SETTING_TO_MODE[workspaceSettings.mode] ?? 'project';

  useEffect(() => {
    if (!authReady || !authToken) return;
    void loadConversations();
  }, [authReady, authToken]);

  const createPersistentConversation = async () => {
    try {
      const session = await fetchJson<StoredSession>(`${API_BASE}/sessions/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: '新协作会话', mode: currentMode }),
      });
      selectConversation(session.id);
      setMainView('chat');
      await loadConversations();
    } catch (error) {
      setNotice(`新建会话失败：${String(error)}`);
    }
  };
  const fetchToken = async () => {
    try {
      const desktopToken = await window.aetherRuntime?.getApiToken?.();
      const data = desktopToken
        ? { access_token: desktopToken }
        : await fetchJson<{ access_token?: string }>(`${API_BASE}/auth/token`, { method: 'POST' });
      if (!data.access_token) throw new Error('后端未返回访问令牌');
      apiAuthToken = data.access_token;
      setAuthToken(data.access_token);
      setTokenStatus('授权已刷新');
      setNotice('本地会话授权已刷新，WebSocket 正在重连');
    } catch (error) {
      setTokenStatus('授权失败');
      setNotice(`授权刷新失败：${String(error)}`);
    }
  };

  useEffect(() => {
    if (!authReady || !authToken) return;
    setConnection('connecting');
    wsClient.current = connectWS(sessionId, (event: WSMessage) => {
      if (event.type === 'delta' && event.model && event.delta) addDelta(event.model, event.delta);
      if (event.type === 'stream_reset' && event.model) resetStream(event.model);
      if (event.type === 'message_complete' && event.message) {
        addMessage(event.message);
        if (event.message.author_model !== 'user') finishStream(event.message.author_model);
        void loadConversations();
      }
      if (event.type === 'task_update' && event.task) updateTask(event.task);
      if (event.type === 'tool_update' && event.tool) upsertTool(event.tool);
      if (event.type === 'tool_run' && event.run) addToolRun(event.run);
      if (event.type === 'approval_required' && event.approval) {
        setApprovals((items) => items.some((item) => item.id === event.approval?.id)
          ? items
          : [...items, event.approval as ApprovalRequest]);
      }
      if (event.type === 'approval_resolved' && event.approval_id && event.decision) {
        setApprovals((items) => items.map((item) => item.id === event.approval_id
          ? { ...item, decision: event.decision }
          : item));
      }
      if (event.type === 'agent_run_update' && event.agent_run) {
        setAgentRuns((items) => items.some((item) => item.id === event.agent_run?.id)
          ? items.map((item) => item.id === event.agent_run?.id
            ? {
                ...event.agent_run as AgentRun,
                workspace_candidates: event.agent_run?.workspace_candidates
                  ?? item.workspace_candidates,
                workspace_candidate_count: event.agent_run?.workspace_candidate_count
                  ?? item.workspace_candidate_count,
              }
            : item)
          : [event.agent_run as AgentRun, ...items]);
      }
      if (event.type === 'plan_created' && event.run_id && event.goal && event.plan_steps) {
        setAgentRuns((items) => items.map((item) => item.id === event.run_id
          ? {
              ...item,
              goal: event.goal,
              plan_steps: event.plan_steps,
              plan_step_count: event.plan_steps?.length || 0,
              completed_plan_step_count: event.plan_steps?.filter((step) => step.status === 'completed').length || 0,
            }
          : item));
        setNotice(t('planCreated'));
      }
      if (event.type === 'plan_confirmation_required' && event.run_id) {
        if (event.agent_run) {
          const waitingRun = event.agent_run;
          setAgentRuns((items) => items.some((item) => item.id === waitingRun.id)
            ? items.map((item) => item.id === waitingRun.id ? waitingRun : item)
            : [waitingRun, ...items]);
        }
        setNotice('计划草稿已就绪，请审核、编辑或确认后再执行');
        setActivePanel('运行');
      }
      if (event.type === 'plan_updated' && event.run_id) {
        if (event.agent_run) {
          setAgentRuns((items) => items.map((item) => (
            item.id === event.run_id ? event.agent_run! : item
          )));
        } else {
          setAgentRuns((items) => items.map((item) => item.id === event.run_id
            ? {
                ...item,
                goal: event.goal || item.goal,
                plan_steps: event.plan_steps || item.plan_steps,
                plan_revision: event.revision || item.plan_revision,
              }
            : item));
        }
        setNotice(`计划草稿已保存为第 ${event.revision || '?'} 版`);
      }
      if (event.type === 'plan_replanned' && event.run_id && event.plan_steps) {
        setAgentRuns((items) => items.map((item) => item.id === event.run_id
          ? {
              ...item,
              plan_steps: event.plan_steps,
              plan_revision: event.revision || item.plan_revision,
              automatic_revision_count: event.automatic_revision_count
                ?? item.automatic_revision_count,
              plan_step_count: event.plan_steps?.length || 0,
              completed_plan_step_count: event.plan_steps?.filter(
                (step) => step.status === 'completed'
              ).length || 0,
            }
          : item));
        setNotice(`Verifier 已触发第 ${event.automatic_revision_count || 1} 次受控返工`);
      }
      if (event.type === 'plan_rejected' && event.agent_run) {
        setAgentRuns((items) => items.map((item) => (
          item.id === event.agent_run?.id ? event.agent_run! : item
        )));
        setNotice('计划已拒绝，未调用任何 Worker');
      }
      if (event.type === 'plan_step_update' && event.run_id && event.plan_step) {
        setAgentRuns((items) => items.map((item) => {
          if (item.id !== event.run_id) return item;
          const current = item.plan_steps || [];
          const planSteps = current.some((step) => step.id === event.plan_step?.id)
            ? current.map((step) => step.id === event.plan_step?.id ? event.plan_step! : step)
            : [...current, event.plan_step!];
          return {
            ...item,
            plan_steps: planSteps,
            plan_step_count: planSteps.length,
            completed_plan_step_count: planSteps.filter((step) => step.status === 'completed').length,
          };
        }));
      }
      if (event.type === 'review_recorded' && event.run_id && event.review) {
        setAgentRuns((items) => items.map((item) => item.id === event.run_id
          ? {
              ...item,
              reviews: [...(item.reviews || []).filter((review) => review.id !== event.review?.id), event.review!],
              review_count: (item.reviews || []).some((review) => review.id === event.review?.id)
                ? item.review_count || item.reviews?.length || 0
                : (item.review_count || item.reviews?.length || 0) + 1,
            }
          : item));
      }
      if (event.type === 'workspace_verified' && event.run_id && event.workspace) {
        setAgentRuns((items) => items.map((item) => {
          if (item.id !== event.run_id) return item;
          const candidates = item.workspace_candidates || [];
          const next = candidates.some((candidate) => candidate.id === event.workspace?.id)
            ? candidates.map((candidate) => candidate.id === event.workspace?.id
              ? event.workspace as AgentWorkspaceCandidate
              : candidate)
            : [...candidates, event.workspace as AgentWorkspaceCandidate];
          return {
            ...item,
            workspace_candidates: next,
            workspace_candidate_count: next.length,
          };
        }));
      }
      if (event.type === 'plan_step_output' && event.model) finishStream(event.model);
      if (event.type === 'steer_ack') setNotice(t('steerQueued'));
      if (event.type === 'cost_update' && typeof event.budget_remaining === 'number') setBudget(event.budget_remaining);
      if (event.type === 'error' && (event as any).message) {
        addMessage({
          id: crypto.randomUUID(),
          session_id: sessionId,
          author_model: '系统',
          role: 'system',
          content: String((event as any).message),
          created_at: new Date().toISOString(),
        });
      }
      if (event.type === 'ack' || ((event as any).type === 'system' && (event as any).content === 'Connected')) setConnection('online');
      if ((event as any).type === 'system' && (event as any).content === 'Disconnected') setConnection('offline');
    }, authToken || undefined, API_BASE);
    loadTasks();
    loadMessages();
    loadTaskEvents();
    loadAgentRuns();
    loadGitRepositories();
    loadArtifacts();
    loadMerges();
    loadTools();
    loadModels();
    return () => wsClient.current?.close();
  }, [sessionId, authToken, authReady]);

  const spent = useMemo(() => budget.total - budget.remaining, [budget]);
  const spendPct = Math.min(100, Math.max(0, (spent / budget.total) * 100));

  useEffect(() => {
    const handleShortcut = (event: KeyboardEvent) => {
      if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'k') {
        event.preventDefault();
        setCommandOpen(true);
      }
      if (event.key === 'Escape') {
        setCommandOpen(false);
        setActivePanel(null);
      }
    };
    window.addEventListener('keydown', handleShortcut);
    return () => window.removeEventListener('keydown', handleShortcut);
  }, [setActivePanel]);

  const commandItems = useMemo<CommandItem[]>(() => {
    const panels = (['记忆', '资料', '任务', '工件', '合并', '工具', '插件', '连接', '电脑', '模型', '运行', '设置'] as const).map((panel) => ({
      id: `panel-${panel}`,
      label: `打开${panel}面板`,
      hint: '命令',
      run: () => setActivePanel(panel),
    }));
    const conversationItems = conversations.map((conversation) => ({
      id: `conversation-${conversation.id}`,
      label: conversation.title,
      hint: `会话 · ${conversation.summary}`,
      run: () => selectConversation(conversation.id),
    }));
    const taskItems = tasks.map((task) => ({
      id: `task-${task.id}`,
      label: task.title,
      hint: `任务 · ${task.status}`,
      run: () => setActivePanel('任务'),
    }));
    const artifactItems = artifacts.map((artifact) => ({
      id: `artifact-${artifact.path}`,
      label: artifact.path,
      hint: `工件 · ${artifact.kind}`,
      run: () => {
        setSelectedArtifactPath(artifact.path);
        setActivePanel('工件');
      },
    }));
    const modelItems = modelConfigs.map((model) => ({
      id: `model-${model.name}`,
      label: model.name,
      hint: `模型 · ${model.model}`,
      run: () => setActivePanel('模型'),
    }));
    return [...panels, ...conversationItems, ...taskItems, ...artifactItems, ...modelItems];
  }, [artifacts, conversations, modelConfigs, selectConversation, setActivePanel, setSelectedArtifactPath, tasks]);

  const sendMessage = (content: string, targets: string[], attachmentIds: string[]) => {
    const resolvedTargets = targets.includes('all')
      ? modelConfigs
          .filter((model) => model.type === 'local' || model.has_api_key)
          .map((model) => model.name)
      : targets;
    const sent = wsClient.current?.sendAction({
      type: 'post_message',
      content,
      targets: resolvedTargets,
      mode: currentMode,
      execution_strategy: currentMode === 'project' ? 'planned' : 'direct',
      plan_confirmation_required: currentMode === 'project',
      max_revision_cycles: currentMode === 'project' ? 1 : 0,
      language: workspaceSettings.language,
      attachment_ids: attachmentIds,
      ...(currentMode === 'project' && gitRepository ? {
        repository_id: gitRepository.id,
        verification_commands: verificationCommands
          .split('\n')
          .map((command) => command.trim())
          .filter(Boolean)
          .slice(0, 4),
      } : {}),
    });
    if (!sent) {
      addMessage({
        id: crypto.randomUUID(),
        session_id: sessionId,
        author_model: '系统',
        role: 'system',
        content: '后端未连接，消息未发送。内容仍保留在输入框中，请等待连接恢复后重试。',
        created_at: new Date().toISOString(),
      });
    }
    return Boolean(sent);
  };

  const selectGitRepository = async () => {
    if (!window.aetherWorkspace) {
      setNotice('选择本机 Git 仓库只在 AetherChat 桌面版中可用');
      return;
    }
    try {
      const repository = await window.aetherWorkspace.selectRepository(sessionId);
      if (!repository) return;
      setGitRepository(repository);
      setVerificationCommands(repository.verification_commands.join('\n'));
      setNotice(`已接入 Git 仓库 ${repository.display_name}；主仓库保持只读`);
    } catch (error) {
      setNotice(`Git 仓库接入失败：${String(error)}`);
    }
  };

  const decideWorkspaceCandidate = async (
    candidate: AgentWorkspaceCandidate,
    decision: 'apply' | 'reject',
  ) => {
    if (!window.aetherWorkspace) {
      setNotice('候选应用只在带原生确认的 AetherChat 桌面版中可用');
      return;
    }
    try {
      const updated = await window.aetherWorkspace.decideCandidate(
        candidate.id,
        sessionId,
        decision,
        {
          changedFiles: candidate.changed_files.length,
          verificationStatus: candidate.verification_status,
        },
      );
      if (!updated) return;
      setAgentRuns((items) => items.map((run) => ({
        ...run,
        workspace_candidates: run.workspace_candidates?.map((item) => (
          item.id === updated.id ? updated : item
        )),
      })));
      setNotice(decision === 'apply'
        ? '候选变更已应用到主仓库；未执行 commit 或 push'
        : '候选 worktree 已安全丢弃；主仓库未修改');
    } catch (error) {
      setNotice(`Git 候选处理失败：${String(error)}`);
    }
  };

  const retryAgentRun = (run: AgentRun) => {
    const sent = wsClient.current?.sendAction({ type: 'retry_agent_run', run_id: run.id });
    setNotice(sent ? '已提交失败步骤重试；已完成步骤不会重复执行' : '后端未连接，无法重试运行');
  };
  const interruptAgentStep = (run: AgentRun, stepId: string) => {
    const sent = wsClient.current?.sendAction({
      type: 'interrupt',
      run_id: run.id,
      step_id: stepId,
    });
    setNotice(sent ? '已提交计划步骤中断请求' : '后端未连接，无法中断计划步骤');
  };
  const updateAgentPlan = (
    run: AgentRun,
    goalSpec: {
      objective: string;
      success_criteria: string[];
      constraints: string[];
      deliverable: string;
    },
    planSteps: Array<{
      id: string;
      title: string;
      description: string;
      assigned_model?: string | null;
      role: string;
      dependencies: string[];
      success_criteria: string[];
    }>,
    changeSummary: string,
  ) => {
    const sent = wsClient.current?.sendAction({
      type: 'update_agent_plan',
      run_id: run.id,
      expected_revision: run.plan_revision,
      goal_spec: goalSpec,
      plan_steps: planSteps,
      change_summary: changeSummary,
    });
    setNotice(sent ? '正在保存计划草稿' : '后端未连接，无法保存计划');
    return Boolean(sent);
  };
  const confirmAgentPlan = (run: AgentRun) => {
    const sent = wsClient.current?.sendAction({
      type: 'confirm_agent_plan',
      run_id: run.id,
      expected_revision: run.plan_revision,
    });
    setNotice(sent ? '计划已确认，开始执行' : '后端未连接，无法确认计划');
  };
  const rejectAgentPlan = (run: AgentRun) => {
    const sent = wsClient.current?.sendAction({
      type: 'reject_agent_plan',
      run_id: run.id,
      expected_revision: run.plan_revision,
    });
    setNotice(sent ? '正在拒绝计划' : '后端未连接，无法拒绝计划');
  };

  const decideApproval = async (
    approval: ApprovalRequest,
    decision: 'allow_once' | 'allow_session' | 'deny',
  ) => {
    try {
      await fetchJson(`${API_BASE}/approvals/${approval.id}/decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: sessionId, decision }),
      });
      setApprovals((items) => items.map((item) => item.id === approval.id ? { ...item, decision } : item));
    } catch (error) {
      setNotice(`授权处理失败：${String(error)}`);
    }
  };

  const uploadAttachments = async (files: File[]) => {
    const uploaded: KnowledgeAttachment[] = [];
    for (const file of files) {
      const body = new FormData();
      body.append('file', file, file.name);
      const attachment = await fetchJson<KnowledgeAttachment>(
        `${API_BASE}/knowledge/${sessionId}/attachments`,
        { method: 'POST', body },
      );
      if (file.type.startsWith('image/')) attachment.preview_url = URL.createObjectURL(file);
      uploaded.push(attachment);
    }
    if (uploaded.length) setNotice(`已解析 ${uploaded.length} 个项目资料，可随消息发送并引用来源`);
    return uploaded;
  };

  const createTask = async (title: string) => {
    const task = await fetchJson<Task>(`${API_BASE}/tasks/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ session_id: sessionId, title, description: '由中文工作台创建', priority: 1 }),
    });
    updateTask(task);
    loadTaskEvents();
    void loadConversations();
    setNotice('任务已创建');
  };

  const patchTask = async (task: Task, patch: Partial<Task>) => {
    const updated = await fetchJson<Task>(`${API_BASE}/tasks/${task.id}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(patch),
    });
    updateTask(updated);
    loadTaskEvents();
    void loadConversations();
  };

  const createMerge = async (artifactPath: string, rationale: string) => {
    const proposal = await fetchJson<MergeProposal>(`${API_BASE}/artifacts/merge/proposals`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        artifact_path: artifactPath,
        proposer: 'user',
        rationale,
        merge_rule: workspaceSettings.mergeRule,
      }),
    });
    updateMergeProposal(proposal);
    setActivePanel('合并');
    setNotice('合并提案已创建');
  };

  const voteMerge = async (proposal: MergeProposal, approve: boolean) => {
    const updated = await fetchJson<MergeProposal>(`${API_BASE}/artifacts/merge/proposals/${proposal.id}/vote`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ voter: 'user', approve }),
    });
    updateMergeProposal(updated);
  };

  const createTool = async (payload: Partial<ToolItem>) => {
    const tool = await fetchJson<ToolItem>(`${API_BASE}/tools/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ ...payload, session_id: sessionId }),
    });
    upsertTool(tool);
    setNotice('工具已添加');
  };

  const deleteTool = async (tool: ToolItem) => {
    if (!window.confirm(`确定删除工具“${tool.name}”吗？此操作无法撤销。`)) return;
    await fetchJson(`${API_BASE}/tools/${tool.id}`, { method: 'DELETE' });
    removeTool(tool.id);
    setNotice('工具已删除');
  };

  const invokeTool = async (tool: ToolItem, caller = 'user') => {
    if (workspaceSettings.sandbox === '关闭执行') {
      setNotice('当前沙箱策略已关闭工具执行');
      return;
    }
    if (workspaceSettings.sandbox === '只读预览' && /write|merge|run|command|create/i.test(tool.name)) {
      setNotice('只读预览模式已阻止写入或执行类工具');
      return;
    }
    const argumentsForTool: Record<string, unknown> = {
      path: selectedArtifactPath,
      artifact_path: selectedArtifactPath,
      input: '来自工具仓的中文调用',
    };
    const required = Array.isArray(tool.input_schema?.required)
      ? tool.input_schema.required.filter((item): item is string => typeof item === 'string')
      : [];
    const missing = required.filter((name) => argumentsForTool[name] === undefined || argumentsForTool[name] === '');
    if (missing.length) {
      setNotice(`工具“${tool.name}”还需要参数：${missing.join('、')}；请通过模型调用或使用对应工作区面板`);
      return;
    }
    const run = await fetchJson<ToolRun>(`${API_BASE}/tools/${tool.id}/invoke`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ caller, session_id: sessionId, arguments: argumentsForTool }),
    });
    addToolRun(run);
  };

  const createModel = async (payload: Partial<ModelConfig> & { api_key?: string }) => {
    const data = await fetchJson<ModelConfig>(`${API_BASE}/models/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    upsertModelConfig(data);
  };

  const deleteModel = async (model: ModelConfig) => {
    if (!window.confirm(`确定删除模型“${model.name}”吗？本地密钥不会显示，但该模型配置将被移除。`)) return;
    await fetchJson(`${API_BASE}/models/${encodeURIComponent(model.name)}`, { method: 'DELETE' });
    removeModelConfig(model.name);
    setNotice('模型配置已删除');
  };

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[#e9e1d6] text-[#2f2a24]">
      <a href="#aether-main-content" className="aether-skip-link">Skip to main content / 跳到主要内容</a>
      <DesktopTitleBar />

      <main id="aether-main-content" tabIndex={-1} className="aether-main-grid grid min-h-0 flex-1 grid-cols-[3.5rem_15rem_minmax(0,1fr)] border-t border-[#cfc4b7]">
        <ActivityRail activePanel={activePanel} setActivePanel={setActivePanel} />

        <WorkspaceSidebar
          conversations={conversations}
          currentSessionId={sessionId}
          onNewConversation={() => void createPersistentConversation()}
          onOpenSearch={() => setCommandOpen(true)}
          onSelectConversation={selectConversation}
        />

        <section className="flex min-h-0 min-w-0 flex-col border-x border-[#d8cfc3] bg-[#fbfaf7]">
          <CommandHeader
            connection={connection}
            providerCount={providers.length}
            activeProviderCount={modelConfigs.filter((model) => model.type === 'local' || model.has_api_key).length}
            budgetRemaining={budget.remaining}
            spendPct={spendPct}
            mode={workspaceSettings.mode}
            mergeRule={workspaceSettings.mergeRule}
            onOpenSearch={() => setCommandOpen(true)}
          />
          <div className="flex items-center gap-2 border-b bg-[#f5efe7] p-2 text-xs">
            <span className="font-medium">模式:</span>
            {(['casual', 'roundtable', 'project'] as const).map(m => (
              <button 
                key={m}
                onClick={() => updateSettings({ mode: MODE_TO_SETTING[m] })}
                title={m === 'project' ? ui('项目模式会增加模型调用：并行执行、独立核验、最终综合。') : undefined}
                aria-pressed={currentMode === m}
                className={`rounded px-2 py-0.5 ${currentMode === m ? 'bg-[#8d5f35] text-white' : 'hover:bg-[#e6ded4]'}`}
              >
                {m === 'casual' ? '休闲' : m === 'roundtable' ? '圆桌' : '项目'}
              </button>
            ))}
            {currentMode === 'project' && (
              <span className="hidden text-[#7a5a3d] xl:inline">
                {ui('项目模式会增加模型调用：并行执行、独立核验、最终综合。')}
              </span>
            )}
            <span className="ml-auto text-[#8b8176]">当前: {currentMode}</span>
          </div>
          <MainViewTabs value={mainView} onChange={setMainView} />
          {mainView === 'chat' && <UnifiedFeed approvals={approvals.filter((item) => item.session_id === sessionId)} onApprovalDecision={(approval, decision) => void decideApproval(approval, decision)} />}
          {mainView === 'history' && (
            <ConversationHistory
              conversations={conversations}
              currentSessionId={sessionId}
              onContinue={(id) => {
                selectConversation(id);
                setMainView('chat');
              }}
            />
          )}
          {mainView === 'tasks' && <TaskHistory tasks={tasks} events={taskEvents} />}
          {mainView === 'chat' && (
            <Composer
              onSend={sendMessage}
              onUpload={uploadAttachments}
              providers={providers.map((provider) => provider.name)}
              onInterrupt={(scope) => wsClient.current?.sendAction({ type: 'interrupt', scope })}
            />
          )}
        </section>

      </main>

      {activePanel && (
        <div className="aether-modal-backdrop" role="presentation" onClick={() => setActivePanel(null)}>
          <div
            ref={inspectorDialog.containerRef}
            role="dialog"
            aria-modal="true"
            aria-label={`${activePanelLabel} panel`}
            tabIndex={-1}
            className="aether-modal-panel"
            onKeyDown={inspectorDialog.onKeyDown}
            onClick={(event) => event.stopPropagation()}
          >
            <InspectorPanel
              apiBase={API_BASE}
              sessionId={sessionId}
              selectedArtifactPath={selectedArtifactPath}
              onSelectArtifact={setSelectedArtifactPath}
              onCreateTask={createTask}
              onPatchTask={patchTask}
              onCreateMerge={createMerge}
              onVoteMerge={voteMerge}
              onReloadArtifacts={loadArtifacts}
              onUpdateSettings={updateSettings}
              onCreateTool={createTool}
              onDeleteTool={deleteTool}
              onInvokeTool={invokeTool}
              onCreateModel={createModel}
              onDeleteModel={deleteModel}
              exportSession={exportSession}
              agentRuns={agentRuns}
              gitRepository={gitRepository}
              verificationCommands={verificationCommands}
              onVerificationCommandsChange={setVerificationCommands}
              onSelectGitRepository={() => void selectGitRepository()}
              onDecideWorkspaceCandidate={(candidate, decision) => {
                void decideWorkspaceCandidate(candidate, decision);
              }}
              onRetryAgentRun={retryAgentRun}
              onInterruptAgentStep={interruptAgentStep}
              onUpdateAgentPlan={updateAgentPlan}
              onConfirmAgentPlan={confirmAgentPlan}
              onRejectAgentPlan={rejectAgentPlan}
              onClose={() => setActivePanel(null)}
            />
          </div>
        </div>
      )}

      {commandOpen && (
        <CommandPalette
          items={commandItems}
          onClose={() => setCommandOpen(false)}
        />
      )}

      {notice && (
        <div role="status" className="fixed bottom-10 left-1/2 z-[70] -translate-x-1/2 rounded-lg bg-[#2f2a24] px-4 py-2 text-sm text-white shadow-xl">
          {notice}
        </div>
      )}

      <DesktopStatusBar 
        connection={connection} 
        budgetRemaining={budget.remaining} 
        uiLocale={workspaceSettings.uiLocale || 'zh-CN'}
        sandbox={workspaceSettings.sandbox}
        tokenStatus={tokenStatus}
        onRefreshToken={fetchToken}
        onToggleLanguage={() => updateSettings({
          uiLocale: (workspaceSettings.uiLocale || 'zh-CN') === 'zh-CN' ? 'en-US' : 'zh-CN',
        })}
      />
    </div>
  );
}

function DesktopTitleBar() {
  const platformLabel = window.aetherRuntime?.platform === 'linux'
    ? 'Linux'
    : window.aetherRuntime?.platform === 'darwin'
      ? 'macOS'
      : window.aetherRuntime?.platform === 'win32'
        ? 'Windows'
        : '浏览器';
  const windowActions = [
    { label: '最小化', icon: Minus, action: () => window.aetherWindow?.minimize() },
    { label: '最大化', icon: Maximize2, action: () => window.aetherWindow?.toggleMaximize() },
    {
      label: '关闭',
      icon: X,
      action: () => {
        if (window.aetherWindow) window.aetherWindow.close();
        else window.close();
      },
    },
  ];

  return (
    <header className="grid h-10 grid-cols-[auto_minmax(0,1fr)_auto] items-center bg-[#f5efe7] text-[#4a4037]">
      <div className="flex h-full items-center gap-2 px-3">
        <div className="flex h-6 w-6 items-center justify-center rounded-md bg-[#2f2a24] text-white">
          <Radio size={13} />
        </div>
        <span className="text-xs font-semibold">AetherChat Desktop</span>
        <span className="ml-2 text-[11px] text-[#8b8176]">{platformLabel}</span>
      </div>
      <div className="flex h-full items-center text-[12px] text-[#6d6258]">
        前端 + 本机后端一体运行
      </div>
      <div className="flex h-full items-center">
        {windowActions.map(({ label, icon: Icon, action }) => (
          <button key={label} title={label} onClick={action} className="flex h-10 w-11 items-center justify-center text-[#6d6258] hover:bg-[#e2d9ce]">
            <Icon size={14} />
          </button>
        ))}
      </div>
    </header>
  );
}

interface ActivityRailProps {
  activePanel: ReturnType<typeof useSessionStore.getState>['activePanel'];
  setActivePanel: ReturnType<typeof useSessionStore.getState>['setActivePanel'];
}

function ActivityRail({ activePanel, setActivePanel }: ActivityRailProps) {
  const { t } = useI18n();
  const items = [
    { label: t('chat'), icon: MessageSquare, panel: null },
    { label: t('memory'), icon: Brain, panel: '记忆' },
    { label: t('knowledge'), icon: FolderOpen, panel: '资料' },
    { label: t('tasks'), icon: LayoutList, panel: '任务' },
    { label: t('artifacts'), icon: FileCode2, panel: '工件' },
    { label: t('changes'), icon: GitMerge, panel: '合并' },
    { label: t('tools'), icon: Wrench, panel: '工具' },
    { label: t('plugins'), icon: Puzzle, panel: '插件' },
    { label: t('connections'), icon: Cable, panel: '连接' },
    { label: t('computer'), icon: Laptop, panel: '电脑' },
    { label: t('models'), icon: Cpu, panel: '模型' },
    { label: t('runs'), icon: SquareTerminal, panel: '运行' },
    { label: t('settings'), icon: Settings, panel: '设置' },
  ] as const;

  return (
    <aside className="flex min-h-0 flex-col items-center border-r border-[#d8cfc3] bg-[#2f2a24] py-3 text-[#d8cfc3]">
      <div className="mb-4 flex h-9 w-9 items-center justify-center rounded-lg bg-[#f5efe7] text-[#2f2a24]">
        <Bot size={17} />
      </div>
      <div className="flex flex-1 flex-col gap-2">
        {items.map((item) => {
          const Icon = item.icon;
          const selected = item.panel === activePanel;
          return (
            <button
              key={item.label}
              title={item.label}
              aria-label={item.label}
              aria-pressed={selected}
              onClick={() => setActivePanel(item.panel)}
              className={`flex h-10 w-10 items-center justify-center rounded-lg transition ${selected ? 'bg-[#8d5f35] text-white' : 'hover:bg-[#443b32]'}`}
            >
              <Icon size={18} />
            </button>
          );
        })}
      </div>
    </aside>
  );
}

interface WorkspaceSidebarProps {
  conversations: ReturnType<typeof useSessionStore.getState>['conversations'];
  currentSessionId: string;
  onNewConversation: () => void;
  onOpenSearch: () => void;
  onSelectConversation: (id: string) => void;
}

interface MainViewTabsProps {
  value: 'chat' | 'history' | 'tasks';
  onChange: (value: MainViewTabsProps['value']) => void;
}

function MainViewTabs({ value, onChange }: MainViewTabsProps) {
  const { t, ui } = useI18n();
  const tabs = [
    { id: 'chat' as const, label: t('currentChat'), icon: MessageSquare },
    { id: 'history' as const, label: t('chatHistory'), icon: Archive },
    { id: 'tasks' as const, label: t('taskRecords'), icon: ListTodo },
  ];
  return (
    <nav role="tablist" aria-label={ui('会话视图')} className="flex h-11 shrink-0 items-end gap-1 border-b border-[#ddd3c7] bg-[#fbfaf7] px-4">
      {tabs.map((tab) => {
        const Icon = tab.icon;
        const selected = tab.id === value;
        return (
          <button
            key={tab.id}
            type="button"
            aria-selected={selected}
            role="tab"
            onClick={() => onChange(tab.id)}
            className={`flex h-10 items-center gap-2 border-b-2 px-3 text-sm ${selected ? 'border-[#8d5f35] font-medium text-[#5d3b20]' : 'border-transparent text-[#766b60] hover:text-[#2f2a24]'}`}
          >
            <Icon size={15} />
            {tab.label}
          </button>
        );
      })}
    </nav>
  );
}

interface ConversationHistoryProps {
  conversations: Conversation[];
  currentSessionId: string;
  onContinue: (id: string) => void;
}

function ConversationHistory({ conversations, currentSessionId, onContinue }: ConversationHistoryProps) {
  const { ui } = useI18n();
  return (
    <div className="min-h-0 flex-1 overflow-auto bg-[#fbfaf7] px-5 py-4">
      <div className="mx-auto max-w-4xl">
        <div className="mb-4 flex items-baseline justify-between gap-4">
          <div>
            <h2 className="text-base font-semibold">{ui('历史聊天')}</h2>
            <p className="mt-1 text-xs text-[#7a7065]">{ui('选择会话后恢复消息、任务和工作区状态')}</p>
          </div>
          <span className="text-xs text-[#8b8176]">{ui('{count} 个会话', { count: conversations.length })}</span>
        </div>
        <div className="divide-y divide-[#e3dbd0] border-y border-[#e3dbd0]">
          {conversations.map((conversation) => (
            <button
              key={conversation.id}
              type="button"
              onClick={() => onContinue(conversation.id)}
              className="grid w-full grid-cols-[minmax(0,1fr)_auto] gap-4 px-2 py-4 text-left hover:bg-[#f5efe7]"
            >
              <div className="min-w-0">
                <div className="flex items-center gap-2">
                  <span className="truncate text-sm font-medium">{conversation.title}</span>
                  {conversation.id === currentSessionId && <span className="shrink-0 rounded bg-[#e7d8c8] px-1.5 py-0.5 text-[10px] text-[#6d4828]">{ui('当前')}</span>}
                </div>
                <p className="mt-1 line-clamp-2 text-xs leading-5 text-[#6f655b]">{conversation.summary}</p>
                <div className="mt-2 flex flex-wrap gap-x-4 gap-y-1 text-[11px] text-[#93887d]">
                  <span>{ui('{count} 条消息', { count: conversation.messageCount ?? 0 })}</span>
                  <span>{ui('{count} 个任务', { count: conversation.taskCount ?? 0 })}</span>
                  <span>{ui('{count} 个进行中', { count: conversation.openTaskCount ?? 0 })}</span>
                </div>
              </div>
              <div className="flex items-center gap-2 self-center text-xs text-[#8b8176]">
                <span>{conversation.updatedAt}</span>
                <ChevronRight size={15} />
              </div>
            </button>
          ))}
          {!conversations.length && <div className="py-12 text-center text-sm text-[#8b8176]">{ui('暂无历史会话')}</div>}
        </div>
      </div>
    </div>
  );
}

const taskStatusLabel: Record<Task['status'], string> = {
  backlog: '待处理', claimed: '已认领', in_progress: '进行中', review: '评审中', merged: '已合并', done: '已完成',
};

function TaskHistory({ tasks, events }: { tasks: Task[]; events: TaskEvent[] }) {
  const { ui } = useI18n();
  return (
    <div className="min-h-0 flex-1 overflow-auto bg-[#fbfaf7] px-5 py-4">
      <div className="mx-auto grid max-w-5xl gap-6 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1.1fr)]">
        <section>
          <div className="mb-3 flex items-baseline justify-between">
            <h2 className="text-base font-semibold">{ui('当前任务')}</h2>
            <span className="text-xs text-[#8b8176]">{ui('{count} 项', { count: tasks.length })}</span>
          </div>
          <div className="divide-y divide-[#e3dbd0] border-y border-[#e3dbd0]">
            {tasks.map((task) => (
              <div key={task.id} className="px-2 py-3">
                <div className="flex items-start justify-between gap-3">
                  <span className="text-sm font-medium leading-5">{task.title}</span>
                  <span className="shrink-0 rounded bg-[#ece4d9] px-2 py-0.5 text-[11px] text-[#665a4e]">{ui(taskStatusLabel[task.status])}</span>
                </div>
                {task.description && <p className="mt-1 text-xs leading-5 text-[#74695e]">{task.description}</p>}
                {task.claimed_by && <div className="mt-2 text-[11px] text-[#93887d]">{ui('执行者：{name}', { name: task.claimed_by })}</div>}
              </div>
            ))}
            {!tasks.length && <div className="py-10 text-center text-sm text-[#8b8176]">{ui('当前会话暂无任务')}</div>}
          </div>
        </section>
        <section>
          <div className="mb-3 flex items-baseline justify-between">
            <h2 className="text-base font-semibold">{ui('变更记录')}</h2>
            <span className="text-xs text-[#8b8176]">{ui('{count} 条', { count: events.length })}</span>
          </div>
          <div className="border-l border-[#cdbfae] pl-4">
            {events.map((event) => (
              <div key={event.id} className="relative pb-5">
                <span className="absolute -left-[19px] top-1.5 h-2 w-2 rounded-full bg-[#8d5f35]" />
                <div className="flex items-start justify-between gap-3">
                  <span className="text-sm font-medium">{event.task.title}</span>
                  <time className="shrink-0 text-[11px] text-[#93887d]">{formatDateTime(event.created_at)}</time>
                </div>
                <p className="mt-1 text-xs text-[#74695e]">
                  {event.action === 'created' ? ui('创建任务') : ui('更新任务')} · {ui(taskStatusLabel[event.task.status])}
                  {event.task.claimed_by ? ` · ${event.task.claimed_by}` : ''}
                </p>
              </div>
            ))}
            {!events.length && <div className="py-10 text-center text-sm text-[#8b8176]">{ui('暂无任务变更记录')}</div>}
          </div>
        </section>
      </div>
    </div>
  );
}

function WorkspaceSidebar({ conversations, currentSessionId, onNewConversation, onOpenSearch, onSelectConversation }: WorkspaceSidebarProps) {
  const { ui } = useI18n();
  return (
    <aside className="flex min-h-0 flex-col bg-[#eee7de]">
      <div className="border-b border-[#d8cfc3] px-3 py-3">
        <div className="mb-3 flex items-center justify-between">
          <div>
            <div className="text-sm font-semibold">AetherChat</div>
            <div className="text-xs text-[#7a7065]">{ui('多模型 Agent 工作台')}</div>
          </div>
          <button onClick={onNewConversation} aria-label={ui('新建会话')} title={ui('新建会话')} className="flex h-8 w-8 items-center justify-center rounded-lg border border-[#d8cfc3] bg-[#fbfaf7]">
            <Plus size={15} />
          </button>
        </div>
        <button onClick={onOpenSearch} className="flex h-9 w-full items-center gap-2 rounded-lg border border-[#d8cfc3] bg-[#fbfaf7] px-3 text-left text-sm text-[#7a7065]">
          <Search size={15} />
          {ui('搜索会话、文件或命令')}
        </button>
      </div>

      <div className="border-b border-[#d8cfc3] p-3">
        <div className="rounded-lg bg-[#2f2a24] p-3 text-white">
          <div className="flex items-center gap-2 text-sm font-semibold">
            <GitBranch size={15} />
            aetherchat/main
          </div>
          <div className="mt-2 text-xs leading-5 text-[#d8cfc3]">{ui('项目协作 · 中文优先 · Docker 严格沙箱')}</div>
        </div>
      </div>

      <div className="px-3 pt-3 text-xs font-semibold text-[#8b8176]">{ui('最近会话')}</div>
      <div className="min-h-0 flex-1 overflow-auto p-3">
        {conversations.map((conversation) => (
          <button
            key={conversation.id}
            onClick={() => onSelectConversation(conversation.id)}
            aria-current={conversation.id === currentSessionId ? 'page' : undefined}
            className={`mb-2 w-full rounded-lg border p-3 text-left shadow-sm ${conversation.id === currentSessionId ? 'border-[#8d5f35] bg-white' : 'border-[#ddd3c7] bg-[#fbfaf7]'}`}
          >
            <div className="flex items-center justify-between gap-2">
              <span className="truncate text-sm font-medium">{conversation.title}</span>
              <ChevronRight size={14} className="text-[#9a9084]" />
            </div>
            <div className="mt-1 line-clamp-2 text-xs leading-5 text-[#7a7065]">{conversation.summary}</div>
            <div className="mt-2 text-[11px] text-[#9a9084]">{conversation.updatedAt}</div>
          </button>
        ))}
      </div>
    </aside>
  );
}

interface CommandHeaderProps {
  connection: ReturnType<typeof useSessionStore.getState>['connection'];
  providerCount: number;
  activeProviderCount: number;
  budgetRemaining: number;
  spendPct: number;
  mode: string;
  mergeRule: string;
  onOpenSearch: () => void;
}

function CommandHeader({ connection, providerCount, activeProviderCount, budgetRemaining, spendPct, mode, mergeRule, onOpenSearch }: CommandHeaderProps) {
  const { ui } = useI18n();
  return (
    <header className="flex h-14 items-center gap-3 border-b border-[#e7dfd3] bg-[#fbfaf7] px-4">
      <div className="min-w-0">
        <div className="truncate text-sm font-semibold">{ui('AetherChat 中文协作工作台')}</div>
        <div className="truncate text-xs text-[#7a7065]">{ui('模式：{mode} · 合并规则：{rule}', { mode: ui(mode), rule: ui(mergeRule) })}</div>
      </div>
      <button onClick={onOpenSearch} className="mx-auto hidden h-9 min-w-[18rem] max-w-xl flex-1 items-center gap-2 rounded-lg border border-[#d8cfc3] bg-white px-3 text-left text-xs text-[#8b8176] lg:flex">
        <Search size={14} />
        {ui('Ctrl+K 搜索命令、文件、模型或任务')}
      </button>
      <div className="ml-auto flex items-center gap-2">
        <div className="flex items-center gap-2 rounded-lg border border-[#e1d9cd] bg-white px-3 py-2 text-xs text-[#6d6258]">
          <Users size={15} />
          {ui('{active}/{total} 个模型已配置', { active: activeProviderCount, total: providerCount })}
        </div>
        <div className="flex min-w-44 items-center gap-3 rounded-lg border border-[#e1d9cd] bg-white px-3 py-2">
          <CircleDollarSign size={15} className="text-[#8d5f35]" />
          <div className="flex-1">
            <div className="flex justify-between text-[11px] text-[#7a7065]">
              <span>{ui('预算余额')}</span>
              <span>${budgetRemaining.toFixed(2)}</span>
            </div>
            <div className="mt-1 h-1.5 rounded-full bg-[#e8e1d7]">
              <div className="h-full rounded-full bg-[#8d5f35]" style={{ width: `${spendPct}%` }} />
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 rounded-lg bg-[#2f2a24] px-3 py-2 text-xs font-medium text-white">
          <Activity size={14} />
          {connection === 'online' ? ui('已连接') : connection === 'connecting' ? ui('连接中') : ui('离线')}
        </div>
      </div>
    </header>
  );
}

function CommandPalette({ items, onClose }: { items: CommandItem[]; onClose: () => void }) {
  const { ui } = useI18n();
  const [query, setQuery] = useState('');
  const [selectedIndex, setSelectedIndex] = useState(0);
  const dialogFocus = useDialogFocus<HTMLElement>(true, onClose);
  const filteredItems = useMemo(() => {
    const needle = query.trim().toLocaleLowerCase('zh-CN');
    if (!needle) return items.slice(0, 12);
    return items
      .filter((item) => `${item.label} ${item.hint}`.toLocaleLowerCase('zh-CN').includes(needle))
      .slice(0, 20);
  }, [items, query]);

  useEffect(() => setSelectedIndex(0), [query]);

  const choose = (item?: CommandItem) => {
    if (!item) return;
    item.run();
    onClose();
  };

  return (
    <div className="fixed inset-0 z-[80] flex items-start justify-center bg-black/25 px-4 pt-[12vh]" role="presentation" onMouseDown={onClose}>
      <section
        ref={dialogFocus.containerRef}
        role="dialog"
        aria-modal="true"
        aria-label={ui('搜索命令、会话、任务、工件或模型')}
        className="w-full max-w-2xl overflow-hidden rounded-xl border border-[#d8cfc3] bg-[#fbfaf7] shadow-2xl"
        onKeyDown={dialogFocus.onKeyDown}
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="flex items-center gap-3 border-b border-[#e1d9cd] px-4">
          <Search size={17} className="text-[#7a7065]" />
          <input
            data-dialog-initial-focus
            autoFocus
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'ArrowDown') {
                event.preventDefault();
                setSelectedIndex((index) => Math.min(index + 1, Math.max(0, filteredItems.length - 1)));
              }
              if (event.key === 'ArrowUp') {
                event.preventDefault();
                setSelectedIndex((index) => Math.max(0, index - 1));
              }
              if (event.key === 'Enter') {
                event.preventDefault();
                choose(filteredItems[selectedIndex]);
              }
              if (event.key === 'Escape') {
                event.stopPropagation();
                onClose();
              }
            }}
            placeholder={ui('输入关键词，例如：任务、README、模型')}
            className="h-12 min-w-0 flex-1 bg-transparent text-sm outline-none"
          />
          <kbd className="rounded border border-[#d8cfc3] bg-[#eee7de] px-2 py-1 text-[10px] text-[#7a7065]">ESC</kbd>
        </div>
        <div className="max-h-[26rem] overflow-auto p-2">
          {filteredItems.map((item, index) => (
            <button
              key={item.id}
              onMouseEnter={() => setSelectedIndex(index)}
              onClick={() => choose(item)}
              className={`flex w-full items-center justify-between rounded-lg px-3 py-2.5 text-left ${selectedIndex === index ? 'bg-[#e8ddd0]' : 'hover:bg-[#f1e9df]'}`}
            >
              <span className="truncate text-sm font-medium">{item.label}</span>
              <span className="ml-4 truncate text-xs text-[#8b8176]">{item.hint}</span>
            </button>
          ))}
          {filteredItems.length === 0 && (
            <div className="px-3 py-8 text-center text-sm text-[#8b8176]">{ui('没有匹配结果')}</div>
          )}
        </div>
      </section>
    </div>
  );
}

interface InspectorProps {
  apiBase: string;
  sessionId: string;
  selectedArtifactPath: string;
  onSelectArtifact: (path: string) => void;
  onCreateTask: (title: string) => void;
  onPatchTask: (task: Task, patch: Partial<Task>) => void;
  onCreateMerge: (artifactPath: string, rationale: string) => void;
  onVoteMerge: (proposal: MergeProposal, approve: boolean) => void;
  onReloadArtifacts: () => void;
  onUpdateSettings: ReturnType<typeof useSessionStore.getState>['updateSettings'];
  onCreateTool: (payload: Partial<ToolItem>) => void;
  onDeleteTool: (tool: ToolItem) => void;
  onInvokeTool: (tool: ToolItem, caller?: string) => void;
  onCreateModel: (payload: Partial<ModelConfig> & { api_key?: string }) => Promise<void>;
  onDeleteModel: (model: ModelConfig) => Promise<void>;
  exportSession?: (fmt?: 'markdown' | 'json') => void;
  agentRuns: AgentRun[];
  gitRepository: GitRepository | null;
  verificationCommands: string;
  onVerificationCommandsChange: (value: string) => void;
  onSelectGitRepository: () => void;
  onDecideWorkspaceCandidate: (
    candidate: AgentWorkspaceCandidate,
    decision: 'apply' | 'reject',
  ) => void;
  onRetryAgentRun: (run: AgentRun) => void;
  onInterruptAgentStep: (run: AgentRun, stepId: string) => void;
  onUpdateAgentPlan: (
    run: AgentRun,
    goalSpec: {
      objective: string;
      success_criteria: string[];
      constraints: string[];
      deliverable: string;
    },
    planSteps: Array<{
      id: string;
      title: string;
      description: string;
      assigned_model?: string | null;
      role: string;
      dependencies: string[];
      success_criteria: string[];
    }>,
    changeSummary: string,
  ) => boolean;
  onConfirmAgentPlan: (run: AgentRun) => void;
  onRejectAgentPlan: (run: AgentRun) => void;
  onClose?: () => void;
}

function InspectorPanel(props: InspectorProps) {
  const {
    activePanel,
    setActivePanel,
    artifacts,
    mergeProposals,
    tools,
    toolRuns,
    modelConfigs,
    settings: workspaceSettings,
    providers,
  } = useSessionStore();
  const { t, ui } = useI18n();
  const activePanelLabel = activePanel ? ({
    记忆: t('memory'), 资料: t('knowledge'), 任务: t('tasks'), 工件: t('artifacts'), 合并: t('changes'),
    工具: t('tools'), 插件: t('plugins'), 连接: t('connections'), 电脑: t('computer'), 模型: t('models'),
    运行: t('runs'), 设置: t('settings'),
  } as Record<string, string>)[activePanel] : ui('检查器');
  const [artifactContent, setArtifactContent] = useState('');
  const [artifactPreview, setArtifactPreview] = useState<ArtifactPreview | null>(null);
  const [artifactVersions, setArtifactVersions] = useState<ArtifactVersion[]>([]);
  const [artifactView, setArtifactView] = useState<'preview' | 'edit' | 'diff' | 'history'>('preview');
  const [artifactDiff, setArtifactDiff] = useState('');
  const [artifactStatus, setArtifactStatus] = useState('');
  const [taskTitle, setTaskTitle] = useState('');
  const [commitMessage, setCommitMessage] = useState('中文工作台更新');
  const [toolName, setToolName] = useState('');
  const [toolDescription, setToolDescription] = useState('');
  const [toolTemplate, setToolTemplate] = useState('工具 ${name} 已由 ${input} 调用。');
  const [modelName, setModelName] = useState('');
  const [modelType, setModelType] = useState<ModelConfig['type']>('openai_compat');
  const [modelId, setModelId] = useState('');
  const [modelBaseUrl, setModelBaseUrl] = useState('');
  const [modelApiKey, setModelApiKey] = useState('');
  const [modelPersona, setModelPersona] = useState('');
  const [modelPolicy, setModelPolicy] = useState<ModelConfig['participation_policy']>('always');
  const [modelRuntime, setModelRuntime] = useState<'external' | 'llama_cpp'>('external');
  const [modelPath, setModelPath] = useState('');
  const [modelExecutable, setModelExecutable] = useState('/usr/local/bin/llama-server');
  const [modelRuntimeArgs, setModelRuntimeArgs] = useState('--n-gpu-layers\n-1\n--ctx-size\n32768\n--flash-attn\non');
  const [editingModelName, setEditingModelName] = useState<string | null>(null);
  const [modelVerifyStatus, setModelVerifyStatus] = useState('未验证');
  const [knowledgeAttachments, setKnowledgeAttachments] = useState<KnowledgeAttachment[]>([]);
  const [knowledgeQuery, setKnowledgeQuery] = useState('');
  const [knowledgeResults, setKnowledgeResults] = useState<KnowledgeSearchHit[]>([]);
  const [knowledgeStatus, setKnowledgeStatus] = useState('');
  const [knowledgeProgress, setKnowledgeProgress] = useState(0);
  const [knowledgePreview, setKnowledgePreview] = useState<KnowledgePreview | null>(null);
  const [sessionMemory, setSessionMemory] = useState<SessionMemory | null>(null);
  const [memoryStatus, setMemoryStatus] = useState('');
  const [memoryDraft, setMemoryDraft] = useState({
    project_note: '', decisions: '', preferences: '', open_items: '',
  });
  const [mcpConnections, setMcpConnections] = useState<MCPConnection[]>([]);
  const [mcpStatus, setMcpStatus] = useState('');
  const [mcpName, setMcpName] = useState('');
  const [mcpTransport, setMcpTransport] = useState<MCPConnection['transport']>('stdio');
  const [mcpEndpoint, setMcpEndpoint] = useState('');
  const [mcpArgs, setMcpArgs] = useState('');
  const [mcpAuthType, setMcpAuthType] = useState<MCPConnection['auth_type']>('none');
  const [mcpToken, setMcpToken] = useState('');

  const artifactPathUrl = (path: string) => path.split('/').map(encodeURIComponent).join('/');

  const loadArtifactDetail = async () => {
    if (!props.selectedArtifactPath) return;
    const encodedPath = artifactPathUrl(props.selectedArtifactPath);
    try {
      const [preview, versions] = await Promise.all([
        fetchJson<ArtifactPreview>(`${props.apiBase}/artifacts/${encodedPath}/preview?session_id=${encodeURIComponent(props.sessionId)}`),
        fetchJson<ArtifactVersion[]>(`${props.apiBase}/artifacts/${encodedPath}/history?session_id=${encodeURIComponent(props.sessionId)}`),
      ]);
      setArtifactPreview(preview);
      setArtifactVersions(versions);
      setArtifactContent(['markdown', 'isolated_html', 'code'].includes(preview.mode) ? preview.content : '');
      setArtifactView('preview');
      setArtifactDiff('');
      setArtifactStatus(preview.truncated ? '预览已按安全上限截断' : '工件已加载');
    } catch (error) {
      setArtifactPreview(null);
      setArtifactVersions([]);
      setArtifactContent('');
      setArtifactStatus(`工件加载失败：${String(error)}`);
    }
  };

  useEffect(() => {
    if (activePanel === '工件') void loadArtifactDetail();
  }, [activePanel, props.apiBase, props.selectedArtifactPath, props.sessionId]);

  const loadKnowledge = async () => {
    try {
      setKnowledgeProgress(20);
      setKnowledgeAttachments(await fetchJson<KnowledgeAttachment[]>(
        `${props.apiBase}/knowledge/${props.sessionId}/attachments`,
      ));
      setKnowledgeProgress(100);
    } catch (error) {
      setKnowledgeStatus(`资料加载失败：${String(error)}`);
      setKnowledgeProgress(0);
    }
  };

  useEffect(() => {
    if (activePanel === '资料') void loadKnowledge();
  }, [activePanel, props.apiBase, props.sessionId]);

  const loadMcpConnections = async () => {
    try {
      setMcpConnections(await fetchJson<MCPConnection[]>(`${props.apiBase}/mcp-connections`));
      setMcpStatus('连接器已同步');
    } catch (error) {
      setMcpStatus(`连接器加载失败：${String(error)}`);
    }
  };

  useEffect(() => {
    if (activePanel === '连接') void loadMcpConnections();
  }, [activePanel, props.apiBase]);

  const createMcpConnection = async () => {
    if (!mcpName.trim() || !mcpEndpoint.trim()) {
      setMcpStatus('名称和命令/URL 不能为空');
      return;
    }
    setMcpStatus('正在连接并发现工具...');
    try {
      const payload = {
        name: mcpName.trim(),
        transport: mcpTransport,
        command: mcpTransport === 'stdio' ? mcpEndpoint.trim() : undefined,
        args: mcpTransport === 'stdio' ? mcpArgs.split('\n').map((item) => item.trim()).filter(Boolean) : [],
        url: mcpTransport === 'streamable_http' ? mcpEndpoint.trim() : undefined,
        auth_type: mcpTransport === 'stdio' ? 'none' : mcpAuthType,
        token: mcpToken || undefined,
      };
      const created = await fetchJson<MCPConnection>(`${props.apiBase}/mcp-connections`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(payload),
      });
      setMcpConnections((items) => [created, ...items.filter((item) => item.id !== created.id)]);
      setMcpName(''); setMcpEndpoint(''); setMcpArgs(''); setMcpToken('');
      setMcpStatus(created.status === 'ready' ? `已发现 ${created.tools.length} 个 MCP 工具` : `连接未就绪：${created.error || '请检查配置'}`);
    } catch (error) {
      setMcpStatus(`创建失败：${String(error)}`);
    }
  };

  const patchMcpConnection = async (connection: MCPConnection, patch: Record<string, unknown>) => {
    try {
      const updated = await fetchJson<MCPConnection>(`${props.apiBase}/mcp-connections/${connection.id}`, {
        method: 'PATCH', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(patch),
      });
      setMcpConnections((items) => items.map((item) => item.id === updated.id ? updated : item));
      setMcpStatus('连接器权限已更新');
    } catch (error) {
      setMcpStatus(`更新失败：${String(error)}`);
    }
  };

  const refreshMcpConnection = async (connection: MCPConnection) => {
    setMcpStatus(`正在连接 ${connection.name}...`);
    try {
      const updated = await fetchJson<MCPConnection>(`${props.apiBase}/mcp-connections/${connection.id}/refresh`, { method: 'POST' });
      setMcpConnections((items) => items.map((item) => item.id === updated.id ? updated : item));
      setMcpStatus(`已发现 ${updated.tools.length} 个 MCP 工具`);
    } catch (error) {
      setMcpStatus(`连接失败：${String(error)}`);
      await loadMcpConnections();
    }
  };

  const deleteMcpConnection = async (connection: MCPConnection) => {
    if (!window.confirm(`删除 MCP 连接器“${connection.name}”及其本机凭据引用吗？`)) return;
    await fetchJson(`${props.apiBase}/mcp-connections/${connection.id}`, { method: 'DELETE' });
    setMcpConnections((items) => items.filter((item) => item.id !== connection.id));
    setMcpStatus('连接器已删除');
  };

  const authorizeMcpOAuth = async (connection: MCPConnection) => {
    setMcpStatus(`正在准备 ${connection.name} 的 OAuth 授权...`);
    try {
      const callbackUrl = new URL(`${props.apiBase}/mcp-connections/oauth/callback/${connection.id}`).toString();
      const result = await fetchJson<{ authorization_url: string }>(`${props.apiBase}/mcp-connections/${connection.id}/oauth/start`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ callback_url: callbackUrl }),
      });
      if (window.aetherRuntime?.openExternal) await window.aetherRuntime.openExternal(result.authorization_url);
      else window.open(result.authorization_url, '_blank', 'noopener,noreferrer');
      setMcpStatus('请在浏览器完成授权；令牌将直接保存到系统密钥环');
      for (let attempt = 0; attempt < 60; attempt += 1) {
        await new Promise((resolve) => window.setTimeout(resolve, 1000));
        const rows = await fetchJson<MCPConnection[]>(`${props.apiBase}/mcp-connections`);
        setMcpConnections(rows);
        const current = rows.find((item) => item.id === connection.id);
        if (current?.status === 'ready') {
          setMcpStatus(`OAuth 授权完成，已发现 ${current.tools.length} 个工具`);
          return;
        }
        if (current?.status === 'error') {
          setMcpStatus(current.error || 'OAuth 授权失败');
          return;
        }
      }
      setMcpStatus('OAuth 授权仍在等待，可稍后点击“刷新工具”检查');
    } catch (error) {
      setMcpStatus(`OAuth 授权启动失败：${String(error)}`);
    }
  };

  const loadMemory = async () => {
    try {
      setMemoryStatus('正在载入本机记忆...');
      const memory = await fetchJson<SessionMemory>(`${props.apiBase}/memory/${props.sessionId}`);
      setSessionMemory(memory);
      setMemoryDraft({
        project_note: memory.project_note || '',
        decisions: memory.decisions.join('\n'),
        preferences: memory.preferences.join('\n'),
        open_items: memory.open_items.join('\n'),
      });
      setMemoryStatus('记忆已同步');
    } catch (error) {
      setMemoryStatus(`记忆加载失败：${String(error)}`);
    }
  };

  useEffect(() => {
    if (activePanel === '记忆') void loadMemory();
  }, [activePanel, props.apiBase, props.sessionId]);

  const saveMemory = async () => {
    const list = (value: string) => value.split('\n').map((item) => item.trim()).filter(Boolean);
    try {
      setMemoryStatus('正在保存...');
      const memory = await fetchJson<SessionMemory>(`${props.apiBase}/memory/${props.sessionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          project_note: memoryDraft.project_note,
          decisions: list(memoryDraft.decisions),
          preferences: list(memoryDraft.preferences),
          open_items: list(memoryDraft.open_items),
        }),
      });
      setSessionMemory(memory);
      setMemoryStatus('记忆已保存，下一次模型调用会使用更新内容');
    } catch (error) {
      setMemoryStatus(`记忆保存失败：${String(error)}`);
    }
  };

  const searchKnowledge = async () => {
    if (!knowledgeQuery.trim()) return;
    setKnowledgeStatus('正在检索...');
    setKnowledgeProgress(25);
    try {
      setKnowledgeProgress(55);
      const rows = await fetchJson<KnowledgeSearchHit[]>(
        `${props.apiBase}/knowledge/${props.sessionId}/search?q=${encodeURIComponent(knowledgeQuery.trim())}&limit=20`,
      );
      setKnowledgeResults(rows);
      setKnowledgeStatus(rows.length ? `混合检索完成：找到 ${rows.length} 个来源片段` : '没有匹配内容');
      setKnowledgeProgress(100);
    } catch (error) {
      setKnowledgeStatus(`检索失败：${String(error)}`);
      setKnowledgeProgress(0);
    }
  };

  const previewKnowledge = async (attachment: KnowledgeAttachment) => {
    setKnowledgeStatus(`正在生成安全预览：${attachment.name}`);
    setKnowledgeProgress(30);
    try {
      const preview = await fetchJson<KnowledgePreview>(
        `${props.apiBase}/knowledge/${props.sessionId}/attachments/${attachment.id}/preview`,
      );
      setKnowledgeProgress(70);
      if (preview.kind === 'image') {
        const response = await fetch(
          `${props.apiBase}/knowledge/${props.sessionId}/attachments/${attachment.id}/content`,
          withApiAuth(),
        );
        if (!response.ok) throw new Error(`HTTP ${response.status}`);
        preview.imageUrl = URL.createObjectURL(await response.blob());
      }
      setKnowledgePreview((current) => {
        if (current?.imageUrl) URL.revokeObjectURL(current.imageUrl);
        return preview;
      });
      setKnowledgeStatus('安全预览已生成');
      setKnowledgeProgress(100);
    } catch (error) {
      setKnowledgeStatus(`预览失败：${String(error)}`);
      setKnowledgeProgress(0);
    }
  };

  const deleteKnowledge = async (attachment: KnowledgeAttachment) => {
    if (!window.confirm(`从项目资料库删除“${attachment.name}”吗？历史消息中的引用名称会保留。`)) return;
    await fetchJson(`${props.apiBase}/knowledge/${props.sessionId}/attachments/${attachment.id}`, { method: 'DELETE' });
    setKnowledgeResults((rows) => rows.filter((row) => row.attachment_id !== attachment.id));
    if (knowledgePreview?.attachment.id === attachment.id) {
      if (knowledgePreview.imageUrl) URL.revokeObjectURL(knowledgePreview.imageUrl);
      setKnowledgePreview(null);
    }
    await loadKnowledge();
  };

  const saveArtifact = async () => {
    if (!props.selectedArtifactPath) return;
    try {
      await fetchJson(`${props.apiBase}/artifacts/write`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: props.sessionId,
          path: props.selectedArtifactPath,
          content: artifactContent,
          commit_message: commitMessage,
          author: 'user',
          status: 'ready',
          parent_version: artifactVersions[0]?.version,
        }),
      });
      setArtifactStatus('新版本已保存');
      props.onReloadArtifacts();
      await loadArtifactDetail();
    } catch (error) {
      setArtifactStatus(`保存失败：${String(error)}`);
    }
  };

  const compareArtifactVersion = async (version: number) => {
    if (!props.selectedArtifactPath || !artifactVersions[0]) return;
    try {
      const encodedPath = artifactPathUrl(props.selectedArtifactPath);
      const data = await fetchJson<{ diff: string }>(
        `${props.apiBase}/artifacts/${encodedPath}/diff?session_id=${encodeURIComponent(props.sessionId)}&from_version=${version}&to_version=${artifactVersions[0].version}`,
      );
      setArtifactDiff(data.diff || '两个版本内容相同。');
      setArtifactView('diff');
    } catch (error) {
      setArtifactStatus(`对比失败：${String(error)}`);
    }
  };

  const restoreArtifactVersion = async (version: number) => {
    if (!props.selectedArtifactPath || !window.confirm(`将当前工件恢复到版本 v${version}？恢复会创建一个新版本，不会删除历史。`)) return;
    try {
      const encodedPath = artifactPathUrl(props.selectedArtifactPath);
      await fetchJson(`${props.apiBase}/artifacts/${encodedPath}/restore`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ session_id: props.sessionId, version, author: 'user' }),
      });
      setArtifactStatus(`已恢复 v${version}，并创建新版本`);
      props.onReloadArtifacts();
      await loadArtifactDetail();
    } catch (error) {
      setArtifactStatus(`恢复失败：${String(error)}`);
    }
  };

  const exportArtifact = async (format: 'docx' | 'xlsx' | 'pptx' | 'md' | 'original') => {
    if (!props.selectedArtifactPath) return;
    try {
      const encodedPath = artifactPathUrl(props.selectedArtifactPath);
      const response = await fetch(
        `${props.apiBase}/artifacts/${encodedPath}/export?session_id=${encodeURIComponent(props.sessionId)}&format=${format}`,
        withApiAuth(),
      );
      if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `HTTP ${response.status}`);
      const disposition = response.headers.get('content-disposition') || '';
      const filename = disposition.match(/filename="([^"]+)"/)?.[1] || `artifact.${format === 'original' ? 'bin' : format}`;
      const url = URL.createObjectURL(await response.blob());
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
      setArtifactStatus(`已导出 ${filename}`);
    } catch (error) {
      setArtifactStatus(`导出失败：${String(error)}`);
    }
  };

  const editModel = (model: ModelConfig) => {
    setEditingModelName(model.name);
    setModelName(model.name);
    setModelType(model.type);
    setModelId(model.model);
    setModelBaseUrl(model.base_url ?? '');
    setModelApiKey('');
    setModelPersona(model.persona ?? '');
    setModelPolicy(model.participation_policy);
    setModelRuntime(model.local_runtime || 'external');
    setModelPath(model.model_path || '');
    setModelExecutable(model.runtime_executable || '/usr/local/bin/llama-server');
    setModelRuntimeArgs((model.runtime_args || []).join('\n'));
    setModelVerifyStatus('已载入，修改后可更新并验证');
  };

  const verifyModelConnection = async (name: string) => {
    const data = await fetchJson<{ ok?: boolean; message?: string; sample?: string }>(
      `${props.apiBase}/models/${encodeURIComponent(name)}/verify`,
      { method: 'POST' },
    );
    const suffix = data.sample ? `，回复：${data.sample}` : '';
    setModelVerifyStatus(data.ok ? `验证通过：${data.message}${suffix}` : `验证失败：${data.message || '真实对话检测未通过'}`);
    return Boolean(data.ok);
  };

  const submitModel = async () => {
    if (!modelName.trim() || !modelId.trim()) {
      setModelVerifyStatus('名称和模型 ID 不能为空');
      return;
    }

    const payload = {
      name: modelName.trim(),
      type: modelType,
      model: modelId.trim(),
      base_url: modelBaseUrl || undefined,
      api_key: modelApiKey || undefined,
      persona: modelPersona || undefined,
      participation_policy: modelPolicy,
      max_tokens: 8192,
      token_limit: 128000,
      local_runtime: modelType === 'local' ? modelRuntime : 'external',
      model_path: modelType === 'local' && modelRuntime === 'llama_cpp' ? modelPath || undefined : undefined,
      runtime_executable: modelType === 'local' && modelRuntime === 'llama_cpp' ? modelExecutable || undefined : undefined,
      runtime_args: modelType === 'local' && modelRuntime === 'llama_cpp'
        ? modelRuntimeArgs.split('\n').map((item) => item.trim()).filter(Boolean)
        : [],
      startup_timeout: 300,
    };

    try {
      setModelVerifyStatus('正在验证...');
      await props.onCreateModel(payload);
      if (editingModelName && editingModelName !== payload.name) {
        await props.onDeleteModel({ name: editingModelName } as ModelConfig);
      }
      await verifyModelConnection(payload.name);
      setEditingModelName(null);
      setModelName('');
      setModelId('');
      setModelBaseUrl('');
      setModelApiKey('');
      setModelPersona('');
      setModelPolicy('always');
      setModelRuntime('external');
      setModelPath('');
    } catch (error) {
      setModelVerifyStatus(`验证失败：${String(error)}`);
    }
  };

  const toggleModelParticipation = async (model: ModelConfig) => {
    const participation_policy = model.participation_policy === 'always' ? 'mentioned' : 'always';
    try {
      setModelVerifyStatus(participation_policy === 'always' ? '正在启用模型...' : '正在暂停模型...');
      await props.onCreateModel({
        name: model.name,
        type: model.type,
        model: model.model,
        base_url: model.base_url,
        api_key_ref: model.api_key_ref,
        persona: model.persona,
        participation_policy,
        max_tokens: model.max_tokens,
        token_limit: model.token_limit,
        local_runtime: model.local_runtime,
        model_path: model.model_path,
        runtime_executable: model.runtime_executable,
        runtime_args: model.runtime_args,
        startup_timeout: model.startup_timeout,
      });
      setModelVerifyStatus(participation_policy === 'always' ? '模型已启用，将参与“全部模型”请求' : '模型已暂停，仅在被明确提及时参与');
    } catch (error) {
      setModelVerifyStatus(`状态更新失败：${String(error)}`);
    }
  };

  return (
    <aside aria-label={`${activePanelLabel} panel`} className="aether-inspector-shell flex h-full min-h-0 flex-col overflow-hidden bg-[#f2eadf]">
      <div className="border-b border-[#d8cfc3] p-3">
        <div className="flex items-center gap-2">
          <div className="grid min-w-0 flex-1 grid-cols-12 gap-1 rounded-lg bg-[#e4dbcf] p-1">
            {([
              ['记忆', t('memory')], ['资料', t('knowledge')], ['任务', t('tasks')], ['工件', t('artifacts')],
              ['合并', t('changes')], ['工具', t('tools')], ['插件', t('plugins')], ['连接', t('connections')],
              ['电脑', t('computer')], ['模型', t('models')], ['运行', t('runs')], ['设置', t('settings')],
            ] as const).map(([panel, label]) => (
              <button
                key={panel}
                onClick={() => setActivePanel(panel)}
                className={`h-8 rounded-md text-xs font-medium ${activePanel === panel ? 'bg-[#fbfaf7] shadow-sm' : 'text-[#786d62]'}`}
              >
                {label}
              </button>
            ))}
          </div>
          {props.onClose && (
            <button data-dialog-initial-focus onClick={props.onClose} className="h-8 rounded-md border border-[#b8a999] px-3 text-xs text-[#514a43]">
              {t('close')}
            </button>
          )}
        </div>
      </div>

      {activePanel === '记忆' && (
        <div className="aether-tab-panel min-h-0 flex-1 overflow-auto p-4">
          <div className="mb-4 flex items-start gap-3">
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#e5d9ca] text-[#684827]"><Brain size={17} /></div>
            <div>
              <h2 className="text-sm font-semibold">{ui('本机会话记忆')}</h2>
              <p className="mt-1 text-xs leading-5 text-[#7a7065]">决定和偏好只从用户明确表达中提取。所有内容按会话隔离，可在这里检查、修改或删除。</p>
            </div>
          </div>
          {memoryStatus && <div className="mb-3 rounded-md bg-[#e9e0d5] px-3 py-2 text-xs text-[#6e5d4b]" aria-live="polite">{ui(memoryStatus)}</div>}
          <div className="space-y-4">
            <label className="block">
              <span className="text-xs font-semibold">项目固定说明</span>
              <textarea value={memoryDraft.project_note} onChange={(event) => setMemoryDraft((value) => ({ ...value, project_note: event.target.value }))} placeholder="例如：本项目使用 TypeScript，所有危险操作必须逐次授权。" className="mt-1 min-h-20 w-full rounded-lg border border-[#d8cdbf] bg-white p-3 text-xs leading-5 outline-none" />
            </label>
            <label className="block">
              <span className="text-xs font-semibold">用户偏好</span>
              <span className="ml-2 text-[10px] text-[#897b6d]">每行一项</span>
              <textarea value={memoryDraft.preferences} onChange={(event) => setMemoryDraft((value) => ({ ...value, preferences: event.target.value }))} className="mt-1 min-h-20 w-full rounded-lg border border-[#d8cdbf] bg-white p-3 text-xs leading-5 outline-none" />
            </label>
            <label className="block">
              <span className="text-xs font-semibold">关键决定</span>
              <span className="ml-2 text-[10px] text-[#897b6d]">每行一项</span>
              <textarea value={memoryDraft.decisions} onChange={(event) => setMemoryDraft((value) => ({ ...value, decisions: event.target.value }))} className="mt-1 min-h-20 w-full rounded-lg border border-[#d8cdbf] bg-white p-3 text-xs leading-5 outline-none" />
            </label>
            <label className="block">
              <span className="text-xs font-semibold">未完成事项</span>
              <span className="ml-2 text-[10px] text-[#897b6d]">完成后可直接删除对应行</span>
              <textarea value={memoryDraft.open_items} onChange={(event) => setMemoryDraft((value) => ({ ...value, open_items: event.target.value }))} className="mt-1 min-h-20 w-full rounded-lg border border-[#d8cdbf] bg-white p-3 text-xs leading-5 outline-none" />
            </label>
            <div>
              <div className="mb-1 flex items-center justify-between text-xs font-semibold">
                <span>滚动摘要</span>
                <span className="font-normal text-[#897b6d]">最近 {sessionMemory?.message_count ?? 0} 条上下文窗口消息</span>
              </div>
              <pre className="max-h-48 overflow-auto whitespace-pre-wrap rounded-lg border border-[#d8cdbf] bg-[#f8f4ee] p-3 font-sans text-xs leading-5 text-[#5b5147]">{sessionMemory?.summary || '发送消息后自动生成。'}</pre>
            </div>
            <div className="flex gap-2">
              <button onClick={() => void saveMemory()} className="rounded-lg bg-[#2f2a24] px-4 py-2 text-xs font-semibold text-white"><Save size={14} className="mr-1 inline" />保存记忆</button>
              <button onClick={() => setMemoryDraft({ project_note: '', decisions: '', preferences: '', open_items: '' })} className="rounded-lg border border-[#cdbfb0] bg-white px-4 py-2 text-xs font-semibold text-[#744438]">清空编辑内容</button>
            </div>
          </div>
        </div>
      )}

      {activePanel === '资料' && (
        <div className="aether-tab-panel min-h-0 flex-1 overflow-auto p-4">
          <div className="mb-4">
            <h2 className="text-sm font-semibold">{ui('项目资料库')}</h2>
            <p className="mt-1 text-xs text-[#7a7065]">FTS5 + 本地向量 + 重排；PDF/Office 仅显示抽取文本，图片仅显示安全重编码副本。</p>
            <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-[#dfd5c9]" aria-label={`资料处理进度 ${knowledgeProgress}%`}>
              <div className="h-full bg-[#8d5f35] transition-all duration-300" style={{ width: `${knowledgeProgress}%` }} />
            </div>
          </div>
          <div className="mb-4 flex gap-2">
            <input
              value={knowledgeQuery}
              onChange={(event) => setKnowledgeQuery(event.target.value)}
              onKeyDown={(event) => {
                if (event.key === 'Enter') void searchKnowledge();
              }}
              placeholder="搜索文件正文、表格或幻灯片"
              className="h-10 min-w-0 flex-1 rounded-lg border border-[#ded6ca] bg-white px-3 text-sm outline-none"
            />
            <button onClick={() => void searchKnowledge()} className="flex h-10 items-center gap-1 rounded-lg bg-[#2f2a24] px-3 text-sm font-medium text-white">
              <Search size={15} />检索
            </button>
          </div>
          {knowledgeStatus && <div className="mb-3 text-xs text-[#7a7065]" aria-live="polite">{ui(knowledgeStatus)}</div>}
          {knowledgeResults.length > 0 && (
            <div className="mb-5 space-y-2">
              {knowledgeResults.map((result, index) => (
                <article key={`${result.attachment_id}-${result.locator}-${index}`} className="rounded-lg border border-[#dfd4c7] bg-white p-3">
                  <div className="flex items-center gap-2 text-xs font-semibold text-[#6b4425]">
                    <button className="truncate text-left hover:underline" onClick={() => {
                      const attachment = knowledgeAttachments.find((item) => item.id === result.attachment_id);
                      if (attachment) void previewKnowledge(attachment);
                    }}>{result.citation}</button>
                    <span className="ml-auto shrink-0 rounded bg-[#eee4d8] px-1.5 py-0.5 text-[10px]">{Math.round((result.score ?? 0) * 100)}%</span>
                  </div>
                  <div className="mt-1 text-[10px] text-[#8a7764]">
                    {(result.match_types ?? ['全文']).map((item) => ui(item)).join(' + ')}
                  </div>
                  <p className="mt-2 line-clamp-4 whitespace-pre-wrap text-xs leading-5 text-[#5f554b]">{result.content}</p>
                </article>
              ))}
            </div>
          )}
          {knowledgePreview && (
            <section className="mb-5 rounded-lg border border-[#cdbba8] bg-white p-3" aria-label="资料安全预览">
              <div className="mb-3 flex items-center gap-2">
                <ShieldCheck size={15} className="text-[#477257]" />
                <div className="min-w-0 flex-1 truncate text-sm font-semibold">安全预览 · {knowledgePreview.attachment.name}</div>
                <button aria-label="关闭预览" onClick={() => {
                  if (knowledgePreview.imageUrl) URL.revokeObjectURL(knowledgePreview.imageUrl);
                  setKnowledgePreview(null);
                }}><X size={15} /></button>
              </div>
              {knowledgePreview.kind === 'image' && knowledgePreview.imageUrl && (
                <img src={knowledgePreview.imageUrl} alt={knowledgePreview.attachment.name} className="max-h-80 w-full rounded-md bg-[#eee8df] object-contain" />
              )}
              {knowledgePreview.kind === 'text' && (
                <div className="max-h-80 space-y-3 overflow-auto">
                  {knowledgePreview.chunks.map((chunk, index) => (
                    <div key={`${chunk.locator}-${index}`} className="rounded-md bg-[#f5efe7] p-2">
                      <div className="text-[10px] font-semibold text-[#7c5735]">{chunk.locator}</div>
                      <pre className="mt-1 whitespace-pre-wrap font-sans text-xs leading-5 text-[#554a40]">{chunk.content}</pre>
                    </div>
                  ))}
                  {!knowledgePreview.chunks.length && <div className="text-xs text-[#7a7065]">此文件没有可安全显示的文本。</div>}
                  {knowledgePreview.truncated && <div className="text-xs text-[#8a6240]">预览已截断，检索索引仍包含其余片段。</div>}
                </div>
              )}
            </section>
          )}
          <div className="space-y-2">
            {knowledgeAttachments.map((attachment) => (
              <div key={attachment.id} className="flex items-center gap-3 rounded-lg border border-[#dfd4c7] bg-[#fffdf9] p-3">
                <FileText size={16} className="shrink-0 text-[#785331]" />
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium">{attachment.name}</div>
                  <div className="mt-0.5 text-xs text-[#84796d]">{attachment.chunk_count} 个片段 · {(attachment.size_bytes / 1024).toFixed(1)} KB</div>
                </div>
                <button aria-label={`预览 ${attachment.name}`} onClick={() => void previewKnowledge(attachment)} className="rounded border border-[#d5c6b6] px-2 py-1 text-xs text-[#654927] hover:bg-[#eee5da]">预览</button>
                <button aria-label={`删除 ${attachment.name}`} onClick={() => void deleteKnowledge(attachment)} className="rounded p-1.5 text-[#8c4a3d] hover:bg-[#f3e3df]">
                  <Trash2 size={15} />
                </button>
              </div>
            ))}
            {knowledgeAttachments.length === 0 && <div className="rounded-lg border border-dashed border-[#d6cbbd] p-8 text-center text-sm text-[#7a7065]">尚未上传项目资料</div>}
          </div>
        </div>
      )}

      {activePanel === '任务' && (
        <div className="aether-tab-panel aether-panel-scroll min-h-0 flex-1 overflow-auto p-4">
          <div className="mb-3 flex gap-2">
            <input
              value={taskTitle}
              onChange={(event) => setTaskTitle(event.target.value)}
              placeholder={ui('新建任务')}
              className="h-10 min-w-0 flex-1 rounded-lg border border-[#ded6ca] bg-white px-3 text-sm outline-none"
            />
            <button
              onClick={() => {
                if (!taskTitle.trim()) return;
                props.onCreateTask(taskTitle.trim());
                setTaskTitle('');
              }}
              className="flex h-10 items-center gap-1 rounded-lg bg-[#2f2a24] px-3 text-sm font-medium text-white"
            >
              <Plus size={15} />
              {ui('添加')}
            </button>
          </div>
          <TaskBoard onPatchTask={props.onPatchTask} />
        </div>
      )}

      {activePanel === '工件' && (
        <div className="aether-tab-panel grid min-h-0 flex-1 overflow-hidden grid-rows-[12rem_minmax(0,1fr)]">
          <div className="overflow-auto border-b border-[#ded6ca] p-4">
            <div className="mb-2 flex items-center gap-2 text-sm font-semibold">
              <Archive size={16} />
              {ui('文件树')}
            </div>
            <div className="space-y-2">
              {artifacts.map((artifact) => (
                <button
                  key={artifact.path}
                  onClick={() => props.onSelectArtifact(artifact.path)}
                  className={`w-full rounded-lg border p-3 text-left text-sm ${props.selectedArtifactPath === artifact.path ? 'border-[#8d5f35] bg-white' : 'border-[#e1d9cd] bg-[#fbfaf7]'}`}
                >
                  <div className="flex items-center gap-2 font-medium">
                    <FileText size={14} />
                    {artifact.path}
                  </div>
                  <div className="mt-1 text-xs text-[#7a7065]">
                    {artifact.mime_type || artifact.kind} · v{artifact.current_version || 1} · {artifact.current_hash.slice(0, 7)}
                  </div>
                </button>
              ))}
            </div>
          </div>
          <div className="flex min-h-0 flex-col p-4">
            <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
              <div>
                <div className="truncate text-sm font-semibold">{props.selectedArtifactPath}</div>
                <div className="mt-0.5 text-[11px] text-[#82776c]">{artifactPreview?.mime_type || ui('未知类型')} · {ui(artifactStatus)}</div>
              </div>
              <div className="flex flex-wrap gap-1">
                {(['preview', 'edit', 'history'] as const).map((view) => (
                  <button
                    key={view}
                    type="button"
                    disabled={view === 'edit' && !artifactPreview?.mode.match(/markdown|isolated_html|code/)}
                    onClick={() => setArtifactView(view)}
                    className={`rounded-md border px-2 py-1 text-xs disabled:cursor-not-allowed disabled:opacity-40 ${artifactView === view ? 'border-[#8d5f35] bg-[#efe2d5]' : 'border-[#ded6ca]'}`}
                  >
                    {view === 'preview' ? '预览' : view === 'edit' ? '编辑' : '版本'}
                  </button>
                ))}
                <button onClick={() => props.onCreateMerge(props.selectedArtifactPath, '请求根据两票规则合并当前工件更新。')} className="rounded-md border border-[#ded6ca] px-2 py-1 text-xs">
                  提交变更
                </button>
              </div>
            </div>

            {artifactView === 'preview' && (
              <div data-testid="artifact-preview" className="min-h-0 flex-1 overflow-auto rounded-lg border border-[#ded6ca] bg-white p-3 text-sm">
                {artifactPreview?.mode === 'markdown' && <SafeMarkdown content={artifactPreview.content} />}
                {artifactPreview?.mode === 'isolated_html' && (
                  <iframe
                    title="隔离工件预览"
                    sandbox=""
                    referrerPolicy="no-referrer"
                    className="h-full min-h-64 w-full border-0"
                    srcDoc={`<!doctype html><meta http-equiv="Content-Security-Policy" content="default-src 'none'; script-src 'none'; connect-src 'none'; img-src data:; style-src 'unsafe-inline'; font-src 'none'; media-src 'none'; object-src 'none'; frame-src 'none'; child-src 'none'; form-action 'none'; base-uri 'none'; navigate-to 'none'">${artifactPreview.content}`}
                  />
                )}
                {(artifactPreview?.mode === 'code' || artifactPreview?.mode === 'office_summary') && (
                  <pre className="whitespace-pre-wrap break-words font-mono text-xs leading-5">{artifactPreview.content}</pre>
                )}
                {artifactPreview?.mode === 'download_only' && <p className="text-[#74695e]">此类型不允许内嵌预览，请仅导出后使用可信应用打开。</p>}
              </div>
            )}

            {artifactView === 'edit' && (
              <textarea
                aria-label="工件内容"
                value={artifactContent}
                onChange={(event) => setArtifactContent(event.target.value)}
                className="min-h-0 flex-1 resize-none rounded-lg border border-[#ded6ca] bg-[#fffdf9] p-3 font-mono text-xs leading-5 outline-none"
              />
            )}

            {artifactView === 'diff' && (
              <pre data-testid="artifact-diff" className="min-h-0 flex-1 overflow-auto whitespace-pre-wrap rounded-lg border border-[#ded6ca] bg-[#28231f] p-3 font-mono text-xs leading-5 text-[#f6eee5]">{artifactDiff}</pre>
            )}

            {artifactView === 'history' && (
              <div className="min-h-0 flex-1 overflow-auto rounded-lg border border-[#ded6ca] bg-white">
                {artifactVersions.map((version) => (
                  <div key={version.id} className="flex items-center justify-between gap-3 border-b border-[#eee6dc] p-3 last:border-0">
                    <div className="min-w-0">
                      <div className="text-xs font-semibold">v{version.version} · {version.short_hash}</div>
                      <div className="mt-1 truncate text-[11px] text-[#7a7065]">{version.message} · {version.author} · {formatDateTime(version.created_at)}</div>
                    </div>
                    <div className="flex shrink-0 gap-1">
                      {version.version !== artifactVersions[0]?.version && <button onClick={() => void compareArtifactVersion(version.version)} className="rounded border border-[#ded6ca] px-2 py-1 text-[11px]">对比</button>}
                      {version.content_encoding === 'utf-8' && version.version !== artifactVersions[0]?.version && <button onClick={() => void restoreArtifactVersion(version.version)} className="rounded border border-[#b98d69] px-2 py-1 text-[11px] text-[#704520]">恢复</button>}
                    </div>
                  </div>
                ))}
              </div>
            )}

            <div className="mt-2 flex flex-wrap gap-2">
              {artifactView === 'edit' && (
                <>
                  <input aria-label="版本说明" value={commitMessage} onChange={(event) => setCommitMessage(event.target.value)} className="h-9 min-w-0 flex-1 rounded-lg border border-[#ded6ca] bg-white px-3 text-xs outline-none" />
                  <button onClick={() => void saveArtifact()} className="rounded-lg bg-[#2f2a24] px-3 text-sm font-medium text-white">保存新版本</button>
                </>
              )}
              <div className="ml-auto flex gap-1">
                {(artifactPreview?.mode === 'download_only' || artifactPreview?.mode === 'office_summary') && (
                  <button onClick={() => void exportArtifact('original')} className="rounded-md border border-[#ded6ca] px-2 py-1 text-[11px]">原文件</button>
                )}
                {(['docx', 'xlsx', 'pptx', 'md'] as const).map((format) => (
                  <button key={format} onClick={() => void exportArtifact(format)} className="rounded-md border border-[#ded6ca] px-2 py-1 text-[11px] uppercase">{format}</button>
                ))}
              </div>
            </div>
          </div>
        </div>
      )}

      {activePanel === '合并' && (
        <div className="aether-tab-panel aether-panel-scroll min-h-0 flex-1 overflow-auto p-4">
          <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
            <GitMerge size={16} />
            {ui('变更与合并')}
          </div>
          <div className="space-y-3">
            {mergeProposals.map((proposal) => (
              <div key={proposal.id} className="rounded-lg border border-[#ded6ca] bg-[#fbfaf7] p-3">
                <div className="flex items-start justify-between gap-3">
                  <div>
                    <div className="text-sm font-medium">{proposal.artifact_path}</div>
                    <div className="mt-1 text-xs leading-5 text-[#7a7065]">{proposal.rationale}</div>
                  </div>
                  <span className="rounded-md bg-[#ebe3d8] px-2 py-1 text-xs">{proposal.status}</span>
                </div>
                <div className="mt-3 flex items-center justify-between">
                  <div className="text-xs text-[#7a7065]">赞成 {Object.values(proposal.votes).filter(Boolean).length} / 2</div>
                  <div className="flex gap-2">
                    <button onClick={() => props.onVoteMerge(proposal, false)} className="rounded-md border border-[#ded6ca] px-2 py-1 text-xs">拒绝</button>
                    <button onClick={() => props.onVoteMerge(proposal, true)} className="rounded-md bg-[#2f2a24] px-2 py-1 text-xs text-white">赞成</button>
                  </div>
                </div>
              </div>
            ))}
            {mergeProposals.length === 0 && <div className="rounded-lg border border-dashed border-[#d6cbbd] p-6 text-center text-sm text-[#7a7065]">暂无合并提案</div>}
          </div>
        </div>
      )}

      {activePanel === '工具' && (
        <div className="aether-tab-panel grid min-h-0 flex-1 overflow-hidden grid-rows-[auto_minmax(0,1fr)_12rem]">
          <div className="border-b border-[#ded6ca] p-4">
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
              <PackageCheck size={16} />
              {ui('工具仓')}
            </div>
            <div className="space-y-2">
              <input
                value={toolName}
                onChange={(event) => setToolName(event.target.value)}
                placeholder={ui('工具名称，例如：生成周报')}
                className="h-9 w-full rounded-lg border border-[#ded6ca] bg-white px-3 text-sm outline-none"
              />
              <input
                value={toolDescription}
                onChange={(event) => setToolDescription(event.target.value)}
                placeholder={ui('工具说明')}
                className="h-9 w-full rounded-lg border border-[#ded6ca] bg-white px-3 text-sm outline-none"
              />
              <textarea
                value={toolTemplate}
                onChange={(event) => setToolTemplate(event.target.value)}
                className="h-20 w-full resize-none rounded-lg border border-[#ded6ca] bg-white px-3 py-2 text-xs leading-5 outline-none"
              />
              <button
                onClick={() => {
                  if (!toolName.trim()) return;
                  props.onCreateTool({
                    name: toolName.trim(),
                    description: toolDescription || '用户创建的内部工具',
                    category: 'custom',
                    owner: 'user',
                    kind: 'template',
                    input_schema: { type: 'object', properties: { input: { type: 'string' } } },
                    output_template: toolTemplate,
                    enabled: true,
                  });
                  setToolName('');
                  setToolDescription('');
                }}
                className="flex h-9 items-center gap-1 rounded-lg bg-[#2f2a24] px-3 text-sm font-medium text-white"
              >
                <Plus size={15} />
                {ui('添加到工具仓')}
              </button>
            </div>
          </div>

          <div className="overflow-auto p-4">
            <div className="mb-2 text-xs font-semibold text-[#7a7065]">{ui('所有模型可调用的工具')}</div>
            <div className="space-y-2">
              {tools.map((tool) => (
                <div key={tool.id} className="rounded-lg border border-[#ded6ca] bg-[#fbfaf7] p-3">
                  <div className="flex items-start justify-between gap-2">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold">{tool.name}</div>
                      <div className="mt-1 text-xs leading-5 text-[#7a7065]">{tool.description}</div>
                    </div>
                    <span className="rounded-md bg-[#ebe3d8] px-2 py-1 text-[11px]">{tool.owner}</span>
                  </div>
                  <div className="mt-3 flex items-center justify-between text-xs text-[#7a7065]">
                    <span>{tool.category} · {tool.kind}</span>
                    <div className="flex gap-2">
                      <button onClick={() => props.onInvokeTool(tool, 'user')} className="rounded-md bg-[#2f2a24] px-2 py-1 text-white">{ui('调用')}</button>
                      {tool.owner !== 'system' && (
                        <button onClick={() => props.onDeleteTool(tool)} className="rounded-md border border-[#d6cbbd] px-2 py-1 text-[#7a3b2e]">
                          <Trash2 size={13} />
                        </button>
                      )}
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          <div className="overflow-auto border-t border-[#ded6ca] p-4">
            <div className="mb-2 text-xs font-semibold text-[#7a7065]">{ui('最近调用')}</div>
            <div className="space-y-2">
              {toolRuns.slice(0, 5).map((run) => (
                <div key={run.id} className="rounded-lg border border-[#ded6ca] bg-[#fffdf9] p-2">
                  <div className="flex items-center justify-between text-xs font-medium">
                    <span>{run.tool_name}</span>
                    <span>{run.caller}</span>
                  </div>
                  <div className={`mt-1 line-clamp-2 text-xs leading-5 ${run.status === 'error' ? 'text-[#9b3328]' : 'text-[#7a7065]'}`}>
                    {run.status === 'error' ? run.error || ui('工具执行失败') : run.output}
                  </div>
                </div>
              ))}
              {toolRuns.length === 0 && <div className="text-xs text-[#8b8176]">{ui('暂无调用记录')}</div>}
            </div>
          </div>
        </div>
      )}

      {activePanel === '插件' && (
        <PluginCenter apiBase={props.apiBase} sessionId={props.sessionId} />
      )}

      {activePanel === '连接' && (
        <div className="aether-tab-panel min-h-0 flex-1 overflow-auto p-4">
          <div className="mb-4 flex items-start gap-3">
            <div className="flex h-9 w-9 items-center justify-center rounded-lg bg-[#e5d9ca] text-[#684827]"><Cable size={17} /></div>
            <div>
              <h2 className="text-sm font-semibold">{ui('MCP 连接器')}</h2>
              <p className="mt-1 text-xs leading-5 text-[#7a7065]">支持标准 stdio 与 Streamable HTTP。令牌保存在系统密钥环，模型调用仍需对话内授权。</p>
            </div>
          </div>
          {mcpStatus && <div role="status" className="mb-3 rounded-md bg-[#e9e0d5] px-3 py-2 text-xs text-[#6e5d4b]">{ui(mcpStatus)}</div>}
          <section className="rounded-xl border border-[#ded6ca] bg-[#fbfaf7] p-3">
            <div className="mb-3 text-xs font-semibold">{ui('添加连接器')}</div>
            <div className="grid gap-2 sm:grid-cols-2">
              <input value={mcpName} onChange={(event) => setMcpName(event.target.value)} placeholder={ui('连接器名称')} className="h-9 rounded-md border border-[#ded6ca] px-3 text-sm outline-none" />
              <select value={mcpTransport} onChange={(event) => { setMcpTransport(event.target.value as MCPConnection['transport']); setMcpEndpoint(''); }} className="h-9 rounded-md border border-[#ded6ca] bg-white px-3 text-sm">
                <option value="stdio">{ui('本地 stdio')}</option><option value="streamable_http">Streamable HTTP</option>
              </select>
              <input value={mcpEndpoint} onChange={(event) => setMcpEndpoint(event.target.value)} placeholder={mcpTransport === 'stdio' ? '可执行命令，例如 npx' : 'https://example.com/mcp'} className="h-9 rounded-md border border-[#ded6ca] px-3 text-sm outline-none sm:col-span-2" />
              {mcpTransport === 'stdio' && <textarea value={mcpArgs} onChange={(event) => setMcpArgs(event.target.value)} placeholder={'每行一个参数，例如：\n-y\n@modelcontextprotocol/server-filesystem\n/home/user/project'} className="min-h-24 rounded-md border border-[#ded6ca] p-3 text-xs outline-none sm:col-span-2" />}
              {mcpTransport === 'streamable_http' && <>
                <select value={mcpAuthType} onChange={(event) => setMcpAuthType(event.target.value as MCPConnection['auth_type'])} className="h-9 rounded-md border border-[#ded6ca] bg-white px-3 text-sm">
                  <option value="none">无认证</option><option value="bearer">Bearer Token</option><option value="oauth_token">导入 OAuth Access Token</option><option value="oauth">浏览器 OAuth / PKCE</option>
                </select>
                <input type="password" autoComplete="new-password" value={mcpToken} onChange={(event) => setMcpToken(event.target.value)} disabled={mcpAuthType === 'none' || mcpAuthType === 'oauth'} placeholder={mcpAuthType === 'oauth' ? '创建后点击 OAuth 授权' : '令牌仅写入系统密钥环'} className="h-9 rounded-md border border-[#ded6ca] px-3 text-sm disabled:bg-[#eee7de]" />
              </>}
            </div>
            <button onClick={createMcpConnection} className="mt-3 rounded-md bg-[#2f2a24] px-3 py-1.5 text-xs text-white">{ui('连接并发现工具')}</button>
          </section>
          <div className="mt-4 space-y-3">
            {mcpConnections.map((connection) => (
              <article key={connection.id} className="rounded-xl border border-[#ded6ca] bg-[#fbfaf7] p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0"><div className="text-sm font-semibold">{connection.name}</div><div className="mt-1 truncate text-[11px] text-[#8b8176]">{connection.transport === 'stdio' ? [connection.command, ...(connection.args || [])].join(' ') : connection.url}</div></div>
                  <span className={connection.status === 'ready' ? 'rounded bg-[#dfead7] px-2 py-1 text-xs text-[#3f6c3a]' : connection.status === 'error' ? 'rounded bg-[#ead5d1] px-2 py-1 text-xs text-[#8a3b31]' : 'rounded bg-[#ebe3d8] px-2 py-1 text-xs text-[#6d6258]'}>{connection.status}</span>
                </div>
                <div className="mt-2 flex flex-wrap gap-2 text-xs">
                  <button onClick={() => patchMcpConnection(connection, { enabled: !connection.enabled })} className="rounded-md border border-[#d6cbbd] px-2 py-1">{connection.enabled ? ui('停用') : ui('启用')}</button>
                  <button onClick={() => refreshMcpConnection(connection)} disabled={!connection.enabled} className="rounded-md border border-[#d6cbbd] px-2 py-1 disabled:opacity-40">{ui('刷新工具')}</button>
                  {connection.auth_type === 'oauth' && <button onClick={() => authorizeMcpOAuth(connection)} disabled={!connection.enabled || connection.status === 'authorizing'} className="rounded-md border border-[#8d5f35] px-2 py-1 text-[#6b4525] disabled:opacity-40">{connection.status === 'authorizing' ? ui('等待 OAuth') : ui('OAuth 授权')}</button>}
                  <button onClick={() => deleteMcpConnection(connection)} className="rounded-md border border-[#d6cbbd] px-2 py-1 text-[#8a3b31]">{ui('删除')}</button>
                  <span className="px-1 py-1 text-[#7a7065]">{connection.has_token ? ui('凭据已安全保存') : ui('无已保存凭据')} · {ui('{count} 个工具', { count: connection.tools.length })}</span>
                </div>
                {connection.error && <div className="mt-2 text-xs text-[#8a3b31]">{connection.error}</div>}
                <div className="mt-3 space-y-1">
                  {connection.tools.map((tool) => {
                    const checked = connection.allowed_tools === null || connection.allowed_tools.includes(tool.name);
                    return <label key={tool.name} className="flex items-start gap-2 rounded-md bg-[#f2ece4] p-2 text-xs">
                      <input type="checkbox" checked={checked} onChange={() => {
                        const current = connection.allowed_tools === null ? connection.tools.map((item) => item.name) : [...connection.allowed_tools];
                        const next = checked ? current.filter((name) => name !== tool.name) : [...current, tool.name];
                        void patchMcpConnection(connection, { allowed_tools: next });
                      }} />
                      <span><span className="font-medium">{tool.title || tool.name}</span><span className="ml-2 text-[#7a7065]">{tool.description}</span></span>
                    </label>;
                  })}
                </div>
              </article>
            ))}
            {!mcpConnections.length && <div className="rounded-lg border border-dashed border-[#d6cbbd] p-6 text-center text-xs text-[#7a7065]">{ui('尚未配置 MCP 连接器')}</div>}
          </div>
        </div>
      )}

      {activePanel === '电脑' && <DesktopControlPanel />}

      {activePanel === '模型' && (
        <div className="aether-tab-panel grid min-h-0 flex-1 overflow-hidden grid-rows-[auto_minmax(0,1fr)]">
          <div className="border-b border-[#ded6ca] p-4">
            <div className="mb-3 flex items-center gap-2 text-sm font-semibold">
              <Cpu size={16} />
              {ui('模型 API')}
            </div>
            <div className="grid grid-cols-2 gap-2">
              <input value={modelName} onChange={(event) => setModelName(event.target.value)} readOnly={Boolean(editingModelName)} title={editingModelName ? ui('模型名称是稳定标识；如需新名称，请新建配置') : undefined} placeholder={ui('名称，例如：我的 GPT')} className="h-9 rounded-lg border border-[#ded6ca] bg-white px-3 text-sm outline-none read-only:bg-[#eee7de]" />
              <select value={modelType} onChange={(event) => setModelType(event.target.value as ModelConfig['type'])} className="h-9 rounded-lg border border-[#ded6ca] bg-white px-3 text-sm outline-none">
                <option value="openai_compat">{ui('OpenAI 兼容')}</option>
                <option value="anthropic">Anthropic / Claude</option>
                <option value="xai">xAI</option>
                <option value="local">{ui('本地模型')}</option>
              </select>
              <input value={modelId} onChange={(event) => setModelId(event.target.value)} placeholder={ui('模型 ID，例如：gpt-4.1')} className="h-9 rounded-lg border border-[#ded6ca] bg-white px-3 text-sm outline-none" />
              <input value={modelBaseUrl} onChange={(event) => setModelBaseUrl(event.target.value)} placeholder={ui('接口地址，可选')} className="h-9 rounded-lg border border-[#ded6ca] bg-white px-3 text-sm outline-none" />
              <input value={modelApiKey} onChange={(event) => setModelApiKey(event.target.value)} placeholder={ui('接口密钥，本地保存')} type="password" autoComplete="new-password" className="h-9 rounded-lg border border-[#ded6ca] bg-white px-3 text-sm outline-none" />
              <select value={modelPolicy} onChange={(event) => setModelPolicy(event.target.value as ModelConfig['participation_policy'])} className="h-9 rounded-lg border border-[#ded6ca] bg-white px-3 text-sm outline-none">
                <option value="always">{ui('始终参与')}</option>
                <option value="mentioned">{ui('被提及时参与')}</option>
                <option value="project_autonomous">{ui('项目模式自动参与')}</option>
              </select>
              <input value={modelPersona} onChange={(event) => setModelPersona(event.target.value)} placeholder={ui('角色，例如：代码评审 / 产品经理')} className="col-span-2 h-9 rounded-lg border border-[#ded6ca] bg-white px-3 text-sm outline-none" />
              {modelType === 'local' && <>
                <select value={modelRuntime} onChange={(event) => setModelRuntime(event.target.value as 'external' | 'llama_cpp')} className="h-9 rounded-lg border border-[#ded6ca] bg-white px-3 text-sm outline-none">
                  <option value="external">{ui('外部服务（仅连接）')}</option>
                  <option value="llama_cpp">{ui('llama.cpp 自动启动')}</option>
                </select>
                <input value={modelPath} onChange={(event) => setModelPath(event.target.value)} disabled={modelRuntime !== 'llama_cpp'} placeholder={ui('GGUF 模型绝对路径')} className="h-9 rounded-lg border border-[#ded6ca] bg-white px-3 text-sm outline-none disabled:bg-[#eee7de]" />
                {modelRuntime === 'llama_cpp' && <>
                  <input value={modelExecutable} onChange={(event) => setModelExecutable(event.target.value)} placeholder={ui('llama-server 绝对路径')} className="col-span-2 h-9 rounded-lg border border-[#ded6ca] bg-white px-3 text-sm outline-none" />
                  <textarea value={modelRuntimeArgs} onChange={(event) => setModelRuntimeArgs(event.target.value)} aria-label={ui('llama.cpp 启动参数')} placeholder={ui('每行一个启动参数')} className="col-span-2 min-h-24 rounded-lg border border-[#ded6ca] bg-white px-3 py-2 text-xs outline-none" />
                </>}
              </>}
              <button onClick={submitModel} className="col-span-2 flex h-9 items-center justify-center gap-1 rounded-lg bg-[#2f2a24] px-3 text-sm font-medium text-white">
                <Plus size={15} />
                {editingModelName ? ui('更新并验证') : ui('添加并验证')}
              </button>
              {modelVerifyStatus && <div className="col-span-2 rounded-md bg-[#ebe3d8] px-3 py-2 text-xs text-[#6d6258]">{ui(modelVerifyStatus)}</div>}
            </div>
          </div>
          <div className="overflow-auto p-4">
            <div className="mb-2 text-xs font-semibold text-[#7a7065]">{ui('已连接模型')}</div>
            <div className="space-y-2">
              {modelConfigs.map((model) => (
                <div key={model.name} className="rounded-lg border border-[#ded6ca] bg-[#fbfaf7] p-3">
                  <div className="flex items-start justify-between gap-3">
                    <div className="min-w-0">
                      <div className="truncate text-sm font-semibold">{model.name}</div>
                      <div className="mt-1 text-xs leading-5 text-[#7a7065]">{model.type} - {model.model}</div>
                      <div className="mt-1 text-xs leading-5 text-[#7a7065]">{model.persona || ui('未设置角色')} · {model.participation_policy === 'always' ? ui('在线待命') : ui('按需唤醒')}</div>
                    </div>
                    <span className={model.has_api_key || model.type === 'local' ? 'rounded-md bg-[#dcebd7] px-2 py-1 text-[11px] text-[#326022]' : 'rounded-md bg-[#ebe3d8] px-2 py-1 text-[11px] text-[#7a7065]'}>{model.type === 'local' ? ui('本地接口已配置') : model.has_api_key ? ui('密钥已保存') : ui('未配置')}</span>
                  </div>
                  <div className="mt-3 flex items-center justify-between text-xs text-[#7a7065]">
                    <span>{model.base_url || ui('默认接口')}</span>
                    <div className="flex gap-2">
                      <button
                        onClick={() => toggleModelParticipation(model)}
                        disabled={!model.has_api_key && model.type !== 'local'}
                        title={!model.has_api_key && model.type !== 'local' ? ui('请先配置接口密钥') : undefined}
                        className={model.participation_policy === 'always' ? 'rounded-md bg-[#2f2a24] px-2 py-1 text-white disabled:opacity-50' : 'rounded-md border border-[#d6cbbd] px-2 py-1 disabled:opacity-50'}
                      >
                        {model.participation_policy === 'always' ? ui('在线') : ui('启用')}
                      </button>
                      <button onClick={() => editModel(model)} className="rounded-md border border-[#d6cbbd] px-2 py-1">{ui('编辑')}</button>
                      <button onClick={async () => {
                        try {
                          setModelVerifyStatus('正在验证...');
                          await verifyModelConnection(model.name);
                        } catch (error) {
                          setModelVerifyStatus(`验证失败：${String(error)}`);
                        }
                      }} className="rounded-md border border-[#d6cbbd] px-2 py-1">{model.type === 'local' ? ui('启动并验证') : ui('验证')}</button>
                      <button onClick={() => props.onDeleteModel(model)} className="rounded-md border border-[#d6cbbd] px-2 py-1 text-[#7a3b2e]">{ui('删除')}</button>
                    </div>
                  </div>
                </div>
              ))}
              {modelConfigs.length === 0 && <div className="rounded-lg border border-dashed border-[#d6cbbd] p-6 text-center text-sm text-[#7a7065]">{ui('暂无模型配置')}</div>}
            </div>
          </div>
        </div>
      )}

      {activePanel === '运行' && (
        <RunPanel
          exportSession={props.exportSession || (() => {})}
          agentRuns={props.agentRuns}
          gitRepository={props.gitRepository}
          verificationCommands={props.verificationCommands}
          onVerificationCommandsChange={props.onVerificationCommandsChange}
          onSelectGitRepository={props.onSelectGitRepository}
          onDecideWorkspaceCandidate={props.onDecideWorkspaceCandidate}
          onRetry={props.onRetryAgentRun}
          onInterruptStep={props.onInterruptAgentStep}
          onUpdatePlan={props.onUpdateAgentPlan}
          onConfirmPlan={props.onConfirmAgentPlan}
          onRejectPlan={props.onRejectAgentPlan}
        />
      )}

      {activePanel === '设置' && (
        <div className="aether-tab-panel aether-panel-scroll min-h-0 flex-1 overflow-auto p-4">
          <div className="mb-4 flex items-center gap-2 text-sm font-semibold">
            <ShieldCheck size={16} />
            {t('collaborationSettings')}
          </div>
          {([
            ['mode', '协作模式', ['日常讨论', '圆桌评审', '项目协作']],
            ['mergeRule', '合并规则', ['两票通过', '主持人通过', '人工确认']],
            ['sandbox', '沙箱策略', ['Docker 严格沙箱', '只读预览', '关闭执行']],
            ['language', '语言策略', ['中文优先', '中英双语']],
            ['uiLocale', t('uiLanguage'), ['zh-CN', 'en-US']],
          ] as Array<[keyof WorkspaceSettings, string, string[]]>).map(([key, label, options]) => (
            <label key={String(key)} className="mb-4 block">
              <span className="mb-1 block text-xs font-semibold text-[#7a7065]">{ui(String(label))}</span>
              <select
                value={workspaceSettings[key]}
                onChange={(event) => props.onUpdateSettings({ [key]: event.target.value } as Partial<WorkspaceSettings>)}
                className="h-10 w-full rounded-lg border border-[#ded6ca] bg-white px-3 text-sm outline-none"
              >
                {(options as string[]).map((option) => <option key={option} value={option}>{option === 'zh-CN' ? ui('简体中文') : option === 'en-US' ? 'English' : ui(option)}</option>)}
              </select>
            </label>
          ))}
          <div className="mt-5 text-xs font-semibold text-[#7a7065]">{ui('模型角色')}</div>
          <div className="mt-2 space-y-2">
            {providers.map((provider) => (
              <div key={provider.name} className="rounded-lg border border-[#ded6ca] bg-[#fbfaf7] p-3">
                <div className="flex items-center justify-between text-sm font-medium">
                  <span>{provider.name}</span>
                  <span className={`status-dot status-${provider.status}`} />
                </div>
                <div className="mt-1 text-xs text-[#7a7065]">{provider.role} · {provider.persona}</div>
              </div>
            ))}
          </div>
        </div>
      )}
    </aside>
  );
}

function DesktopControlPanel() {
  const { ui } = useI18n();
  const bridge = window.aetherDesktop;
  const [capabilities, setCapabilities] = useState<Record<string, unknown> | null>(null);
  const [selected, setSelected] = useState<SelectedDesktopDocument | null>(null);
  const [editorText, setEditorText] = useState('');
  const [screenshot, setScreenshot] = useState<{ dataUrl: string; width: number; height: number; capturedAt: string } | null>(null);
  const [mouse, setMouse] = useState<DesktopMouseResult | null>(null);
  const [mouseX, setMouseX] = useState('');
  const [mouseY, setMouseY] = useState('');
  const [mouseArmed, setMouseArmed] = useState(false);
  const [writeArmed, setWriteArmed] = useState(false);
  const [busy, setBusy] = useState('');
  const [status, setStatus] = useState('');
  const mouseAvailable = capabilities?.mouse === true;
  const computerUseAvailable = capabilities?.model_computer_use === true;

  useEffect(() => {
    if (!bridge) {
      setStatus('电脑控制仅在 AetherChat 桌面版中可用');
      return;
    }
    bridge.status()
      .then((value) => setCapabilities(value))
      .catch((error) => setStatus(`电脑服务不可用：${String(error)}`));
  }, [bridge]);

  const selectDocument = async () => {
    if (!bridge) return;
    setBusy('select');
    try {
      const value = await bridge.selectDocument();
      if (!value) return;
      setSelected(value);
      setEditorText(value.document.editor_mode === 'text'
        ? String(value.document.data.text ?? '')
        : JSON.stringify(value.document.data, null, 2));
      setWriteArmed(false);
      setStatus(`已读取 ${value.displayName}（${value.document.format.toUpperCase()}）`);
    } catch (error) {
      setStatus(`读取失败：${String(error)}`);
    } finally {
      setBusy('');
    }
  };

  const editorData = () => {
    if (!selected) throw new Error('请先选择文档');
    if (selected.document.editor_mode === 'text') return { text: editorText };
    const parsed = JSON.parse(editorText);
    if (!parsed || typeof parsed !== 'object' || Array.isArray(parsed)) throw new Error('结构化文档内容必须是 JSON 对象');
    return parsed as Record<string, unknown>;
  };

  const saveDocument = async (saveAs: boolean) => {
    if (!bridge || !selected || !writeArmed) return;
    setBusy(saveAs ? 'save-as' : 'save');
    try {
      const data = editorData();
      if (saveAs) {
        const result = await bridge.saveDocumentAs(selected.handle, data);
        if (result) {
          setSelected({ ...selected, handle: result.handle, displayName: result.displayName });
          const backup = result.backupCreated
            ? `；已备份原文件：${result.backupDisplayName || '备份已创建'}`
            : '';
          setStatus(`已另存为 ${result.displayName}${backup}`);
        }
      } else {
        const result = await bridge.saveDocument(selected.handle, data);
        const backup = result.backupCreated
          ? `；已创建备份：${result.backupDisplayName || '备份已创建'}`
          : '';
        setStatus(`文档保存成功：${result.displayName}${backup}`);
      }
      setWriteArmed(false);
    } catch (error) {
      setStatus(`保存失败：${String(error)}`);
    } finally {
      setBusy('');
    }
  };

  const captureScreen = async () => {
    if (!bridge) return;
    setBusy('capture');
    try {
      const value = await bridge.captureScreen();
      setScreenshot(value);
      setStatus(`已截取主屏幕 ${value.width}×${value.height}，尚未写入文件`);
    } catch (error) {
      setStatus(`截图失败：${String(error)}`);
    } finally {
      setBusy('');
    }
  };

  const saveScreenshot = async () => {
    if (!bridge || !screenshot) return;
    setBusy('save-shot');
    try {
      const result = await bridge.saveScreenshot(screenshot.dataUrl);
      setStatus(`截图已保存：${result.locationLabel}/${result.displayName}`);
    } catch (error) {
      setStatus(`截图保存失败：${String(error)}`);
    } finally {
      setBusy('');
    }
  };

  const mouseAction = async (action: 'get' | 'move' | 'click') => {
    if (!bridge || (action !== 'get' && !mouseArmed)) return;
    setBusy(`mouse-${action}`);
    try {
      const x = Number.parseInt(mouseX, 10);
      const y = Number.parseInt(mouseY, 10);
      const value = await bridge.mouse(action, action === 'get' ? {} : { x, y, button: 'left' });
      setMouse(value);
      setMouseX(String(value.position.x));
      setMouseY(String(value.position.y));
      setStatus(action === 'get' ? `鼠标位置：(${value.position.x}, ${value.position.y})` : `鼠标 ${action === 'move' ? '移动' : '点击'}完成`);
      if (action !== 'get') setMouseArmed(false);
    } catch (error) {
      setStatus(`鼠标操作失败：${String(error)}`);
    } finally {
      setBusy('');
    }
  };

  return (
    <div className="aether-tab-panel aether-panel-scroll min-h-0 flex-1 overflow-auto p-4">
      <div className="mb-3 flex items-start justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold"><Laptop size={16} />{ui('本地电脑')}</div>
          <div className="mt-1 text-xs leading-5 text-[#7a7065]">文件选择器路径与桌面令牌不会传入界面层。文档与截图功能跨平台可用；Windows 版还支持经原生授权的窗口观察与键鼠操作。</div>
        </div>
        <span className={`rounded-md px-2 py-1 text-xs ${capabilities ? 'bg-[#dfead7] text-[#3f6c3a]' : 'bg-[#ead5d1] text-[#8a3b31]'}`}>
          {capabilities ? (computerUseAvailable ? 'Computer Use 已连接' : 'Linux 桌面服务已连接') : '不可用'}
        </span>
      </div>
      {status && <div className="mb-3 break-all rounded-lg border border-[#d8cfc3] bg-[#fffdf9] px-3 py-2 text-xs leading-5 text-[#6d6258]">{status}</div>}

      <section className="rounded-xl border border-[#ded6ca] bg-[#fbfaf7] p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold"><FolderOpen size={15} />本地文档编辑</div>
            <div className="mt-1 text-xs text-[#7a7065]">TXT/MD/JSON/CSV/TSV、DOCX、XLSX、PPTX，以及 PDF 元数据与附加备注。</div>
          </div>
          <button onClick={selectDocument} disabled={!bridge || busy === 'select'} className="rounded-md bg-[#2f2a24] px-3 py-1.5 text-xs text-white disabled:opacity-40">
            {busy === 'select' ? '读取中' : '选择文档'}
          </button>
        </div>
        {selected && (
          <div className="mt-3">
            <div className="mb-2 flex flex-wrap items-center gap-2 text-xs">
              <span className="font-semibold">{selected.displayName}</span>
              <span className="rounded bg-[#ebe3d8] px-2 py-1">{selected.document.format.toUpperCase()}</span>
              <span className="text-[#7a7065]">{selected.document.size_bytes.toLocaleString()} bytes</span>
              {selected.document.truncated && <span className="text-[#9b3328]">大型表格仅加载前 500×100 单元格</span>}
            </div>
            {selected.document.read_only_text && (
              <details className="mb-2 rounded-lg border border-[#ded6ca] bg-white p-2">
                <summary className="cursor-pointer text-xs font-medium">查看 PDF 已提取文本（只读）</summary>
                <pre className="mt-2 max-h-32 overflow-auto whitespace-pre-wrap text-[11px] leading-5 text-[#7a7065]">{selected.document.read_only_text}</pre>
              </details>
            )}
            <textarea
              value={editorText}
              onChange={(event) => setEditorText(event.target.value)}
              aria-label="本地文档编辑器"
              className="h-64 w-full resize-y rounded-lg border border-[#ded6ca] bg-white p-3 font-mono text-xs leading-5 outline-none"
            />
            <label className="mt-2 flex items-center gap-2 text-xs text-[#6d6258]">
              <input type="checkbox" checked={writeArmed} onChange={(event) => setWriteArmed(event.target.checked)} />
              我确认本次写入；覆盖保存（包括另存为覆盖已有目标）会先创建时间戳备份
            </label>
            <div className="mt-2 flex gap-2">
              <button onClick={() => saveDocument(false)} disabled={!writeArmed || Boolean(busy)} className="flex items-center gap-1 rounded-md bg-[#8d5f35] px-3 py-1.5 text-xs text-white disabled:opacity-40"><Save size={13} />覆盖保存</button>
              <button onClick={() => saveDocument(true)} disabled={!writeArmed || Boolean(busy)} className="rounded-md border border-[#d6cbbd] px-3 py-1.5 text-xs disabled:opacity-40">另存为</button>
            </div>
          </div>
        )}
      </section>

      <section className="mt-3 rounded-xl border border-[#ded6ca] bg-[#fbfaf7] p-4">
        <div className="flex items-center justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold"><Camera size={15} />屏幕截图</div>
            <div className="mt-1 text-xs text-[#7a7065]">每次截图由主进程再次确认；预览不会上传。</div>
          </div>
          <button onClick={captureScreen} disabled={!bridge || Boolean(busy)} className="rounded-md bg-[#2f2a24] px-3 py-1.5 text-xs text-white disabled:opacity-40">{busy === 'capture' ? '截图中' : '截取主屏幕'}</button>
        </div>
        {screenshot && (
          <div className="mt-3">
            <img src={screenshot.dataUrl} alt="本机屏幕截图预览" className="max-h-72 w-full rounded-lg border border-[#ded6ca] bg-black object-contain" />
            <button onClick={saveScreenshot} disabled={Boolean(busy)} className="mt-2 rounded-md border border-[#d6cbbd] px-3 py-1.5 text-xs disabled:opacity-40">保存到“图片/Screenshots”</button>
          </div>
        )}
      </section>

      <section className="mt-3 rounded-xl border border-[#ded6ca] bg-[#fbfaf7] p-4">
        <div className="flex items-center gap-2 text-sm font-semibold"><MousePointer2 size={15} />鼠标控制</div>
        <div className="mt-1 text-xs text-[#7a7065]">{mouseAvailable ? '移动和点击均需要勾选一次性授权，并由主进程弹出确认。' : 'Linux 版本不启用 Win32 键鼠注入；文档和截图功能不受影响。'}</div>
        <div className="mt-3 grid grid-cols-[1fr_1fr_auto] gap-2">
          <input value={mouseX} onChange={(event) => setMouseX(event.target.value)} inputMode="numeric" placeholder="屏幕 X" aria-label="鼠标 X 坐标" className="h-9 rounded-lg border border-[#ded6ca] px-3 text-sm outline-none" />
          <input value={mouseY} onChange={(event) => setMouseY(event.target.value)} inputMode="numeric" placeholder="屏幕 Y" aria-label="鼠标 Y 坐标" className="h-9 rounded-lg border border-[#ded6ca] px-3 text-sm outline-none" />
          <button onClick={() => mouseAction('get')} disabled={!bridge || !mouseAvailable || Boolean(busy)} className="rounded-md border border-[#d6cbbd] px-3 text-xs disabled:opacity-40">读取位置</button>
        </div>
        {mouse && <div className="mt-2 text-xs text-[#7a7065]">虚拟桌面：{mouse.virtual_desktop.left},{mouse.virtual_desktop.top} · {mouse.virtual_desktop.width}×{mouse.virtual_desktop.height}</div>}
        <label className="mt-3 flex items-center gap-2 text-xs text-[#6d6258]">
          <input type="checkbox" checked={mouseArmed} disabled={!mouseAvailable} onChange={(event) => setMouseArmed(event.target.checked)} />
          我确认只授权下一次鼠标移动或左键单击
        </label>
        <div className="mt-2 flex gap-2">
          <button onClick={() => mouseAction('move')} disabled={!mouseAvailable || !mouseArmed || !mouseX || !mouseY || Boolean(busy)} className="rounded-md bg-[#8d5f35] px-3 py-1.5 text-xs text-white disabled:opacity-40">移动鼠标</button>
          <button onClick={() => mouseAction('click')} disabled={!mouseAvailable || !mouseArmed || !mouseX || !mouseY || Boolean(busy)} className="rounded-md border border-[#8d5f35] px-3 py-1.5 text-xs text-[#6b4525] disabled:opacity-40">左键单击</button>
        </div>
      </section>
    </div>
  );
}

function PluginCenter({ apiBase, sessionId }: { apiBase: string; sessionId: string }) {
  const { ui } = useI18n();
  const [plugins, setPlugins] = useState<PluginItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState('');
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [lastResult, setLastResult] = useState<PluginInvokeResult | null>(null);
  const [status, setStatus] = useState('');
  const [manifestText, setManifestText] = useState('');

  const load = async () => {
    setLoading(true);
    try {
      setPlugins(await fetchJson<PluginItem[]>(`${apiBase}/plugins/`));
      setStatus('');
    } catch (error) {
      setStatus(`插件加载失败：${String(error)}`);
    } finally {
      setLoading(false);
    }
  };

  useEffect(() => {
    load();
  }, [apiBase]);

  const toggle = async (plugin: PluginItem) => {
    setBusyKey(plugin.id);
    try {
      const updated = await fetchJson<PluginItem>(`${apiBase}/plugins/${encodeURIComponent(plugin.id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !plugin.enabled }),
      });
      setPlugins((current) => current.map((item) => item.id === updated.id ? updated : item));
      setStatus(`${plugin.name} 已${updated.enabled ? '启用' : '停用'}`);
    } catch (error) {
      setStatus(`更新失败：${String(error)}`);
    } finally {
      setBusyKey('');
    }
  };

  const invoke = async (plugin: PluginItem, action: PluginItem['actions'][number]) => {
    const actionKey = `${plugin.id}:${action.id}`;
    const argumentsForAction: Record<string, unknown> = {};
    for (const [name, property] of Object.entries(action.input_schema?.properties || {})) {
      const raw = inputs[`${actionKey}:${name}`] ?? '';
      if (!raw && !(action.input_schema.required || []).includes(name)) continue;
      if (property.type === 'integer') argumentsForAction[name] = Number.parseInt(raw, 10);
      else if (property.type === 'number') argumentsForAction[name] = Number.parseFloat(raw);
      else if (property.type === 'boolean') argumentsForAction[name] = raw === 'true';
      else if (property.type === 'object' || property.type === 'array') {
        try {
          argumentsForAction[name] = JSON.parse(raw);
        } catch {
          setStatus(`${action.label} 的 ${name} 必须是有效 JSON`);
          return;
        }
      }
      else argumentsForAction[name] = raw;
    }
    setBusyKey(actionKey);
    try {
      const result = await fetchJson<PluginInvokeResult>(`${apiBase}/plugins/${encodeURIComponent(plugin.id)}/invoke`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action: action.id, session_id: sessionId, arguments: argumentsForAction }),
      });
      setLastResult(result);
      const payload = result.result as { kind?: string; url?: string } | null;
      if (payload?.kind === 'open_external' && payload.url) {
        if (!window.aetherRuntime?.openExternal) throw new Error('当前不是可打开外部链接的桌面运行环境');
        await window.aetherRuntime.openExternal(payload.url);
      }
      setStatus(`${plugin.name} · ${action.label} 执行成功`);
    } catch (error) {
      setStatus(`调用失败：${String(error)}`);
    } finally {
      setBusyKey('');
    }
  };

  const install = async () => {
    try {
      const manifest = JSON.parse(manifestText);
      const installed = await fetchJson<PluginItem>(`${apiBase}/plugins/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(manifest),
      });
      setPlugins((current) => [installed, ...current.filter((item) => item.id !== installed.id)]);
      setManifestText('');
      setStatus(`插件 ${installed.name} 已安装`);
    } catch (error) {
      setStatus(`安装失败：${String(error)}`);
    }
  };

  const uninstall = async (plugin: PluginItem) => {
    if (!window.confirm(`确定卸载插件“${plugin.name}”吗？`)) return;
    try {
      await fetchJson(`${apiBase}/plugins/${encodeURIComponent(plugin.id)}`, { method: 'DELETE' });
      setPlugins((current) => current.filter((item) => item.id !== plugin.id));
      setStatus(`${plugin.name} 已卸载`);
    } catch (error) {
      setStatus(`卸载失败：${String(error)}`);
    }
  };

  return (
    <div className="aether-tab-panel aether-panel-scroll min-h-0 flex-1 overflow-auto p-4">
      <div className="mb-3 flex items-center justify-between gap-3">
        <div>
          <div className="flex items-center gap-2 text-sm font-semibold"><Puzzle size={16} />{ui('插件中心')}</div>
          <div className="mt-1 text-xs text-[#7a7065]">兼容 Codex 风格 manifest；自定义插件仅支持声明式模板，不执行任意代码。</div>
        </div>
        <button onClick={load} disabled={loading} className="rounded-md border border-[#d6cbbd] px-3 py-1.5 text-xs disabled:opacity-50">刷新</button>
      </div>

      {status && <div className="mb-3 rounded-lg border border-[#d8cfc3] bg-[#fffdf9] px-3 py-2 text-xs text-[#6d6258]">{status}</div>}

      <div className="space-y-3">
        {plugins.map((plugin) => (
          <article key={plugin.id} className="rounded-xl border border-[#ded6ca] bg-[#fbfaf7] p-4">
            <div className="flex items-start justify-between gap-3">
              <div className="min-w-0">
                <div className="flex flex-wrap items-center gap-2">
                  <span className="text-sm font-semibold">{plugin.name}</span>
                  <span className="rounded bg-[#ebe3d8] px-1.5 py-0.5 text-[10px]">v{plugin.version}</span>
                  <span className="rounded bg-[#eee7de] px-1.5 py-0.5 text-[10px]">{plugin.builtin ? '内置' : '本地'}</span>
                </div>
                <div className="mt-1 text-xs leading-5 text-[#7a7065]">{plugin.description}</div>
              </div>
              <button
                onClick={() => toggle(plugin)}
                disabled={busyKey === plugin.id}
                className={`shrink-0 rounded-md px-2.5 py-1 text-xs ${plugin.enabled ? 'bg-[#2f2a24] text-white' : 'border border-[#d6cbbd] text-[#786d62]'}`}
              >
                {plugin.enabled ? '已启用' : '已停用'}
              </button>
            </div>

            <div className="mt-3 flex flex-wrap gap-1.5">
              {plugin.permissions.map((permission) => <span key={permission} className="rounded-md border border-[#ded6ca] px-2 py-1 text-[10px] text-[#7a7065]">{permission}</span>)}
              {!plugin.permissions.length && <span className="text-[10px] text-[#8b8176]">无需额外权限</span>}
            </div>

            <div className="mt-3 space-y-2">
              {plugin.actions.map((action) => {
                const actionKey = `${plugin.id}:${action.id}`;
                const properties = Object.entries(action.input_schema?.properties || {});
                return (
                  <div key={action.id} className="rounded-lg border border-[#e4dbcf] bg-white p-3">
                    <div className="flex items-center justify-between gap-3">
                      <div>
                        <div className="text-xs font-semibold">{action.label}</div>
                        <div className="mt-1 text-[11px] text-[#7a7065]">{action.description}</div>
                      </div>
                      <button
                        onClick={() => invoke(plugin, action)}
                        disabled={!plugin.enabled || busyKey === actionKey}
                        className="shrink-0 rounded-md bg-[#8d5f35] px-2.5 py-1 text-xs text-white disabled:opacity-40"
                      >
                        {busyKey === actionKey ? '执行中' : '运行'}
                      </button>
                    </div>
                    {properties.length > 0 && (
                      <div className="mt-2 grid gap-2">
                        {properties.map(([name, property]) => (
                          <input
                            key={name}
                            value={inputs[`${actionKey}:${name}`] ?? ''}
                            onChange={(event) => setInputs((current) => ({ ...current, [`${actionKey}:${name}`]: event.target.value }))}
                            placeholder={`${property.title || name}${(action.input_schema.required || []).includes(name) ? '（必填）' : ''}`}
                            className="h-8 rounded-md border border-[#ded6ca] px-2 text-xs outline-none"
                          />
                        ))}
                      </div>
                    )}
                  </div>
                );
              })}
            </div>

            {!plugin.builtin && (
              <button onClick={() => uninstall(plugin)} className="mt-3 flex items-center gap-1 text-xs text-[#8a3428]"><Trash2 size={12} />卸载</button>
            )}
          </article>
        ))}
        {!loading && plugins.length === 0 && <div className="rounded-lg border border-dashed border-[#d6cbbd] p-6 text-center text-sm text-[#7a7065]">暂无插件</div>}
      </div>

      {lastResult && (
        <div className="mt-4 rounded-xl border border-[#ded6ca] bg-[#2f2a24] p-3 text-[#f6eee4]">
          <div className="text-xs font-semibold">最近结果 · {lastResult.plugin_name}</div>
          <pre className="mt-2 max-h-52 overflow-auto whitespace-pre-wrap break-all text-[11px] leading-5">{JSON.stringify(
            lastResult.result,
            (key, value) => key === 'image_data_url' && typeof value === 'string'
              ? `[PNG 视觉数据已省略，${value.length} 字符]`
              : value,
            2,
          )}</pre>
        </div>
      )}

      <details className="mt-4 rounded-xl border border-[#ded6ca] bg-[#fbfaf7] p-3">
        <summary className="cursor-pointer text-xs font-semibold">导入本地插件 manifest</summary>
        <textarea
          value={manifestText}
          onChange={(event) => setManifestText(event.target.value)}
          placeholder='粘贴 JSON，例如：{"id":"my-plugin","name":"我的插件","version":"1.0.0",...}'
          className="mt-3 h-36 w-full resize-y rounded-lg border border-[#ded6ca] bg-white p-2 font-mono text-[11px] outline-none"
        />
        <button onClick={install} disabled={!manifestText.trim()} className="mt-2 rounded-md bg-[#2f2a24] px-3 py-1.5 text-xs text-white disabled:opacity-40">安装 manifest</button>
      </details>
    </div>
  );
}

interface RunPanelProps {
  exportSession: (fmt?: 'markdown' | 'json') => void;
  agentRuns: AgentRun[];
  gitRepository: GitRepository | null;
  verificationCommands: string;
  onVerificationCommandsChange: (value: string) => void;
  onSelectGitRepository: () => void;
  onDecideWorkspaceCandidate: (
    candidate: AgentWorkspaceCandidate,
    decision: 'apply' | 'reject',
  ) => void;
  onRetry: (run: AgentRun) => void;
  onInterruptStep: (run: AgentRun, stepId: string) => void;
  onUpdatePlan: InspectorProps['onUpdateAgentPlan'];
  onConfirmPlan: (run: AgentRun) => void;
  onRejectPlan: (run: AgentRun) => void;
}

type PlanEditorDraft = {
  runId: string;
  baseRevision: number;
  saving: boolean;
  objective: string;
  deliverable: string;
  successCriteria: string;
  constraints: string;
  changeSummary: string;
  steps: Record<string, {
    title: string;
    description: string;
    assignedModel: string;
    role: string;
  }>;
};

type RunCheckTone = 'neutral' | 'ok' | 'warn' | 'bad';

type RunCheckRow = {
  name: string;
  detail: string;
  status: string;
  tone: RunCheckTone;
};

function RunPanel({
  exportSession,
  agentRuns,
  gitRepository,
  verificationCommands,
  onVerificationCommandsChange,
  onSelectGitRepository,
  onDecideWorkspaceCandidate,
  onRetry,
  onInterruptStep,
  onUpdatePlan,
  onConfirmPlan,
  onRejectPlan,
}: RunPanelProps) {
  const { ui } = useI18n();
  const { connection } = useSessionStore();
  const [checking, setChecking] = useState(false);
  const [verifyingModels, setVerifyingModels] = useState(false);
  const [lastChecked, setLastChecked] = useState<string>('尚未检查');
  const [planDraft, setPlanDraft] = useState<PlanEditorDraft | null>(null);
  useEffect(() => {
    if (!planDraft?.saving) return;
    const current = agentRuns.find((run) => run.id === planDraft.runId);
    if ((current?.plan_revision || 0) > planDraft.baseRevision) {
      setPlanDraft(null);
    }
  }, [agentRuns, planDraft]);
  const frontendDetail = window.aetherRuntime?.appVersion
    ? `AetherChat ${window.aetherRuntime.appVersion} · 本地桌面前端`
    : '本地桌面前端';
  const [rows, setRows] = useState<RunCheckRow[]>([
    { name: '后端 API', detail: API_BASE, status: '未检查', tone: 'neutral' },
    { name: '桌面前端', detail: frontendDetail, status: '已加载', tone: 'ok' },
    { name: '模型配置', detail: `${API_BASE}/models/`, status: '未检查', tone: 'neutral' },
    { name: '工具仓', detail: `${API_BASE}/tools/`, status: '未检查', tone: 'neutral' },
    { name: '插件中心', detail: `${API_BASE}/plugins/`, status: '未检查', tone: 'neutral' },
    { name: '电脑控制', detail: '主进程令牌保护', status: window.aetherDesktop ? '未检查' : '仅桌面版', tone: window.aetherDesktop ? 'neutral' : 'warn' },
    { name: 'WebSocket', detail: '/ws/sessions/:id', status: connection === 'online' ? '已连接' : '未连接', tone: connection === 'online' ? 'ok' : 'warn' },
  ]);

  const runCheck = async () => {
    setChecking(true);
    const nextRows: RunCheckRow[] = [
      { name: '桌面前端', detail: frontendDetail, status: '已加载', tone: 'ok' },
      {
        name: 'WebSocket',
        detail: '/ws/sessions/:id',
        status: connection === 'online' ? '已连接' : connection === 'connecting' ? '连接中' : '未连接',
        tone: connection === 'online' ? 'ok' : connection === 'connecting' ? 'warn' : 'bad',
      },
    ];

    try {
      const health = await fetchJson<{ status?: string; redis?: boolean; service?: string }>(`${API_BASE}/health`);
      nextRows.unshift({
        name: '后端 API',
        detail: `${API_BASE} · Redis ${health.redis ? '可用' : '未启用'}${health.service ? ` · ${health.service}` : ''}`,
        status: health.status === 'ok' ? '正常' : '异常',
        tone: health.status === 'ok' ? 'ok' : 'bad',
      });
    } catch (error) {
      nextRows.unshift({ name: '后端 API', detail: `${API_BASE} · ${String(error)}`, status: '不可用', tone: 'bad' });
    }

    try {
      const models = await fetchJson<ModelConfig[]>(`${API_BASE}/models/`);
      const enabled = models.filter((model) => model.participation_policy === 'always').length;
      nextRows.push({
        name: '模型配置',
        detail: `已配置 ${models.length} 个模型，其中 ${enabled} 个已启用；基础检查不会调用模型 API`,
        status: models.length ? '已加载' : '未配置',
        tone: models.length ? 'ok' : 'warn',
      });
    } catch (error) {
      nextRows.push({ name: '模型配置', detail: String(error), status: '不可用', tone: 'bad' });
    }

    try {
      const tools = await fetchJson<ToolItem[]>(`${API_BASE}/tools/`);
      nextRows.push({
        name: '工具仓',
        detail: Array.isArray(tools) ? `已加载 ${tools.length} 个工具` : '返回格式异常',
        status: Array.isArray(tools) ? '正常' : '异常',
        tone: Array.isArray(tools) ? 'ok' : 'bad',
      });
    } catch (error) {
      nextRows.push({ name: '工具仓', detail: String(error), status: '不可用', tone: 'bad' });
    }

    try {
      const plugins = await fetchJson<PluginItem[]>(`${API_BASE}/plugins/`);
      const enabled = plugins.filter((plugin) => plugin.enabled).length;
      nextRows.push({
        name: '插件中心',
        detail: `已加载 ${plugins.length} 个插件，其中 ${enabled} 个已启用`,
        status: '正常',
        tone: 'ok',
      });
    } catch (error) {
      nextRows.push({ name: '插件中心', detail: String(error), status: '不可用', tone: 'bad' });
    }

    if (window.aetherDesktop) {
      try {
        const desktop = await window.aetherDesktop.status();
        const formats = Array.isArray(desktop.document_formats) ? desktop.document_formats.join(' / ') : '未知';
        nextRows.push({
          name: '电脑控制',
          detail: `文件读写、鼠标和截图桥接已保护；文档格式：${formats}`,
          status: desktop.available ? '正常' : '不可用',
          tone: desktop.available ? 'ok' : 'bad',
        });
      } catch (error) {
        nextRows.push({ name: '电脑控制', detail: String(error), status: '不可用', tone: 'bad' });
      }
    } else {
      nextRows.push({ name: '电脑控制', detail: '浏览器预览不提供本地电脑权限', status: '仅桌面版', tone: 'warn' });
    }

    setRows(nextRows);
    setLastChecked(new Date().toLocaleTimeString());
    setChecking(false);
  };

  const verifyConfiguredModels = async () => {
    if (!window.confirm('连接验证会向每个已配置且已启用的模型发送一条最小测试请求，可能产生少量 API 费用。确定继续吗？')) return;
    setVerifyingModels(true);
    try {
      const models = await fetchJson<ModelConfig[]>(`${API_BASE}/models/`);
      const enabled = models.filter((model) => model.participation_policy === 'always');
      let passed = 0;
      const failures: string[] = [];
      for (const model of enabled) {
        try {
          const result = await fetchJson<{ ok?: boolean; message?: string }>(
            `${API_BASE}/models/${encodeURIComponent(model.name)}/verify`,
            { method: 'POST' },
          );
          if (result.ok) passed += 1;
          else failures.push(`${model.name}: ${result.message || '验证失败'}`);
        } catch (error) {
          failures.push(`${model.name}: ${String(error)}`);
        }
      }
      setRows((current) => [
        ...current.filter((row) => row.name !== '模型连接验证'),
        {
          name: '模型连接验证',
          detail: enabled.length
            ? `已验证 ${enabled.length} 个启用模型，通过 ${passed} 个${failures.length ? `；${failures.join('；')}` : ''}`
            : '没有已启用的模型',
          status: failures.length === 0 && enabled.length > 0 ? '全部通过' : enabled.length ? '存在失败' : '未执行',
          tone: failures.length === 0 && enabled.length > 0 ? 'ok' : enabled.length ? 'bad' : 'warn',
        },
      ]);
      setLastChecked(new Date().toLocaleTimeString());
    } finally {
      setVerifyingModels(false);
    }
  };

  const toneClass: Record<RunCheckTone, string> = {
    neutral: 'bg-[#ebe3d8] text-[#6d6258]',
    ok: 'bg-[#dfead7] text-[#3f6c3a]',
    warn: 'bg-[#f2e3c4] text-[#855f18]',
    bad: 'bg-[#ead5d1] text-[#8a3b31]',
  };
  const runStatus: Record<AgentRun['status'], { label: string; className: string }> = {
    queued: { label: '排队中', className: 'bg-[#ebe3d8] text-[#6d6258]' },
    running: { label: '运行中', className: 'bg-[#dbe7ee] text-[#315f78]' },
    waiting_approval: { label: '等待授权', className: 'bg-[#f2e3c4] text-[#855f18]' },
    waiting_plan_confirmation: { label: '等待计划确认', className: 'bg-[#f2e3c4] text-[#855f18]' },
    completed: { label: '已完成', className: 'bg-[#dfead7] text-[#3f6c3a]' },
    failed: { label: '失败', className: 'bg-[#ead5d1] text-[#8a3b31]' },
    interrupted: { label: '已中断', className: 'bg-[#ead5d1] text-[#8a3b31]' },
    recoverable: { label: '可恢复', className: 'bg-[#f2e3c4] text-[#855f18]' },
  };
  const planKindLabel = {
    planner: '规划',
    worker: '执行',
    verifier: '核验',
    synthesizer: '综合',
  } as const;
  const planStepStatus = {
    pending: '待执行',
    ready: '待执行',
    running: '运行中',
    completed: '已完成',
    failed: '失败',
    interrupted: '已中断',
    blocked: '已阻塞',
    skipped: '已跳过',
  } as const;
  const reviewVerdict = {
    pass: '通过',
    needs_revision: '需修订',
    fail: '失败',
    unknown: '未验证',
  } as const;
  const workspaceVerificationLabel = {
    pending: '等待确定性核验',
    passed: 'diff 与命令均通过',
    failed: '核验命令失败',
    unverified: '有 diff，未配置命令',
    no_changes: '没有可应用变更',
  } as const;
  const beginPlanEdit = (run: AgentRun) => {
    if (!run.goal) return;
    setPlanDraft({
      runId: run.id,
      baseRevision: run.plan_revision || 0,
      saving: false,
      objective: run.goal.objective,
      deliverable: run.goal.deliverable,
      successCriteria: (run.goal.success_criteria || []).join('\n'),
      constraints: (run.goal.constraints || []).join('\n'),
      changeSummary: '',
      steps: Object.fromEntries(
        (run.plan_steps || [])
          .filter((step) => step.kind !== 'planner')
          .map((step) => [step.id, {
            title: step.title,
            description: step.description,
            assignedModel: step.assigned_model || '',
            role: step.role,
          }]),
      ),
    });
  };
  const savePlanEdit = (run: AgentRun) => {
    if (!planDraft || planDraft.runId !== run.id) return;
    const lineList = (value: string) => value
      .split('\n')
      .map((item) => item.trim())
      .filter(Boolean);
    const sent = onUpdatePlan(
      run,
      {
        objective: planDraft.objective.trim(),
        deliverable: planDraft.deliverable.trim(),
        success_criteria: lineList(planDraft.successCriteria),
        constraints: lineList(planDraft.constraints),
      },
      (run.plan_steps || [])
        .filter((step) => step.kind !== 'planner')
        .map((step) => ({
          id: step.id,
          title: planDraft.steps[step.id]?.title ?? step.title,
          description: planDraft.steps[step.id]?.description ?? step.description,
          assigned_model: planDraft.steps[step.id]?.assignedModel
            ?? step.assigned_model,
          role: planDraft.steps[step.id]?.role ?? step.role,
          dependencies: step.dependencies,
          success_criteria: step.success_criteria,
        })),
      planDraft.changeSummary.trim() || '执行前人工修订',
    );
    if (sent) setPlanDraft({ ...planDraft, saving: true });
  };

  return (
    <div className="aether-tab-panel aether-panel-scroll min-h-0 flex-1 overflow-auto p-4">
      <section data-testid="git-workspace-config" className="mb-5 rounded-lg border border-[#d6c9b9] bg-[#fbfaf7] p-3">
        <div className="flex items-start justify-between gap-3">
          <div>
            <h2 className="flex items-center gap-2 text-sm font-semibold">
              <GitBranch size={15} />
              {ui('Git Agent 工作区')}
            </h2>
            <p className="mt-1 text-xs leading-5 text-[#7a7065]">
              {ui('每个 Worker 使用独立 worktree；主仓库只在原生确认后应用 diff，不自动 commit 或 push。')}
            </p>
          </div>
          <button
            onClick={onSelectGitRepository}
            disabled={!window.aetherWorkspace}
            className="shrink-0 rounded-md border border-[#8d5f35] px-3 py-1.5 text-xs text-[#6b4525] disabled:cursor-not-allowed disabled:opacity-45"
          >
            {gitRepository ? ui('重新选择仓库') : ui('选择 Git 仓库')}
          </button>
        </div>
        {gitRepository ? (
          <div className="mt-3 space-y-3">
            <div className="rounded-md bg-[#e8eee2] px-3 py-2 text-xs text-[#486342]">
              <div className="font-semibold">{gitRepository.display_name}</div>
              <div className="mt-1">
                {gitRepository.branch} · {gitRepository.head_short} · {ui('登记时工作树干净')}
              </div>
            </div>
            <label className="block text-[11px] text-[#665a4e]">
              <span className="mb-1 block font-semibold">
                {ui('确定性核验命令（每行一条，最多 4 条）')}
              </span>
              <textarea
                aria-label={ui('Git 核验命令')}
                value={verificationCommands}
                onChange={(event) => onVerificationCommandsChange(event.target.value)}
                placeholder="python -m pytest -q"
                className="min-h-20 w-full rounded-md border border-[#d8c8af] bg-white p-2 font-mono text-xs outline-none"
              />
              <span className="mt-1 block text-[#8b8176]">
                {ui('命令会在断网、只挂载对应 worktree 的 Docker 沙箱中执行，并在计划确认页再次展示。')}
              </span>
            </label>
          </div>
        ) : (
          <div className="mt-3 rounded-md border border-dashed border-[#d6cbbd] px-3 py-3 text-xs text-[#7a7065]">
            {window.aetherWorkspace
              ? ui('尚未接入仓库；项目 Worker 继续保持只读。')
              : ui('浏览器预览不能选择或应用本机仓库；请使用 AetherChat 桌面版。')}
          </div>
        )}
      </section>
      <section className="mb-5">
        <div className="mb-2 flex items-center justify-between">
          <div>
            <h2 className="text-sm font-semibold">{ui('Agent 运行记录')}</h2>
            <p className="mt-1 text-xs text-[#7a7065]">{ui('目标、计划、步骤证据和关键事件保存在本机；重启后从未完成步骤继续。')}</p>
          </div>
          <span className="text-xs text-[#8b8176]">{ui('{count} 次', { count: agentRuns.length })}</span>
        </div>
        <div className="space-y-2">
          {agentRuns.map((run) => {
            const status = runStatus[run.status];
            const retryable = ['failed', 'interrupted', 'recoverable'].includes(run.status);
            return (
              <article key={run.id} className="rounded-lg border border-[#ded6ca] bg-[#fbfaf7] p-3">
                <div className="flex items-start justify-between gap-3">
                  <div className="min-w-0">
                    <div className="truncate text-sm font-medium">{run.input.content || ui('未命名运行')}</div>
                    <div className="mt-1 text-[11px] text-[#8b8176]">{new Date(run.updated_at).toLocaleString()} · {ui('重试 {count} 次', { count: run.retry_count })}</div>
                    <div className="mt-1 text-[11px] text-[#7a7065]">
                      {ui('工具 {tools} · 检查点 {checkpoints} · 转向 {steers}', {
                        tools: run.tool_call_count || 0,
                        checkpoints: run.checkpoint_count || 0,
                        steers: run.steer_count || 0,
                      })}
                    </div>
                    {!!run.plan_step_count && (
                      <div className="mt-1 text-[11px] text-[#7a7065]">
                        {ui('计划 {completed}/{total} · 核验 {reviews}', {
                          completed: run.completed_plan_step_count || 0,
                          total: run.plan_step_count || 0,
                          reviews: run.review_count || 0,
                        })}
                      </div>
                    )}
                    {!!run.plan_revision && (
                      <div className="mt-1 text-[11px] text-[#7a7065]">
                        {ui('计划版本 {revision} · 自动返工 {count}/{max}', {
                          revision: run.plan_revision,
                          count: run.automatic_revision_count || 0,
                          max: run.input.max_revision_cycles ?? 1,
                        })}
                      </div>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <span className={`rounded-md px-2 py-1 text-xs ${status.className}`}>{ui(status.label)}</span>
                    {retryable && <button onClick={() => onRetry(run)} className="rounded-md border border-[#8d5f35] px-2 py-1 text-xs text-[#6b4525]">{ui('重试未完成步骤')}</button>}
                  </div>
                </div>
                {run.status === 'waiting_plan_confirmation' && (
                  <div className="mt-3 rounded-md border border-[#d7b982] bg-[#fbf1dc] px-3 py-2">
                    <div className="flex flex-wrap items-center gap-2">
                      <span className="mr-auto text-xs text-[#76551d]">
                        {ui('Worker 尚未启动；确认前不会产生后续模型调用。')}
                      </span>
                      <button
                        onClick={() => planDraft?.runId === run.id ? setPlanDraft(null) : beginPlanEdit(run)}
                        className="rounded-md border border-[#9a7541] px-2 py-1 text-xs text-[#6b4d21]"
                      >
                        {planDraft?.runId === run.id ? ui('取消编辑') : ui('编辑计划')}
                      </button>
                      <button
                        onClick={() => onConfirmPlan(run)}
                        disabled={planDraft?.runId === run.id}
                        title={planDraft?.runId === run.id ? ui('请先保存或取消编辑') : undefined}
                        className="rounded-md bg-[#2f6a43] px-2 py-1 text-xs text-white disabled:cursor-not-allowed disabled:opacity-45"
                      >
                        {ui('确认并执行')}
                      </button>
                      <button
                        onClick={() => {
                          if (window.confirm(ui('拒绝该计划并结束本次运行？'))) {
                            onRejectPlan(run);
                          }
                        }}
                        className="rounded-md border border-[#a35d50] px-2 py-1 text-xs text-[#873f34]"
                      >
                        {ui('拒绝计划')}
                      </button>
                    </div>
                    {run.input.repository_id && (
                      <div className="mt-2 border-t border-[#e2cfa9] pt-2 text-[11px] leading-5 text-[#76551d]">
                        <div>
                          {ui('仓库快照：{name} · {branch} · {commit}', {
                            name: run.input.repository_name || 'Git',
                            branch: run.input.repository_branch || '(detached)',
                            commit: (run.input.repository_base_commit || '').slice(0, 12),
                          })}
                        </div>
                        <div>
                          {ui('核验命令：{commands}', {
                            commands: run.input.verification_commands?.length
                              ? run.input.verification_commands.join('；')
                              : '未配置（候选将标记为未验证）',
                          })}
                        </div>
                      </div>
                    )}
                  </div>
                )}
                {run.goal && (
                  <div className="mt-3 rounded-md border border-[#e2d8cb] bg-white px-3 py-2 text-[11px] leading-5 text-[#665a4e]">
                    <div>{ui('目标：{value}', { value: run.goal.objective })}</div>
                    <div>{ui('交付：{value}', { value: run.goal.deliverable })}</div>
                  </div>
                )}
                {planDraft?.runId === run.id && run.goal && (
                  <div className="mt-3 space-y-3 rounded-lg border border-[#c6a977] bg-[#fffaf0] p-3">
                    <div className="text-xs font-semibold text-[#684b22]">{ui('编辑计划草稿')}</div>
                    <label className="block text-[11px] text-[#665a4e]">
                      <span className="mb-1 block font-semibold">{ui('目标')}</span>
                      <textarea
                        aria-label={ui('计划目标')}
                        value={planDraft.objective}
                        onChange={(event) => setPlanDraft({ ...planDraft, objective: event.target.value })}
                        className="min-h-20 w-full rounded-md border border-[#d8c8af] bg-white p-2 text-xs outline-none"
                      />
                    </label>
                    <label className="block text-[11px] text-[#665a4e]">
                      <span className="mb-1 block font-semibold">{ui('交付物')}</span>
                      <textarea
                        aria-label={ui('计划交付物')}
                        value={planDraft.deliverable}
                        onChange={(event) => setPlanDraft({ ...planDraft, deliverable: event.target.value })}
                        className="min-h-16 w-full rounded-md border border-[#d8c8af] bg-white p-2 text-xs outline-none"
                      />
                    </label>
                    <div className="grid gap-3 sm:grid-cols-2">
                      <label className="block text-[11px] text-[#665a4e]">
                        <span className="mb-1 block font-semibold">{ui('成功条件（每行一条）')}</span>
                        <textarea
                          aria-label={ui('计划成功条件')}
                          value={planDraft.successCriteria}
                          onChange={(event) => setPlanDraft({ ...planDraft, successCriteria: event.target.value })}
                          className="min-h-24 w-full rounded-md border border-[#d8c8af] bg-white p-2 text-xs outline-none"
                        />
                      </label>
                      <label className="block text-[11px] text-[#665a4e]">
                        <span className="mb-1 block font-semibold">{ui('约束（每行一条）')}</span>
                        <textarea
                          aria-label={ui('计划约束')}
                          value={planDraft.constraints}
                          onChange={(event) => setPlanDraft({ ...planDraft, constraints: event.target.value })}
                          className="min-h-24 w-full rounded-md border border-[#d8c8af] bg-white p-2 text-xs outline-none"
                        />
                      </label>
                    </div>
                    <div className="space-y-2">
                      {(run.plan_steps || []).filter((step) => step.kind !== 'planner').map((step) => {
                        const draftStep = planDraft.steps[step.id];
                        if (!draftStep) return null;
                        return (
                          <div key={step.id} className="grid gap-2 rounded-md border border-[#dfd1bd] bg-white p-2 sm:grid-cols-2">
                            <input
                              aria-label={`${step.kind} ${ui('步骤标题')}`}
                              value={draftStep.title}
                              onChange={(event) => setPlanDraft({
                                ...planDraft,
                                steps: {
                                  ...planDraft.steps,
                                  [step.id]: { ...draftStep, title: event.target.value },
                                },
                              })}
                              className="h-8 rounded border border-[#ddd1c1] px-2 text-xs outline-none"
                            />
                            <select
                              aria-label={`${step.kind} ${ui('执行模型')}`}
                              value={draftStep.assignedModel}
                              onChange={(event) => setPlanDraft({
                                ...planDraft,
                                steps: {
                                  ...planDraft.steps,
                                  [step.id]: { ...draftStep, assignedModel: event.target.value },
                                },
                              })}
                              className="h-8 rounded border border-[#ddd1c1] bg-white px-2 text-xs outline-none"
                            >
                              {run.targets.map((model) => <option key={model} value={model}>{model}</option>)}
                            </select>
                            <input
                              aria-label={`${step.kind} ${ui('步骤角色')}`}
                              value={draftStep.role}
                              onChange={(event) => setPlanDraft({
                                ...planDraft,
                                steps: {
                                  ...planDraft.steps,
                                  [step.id]: { ...draftStep, role: event.target.value },
                                },
                              })}
                              className="h-8 rounded border border-[#ddd1c1] px-2 text-xs outline-none sm:col-span-2"
                            />
                            <textarea
                              aria-label={`${step.kind} ${ui('步骤说明')}`}
                              value={draftStep.description}
                              onChange={(event) => setPlanDraft({
                                ...planDraft,
                                steps: {
                                  ...planDraft.steps,
                                  [step.id]: { ...draftStep, description: event.target.value },
                                },
                              })}
                              className="min-h-16 rounded border border-[#ddd1c1] p-2 text-xs outline-none sm:col-span-2"
                            />
                          </div>
                        );
                      })}
                    </div>
                    <label className="block text-[11px] text-[#665a4e]">
                      <span className="mb-1 block font-semibold">{ui('修订说明')}</span>
                      <input
                        aria-label={ui('计划修订说明')}
                        value={planDraft.changeSummary}
                        onChange={(event) => setPlanDraft({ ...planDraft, changeSummary: event.target.value })}
                        placeholder={ui('例如：调整交付范围和核验模型')}
                        className="h-8 w-full rounded border border-[#ddd1c1] bg-white px-2 text-xs outline-none"
                      />
                    </label>
                    <div className="flex justify-end gap-2">
                      <button onClick={() => setPlanDraft(null)} className="rounded border border-[#cdbda6] px-3 py-1.5 text-xs">{ui('取消')}</button>
                      <button
                        onClick={() => savePlanEdit(run)}
                        disabled={planDraft.saving || !planDraft.objective.trim() || !planDraft.deliverable.trim() || !planDraft.successCriteria.trim()}
                        className="rounded bg-[#8d5f35] px-3 py-1.5 text-xs text-white disabled:opacity-40"
                      >
                        {planDraft.saving ? ui('保存中...') : ui('保存计划草稿')}
                      </button>
                    </div>
                  </div>
                )}
                {!!run.plan_steps?.length ? (
                  <ol className="mt-3 grid gap-2 sm:grid-cols-2">
                    {run.plan_steps.map((step) => (
                      <li key={step.id} title={step.error || step.description || undefined} className="rounded-md border border-[#e1d8cd] bg-[#f5f1eb] px-3 py-2 text-[11px] text-[#665a4e]">
                        <div className="flex items-center justify-between gap-2">
                          <span className="font-semibold">
                            {ui(planKindLabel[step.kind])}
                            {step.assigned_model ? ` · ${step.assigned_model}` : ''}
                          </span>
                          <span className="flex items-center gap-2">
                            <span>{ui(planStepStatus[step.status])}</span>
                            {step.status === 'running' && step.assigned_model && (
                              <button
                                onClick={() => onInterruptStep(run, step.id)}
                                className="rounded border border-[#b88a72] px-1.5 py-0.5 text-[10px] text-[#7a3b2e]"
                              >
                                {ui('中断步骤')}
                              </button>
                            )}
                          </span>
                        </div>
                        <div className="mt-1 truncate text-[#8b8176]">
                          {step.role || ui('未分配')} · {ui('重试 {count} 次', { count: Math.max(0, (step.attempt || 0) - 1) })}
                        </div>
                      </li>
                    ))}
                  </ol>
                ) : (
                  <div className="mt-3 flex flex-wrap gap-2">
                    {run.steps.map((step) => (
                      <span key={step.id} title={step.error || undefined} className="rounded bg-[#eee7de] px-2 py-1 text-[11px] text-[#665a4e]">
                        {step.model} · {step.status} · 第 {step.attempt || 0} 次
                      </span>
                    ))}
                  </div>
                )}
                {!!run.reviews?.length && (
                  <div className="mt-2 rounded-md bg-[#e8eee2] px-3 py-2 text-[11px] leading-5 text-[#486342]">
                    <div className="font-semibold">
                      {ui('核验结论：{verdict}', {
                        verdict: ui(reviewVerdict[run.reviews[run.reviews.length - 1].verdict]),
                      })}
                    </div>
                    <div>{run.reviews[run.reviews.length - 1].summary}</div>
                  </div>
                )}
                {!!run.workspace_candidates?.length && (
                  <div className="mt-3 space-y-2">
                    <div className="text-[11px] font-semibold text-[#665a4e]">
                      {ui('隔离 worktree 候选')}
                    </div>
                    {run.workspace_candidates.map((candidate) => {
                      const verificationTone = candidate.verification_status === 'passed'
                        ? 'border-[#b9ceb2] bg-[#eef5eb] text-[#3f6c3a]'
                        : candidate.verification_status === 'failed' || candidate.verification_status === 'no_changes'
                          ? 'border-[#d9b6af] bg-[#f8ece9] text-[#873f34]'
                          : 'border-[#d7c59f] bg-[#fff8e9] text-[#76551d]';
                      const canDecide = candidate.apply_status === 'pending'
                        && !['queued', 'running', 'waiting_approval', 'waiting_plan_confirmation'].includes(run.status);
                      const canApply = canDecide
                        && ['completed', 'failed', 'interrupted'].includes(run.status)
                        && Boolean(candidate.diff_sha256);
                      return (
                        <div key={candidate.id} className={`rounded-md border px-3 py-2 text-[11px] ${verificationTone}`}>
                          <div className="flex flex-wrap items-center justify-between gap-2">
                            <span className="font-semibold">
                              {ui('候选 #{attempt} · {status}', {
                                attempt: candidate.attempt,
                                status: ui(workspaceVerificationLabel[candidate.verification_status]),
                              })}
                            </span>
                            <span>
                              {candidate.changed_files.length} files · {(candidate.diff_bytes / 1024).toFixed(1)} KiB
                            </span>
                          </div>
                          {!!candidate.changed_files.length && (
                            <div className="mt-1 break-all text-[#665a4e]">
                              {candidate.changed_files.slice(0, 8).map((file) => (
                                `${file.status.trim() || 'M'} ${file.path}`
                              )).join(' · ')}
                              {candidate.changed_files.length > 8 ? ' …' : ''}
                            </div>
                          )}
                          {!!candidate.verification.length && (
                            <ul className="mt-2 space-y-1 text-[#665a4e]">
                              {candidate.verification.map((check) => (
                                <li key={`${candidate.id}-${check.command}`}>
                                  <code>{check.command}</code> · exit {check.exit_code}
                                  {check.timed_out ? ` · ${ui('超时')}` : ''}
                                </li>
                              ))}
                            </ul>
                          )}
                          {candidate.error && <div className="mt-1">{candidate.error}</div>}
                          {candidate.apply_status !== 'pending' ? (
                            <div className="mt-2 font-semibold">
                              {candidate.apply_status === 'applied'
                                ? ui('已应用（未 commit / push）')
                                : ui('候选已丢弃')}
                            </div>
                          ) : canDecide ? (
                            <div className="mt-2 flex justify-end gap-2">
                              <button
                                onClick={() => onDecideWorkspaceCandidate(candidate, 'reject')}
                                className="rounded border border-current px-2 py-1"
                              >
                                {ui('丢弃候选')}
                              </button>
                              <button
                                onClick={() => onDecideWorkspaceCandidate(candidate, 'apply')}
                                disabled={!window.aetherWorkspace || !canApply}
                                title={!candidate.diff_sha256 ? ui('该候选没有可应用的 diff') : undefined}
                                className="rounded bg-[#2f6a43] px-2 py-1 text-white disabled:opacity-45"
                              >
                                {ui('审核并应用 diff')}
                              </button>
                            </div>
                          ) : null}
                        </div>
                      );
                    })}
                  </div>
                )}
                {run.error && <div className="mt-2 text-xs text-[#8a3b31]">{run.error}</div>}
              </article>
            );
          })}
          {!agentRuns.length && <div className="rounded-lg border border-dashed border-[#d6cbbd] p-5 text-center text-xs text-[#7a7065]">{ui('当前会话暂无 Agent 运行记录')}</div>}
        </div>
      </section>
      <div className="mb-3 flex items-center justify-between">
        <div className="flex items-center gap-2 text-sm font-semibold">
          <SquareTerminal size={16} />
          {ui('运行与沙箱')}
        </div>
        <div className="flex items-center gap-2">
          <button onClick={runCheck} disabled={checking} className="flex items-center gap-1 rounded-md bg-[#2f2a24] px-2 py-1 text-xs text-white disabled:cursor-not-allowed disabled:opacity-60">
            <Play size={13} />
            {checking ? ui('检查中') : ui('基础检查')}
          </button>
          <button onClick={verifyConfiguredModels} disabled={verifyingModels} className="flex items-center gap-1 rounded-md border border-[#8d5f35] px-2 py-1 text-xs text-[#6b4525] disabled:cursor-not-allowed disabled:opacity-60">
            {verifyingModels ? ui('验证中') : ui('验证模型（可能计费）')}
          </button>
          <button onClick={() => exportSession('markdown')} className="flex items-center gap-1 rounded-md bg-[#8d5f35] px-2 py-1 text-xs text-white">
            {ui('导出 MD')}
          </button>
          <button onClick={() => exportSession('json')} className="flex items-center gap-1 rounded-md bg-[#8d5f35] px-2 py-1 text-xs text-white">
            {ui('导出 JSON')}
          </button>
        </div>
      </div>
      <div className="rounded-lg border border-[#1f1b17] bg-[#2f2a24] p-3 font-mono text-xs text-[#f5efe7]">
        <div>$ AetherChat runtime check</div>
        <div className="mt-1 text-[#b8d8af]">{ui('最后检查：{value}', { value: ui(lastChecked) })}</div>
        <div className="mt-1 text-[#d7c9b6]">{ui('基础检查只读取本机状态，不调用模型 API；模型验证会先请求确认。')}</div>
      </div>
      <div className="mt-4 space-y-2">
        {rows.map(({ name, detail, status, tone }) => (
          <div key={name} className="rounded-lg border border-[#ded6ca] bg-[#fbfaf7] p-3">
            <div className="flex items-center justify-between text-sm font-medium">
              <span>{ui(name)}</span>
              <span className={`rounded-md px-2 py-1 text-xs ${toneClass[tone]}`}>{ui(status)}</span>
            </div>
            <div className="mt-1 text-xs text-[#7a7065]">{ui(detail)}</div>
          </div>
        ))}
      </div>
    </div>
  );
}

interface DesktopStatusBarProps {
  connection: ReturnType<typeof useSessionStore.getState>['connection'];
  budgetRemaining: number;
  uiLocale: WorkspaceSettings['uiLocale'];
  sandbox: WorkspaceSettings['sandbox'];
  tokenStatus: string;
  onRefreshToken: () => void;
  onToggleLanguage: () => void;
}

function DesktopStatusBar({ connection, budgetRemaining, uiLocale, sandbox, tokenStatus, onRefreshToken, onToggleLanguage }: DesktopStatusBarProps) {
  const { t, ui } = useI18n();
  return (
    <footer className="flex h-7 items-center gap-4 border-t border-[#cfc4b7] bg-[#2f2a24] px-3 text-[11px] text-[#e7dfd3]">
      <span className="flex items-center gap-1"><FolderOpen size={12} /> {ui('本地工作区')}</span>
      <span>{t('sandbox', { value: sandbox })}</span>
      <span>WS {connection === 'online' ? 'connected' : connection === 'connecting' ? 'connecting' : 'offline'}</span>
      <span>{t('budget', { value: budgetRemaining.toFixed(2) })}</span>
      <button onClick={onRefreshToken} title="刷新本地 WebSocket 授权，不显示或复制令牌" className="rounded bg-[#8d5f35] px-1.5 py-0.5 text-[10px] text-white">{tokenStatus}</button>
      <button onClick={onToggleLanguage} title={t('switchUiLanguage')} aria-label={t('switchUiLanguage')} className="rounded bg-[#8d5f35] px-1.5 py-0.5 text-[10px] text-white">{uiLocale === 'zh-CN' ? 'EN' : '中文'}</button>
      <span className="ml-auto">AetherChat Desktop · {ui('多模型 Agent 工作台')}</span>
    </footer>
  );
}

export default App;
