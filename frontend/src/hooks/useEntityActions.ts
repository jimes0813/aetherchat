import type { Dispatch, MutableRefObject, SetStateAction } from 'react';
import { API_BASE, isConfiguredModel } from '../components/shared';
import { fetchJson, withApiAuth } from '../api/client';
import { connectWS } from '../ws/client';
import { useSessionStore } from '../stores/sessionStore';
import type { ApprovalRequest, GitRepository, KnowledgeAttachment, MergeProposal, ModelConfig, Task, ToolItem, ToolRun } from '../types';

type SessionStoreState = ReturnType<typeof useSessionStore.getState>;

export type UseEntityActionsParams = {
  sessionId: string;
  currentMode: 'casual' | 'roundtable' | 'project';
  workspaceSettings: SessionStoreState['settings'];
  conversations: SessionStoreState['conversations'];
  modelConfigs: SessionStoreState['modelConfigs'];
  gitRepository: GitRepository | null;
  verificationCommands: string;
  selectedArtifactPath: SessionStoreState['selectedArtifactPath'];
  wsClient: MutableRefObject<ReturnType<typeof connectWS> | null>;
  setNotice: Dispatch<SetStateAction<string>>;
  setApprovals: Dispatch<SetStateAction<ApprovalRequest[]>>;
  setMainView: Dispatch<SetStateAction<'chat' | 'history' | 'tasks' | 'settings'>>;
  setConversations: SessionStoreState['setConversations'];
  selectConversation: SessionStoreState['selectConversation'];
  setActivePanel: SessionStoreState['setActivePanel'];
  updateTask: SessionStoreState['updateTask'];
  updateMergeProposal: SessionStoreState['updateMergeProposal'];
  upsertTool: SessionStoreState['upsertTool'];
  removeTool: SessionStoreState['removeTool'];
  addToolRun: SessionStoreState['addToolRun'];
  upsertModelConfig: SessionStoreState['upsertModelConfig'];
  removeModelConfig: SessionStoreState['removeModelConfig'];
  loadTaskEvents: () => void;
  loadConversations: () => Promise<void>;
  createPersistentConversation: (
    modeOverride?: 'casual' | 'roundtable' | 'project' | null,
  ) => Promise<string | null>;
};

export type UseEntityActionsResult = {
  sendMessage: (
    content: string,
    targets: string[],
    attachmentIds: string[],
    references?: Array<{ kind: string; id: string; session_id?: string; label?: string }>,
  ) => boolean;
  exportSession: (fmt?: 'markdown' | 'json') => Promise<void>;
  decideApproval: (
    approval: ApprovalRequest,
    decision: 'allow_once' | 'allow_session' | 'deny',
    feedback?: string,
  ) => Promise<void>;
  uploadAttachments: (files: File[]) => Promise<KnowledgeAttachment[]>;
  createTask: (title: string, dependsOn?: string[]) => Promise<void>;
  patchTask: (task: Task, patch: Partial<Task>) => Promise<void>;
  createMerge: (artifactPath: string, rationale: string) => Promise<void>;
  voteMerge: (proposal: MergeProposal, approve: boolean) => Promise<void>;
  createTool: (payload: Partial<ToolItem>) => Promise<void>;
  deleteTool: (tool: ToolItem) => Promise<void>;
  invokeTool: (tool: ToolItem, caller?: string) => Promise<void>;
  createModel: (payload: Partial<ModelConfig> & { api_key?: string }) => Promise<void>;
  deleteModel: (model: ModelConfig) => Promise<void>;
  deleteConversation: (conversationId: string) => Promise<void>;
};

export function useEntityActions({
  sessionId,
  currentMode,
  workspaceSettings,
  conversations,
  modelConfigs,
  gitRepository,
  verificationCommands,
  selectedArtifactPath,
  wsClient,
  setNotice,
  setApprovals,
  setMainView,
  setConversations,
  selectConversation,
  setActivePanel,
  updateTask,
  updateMergeProposal,
  upsertTool,
  removeTool,
  addToolRun,
  upsertModelConfig,
  removeModelConfig,
  loadTaskEvents,
  loadConversations,
  createPersistentConversation,
}: UseEntityActionsParams): UseEntityActionsResult {
  const sendMessage = (
    content: string,
    targets: string[],
    attachmentIds: string[],
    references: Array<{ kind: string; id: string; session_id?: string; label?: string }> = [],
  ) => {
    const resolvedTargets = targets.includes('all')
      ? modelConfigs
          .filter(isConfiguredModel)
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
      references: references.map((item) => ({
        kind: item.kind,
        id: item.id,
        ...(item.session_id ? { session_id: item.session_id } : {}),
      })),
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
      setNotice('后端未连接，消息未发送。内容仍保留在输入框中，请等待连接恢复后重试。');
    }
    return Boolean(sent);
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

  const decideApproval = async (
    approval: ApprovalRequest,
    decision: 'allow_once' | 'allow_session' | 'deny',
    feedback = '',
  ) => {
    const normalizedFeedback = decision === 'deny' ? feedback.trim() : '';
    try {
      await fetchJson(`${API_BASE}/approvals/${approval.id}/decision`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          session_id: sessionId,
          decision,
          ...(normalizedFeedback ? { feedback: normalizedFeedback } : {}),
        }),
      });
      setApprovals((items) => items.map((item) => item.id === approval.id
        ? { ...item, decision, feedback: normalizedFeedback || undefined }
        : item));
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

  const createTask = async (title: string, dependsOn: string[] = []) => {
    const task = await fetchJson<Task>(`${API_BASE}/tasks/`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        session_id: sessionId,
        title,
        description: '由中文工作台创建',
        priority: 1,
        depends_on: dependsOn,
      }),
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

  const deleteConversation = async (conversationId: string) => {
    const conversation = conversations.find((item) => item.id === conversationId);
    if (!conversation) return;
    if (!window.confirm(`确定删除会话“${conversation.title}”吗？该会话的消息、任务、资料和生成文件将一并删除。`)) return;
    try {
      await fetchJson(`${API_BASE}/sessions/${conversationId}`, {
        method: 'DELETE',
      });
      const remaining = conversations.filter((item) => item.id !== conversationId);
      setConversations(remaining);
      if (conversationId === sessionId) {
        if (remaining.length) {
          selectConversation(remaining[0].id);
          setMainView('chat');
        } else {
          await createPersistentConversation();
        }
      }
      await loadConversations();
      setNotice(`会话“${conversation.title}”已删除`);
    } catch (error) {
      setNotice(`删除会话失败：${String(error)}`);
    }
  };

  return {
    sendMessage,
    exportSession,
    decideApproval,
    uploadAttachments,
    createTask,
    patchTask,
    createMerge,
    voteMerge,
    createTool,
    deleteTool,
    invokeTool,
    createModel,
    deleteModel,
    deleteConversation,
  };
}
