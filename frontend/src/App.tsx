import { useEffect, useMemo, useRef, useState } from 'react';
import { Composer } from './components/Composer';
import { VoiceModeOverlay } from './components/VoiceModeOverlay';
import { SettingsPage } from './components/SettingsPage';
import { isSettingsTab, type SettingsTabId } from './components/settingsTabs';
import { ModelWorkingStrip, type AgentActivityItem } from './components/ModelWorkingStrip';
import { UnifiedFeed } from './components/UnifiedFeed';
import { ErrorBoundary } from './components/ErrorBoundary';

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
  MainViewTabs,
  TaskHistory,
  WorkspaceSidebar,
  type CommandItem,
} from './components/chrome';
import { API_BASE, isConfiguredModel } from './components/shared';
import {
  createGovernanceProject,
  loadDefaultGovernanceProjectId,
  loadGovernanceProjects,
  projectLabel,
  resolvePreferredProjectId,
  type GovernanceProject,
} from './components/governanceProject';
import { SessionProjectSelect } from './components/SessionProjectSelect';
import { fetchJson, setApiAuthToken } from './api/client';
import { MODE_TO_SETTING, SETTING_TO_MODE } from './app/collaborationModes';
import { useI18n } from './i18n';
import { useSessionStore } from './stores/sessionStore';
import { useSessionEvents } from './hooks/useSessionEvents';
import { useSessionLoaders, type StoredSession } from './hooks/useSessionLoaders';
import { useAgentActions } from './hooks/useAgentActions';
import { useEntityActions } from './hooks/useEntityActions';
import { useGeneratedFiles } from './hooks/useGeneratedFiles';
import { useVoiceSession } from './hooks/useVoiceSession';
import type { LaunchIntent } from './types/desktop';
import type { AgentRun, ApprovalRequest, GitRepository, Message, TaskEvent } from './types';
import './types/desktop';

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
  const approvalActivity = useRef<Record<string, { model: string; toolName: string }>>({});
  const [authToken, setAuthToken] = useState('');
  const [authReady, setAuthReady] = useState(false);
  const [tokenStatus, setTokenStatus] = useState('正在授权');
  const [notice, setNotice] = useState('');
  const [firstRunOpen, setFirstRunOpen] = useState(false);
  /** Avoid flashing the first-run wizard on the empty pre-fetch model list. */
  const [modelsHydrated, setModelsHydrated] = useState(false);
  const [commandOpen, setCommandOpen] = useState(false);
  const [mainView, setMainView] = useState<'chat' | 'history' | 'tasks' | 'settings'>('chat');

  useEffect(() => {
    if (activePanel) setMainView('settings');
  }, [activePanel]);
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
  const [governanceProjects, setGovernanceProjects] = useState<GovernanceProject[]>([]);
  const [defaultGovernanceProjectId, setDefaultGovernanceProjectId] = useState<string | null>(null);
  const preferredProjectId = resolvePreferredProjectId(
    workspaceSettings.preferredProjectId,
    governanceProjects,
    defaultGovernanceProjectId,
  );
  const activeConversation = useMemo(
    () => conversations.find((item) => item.id === sessionId) || null,
    [conversations, sessionId],
  );
  const activeProjectId = activeConversation?.projectId ?? preferredProjectId ?? null;

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
    onModelsHydrated: () => setModelsHydrated(true),
  });

  useEffect(() => {
    if (!authReady || !authToken) return;
    void loadConversations();
  }, [authReady, authToken]);

  useEffect(() => {
    if (!authReady || !authToken) return;
    let cancelled = false;
    void (async () => {
      const [projects, defaultId] = await Promise.all([
        loadGovernanceProjects(),
        loadDefaultGovernanceProjectId(),
      ]);
      if (cancelled) return;
      setGovernanceProjects(projects);
      setDefaultGovernanceProjectId(defaultId);
      if (!workspaceSettings.preferredProjectId && defaultId) {
        updateSettings({ preferredProjectId: defaultId });
      }
    })();
    return () => {
      cancelled = true;
    };
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
    projectId?: string | null,
  ) => {
    try {
      const requestedMode = modeOverride || currentMode;
      const body: Record<string, unknown> = {
        title: '新协作会话',
        mode: requestedMode,
      };
      const boundProjectId = projectId || preferredProjectId;
      if (boundProjectId) {
        body.project_id = boundProjectId;
      }
      const session = await fetchJson<StoredSession>(`${API_BASE}/sessions/`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(body),
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

  const bindSessionProject = async (projectId: string) => {
    try {
      const updated = await fetchJson<StoredSession>(
        `${API_BASE}/sessions/${encodeURIComponent(sessionId)}/project`,
        {
          method: 'PATCH',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ project_id: projectId }),
        },
      );
      setConversations(
        conversations.map((item) =>
          item.id === sessionId
            ? { ...item, projectId: updated.project_id ?? projectId }
            : item,
        ),
      );
      const label = projectLabel(projectId, governanceProjects) || projectId;
      setNotice(`会话已绑定治理项目：${label}`);
    } catch (error) {
      setNotice(`绑定治理项目失败：${String(error)}`);
    }
  };

  const createProjectFromSidebar = async (name: string) => {
    try {
      const project = await createGovernanceProject(name);
      setGovernanceProjects((current) => {
        if (current.some((item) => item.id === project.id)) return current;
        return [...current, project].sort((left, right) => left.name.localeCompare(right.name, 'zh-CN'));
      });
      updateSettings({ preferredProjectId: project.id });
      await createPersistentConversation(null, project.id);
      setNotice(`已创建项目：${project.name}`);
    } catch (error) {
      setNotice(`新建项目失败：${String(error)}`);
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

  const voice = useVoiceSession({
    onSend: sendMessage,
    onInterrupt: () => {
      wsClient.current?.sendAction({ type: 'interrupt', scope: 'all' });
    },
    hasApproval: approvals.some((item) => item.session_id === sessionId && !item.decision),
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
        if (voice.open) return;
        setCommandOpen(false);
        setActivePanel(null);
        setMainView((current) => (current === 'settings' ? 'chat' : current));
      }
    };
    window.addEventListener('keydown', handleShortcut);
    return () => window.removeEventListener('keydown', handleShortcut);
  }, [setActivePanel, voice.open]);

  const commandItems = useMemo<CommandItem[]>(() => {
    const panels = (['记忆', '资料', '任务', '自动化', '工件', '合并', '工具', '插件', '连接', '电脑', '模型', '运行', '设置', '语音', '规范', '治理'] as const).map((panel) => ({
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
    return [
      {
        id: 'voice-live',
        label: ui('开始语音对话'),
        hint: ui('本地 · Live'),
        run: () => { void voice.start(); },
      },
      ...panels,
      ...conversationItems,
      ...taskItems,
      ...artifactItems,
      ...modelItems,
    ];
  }, [artifacts, conversations, modelConfigs, selectConversation, setActivePanel, setSelectedArtifactPath, tasks, ui, voice]);

  const {
    inspectGeneratedFile,
    resolveGeneratedFile,
    openGeneratedFile,
  } = useGeneratedFiles(
    sessionId,
    setSelectedArtifactPath,
    setActivePanel,
    setNotice,
  );

  const sessionApprovals = approvals.filter((item) => item.session_id === sessionId);
  const pendingApprovals = sessionApprovals.filter((item) => !item.decision);
  const canInterrupt = Object.keys(agentActivity).length > 0 || agentRuns.some((run) => (
    ['queued', 'running', 'waiting_approval'].includes(run.status)
  ));

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-[#e9e1d6] text-[#2f2a24]">
      <a href="#aether-main-content" className="aether-skip-link">{ui('跳到主要内容')}</a>
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

      <main id="aether-main-content" tabIndex={-1} className="aether-main-grid grid min-h-0 flex-1">
        <ActivityRail
          activePanel={activePanel}
          setActivePanel={setActivePanel}
          settingsOpen={mainView === 'settings'}
          onOpenChat={() => setMainView('chat')}
          onOpenSettings={() => {
            setMainView('settings');
            if (!activePanel) setActivePanel('设置');
          }}
        />

        <WorkspaceSidebar
          conversations={conversations}
          currentSessionId={sessionId}
          projects={governanceProjects.map((item) => ({ id: item.id, name: item.name }))}
          activeProjectId={preferredProjectId ?? null}
          projectNames={Object.fromEntries(
            governanceProjects.map((item) => [item.id, item.name]),
          )}
          onNewConversation={() => {
            setMainView('chat');
            void createPersistentConversation();
          }}
          onNewProject={(name) => void createProjectFromSidebar(name)}
          onSelectProject={(projectId) => {
            updateSettings({ preferredProjectId: projectId });
            setMainView('chat');
          }}
          onOpenSearch={() => setCommandOpen(true)}
          onSelectConversation={(id) => {
            selectConversation(id);
            setMainView('chat');
          }}
          onDeleteConversation={(id) => void deleteConversation(id)}
        />

        <ErrorBoundary
          scope="主工作区"
          resetKey={`${sessionId}:${mainView}`}
          fallbackClassName="flex min-h-0 min-w-0 flex-col border-x border-[#d8cfc3] bg-[#f8ece9] p-5 text-[#67372f]"
        >
        <section className="aether-chat-column border-x border-[#d8cfc3] bg-[#fbfaf7]">
          {mainView !== 'settings' && (
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
          )}
          {mainView !== 'settings' && (
          <div className="aether-toolbar">
            <MainViewTabs value={mainView} onChange={(view) => setMainView(view)} />
            <div className="h-4 w-px shrink-0 bg-[#d8cdbf]" aria-hidden />
            <span className="font-medium text-[#5f554d]">{ui('模式:')}</span>
            {(['casual', 'roundtable', 'project'] as const).map(m => (
              <button 
                key={m}
                onClick={() => updateSettings({ mode: MODE_TO_SETTING[m] })}
                title={m === 'project' ? ui('项目模式会增加模型调用：并行执行、独立核验、最终综合。') : undefined}
                aria-pressed={currentMode === m}
                className={`h-6 rounded px-1.5 text-[11px] ${currentMode === m ? 'bg-[#8d5f35] text-white' : 'text-[#5f554d] hover:bg-[#efe6db]'}`}
              >
                {m === 'casual' ? ui('休闲') : m === 'roundtable' ? ui('圆桌') : ui('项目')}
              </button>
            ))}
            <div className="hidden h-4 w-px shrink-0 bg-[#d8cdbf] lg:block" aria-hidden />
            <div className="min-w-0">
              <SessionProjectSelect
                projects={governanceProjects}
                value={activeProjectId}
                preferredProjectId={preferredProjectId}
                onChangeSessionProject={(projectId) => void bindSessionProject(projectId)}
                onChangePreferred={(projectId) => updateSettings({ preferredProjectId: projectId })}
              />
            </div>
            <span className="ml-auto shrink-0 text-[11px] text-[#8b8176]">
              {ui('当前: {mode}', {
                mode: locale === 'en-US'
                  ? ui(currentMode === 'casual' ? '休闲' : currentMode === 'roundtable' ? '圆桌' : '项目')
                  : currentMode,
              })}
            </span>
          </div>
          )}
          {mainView === 'chat' && (
            <UnifiedFeed
              approvals={sessionApprovals}
              onApprovalDecision={decideApproval}
              hasConfiguredModel={modelConfigs.some(isConfiguredModel)}
              hasLocalModel={modelConfigs.some((model) => model.type === 'local')}
              onInspectGeneratedFile={inspectGeneratedFile}
              onResolveGeneratedFile={resolveGeneratedFile}
              onOpenGeneratedFile={(file, action) => void openGeneratedFile(file, action)}
              onOpenRunHistory={() => {
                setActivePanel('运行');
                setMainView('settings');
              }}
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
              projectNames={Object.fromEntries(
                governanceProjects.map((item) => [item.id, item.name]),
              )}
              onContinue={(id) => {
                selectConversation(id);
                setMainView('chat');
              }}
              onDelete={(id) => void deleteConversation(id)}
            />
          )}
          {mainView === 'tasks' && <TaskHistory tasks={tasks} events={taskEvents} />}
          {mainView === 'settings' && (
            <SettingsPage
              activeTab={isSettingsTab(activePanel) ? activePanel : '设置'}
              onChangeTab={(tab: SettingsTabId) => setActivePanel(tab)}
              onBackToChat={() => {
                setActivePanel(null);
                setMainView('chat');
              }}
            >
              <InspectorPanel
                hideChrome
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
                onInsertComposer={(text) => {
                  setComposerDraft({ id: `knowledge-${Date.now()}`, text });
                  setActivePanel(null);
                  setMainView('chat');
                }}
                onStartVoice={() => {
                  setActivePanel(null);
                  setMainView('chat');
                  void voice.start();
                }}
                voiceSilenceMs={voice.silenceMs}
                onVoiceSilenceMs={voice.setSilenceMs}
                voiceTtsEngine={voice.ttsEngine}
                onVoiceTtsEngine={voice.setTtsEngine}
                onStartCosyVoice={voice.startCosyVoice}
              />
            </SettingsPage>
          )}
          {mainView === 'chat' && <ModelWorkingStrip activity={agentActivity} />}
          {mainView === 'chat' && (
            <div className="aether-composer-shell">
            <Composer
              apiBase={API_BASE}
              catalogEnabled={Boolean(authReady && authToken)}
              onSend={sendMessage}
              onUpload={uploadAttachments}
              providers={modelConfigs.filter(isConfiguredModel).map((model) => model.name)}
              onInterrupt={canInterrupt
                ? (scope) => wsClient.current?.sendAction({ type: 'interrupt', scope })
                : undefined}
              draft={composerDraft}
              onStartVoice={() => {
                setMainView('chat');
                void voice.start();
              }}
              voiceActive={voice.open}
            />
            </div>
          )}
        </section>
        </ErrorBoundary>

      </main>

      {commandOpen && (
        <CommandPalette
          items={commandItems}
          onClose={() => setCommandOpen(false)}
        />
      )}

      <VoiceModeOverlay
        open={voice.open}
        phase={voice.phase}
        muted={voice.muted}
        showTranscript={voice.showTranscript}
        error={voice.error}
        level={voice.level}
        userTurns={voice.userTurns}
        assistantText={voice.assistantText}
        sttLabel={voice.sttLabel}
        ttsLabel={voice.ttsLabel}
        onClose={() => { void voice.stop(); }}
        onToggleMute={voice.toggleMute}
        onToggleTranscript={voice.toggleTranscript}
        onInterrupt={voice.interrupt}
      />

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
