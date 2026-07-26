/**
 * 项目模块 API 契约层测试:路径/方法/请求体/查询与 project.md §3 / README §6.14 包络一致,
 * 列表经 `list` 自动解 {data,next_cursor} → {data,nextCursor},乐观并发经 ifMatch 透传。
 */
import { describe, expect, it, vi } from 'vitest';
import type { MeshApiClient } from '../../../api';
import {
  addProjectMember,
  addProjectUpdate,
  archiveProject,
  createCycle,
  createMilestone,
  createProject,
  createProjectTemplate,
  deleteMilestone,
  deleteProject,
  deleteProjectTemplate,
  getProject,
  instantiateProjectTemplate,
  listCycles,
  listMilestones,
  listProjectMembers,
  listProjects,
  listProjectTemplates,
  listProjectUpdates,
  projectChannel,
  removeProjectMember,
  unarchiveProject,
  updateCycle,
  updateMilestone,
  updateProject,
  updateProjectMemberRole,
  updateProjectTemplate,
  workspaceProjectsChannel,
} from '../api';

function makeClient() {
  const request = vi.fn(async () => ({}));
  const list = vi.fn(async (): Promise<{ data: unknown[]; next_cursor: string | null }> => ({
    data: [],
    next_cursor: null,
  }));
  const client = { request, list } as unknown as MeshApiClient;
  return { client, request, list };
}

describe('实时频道辅助', () => {
  it('projectChannel 返回项目详情页频道', () => {
    expect(projectChannel('p1')).toBe('project:p1');
  });

  it('workspaceProjectsChannel 返回工作区列表级频道', () => {
    expect(workspaceProjectsChannel('ws-1')).toBe('workspace:ws-1:projects');
  });
});

describe('项目 CRUD 与归档', () => {
  it('listProjects 透传全部筛选查询并解包 {data,nextCursor}', async () => {
    const { client, list } = makeClient();
    list.mockResolvedValueOnce({ data: [{ id: 'p1' }], next_cursor: 'c1' });
    const result = await listProjects(client, 'ws-1', {
      status: 'active',
      visibility: 'private',
      archived: false,
      mine: true,
      lead_member_id: 'm1',
      limit: 20,
      cursor: 'prev',
    });
    expect(list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/projects', {
      query: {
        status: 'active',
        visibility: 'private',
        archived: false,
        mine: true,
        lead_member_id: 'm1',
        limit: 20,
        cursor: 'prev',
      },
    });
    expect(result.data).toEqual([{ id: 'p1' }]);
    expect(result.nextCursor).toBe('c1');
  });

  it('listProjects 无参数时省略所有查询键值(undefined)', async () => {
    const { client, list } = makeClient();
    list.mockResolvedValueOnce({ data: [], next_cursor: null });
    const result = await listProjects(client, 'ws-1');
    expect(list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/projects', {
      query: {
        status: undefined,
        visibility: undefined,
        archived: undefined,
        mine: undefined,
        lead_member_id: undefined,
        limit: undefined,
        cursor: undefined,
      },
    });
    expect(result).toEqual({ data: [], nextCursor: null });
  });

  it('getProject 命中 GET /projects/{id}', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'p1', milestones: [] });
    const detail = await getProject(client, 'p1');
    expect(request).toHaveBeenCalledWith('GET', '/api/v1/projects/p1');
    expect(detail.id).toBe('p1');
  });

  it('createProject 以 POST /workspaces/{ws}/projects 提交 body', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'p1' });
    const body = { name: 'Apollo', key: 'APL' } as const;
    const created = await createProject(client, 'ws-1', body);
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/projects', { body });
    expect(created.id).toBe('p1');
  });

  it('updateProject 以 PATCH 提交并把 ifMatch 透传为 opts.ifMatch', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'p1' });
    const body = { status: 'active' } as const;
    await updateProject(client, 'p1', body, '2026-07-25T00:00:00Z');
    expect(request).toHaveBeenCalledWith('PATCH', '/api/v1/projects/p1', {
      body,
      ifMatch: '2026-07-25T00:00:00Z',
    });
  });

  it('updateProject 省略 ifMatch 时透传 undefined', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'p1' });
    const body = { name: 'Renamed' } as const;
    await updateProject(client, 'p1', body);
    expect(request).toHaveBeenCalledWith('PATCH', '/api/v1/projects/p1', {
      body,
      ifMatch: undefined,
    });
  });

  it('deleteProject 命中 DELETE /projects/{id} 并返回 {id,deleted}', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'p1', deleted: true });
    const result = await deleteProject(client, 'p1');
    expect(request).toHaveBeenCalledWith('DELETE', '/api/v1/projects/p1');
    expect(result).toEqual({ id: 'p1', deleted: true });
  });

  it('archiveProject 命中 POST /projects/{id}/archive', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'p1', archived: true });
    const result = await archiveProject(client, 'p1');
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/projects/p1/archive');
    expect(result.archived).toBe(true);
  });

  it('unarchiveProject 命中 POST /projects/{id}/unarchive', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'p1', archived: false });
    const result = await unarchiveProject(client, 'p1');
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/projects/p1/unarchive');
    expect(result.archived).toBe(false);
  });
});

describe('健康度/状态留痕', () => {
  it('addProjectUpdate 以 POST /projects/{id}/updates 提交 body', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'u1', health: 'on_track' });
    const body = { health: 'on_track', message: 'ok' } as const;
    const entry = await addProjectUpdate(client, 'p1', body);
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/projects/p1/updates', { body });
    expect(entry.health).toBe('on_track');
  });

  it('listProjectUpdates 透传 limit/cursor 并解包', async () => {
    const { client, list } = makeClient();
    list.mockResolvedValueOnce({ data: [{ id: 'u1' }], next_cursor: 'n' });
    const result = await listProjectUpdates(client, 'p1', { limit: 5, cursor: 'c' });
    expect(list).toHaveBeenCalledWith('/api/v1/projects/p1/updates', {
      query: { limit: 5, cursor: 'c' },
    });
    expect(result.data).toEqual([{ id: 'u1' }]);
    expect(result.nextCursor).toBe('n');
  });
});

describe('里程碑', () => {
  it('listMilestones 透传 state/limit/cursor 并解包', async () => {
    const { client, list } = makeClient();
    list.mockResolvedValueOnce({ data: [{ id: 'ms1' }], next_cursor: null });
    const result = await listMilestones(client, 'p1', { state: 'open', limit: 10, cursor: 'c' });
    expect(list).toHaveBeenCalledWith('/api/v1/projects/p1/milestones', {
      query: { state: 'open', limit: 10, cursor: 'c' },
    });
    expect(result.nextCursor).toBeNull();
  });

  it('createMilestone 以 POST /projects/{id}/milestones 提交 body', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'ms1' });
    const body = { title: 'Beta' } as const;
    await createMilestone(client, 'p1', body);
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/projects/p1/milestones', { body });
  });

  it('updateMilestone 命中 PATCH /milestones/{id}', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'ms1' });
    const body = { state: 'closed' } as const;
    await updateMilestone(client, 'ms1', body);
    expect(request).toHaveBeenCalledWith('PATCH', '/api/v1/milestones/ms1', { body });
  });

  it('deleteMilestone 命中 DELETE /milestones/{id}', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'ms1', deleted: true });
    const result = await deleteMilestone(client, 'ms1');
    expect(request).toHaveBeenCalledWith('DELETE', '/api/v1/milestones/ms1');
    expect(result.deleted).toBe(true);
  });
});

describe('周期', () => {
  it('listCycles 透传 state/project_id/limit/cursor 并解包', async () => {
    const { client, list } = makeClient();
    list.mockResolvedValueOnce({ data: [{ id: 'cyc1' }], next_cursor: 'c' });
    const result = await listCycles(client, 'ws-1', {
      state: 'active',
      project_id: 'p1',
      limit: 8,
      cursor: 'cur',
    });
    expect(list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/cycles', {
      query: { state: 'active', project_id: 'p1', limit: 8, cursor: 'cur' },
    });
    expect(result.data).toEqual([{ id: 'cyc1' }]);
    expect(result.nextCursor).toBe('c');
  });

  it('createCycle 以 POST /workspaces/{ws}/cycles 提交 body', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'cyc1' });
    const body = { name: 'Sprint 1', starts_at: 'a', ends_at: 'b' } as const;
    await createCycle(client, 'ws-1', body);
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/cycles', { body });
  });

  it('updateCycle 命中 PATCH /cycles/{id} 并可携带 next_cycle', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'cyc1', next_cycle: { id: 'cyc2' } });
    const body = { state: 'completed' } as const;
    const result = await updateCycle(client, 'cyc1', body);
    expect(request).toHaveBeenCalledWith('PATCH', '/api/v1/cycles/cyc1', { body });
    expect(result.next_cycle?.id).toBe('cyc2');
  });
});

describe('项目成员', () => {
  it('listProjectMembers 透传 limit/cursor 并解包', async () => {
    const { client, list } = makeClient();
    list.mockResolvedValueOnce({ data: [{ id: 'pm1' }], next_cursor: null });
    const result = await listProjectMembers(client, 'p1', { limit: 3, cursor: 'c' });
    expect(list).toHaveBeenCalledWith('/api/v1/projects/p1/members', {
      query: { limit: 3, cursor: 'c' },
    });
    expect(result.data).toEqual([{ id: 'pm1' }]);
  });

  it('addProjectMember 以 POST /projects/{id}/members 提交 body', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'pm1', role: 'member' });
    const body = { member_id: 'm1', role: 'member' } as const;
    const entry = await addProjectMember(client, 'p1', body);
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/projects/p1/members', { body });
    expect(entry.role).toBe('member');
  });

  it('updateProjectMemberRole 命中 PATCH /projects/{id}/members/{memberId}', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'pm1', role: 'lead' });
    const body = { role: 'lead' } as const;
    const entry = await updateProjectMemberRole(client, 'p1', 'm1', body);
    expect(request).toHaveBeenCalledWith('PATCH', '/api/v1/projects/p1/members/m1', { body });
    expect(entry.role).toBe('lead');
  });

  it('removeProjectMember 命中 DELETE /projects/{id}/members/{memberId}', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'm1', deleted: true });
    const result = await removeProjectMember(client, 'p1', 'm1');
    expect(request).toHaveBeenCalledWith('DELETE', '/api/v1/projects/p1/members/m1');
    expect(result.deleted).toBe(true);
  });
});

describe('项目模板', () => {
  it('listProjectTemplates 命中列表路径并解包', async () => {
    const { client, list } = makeClient();
    list.mockResolvedValueOnce({ data: [{ id: 't1' }], next_cursor: 'n' });
    const result = await listProjectTemplates(client, 'ws-1');
    expect(list).toHaveBeenCalledWith('/api/v1/workspaces/ws-1/project-templates');
    expect(result.data).toEqual([{ id: 't1' }]);
    expect(result.nextCursor).toBe('n');
  });

  it('createProjectTemplate 以 POST 提交 body', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 't1' });
    const body = { name: 'Launch', template_body: { milestones: [] } } as const;
    const created = await createProjectTemplate(client, 'ws-1', body);
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/workspaces/ws-1/project-templates', {
      body,
    });
    expect(created.id).toBe('t1');
  });

  it('updateProjectTemplate 命中 PATCH /project-templates/{id}', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 't1' });
    const body = { name: 'Renamed template' } as const;
    await updateProjectTemplate(client, 't1', body);
    expect(request).toHaveBeenCalledWith('PATCH', '/api/v1/project-templates/t1', { body });
  });

  it('deleteProjectTemplate 命中 DELETE /project-templates/{id}', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 't1', deleted: true });
    const result = await deleteProjectTemplate(client, 't1');
    expect(request).toHaveBeenCalledWith('DELETE', '/api/v1/project-templates/t1');
    expect(result.deleted).toBe(true);
  });

  it('instantiateProjectTemplate 命中 POST /project-templates/{id}/instantiate', async () => {
    const { client, request } = makeClient();
    request.mockResolvedValueOnce({ id: 'p1', milestone_ids: ['ms1'], cycle_ids: [], skipped: [] });
    const body = { name: 'From template', key: 'FT' } as const;
    const result = await instantiateProjectTemplate(client, 't1', body);
    expect(request).toHaveBeenCalledWith('POST', '/api/v1/project-templates/t1/instantiate', {
      body,
    });
    expect(result.milestone_ids).toEqual(['ms1']);
  });
});
