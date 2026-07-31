/**
 * 导入向导 / 导出对话框 / 数据管理页的交互分支覆盖(import-export.md §4)。
 *
 * 与 components.test.tsx 互补:此处驱动向导全流(upload→mapping→validate→confirm→
 * progress)、映射编辑/删列、实时帧合并、失败终态与错误报告链接,以及导出的范围/格式
 * 切换、无实时通道轮询、实时帧完成、提交/下载失败等分支,把 data-jobs 组件变更行的
 * 覆盖率抬到门禁线(verify-coverage.mjs --base origin/main ≥90%)。
 *
 * 客户端以可控 stub 注入(不触网);useAttachmentUploader 经 vi.mock 注入「已就绪」
 * 上传项以跳过真实签名直传;useRealtimeContext 经 RealtimeContext.Provider 注入假
 * client 以覆盖订阅/帧合并分支,缺省(无 provider)为 null 走轮询分支。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { act, fireEvent, screen, waitFor } from '@testing-library/react';
import type { Mock } from 'vitest';

import { getApiClient, MeshApiError } from '../../../api';
import type { MeshApiClient } from '../../../api';
import { RealtimeContext } from '../../../shell/AppShell';
import type { RealtimeContextValue } from '../../../shell/AppShell';
import { renderWithProviders } from '../../../test-utils/render';
import { DataManagementPage } from '../DataManagementPage';
import { ExportDialog } from '../ExportDialog';
import { ImportWizard } from '../ImportWizard';
import type { DataJob } from '../types';

vi.mock('../../../api', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../api')>();
  return { ...actual, getApiClient: vi.fn() };
});

// vi.mock 工厂只能引用 mock 前缀的外层变量(vitest 提升规则)。
let mockUploads: ReadonlyArray<Record<string, unknown>> = [];
const mockAddFiles = vi.fn();
vi.mock('../../attachments/useAttachmentUploader', () => ({
  useAttachmentUploader: () => ({ uploads: mockUploads, addFiles: mockAddFiles }),
}));

vi.mock('../../../workspace/WorkspaceProvider', () => ({
  useWorkspace: () => ({
    workspace: { id: 'ws-1', slug: 'acme', name: 'Acme' },
    isAdmin: true,
    isOwner: true,
    refresh: vi.fn(),
  }),
}));

const READY_UPLOAD = {
  localId: 'u1',
  phase: 'ready',
  fileName: 'issues.csv',
  attachmentId: 'att-1',
  progress: 1,
};

/** dataJobs.export.runningHint 的渲染值(en)。 */
const RUNNING_HINT =
  "The export runs in the background; you can close this dialog — you'll be notified when it finishes.";

function makeJob(overrides: Partial<DataJob> = {}): DataJob {
  return {
    id: 'dj-1',
    workspace_id: 'ws-1',
    kind: 'import',
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

/** 按 "METHOD path" 精确路由的 request stub,未命中则抛错(暴露测试缺路由)。 */
function routedRequest(routes: Record<string, unknown>): Mock {
  return vi.fn(async (method: string, path: string) => {
    const key = `${method} ${path}`;
    if (Object.prototype.hasOwnProperty.call(routes, key)) {
      const value = routes[key];
      if (value instanceof Error) throw value;
      return typeof value === 'function' ? (value as () => unknown)() : value;
    }
    throw new Error(`unrouted request ${key}`);
  });
}

function makeClient(request: Mock): MeshApiClient {
  return {
    request,
    list: vi.fn().mockResolvedValue({ data: [], next_cursor: null }),
  } as unknown as MeshApiClient;
}

/** 假实时 client:onFrame 记录回调,供测试主动推帧触发合并分支。 */
function makeRealtime(): { value: RealtimeContextValue; client: Record<string, Mock> } {
  const client: Record<string, Mock> = {
    subscribe: vi.fn(),
    unsubscribe: vi.fn(),
    onFrame: vi.fn(() => vi.fn()),
  };
  return { value: { state: 'connected', client: client as never }, client };
}

function lastFrameHandler(client: Record<string, Mock>): (frame: unknown) => void {
  const calls = client.onFrame.mock.calls;
  return calls[calls.length - 1][0] as (frame: unknown) => void;
}

function pushFrame(client: Record<string, Mock>, frame: unknown): void {
  act(() => {
    lastFrameHandler(client)(frame);
  });
}

beforeEach(() => {
  mockUploads = [];
  mockAddFiles.mockClear();
});

describe('ImportWizard full flow', () => {
  it('walks upload → mapping(edit/remove) → validate → confirm → progress', async () => {
    mockUploads = [READY_UPLOAD];
    const created = makeJob({
      status: 'pending',
      total_rows: 3,
      mapping: {
        columns: [
          { source: 'Title', target: 'title', transform: { type: 'direct' } },
          { source: 'State', target: 'status', transform: { type: 'status_by_name' } },
        ],
      },
    });
    const validated = makeJob({
      status: 'pending',
      total_rows: 3,
      params: { predicted_failed_rows: 1 },
      error_report: [
        { row: 3, field: 'title', code: 'required_field_missing', message: 'title missing' },
      ],
    });
    const running = makeJob({ status: 'running', total_rows: 3, succeeded_rows: 2, failed_rows: 1 });
    const request = routedRequest({
      'POST /api/v1/data-jobs/import': created,
      'POST /api/v1/data-jobs/import/dj-1/validate': validated,
      'POST /api/v1/data-jobs/import/dj-1/run': running,
    });
    renderWithProviders(
      <ImportWizard open onClose={vi.fn()} workspaceId="ws-1" client={makeClient(request)} />,
    );

    // upload 步:有就绪上传 → Next 可用,点击进入 mapping。
    expect((screen.getByRole('button', { name: 'Next' }) as HTMLButtonElement).disabled).toBe(false);
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));

    // mapping 步:改首列 transform + 删除一列。
    await waitFor(() => expect(screen.getByText('Title')).toBeTruthy());
    const selects = screen.getAllByRole('combobox');
    fireEvent.change(selects[0], { target: { value: 'value_map' } });
    fireEvent.click(screen.getAllByRole('button', { name: 'Remove' })[0]);

    // validate 步:摘要 + 错误行表。
    fireEvent.click(screen.getByRole('button', { name: 'Validate (dry run)' }));
    await waitFor(() => expect(screen.getByTestId('validate-summary')).toBeTruthy());
    expect(screen.getByText(/3 rows total: 2 importable, 1 will be skipped/)).toBeTruthy();
    expect(screen.getByText('required_field_missing')).toBeTruthy();

    // confirm 步:确认摘要。
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    expect(screen.getByText(/Import 2 rows; 1 rows will be skipped/)).toBeTruthy();

    // progress 步:进度条 + 计数 + 状态。
    fireEvent.click(screen.getByTestId('confirm-import-button'));
    await waitFor(() => expect(screen.getByTestId('progress-status')).toBeTruthy());
    expect(screen.getByTestId('progress-count')).toBeTruthy();
    expect(screen.getByText('Succeeded 2 / failed 1 / total 3')).toBeTruthy();
    expect(screen.getByRole('progressbar')).toBeTruthy();
    expect(request).toHaveBeenCalledWith(
      'POST',
      '/api/v1/data-jobs/import/dj-1/run',
      expect.anything(),
    );
  });



  it('surfaces a toast and stays on upload when create fails (typed + untyped error)', async () => {
    mockUploads = [READY_UPLOAD];
    const request = routedRequest({});
    request.mockRejectedValueOnce(
      new MeshApiError({ status: 422, code: 'source_not_ready', message: 'nope' }),
    );
    renderWithProviders(
      <ImportWizard open onClose={vi.fn()} workspaceId="ws-1" client={makeClient(request)} />,
    );
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId('validate-summary')).toBeNull();
    // busy 期间按钮 isLoading 禁用;待其恢复可点再触发下一次(防满载机竞态)。
    await waitFor(() => expect(screen.getByRole('button', { name: 'Next' })).toBeEnabled());

    request.mockRejectedValueOnce(new Error('network down'));
    // 竞态修复:首次 create 失败的 catch/finally(setBusy(false)) 在 reject 续体里
    // 异步刷新;覆盖率插桩下第二次同步 click 可能落在按钮仍 busy 的瞬间被吞掉,
    // 使 request 停留 1 次。等按钮重新可用(失败态已落定)再点击,与 i18n 文案无关。
    await waitFor(() =>
      expect((screen.getByRole('button', { name: 'Next' }) as HTMLButtonElement).disabled).toBe(false),
    );
    fireEvent.click(screen.getByRole('button', { name: 'Next' }));
    await waitFor(() => expect(request).toHaveBeenCalledTimes(2));
    expect(screen.queryByTestId('validate-summary')).toBeNull();
  });
});

describe('ExportDialog branches', () => {
  it('changes scope/format and downloads a completed export (project scope + filters)', async () => {
    const completed = makeJob({ kind: 'export', status: 'completed', result_attachment_id: 'a-1' });
    const request = routedRequest({
      'POST /api/v1/data-jobs/export': completed,
      'GET /api/v1/data-jobs/dj-1/download': {
        url: 'https://cdn/out.csv',
        file_name: 'out.csv',
        expires_at: 't',
      },
    });
    renderWithProviders(
      <ExportDialog
        open
        onClose={vi.fn()}
        workspaceId="ws-1"
        defaultScope="project"
        projectId="p-1"
        filters={{ q: 'x' }}
        client={makeClient(request)}
      />,
    );
    fireEvent.change(screen.getByTestId('export-scope-select'), { target: { value: 'workspace' } });
    fireEvent.change(screen.getByTestId('export-scope-select'), { target: { value: 'project' } });
    fireEvent.change(screen.getByTestId('export-format-select'), { target: { value: 'json' } });
    fireEvent.click(screen.getByTestId('export-submit-button'));
    await waitFor(() => expect(screen.getByTestId('export-download-link')).toBeTruthy());
    expect((screen.getByTestId('export-download-link') as HTMLAnchorElement).href).toContain(
      'https://cdn/out.csv',
    );
    expect(request).toHaveBeenCalledWith(
      'POST',
      '/api/v1/data-jobs/export',
      expect.objectContaining({
        body: expect.objectContaining({ scope: 'project', project_id: 'p-1', filters: { q: 'x' } }),
      }),
    );
  });

  it('polls via REST when no realtime channel, then resolves the download', async () => {
    const running = makeJob({ kind: 'export', status: 'running' });
    const completed = makeJob({ kind: 'export', status: 'completed', result_attachment_id: 'a-2' });
    // 首轮轮询仍返回 running,保证「后台运行提示」稳定可见一个轮询周期(避免收敛竞态)。
    let pollCount = 0;
    const request = routedRequest({
      'POST /api/v1/data-jobs/export': running,
      'GET /api/v1/data-jobs/dj-1': () => {
        pollCount += 1;
        return pollCount === 1 ? running : completed;
      },
      'GET /api/v1/data-jobs/dj-1/download': {
        url: 'https://cdn/p.csv',
        file_name: 'p.csv',
        expires_at: 't',
      },
    });
    renderWithProviders(
      <ExportDialog open onClose={vi.fn()} workspaceId="ws-1" client={makeClient(request)} />,
    );
    fireEvent.click(screen.getByTestId('export-submit-button'));
    // 非终态时显示后台运行提示。
    await waitFor(() => expect(screen.getByText(RUNNING_HINT)).toBeTruthy());
    // 轮询收敛到 completed 并取到签名下载链接。
    await waitFor(() => expect(screen.getByTestId('export-download-link')).toBeTruthy(), {
      timeout: 4000,
    });
    expect(request).toHaveBeenCalledWith('GET', '/api/v1/data-jobs/dj-1', expect.anything());
  });

  it('merges a realtime completion frame and downloads', async () => {
    const running = makeJob({ kind: 'export', status: 'running' });
    const request = routedRequest({
      'POST /api/v1/data-jobs/export': running,
      'GET /api/v1/data-jobs/dj-1/download': {
        url: 'https://cdn/r.csv',
        file_name: 'r.csv',
        expires_at: 't',
      },
    });
    const { value: rtValue, client: rtClient } = makeRealtime();
    renderWithProviders(
      <RealtimeContext.Provider value={rtValue}>
        <ExportDialog open onClose={vi.fn()} workspaceId="ws-1" client={makeClient(request)} />
      </RealtimeContext.Provider>,
    );
    fireEvent.click(screen.getByTestId('export-submit-button'));
    await waitFor(() => expect(rtClient.subscribe).toHaveBeenCalled());
    pushFrame(rtClient, {
      op: 'event',
      channel: 'data_job:dj-1',
      seq: 3,
      event: 'data_job.updated',
      payload: {
        id: 'dj-1',
        status: 'completed',
        result_attachment_id: 'a-3',
        updated_at: '2099-01-01T00:00:00Z',
      },
    });
    await waitFor(() => expect(screen.getByTestId('export-download-link')).toBeTruthy());
  });

  it('shows failure reason + toast on a failed export', async () => {
    const failed = makeJob({ kind: 'export', status: 'failed', failure_reason: 'export_too_large' });
    const request = routedRequest({ 'POST /api/v1/data-jobs/export': failed });
    renderWithProviders(
      <ExportDialog open onClose={vi.fn()} workspaceId="ws-1" client={makeClient(request)} />,
    );
    fireEvent.click(screen.getByTestId('export-submit-button'));
    await waitFor(() => expect(screen.getByText('Failure reason: export_too_large')).toBeTruthy());
  });

  it('keeps the configure form when submit fails', async () => {
    const request = routedRequest({
      'POST /api/v1/data-jobs/export': new MeshApiError({
        status: 413,
        code: 'export_too_large',
        message: 'too big',
      }),
    });
    renderWithProviders(
      <ExportDialog open onClose={vi.fn()} workspaceId="ws-1" client={makeClient(request)} />,
    );
    fireEvent.click(screen.getByTestId('export-submit-button'));
    await waitFor(() => expect(request).toHaveBeenCalledTimes(1));
    expect(screen.queryByTestId('export-status')).toBeNull();
    expect(screen.getByTestId('export-submit-button')).toBeTruthy();
  });

  it('does not show a download link when the product descriptor fails', async () => {
    const completed = makeJob({ kind: 'export', status: 'completed', result_attachment_id: 'a-4' });
    const request = routedRequest({
      'POST /api/v1/data-jobs/export': completed,
      'GET /api/v1/data-jobs/dj-1/download': new MeshApiError({
        status: 500,
        code: 'storage_error',
        message: 'boom',
      }),
    });
    renderWithProviders(
      <ExportDialog open onClose={vi.fn()} workspaceId="ws-1" client={makeClient(request)} />,
    );
    fireEvent.click(screen.getByTestId('export-submit-button'));
    await waitFor(() =>
      expect(request).toHaveBeenCalledWith(
        'GET',
        '/api/v1/data-jobs/dj-1/download',
        expect.anything(),
      ),
    );
    // 给 catch 分支一个微任务窗口,确认未渲染下载链接。
    await new Promise((resolve) => setTimeout(resolve, 20));
    expect(screen.queryByTestId('export-download-link')).toBeNull();
  });
});

describe('DataManagementPage extra branches', () => {
  it('renders the export-row count branch', async () => {
    const client = makeClient(routedRequest({}));
    client.list = vi.fn().mockResolvedValue({
      data: [makeJob({ kind: 'export', status: 'completed', total_rows: 42 })],
      next_cursor: null,
    });
    vi.mocked(getApiClient).mockReturnValue(client);
    renderWithProviders(<DataManagementPage />, { route: '/w/acme/settings/data' });
    await waitFor(() => expect(screen.getByTestId('job-row-dj-1')).toBeTruthy());
    expect(screen.getByText('Export')).toBeTruthy();
  });

  it('reloads the list when the import wizard / export dialog close', async () => {
    const client = makeClient(routedRequest({}));
    client.list = vi.fn().mockResolvedValue({ data: [], next_cursor: null });
    vi.mocked(getApiClient).mockReturnValue(client);
    renderWithProviders(<DataManagementPage />, { route: '/w/acme/settings/data' });

    await waitFor(() => expect(screen.getByTestId('open-import-wizard')).toBeTruthy());
    fireEvent.click(screen.getByTestId('open-import-wizard'));
    await waitFor(() => expect(screen.getByTestId('import-file-input')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByTestId('import-file-input')).toBeNull());

    fireEvent.click(screen.getByTestId('open-export-dialog'));
    await waitFor(() => expect(screen.getByTestId('export-scope-select')).toBeTruthy());
    fireEvent.click(screen.getByRole('button', { name: 'Close' }));
    await waitFor(() => expect(screen.queryByTestId('export-scope-select')).toBeNull());
    // 初始加载 1 + 向导关闭 1 + 对话框关闭 1。
    expect((client.list as Mock).mock.calls.length).toBeGreaterThanOrEqual(3);
  });
});
