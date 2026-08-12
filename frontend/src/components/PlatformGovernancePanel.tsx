import { useCallback, useEffect, useState } from 'react';
import { Building2, RefreshCw, Shield } from 'lucide-react';
import { fetchJson } from '../api/client';

export type PlatformStatus = {
  phase: string;
  phase_label: string;
  default_tenant_id?: string;
  default_project_id?: string;
  tenant_count: number;
  project_count?: number;
  principal_count: number;
  policy?: {
    environment?: string;
    enforce_tool_allowlist?: boolean;
    enforce_model_allowlist?: boolean;
    allowed_tools?: string[];
    allowed_models?: string[];
    require_approval_for_high_risk?: boolean;
    dlp_redact_secrets?: boolean;
    audit_enabled?: boolean;
    audit_retention_days?: number;
    audit_max_events?: number;
  };
  projects?: PlatformProject[];
  audit_retention?: {
    days?: number;
    max_events?: number;
  };
  runtime_gate?: {
    enabled?: boolean;
    env?: string;
    tool_path?: boolean;
    model_path?: boolean;
    enforce_tool_allowlist?: boolean;
    enforce_model_allowlist?: boolean;
  };
  capabilities?: Record<string, boolean | string>;
  notes?: string[];
};

export type PlatformProject = {
  id: string;
  name: string;
  slug?: string;
  tenant_id?: string;
  description?: string | null;
  status?: string;
};

export type PlatformPrincipal = {
  id: string;
  display_name: string;
  role: string;
  email?: string | null;
  status?: string;
  project_id?: string | null;
};

export type AuditEvent = {
  id: string;
  ts: string;
  action: string;
  tenant_id?: string | null;
  project_id?: string | null;
  detail?: Record<string, unknown>;
};

export type PlatformGovernancePanelProps = {
  apiBase: string;
};

export function PlatformGovernancePanel({ apiBase }: PlatformGovernancePanelProps) {
  const [status, setStatus] = useState<PlatformStatus | null>(null);
  const [principals, setPrincipals] = useState<PlatformPrincipal[]>([]);
  const [projects, setProjects] = useState<PlatformProject[]>([]);
  const [audit, setAudit] = useState<AuditEvent[]>([]);
  const [message, setMessage] = useState('');
  const [busy, setBusy] = useState(false);
  const [toolName, setToolName] = useState('run_command');
  const [authResult, setAuthResult] = useState('');
  const [newProjectName, setNewProjectName] = useState('');

  const load = useCallback(async () => {
    setBusy(true);
    try {
      const [platformStatus, principalRows, projectRows, auditRows] = await Promise.all([
        fetchJson<PlatformStatus>(`${apiBase}/platform/status`),
        fetchJson<PlatformPrincipal[]>(`${apiBase}/platform/principals`),
        fetchJson<PlatformProject[]>(`${apiBase}/platform/projects`),
        fetchJson<AuditEvent[]>(`${apiBase}/platform/audit?limit=20`),
      ]);
      setStatus(platformStatus);
      setPrincipals(principalRows);
      setProjects(projectRows);
      setAudit(auditRows);
      setMessage(
        `${platformStatus.phase_label} · 租户 ${platformStatus.tenant_count} · 项目 ${platformStatus.project_count ?? projectRows.length} · 主体 ${platformStatus.principal_count}`,
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  }, [apiBase]);

  useEffect(() => {
    void load();
  }, [load]);

  const toggleToolAllowlist = async () => {
    if (!status?.default_tenant_id) return;
    setBusy(true);
    try {
      const current = Boolean(status.policy?.enforce_tool_allowlist);
      await fetchJson(`${apiBase}/platform/tenants/${encodeURIComponent(status.default_tenant_id)}/policy`, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ enforce_tool_allowlist: !current }),
      });
      await load();
      setMessage(`工具白名单强制：${!current ? '开启' : '关闭'}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
      setBusy(false);
    }
  };

  const runAuthorize = async () => {
    setBusy(true);
    try {
      const result = await fetchJson<{ allowed: boolean; message?: string; reason?: string }>(
        `${apiBase}/platform/authorize`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ tool_name: toolName, action: 'invoke_tool' }),
        },
      );
      setAuthResult(
        result.allowed
          ? `允许：${result.message || 'ok'}`
          : `拒绝：${result.message || result.reason || 'denied'}`,
      );
      await load();
    } catch (error) {
      setAuthResult(error instanceof Error ? error.message : String(error));
      setBusy(false);
    }
  };

  const exportAudit = async (format: 'json' | 'csv' = 'json') => {
    setBusy(true);
    try {
      const payload = await fetchJson<{
        format: string;
        count: number;
        exported_at: string;
        events?: AuditEvent[];
        body?: string;
      }>(`${apiBase}/platform/audit/export?limit=500&format=${format}`);
      const stamp = payload.exported_at || 'export';
      if (format === 'csv') {
        const blob = new Blob([payload.body || ''], { type: 'text/csv;charset=utf-8' });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = `aetherchat-audit-${stamp}.csv`;
        anchor.click();
        URL.revokeObjectURL(url);
      } else {
        const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
        const url = URL.createObjectURL(blob);
        const anchor = document.createElement('a');
        anchor.href = url;
        anchor.download = `aetherchat-audit-${stamp}.json`;
        anchor.click();
        URL.revokeObjectURL(url);
      }
      setMessage(`已导出审计 ${payload.count} 条（${format.toUpperCase()}）`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
    } finally {
      setBusy(false);
    }
  };

  const createProject = async () => {
    const name = newProjectName.trim();
    if (!name) return;
    setBusy(true);
    try {
      await fetchJson(`${apiBase}/platform/projects`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          name,
          tenant_id: status?.default_tenant_id || 'local',
        }),
      });
      setNewProjectName('');
      await load();
      setMessage(`已创建项目：${name}`);
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
      setBusy(false);
    }
  };

  const pruneAudit = async () => {
    setBusy(true);
    try {
      const result = await fetchJson<{ removed: number; kept: number; retention_days: number; max_events: number }>(
        `${apiBase}/platform/audit/prune`,
        { method: 'POST' },
      );
      await load();
      setMessage(
        `审计裁剪完成：删除 ${result.removed} · 保留 ${result.kept}（${result.retention_days} 天 / 最多 ${result.max_events} 条）`,
      );
    } catch (error) {
      setMessage(error instanceof Error ? error.message : String(error));
      setBusy(false);
    }
  };

  const policy = status?.policy;
  const gate = status?.runtime_gate;
  const retention = status?.audit_retention;

  return (
    <div className="aether-tab-panel aether-panel-scroll min-h-0 flex-1 overflow-auto p-4">
      <div className="mb-4 flex items-start gap-3">
        <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-lg bg-[#e5d9ca] text-[#684827]">
          <Building2 size={17} />
        </div>
        <div className="min-w-0 flex-1">
          <h2 className="text-sm font-semibold">平台治理 · Phase 3</h2>
          <p className="mt-1 text-xs leading-5 text-[#7a7065]">
            租户 → 项目作用域 / RBAC / 白名单 / 审计导出与保留。默认本机单租户 + 默认项目。
          </p>
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={() => void load()}
          className="inline-flex items-center gap-1 rounded-md border border-[#cfc4b7] px-2 py-1 text-xs text-[#5d4a3a]"
        >
          <RefreshCw size={12} />
          刷新
        </button>
      </div>

      {message && (
        <div className="mb-3 rounded-md bg-[#e9e0d5] px-3 py-2 text-xs text-[#6e5d4b]" aria-live="polite">
          {message}
        </div>
      )}

      <section className="mb-4 rounded-xl border border-[#ded6ca] bg-white p-3">
        <div className="mb-2 flex items-center gap-2 text-xs font-semibold text-[#5d4a3a]">
          <Shield size={13} />
          当前策略（{status?.default_tenant_id || 'local'}）
        </div>
        <div className="mb-2 rounded-md bg-[#f4efe8] px-2 py-1.5 text-[11px] text-[#6e5d4b]">
          运行时门禁：{gate?.enabled ? '已接入工具与模型路径' : '已关闭（AETHERCHAT_PLATFORM_POLICY_GATE）'}
          {gate?.enforce_tool_allowlist ? ' · 工具白名单强制中' : ''}
          {gate?.enforce_model_allowlist ? ' · 模型白名单强制中' : ''}
        </div>
        <div className="grid grid-cols-2 gap-2 text-[11px] text-[#5f554d]">
          <div>环境：{policy?.environment || 'dev'}</div>
          <div>高风险需授权：{policy?.require_approval_for_high_risk ? '是' : '否'}</div>
          <div>工具白名单：{policy?.enforce_tool_allowlist ? '强制' : '关闭'}</div>
          <div>模型白名单：{policy?.enforce_model_allowlist ? '强制' : '关闭'}</div>
          <div>DLP 脱敏：{policy?.dlp_redact_secrets ? '开' : '关'}</div>
          <div>审计：{policy?.audit_enabled ? '开' : '关'}</div>
          <div>审计保留：{retention?.days ?? policy?.audit_retention_days ?? 90} 天</div>
          <div>审计上限：{retention?.max_events ?? policy?.audit_max_events ?? 10000} 条</div>
        </div>
        <div className="mt-2 text-[11px] text-[#8a7f75]">
          允许工具：{(policy?.allowed_tools || []).join('、') || '（空）'}
        </div>
        <div className="mt-2 text-[11px] text-[#8a7f75]">
          默认项目：{status?.default_project_id || 'default'}
        </div>
        <button
          type="button"
          disabled={busy}
          onClick={() => void toggleToolAllowlist()}
          className="mt-3 rounded-md bg-[#2f2a24] px-2 py-1 text-xs text-white disabled:opacity-50"
        >
          {policy?.enforce_tool_allowlist ? '关闭工具白名单强制' : '开启工具白名单强制'}
        </button>
      </section>

      <section className="mb-4 rounded-xl border border-[#ded6ca] bg-white p-3">
        <div className="mb-2 text-xs font-semibold text-[#5d4a3a]">项目（{projects.length}）</div>
        <div className="mb-2 flex flex-wrap gap-2">
          <input
            value={newProjectName}
            onChange={(event) => setNewProjectName(event.target.value)}
            className="min-w-[10rem] flex-1 rounded-lg border border-[#d8cdbf] px-2 py-1.5 text-xs"
            placeholder="新项目名称"
          />
          <button
            type="button"
            disabled={busy || !newProjectName.trim()}
            onClick={() => void createProject()}
            className="rounded-md border border-[#cfc4b7] px-2 py-1 text-xs text-[#5d4a3a] disabled:opacity-50"
          >
            创建项目
          </button>
        </div>
        <div className="space-y-1">
          {projects.map((item) => (
            <div key={item.id} className="rounded border border-[#eee6db] px-2 py-1.5 text-[11px]">
              <span className="font-medium">{item.name}</span>
              <span className="ml-2 text-[#8a7f75]">{item.slug || item.id}</span>
              {item.id === status?.default_project_id && (
                <span className="ml-2 rounded bg-[#e5d9ca] px-1 text-[10px] text-[#684827]">默认</span>
              )}
            </div>
          ))}
          {!projects.length && <div className="text-[11px] text-[#8a7f75]">暂无项目</div>}
        </div>
      </section>

      <section className="mb-4 rounded-xl border border-[#ded6ca] bg-white p-3">
        <div className="mb-2 text-xs font-semibold text-[#5d4a3a]">授权试算（不执行工具）</div>
        <div className="flex flex-wrap gap-2">
          <input
            value={toolName}
            onChange={(event) => setToolName(event.target.value)}
            className="min-w-[12rem] flex-1 rounded-lg border border-[#d8cdbf] px-2 py-1.5 font-mono text-xs"
            placeholder="tool name"
          />
          <button
            type="button"
            disabled={busy}
            onClick={() => void runAuthorize()}
            className="rounded-md border border-[#cfc4b7] px-2 py-1 text-xs text-[#5d4a3a]"
          >
            试算
          </button>
        </div>
        {authResult && <div className="mt-2 text-[11px] text-[#5f554d]">{authResult}</div>}
      </section>

      <section className="mb-4 rounded-xl border border-[#ded6ca] bg-white p-3">
        <div className="mb-2 text-xs font-semibold text-[#5d4a3a]">主体（{principals.length}）</div>
        <div className="space-y-1">
          {principals.map((item) => (
            <div key={item.id} className="rounded border border-[#eee6db] px-2 py-1.5 text-[11px]">
              <span className="font-medium">{item.display_name}</span>
              <span className="ml-2 text-[#8a7f75]">{item.role}</span>
            </div>
          ))}
        </div>
      </section>

      <section className="rounded-xl border border-[#ded6ca] bg-white p-3">
        <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
          <div className="text-xs font-semibold text-[#5d4a3a]">审计（最近）</div>
          <div className="flex flex-wrap gap-1">
            <button
              type="button"
              disabled={busy}
              onClick={() => void exportAudit('json')}
              className="rounded-md border border-[#cfc4b7] px-2 py-1 text-[11px] text-[#5d4a3a] disabled:opacity-50"
            >
              导出 JSON
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void exportAudit('csv')}
              className="rounded-md border border-[#cfc4b7] px-2 py-1 text-[11px] text-[#5d4a3a] disabled:opacity-50"
            >
              导出 CSV
            </button>
            <button
              type="button"
              disabled={busy}
              onClick={() => void pruneAudit()}
              className="rounded-md border border-[#cfc4b7] px-2 py-1 text-[11px] text-[#5d4a3a] disabled:opacity-50"
            >
              按策略裁剪
            </button>
          </div>
        </div>
        <div className="max-h-48 space-y-1 overflow-auto">
          {audit.map((event) => (
            <div key={event.id} className="rounded border border-[#eee6db] px-2 py-1 font-mono text-[10px] text-[#6d6258]">
              {event.ts} · {event.action}
              {event.project_id ? ` · ${event.project_id}` : ''}
            </div>
          ))}
          {!audit.length && <div className="text-[11px] text-[#8a7f75]">暂无审计事件</div>}
        </div>
        {status?.notes && (
          <ul className="mt-3 list-disc space-y-0.5 pl-4 text-[11px] text-[#8a7f75]">
            {status.notes.map((note) => (
              <li key={note}>{note}</li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
