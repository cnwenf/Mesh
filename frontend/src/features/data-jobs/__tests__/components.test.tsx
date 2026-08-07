/**
 * 导入向导 / 导出对话框 / 数据管理页组件测试(import-export.md §4)。
 * 客户端以 stub 注入(不触网);useWorkspace 经 vi.mock 提供。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import { renderWithProviders } from '../../../test-utils/render';
import { getApiClient, MeshApiError } from '../../../api';
import type { MeshApiClient } from '../../../api';
import { RealtimeContext } from '../../../shell/AppShell';
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

/** 最小可控实时 fake:记录订阅,可手工 emit 帧(与 MembersPage 测试同构)。 */
interface FakeFrame {
  channel: string;
  event: string;
  payload: unknown;
}

function makeFakeRealtime() {
  const handlers: Array<(frame: FakeFrame) => void> = [];
  const client = {
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
    onFrame: vi.fn((handler: (frame: FakeFrame) => void) => {
      handlers.push(handler);
      return (): void => {
        const index = handlers.indexOf(handler);
        if (index >= 0) handlers.splice(index, 1);
      };
    }),
    onState: vi.fn(() => () => undefined),
  };
  return {
    client,
    emit: (frame: FakeFrame): void => {
      for (const handler of [...handlers]) handler(frame);
    },
  };
}

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

  it('shows the export_too_large pre-warning on 413 instead of a toast (§4.4)', async () => {
    const tooLarge = new MeshApiError({
      status: 413,
      code: 'export_too_large',
      message: 'export estimate exceeds the row ceiling',
      details: { estimate: 90000, max_rows: 50000 },
    });
    const client = makeClient({ request: vi.fn().mockRejectedValue(tooLarge) });
    renderWithProviders(
      <ExportDialog open onClose={() => undefined} workspaceId="ws-1" client={client} />,
    );
    fireEvent.click(screen.getByTestId('export-submit-button'));
    const warning = await screen.findByTestId('export-size-warning');
    expect(warning.textContent).toContain('90000');
    expect(warning.textContent).toContain('50000');
    // 预警可消除,回到范围选择继续收窄重试。
    fireEvent.click(screen.getByTestId('export-size-warning-dismiss'));
    await waitFor(() => expect(screen.queryByTestId('export-size-warning')).toBeNull());
    expect(screen.getByTestId('export-scope-select')).toBeTruthy();
  });

  it('keeps the toast path for non-size submission errors', async () => {
    const client = makeClient({
      request: vi
        .fn()
        .mockRejectedValue(
          new MeshApiError({ status: 403, code: 'forbidden', message: 'forbidden' }),
        ),
    });
    renderWithProviders(
      <ExportDialog open onClose={() => undefined} workspaceId="ws-1" client={client} />,
    );
    fireEvent.click(screen.getByTestId('export-submit-button'));
    await waitFor(() => expect(screen.queryByTestId('export-size-warning')).toBeNull());
    expect(screen.getByTestId('export-submit-button')).toBeTruthy();
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

  it('renders an empty creation date when the API omits it', async () => {
    const client = makeClient({
      list: vi.fn().mockResolvedValue({
        data: [makeJob({ created_at: null as unknown as string })],
        next_cursor: null,
      }),
    });
    vi.mocked(getApiClient).mockReturnValue(client);
    renderWithProviders(<DataManagementPage />, { route: '/w/acme/settings/data' });
    const row = await screen.findByTestId('job-row-dj-1');
    expect(row.querySelectorAll('td')[4]?.textContent).toBe('');
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

  it('renders live row-level progress for running jobs (§4.4 text signal)', async () => {
    const client = makeClient({
      list: vi.fn().mockResolvedValue({
        data: [
          makeJob({
            kind: 'import',
            status: 'running',
            total_rows: 1000,
            succeeded_rows: 400,
          }),
        ],
        next_cursor: null,
      }),
    });
    vi.mocked(getApiClient).mockReturnValue(client);
    renderWithProviders(<DataManagementPage />, { route: '/w/acme/settings/data' });
    const progress = await screen.findByTestId('job-progress-dj-1');
    expect(progress.textContent).toContain('400/1000');
  });

  it('subscribes running job channels and merges data_job.updated frames', async () => {
    const client = makeClient({
      list: vi.fn().mockResolvedValue({
        data: [
          makeJob({
            kind: 'import',
            status: 'running',
            total_rows: 1000,
            succeeded_rows: 400,
          }),
          makeJob({ id: 'dj-2', status: 'completed', total_rows: 5, succeeded_rows: 5 }),
        ],
        next_cursor: null,
      }),
    });
    vi.mocked(getApiClient).mockReturnValue(client);
    const realtime = makeFakeRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={realtime as never}>
        <DataManagementPage />
      </RealtimeContext.Provider>,
      { route: '/w/acme/settings/data' },
    );
    await screen.findByTestId('job-progress-dj-1');
    // 只订阅在途作业频道;终态作业不订阅。
    expect(realtime.client.subscribe).toHaveBeenCalledWith('data_job:dj-1');
    expect(realtime.client.subscribe).not.toHaveBeenCalledWith('data_job:dj-2');

    // 帧合并 → 行级进度推进。
    act(() => {
      realtime.emit({
        channel: 'data_job:dj-1',
        event: 'data_job.updated',
        payload: {
          id: 'dj-1',
          status: 'running',
          succeeded_rows: 980,
          updated_at: '2026-07-29T00:00:00Z',
        },
      });
    });
    await waitFor(() =>
      expect(screen.getByTestId('job-progress-dj-1').textContent).toContain('980/1000'),
    );

    // 无关频道/畸形载荷不破坏列表。
    act(() => {
      realtime.emit({ channel: 'other:1', event: 'data_job.updated', payload: { id: 'dj-1' } });
      realtime.emit({ channel: 'data_job:dj-1', event: 'data_job.updated', payload: null });
    });
    expect(screen.getByTestId('job-row-dj-1')).toBeTruthy();
  });
});
