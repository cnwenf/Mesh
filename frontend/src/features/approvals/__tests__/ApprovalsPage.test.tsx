/**
 * 统一「待我审批」页(README §6.10):pending 列表呈现 + 批准/拒绝决策端点调用。
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import { resetApiClient } from '../../../api/instance';
import { ToastProvider } from '../../../design';
import { I18nProvider } from '../../../i18n';
import { WorkspaceProvider } from '../../../workspace/WorkspaceProvider';
import { ApprovalsPage } from '../ApprovalsPage';

const DETAIL = {
  id: 'ws-1',
  name: 'Acme',
  slug: 'acme',
  logo_url: null,
  timezone: 'UTC',
  settings: { default_locale: 'en' },
  my_role: 'admin',
  created_at: '2026-07-25T00:00:00Z',
  updated_at: '2026-07-25T00:00:00Z',
};

const APPROVAL_1 = {
  id: 'ap-1',
  subject_type: 'tool_call',
  subject_execution_id: null,
  subject_task_id: null,
  status: 'pending',
  action_summary: 'Run destructive migration',
  requested_at: '2026-07-29T00:00:00Z',
  expires_at: '2026-07-30T00:00:00Z',
  decided_at: null,
  decision_comment: null,
  execution_status: null,
};
const APPROVAL_2 = { ...APPROVAL_1, id: 'ap-2', action_summary: 'Publish webhook' };

const posts: string[] = [];

function stubBackend(approvals: ReadonlyArray<unknown>): void {
  posts.length = 0;
  vi.stubGlobal(
    'fetch',
    vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const url = String(input);
      if (url.includes('/workspaces/by-slug/')) {
        return fakeResponse({ body: { data: DETAIL } });
      }
      if (url.includes('/approvals/ap-') && init?.method === 'POST') {
        posts.push(url);
        return fakeResponse({ body: { data: { ...APPROVAL_1, status: 'approved' } } });
      }
      if (url.includes('/approvals')) {
        return fakeResponse({ body: { data: approvals, next_cursor: null } });
      }
      return fakeResponse({ body: { data: [], next_cursor: null } });
    }) as unknown as typeof fetch,
  );
  resetApiClient();
}

function renderPage(): void {
  render(
    <I18nProvider requested={null} systemLocales={[]}>
      <ToastProvider regionLabel="notifications">
        <MemoryRouter initialEntries={['/w/acme/approvals']}>
          <Routes>
            <Route
              path="/w/:workspaceSlug/approvals"
              element={
                <WorkspaceProvider slug="acme">
                  <ApprovalsPage />
                </WorkspaceProvider>
              }
            />
          </Routes>
        </MemoryRouter>
      </ToastProvider>
    </I18nProvider>,
  );
}

beforeEach(() => window.localStorage.clear());
afterEach(() => {
  vi.unstubAllGlobals();
  resetApiClient();
});

describe('ApprovalsPage(统一「待我审批」)', () => {
  it('pending 审批卡片列表(data-testid approvals-list)', async () => {
    stubBackend([APPROVAL_1, APPROVAL_2]);
    renderPage();
    await waitFor(() => expect(screen.getByTestId('approvals-list')).toBeInTheDocument());
    expect(screen.getByTestId('approval-card-ap-1')).toBeInTheDocument();
    expect(screen.getByTestId('approval-summary-ap-2').textContent).toBe('Publish webhook');
  });

  it('批准 → POST /approvals/{id}/approve,条目即时离列', async () => {
    stubBackend([APPROVAL_1, APPROVAL_2]);
    renderPage();
    await waitFor(() => expect(screen.getByTestId('approval-approve-ap-1')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('approval-approve-ap-1'));
    await waitFor(() => expect(posts.some((url) => url.includes('/approvals/ap-1/approve'))).toBe(true));
    await waitFor(() => expect(screen.queryByTestId('approval-card-ap-1')).not.toBeInTheDocument());
    expect(screen.getByTestId('approval-card-ap-2')).toBeInTheDocument();
  });

  it('拒绝 → POST /approvals/{id}/reject', async () => {
    stubBackend([APPROVAL_2]);
    renderPage();
    await waitFor(() => expect(screen.getByTestId('approval-reject-ap-2')).toBeInTheDocument());
    fireEvent.click(screen.getByTestId('approval-reject-ap-2'));
    await waitFor(() => expect(posts.some((url) => url.includes('/approvals/ap-2/reject'))).toBe(true));
  });

  it('空列表呈现空态', async () => {
    stubBackend([]);
    renderPage();
    await waitFor(() => expect(screen.getByTestId('approvals-empty')).toBeInTheDocument());
  });
});
