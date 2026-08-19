import { useEffect, useState } from 'react';
import { GitBranch, Play, SquareTerminal } from 'lucide-react';
import { fetchJson } from '../api/client';
import { confirmAction } from './confirmDialog';
import { useI18n } from '../i18n';
import { useSessionStore } from '../stores/sessionStore';
import { API_BASE, isConfiguredModel } from './shared';
import type { AgentRun, AgentWorkspaceCandidate, GitRepository, ModelConfig, PluginItem, ToolItem } from '../types';

interface RunPanelProps {
  sessionId: string;
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
  onUpdatePlan: (
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

function auditEventSummary(payload: Record<string, unknown>): string {
  const labels = [
    payload.model,
    payload.author_model,
    payload.tool_name,
    payload.status,
    payload.decision,
    payload.code,
  ]
    .filter((value): value is string | number | boolean => (
      ['string', 'number', 'boolean'].includes(typeof value)
    ))
    .map(String);
  const narrative = [
    payload.content_preview,
    payload.message,
    payload.error,
    payload.reason,
  ].find((value) => typeof value === 'string' && value.trim());
  if (typeof narrative === 'string') labels.push(narrative.slice(0, 500));
  return labels.join(' · ') || '状态已记录';
}

function auditJsonPreview(value: unknown, maxLength = 2000): string {
  try {
    const encoded = JSON.stringify(value, null, 2);
    if (!encoded) return '';
    return encoded.length > maxLength
      ? `${encoded.slice(0, maxLength)}\n…`
      : encoded;
  } catch {
    return String(value).slice(0, maxLength);
  }
}

export function RunPanel({
  sessionId,
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
  const connection = useSessionStore((state) => state.connection);
  const [checking, setChecking] = useState(false);
  const [verifyingModels, setVerifyingModels] = useState(false);
  const [lastChecked, setLastChecked] = useState<string>('尚未检查');
  const [planDraft, setPlanDraft] = useState<PlanEditorDraft | null>(null);
  const [expandedAuditRunId, setExpandedAuditRunId] = useState<string | null>(null);
  const [runDetails, setRunDetails] = useState<Record<string, AgentRun>>({});
  const [auditLoading, setAuditLoading] = useState<string | null>(null);
  useEffect(() => {
    if (!planDraft?.saving) return;
    const current = agentRuns.find((run) => run.id === planDraft.runId);
    if ((current?.plan_revision || 0) > planDraft.baseRevision) {
      setPlanDraft(null);
    }
  }, [agentRuns, planDraft]);
  const toggleAuditLog = async (run: AgentRun) => {
    if (expandedAuditRunId === run.id) {
      setExpandedAuditRunId(null);
      return;
    }
    setExpandedAuditRunId(run.id);
    if (runDetails[run.id]) return;
    setAuditLoading(run.id);
    try {
      const detail = await fetchJson<AgentRun>(
        `${API_BASE}/agent-runs/${run.id}?session_id=${encodeURIComponent(sessionId)}`,
      );
      setRunDetails((items) => ({ ...items, [run.id]: detail }));
    } catch {
      setRunDetails((items) => ({
        ...items,
        [run.id]: { ...run, events: [], tool_calls: [] },
      }));
    } finally {
      setAuditLoading(null);
    }
  };
  const frontendDetail = window.aetherRuntime?.appVersion
    ? `AetherChat ${window.aetherRuntime.appVersion} · 本地桌面前端`
    : '本地桌面前端';
  const dataDir = window.aetherRuntime?.dataDir || '';
  const dataDirRow: RunCheckRow = {
    name: ui('后端数据目录'),
    detail: dataDir || ui('未由桌面主进程报告'),
    status: dataDir ? ui('已报告') : ui('未知'),
    tone: dataDir ? 'ok' : 'warn',
  };
  const [rows, setRows] = useState<RunCheckRow[]>([
    { name: '后端 API', detail: API_BASE, status: '未检查', tone: 'neutral' },
    { name: '桌面前端', detail: frontendDetail, status: '已加载', tone: 'ok' },
    dataDirRow,
    { name: '模型配置', detail: `${API_BASE}/models/`, status: '未检查', tone: 'neutral' },
    { name: '工具仓', detail: `${API_BASE}/tools/`, status: '未检查', tone: 'neutral' },
    { name: '插件中心', detail: `${API_BASE}/plugins/`, status: '未检查', tone: 'neutral' },
    { name: '电脑控制', detail: '主进程令牌保护', status: window.aetherDesktop ? '未检查' : '仅桌面版', tone: window.aetherDesktop ? 'neutral' : 'warn' },
    { name: 'WebSocket', detail: '/ws/v1/sessions/:id', status: connection === 'online' ? '已连接' : '未连接', tone: connection === 'online' ? 'ok' : 'warn' },
  ]);

  const runCheck = async () => {
    setChecking(true);
    const nextRows: RunCheckRow[] = [
      { name: '桌面前端', detail: frontendDetail, status: '已加载', tone: 'ok' },
      dataDirRow,
      {
        name: 'WebSocket',
        detail: '/ws/v1/sessions/:id',
        status: connection === 'online' ? '已连接' : connection === 'connecting' ? '连接中' : '未连接',
        tone: connection === 'online' ? 'ok' : connection === 'connecting' ? 'warn' : 'bad',
      },
    ];

    try {
      const health = await fetchJson<{
        status?: string;
        ready?: boolean;
        service?: string;
        checks?: { database?: { status?: string; schema_version?: number; expected_schema_version?: number } };
        optional_dependencies?: { redis?: { status?: string } };
      }>(`${API_BASE}/health/ready`);
      const database = health.checks?.database;
      const schemaDetail = database?.schema_version === undefined
        ? '数据库已响应'
        : `数据库 schema ${database.schema_version}/${database.expected_schema_version ?? database.schema_version}`;
      const redisStatus = health.optional_dependencies?.redis?.status || 'not_probed';
      nextRows.unshift({
        name: '后端 API',
        detail: `${API_BASE} · ${schemaDetail} · Redis ${redisStatus}${health.service ? ` · ${health.service}` : ''}`,
        status: health.ready && health.status === 'ready' ? '就绪' : '异常',
        tone: health.ready && health.status === 'ready' ? 'ok' : 'bad',
      });
    } catch (error) {
      nextRows.unshift({ name: '后端 API', detail: `${API_BASE} · ${String(error)}`, status: '不可用', tone: 'bad' });
    }

    try {
      const models = await fetchJson<ModelConfig[]>(`${API_BASE}/models/`);
      const configured = models.filter(isConfiguredModel);
      const alwaysParticipating = configured.filter(
        (model) => model.participation_policy === 'always',
      ).length;
      nextRows.push({
        name: '模型配置',
        detail: `已配置 ${configured.length}/${models.length} 个模型，其中 ${alwaysParticipating} 个始终参与；基础检查不会调用模型 API`,
        status: configured.length ? '已加载' : '未配置',
        tone: configured.length ? 'ok' : 'warn',
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
    if (!await confirmAction('连接验证会向每个已配置且已启用的模型发送一条最小测试请求，可能产生少量 API 费用。确定继续吗？')) return;
    setVerifyingModels(true);
    try {
      const models = await fetchJson<ModelConfig[]>(`${API_BASE}/models/`);
      const enabled = models.filter((model) => (
        isConfiguredModel(model) && model.participation_policy === 'always'
      ));
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
    completed: { label: '历史完成（未核验）', className: 'bg-[#f2e3c4] text-[#855f18]' },
    completed_verified: { label: '已核验完成', className: 'bg-[#dfead7] text-[#3f6c3a]' },
    completed_with_warnings: { label: '已生成，存在警告', className: 'bg-[#f2e3c4] text-[#855f18]' },
    uncertain: { label: '结果待确认', className: 'bg-[#ead5d1] text-[#8a3b31]' },
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
            const status = runStatus[run.outcome_status || run.status];
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
                    <div className={`mt-1 text-[11px] ${
                      run.mode === 'project' && run.execution_strategy_source === 'compatibility_default'
                        ? 'text-[#9a5a18]'
                        : 'text-[#7a7065]'
                    }`}>
                      {ui('执行 {strategy} · 来源 {source}', {
                        strategy: (run.effective_execution_strategy || run.input.execution_strategy || 'direct') === 'planned'
                          ? ui('计划')
                          : ui('直接'),
                        source: run.execution_strategy_source === 'compatibility_default'
                          ? ui('旧客户端兼容默认')
                          : ui('显式请求'),
                      })}
                    </div>
                    {!!run.effect_count && (
                      <div className={`mt-1 text-[11px] ${
                        run.effect_verification_status === 'verified'
                          ? 'text-[#477257]'
                          : run.effect_verification_status === 'failed' || run.effect_verification_status === 'uncertain'
                            ? 'text-[#9a4237]'
                            : 'text-[#9a5a18]'
                      }`}>
                        {ui('副作用核验 {verified}/{total} · {status}', {
                          verified: run.verified_effect_count || 0,
                          total: run.effect_count,
                          status: run.effect_verification_status === 'verified'
                            ? ui('全部已核验')
                            : run.effect_verification_status === 'uncertain'
                              ? ui('存在结果不确定项')
                              : run.effect_verification_status === 'failed'
                                ? ui('存在失败项')
                                : ui('存在未核验项'),
                        })}
                      </div>
                    )}
                    {run.completion_summary && (
                      <div className={`mt-1 text-[11px] ${
                        (run.outcome_status || run.status) === 'completed_verified'
                          ? 'text-[#477257]'
                          : 'text-[#9a5a18]'
                      }`}>
                        {ui(run.completion_summary)}
                      </div>
                    )}
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
                    <button onClick={() => void toggleAuditLog(run)} className="rounded-md border border-[#8d735d] px-2 py-1 text-xs text-[#654927]">
                      {expandedAuditRunId === run.id ? ui('收起协作日志') : ui('协作日志')}
                    </button>
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
                        onClick={async () => {
                          if (await confirmAction(ui('拒绝该计划并结束本次运行？'), { danger: true })) {
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
                              aria-label={ui('{kind} 步骤标题', { kind: step.kind })}
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
                              aria-label={ui('{kind} 执行模型', { kind: step.kind })}
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
                              aria-label={ui('{kind} 步骤角色', { kind: step.kind })}
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
                              aria-label={ui('{kind} 步骤说明', { kind: step.kind })}
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
                        {ui('{model} · {status} · 第 {attempt} 次', {
                          model: step.model,
                          status: step.status,
                          attempt: step.attempt || 0,
                        })}
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
                {expandedAuditRunId === run.id && (
                  <div className="mt-3 rounded-lg border border-[#d8cec1] bg-white p-3">
                    <div className="flex items-start justify-between gap-3">
                      <div>
                        <div className="text-xs font-semibold text-[#574a3e]">{ui('Agent 协作审计日志')}</div>
                        <div className="mt-1 text-[10px] leading-4 text-[#897b6d]">{ui('展示可核验的状态、消息交接、工具调用和结果；不记录或暴露模型私密思维链。')}</div>
                      </div>
                      <span className="shrink-0 text-[10px] text-[#897b6d]">
                        {auditLoading === run.id
                          ? ui('载入中…')
                          : ui('{count} 个事件', { count: runDetails[run.id]?.events?.length || 0 })}
                      </span>
                    </div>
                    {auditLoading !== run.id && (
                      <div className="mt-3 grid gap-3 lg:grid-cols-2">
                        <div>
                          <div className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-[#897b6d]">{ui('事件时间线')}</div>
                          <ol className="max-h-64 space-y-2 overflow-auto border-l border-[#d8cec1] pl-3">
                            {(runDetails[run.id]?.events || []).map((event) => (
                              <li key={event.id} className="relative text-[11px] leading-4 text-[#5f554b]">
                                <span className="absolute -left-[16px] top-1 h-1.5 w-1.5 rounded-full bg-[#8d5f35]" />
                                <div className="flex justify-between gap-2">
                                  <span className="font-semibold">{event.event_type}</span>
                                  <time className="shrink-0 text-[9px] text-[#9b8d80]">{new Date(event.created_at).toLocaleTimeString()}</time>
                                </div>
                                <div className="mt-0.5 break-all text-[10px] text-[#897b6d]">
                                  {auditEventSummary(event.payload)}
                                </div>
                              </li>
                            ))}
                            {!runDetails[run.id]?.events?.length && <li className="text-[10px] text-[#897b6d]">{ui('暂无事件记录。')}</li>}
                          </ol>
                        </div>
                        <div>
                          <div className="mb-2 text-[10px] font-semibold uppercase tracking-wide text-[#897b6d]">{ui('工具调用')}</div>
                          <div className="max-h-64 space-y-1.5 overflow-auto">
                            {(runDetails[run.id]?.tool_calls || []).map((call) => (
                              <div key={call.id} className="rounded-lg bg-[#f8f4ee] px-2.5 py-2 text-[11px]">
                                <div className="flex justify-between gap-2">
                                  <span className="font-medium">Used {call.tool_name.split('__').pop()}</span>
                                  <span className={call.status === 'completed' && (call.verification_status === 'verified' || call.verification_status === 'not_applicable') ? 'text-[#477257]' : call.status === 'failed' || call.status === 'denied' || call.verification_status === 'failed' ? 'text-[#9a4237]' : 'text-[#9a5a18]'}>
                                    {call.status}
                                    {call.side_effect && ui(' · {status}', {
                                      status: ui(call.verification_status === 'verified'
                                        ? '已核验'
                                        : call.verification_status === 'unverified'
                                          ? '未核验'
                                          : call.verification_status === 'uncertain'
                                            ? '结果不确定'
                                            : call.verification_status === 'failed'
                                              ? '核验失败'
                                              : '待核验'),
                                    })}
                                  </span>
                                </div>
                                <pre className="mt-1 max-h-20 overflow-auto whitespace-pre-wrap break-all text-[9px] leading-4 text-[#76695d]">{auditJsonPreview(call.arguments)}</pre>
                                {call.result !== undefined && call.result !== null && (
                                  <pre className="mt-1 max-h-20 overflow-auto whitespace-pre-wrap break-all border-t border-[#e1d8cd] pt-1 text-[9px] leading-4 text-[#526f5c]">{ui('结果：')}{auditJsonPreview(call.result)}</pre>
                                )}
                                {call.error && <div className="mt-1 break-all text-[9px] text-[#9a4237]">{ui('错误：')}{call.error}</div>}
                                {!!call.evidence?.length && (
                                  <div className="mt-1 text-[9px] text-[#76695d]">
                                    {ui('证据：')}{call.evidence.map((item) => String(item.kind || 'unknown')).join('、')}
                                  </div>
                                )}
                              </div>
                            ))}
                            {!runDetails[run.id]?.tool_calls?.length && <div className="text-[10px] text-[#897b6d]">{ui('本次运行没有工具调用。')}</div>}
                          </div>
                        </div>
                      </div>
                    )}
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
                        && [
                          'completed',
                          'completed_verified',
                          'completed_with_warnings',
                          'uncertain',
                          'failed',
                          'interrupted',
                        ].includes(run.status)
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
                                  {check.timed_out ? ui(' · 超时') : ''}
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
