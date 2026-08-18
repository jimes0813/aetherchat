import { asArray, fetchJson } from '../api/client';
import { API_BASE } from './shared';

export type GovernanceProject = {
  id: string;
  name: string;
  slug?: string;
  tenant_id?: string;
  description?: string | null;
  status?: string;
};

export type PlatformStatusLite = {
  default_project_id?: string;
};

/** Load governance projects; empty list on failure (UI degrades gracefully). */
export async function loadGovernanceProjects(): Promise<GovernanceProject[]> {
  try {
    const value = await fetchJson<unknown>(`${API_BASE}/platform/projects`);
    return asArray<GovernanceProject>(value);
  } catch {
    return [];
  }
}

export async function loadDefaultGovernanceProjectId(): Promise<string | null> {
  try {
    const status = await fetchJson<PlatformStatusLite>(`${API_BASE}/platform/status`);
    return status.default_project_id || null;
  } catch {
    return null;
  }
}

export async function createGovernanceProject(name: string): Promise<GovernanceProject> {
  const status = await fetchJson<PlatformStatusLite & { default_tenant_id?: string }>(`${API_BASE}/platform/status`);
  return fetchJson<GovernanceProject>(`${API_BASE}/platform/projects`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      name,
      tenant_id: status.default_tenant_id || 'local',
    }),
  });
}

export function projectLabel(
  projectId: string | null | undefined,
  projects: GovernanceProject[],
): string {
  if (!projectId) return '';
  const match = projects.find((item) => item.id === projectId);
  return match?.name || match?.slug || projectId;
}

export function resolvePreferredProjectId(
  preferred: string | null | undefined,
  projects: GovernanceProject[],
  defaultProjectId?: string | null,
): string | undefined {
  if (preferred && projects.some((item) => item.id === preferred)) {
    return preferred;
  }
  if (defaultProjectId && projects.some((item) => item.id === defaultProjectId)) {
    return defaultProjectId;
  }
  return projects[0]?.id;
}
