/**
 * VcsLinksPanel 组件测试(integrations.md §4.2 / §3.3):issue 侧栏关联列表
 * (external_object_ref + 状态 + external_state)+ 手动关联(仅 VCS 集成)+ 解除关联。
 */
import { screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { afterEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { renderWithProviders } from '../../../test-utils/render';
import { VcsLinksPanel } from '../VcsLinksPanel';

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const LINK = {
  id: 'l-1',
  integration_id: 'int-1',
  provider: 'github',
  external_object_type: 'pull_request',
  external_object_ref: 'owner/repo#123',
  mesh_entity_type: 'issue',
  mesh_entity_id: 'issue-1',
  link_source: 'manual',
  status: 'active',
  external_state: { pr_state: 'merged' },
  created_by: 'm-1',
  created_at: '2026-07-01T00:00:00Z',
};

const VCS_INTEGRATION = {
  id: 'int-1',
  workspace_id: 'ws-1',
  kind: 'vcs_github',
  name: 'GitHub',
  status: 'active',
  config: {},
  has_secret: true,
  created_by: 'm-1',
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-01T00:00:00Z',
};
const IM_INTEGRATION = { ...VCS_INTEGRATION, id: 'int-2', kind: 'im_slack', name: 'Slack' };

interface Recorded {
  url: string;
  method: string;
}

function setup(links: unknown[] = [LINK]): Recorded[] {
  const calls: Recorded[] = [];
  const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
    const url = String(input);
    const method = init?.method ?? 'GET';
    calls.push({ url, method });
    if (/\/issues\/issue-1\/vcs-links/.test(url))
      return fakeResponse({ body: { data: links, next_cursor: null } });
    if (url.includes('/integrations') && method === 'GET')
      return fakeResponse({ body: { data: [VCS_INTEGRATION, IM_INTEGRATION], next_cursor: null } });
    if (method === 'POST' && url.endsWith('/integrations/vcs/links'))
      return fakeResponse({ body: { data: LINK } });
    if (method === 'DELETE') return fakeResponse({ status: 204 });
    return fakeResponse({ body: { data: [], next_cursor: null } });
  }) as typeof fetch;
  vi.stubGlobal('fetch', impl);
  return calls;
}

function renderPanel() {
  return renderWithProviders(<VcsLinksPanel workspaceId="ws-1" issueId="issue-1" />);
}

describe('VcsLinksPanel', () => {
  it('renders active links with ref and external state', async () => {
    setup();
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('vcs-link-row-l-1')).toBeInTheDocument());
    expect(screen.getByText('owner/repo#123')).toBeInTheDocument();
    expect(screen.getByText(/pr_state=merged/)).toBeInTheDocument();
  });

  it('creates a link using only vcs integrations', async () => {
    const calls = setup();
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('vcs-link-open')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('vcs-link-open'));
    const options = screen.getByTestId('vcs-integration').querySelectorAll('option');
    // placeholder + only the vcs_github integration (im_slack filtered out)
    expect(options.length).toBe(2);
    await userEvent.selectOptions(screen.getByTestId('vcs-integration'), 'int-1');
    await userEvent.type(screen.getByTestId('vcs-object-id'), 'owner/repo#9');
    await userEvent.click(screen.getByTestId('vcs-link-submit'));
    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith('/integrations/vcs/links') && call.method === 'POST')).toBe(true),
    );
  });

  it('unlinks a link', async () => {
    const calls = setup();
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('vcs-unlink-l-1')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('vcs-unlink-l-1'));
    await waitFor(() =>
      expect(calls.some((call) => call.url.endsWith('/integrations/vcs/links/l-1') && call.method === 'DELETE')).toBe(true),
    );
  });

  it('shows the empty state without links', async () => {
    setup([]);
    renderPanel();
    await waitFor(() => expect(screen.getByText(/No VCS links yet/)).toBeInTheDocument());
  });

  it('closes the dialog via cancel and changes the object type', async () => {
    setup();
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('vcs-link-open')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('vcs-link-open'));
    await waitFor(() => expect(screen.getByTestId('vcs-type')).toBeInTheDocument());
    await userEvent.selectOptions(screen.getByTestId('vcs-type'), 'commit');
    await userEvent.click(screen.getByRole('button', { name: /cancel/i }));
    await waitFor(() => expect(screen.queryByTestId('vcs-type')).toBeNull());
  });

  it('renders a link without external state and surfaces an unlink failure', async () => {
    const noState = { ...LINK, id: 'l-2', external_state: null };
    const calls: Recorded[] = [];
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      calls.push({ url, method });
      if (/\/issues\/issue-1\/vcs-links/.test(url))
        return fakeResponse({ body: { data: [noState], next_cursor: null } });
      if (url.includes('/integrations') && method === 'GET')
        return fakeResponse({ body: { data: [VCS_INTEGRATION], next_cursor: null } });
      if (method === 'DELETE')
        return fakeResponse({ status: 404, body: { error: { code: 'not_found', message: 'gone' } } });
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('vcs-link-row-l-2')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('vcs-unlink-l-2'));
    await waitFor(() => expect(screen.getByText(/could not find that resource/i)).toBeInTheDocument());
  });

  it('falls back to an empty list when loading fails', async () => {
    const impl = (async () =>
      fakeResponse({ status: 500, body: { error: { code: 'internal_error', message: 'boom' } } })) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPanel();
    await waitFor(() => expect(screen.getByText(/No VCS links yet/)).toBeInTheDocument());
  });

  it('renders a gitlab link icon and closes the dialog via the close button', async () => {
    const gitlabLink = { ...LINK, id: 'l-3', provider: 'gitlab', external_object_ref: 'group/proj!7' };
    setup([gitlabLink]);
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('vcs-link-row-l-3')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('vcs-link-open'));
    await waitFor(() => expect(screen.getByTestId('vcs-type')).toBeInTheDocument());
    await userEvent.click(screen.getByRole('button', { name: /close/i }));
    await waitFor(() => expect(screen.queryByTestId('vcs-type')).toBeNull());
  });

  it('surfaces a create failure as a toast', async () => {
    const impl = (async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      const method = init?.method ?? 'GET';
      if (/\/issues\/issue-1\/vcs-links/.test(url))
        return fakeResponse({ body: { data: [], next_cursor: null } });
      if (url.includes('/integrations') && method === 'GET')
        return fakeResponse({ body: { data: [VCS_INTEGRATION], next_cursor: null } });
      if (method === 'POST' && url.endsWith('/integrations/vcs/links'))
        return fakeResponse({ status: 422, body: { error: { code: 'vcs_link_invalid', message: 'bad' } } });
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as typeof fetch;
    vi.stubGlobal('fetch', impl);
    renderPanel();
    await waitFor(() => expect(screen.getByTestId('vcs-link-open')).toBeInTheDocument());
    await userEvent.click(screen.getByTestId('vcs-link-open'));
    await userEvent.selectOptions(screen.getByTestId('vcs-integration'), 'int-1');
    await userEvent.type(screen.getByTestId('vcs-object-id'), 'owner/repo#9');
    await userEvent.click(screen.getByTestId('vcs-link-submit'));
    await waitFor(() => expect(screen.getByText(/invalid or cross-workspace/i)).toBeInTheDocument());
  });
});
