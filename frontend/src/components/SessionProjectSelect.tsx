import type { GovernanceProject } from './governanceProject';
import { projectLabel } from './governanceProject';

export type SessionProjectSelectProps = {
  projects: GovernanceProject[];
  /** Current session binding (null/undefined = unbound). */
  value: string | null | undefined;
  preferredProjectId?: string | null;
  disabled?: boolean;
  onChangeSessionProject: (projectId: string) => void;
  onChangePreferred?: (projectId: string) => void;
};

/**
 * Compact governance project controls for the session toolbar:
 * bind the active session, and remember preferred project for new sessions.
 */
export function SessionProjectSelect({
  projects,
  value,
  preferredProjectId,
  disabled,
  onChangeSessionProject,
  onChangePreferred,
}: SessionProjectSelectProps) {
  if (!projects.length) {
    return (
      <span className="text-[11px] text-[#8b8176]" data-testid="session-project-empty">
        治理项目未加载
      </span>
    );
  }

  const currentLabel = projectLabel(value, projects) || '未绑定';

  return (
    <div className="flex flex-wrap items-center gap-2" data-testid="session-project-select">
      <label className="inline-flex items-center gap-1 text-[11px] text-[#5f554d]">
        <span className="font-medium">项目</span>
        <select
          aria-label="当前会话治理项目"
          disabled={disabled}
          value={value || ''}
          onChange={(event) => {
            const next = event.target.value;
            if (next) onChangeSessionProject(next);
          }}
          className="max-w-[10rem] rounded border border-[#d8cdbf] bg-white px-1.5 py-0.5 text-[11px] text-[#2f2a24] outline-none disabled:opacity-50"
        >
          {!value && <option value="">未绑定</option>}
          {projects.map((item) => (
            <option key={item.id} value={item.id}>
              {item.name}
            </option>
          ))}
        </select>
      </label>
      {onChangePreferred && (
        <label className="inline-flex items-center gap-1 text-[11px] text-[#5f554d]">
          <span className="text-[#8b8176]">新建默认</span>
          <select
            aria-label="新建会话默认治理项目"
            disabled={disabled}
            value={preferredProjectId || value || projects[0]?.id || ''}
            onChange={(event) => {
              const next = event.target.value;
              if (next) onChangePreferred(next);
            }}
            className="max-w-[9rem] rounded border border-[#d8cdbf] bg-white px-1.5 py-0.5 text-[11px] text-[#2f2a24] outline-none disabled:opacity-50"
          >
            {projects.map((item) => (
              <option key={item.id} value={item.id}>
                {item.name}
              </option>
            ))}
          </select>
        </label>
      )}
      <span className="hidden text-[11px] text-[#8b8176] sm:inline" title={value || undefined}>
        {currentLabel}
      </span>
    </div>
  );
}
