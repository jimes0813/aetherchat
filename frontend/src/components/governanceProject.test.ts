import { afterEach, describe, expect, it, vi } from 'vitest';
import {
  loadGovernanceProjects,
  projectLabel,
  resolvePreferredProjectId,
  type GovernanceProject,
} from './governanceProject';

const projects: GovernanceProject[] = [
  { id: 'default', name: '默认项目', slug: 'default' },
  { id: 'p2', name: '严格项目', slug: 'strict' },
];

describe('governanceProject helpers', () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it('projectLabel prefers name then slug then id', () => {
    expect(projectLabel('p2', projects)).toBe('严格项目');
    expect(projectLabel('missing', projects)).toBe('missing');
    expect(projectLabel(null, projects)).toBe('');
  });

  it('resolvePreferredProjectId uses preferred when valid', () => {
    expect(resolvePreferredProjectId('p2', projects, 'default')).toBe('p2');
  });

  it('resolvePreferredProjectId falls back to default then first', () => {
    expect(resolvePreferredProjectId('gone', projects, 'default')).toBe('default');
    expect(resolvePreferredProjectId(null, [], null)).toBeUndefined();
    expect(resolvePreferredProjectId(null, projects, null)).toBe('default');
  });

  it('degrades a malformed project collection to an empty array', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(new Response('{}', {
      status: 200,
      headers: { 'content-type': 'application/json' },
    })));

    await expect(loadGovernanceProjects()).resolves.toEqual([]);
  });
});
