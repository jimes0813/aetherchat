import { useCallback, useEffect, useState } from 'react';
import { BookMarked, Plus, RefreshCw, Sparkles, Trash2 } from 'lucide-react';
import { fetchJson } from '../api/client';
import { useI18n } from '../i18n';

export type InstructionVersion = {
  version: string;
  body?: string;
  changelog?: string;
  content_hash?: string;
  published_at?: string;
};

export type InstructionRule = {
  id: string;
  name: string;
  body: string;
  enabled: boolean;
  priority: number;
  source?: string;
  content_hash?: string;
  published_version?: string;
  draft_dirty?: boolean;
  versions?: InstructionVersion[];
};

export type InstructionSkill = {
  id: string;
  name: string;
  description?: string;
  body: string;
  version: string;
  published_version?: string;
  enabled: boolean;
  always_on: boolean;
  trigger_keywords: string[];
  content_hash?: string;
  draft_dirty?: boolean;
  versions?: InstructionVersion[];
  input_schema?: Record<string, unknown> | null;
  output_schema?: Record<string, unknown> | null;
};

export type ResolvePreview = {
  context: string;
  manifest: Array<{ kind: string; id: string; name: string; version?: string; content_hash?: string }>;
  layer_count: number;
};

export type DryRunPermission = {
  tool: string;
  label?: string;
  risk: string;
  level: string;
  requires_approval: boolean;
  source?: string;
  note?: string;
};

export type DryRunReport = {
  dry_run: boolean;
  executed: boolean;
  status: string;
  layer_count: number;
  layers: Array<{ kind: string; name: string; version?: string }>;
  permissions: DryRunPermission[];
  summary: {
    permission_count: number;
    high_risk_count: number;
    approval_count: number;
    io_field_count: number;
  };
  issues: string[];
  context_preview?: string;
  io_checklist?: Array<{ kind: string; name: string; type: string; required: boolean }>;
};

export type InstructionWorkflow = {
  id: string;
  name: string;
  trigger_type: 'interval' | 'webhook' | 'manual';
  execution_mode: 'dry_run' | 'automation';
  enabled: boolean;
  skill_id?: string | null;
  interval_minutes?: number | null;
  schedule_id?: string | null;
  has_webhook?: boolean;
  webhook_token?: string;
  webhook_path?: string | null;
  last_status?: string | null;
  last_run_at?: string | null;
  last_result_summary?: string | null;
};

export type SkillsRulesPanelProps = {
  apiBase: string;
  sessionId: string;
};

export function SkillsRulesPanel({ apiBase, sessionId }: SkillsRulesPanelProps) {
  const { ui } = useI18n();
  const [rules, setRules] = useState<InstructionRule[]>([]);
  const [skills, setSkills] = useState<InstructionSkill[]>([]);
  const [workflows, setWorkflows] = useState<InstructionWorkflow[]>([]);
  const [status, setStatus] = useState('');
  const [preview, setPreview] = useState<ResolvePreview | null>(null);
  const [dryRun, setDryRun] = useState<DryRunReport | null>(null);
  const [previewMessage, setPreviewMessage] = useState('请评审这段代码');
  const [agentsPath, setAgentsPath] = useState('');
  const [newRuleName, setNewRuleName] = useState('');
  const [newRuleBody, setNewRuleBody] = useState('');
  const [newSkillName, setNewSkillName] = useState('');
  const [newSkillBody, setNewSkillBody] = useState('');
  const [newSkillKeywords, setNewSkillKeywords] = useState('评审, review');
  const [wfName, setWfName] = useState('');
  const [wfSkillId, setWfSkillId] = useState('');
  const [wfTrigger, setWfTrigger] = useState<'manual' | 'interval' | 'webhook'>('manual');
  const [wfInterval, setWfInterval] = useState(60);
  const [wfMode, setWfMode] = useState<'dry_run' | 'automation'>('dry_run');
  const [wfMessage, setWfMessage] = useState('按技能执行例行检查');
  const [lastWebhookToken, setLastWebhookToken] = useState('');
  const [busy, setBusy] = useState(false);

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [ruleRows, skillRows, workflowRows] = await Promise.all([
        fetchJson<InstructionRule[]>(`${apiBase}/instructions/rules?session_id=${encodeURIComponent(sessionId)}`),
        fetchJson<InstructionSkill[]>(`${apiBase}/instructions/skills`),
        fetchJson<InstructionWorkflow[]>(
          `${apiBase}/instructions/workflows?session_id=${encodeURIComponent(sessionId)}`,
        ).catch(() => []),
      ]);
      setRules(ruleRows);
      setSkills(skillRows);
      setWorkflows(workflowRows);
      setStatus(
        `已加载 ${ruleRows.length} 条规则 · ${skillRows.length} 个技能 · ${workflowRows.length} 个工作流`,
      );
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }, [apiBase, sessionId]);

  useEffect(() => {
    void load();
  }, [load]);

  const createRule = async () => {
    if (!newRuleName.trim() || !newRuleBody.trim()) {
      setStatus('请填写规则名称与正文');
      return;
    }
    setBusy(true);
    try {
      await fetchJson(`${apiBase}/instructions/rules`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newRuleName.trim(),
          body: newRuleBody.trim(),
          enabled: true,
          priority: 50,
          scope: 'global',
        }),
      });
      setNewRuleName('');
      setNewRuleBody('');
      await load();
      setStatus('规则已创建');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
      setBusy(false);
    }
  };

  const createSkill = async () => {
    if (!newSkillName.trim() || !newSkillBody.trim()) {
      setStatus('请填写技能名称与正文');
      return;
    }
    setBusy(true);
    try {
      const keywords = newSkillKeywords
        .split(/[,，\n]/)
        .map((item) => item.trim())
        .filter(Boolean);
      await fetchJson(`${apiBase}/instructions/skills`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: newSkillName.trim(),
          body: newSkillBody.trim(),
          description: '',
          version: '1.0.0',
          enabled: true,
          always_on: false,
          trigger_keywords: keywords,
        }),
      });
      setNewSkillName('');
      setNewSkillBody('');
      await load();
      setStatus('技能已创建');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
      setBusy(false);
    }
  };

  const toggleRule = async (rule: InstructionRule) => {
    try {
      await fetchJson(`${apiBase}/instructions/rules/${encodeURIComponent(rule.id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !rule.enabled }),
      });
      await load();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    }
  };

  const toggleSkill = async (skill: InstructionSkill) => {
    try {
      await fetchJson(`${apiBase}/instructions/skills/${encodeURIComponent(skill.id)}`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enabled: !skill.enabled }),
      });
      await load();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    }
  };

  const removeRule = async (id: string) => {
    try {
      await fetchJson(`${apiBase}/instructions/rules/${encodeURIComponent(id)}`, { method: 'DELETE' });
      await load();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    }
  };

  const removeSkill = async (id: string) => {
    try {
      await fetchJson(`${apiBase}/instructions/skills/${encodeURIComponent(id)}`, { method: 'DELETE' });
      await load();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    }
  };

  const publishSkill = async (skill: InstructionSkill) => {
    setBusy(true);
    try {
      await fetchJson(`${apiBase}/instructions/skills/${encodeURIComponent(skill.id)}/publish`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ changelog: '从面板发布' }),
      });
      await load();
      setStatus(`已发布技能 ${skill.name}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
      setBusy(false);
    }
  };

  const rollbackSkill = async (skill: InstructionSkill, version: string) => {
    setBusy(true);
    try {
      await fetchJson(`${apiBase}/instructions/skills/${encodeURIComponent(skill.id)}/rollback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version }),
      });
      await load();
      setStatus(`已回滚技能 ${skill.name} → v${version}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
      setBusy(false);
    }
  };

  const publishRule = async (rule: InstructionRule) => {
    setBusy(true);
    try {
      await fetchJson(`${apiBase}/instructions/rules/${encodeURIComponent(rule.id)}/publish`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ changelog: '从面板发布' }),
      });
      await load();
      setStatus(`已发布规则 ${rule.name}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
      setBusy(false);
    }
  };

  const rollbackRule = async (rule: InstructionRule, version: string) => {
    setBusy(true);
    try {
      await fetchJson(`${apiBase}/instructions/rules/${encodeURIComponent(rule.id)}/rollback`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ version }),
      });
      await load();
      setStatus(`已回滚规则 ${rule.name} → v${version}`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
      setBusy(false);
    }
  };

  const runPreview = async () => {
    setBusy(true);
    try {
      const result = await fetchJson<ResolvePreview>(`${apiBase}/instructions/resolve`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_message: previewMessage,
          session_id: sessionId,
        }),
      });
      setPreview(result);
      setStatus(`预览：将注入 ${result.layer_count} 层指令`);
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const runDryRun = async () => {
    setBusy(true);
    try {
      const result = await fetchJson<DryRunReport>(`${apiBase}/instructions/dry-run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          user_message: previewMessage,
          session_id: sessionId,
        }),
      });
      setDryRun(result);
      setStatus(
        `Dry-run：${result.layer_count} 层 · ${result.summary.permission_count} 项权限`
        + (result.summary.high_risk_count ? ` · ${result.summary.high_risk_count} 高风险` : ''),
      );
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const importAgents = async () => {
    if (!agentsPath.trim()) {
      setStatus('请填写 AGENTS.md 或仓库根目录绝对路径');
      return;
    }
    setBusy(true);
    try {
      await fetchJson(`${apiBase}/instructions/import-agents-md`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ path: agentsPath.trim() }),
      });
      await load();
      setStatus('已导入 AGENTS.md 为规则');
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
      setBusy(false);
    }
  };

  const createWorkflow = async () => {
    if (!wfName.trim()) {
      setStatus('请填写工作流名称');
      return;
    }
    setBusy(true);
    try {
      const created = await fetchJson<InstructionWorkflow>(`${apiBase}/instructions/workflows`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name: wfName.trim(),
          session_id: sessionId,
          skill_id: wfSkillId || null,
          message: wfMessage,
          trigger_type: wfTrigger,
          interval_minutes: wfTrigger === 'interval' ? wfInterval : null,
          execution_mode: wfMode,
          enabled: true,
          targets: ['all'],
        }),
      });
      if (created.webhook_token) {
        setLastWebhookToken(created.webhook_token);
      }
      setWfName('');
      await load();
      setStatus(
        created.trigger_type === 'webhook'
          ? '工作流已创建（请复制 webhook token，仅显示一次）'
          : `工作流已创建（${created.trigger_type}）`,
      );
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
      setBusy(false);
    }
  };

  const runWorkflow = async (workflow: InstructionWorkflow) => {
    setBusy(true);
    try {
      const result = await fetchJson<{
        execution_mode: string;
        dry_run?: { status?: string; layer_count?: number };
        job?: { id?: string; status?: string } | null;
      }>(`${apiBase}/instructions/workflows/${encodeURIComponent(workflow.id)}/run`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ message: wfMessage || undefined }),
      });
      await load();
      setStatus(
        result.job?.id
          ? `已入队后台任务 ${result.job.id}（${result.job.status}）`
          : `工作流 dry-run：${result.dry_run?.status || 'ok'} · ${result.dry_run?.layer_count ?? 0} 层`,
      );
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
      setBusy(false);
    }
  };

  const removeWorkflow = async (id: string) => {
    try {
      await fetchJson(`${apiBase}/instructions/workflows/${encodeURIComponent(id)}`, {
        method: 'DELETE',
      });
      await load();
    } catch (error) {
      setStatus(error instanceof Error ? error.message : String(error));
    }
  };

  return (
    <div className="aether-tab-panel aether-panel-scroll min-h-0 flex-1 overflow-auto p-4">
      <div className="mb-4 flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#e5d9ca] text-[#684827]">
          <BookMarked size={17} />
        </div>
        <div className="min-w-0">
          <h2 className="text-sm font-semibold">{ui('规范 · Skills / Rules')}</h2>
          <p className="mt-1 text-xs leading-5 text-[#7a7065]">
            {ui('分层指令：AGENTS.md → 已发布规则 → 已发布技能。支持版本回滚、typed IO，以及不执行工具的 dry-run 权限清单。')}
          </p>
        </div>
        <button
          type="button"
          onClick={() => void load()}
          disabled={busy}
          className="ml-auto inline-flex h-9 shrink-0 items-center gap-1.5 rounded-md border border-[#cfc4b7] bg-white px-3 text-xs font-medium text-[#5d4a3a] disabled:opacity-50"
        >
          <RefreshCw size={14} />
          {ui('刷新')}
        </button>
      </div>

      {status && (
        <div className="mb-3 rounded-md bg-[#e9e0d5] px-3 py-2 text-xs text-[#6e5d4b]" aria-live="polite">
          {ui(status)}
        </div>
      )}

      <section className="mb-5 rounded-xl border border-[#ded6ca] bg-white p-3">
        <div className="mb-2 text-xs font-semibold text-[#5d4a3a]">{ui('预览当前消息将注入的指令')}</div>
        <textarea
          value={previewMessage}
          onChange={(event) => setPreviewMessage(event.target.value)}
          className="min-h-16 w-full rounded-lg border border-[#d8cdbf] bg-[#fbfaf7] p-2 text-xs"
          placeholder={ui('输入示例用户消息')}
        />
        <div className="mt-2 flex flex-wrap gap-2">
          <button
            type="button"
            disabled={busy}
            onClick={() => void runPreview()}
            className="inline-flex h-9 items-center rounded-md bg-[#2f2a24] px-3 text-xs font-medium text-white disabled:opacity-50"
          >
            {ui('解析预览')}
          </button>
          <button
            type="button"
            disabled={busy}
            onClick={() => void runDryRun()}
            className="inline-flex h-9 items-center rounded-md border border-[#8d5f35] bg-[#fffaf4] px-3 text-xs font-medium text-[#6b4425] disabled:opacity-50"
          >
            {ui('Dry-run 权限清单')}
          </button>
        </div>
        {preview && (
          <div className="mt-3 space-y-2">
            <div className="text-[11px] text-[#6d6258]">
              manifest：
              {preview.manifest.map((item) => (
                <span key={item.id} className="mr-2 inline-block rounded bg-[#f0e9df] px-1.5 py-0.5">
                  {item.kind}:{item.name}
                  {item.content_hash ? ` · ${item.content_hash.slice(0, 8)}` : ''}
                </span>
              ))}
              {!preview.manifest.length && ui('（无）')}
            </div>
            {preview.context && (
              <pre className="max-h-40 overflow-auto whitespace-pre-wrap rounded-lg bg-[#f7f3ed] p-2 text-[11px] leading-5 text-[#4a4037]">
                {preview.context}
              </pre>
            )}
          </div>
        )}
        {dryRun && (
          <div className="mt-3 space-y-2 rounded-lg border border-[#e2d6c8] bg-[#fbf8f3] p-3">
            <div className="text-[11px] font-semibold text-[#5d4a3a]">
              Dry-run · {dryRun.status}
              <span className="ml-2 font-normal text-[#8a7f75]">{ui('不执行工具 · executed={value}', { value: String(dryRun.executed) })}</span>
            </div>
            {dryRun.issues?.length > 0 && (
              <ul className="list-disc space-y-0.5 pl-4 text-[11px] text-[#8a5a28]">
                {dryRun.issues.map((issue) => (
                  <li key={issue}>{issue}</li>
                ))}
              </ul>
            )}
            <div className="text-[11px] text-[#6d6258]">
              {ui('层 {layers} · 权限 {permissions} · 高风险 {highRisk} · 需授权 {approvals} · IO 字段 {ioFields}', {
                layers: dryRun.summary.permission_count >= 0 ? dryRun.layer_count : 0,
                permissions: dryRun.summary.permission_count,
                highRisk: dryRun.summary.high_risk_count,
                approvals: dryRun.summary.approval_count,
                ioFields: dryRun.summary.io_field_count,
              })}
            </div>
            {dryRun.permissions.length > 0 ? (
              <div className="space-y-1">
                {dryRun.permissions.map((item) => (
                  <div
                    key={`${item.tool}-${item.source}`}
                    className="flex flex-wrap items-center gap-2 rounded border border-[#e7dfd4] bg-white px-2 py-1.5 text-[11px]"
                  >
                    <span className={`rounded px-1.5 py-0.5 font-medium ${
                      item.level === 'high' || item.level === 'critical'
                        ? 'bg-[#f8e8e4] text-[#8a4036]'
                        : item.level === 'medium'
                          ? 'bg-[#fff5ec] text-[#8a5a28]'
                          : 'bg-[#eef6ea] text-[#326022]'
                    }`}
                    >
                      {item.level}
                    </span>
                    <span className="font-medium text-[#4a4037]">{item.label || item.tool}</span>
                    <span className="text-[#8a7f75]">{item.risk}</span>
                    {item.requires_approval ? (
                      <span className="rounded bg-[#eee4d8] px-1 text-[10px] text-[#6b4425]">{ui('需授权')}</span>
                    ) : null}
                    {item.note ? <span className="w-full text-[10px] text-[#8a7f75]">{item.note}</span> : null}
                  </div>
                ))}
              </div>
            ) : (
              <div className="text-[11px] text-[#8a7f75]">{ui('未检测到副作用工具暗示')}</div>
            )}
            {dryRun.io_checklist && dryRun.io_checklist.length > 0 && (
              <div className="text-[11px] text-[#6d6258]">
                {ui('IO 契约：')}
                {dryRun.io_checklist.map((field) => (
                  <span key={`${field.kind}-${field.name}`} className="mr-1 inline-block rounded bg-[#f0e9df] px-1">
                    {field.name}{field.required ? '*' : ''}
                  </span>
                ))}
              </div>
            )}
          </div>
        )}
      </section>

      <section className="mb-5 rounded-xl border border-[#ded6ca] bg-white p-3">
        <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-[#5d4a3a]">
          <BookMarked size={13} />
          {ui('规则（Rules）')}
        </div>
        <div className="space-y-2">
          {rules.map((rule) => {
            const published = rule.published_version || '1.0.0';
            const priorVersions = (rule.versions || [])
              .map((item) => item.version)
              .filter((version) => version !== published);
            return (
              <div key={rule.id} className="rounded-lg border border-[#e7dfd4] p-2">
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">
                      {rule.name}
                      <span className="ml-1 text-[11px] font-normal text-[#8a7f75]">v{published}</span>
                      {rule.draft_dirty ? (
                        <span className="ml-1 rounded bg-[#fff5ec] px-1 text-[10px] text-[#8a5a28]">{ui('草稿未发布')}</span>
                      ) : null}
                    </div>
                    <div className="mt-0.5 text-[11px] text-[#8a7f75]">
                      {ui('优先级 {value}', { value: rule.priority })}
                      {rule.source ? ` · ${rule.source}` : ''}
                      {rule.content_hash ? ` · ${rule.content_hash.slice(0, 8)}` : ''}
                    </div>
                    <p className="mt-1 line-clamp-3 text-[11px] leading-5 text-[#5f554d]">{rule.body}</p>
                    <div className="mt-2 flex flex-wrap gap-1">
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void publishRule(rule)}
                        className="rounded border border-[#cfc4b7] px-2 py-0.5 text-[11px] text-[#5d4a3a]"
                      >
                        {ui('发布')}
                      </button>
                      {priorVersions.slice().reverse().slice(0, 3).map((version) => (
                        <button
                          key={version}
                          type="button"
                          disabled={busy}
                          onClick={() => void rollbackRule(rule, version)}
                          className="rounded border border-[#e2b8b0] px-2 py-0.5 text-[11px] text-[#8a4036]"
                        >
                          {ui('回滚 v{version}', { version })}
                        </button>
                      ))}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => void toggleRule(rule)}
                    className={`shrink-0 rounded px-2 py-1 text-[11px] ${rule.enabled ? 'bg-[#eef6ea] text-[#326022]' : 'bg-[#f0e9df] text-[#7a7065]'}`}
                  >
                    {rule.enabled ? ui('已启用') : ui('已停用')}
                  </button>
                  <button
                    type="button"
                    onClick={() => void removeRule(rule.id)}
                    className="shrink-0 rounded p-1 text-[#8a4036]"
                    aria-label={ui('删除规则 {name}', { name: rule.name })}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
        <div className="mt-3 space-y-2 border-t border-[#eee6db] pt-3">
          <input
            value={newRuleName}
            onChange={(event) => setNewRuleName(event.target.value)}
            placeholder={ui('新规则名称')}
            className="w-full rounded-lg border border-[#d8cdbf] px-2 py-1.5 text-xs"
          />
          <textarea
            value={newRuleBody}
            onChange={(event) => setNewRuleBody(event.target.value)}
            placeholder={ui('规则正文')}
            className="min-h-16 w-full rounded-lg border border-[#d8cdbf] px-2 py-1.5 text-xs"
          />
          <button
            type="button"
            disabled={busy}
            onClick={() => void createRule()}
            className="inline-flex items-center gap-1 rounded-md bg-[#8d5f35] px-2 py-1 text-xs text-white disabled:opacity-50"
          >
            <Plus size={12} />
            {ui('添加规则')}
          </button>
        </div>
        <div className="mt-3 space-y-2 border-t border-[#eee6db] pt-3">
          <div className="text-[11px] font-medium text-[#6d6258]">{ui('从路径导入 AGENTS.md')}</div>
          <input
            value={agentsPath}
            onChange={(event) => setAgentsPath(event.target.value)}
            placeholder={ui('/path/to/repo 或 /path/to/AGENTS.md')}
            className="w-full rounded-lg border border-[#d8cdbf] px-2 py-1.5 font-mono text-[11px]"
          />
          <button
            type="button"
            disabled={busy}
            onClick={() => void importAgents()}
            className="rounded-md border border-[#cfc4b7] px-2 py-1 text-xs text-[#5d4a3a] disabled:opacity-50"
          >
            {ui('导入为规则')}
          </button>
        </div>
      </section>

      <section className="rounded-xl border border-[#ded6ca] bg-white p-3">
        <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-[#5d4a3a]">
          <Sparkles size={13} />
          {ui('技能（Skills）')}
        </div>
        <div className="space-y-2">
          {skills.map((skill) => {
            const published = skill.published_version || skill.version || '1.0.0';
            const priorVersions = (skill.versions || [])
              .map((item) => item.version)
              .filter((version) => version !== published);
            const hasIo = Boolean(skill.input_schema || skill.output_schema);
            return (
              <div key={skill.id} className="rounded-lg border border-[#e7dfd4] p-2">
                <div className="flex items-start gap-2">
                  <div className="min-w-0 flex-1">
                    <div className="truncate text-sm font-medium">
                      {skill.name}
                      <span className="ml-1 text-[11px] font-normal text-[#8a7f75]">v{published}</span>
                      {skill.draft_dirty ? (
                        <span className="ml-1 rounded bg-[#fff5ec] px-1 text-[10px] text-[#8a5a28]">{ui('草稿未发布')}</span>
                      ) : null}
                      {hasIo ? (
                        <span className="ml-1 rounded bg-[#eef6ea] px-1 text-[10px] text-[#326022]">typed IO</span>
                      ) : null}
                    </div>
                    <div className="mt-0.5 text-[11px] text-[#8a7f75]">
                      {skill.always_on
                        ? ui('始终启用')
                        : ui('触发词：{value}', { value: (skill.trigger_keywords || []).join('、') || ui('（无）') })}
                      {skill.content_hash ? ` · ${skill.content_hash.slice(0, 8)}` : ''}
                    </div>
                    <p className="mt-1 line-clamp-3 text-[11px] leading-5 text-[#5f554d]">{skill.body}</p>
                    <div className="mt-2 flex flex-wrap gap-1">
                      <button
                        type="button"
                        disabled={busy}
                        onClick={() => void publishSkill(skill)}
                        className="rounded border border-[#cfc4b7] px-2 py-0.5 text-[11px] text-[#5d4a3a]"
                      >
                        {ui('发布')}
                      </button>
                      {priorVersions.slice().reverse().slice(0, 3).map((version) => (
                        <button
                          key={version}
                          type="button"
                          disabled={busy}
                          onClick={() => void rollbackSkill(skill, version)}
                          className="rounded border border-[#e2b8b0] px-2 py-0.5 text-[11px] text-[#8a4036]"
                        >
                          {ui('回滚 v{version}', { version })}
                        </button>
                      ))}
                    </div>
                  </div>
                  <button
                    type="button"
                    onClick={() => void toggleSkill(skill)}
                    className={`shrink-0 rounded px-2 py-1 text-[11px] ${skill.enabled ? 'bg-[#eef6ea] text-[#326022]' : 'bg-[#f0e9df] text-[#7a7065]'}`}
                  >
                    {skill.enabled ? ui('已启用') : ui('已停用')}
                  </button>
                  <button
                    type="button"
                    onClick={() => void removeSkill(skill.id)}
                    className="shrink-0 rounded p-1 text-[#8a4036]"
                    aria-label={ui('删除技能 {name}', { name: skill.name })}
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              </div>
            );
          })}
        </div>
        <div className="mt-3 space-y-2 border-t border-[#eee6db] pt-3">
          <input
            value={newSkillName}
            onChange={(event) => setNewSkillName(event.target.value)}
            placeholder={ui('新技能名称')}
            className="w-full rounded-lg border border-[#d8cdbf] px-2 py-1.5 text-xs"
          />
          <input
            value={newSkillKeywords}
            onChange={(event) => setNewSkillKeywords(event.target.value)}
            placeholder={ui('触发关键词，逗号分隔')}
            className="w-full rounded-lg border border-[#d8cdbf] px-2 py-1.5 text-xs"
          />
          <textarea
            value={newSkillBody}
            onChange={(event) => setNewSkillBody(event.target.value)}
            placeholder={ui('技能正文（步骤、输出契约等）')}
            className="min-h-20 w-full rounded-lg border border-[#d8cdbf] px-2 py-1.5 text-xs"
          />
          <button
            type="button"
            disabled={busy}
            onClick={() => void createSkill()}
            className="inline-flex items-center gap-1 rounded-md bg-[#8d5f35] px-2 py-1 text-xs text-white disabled:opacity-50"
          >
            <Plus size={12} />
            {ui('添加技能')}
          </button>
        </div>
      </section>

      <section className="mt-5 rounded-xl border border-[#ded6ca] bg-white p-3">
        <div className="mb-2 text-xs font-semibold text-[#5d4a3a]">{ui('工作流触发（interval / webhook / manual）')}</div>
        <p className="mb-3 text-[11px] leading-5 text-[#7a7065]">
          {ui('绑定技能后可定时或 webhook 触发。默认 dry-run 只出权限清单；automation 模式会入队后台只读任务（沿用自动化调度器）。')}
        </p>
        <div className="space-y-2">
          {workflows.map((workflow) => (
            <div key={workflow.id} className="rounded-lg border border-[#e7dfd4] p-2">
              <div className="flex items-start gap-2">
                <div className="min-w-0 flex-1">
                  <div className="text-sm font-medium">
                    {workflow.name}
                    <span className="ml-1 text-[11px] font-normal text-[#8a7f75]">
                      {workflow.trigger_type} · {workflow.execution_mode}
                    </span>
                  </div>
                  <div className="mt-0.5 text-[11px] text-[#8a7f75]">
                    {workflow.trigger_type === 'interval' && workflow.interval_minutes
                      ? ui('每 {count} 分钟', { count: workflow.interval_minutes })
                      : workflow.trigger_type === 'webhook'
                        ? ui('Webhook token 触发')
                        : ui('手动触发')}
                    {workflow.schedule_id ? ` · schedule ${workflow.schedule_id.slice(0, 8)}` : ''}
                    {workflow.last_status ? ui(' · 上次 {status}', { status: workflow.last_status }) : ''}
                  </div>
                  {workflow.last_result_summary && (
                    <div className="mt-1 text-[10px] text-[#8a7f75]">{workflow.last_result_summary}</div>
                  )}
                  <div className="mt-2 flex flex-wrap gap-1">
                    <button
                      type="button"
                      disabled={busy}
                      onClick={() => void runWorkflow(workflow)}
                      className="rounded border border-[#cfc4b7] px-2 py-0.5 text-[11px] text-[#5d4a3a]"
                    >
                      {ui('立即运行')}
                    </button>
                    <button
                      type="button"
                      onClick={() => void removeWorkflow(workflow.id)}
                      className="rounded border border-[#e2b8b0] px-2 py-0.5 text-[11px] text-[#8a4036]"
                    >
                      {ui('删除')}
                    </button>
                  </div>
                </div>
              </div>
            </div>
          ))}
          {!workflows.length && (
            <div className="rounded-lg border border-dashed border-[#e1d9cd] p-3 text-[11px] text-[#8a7f75]">
              {ui('尚无工作流。可创建 manual / interval / webhook 三种触发。')}
            </div>
          )}
        </div>
        <div className="mt-3 space-y-2 border-t border-[#eee6db] pt-3">
          <input
            value={wfName}
            onChange={(event) => setWfName(event.target.value)}
            placeholder={ui('工作流名称')}
            className="w-full rounded-lg border border-[#d8cdbf] px-2 py-1.5 text-xs"
          />
          <select
            value={wfSkillId}
            onChange={(event) => setWfSkillId(event.target.value)}
            className="w-full rounded-lg border border-[#d8cdbf] px-2 py-1.5 text-xs"
          >
            <option value="">{ui('不绑定技能')}</option>
            {skills.map((skill) => (
              <option key={skill.id} value={skill.id}>{skill.name}</option>
            ))}
          </select>
          <div className="flex flex-wrap gap-2">
            <select
              value={wfTrigger}
              onChange={(event) => setWfTrigger(event.target.value as typeof wfTrigger)}
              className="rounded-lg border border-[#d8cdbf] px-2 py-1.5 text-xs"
            >
              <option value="manual">manual</option>
              <option value="interval">interval</option>
              <option value="webhook">webhook</option>
            </select>
            {wfTrigger === 'interval' && (
              <input
                type="number"
                min={5}
                value={wfInterval}
                onChange={(event) => setWfInterval(Number(event.target.value) || 60)}
                className="w-24 rounded-lg border border-[#d8cdbf] px-2 py-1.5 text-xs"
                title={ui('间隔分钟')}
              />
            )}
            <select
              value={wfMode}
              onChange={(event) => setWfMode(event.target.value as typeof wfMode)}
              className="rounded-lg border border-[#d8cdbf] px-2 py-1.5 text-xs"
            >
              <option value="dry_run">dry_run</option>
              <option value="automation">automation</option>
            </select>
          </div>
          <textarea
            value={wfMessage}
            onChange={(event) => setWfMessage(event.target.value)}
            placeholder={ui('默认消息 / 任务描述')}
            className="min-h-14 w-full rounded-lg border border-[#d8cdbf] px-2 py-1.5 text-xs"
          />
          <button
            type="button"
            disabled={busy}
            onClick={() => void createWorkflow()}
            className="inline-flex items-center gap-1 rounded-md bg-[#8d5f35] px-2 py-1 text-xs text-white disabled:opacity-50"
          >
            <Plus size={12} />
            {ui('创建工作流')}
          </button>
          {lastWebhookToken && (
            <div className="rounded-lg bg-[#fff5ec] px-2 py-2 text-[11px] text-[#8a5a28]">
              {ui('Webhook token（仅显示一次）：')}
              <code className="mt-1 block break-all font-mono text-[10px]">{lastWebhookToken}</code>
              <span className="mt-1 block">POST /instructions/workflows/hooks/&lt;token&gt;</span>
            </div>
          )}
        </div>
      </section>
    </div>
  );
}
