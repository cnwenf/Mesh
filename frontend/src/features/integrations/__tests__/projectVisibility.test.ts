import { afterEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { listAllVisibleProjects } from '../projectVisibility';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe('listAllVisibleProjects', () => {
  it('pages active and archived projects and deduplicates their ids', async () => {
    const urls: string[] = [];
    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const url = String(input);
      urls.push(url);
      const archived = url.includes('archived=true');
      const secondPage = url.includes('cursor=next-page');
      return fakeResponse({
        body: {
          data: archived
            ? [{ id: 'project-archived', archived: true }]
            : secondPage
              ? [{ id: 'project-shared', archived: false }]
              : [
                  { id: 'project-active', archived: false },
                  { id: 'project-shared', archived: false },
                ],
          next_cursor: !archived && !secondPage ? 'next-page' : null,
        },
      });
    }) as typeof fetch);

    const projects = await listAllVisibleProjects(
      new MeshApiClient({ baseUrl: '', getToken: () => null }),
      'ws-1',
    );

    expect(projects.map((project) => project.id)).toEqual([
      'project-active',
      'project-shared',
      'project-archived',
    ]);
    expect(urls).toHaveLength(3);
    expect(urls.some((url) => url.includes('archived=false'))).toBe(true);
    expect(urls.some((url) => url.includes('archived=true'))).toBe(true);
    expect(urls.some((url) => url.includes('cursor=next-page'))).toBe(true);
  });
});
