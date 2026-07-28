/**
 * 导入向导 / 导出对话框 / 数据管理页组件测试(import-export.md §4)。
 * 客户端以 stub 注入(不触网);useWorkspace 经 vi.mock 提供。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { fireEvent, screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '../../../test-utils/render';
import { getApiClient } from '../../../api';
import type { MeshApiClient } from '../../../api';
import { DataManagementPage } from '../DataManagementPage';
import { ExportDialog } from '../ExportDialog';
import { ImportWizard } from '../ImportWizard';
import type { DataJob } from '../types';

vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api')>();
  return { ...actual, getApiClient: vi.fn() };
});

vi.mock('../../../workspace/WorkspaceProvider', () => ({
  useWorkspace: () => ({
    workspace: { id: 'ws-1', slug: 'acme', name: 'Acme' },
    isAdmin: true,
    isOwner: true,
    refresh: vi.fn(),
  }),
}));

function makeClient(overrides: Record<string, unknown> = {}): MeshApiClient {
  return {
    request: vi.fn().mockResolvedValue({}),
    list: vi.fn().mockResolvedValue({ data: [], next_cursor: null }),
    ...overrides,
  } as unknown as MeshApiClient;
}

function makeJob(overrides: Partial<DataJob> = {}): DataJob {
  return {
    id: 'dj-1',
    workspace_id: 'ws-1',
    kind: 'export',
    entity_type: 'issues',
    format: 'csv',
    status: 'pending',
    total_rows: 0,
    succeeded_rows: 0,
    failed_rows: 0,
    source_attachment_id: null,
    result_attachment_id: null,
    failure_reason: null,
    requested_by: 'm-1',
    mapping: { columns: [] },
    params: {},
    started_at: null,
    finished_at: null,
    created_at: '2026-07-28T00:00:00Z',
    updated_at: '2026-07-28T00:00:00Z',
    ...overrides,
  };
}

describe('ImportWizard', () => {
  it('renders the five steps and disables Next until upload is ready', () => {
    renderWithProviders(
      <ImportWizard open onClose={() => undefined} workspaceId="ws-1" client={makeClient()} />,
    );
    expect(screen.getByText('Upload')).toBeTruthy();
    expect(screen.getByText('Mapping')).toBeTruthy();
    expect(screen.getByText('Preview')).toBeTruthy();
    expect(screen.getByText('Confirm')).toBeTruthy();
    expect(screen.getByText('Progress')).toBeTruthy();
    const next = screen.getByText('Next');
    expect((next.closest('button') as HTMLButtonElement).disabled).toBe(true);
  });

  it('renders nothing when closed', () => {
    renderWithProviders(
      <ImportWizard
        open={false}
        onClose={() => undefined}
        workspaceId="ws-1"
        client={makeClient()}
      />,
    );
    expect(screen.queryByText('Import data')).toBeNull();
  });
});

describe('ExportDialog', () => {
  beforeEach(() => {
    vi.unstubAllGlobals();
  });

  it('submits an export and shows progress state', async () => {
    const client = makeClient({
      request: vi.fn().mockResolvedValue(makeJob({ status: 'pending' })),
    });
    renderWithProviders(
      <ExportDialog open onClose={() => undefined} workspaceId="ws-1" client={client} />,
    );
    fireEvent.click(screen.getByTestId('export-submit-button'));
    await waitFor(() => {
      expect(screen.getByTestId('export-status')).toBeTruthy();
    });
    expect(client.request).toHaveBeenCalledWith(
      'POST',
      '/api/v1/data-jobs/export',
      expect.objectContaining({ body: expect.objectContaining({ scope: 'workspace' }) }),
    );
  });

  it('disables submit for project scope without a project id', () => {
    renderWithProviders(
      <ExportDialog
        open
        onClose={() => undefined}
        workspaceId="ws-1"
        defaultScope="project"
        client={makeClient()}
      />,
    );
    const submit = screen.getByTestId('export-submit-button') as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
  });

  it('shows the download link once the product descriptor resolves', async () => {
    const client = makeClient({
      request: vi
        .fn()
        .mockResolvedValueOnce(makeJob({ status: 'completed', result_attachment_id: 'a-1' }))
        .mockResolvedValueOnce({
          url: 'https://cdn/x.csv',
          file_name: 'x.csv',
          expires_at: 't',
        }),
    });
    renderWithProviders(
      <ExportDialog open onClose={() => undefined} workspaceId="ws-1" client={client} />,
    );
    fireEvent.click(screen.getByTestId('export-submit-button'));
    await waitFor(() => {
      expect(screen.getByTestId('export-download-link')).toBeTruthy();
    });
    expect((screen.getByTestId('export-download-link') as HTMLAnchorElement).href).toContain(
      'https://cdn/x.csv',
    );
  });
});

describe('DataManagementPage', () => {
  it('renders the jobs table with counts and download action', async () => {
    const client = makeClient({
      list: vi.fn().mockResolvedValue({
        data: [
          makeJob({
            kind: 'import',
            status: 'completed_with_errors',
            total_rows: 100,
            succeeded_rows: 90,
            failed_rows: 10,
            result_attachment_id: 'att-9',
          }),
        ],
        next_cursor: null,
      }),
    });
    vi.mocked(getApiClient).mockReturnValue(client);
    renderWithProviders(<DataManagementPage />, { route: '/w/acme/settings/data' });
    await waitFor(() => {
      expect(screen.getByTestId('job-row-dj-1')).toBeTruthy();
    });
    expect(screen.getByText('Import')).toBeTruthy();
    expect(screen.getByText('Completed with errors')).toBeTruthy();
    expect(screen.getByText('90 ok / 10 failed / 100 total')).toBeTruthy();
    fireEvent.click(screen.getByText('Download'));
  });

  it('renders the empty state when there are no jobs', async () => {
    vi.mocked(getApiClient).mockReturnValue(makeClient());
    renderWithProviders(<DataManagementPage />, { route: '/w/acme/settings/data' });
    await waitFor(() => {
      expect(screen.getByText('No data jobs yet')).toBeTruthy();
    });
  });

  it('renders the error state with retry when listing fails', async () => {
    const client = makeClient({ list: vi.fn().mockRejectedValue(new Error('boom')) });
    vi.mocked(getApiClient).mockReturnValue(client);
    renderWithProviders(<DataManagementPage />, { route: '/w/acme/settings/data' });
    await waitFor(() => {
      expect(screen.getByText('Failed to load data jobs')).toBeTruthy();
    });
    fireEvent.click(screen.getByText('Retry'));
  });

  it('opens the import wizard and export dialog from the entry buttons', async () => {
    vi.mocked(getApiClient).mockReturnValue(makeClient());
    renderWithProviders(<DataManagementPage />, { route: '/w/acme/settings/data' });
    await waitFor(() => {
      expect(screen.getByTestId('open-import-wizard')).toBeTruthy();
    });
    fireEvent.click(screen.getByTestId('open-import-wizard'));
    expect(screen.getAllByText('Import data').length).toBeGreaterThan(0);
  });
});
