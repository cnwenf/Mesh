/**
 * 数据作业 API 契约层测试(import-export.md §3.1–§3.6):
 * 路径 / 方法 / 包络解包 / 幂等键 / 频道名。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import type { FetchStub } from '../../../api/__tests__/fetchStub';
import {
  createExportJob,
  createImportJob,
  dataJobChannelExportHelper,
  downloadDataJobProduct,
  getDataJob,
  listDataJobs,
  runImportJob,
  validateImportJob,
} from './testImports';
import { dataJobChannel, isTerminalDataJobStatus } from '../types';

let stub: FetchStub;
let client: MeshApiClient;

beforeEach(() => {
  vi.unstubAllGlobals();
  stub = stubFetch(fakeResponse({ body: { data: { id: 'dj-1' } } }));
  vi.stubGlobal('fetch', stub.fetchImpl);
  client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
});

describe('type helpers', () => {
  it('builds the job-scoped channel and detects terminal statuses', () => {
    expect(dataJobChannel('dj-9')).toBe('data_job:dj-9');
    expect(dataJobChannelExportHelper()).toBe('data_job');
    expect(isTerminalDataJobStatus('completed')).toBe(true);
    expect(isTerminalDataJobStatus('completed_with_errors')).toBe(true);
    expect(isTerminalDataJobStatus('failed')).toBe(true);
    expect(isTerminalDataJobStatus('running')).toBe(false);
    expect(isTerminalDataJobStatus('validating')).toBe(false);
    expect(isTerminalDataJobStatus('pending')).toBe(false);
  });
});

describe('endpoint surface', () => {
  it('creates an import job with mapping + idempotency key', async () => {
    await createImportJob(
      client,
      {
        workspace_id: 'ws-1',
        entity_type: 'issues',
        format: 'csv',
        source_attachment_id: 'att-1',
        mapping: {
          columns: [
            { source: 'Title', target: 'title', transform: { type: 'direct' } },
          ],
        },
        target_project_id: 'proj-1',
      },
      'idem-1',
    );
    expect(stub.calls[0].url).toBe('http://api/api/v1/data-jobs/import');
    expect(stub.calls[0].init?.method).toBe('POST');
    const headers = stub.calls[0].init?.headers as Record<string, string>;
    expect(headers['Idempotency-Key']).toBe('idem-1');
    const body = JSON.parse(String(stub.calls[0].init?.body));
    expect(body.source_attachment_id).toBe('att-1');
    expect(body.target_project_id).toBe('proj-1');
    expect(body.mapping.columns).toHaveLength(1);
  });

  it('creates an import job with auto_infer when no mapping given', async () => {
    await createImportJob(client, {
      workspace_id: 'ws-1',
      source_attachment_id: 'att-1',
      auto_infer: true,
    });
    const body = JSON.parse(String(stub.calls[0].init?.body));
    expect(body.auto_infer).toBe(true);
    // entity_type/format omitted → server defaults apply (issues/csv)
    expect(body.entity_type).toBeUndefined();
    expect(body.format).toBeUndefined();
  });

  it('validates (dry-run) via POST import/{id}/validate', async () => {
    await validateImportJob(client, 'dj-2');
    expect(stub.calls[0].url).toBe('http://api/api/v1/data-jobs/import/dj-2/validate');
    expect(stub.calls[0].init?.method).toBe('POST');
  });

  it('runs the import via POST import/{id}/run', async () => {
    await runImportJob(client, 'dj-3');
    expect(stub.calls[0].url).toBe('http://api/api/v1/data-jobs/import/dj-3/run');
  });

  it('creates an export job with scope + filters', async () => {
    await createExportJob(
      client,
      {
        workspace_id: 'ws-1',
        scope: 'project',
        project_id: 'proj-1',
        format: 'json',
        filters: { state_category: ['todo'] },
        locale: 'zh-CN',
      },
      'idem-2',
    );
    expect(stub.calls[0].url).toBe('http://api/api/v1/data-jobs/export');
    const body = JSON.parse(String(stub.calls[0].init?.body));
    expect(body.scope).toBe('project');
    expect(body.filters).toEqual({ state_category: ['todo'] });
    expect(body.locale).toBe('zh-CN');
  });

  it('gets a single job (single envelope)', async () => {
    const job = await getDataJob(client, 'dj-4');
    expect(stub.calls[0].url).toBe('http://api/api/v1/data-jobs/dj-4');
    expect(job.id).toBe('dj-1');
  });

  it('lists jobs with query filters', async () => {
    vi.unstubAllGlobals();
    const listStub = stubFetch(
      fakeResponse({ body: { data: [{ id: 'dj-1' }], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', listStub.fetchImpl);
    const page = await listDataJobs(client, {
      workspace_id: 'ws-1',
      kind: 'export',
      status: 'completed',
      limit: 10,
      cursor: 'cur-1',
    });
    expect(listStub.calls[0].url).toContain('/api/v1/data-jobs?');
    expect(listStub.calls[0].url).toContain('workspace_id=ws-1');
    expect(listStub.calls[0].url).toContain('kind=export');
    expect(listStub.calls[0].url).toContain('status=completed');
    expect(listStub.calls[0].url).toContain('limit=10');
    expect(listStub.calls[0].url).toContain('cursor=cur-1');
    expect(page.data).toHaveLength(1);
    expect(page.next_cursor).toBeNull();
  });

  it('lists jobs without optional filters (undefined skipped)', async () => {
    vi.unstubAllGlobals();
    const listStub = stubFetch(
      fakeResponse({ body: { data: [], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', listStub.fetchImpl);
    await listDataJobs(client, { workspace_id: 'ws-9' });
    expect(listStub.calls[0].url).toContain('workspace_id=ws-9');
    expect(listStub.calls[0].url).not.toContain('kind=');
  });

  it('downloads the product descriptor', async () => {
    vi.unstubAllGlobals();
    const dlStub = stubFetch(
      fakeResponse({
        body: { data: { url: 'https://s/x', file_name: 'e.csv', expires_at: 't' } },
      }),
    );
    vi.stubGlobal('fetch', dlStub.fetchImpl);
    const descriptor = await downloadDataJobProduct(client, 'dj-5');
    expect(dlStub.calls[0].url).toBe('http://api/api/v1/data-jobs/dj-5/download');
    expect(descriptor.file_name).toBe('e.csv');
  });
});
