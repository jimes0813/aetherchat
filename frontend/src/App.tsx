import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Composer } from './components/Composer';
import { ModelWorkingStrip, type AgentActivityItem } from './components/ModelWorkingStrip';
import { UnifiedFeed } from './components/UnifiedFeed';
import { ErrorBoundary } from './components/ErrorBoundary';
import { useDialogFocus } from './components/useDialogFocus';
import { InspectorPanel } from './components/InspectorPanel';
import {
  FirstRunWizard,
  shouldShowFirstRunWizard,
} from './components/FirstRunWizard';
import {
  ActivityRail,
  CommandHeader,
  CommandPalette,
  ConversationHistory,
  DesktopStatusBar,
  DesktopTitleBar,
  MainViewTabs,
  TaskHistory,
  WorkspaceSidebar,
  type CommandItem,
} from './components/chrome';
import { API_BASE, formatDateTime, isConfiguredModel } from './components/shared';
import type { DesktopDocumentSaveResult, DesktopMouseResult, DesktopScreenshotSaveResult, ExternalNavigationReceipt, SelectedDesktopDocument } from './components/shared';
import { fetchJson, setApiAuthToken, withApiAuth } from './api/client';
import { useI18n } from './i18n';
import { useSessionStore } from './stores/sessionStore';
import { useSessionEvents } from './hooks/useSessionEvents';
import { useSessionLoaders, type StoredSession } from './hooks/useSessionLoaders';
import { useAgentActions } from './hooks/useAgentActions';
import { useEntityActions } from './hooks/useEntityActions';
import type { AgentRun, AgentWorkspaceCandidate, ApprovalRequest, Conversation, GeneratedFile, GeneratedFileView, GitRepository, KnowledgeAttachment, MergeProposal, Message, ModelConfig, Task, TaskEvent, ToolItem, ToolRun, WorkspaceSettings } from './types';

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

type LaunchIntent = {
  id: string;
  action: 'compose';
  prompt: string;
  mode: 'casual' | 'roundtable' | 'project' | null;
  newConversation: boolean;
  source: 'protocol' | 'shortcut';
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
      backendErrorCode?: string;
      backendRequestId?: string;
      appVersion?: string;
      platform?: string;
      quickEntryShortcut?: string;
      getApiToken?: () => Promise<string>;
      openExternal?: (
        url: string,
        options?: { browser?: 'default' | 'firefox' },
      ) => Promise<ExternalNavigationReceipt>;
      openArtifact?: (
        sessionId: string,
        path: string,
        action?: 'open' | 'reveal',
      ) => Promise<{ opened: boolean; action: 'open' | 'reveal'; displayName: string }>;
      onLaunchIntent?: (callback: (intent: LaunchIntent) => void) => () => void;
      onBackgroundChange?: (
        callback: (payload: { reviewItems: number }) => void,
      ) => () => void;
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
    addReasoningStep,
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
    记忆: t('memory'), 资料: t('knowledge'), 任务: t('tasks'), 自动化: t('automations'), 工件: t('artifacts'), 合并: t('changes'),
    工具: t('tools'), 插件: t('plugins'), 连接: t('connections'), 电脑: t('computer'), 模型: t('models'),
    运行: t('runs'), 设置: t('settings'), 规范: t('instructions'),
  } as Record<string, string>)[activePanel] : ui('检查器');
  const approvalActivity = useRef<Record<string, { model: string; toolName: string }>>({});
  const [authToken, setAuthToken] = useState('');
  const [authReady, setAuthReady] = useState(false);
  const [tokenStatus, setTokenStatus] = useState('正在授权');
  const [notice, setNotice] = useState('');
  const [firstRunOpen, setFirstRunOpen] = useState(false);
  /** Avoid flashing the first-run wizard on the empty pre-fetch model list. */
  const [modelsHydrated, setModelsHydrated] = useState(false);
  const [commandOpen, setCommandOpen] = useState(false);
  const [mainView, setMainView] = useState<'chat' | 'history' | 'tasks'>('chat');
  const [taskEvents, setTaskEvents] = useState<TaskEvent[]>([]);
  const [approvals, setApprovals] = useState<ApprovalRequest[]>([]);
  const [agentRuns, setAgentRuns] = useState<AgentRun[]>([]);
  const [agentActivity, setAgentActivity] = useState<Record<string, AgentActivityItem>>({});
  const [runQueue, setRunQueue] = useState<{
    dispatchId: string;
    position: number | null;
  } | null>(null);
  const [gitRepository, setGitRepository] = useState<GitRepository | null>(null);
  const [verificationCommands, setVerificationCommands] = useState('');
  const [pendingLaunchIntent, setPendingLaunchIntent] = useState<LaunchIntent | null>(null);
  const [composerDraft, setComposerDraft] = useState<{ id: string; text: string; clear?: boolean } | null>(null);
  const inspectorDialog = useDialogFocus<HTMLDivElement>(Boolean(activePanel), () => setActivePanel(null));

  useEffect(() => {
    document.documentElement.lang = locale;
    document.title = locale === 'en-US' ? 'AetherChat Desktop' : 'AetherChat 中文协作工作台';
  }, [locale]);

  useEffect(() => window.aetherRuntime?.onLaunchIntent?.((intent) => {
    if (intent?.action === 'compose' && typeof intent.id === 'string') {
      setPendingLaunchIntent(intent);
    }
  }), []);

  useEffect(() => {
    setAgentActivity({});
    setRunQueue(null);
    approvalActivity.current = {};
  }, [sessionId]);

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
        setApiAuthToken(data.access_token);
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

  const currentMode = SETTING_TO_MODE[workspaceSettings.mode] ?? 'project';

  const {
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
  } = useSessionLoaders({
    sessionId,
    currentMode,
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
    onModelsHydrated: () => setModelsHydrated(true),
  });

  useEffect(() => {
    if (!authReady || !authToken) return;
    void loadConversations();
  }, [authReady, authToken]);

  useEffect(() => {
    if (!authReady || !authToken || !modelsHydrated) return;
    // Auto-open only after models hydrate; dismiss/finish own close so mid-wizard
    // save+verify is not torn down when the new model appears in modelConfigs.
    if (shouldShowFirstRunWizard(modelConfigs)) {
      setFirstRunOpen(true);
    }
  }, [authReady, authToken, modelConfigs, modelsHydrated]);

  useEffect(() => {
    if (!authReady || !authToken) return undefined;
    return window.aetherRuntime?.onBackgroundChange?.(() => {
      fetchJson<Message[]>(
        `${API_BASE}/sessions/${sessionId}/messages?limit=5000`,
      ).then(setMessages).catch(() => undefined);
      fetchJson<AgentRun[]>(
        `${API_BASE}/agent-runs?session_id=${encodeURIComponent(sessionId)}`,
      ).then(setAgentRuns).catch(() => undefined);
      void loadConversations();
    });
  }, [authReady, authToken, sessionId, setMessages, setConversations]);

  const createPersistentConversation = async (
    modeOverride?: 'casual' | 'roundtable' | 'project' | null,
  ) => {
    try {
      const requestedMode = modeOverride || currentMode;
      const session = await fetchJson<StoredSession>(`${API_BASE}/sessions/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ title: '新协作会话', mode: requestedMode }),
      });
      selectConversation(session.id);
      setMainView('chat');
      await loadConversations();
      return session.id;
    } catch (error) {
      setNotice(`新建会话失败：${String(error)}`);
      return null;
    }
  };

  useEffect(() => {
    if (!pendingLaunchIntent || !authReady || !authToken) return undefined;
    let cancelled = false;
    const applyLaunchIntent = async () => {
      const requestedMode = pendingLaunchIntent.mode;
      if (requestedMode) {
        updateSettings({ mode: MODE_TO_SETTING[requestedMode] });
      }
      if (pendingLaunchIntent.newConversation) {
        await createPersistentConversation(requestedMode);
      }
      if (cancelled) return;
      setMainView('chat');
      setActivePanel(null);
      setComposerDraft({
        id: pendingLaunchIntent.id,
        text: pendingLaunchIntent.prompt,
        clear: pendingLaunchIntent.newConversation,
      });
      setPendingLaunchIntent(null);
      setNotice(pendingLaunchIntent.source === 'shortcut'
        ? '快速输入已就绪'
        : '已从 AetherChat 链接载入输入内容');
    };
    void applyLaunchIntent();
    return () => { cancelled = true; };
  }, [pendingLaunchIntent, authReady, authToken]);
  const fetchToken = async () => {
    try {
      const desktopToken = await window.aetherRuntime?.getApiToken?.();
      const data = desktopToken
        ? { access_token: desktopToken }
        : await fetchJson<{ access_token?: string }>(`${API_BASE}/auth/token`, { method: 'POST' });
      if (!data.access_token) throw new Error('后端未返回访问令牌');
      setApiAuthToken(data.access_token);
      setAuthToken(data.access_token);
      setTokenStatus('授权已刷新');
      setNotice('本地会话授权已刷新，WebSocket 正在重连');
    } catch (error) {
      setTokenStatus('授权失败');
      setNotice(`授权刷新失败：${String(error)}`);
    }
  };

  const { wsClient } = useSessionEvents({
    sessionId,
    authToken,
    authReady,
    t,
    approvalActivity,
    setNotice,
    setAgentActivity,
    setApprovals,
    setAgentRuns,
    setRunQueue,
    setConnection,
    addMessage,
    addDelta,
    addReasoningStep,
    resetStream,
    finishStream,
    updateTask,
    upsertTool,
    addToolRun,
    setBudget,
    setActivePanel,
    loadTasks,
    loadMessages,
    loadTaskEvents,
    loadAgentRuns,
    loadGitRepositories,
    loadConversations,
    loadArtifacts,
    loadMerges,
    loadTools,
    loadModels,
  });

  const {
    selectGitRepository,
    decideWorkspaceCandidate,
    retryAgentRun,
    interruptAgentStep,
    updateAgentPlan,
    confirmAgentPlan,
    rejectAgentPlan,
  } = useAgentActions({
    sessionId,
    wsClient,
    setNotice,
    setGitRepository,
    setVerificationCommands,
    setAgentRuns,
  });

  const {
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
  } = useEntityActions({
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
  });

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
    const panels = (['记忆', '资料', '任务', '自动化', '工件', '合并', '工具', '插件', '连接', '电脑', '模型', '运行', '设置', '规范'] as const).map((panel) => ({
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

  const inspectGeneratedFile = (file: GeneratedFile) => {
    if (!file.path) return;
    setSelectedArtifactPath(file.path);
    setActivePanel('工件');
  };

  const resolveGeneratedFile = useCallback(async (
    file: GeneratedFile,
    includePreview = false,
  ): Promise<GeneratedFileView> => {
    if (!file.path) {
      return {
        location: file.url || null,
        preview_url: includePreview ? file.url || null : null,
        mime_type: file.mime_type,
      };
    }
    const encodedPath = file.path.split('/').map(encodeURIComponent).join('/');
    const location = await fetchJson<{
      path: string;
      mime_type?: string;
    }>(
      `${API_BASE}/artifacts/${encodedPath}/native-location?session_id=${encodeURIComponent(sessionId)}`,
    );
    const mimeType = location.mime_type || file.mime_type || 'application/octet-stream';
    const safeInlineMedia = (
      /^(image\/(?:png|jpeg|webp|gif)|video\/|audio\/)/i.test(mimeType)
    );
    if (!includePreview || !safeInlineMedia) {
      return { location: location.path, mime_type: mimeType };
    }
    const response = await fetch(
      `${API_BASE}/artifacts/${encodedPath}/content?session_id=${encodeURIComponent(sessionId)}`,
      withApiAuth(),
    );
    if (!response.ok) {
      throw new Error(`文件预览加载失败：HTTP ${response.status}`);
    }
    const blob = await response.blob();
    return {
      location: location.path,
      preview_url: URL.createObjectURL(blob),
      mime_type: mimeType,
    };
  }, [sessionId]);

  const openGeneratedFile = async (
    file: GeneratedFile,
    action: 'open' | 'reveal',
  ) => {
    try {
      if (file.path && window.aetherRuntime?.openArtifact) {
        const opened = await window.aetherRuntime.openArtifact(
          sessionId,
          file.path,
          action,
        );
        setNotice(action === 'reveal'
          ? `已在文件夹中显示 ${opened.displayName}`
          : `已打开 ${opened.displayName}`);
        return;
      }
      if (action === 'open' && file.url && window.aetherRuntime?.openExternal) {
        await window.aetherRuntime.openExternal(file.url);
        setNotice(`已打开 ${file.name}`);
        return;
      }
      if (file.path) {
        inspectGeneratedFile(file);
        setNotice('浏览器模式无法调用系统文件管理器，已在工件面板中打开');
        return;
      }
      throw new Error('该文件没有可打开的本机路径');
    } catch (error) {
      setNotice(`打开文件失败：${String(error)}`);
    }
  };

  const sessionApprovals = approvals.filter((item) => item.session_id === sessionId);
  const pendingApprovals = sessionApprovals.filter((item) => !item.decision);
  const canInterrupt = Object.keys(agentActivity).length > 0 || agentRuns.some((run) => (
    ['queued', 'running', 'waiting_approval'].includes(run.status)
  ));

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[#e9e1d6] text-[#2f2a24]">
      <a href="#aether-main-content" className="aether-skip-link">Skip to main content / 跳到主要内容</a>
      <DesktopTitleBar />
      {firstRunOpen && (
        <FirstRunWizard
          models={modelConfigs}
          onConfigured={(model) => {
            upsertModelConfig(model);
            setNotice(`已配置模型 ${model.name}`);
          }}
          onDismiss={() => setFirstRunOpen(false)}
        />
      )}

      <main id="aether-main-content" tabIndex={-1} className="aether-main-grid grid min-h-0 flex-1 grid-cols-[3.5rem_15rem_minmax(0,1fr)] border-t border-[#cfc4b7]">
        <ActivityRail activePanel={activePanel} setActivePanel={setActivePanel} />

        <WorkspaceSidebar
          conversations={conversations}
          currentSessionId={sessionId}
          onNewConversation={() => void createPersistentConversation()}
          onOpenSearch={() => setCommandOpen(true)}
          onSelectConversation={selectConversation}
          onDeleteConversation={(id) => void deleteConversation(id)}
        />

        <ErrorBoundary
          scope="主工作区"
          resetKey={`${sessionId}:${mainView}`}
          fallbackClassName="flex min-h-0 min-w-0 flex-col border-x border-[#d8cfc3] bg-[#f8ece9] p-5 text-[#67372f]"
        >
        <section className="flex min-h-0 min-w-0 flex-col border-x border-[#d8cfc3] bg-[#fbfaf7]">
          <CommandHeader
            connection={connection}
            providerCount={providers.length}
            activeProviderCount={modelConfigs.filter(isConfiguredModel).length}
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
          {mainView === 'chat' && (
            <UnifiedFeed
              approvals={sessionApprovals.filter((item) => Boolean(item.decision))}
              hasConfiguredModel={modelConfigs.some(isConfiguredModel)}
              hasLocalModel={modelConfigs.some((model) => model.type === 'local')}
              onInspectGeneratedFile={inspectGeneratedFile}
              onResolveGeneratedFile={resolveGeneratedFile}
              onOpenGeneratedFile={(file, action) => void openGeneratedFile(file, action)}
              onOpenRunHistory={() => setActivePanel('运行')}
              onOpenModels={() => setActivePanel('模型')}
              onPickExample={(prompt) => {
                setComposerDraft({
                  id: `example-${Date.now()}`,
                  text: prompt,
                  clear: true,
                });
                setMainView('chat');
              }}
            />
          )}
          {mainView === 'history' && (
            <ConversationHistory
              conversations={conversations}
              currentSessionId={sessionId}
              onContinue={(id) => {
                selectConversation(id);
                setMainView('chat');
              }}
              onDelete={(id) => void deleteConversation(id)}
            />
          )}
          {mainView === 'tasks' && <TaskHistory tasks={tasks} events={taskEvents} />}
          {mainView === 'chat' && <ModelWorkingStrip activity={agentActivity} />}
          {mainView === 'chat' && (
            <Composer
              onSend={sendMessage}
              onUpload={uploadAttachments}
              providers={modelConfigs.filter(isConfiguredModel).map((model) => model.name)}
              onInterrupt={canInterrupt
                ? (scope) => wsClient.current?.sendAction({ type: 'interrupt', scope })
                : undefined}
              draft={composerDraft}
              approval={pendingApprovals[0]}
              queuedApprovalCount={Math.max(0, pendingApprovals.length - 1)}
              onApprovalDecision={decideApproval}
            />
          )}
        </section>
        </ErrorBoundary>

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
            <ErrorBoundary
              scope={`${activePanelLabel}面板`}
              resetKey={`${sessionId}:${activePanel}`}
              fallbackClassName="h-full overflow-auto bg-[#f8ece9] p-5 text-[#67372f]"
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
              onInsertComposer={(text) => {
                setComposerDraft({ id: `knowledge-${Date.now()}`, text });
                setActivePanel(null);
                setMainView('chat');
              }}
            />
            </ErrorBoundary>
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
        runQueuePosition={runQueue?.position}
        onCancelQueuedRun={runQueue ? () => {
          const sent = wsClient.current?.sendAction({
            type: 'interrupt',
            scope: 'all',
            dispatch_id: runQueue.dispatchId,
          });
          setNotice(sent
            ? '正在取消排队运行'
            : '运行连接已断开，暂时无法取消排队运行');
        } : undefined}
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

export default App;
