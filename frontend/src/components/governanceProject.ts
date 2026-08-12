import { fetchJson } from '../api/client';
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
    return await fetchJson<GovernanceProject[]>(`${API_BASE}/platform/projects`);
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
