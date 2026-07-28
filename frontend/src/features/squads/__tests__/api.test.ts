/**
 * Squad API 契约层测试(squad.md §3):路径 / 查询参数 / 方法 / 请求体 / 包络解包 / 频道名。
 */
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { MeshApiClient } from '../../../api';
import { fakeResponse, stubFetch } from '../../../api/__tests__/fetchStub';
import type { FetchStub } from '../../../api/__tests__/fetchStub';
import {
  addMembers,
  approvePlan,
  archiveSquad,
  assignTask,
  cancelTask,
  changeRole,
  createSquad,
  createSubtasks,
  dispatchTask,
  getSquad,
  getTask,
  getTaskStatus,
  getTaskTree,
  getIssueAssignment,
  listActivity,
  listMembers,
  listMessages,
  listSquads,
  listTasks,
  moveTaskStatus,
  removeMember,
  rejectPlan,
  restoreSquad,
  sendMessage,
  squadChannel,
  taskStreamUrl,
  updateSquad,
} from '../api';

let stub: FetchStub;
let client: MeshApiClient;

beforeEach(() => {
  vi.unstubAllGlobals();
  stub = stubFetch(fakeResponse({ body: { data: [], next_cursor: null } }));
  vi.stubGlobal('fetch', stub.fetchImpl);
  client = new MeshApiClient({ baseUrl: 'http://api', getToken: () => null });
});

function lastBody(): Record<string, unknown> {
  const call = stub.calls[stub.calls.length - 1];
  return JSON.parse(String(call.init?.body)) as Record<string, unknown>;
}

describe('channel helper (§3.5)', () => {
  it('builds the squad channel name', () => {
    expect(squadChannel('sq-1')).toBe('squad:sq-1');
  });
});

describe('squad CRUD (§3.1)', () => {
  it('lists squads with status/kind/q/pagination query params', async () => {
    await listSquads(client, 'ws-1', {
      status: 'active',
      kind: 'standing',
      q: 'platform',
      limit: 10,
      cursor: 'cur',
    });
    const url = stub.calls[0].url;
    expect(url).toContain('/api/v1/workspaces/ws-1/squads');
    expect(url).toContain('status=active');
    expect(url).toContain('kind=standing');
    expect(url).toContain('q=platform');
    expect(url).toContain('limit=10');
    expect(url).toContain('cursor=cur');
  });

  it('unwraps the squad list envelope', async () => {
    const listStub = stubFetch(
      fakeResponse({ body: { data: [{ id: 'sq-1' }], next_cursor: 'next' } }),
    );
    vi.stubGlobal('fetch', listStub.fetchImpl);
    const page = await listSquads(client, 'ws-1');
    expect(page.data).toEqual([{ id: 'sq-1' }]);
    expect(page.nextCursor).toBe('next');
  });

  it('gets a single squad', async () => {
    await getSquad(client, 'ws-1', 'sq-1');
    expect(stub.calls[0].url).toBe('http://api/api/v1/workspaces/ws-1/squads/sq-1');
  });

  it('creates a squad via POST with body', async () => {
    await createSquad(client, 'ws-1', {
      name: 'Platform',
      description: 'Owns the platform',
      kind: 'adhoc',
      require_plan_approval: true,
      max_decompose_depth: 3,
      members: [{ member_id: 'mem-1', role: 'leader' }],
    });
    expect(stub.calls[0].init?.method).toBe('POST');
    expect(stub.calls[0].url).toBe('http://api/api/v1/workspaces/ws-1/squads');
    expect(lastBody()).toEqual({
      name: 'Platform',
      description: 'Owns the platform',
      kind: 'adhoc',
      require_plan_approval: true,
      max_decompose_depth: 3,
      members: [{ member_id: 'mem-1', role: 'leader' }],
    });
  });

  it('updates a squad via PATCH', async () => {
    await updateSquad(client, 'ws-1', 'sq-1', { name: 'Renamed' });
    expect(stub.calls[0].init?.method).toBe('PATCH');
    expect(stub.calls[0].url).toBe('http://api/api/v1/workspaces/ws-1/squads/sq-1');
    expect(lastBody()).toEqual({ name: 'Renamed' });
  });

  it('archives and restores via POST sub-resources', async () => {
    await archiveSquad(client, 'ws-1', 'sq-1');
    expect(stub.calls[0].init?.method).toBe('POST');
    expect(stub.calls[0].url).toBe('http://api/api/v1/workspaces/ws-1/squads/sq-1/archive');
    await restoreSquad(client, 'ws-1', 'sq-1');
    expect(stub.calls[1].url).toBe('http://api/api/v1/workspaces/ws-1/squads/sq-1/restore');
  });
});

describe('membership (§3.2)', () => {
  it('lists members and unwraps data', async () => {
    const memberStub = stubFetch(
      fakeResponse({ body: { data: [{ id: 'sm-1', role: 'leader' }], next_cursor: null } }),
    );
    vi.stubGlobal('fetch', memberStub.fetchImpl);
    const members = await listMembers(client, 'ws-1', 'sq-1');
    expect(memberStub.calls[0].url).toBe('http://api/api/v1/workspaces/ws-1/squads/sq-1/members');
    expect(members).toEqual([{ id: 'sm-1', role: 'leader' }]);
  });

  it('adds members with a wrapped members array', async () => {
    await addMembers(client, 'ws-1', 'sq-1', [{ member_id: 'mem-2', role: 'member' }]);
    expect(stub.calls[0].init?.method).toBe('POST');
    expect(stub.calls[0].url).toBe('http://api/api/v1/workspaces/ws-1/squads/sq-1/members');
    expect(lastBody()).toEqual({ members: [{ member_id: 'mem-2', role: 'member' }] });
  });

  it('changes role via PATCH on the member sub-resource', async () => {
    await changeRole(client, 'ws-1', 'sq-1', 'mem-2', 'observer');
    expect(stub.calls[0].init?.method).toBe('PATCH');
    expect(stub.calls[0].url).toBe('http://api/api/v1/workspaces/ws-1/squads/sq-1/members/mem-2');
    expect(lastBody()).toEqual({ role: 'observer' });
  });

  it('removes a member via DELETE', async () => {
    await removeMember(client, 'ws-1', 'sq-1', 'mem-2');
    expect(stub.calls[0].init?.method).toBe('DELETE');
    expect(stub.calls[0].url).toBe('http://api/api/v1/workspaces/ws-1/squads/sq-1/members/mem-2');
  });
});

describe('orchestration and tasks (§3.3 / §3.4)', () => {
  it('assigns a task via POST /tasks', async () => {
    await assignTask(client, 'ws-1', 'sq-1', { issue_id: 'iss-1', brief: 'Fix it' });
    expect(stub.calls[0].init?.method).toBe('POST');
    expect(stub.calls[0].url).toBe('http://api/api/v1/workspaces/ws-1/squads/sq-1/tasks');
    expect(lastBody()).toEqual({ issue_id: 'iss-1', brief: 'Fix it' });
  });

  it('lists tasks with status/limit query params', async () => {
    await listTasks(client, 'ws-1', 'sq-1', { status: 'in_progress', limit: 5 });
    const url = stub.calls[0].url;
    expect(url).toContain('/squads/sq-1/tasks');
    expect(url).toContain('status=in_progress');
    expect(url).toContain('limit=5');
  });

  it('gets a task, its tree, and its status', async () => {
    await getTask(client, 'ws-1', 'sq-1', 'tk-1');
    expect(stub.calls[0].url).toBe('http://api/api/v1/workspaces/ws-1/squads/sq-1/tasks/tk-1');
    await getTaskTree(client, 'ws-1', 'sq-1', 'tk-1');
    expect(stub.calls[1].url).toBe('http://api/api/v1/workspaces/ws-1/squads/sq-1/tasks/tk-1/tree');
    await getTaskStatus(client, 'ws-1', 'sq-1', 'tk-1');
    expect(stub.calls[2].url).toBe(
      'http://api/api/v1/workspaces/ws-1/squads/sq-1/tasks/tk-1/status',
    );
  });

  it('creates subtasks with plan and dependency payload', async () => {
    await createSubtasks(client, 'ws-1', 'sq-1', 'tk-1', {
      plan_markdown: '# Plan',
      subtasks: [{ title: 'Step 1', stage: 1, depends_on: [] }],
    });
    expect(stub.calls[0].init?.method).toBe('POST');
    expect(stub.calls[0].url).toBe(
      'http://api/api/v1/workspaces/ws-1/squads/sq-1/tasks/tk-1/subtasks',
    );
    expect(lastBody()).toEqual({
      plan_markdown: '# Plan',
      subtasks: [{ title: 'Step 1', stage: 1, depends_on: [] }],
    });
  });

  it('approves and rejects plans with optional comment', async () => {
    await approvePlan(client, 'ws-1', 'sq-1', 'tk-1', 'lgtm');
    expect(stub.calls[0].url).toBe(
      'http://api/api/v1/workspaces/ws-1/squads/sq-1/tasks/tk-1/plan/approve',
    );
    expect(lastBody()).toEqual({ comment: 'lgtm' });
    await rejectPlan(client, 'ws-1', 'sq-1', 'tk-1');
    expect(stub.calls[1].url).toBe(
      'http://api/api/v1/workspaces/ws-1/squads/sq-1/tasks/tk-1/plan/reject',
    );
  });

  it('dispatches and cancels tasks', async () => {
    await dispatchTask(client, 'ws-1', 'sq-1', 'tk-1');
    expect(stub.calls[0].init?.method).toBe('POST');
    expect(stub.calls[0].url).toBe(
      'http://api/api/v1/workspaces/ws-1/squads/sq-1/tasks/tk-1/dispatch',
    );
    await cancelTask(client, 'ws-1', 'sq-1', 'tk-1', 'obsolete');
    expect(stub.calls[1].url).toBe(
      'http://api/api/v1/workspaces/ws-1/squads/sq-1/tasks/tk-1/cancel',
    );
    expect(lastBody()).toEqual({ reason: 'obsolete' });
  });

  it('moves a task status via PATCH with the target status body (§4.2)', async () => {
    await moveTaskStatus(client, 'ws-1', 'sq-1', 'tk-1', { status: 'in_progress' });
    expect(stub.calls[0].init?.method).toBe('PATCH');
    expect(stub.calls[0].url).toBe(
      'http://api/api/v1/workspaces/ws-1/squads/sq-1/tasks/tk-1/status',
    );
    expect(lastBody()).toEqual({ status: 'in_progress' });
  });

  it('builds the task SSE stream absolute URL (§3.2 / §6.8)', () => {
    expect(taskStreamUrl('ws-1', 'sq-1', 'tk-1')).toContain(
      '/api/v1/workspaces/ws-1/squads/sq-1/tasks/tk-1/stream',
    );
  });
});

describe('issue assignment (§2.5 / §4.3-2)', () => {
  it('reads the active squad assignment for an issue (by-issue, before {squad_id})', async () => {
    await getIssueAssignment(client, 'ws-1', 'iss-1');
    expect(stub.calls[0].init?.method ?? 'GET').toBe('GET');
    expect(stub.calls[0].url).toBe(
      'http://api/api/v1/workspaces/ws-1/squads/assignments/by-issue/iss-1',
    );
  });
});

describe('messages and activity (§3.5)', () => {
  it('lists messages with task_id/kind/limit query params', async () => {
    await listMessages(client, 'ws-1', 'sq-1', { taskId: 'tk-1', kind: 'report', limit: 3 });
    const url = stub.calls[0].url;
    expect(url).toContain('/squads/sq-1/messages');
    expect(url).toContain('task_id=tk-1');
    expect(url).toContain('kind=report');
    expect(url).toContain('limit=3');
  });

  it('sends a message via POST with body', async () => {
    await sendMessage(client, 'ws-1', 'sq-1', {
      kind: 'instruction',
      body_markdown: 'Do this',
      pinned: true,
    });
    expect(stub.calls[0].init?.method).toBe('POST');
    expect(stub.calls[0].url).toBe('http://api/api/v1/workspaces/ws-1/squads/sq-1/messages');
    expect(lastBody()).toEqual({ kind: 'instruction', body_markdown: 'Do this', pinned: true });
  });

  it('lists activity with task_id/action/limit query params', async () => {
    await listActivity(client, 'ws-1', 'sq-1', { taskId: 'tk-1', action: 'task_started', limit: 2 });
    const url = stub.calls[0].url;
    expect(url).toContain('/squads/sq-1/activity');
    expect(url).toContain('task_id=tk-1');
    expect(url).toContain('action=task_started');
    expect(url).toContain('limit=2');
  });
});
