import type { Dispatch, SetStateAction } from 'react';
import { fetchJson } from '../api/client';
import { API_BASE, formatDateTime } from '../components/shared';
import { useSessionStore } from '../stores/sessionStore';
import type { AgentRun, Conversation, GitRepository, MergeProposal, Message, ModelConfig, Task, TaskEvent, ToolItem, ToolRun } from '../types';

type SessionStoreState = ReturnType<typeof useSessionStore.getState>;

export type StoredSession = {
  id: string;
  title: string;
  summary: string;
  mode: 'casual' | 'roundtable' | 'project';
  project_id?: string | null;
  created_at: string;
  updated_at: string;
  last_message_at?: string | null;
  message_count: number;
  task_count: number;
  open_task_count: number;
};

export const conversationFromSession = (session: StoredSession): Conversation => ({
  id: session.id,
  title: session.title,
  summary: session.summary || '尚未开始对话',
  updatedAt: formatDateTime(session.last_message_at || session.updated_at),
  createdAt: session.created_at,
  mode: session.mode,
  projectId: session.project_id ?? null,
  messageCount: session.message_count,
  taskCount: session.task_count,
  openTaskCount: session.open_task_count,
});

export type UseSessionLoadersParams = {
  sessionId: string;
  currentMode: 'casual' | 'roundtable' | 'project';
  /** Governance project for auto-created active session rows. */
  preferredProjectId?: string | null;
  setTasks: SessionStoreState['setTasks'];
  setConversations: SessionStoreState['setConversations'];
  setMessages: SessionStoreState['setMessages'];
  setArtifacts: SessionStoreState['setArtifacts'];
  setMergeProposals: SessionStoreState['setMergeProposals'];
  setTools: SessionStoreState['setTools'];
  setToolRuns: SessionStoreState['setToolRuns'];
  setModelConfigs: SessionStoreState['setModelConfigs'];
  setTaskEvents: Dispatch<SetStateAction<TaskEvent[]>>;
  setAgentRuns: Dispatch<SetStateAction<AgentRun[]>>;
  setGitRepository: Dispatch<SetStateAction<GitRepository | null>>;
  setVerificationCommands: Dispatch<SetStateAction<string>>;
  /** Called once the first /models/ fetch settles (success or empty fallback). */
  onModelsHydrated?: () => void;
};

export type SessionLoaders = {
  loadTasks: () => void;
  loadConversations: () => Promise<void>;
  loadMessages: () => void;
  loadTaskEvents: () => void;
  loadAgentRuns: () => void;
  loadGitRepositories: () => void;
  loadArtifacts: () => void;
  loadMerges: () => void;
  loadTools: () => void;
  loadModels: () => void;
};

export function useSessionLoaders({
  sessionId,
  currentMode,
  preferredProjectId,
  setTasks,
  setConversations,
  setMessages,
  setArtifacts,
  setMergeProposals,
  setTools,
  setToolRuns,
  setModelConfigs,
  setTaskEvents,
  setAgentRuns,
  setGitRepository,
  setVerificationCommands,
  onModelsHydrated,
}: UseSessionLoadersParams): SessionLoaders {
  const loadTasks = () => {
    fetchJson<Task[]>(`${API_BASE}/tasks/?session_id=${sessionId}`).then(setTasks).catch(() => setTasks([]));
  };

  const loadConversations = async () => {
    try {
      const activeSessionId = useSessionStore.getState().sessionId;
      let rows = await fetchJson<StoredSession[]>(`${API_BASE}/sessions/`);
      if (!rows.some((session) => session.id === activeSessionId)) {
        const body: Record<string, unknown> = {
          id: activeSessionId,
          title: '新会话',
          mode: currentMode,
        };
        if (preferredProjectId) {
          body.project_id = preferredProjectId;
        }
        await fetchJson<StoredSession>(`${API_BASE}/sessions/`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(body),
        });
        rows = await fetchJson<StoredSession[]>(`${API_BASE}/sessions/`);
      }
      setConversations(rows.map(conversationFromSession));
    } catch {
      // 保留本地缓存，后端恢复连接后会再次刷新。
    }
  };

  const loadMessages = () => {
    fetchJson<Message[]>(`${API_BASE}/sessions/${sessionId}/messages?limit=5000`).then(setMessages).catch(() => setMessages([]));
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
    fetchJson<ModelConfig[]>(`${API_BASE}/models/`)
      .then((models) => {
        setModelConfigs(models);
      })
      .catch(() => {
        setModelConfigs([]);
      })
      .finally(() => {
        onModelsHydrated?.();
      });
  };

  return {
    loadTasks,
    loadConversations,
    loadMessages,
    loadTaskEvents,
    loadAgentRuns,
    loadGitRepositories,
    loadArtifacts,
    loadMerges,
    loadTools,
    loadModels,
  };
}
