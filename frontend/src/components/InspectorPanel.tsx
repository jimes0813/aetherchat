import { useEffect, useState } from 'react';
import {
  Archive,
  Brain,
  Camera,
  Cable,
  FileText,
  FolderOpen,
  GitBranch,
  GitMerge,
  Images,
  Laptop,
  MousePointer2,
  Play,
  Plus,
  Puzzle,
  PackageCheck,
  Cpu,
  Search,
  Save,
  ShieldCheck,
  SquareTerminal,
  Trash2,
  X,
} from 'lucide-react';
import { AutomationPanel } from './AutomationPanel';
import { TaskBoard } from './TaskBoard';
import { SafeMarkdown } from './SafeMarkdown';
import { RunPanel } from './RunPanel';
import { LocalModelQuickPath } from './LocalModelQuickPath';
import { SkillsRulesPanel } from './SkillsRulesPanel';
import {
  formatAskWithSources,
  formatCitationForComposer,
  formatKnowledgeStatsLine,
  formatLayerLabel,
} from './knowledgeUi';
import { startAndVerifyLocalModel } from './localModelRuntime';
import { fetchJson, withApiAuth } from '../api/client';
import { useI18n } from '../i18n';
import { useSessionStore } from '../stores/sessionStore';
import { API_BASE, formatDateTime, isConfiguredModel, taskStatusLabel } from './shared';
import type { DesktopMouseResult, SelectedDesktopDocument } from './shared';
import type { AgentRun, AgentWorkspaceCandidate, GitRepository, KnowledgeAttachment, MCPConnection, MergeProposal, ModelConfig, PluginInvokeResult, PluginItem, SessionMemory, Task, ToolItem, WorkspaceSettings } from '../types';

type KnowledgeSearchHit = {
  attachment_id: string;
  source_name: string;
  locator: string;
  content: string;
  citation: string;
  score: number;
  match_types: string[];
  rank?: number;
  layers?: {
    lexical?: number;
    vector?: number;
    coverage?: number;
    exact?: number;
  };
};

type KnowledgeStats = {
  attachment_count: number;
  ready_count: number;
  chunk_count: number;
  vector_count: number;
  total_bytes: number;
  extensions?: Record<string, number>;
  retrieval?: { lexical?: string; vector?: string; hybrid?: string; offline?: boolean };
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
  content_encoding: 'utf-8' | 'base64' | 'external';
};

interface InspectorProps {
  apiBase: string;
  sessionId: string;
  selectedArtifactPath: string;
  onSelectArtifact: (path: string) => void;
  onCreateTask: (title: string, dependsOn?: string[]) => void;
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
  /** Insert text into the chat composer (knowledge citations). */
  onInsertComposer?: (text: string) => void;
}

export function InspectorPanel(props: InspectorProps) {
  const {
    activePanel,
    setActivePanel,
    artifacts,
    tasks,
    mergeProposals,
    tools,
    toolRuns,
    modelConfigs,
    settings: workspaceSettings,
    providers,
  } = useSessionStore();
  const { t, ui } = useI18n();
  const activePanelLabel = activePanel ? ({
    记忆: t('memory'), 资料: t('knowledge'), 任务: t('tasks'), 自动化: t('automations'), 工件: t('artifacts'), 合并: t('changes'),
    工具: t('tools'), 插件: t('plugins'), 连接: t('connections'), 电脑: t('computer'), 模型: t('models'),
    运行: t('runs'), 设置: t('settings'), 规范: t('instructions'),
  } as Record<string, string>)[activePanel] : ui('检查器');
  const [artifactContent, setArtifactContent] = useState('');
  const [artifactPreview, setArtifactPreview] = useState<ArtifactPreview | null>(null);
  const [artifactVersions, setArtifactVersions] = useState<ArtifactVersion[]>([]);
  const [artifactView, setArtifactView] = useState<'preview' | 'edit' | 'diff' | 'history'>('preview');
  const [artifactDiff, setArtifactDiff] = useState('');
  const [artifactStatus, setArtifactStatus] = useState('');
  const [taskTitle, setTaskTitle] = useState('');
  const [taskDependencies, setTaskDependencies] = useState<string[]>([]);
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
  const [modelRuntimeArgs, setModelRuntimeArgs] = useState(
    '--n-gpu-layers\n-1\n--ctx-size\n32768\n--flash-attn\non\n--parallel\n1',
  );
  const [editingModelName, setEditingModelName] = useState<string | null>(null);
  const [modelVerifyStatus, setModelVerifyStatus] = useState('未验证');
  const [knowledgeAttachments, setKnowledgeAttachments] = useState<KnowledgeAttachment[]>([]);
  const [knowledgeQuery, setKnowledgeQuery] = useState('');
  const [knowledgeResults, setKnowledgeResults] = useState<KnowledgeSearchHit[]>([]);
  const [knowledgeStatus, setKnowledgeStatus] = useState('');
  const [knowledgeProgress, setKnowledgeProgress] = useState(0);
  const [knowledgePreview, setKnowledgePreview] = useState<KnowledgePreview | null>(null);
  const [knowledgeStats, setKnowledgeStats] = useState<KnowledgeStats | null>(null);
  const [knowledgeExtFilter, setKnowledgeExtFilter] = useState('');
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
  const [diagnosticStatus, setDiagnosticStatus] = useState('');

  const artifactPathUrl = (path: string) => path.split('/').map(encodeURIComponent).join('/');

  const downloadDiagnosticBundle = async () => {
    setDiagnosticStatus(ui('正在生成脱敏诊断包...'));
    try {
      const response = await fetch(
        `${props.apiBase}/diagnostics/bundle`,
        withApiAuth({ method: 'POST' }),
      );
      if (!response.ok) throw new Error(`HTTP ${response.status}`);
      const blob = await response.blob();
      const disposition = response.headers.get('Content-Disposition') || '';
      const match = /filename="?([^";]+)"?/i.exec(disposition);
      const filename = (match?.[1] || 'aetherchat-diagnostics.zip')
        .replace(/[^A-Za-z0-9._-]/g, '_');
      const url = URL.createObjectURL(blob);
      const anchor = document.createElement('a');
      anchor.href = url;
      anchor.download = filename;
      document.body.appendChild(anchor);
      anchor.click();
      anchor.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 0);
      setDiagnosticStatus(ui('诊断包已导出；不含请求正文、文件内容或凭据。'));
    } catch (error) {
      setDiagnosticStatus(ui(`诊断包导出失败：${String(error)}`));
    }
  };

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
      const [attachments, stats] = await Promise.all([
        fetchJson<KnowledgeAttachment[]>(
          `${props.apiBase}/knowledge/${props.sessionId}/attachments`,
        ),
        fetchJson<KnowledgeStats>(
          `${props.apiBase}/knowledge/${props.sessionId}/stats`,
        ).catch(() => null),
      ]);
      setKnowledgeAttachments(attachments);
      setKnowledgeStats(stats);
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

  const removeMemoryEntity = async (key: string) => {
    if (!sessionMemory) return;
    try {
      const memory = await fetchJson<SessionMemory>(`${props.apiBase}/memory/${props.sessionId}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          entities: sessionMemory.entities.filter((entity) => entity.key !== key),
        }),
      });
      setSessionMemory(memory);
      setMemoryStatus(`共享实体 ${key} 已删除`);
    } catch (error) {
      setMemoryStatus(`共享实体删除失败：${String(error)}`);
    }
  };

  const searchKnowledge = async () => {
    if (!knowledgeQuery.trim()) return;
    setKnowledgeStatus('正在检索...');
    setKnowledgeProgress(25);
    try {
      setKnowledgeProgress(55);
      const params = new URLSearchParams({
        q: knowledgeQuery.trim(),
        limit: '20',
      });
      if (knowledgeExtFilter) params.set('extension', knowledgeExtFilter);
      const rows = await fetchJson<KnowledgeSearchHit[]>(
        `${props.apiBase}/knowledge/${props.sessionId}/search?${params.toString()}`,
      );
      setKnowledgeResults(rows);
      const layerHint = knowledgeStats?.retrieval?.offline ? '本地离线' : '混合';
      setKnowledgeStatus(
        rows.length
          ? `${layerHint}检索完成：${rows.length} 个片段（FTS + 本地向量）`
          : '没有匹配内容。可换关键词，或先上传 .md/.pdf 资料',
      );
      setKnowledgeProgress(100);
    } catch (error) {
      setKnowledgeStatus(`检索失败：${String(error)}`);
      setKnowledgeProgress(0);
    }
  };

  const uploadKnowledgeFiles = async (files: FileList | File[]) => {
    const list = Array.from(files);
    if (!list.length) return;
    setKnowledgeStatus(`正在上传 ${list.length} 个文件…`);
    setKnowledgeProgress(15);
    try {
      for (let index = 0; index < list.length; index += 1) {
        const file = list[index];
        const body = new FormData();
        body.append('file', file);
        await fetchJson(
          `${props.apiBase}/knowledge/${props.sessionId}/attachments`,
          { method: 'POST', body },
        );
        setKnowledgeProgress(Math.round(((index + 1) / list.length) * 90));
      }
      await loadKnowledge();
      setKnowledgeStatus(`已索引 ${list.length} 个文件，可开始检索`);
      setKnowledgeProgress(100);
    } catch (error) {
      setKnowledgeStatus(`上传失败：${String(error)}`);
      setKnowledgeProgress(0);
    }
  };

  const citeHitToComposer = (hit: KnowledgeSearchHit) => {
    props.onInsertComposer?.(formatCitationForComposer(hit));
    setKnowledgeStatus(`已将引用「${hit.citation}」填入输入框`);
  };

  const askWithTopHits = () => {
    if (!knowledgeQuery.trim() || !knowledgeResults.length) return;
    props.onInsertComposer?.(formatAskWithSources(knowledgeQuery.trim(), knowledgeResults, 3));
    setKnowledgeStatus('已将问题 + Top 来源填入输入框');
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
    const model = modelConfigs.find((item) => item.name === name);
    if (model?.type === 'local') {
      const result = await startAndVerifyLocalModel(
        props.apiBase,
        model,
        fetchJson,
        (phase) => setModelVerifyStatus(phase === 'start' ? '正在启动本地服务…' : '正在验证真实对话…'),
      );
      setModelVerifyStatus(result.ok ? `验证通过：${result.message}` : `验证失败：${result.message}`);
      return result.ok;
    }
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
          <div className="grid min-w-0 flex-1 grid-cols-7 gap-1 rounded-lg bg-[#e4dbcf] p-1">
            {([
              ['记忆', t('memory')], ['资料', t('knowledge')], ['任务', t('tasks')], ['自动化', t('automations')], ['工件', t('artifacts')],
              ['合并', t('changes')], ['工具', t('tools')], ['插件', t('plugins')], ['连接', t('connections')],
              ['电脑', t('computer')], ['模型', t('models')], ['运行', t('runs')], ['设置', t('settings')], ['规范', t('instructions')],
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
              <p className="mt-1 text-xs leading-5 text-[#7a7065]">决定和偏好只从用户明确表达中提取；工具可写入结构化共享实体，供同一会话中的所有 Agent 直接引用。所有内容均可检查或删除。</p>
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
              <div className="mb-2 flex items-center justify-between">
                <span className="text-xs font-semibold">Agent 共享实体</span>
                <span className="text-[10px] text-[#897b6d]">{sessionMemory?.entities?.length ?? 0} 项 · 自动进入模型上下文</span>
              </div>
              <div className="space-y-2">
                {(sessionMemory?.entities || []).map((entity) => (
                  <div key={entity.key} className="rounded-lg border border-[#d8cdbf] bg-[#f8f4ee] p-3 text-xs">
                    <div className="flex items-start justify-between gap-3">
                      <div className="min-w-0">
                        <div className="break-all font-mono font-semibold text-[#5d4937]">{entity.key}</div>
                        <div className="mt-1 break-all leading-5 text-[#5b5147]">{entity.value}</div>
                        <div className="mt-1 text-[10px] text-[#897b6d]">{entity.kind} · {entity.source}</div>
                        {typeof entity.metadata?.artifact_path === 'string' && (
                          <button
                            onClick={() => {
                              props.onSelectArtifact(String(entity.metadata?.artifact_path));
                              setActivePanel('工件');
                            }}
                            className="mt-1 break-all text-left font-mono text-[10px] text-[#356a82] underline"
                          >
                            元数据：{entity.metadata.artifact_path}
                          </button>
                        )}
                      </div>
                      <button onClick={() => void removeMemoryEntity(entity.key)} className="shrink-0 rounded border border-[#cdbfb0] p-1 text-[#744438]" aria-label={`删除共享实体 ${entity.key}`}>
                        <Trash2 size={12} />
                      </button>
                    </div>
                  </div>
                ))}
                {!sessionMemory?.entities?.length && (
                  <div className="rounded-lg border border-dashed border-[#d8cdbf] p-3 text-center text-[11px] text-[#897b6d]">生成图片或由 Agent 记录关键实体后会显示在这里。</div>
                )}
              </div>
            </div>
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
            <p className="mt-1 text-xs text-[#7a7065]">
              本地 FTS5/BM25 + 哈希向量混合检索（不上传云端）。检索结果展示各层贡献，并可一键引用到输入框。
            </p>
            <div className="mt-2 text-[11px] text-[#6d6258]">{formatKnowledgeStatsLine(knowledgeStats)}</div>
            <div className="mt-3 h-1.5 overflow-hidden rounded-full bg-[#dfd5c9]" aria-label={`资料处理进度 ${knowledgeProgress}%`}>
              <div className="h-full bg-[#8d5f35] transition-all duration-300" style={{ width: `${knowledgeProgress}%` }} />
            </div>
          </div>

          <label className="mb-4 flex cursor-pointer flex-col items-center justify-center rounded-xl border border-dashed border-[#c9b8a3] bg-[#fffdf9] px-4 py-5 text-center hover:bg-[#fbf6ef]">
            <input
              type="file"
              multiple
              className="hidden"
              accept=".md,.txt,.pdf,.docx,.xlsx,.pptx,.csv,.json,.png,.jpg,.jpeg,.webp,.py,.ts,.tsx,.js"
              onChange={(event) => {
                if (event.target.files?.length) void uploadKnowledgeFiles(event.target.files);
                event.currentTarget.value = '';
              }}
            />
            <div className="text-sm font-medium text-[#5d4a3a]">点击上传项目资料</div>
            <div className="mt-1 text-[11px] text-[#8a7f75]">支持 Markdown / PDF / Office / 代码 / 图片 · 单文件 ≤ 25MB</div>
          </label>

          <div className="mb-2 flex flex-wrap gap-1">
            {['', '.md', '.pdf', '.docx', '.txt', '.py'].map((ext) => (
              <button
                key={ext || 'all'}
                type="button"
                onClick={() => setKnowledgeExtFilter(ext)}
                className={`rounded-full px-2 py-0.5 text-[11px] ${knowledgeExtFilter === ext ? 'bg-[#2f2a24] text-white' : 'bg-[#eee4d8] text-[#6b4425]'}`}
              >
                {ext || '全部'}
              </button>
            ))}
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
              <div className="flex items-center justify-between">
                <div className="text-[11px] font-semibold text-[#6d6258]">检索层 · 引用</div>
                {props.onInsertComposer && (
                  <button
                    type="button"
                    onClick={askWithTopHits}
                    className="rounded border border-[#cfc4b7] px-2 py-0.5 text-[11px] text-[#5d4a3a]"
                  >
                    用 Top 来源提问
                  </button>
                )}
              </div>
              {knowledgeResults.map((result, index) => (
                <article key={`${result.attachment_id}-${result.locator}-${index}`} className="rounded-lg border border-[#dfd4c7] bg-white p-3">
                  <div className="flex items-center gap-2 text-xs font-semibold text-[#6b4425]">
                    <span className="shrink-0 rounded bg-[#f0e9df] px-1.5 py-0.5 text-[10px]">#{result.rank ?? index + 1}</span>
                    <button className="truncate text-left hover:underline" onClick={() => {
                      const attachment = knowledgeAttachments.find((item) => item.id === result.attachment_id);
                      if (attachment) void previewKnowledge(attachment);
                    }}>{result.citation}</button>
                    <span className="ml-auto shrink-0 rounded bg-[#eee4d8] px-1.5 py-0.5 text-[10px]">{Math.round((result.score ?? 0) * 100)}%</span>
                  </div>
                  <div className="mt-1 text-[10px] text-[#8a7764]">
                    {formatLayerLabel(result.match_types, result.layers)}
                  </div>
                  <p className="mt-2 line-clamp-4 whitespace-pre-wrap text-xs leading-5 text-[#5f554b]">{result.content}</p>
                  {props.onInsertComposer && (
                    <button
                      type="button"
                      onClick={() => citeHitToComposer(result)}
                      className="mt-2 rounded border border-[#d5c6b6] px-2 py-1 text-[11px] text-[#654927] hover:bg-[#eee5da]"
                    >
                      引用到输入框
                    </button>
                  )}
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
                  <div className="mt-0.5 text-xs text-[#84796d]">{attachment.chunk_count} 个片段 · {(attachment.size_bytes / 1024).toFixed(1)} KB · {attachment.extension || ''}</div>
                </div>
                <button aria-label={`预览 ${attachment.name}`} onClick={() => void previewKnowledge(attachment)} className="rounded border border-[#d5c6b6] px-2 py-1 text-xs text-[#654927] hover:bg-[#eee5da]">预览</button>
                <button aria-label={`删除 ${attachment.name}`} onClick={() => void deleteKnowledge(attachment)} className="rounded p-1.5 text-[#8c4a3d] hover:bg-[#f3e3df]">
                  <Trash2 size={15} />
                </button>
              </div>
            ))}
            {knowledgeAttachments.length === 0 && (
              <div className="rounded-lg border border-dashed border-[#d6cbbd] p-6 text-center text-sm text-[#7a7065]">
                <div>尚未上传项目资料</div>
                <div className="mt-2 text-xs leading-5">
                  1. 上方上传 README / 设计文档<br />
                  2. 检索关键词查看「全文 / 向量」分层<br />
                  3. 点「引用到输入框」带证据提问
                </div>
              </div>
            )}
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
                props.onCreateTask(taskTitle.trim(), taskDependencies);
                setTaskTitle('');
                setTaskDependencies([]);
              }}
              className="flex h-10 items-center gap-1 rounded-lg bg-[#2f2a24] px-3 text-sm font-medium text-white"
            >
              <Plus size={15} />
              {ui('添加')}
            </button>
          </div>
          {!!tasks.length && (
            <label className="mb-4 block text-xs text-[#6d6258]">
              <span className="mb-1 block font-semibold">前置依赖（可多选）</span>
              <select
                multiple
                value={taskDependencies}
                onChange={(event) => setTaskDependencies(
                  Array.from(event.currentTarget.selectedOptions, (option) => option.value),
                )}
                className="min-h-20 w-full rounded-lg border border-[#ded6ca] bg-white p-2 text-xs outline-none"
              >
                {tasks.map((task) => (
                  <option key={task.id} value={task.id}>
                    [{taskStatusLabel[task.status]}] {task.title}
                  </option>
                ))}
              </select>
              <span className="mt-1 block text-[10px] text-[#897b6d]">依赖未完成时，任务不能被认领或推进；Agent 工具也会拒绝执行。</span>
            </label>
          )}
          <TaskBoard onPatchTask={props.onPatchTask} />
        </div>
      )}

      {activePanel === '自动化' && (
        <div className="aether-tab-panel aether-panel-scroll min-h-0 flex-1 overflow-auto">
          <AutomationPanel
            apiBase={props.apiBase}
            sessionId={props.sessionId}
            models={modelConfigs.map((model) => model.name)}
            request={fetchJson}
            onOpenRuns={() => setActivePanel('运行')}
          />
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
            <LocalModelQuickPath
              apiBase={props.apiBase}
              models={modelConfigs}
              onNotice={setModelVerifyStatus}
            />
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
          sessionId={props.sessionId}
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
            ['reasoningDisplay', '思考过程摘要', ['显示', '隐藏']],
          ] as Array<[keyof WorkspaceSettings, string, string[]]>).map(([key, label, options]) => (
            <label key={String(key)} className="mb-4 block">
              <span className="mb-1 block text-xs font-semibold text-[#7a7065]">{ui(String(label))}</span>
              <select
                value={workspaceSettings[key] ?? (key === 'reasoningDisplay' ? '显示' : '')}
                onChange={(event) => props.onUpdateSettings({ [key]: event.target.value } as Partial<WorkspaceSettings>)}
                className="h-10 w-full rounded-lg border border-[#ded6ca] bg-white px-3 text-sm outline-none"
              >
                {(options as string[]).map((option) => <option key={option} value={option}>{option === 'zh-CN' ? ui('简体中文') : option === 'en-US' ? 'English' : ui(option)}</option>)}
              </select>
            </label>
          ))}
          <section className="mb-5 rounded-xl border border-[#ded6ca] bg-[#fbfaf7] p-3">
            <div className="flex items-start gap-2">
              <Archive size={16} className="mt-0.5 shrink-0 text-[#6b523c]" />
              <div>
                <div className="text-xs font-semibold text-[#574a3e]">{ui('脱敏诊断包')}</div>
                <p className="mt-1 text-xs leading-5 text-[#7a7065]">
                  {ui('导出 schema、运行指标、哈希 trace 引用与二次脱敏日志；不包含提示正文、文件内容、查询串、凭据、模型/provider 名称、原始 Run ID 或工具参数。')}
                </p>
              </div>
            </div>
            <button
              type="button"
              onClick={() => void downloadDiagnosticBundle()}
              className="mt-3 rounded-md bg-[#2f2a24] px-3 py-1.5 text-xs font-medium text-white"
            >
              {ui('导出脱敏诊断包')}
            </button>
            {diagnosticStatus && (
              <div role="status" className="mt-2 text-xs text-[#6d6258]">{diagnosticStatus}</div>
            )}
          </section>
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

      {activePanel === '规范' && (
        <SkillsRulesPanel apiBase={props.apiBase} sessionId={props.sessionId} />
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

type ComfyUIImageModel = {
  name: string;
  kind: 'checkpoint' | 'diffusion_model';
  recommended: boolean;
};

type ComfyUIVideoPipeline = {
  id: 'wan-i2v' | 'ltx-t2v';
  label: string;
  installed: boolean;
  available: boolean;
  missing: string[];
  requires_input_image: boolean;
  warning: string;
  minimum_free_memory_bytes?: number;
  current_free_memory_bytes?: number;
};

type ComfyUIOverview = {
  ready: boolean;
  runtime: {
    runtime?: string;
    autostart_enabled?: boolean;
    diagnostics?: ComfyUIOverview['diagnostics'];
  };
  diagnostics: {
    reason?: string;
    message?: string;
    unit_active?: boolean;
    docker?: {
      container?: string;
      status?: string;
      oom_killed?: boolean;
      memory_limit_bytes?: number | null;
      comfyui_process_running?: boolean;
    } | null;
  };
  selection: {
    image_model?: string | null;
    video_pipeline?: 'wan-i2v' | 'ltx-t2v' | null;
  };
  catalog: {
    image_models: ComfyUIImageModel[];
    video_pipelines: ComfyUIVideoPipeline[];
    assets: Record<string, Array<string | { name?: string }>>;
    system: { ram_free?: number; ram_total?: number };
  } | null;
};

const formatGiB = (value?: number | null) => (
  value ? `${(value / 1024 ** 3).toFixed(1)} GiB` : '未知'
);

function PluginCenter({ apiBase, sessionId }: { apiBase: string; sessionId: string }) {
  const { ui } = useI18n();
  const [plugins, setPlugins] = useState<PluginItem[]>([]);
  const [loading, setLoading] = useState(true);
  const [busyKey, setBusyKey] = useState('');
  const [inputs, setInputs] = useState<Record<string, string>>({});
  const [lastResult, setLastResult] = useState<PluginInvokeResult | null>(null);
  const [status, setStatus] = useState('');
  const [manifestText, setManifestText] = useState('');
  const [comfyOverview, setComfyOverview] = useState<ComfyUIOverview | null>(null);
  const [comfyBusy, setComfyBusy] = useState<'status' | 'start' | 'save' | ''>('');
  const [comfyStatus, setComfyStatus] = useState('');
  const [comfyImageModel, setComfyImageModel] = useState('');
  const [comfyVideoPipeline, setComfyVideoPipeline] = useState<'wan-i2v' | 'ltx-t2v'>('wan-i2v');

  const applyComfyOverview = (overview: ComfyUIOverview) => {
    setComfyOverview(overview);
    const recommendedImage = overview.catalog?.image_models.find((item) => item.recommended)
      || overview.catalog?.image_models[0];
    const selectedImage = overview.catalog?.image_models.find(
      (item) => item.name === overview.selection.image_model,
    );
    setComfyImageModel(selectedImage?.name || recommendedImage?.name || '');
    const installedPipeline = overview.catalog?.video_pipelines.find((item) => item.installed);
    const selectedPipeline = overview.catalog?.video_pipelines.find(
      (item) => item.installed && item.id === overview.selection.video_pipeline,
    );
    setComfyVideoPipeline(
      selectedPipeline?.id || installedPipeline?.id || 'wan-i2v',
    );
  };

  const refreshComfyUI = async (start: boolean) => {
    setComfyBusy(start ? 'start' : 'status');
    try {
      const overview = await fetchJson<ComfyUIOverview>(
        `${apiBase}/plugins/comfyui/runtime/${start ? 'start' : 'status'}`,
        start ? { method: 'POST' } : undefined,
      );
      applyComfyOverview(overview);
      setComfyStatus(start ? 'ComfyUI 已启动并完成模型扫描' : '');
    } catch (error) {
      setComfyStatus(`${start ? '启动' : '状态检查'}失败：${String(error)}`);
      try {
        const overview = await fetchJson<ComfyUIOverview>(`${apiBase}/plugins/comfyui/runtime/status`);
        applyComfyOverview(overview);
      } catch {
        // Keep the actionable start error when even the diagnostic endpoint is unavailable.
      }
    } finally {
      setComfyBusy('');
    }
  };

  const saveComfySelection = async () => {
    setComfyBusy('save');
    try {
      const overview = await fetchJson<ComfyUIOverview>(`${apiBase}/plugins/comfyui/selection`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          image_model: comfyImageModel || null,
          video_pipeline: comfyVideoPipeline || null,
        }),
      });
      applyComfyOverview(overview);
      setComfyStatus('模型选择已保存；后续模型调用将优先使用该配置');
    } catch (error) {
      setComfyStatus(`保存模型选择失败：${String(error)}`);
    } finally {
      setComfyBusy('');
    }
  };

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
    void refreshComfyUI(false);
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
      const payload = result.result as {
        kind?: string;
        url?: string;
        browser?: 'default' | 'firefox';
      } | null;
      if (payload?.kind === 'open_external' && payload.url) {
        if (!window.aetherRuntime?.openExternal) throw new Error('当前不是可打开外部链接的桌面运行环境');
        await window.aetherRuntime.openExternal(payload.url, {
          browser: payload.browser || 'default',
        });
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

      <section className="mb-4 rounded-xl border border-[#cdbda9] bg-[#fffaf3] p-4" aria-label="ComfyUI 模型选择">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2 text-sm font-semibold">
              <Images size={16} />ComfyUI 运行与模型选择
              <span className={`rounded px-2 py-0.5 text-[10px] ${comfyOverview?.ready ? 'bg-[#dcebd7] text-[#326022]' : 'bg-[#ead5d1] text-[#8a3b31]'}`}>
                {comfyOverview?.ready ? '已就绪' : '未就绪'}
              </span>
            </div>
            <div className="mt-1 text-xs leading-5 text-[#7a7065]">
              启动后读取 ComfyUI 实际安装的模型；保存的选择优先于模型自动选择。
            </div>
          </div>
          <div className="flex gap-2">
            <button onClick={() => refreshComfyUI(false)} disabled={Boolean(comfyBusy)} className="rounded-md border border-[#d6cbbd] px-3 py-1.5 text-xs disabled:opacity-40">
              {comfyBusy === 'status' ? '检查中' : '检查状态'}
            </button>
            <button onClick={() => refreshComfyUI(true)} disabled={Boolean(comfyBusy)} className="rounded-md bg-[#2f2a24] px-3 py-1.5 text-xs text-white disabled:opacity-40">
              {comfyBusy === 'start' ? '启动与扫描中' : '启动并扫描模型'}
            </button>
          </div>
        </div>

        {(comfyStatus || comfyOverview?.diagnostics?.message) && (
          <div role="status" className="mt-3 rounded-md border border-[#dfd2c2] bg-white px-3 py-2 text-xs leading-5 text-[#6d6258]">
            {comfyStatus || comfyOverview?.diagnostics?.message}
          </div>
        )}
        {comfyOverview?.diagnostics?.docker?.oom_killed && (
          <div className="mt-2 rounded-md border border-[#d7a98b] bg-[#fff0e5] px-3 py-2 text-xs leading-5 text-[#8a3b31]">
            检测到 ComfyUI 容器曾发生 OOM：视频大模型可能与本地语言模型争用统一内存。
            LTX 22B 只有在可用内存达到安全阈值时才允许执行。
          </div>
        )}

        {comfyOverview?.catalog && (
          <div className="mt-4 grid gap-4 lg:grid-cols-2">
            <label className="text-xs font-semibold text-[#62584e]">
              图像生成模型
              <select aria-label="ComfyUI 图像生成模型" value={comfyImageModel} onChange={(event) => setComfyImageModel(event.target.value)} className="mt-2 h-9 w-full rounded-md border border-[#d6cbbd] bg-white px-2 text-xs font-normal">
                {comfyOverview.catalog.image_models.map((model) => (
                  <option key={model.name} value={model.name}>
                    {model.name}{model.recommended ? '（推荐）' : ''}
                  </option>
                ))}
              </select>
              <span className="mt-1 block font-normal text-[#8b8176]">Checkpoint 与 Qwen-Image 均来自当前 ComfyUI 实际清单。</span>
            </label>

            <label className="text-xs font-semibold text-[#62584e]">
              视频生成管线
              <select aria-label="ComfyUI 视频生成管线" value={comfyVideoPipeline} onChange={(event) => setComfyVideoPipeline(event.target.value as 'wan-i2v' | 'ltx-t2v')} className="mt-2 h-9 w-full rounded-md border border-[#d6cbbd] bg-white px-2 text-xs font-normal">
                {comfyOverview.catalog.video_pipelines.filter((pipeline) => pipeline.installed).map((pipeline) => (
                  <option key={pipeline.id} value={pipeline.id}>
                    {pipeline.label}{pipeline.available ? '' : '（当前资源不足）'}
                  </option>
                ))}
              </select>
              <span className="mt-1 block font-normal text-[#8b8176]">
                当前系统可用内存：{formatGiB(comfyOverview.catalog.system.ram_free)}
              </span>
            </label>

            <div className="space-y-2 lg:col-span-2">
              {comfyOverview.catalog.video_pipelines.map((pipeline) => (
                <div key={pipeline.id} data-comfy-pipeline={pipeline.id} className={`rounded-md border px-3 py-2 text-xs leading-5 ${pipeline.available ? 'border-[#c6d8c3] bg-[#f2f8ef] text-[#4d6848]' : 'border-[#dfb69d] bg-[#fff2e8] text-[#8a4a32]'}`}>
                  <span className="font-semibold">{pipeline.label}</span>
                  <span className="ml-2">{pipeline.installed ? pipeline.available ? '可运行' : '已安装但资源不足' : '缺少依赖'}</span>
                  <div>{pipeline.warning}</div>
                  {pipeline.missing.length > 0 && <div>缺少：{pipeline.missing.join('、')}</div>}
                  {pipeline.minimum_free_memory_bytes && <div>安全阈值：{formatGiB(pipeline.minimum_free_memory_bytes)}；当前：{formatGiB(pipeline.current_free_memory_bytes)}</div>}
                </div>
              ))}
            </div>

            <div className="flex flex-wrap items-center justify-between gap-3 lg:col-span-2">
              <button onClick={saveComfySelection} disabled={Boolean(comfyBusy) || !comfyImageModel} className="rounded-md bg-[#8d5f35] px-3 py-1.5 text-xs text-white disabled:opacity-40">
                {comfyBusy === 'save' ? '保存中' : '保存模型选择'}
              </button>
              <span className="text-[11px] text-[#8b8176]">
                已发现 {comfyOverview.catalog.image_models.length} 个可用图像模型、{comfyOverview.catalog.video_pipelines.filter((item) => item.installed).length} 条已安装视频管线
              </span>
            </div>

            <details className="rounded-md border border-[#ded6ca] bg-white p-3 text-xs lg:col-span-2">
              <summary className="cursor-pointer font-semibold">查看全部已安装模型资源</summary>
              <div className="mt-3 grid gap-3 sm:grid-cols-2">
                {Object.entries(comfyOverview.catalog.assets).map(([group, values]) => (
                  <div key={group}>
                    <div className="font-semibold text-[#62584e]">{group} · {values.length}</div>
                    <div className="mt-1 max-h-28 overflow-auto whitespace-pre-line break-all text-[11px] leading-5 text-[#7a7065]">
                      {values.map((value) => typeof value === 'string' ? value : value.name || 'unnamed').join('\n') || '无'}
                    </div>
                  </div>
                ))}
              </div>
            </details>
          </div>
        )}
      </section>

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
