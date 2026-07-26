/**
 * Issue API 契约层测试(issue.md §3.1):路径 / 查询参数 / 方法 / 包络解包 / 频道名。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse } from '../../../api/__tests__/fetchStub';
import type { FetchStub } from '../../../api/__tests__/fetchStub';
import { stubFetch } from '../../../api/__tests__/fetchStub';
import {
  addDependency,
  bulkIssues,
  createIssue,
  deleteIssue,
  getIssue,
  getIssueByIdentifier,
  issueChannel,
  listActivity,
  listChildren,
  listDependencies,
  listIssues,
  listIssuesGrouped,
  listStatuses,
  moveIssue,
  movePreview,
  removeDependency,
  updateIssue,
  workspaceIssuesChannel,
} from '../api';

let stub: FetchStub;
let client: MeshApiClient;

beforeEach(() => {
  vi.unstubAllGlobals();
  stub = stubFetch(fakeResponse({ body: { data: [], next_cursor: null } }));
  vi.stubGlobal('fetch', stub.fetchImpl);
  client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
});

describe('channel helpers (§3.6)', () => {
  it('builds detail and list channel names', () => {
    expect(issueChannel('iss-1')).toBe('issue:iss-1');
    expect(workspaceIssuesChannel('ws-1')).toBe('workspace:ws-1:issues');
  });
});

describe('endpoint surface', () => {
  it('lists issues with filter/sort/pagination query params', async () => {
    await listIssues(client, 'ws-1', {
      q: 'bug',
      state_category: 'todo',
      priority: 'high',
      sort: 'due_date',
      order: 'asc',
      limit: 10,
      cursor: 'cur',
    });
    const url = stub.calls[0].url;
    expect(url).toContain('/api/v1/workspaces/ws-1/issues');
    expect(url).toContain('q=bug');
    expect(url).toContain('state_category=todo');
    expect(url).toContain('priority=high');
    expect(url).toContain('sort=due_date');
    expect(url).toContain('order=asc');
    expect(url).toContain('cursor=cur');
  });

  it('lists grouped issues with the overall cursor contract', async () => {
    const groupedStub = stubFetch(fakeResponse({ body: { groups: [], next_cursor: null } }));
    vi.stubGlobal('fetch', groupedStub.fetchImpl);
    const page = await listIssuesGrouped(client, 'ws-1', { group_by: 'state_category' });
    expect(groupedStub.calls[0].url).toContain('group_by=state_category');
    expect(page.groups).toEqual([]);
    expect(page.nextCursor).toBeNull();
  });

  it('gets by uuid and by identifier', async () => {
    await getIssue(client, 'iss-1');
    expect(stub.calls[0].url).toBe('http://api/api/v1/issues/iss-1');
    await getIssueByIdentifier(client, 'ws-1', 'WEB-12');
    expect(stub.calls[1].url).toBe('http://api/api/v1/workspaces/ws-1/issues/by-identifier/WEB-12');
  });

  it('creates and deletes', async () => {
    await createIssue(client, 'ws-1', { title: 't' });
    expect(stub.calls[0].init?.method).toBe('POST');
    await deleteIssue(client, 'iss-1');
    expect(stub.calls[1].init?.method).toBe('DELETE');
  });

  it('updates with If-Match header (§6.14)', async () => {
    await updateIssue(client, 'iss-1', { title: 'x' }, '2026-07-01T00:00:00Z');
    const headers = (stub.calls[0].init?.headers ?? {}) as Record<string, string>;
    expect(headers['If-Match']).toBe('2026-07-01T00:00:00Z');
  });

  it('children / activity / dependencies paths', async () => {
    await listChildren(client, 'iss-1');
    await listActivity(client, 'iss-1');
    await listDependencies(client, 'iss-1');
    expect(stub.calls[0].url).toBe('http://api/api/v1/issues/iss-1/children');
    expect(stub.calls[1].url).toBe('http://api/api/v1/issues/iss-1/activity');
    expect(stub.calls[2].url).toBe('http://api/api/v1/issues/iss-1/dependencies');
  });

  it('adds and removes dependencies', async () => {
    await addDependency(client, 'iss-1', { depends_on_id: 'iss-2', type: 'blocked_by' });
    expect(stub.calls[0].init?.method).toBe('POST');
    const body = JSON.parse(String(stub.calls[0].init?.body));
    expect(body).toEqual({ depends_on_id: 'iss-2', type: 'blocked_by' });
    await removeDependency(client, 'iss-1', 'dep-1');
    expect(stub.calls[1].url).toBe('http://api/api/v1/issues/iss-1/dependencies/dep-1');
  });

  it('move preview and confirmed move (§3.8)', async () => {
    await movePreview(client, 'iss-1', 'prj-2');
    expect(stub.calls[0].url).toBe('http://api/api/v1/issues/iss-1/move-preview');
    await moveIssue(client, 'iss-1', { target_project_id: 'prj-2', confirm: true, version: 3 });
    const body = JSON.parse(String(stub.calls[1].init?.body));
    expect(body).toEqual({ target_project_id: 'prj-2', confirm: true, version: 3 });
  });

  it('bulk endpoint posts to /issues/bulk', async () => {
    await bulkIssues(client, { issue_ids: ['a'], changes: { priority: 'high' } });
    expect(stub.calls[0].url).toBe('http://api/api/v1/issues/bulk');
  });

  it('lists statuses with optional project scope', async () => {
    await listStatuses(client, 'ws-1', 'prj-1');
    expect(stub.calls[0].url).toContain('/api/v1/workspaces/ws-1/statuses');
    expect(stub.calls[0].url).toContain('project_id=prj-1');
  });
});
