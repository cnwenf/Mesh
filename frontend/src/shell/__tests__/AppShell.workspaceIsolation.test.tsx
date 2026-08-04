/**
 * Workspace route isolation regressions: changing `/w/:workspaceSlug` must
 * replace the complete workspace subtree. Requests owned by the previous
 * workspace may still settle, but they must never render into the new route.
 */
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { Route, Routes, useNavigate, useParams } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../api/__tests__/fetchStub';
import { MembersPage } from '../../features/members/MembersPage';
import { SquadDetailPage } from '../../features/squads/SquadDetailPage';
import { useAuthStore } from '../../state/authStore';
import { renderWithProviders } from '../../test-utils/render';
import { useWorkspace } from '../../workspace/WorkspaceProvider';
import { resetPaletteContextCache } from '../../shortcuts/usePaletteContext';
import { AppShell } from '../AppShell';

const WORKSPACE_ALPHA = {
  id: 'ws-alpha',
  name: 'Alpha',
  slug: 'alpha',
  logo_url: null,
  timezone: 'UTC',
  settings: {},
  my_role: 'owner',
  created_at: '2026-08-01T00:00:00Z',
  updated_at: '2026-08-01T00:00:00Z',
};

const WORKSPACE_BETA = {
  ...WORKSPACE_ALPHA,
  id: 'ws-beta',
  name: 'Beta',
  slug: 'beta',
};

const ME = {
  user: { id: 'user-1', email: 'owner@example.com', display_name: 'Owner' },
  memberships: [
    {
      workspace_id: 'ws-alpha',
      workspace_name: 'Alpha',
      workspace_slug: 'alpha',
      role: 'owner',
      status: 'active',
      joined_at: null,
    },
    {
      workspace_id: 'ws-beta',
      workspace_name: 'Beta',
      workspace_slug: 'beta',
      role: 'owner',
      status: 'active',
      joined_at: null,
    },
  ],
};

function memberFixture(id: string, displayName: string) {
  return {
    id,
    member_type: 'human',
    role: 'member',
    status: 'active',
    display_name: displayName,
    joined_at: null,
    profile: {
      id: `${id}-user`,
      email: `${id}@example.com`,
      display_name: displayName,
      avatar_url: null,
    },
  };
}

function squadFixture(workspaceId: string, name: string) {
  return {
    id: 'shared-squad',
    workspace_id: workspaceId,
    name,
    description: null,
    instructions: null,
    avatar_url: null,
    kind: 'standing',
    status: 'active',
    leader_mode: 'single',
    primary_leader_id: null,
    primary_leader: null,
    require_plan_approval: false,
    max_decompose_depth: 2,
    member_count: 0,
    active_task_count: 0,
    leaders: [],
    member_preview: [],
    archived_at: null,
    created_at: '2026-08-01T00:00:00Z',
    updated_at: '2026-08-01T00:00:00Z',
  };
}

interface DeferredResponse {
  readonly promise: Promise<Response>;
  readonly resolve: (response: Response) => void;
}

function deferredResponse(): DeferredResponse {
  let resolve!: (response: Response) => void;
  const promise = new Promise<Response>((next) => {
    resolve = next;
  });
  return { promise, resolve };
}

function WorkspaceSnapshot(): React.JSX.Element {
  const context = useWorkspace();
  return (
    <span data-testid="workspace-snapshot">
      {context.status}:{context.workspace?.slug ?? 'none'}
    </span>
  );
}

function SwitchWorkspace(props: { readonly suffix: string }): React.JSX.Element {
  const navigate = useNavigate();
  const { workspaceSlug } = useParams<{ workspaceSlug: string }>();
  const target = workspaceSlug === 'alpha' ? 'beta' : 'alpha';
  return (
    <>
      <WorkspaceSnapshot />
      <button
        type="button"
        data-testid="switch-workspace"
        onClick={() => navigate(`/w/${target}/${props.suffix}`)}
      >
        switch
      </button>
    </>
  );
}

function MembersRoute(): React.JSX.Element {
  return (
    <>
      <SwitchWorkspace suffix="members" />
      <MembersPage />
    </>
  );
}

function SquadRoute(): React.JSX.Element {
  return (
    <>
      <SwitchWorkspace suffix="squads/shared-squad" />
      <SquadDetailPage />
    </>
  );
}

function renderShell(route: string): ReturnType<typeof renderWithProviders> {
  return renderWithProviders(
    <Routes>
      <Route path="/w/:workspaceSlug/*" element={<AppShell />}>
        <Route path="members" element={<MembersRoute />} />
        <Route path="squads/:squadId" element={<SquadRoute />} />
      </Route>
    </Routes>,
    { route },
  );
}

beforeEach(() => {
  useAuthStore.getState().setToken(null);
  resetPaletteContextCache();
});

afterEach(() => {
  vi.unstubAllGlobals();
  useAuthStore.getState().setToken(null);
  resetPaletteContextCache();
});

describe('AppShell workspace subtree isolation', () => {
  it('unmounts the Alpha Members state and rejects its late roster response after switching to Beta', async () => {
    const betaWorkspace = deferredResponse();
    const lateAlphaRoster = deferredResponse();
    const calls: string[] = [];
    let alphaRosterCalls = 0;

    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const path = new URL(String(input), 'http://mesh.test').pathname;
      calls.push(path);
      if (path === '/api/v1/workspaces/by-slug/alpha') {
        return fakeResponse({ body: { data: WORKSPACE_ALPHA } });
      }
      if (path === '/api/v1/workspaces/by-slug/beta') return betaWorkspace.promise;
      if (path === '/api/v1/users/me') return fakeResponse({ body: { data: ME } });
      if (path === '/api/v1/workspaces/ws-alpha/members') {
        alphaRosterCalls += 1;
        if (alphaRosterCalls === 1) {
          return fakeResponse({
            body: { data: [memberFixture('member-alpha', 'Alpha Member')], next_cursor: null },
          });
        }
        return lateAlphaRoster.promise;
      }
      if (path === '/api/v1/workspaces/ws-beta/members') {
        return fakeResponse({
          body: { data: [memberFixture('member-beta', 'Beta Member')], next_cursor: null },
        });
      }
      throw new Error(`Unexpected GET ${path}`);
    }) as typeof fetch);

    renderShell('/w/alpha/members');
    expect(await screen.findByTestId('member-row-member-alpha')).toBeInTheDocument();
    expect(screen.getByTestId('workspace-snapshot')).toHaveTextContent('ready:alpha');

    fireEvent.click(screen.getByTestId('tab-human'));
    await waitFor(() => expect(alphaRosterCalls).toBe(2));
    fireEvent.click(screen.getByTestId('switch-workspace'));

    expect(screen.queryByTestId('member-row-member-alpha')).toBeNull();
    expect(screen.getByTestId('workspace-snapshot')).toHaveTextContent('loading:none');

    await act(async () => {
      betaWorkspace.resolve(fakeResponse({ body: { data: WORKSPACE_BETA } }));
    });
    expect(await screen.findByTestId('member-row-member-beta')).toBeInTheDocument();

    await act(async () => {
      lateAlphaRoster.resolve(
        fakeResponse({
          body: {
            data: [memberFixture('member-alpha-late', 'Late Alpha Member')],
            next_cursor: null,
          },
        }),
      );
    });

    expect(screen.getByTestId('member-row-member-beta')).toBeInTheDocument();
    expect(screen.queryByTestId('member-row-member-alpha-late')).toBeNull();
    expect(calls).toContain('/api/v1/workspaces/ws-beta/members');
  });

  it('keeps a late Alpha squad detail response out of the Beta detail route', async () => {
    const betaWorkspace = deferredResponse();
    const lateAlphaSquad = deferredResponse();
    const calls: string[] = [];
    const alphaSquadPath = '/api/v1/workspaces/ws-alpha/squads/shared-squad';
    const betaSquadPath = '/api/v1/workspaces/ws-beta/squads/shared-squad';

    vi.stubGlobal('fetch', (async (input: RequestInfo | URL) => {
      const path = new URL(String(input), 'http://mesh.test').pathname;
      calls.push(path);
      if (path === '/api/v1/workspaces/by-slug/alpha') {
        return fakeResponse({ body: { data: WORKSPACE_ALPHA } });
      }
      if (path === '/api/v1/workspaces/by-slug/beta') return betaWorkspace.promise;
      if (path === '/api/v1/users/me') return fakeResponse({ body: { data: ME } });
      if (path === alphaSquadPath) return lateAlphaSquad.promise;
      if (path === betaSquadPath) {
        return fakeResponse({ body: { data: squadFixture('ws-beta', 'Beta Squad') } });
      }
      if (path.startsWith(`${alphaSquadPath}/`) || path.startsWith(`${betaSquadPath}/`)) {
        return fakeResponse({ body: { data: [], next_cursor: null } });
      }
      throw new Error(`Unexpected GET ${path}`);
    }) as typeof fetch);

    renderShell('/w/alpha/squads/shared-squad');
    await waitFor(() => expect(calls).toContain(alphaSquadPath));
    expect(screen.getByTestId('workspace-snapshot')).toHaveTextContent('ready:alpha');

    fireEvent.click(screen.getByTestId('switch-workspace'));
    expect(screen.getByTestId('workspace-snapshot')).toHaveTextContent('loading:none');

    await act(async () => {
      betaWorkspace.resolve(fakeResponse({ body: { data: WORKSPACE_BETA } }));
    });
    expect(await screen.findByText('Beta Squad')).toBeInTheDocument();

    await act(async () => {
      lateAlphaSquad.resolve(
        fakeResponse({ body: { data: squadFixture('ws-alpha', 'Late Alpha Squad') } }),
      );
    });
    await waitFor(() =>
      expect(calls.filter((path) => path.startsWith(`${alphaSquadPath}/`))).toHaveLength(4),
    );

    expect(screen.getByText('Beta Squad')).toBeInTheDocument();
    expect(screen.queryByText('Late Alpha Squad')).toBeNull();
  });
});
