/**
 * 项目模块实时帧合并纯函数测试(project.md §3.5/§4.5,README §6.7)。
 * 覆盖:列表合并(created/updated/deleted/archived/unarchived)、updated_at 字符串防回退、
 * 嵌套 changes 合并、belongs 可见性水位移除、异实体/无 id 不变、不可变性(入参不被改写)。
 */
import { describe, expect, it } from 'vitest';
import type { RealtimeEventFrame } from '../../../types/realtime';
import type { Milestone, ProjectDetail, ProjectSummary, ProjectUpdateEntry } from '../types';
import {
  applyMilestoneFrame,
  applyProjectListFrame,
  applyUpdateFrame,
  mergeProjectHeader,
} from '../realtime';

function makeFrame(event: string, payload: Record<string, unknown>): RealtimeEventFrame {
  return { op: 'event', channel: 'workspace:ws-1:projects', seq: 1, event, payload };
}

const T1 = '2026-01-01T00:00:00Z';
const T2 = '2026-02-01T00:00:00Z';

function makeProject(overrides: Partial<ProjectSummary> = {}): ProjectSummary {
  return {
    id: 'p1',
    workspace_id: 'ws-1',
    name: 'Apollo',
    key: 'APL',
    description: null,
    icon: null,
    color: null,
    status: 'active',
    health: null,
    visibility: 'public',
    lead: null,
    lead_member_id: null,
    start_date: null,
    target_date: null,
    progress: 0,
    open_issues: 0,
    done_issues: 0,
    issue_seq: 0,
    archived: false,
    archived_at: null,
    my_role: null,
    created_at: T1,
    updated_at: T1,
    ...overrides,
  };
}

function makeDetail(overrides: Partial<ProjectDetail> = {}): ProjectDetail {
  return { ...makeProject(), milestones: [], ...overrides };
}

function makeMilestone(overrides: Partial<Milestone> = {}): Milestone {
  return {
    id: 'ms1',
    project_id: 'p1',
    title: 'Beta',
    description: null,
    target_date: null,
    state: 'open',
    overdue: false,
    created_at: T1,
    updated_at: T1,
    ...overrides,
  };
}

function makeUpdate(overrides: Partial<ProjectUpdateEntry> = {}): ProjectUpdateEntry {
  return {
    id: 'u1',
    project_id: 'p1',
    author: null,
    health: null,
    status: null,
    message: null,
    created_at: T1,
    ...overrides,
  };
}

const always = (): boolean => true;

describe('applyProjectListFrame — 守卫与无操作', () => {
  it('异实体事件原样返回同一引用', () => {
    const projects = [makeProject()];
    const result = applyProjectListFrame(
      projects,
      makeFrame('issue.updated', { id: 'p1' }),
      always,
    );
    expect(result).toBe(projects);
  });

  it('payload 缺少 id 原样返回同一引用', () => {
    const projects = [makeProject()];
    const result = applyProjectListFrame(
      projects,
      makeFrame('project.updated', { status: 'paused' }),
      always,
    );
    expect(result).toBe(projects);
  });

  it('payload id 非字符串原样返回同一引用', () => {
    const projects = [makeProject()];
    const result = applyProjectListFrame(
      projects,
      makeFrame('project.updated', { id: 123 }),
      always,
    );
    expect(result).toBe(projects);
  });
});

describe('applyProjectListFrame — created/updated 合并', () => {
  it('created 追加新项目且不改写入参数组', () => {
    const projects = [makeProject()];
    const incoming = makeProject({ id: 'p2', key: 'TWO', name: 'Two' });
    const result = applyProjectListFrame(
      projects,
      makeFrame('project.created', { ...incoming }),
      always,
    );
    expect(result).toHaveLength(2);
    expect(result[1].id).toBe('p2');
    expect(result).not.toBe(projects);
    // 入参数组未被改写
    expect(projects).toHaveLength(1);
    expect(projects[0].id).toBe('p1');
  });

  it('created 但不属于当前视图且不存在 → 原样返回', () => {
    const projects = [makeProject()];
    const incoming = makeProject({ id: 'p2', visibility: 'private' });
    const belongs = (p: ProjectSummary): boolean => p.visibility === 'public';
    const result = applyProjectListFrame(
      projects,
      makeFrame('project.created', { ...incoming }),
      belongs,
    );
    expect(result).toBe(projects);
  });

  it('updated 合并顶层字段并替换原项(不改写原对象)', () => {
    const original = makeProject();
    const projects = [original];
    const result = applyProjectListFrame(
      projects,
      makeFrame('project.updated', { id: 'p1', status: 'paused', updated_at: T2 }),
      always,
    );
    expect(result[0].status).toBe('paused');
    expect(result).not.toBe(projects);
    // 原对象未被改写
    expect(original.status).toBe('active');
  });

  it('updated 合并嵌套 changes 字段(changes 本身不落库)', () => {
    const projects = [makeProject()];
    const result = applyProjectListFrame(
      projects,
      makeFrame('project.updated', {
        id: 'p1',
        updated_at: T2,
        changes: { status: 'completed', progress: 0.5 },
      }),
      always,
    );
    expect(result[0].status).toBe('completed');
    expect(result[0].progress).toBe(0.5);
    expect(result[0] as unknown as Record<string, unknown>).not.toHaveProperty('changes');
  });

  it('updated 防回退:帧更旧则丢弃(同一引用)', () => {
    const projects = [makeProject({ updated_at: T2 })];
    const result = applyProjectListFrame(
      projects,
      makeFrame('project.updated', { id: 'p1', status: 'paused', updated_at: T1 }),
      always,
    );
    expect(result).toBe(projects);
  });

  it('updated 的 changes 为 null/非对象时按空处理(仅合并顶层字段)', () => {
    const projects = [makeProject()];
    const withNull = applyProjectListFrame(
      projects,
      makeFrame('project.updated', { id: 'p1', status: 'paused', updated_at: T2, changes: null }),
      always,
    );
    expect(withNull[0].status).toBe('paused');

    const withScalar = applyProjectListFrame(
      projects,
      makeFrame('project.updated', { id: 'p1', status: 'paused', updated_at: T2, changes: 'oops' }),
      always,
    );
    expect(withScalar[0].status).toBe('paused');
  });

  it('updated 帧缺 updated_at 时不判旧、正常合并', () => {
    const projects = [makeProject({ updated_at: T2 })];
    const result = applyProjectListFrame(
      projects,
      makeFrame('project.updated', { id: 'p1', status: 'paused' }),
      always,
    );
    expect(result[0].status).toBe('paused');
  });

  it('updated 合并后不属于视图(可见性水位)→ 从列表移除', () => {
    const projects = [makeProject({ visibility: 'public' })];
    const belongs = (p: ProjectSummary): boolean => p.visibility === 'public';
    const result = applyProjectListFrame(
      projects,
      makeFrame('project.updated', {
        id: 'p1',
        changes: { visibility: 'private' },
        updated_at: T2,
      }),
      belongs,
    );
    expect(result).toHaveLength(0);
  });

  it('已存在项缺 updated_at 时帧不判旧(防回退闸放开)', () => {
    // 模拟此前 created 帧未带 updated_at 的项目(运行时可能缺字段)
    const noStamp = { ...makeProject() } as unknown as { updated_at?: string };
    delete noStamp.updated_at;
    const projects = [noStamp as unknown as ProjectSummary];
    const result = applyProjectListFrame(
      projects,
      makeFrame('project.updated', { id: 'p1', status: 'paused', updated_at: T1 }),
      always,
    );
    expect(result[0].status).toBe('paused');
  });
});

describe('applyProjectListFrame — archived/unarchived', () => {
  it('archived 置 archived=true', () => {
    const projects = [makeProject()];
    const result = applyProjectListFrame(
      projects,
      makeFrame('project.archived', { id: 'p1', updated_at: T2 }),
      always,
    );
    expect(result[0].archived).toBe(true);
  });

  it('unarchived 置 archived=false', () => {
    const projects = [makeProject({ archived: true })];
    const result = applyProjectListFrame(
      projects,
      makeFrame('project.unarchived', { id: 'p1', updated_at: T2 }),
      always,
    );
    expect(result[0].archived).toBe(false);
  });

  it('archived 目标不存在 → 原样返回', () => {
    const projects = [makeProject()];
    const result = applyProjectListFrame(
      projects,
      makeFrame('project.archived', { id: 'ghost', updated_at: T2 }),
      always,
    );
    expect(result).toBe(projects);
  });

  it('archived 防回退:帧更旧则丢弃', () => {
    const projects = [makeProject({ updated_at: T2 })];
    const result = applyProjectListFrame(
      projects,
      makeFrame('project.archived', { id: 'p1', updated_at: T1 }),
      always,
    );
    expect(result).toBe(projects);
  });

  it('archived 后不属于视图 → 从列表移除', () => {
    const projects = [makeProject()];
    const belongs = (p: ProjectSummary): boolean => !p.archived;
    const result = applyProjectListFrame(
      projects,
      makeFrame('project.archived', { id: 'p1', updated_at: T2 }),
      belongs,
    );
    expect(result).toHaveLength(0);
  });
});

describe('applyProjectListFrame — deleted', () => {
  it('deleted 移除匹配项且不改写入参数组', () => {
    const projects = [makeProject(), makeProject({ id: 'p2' })];
    const result = applyProjectListFrame(
      projects,
      makeFrame('project.deleted', { id: 'p1' }),
      always,
    );
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('p2');
    expect(projects).toHaveLength(2);
  });

  it('deleted 目标不存在 → 原样返回', () => {
    const projects = [makeProject()];
    const result = applyProjectListFrame(
      projects,
      makeFrame('project.deleted', { id: 'ghost' }),
      always,
    );
    expect(result).toBe(projects);
  });
});

describe('applyMilestoneFrame', () => {
  it('异实体事件原样返回', () => {
    const milestones = [makeMilestone()];
    const result = applyMilestoneFrame(milestones, makeFrame('project.updated', { id: 'ms1' }));
    expect(result).toBe(milestones);
  });

  it('无 id 原样返回', () => {
    const milestones = [makeMilestone()];
    const result = applyMilestoneFrame(milestones, makeFrame('milestone.updated', { title: 'X' }));
    expect(result).toBe(milestones);
  });

  it('milestone.created 追加新里程碑', () => {
    const milestones = [makeMilestone()];
    const result = applyMilestoneFrame(
      milestones,
      makeFrame('milestone.created', { ...makeMilestone({ id: 'ms2', title: 'GA' }) }),
    );
    expect(result).toHaveLength(2);
    expect(result[1].id).toBe('ms2');
  });

  it('milestone.updated 合并字段(含嵌套 changes)且不改写原对象', () => {
    const original = makeMilestone();
    const milestones = [original];
    const result = applyMilestoneFrame(
      milestones,
      makeFrame('milestone.updated', {
        id: 'ms1',
        updated_at: T2,
        changes: { state: 'closed', overdue: false },
      }),
    );
    expect(result[0].state).toBe('closed');
    expect(original.state).toBe('open');
  });

  it('milestone.updated 防回退 → 原样返回', () => {
    const milestones = [makeMilestone({ updated_at: T2 })];
    const result = applyMilestoneFrame(
      milestones,
      makeFrame('milestone.updated', { id: 'ms1', title: 'X', updated_at: T1 }),
    );
    expect(result).toBe(milestones);
  });

  it('milestone.deleted 移除匹配项', () => {
    const milestones = [makeMilestone(), makeMilestone({ id: 'ms2' })];
    const result = applyMilestoneFrame(milestones, makeFrame('milestone.deleted', { id: 'ms1' }));
    expect(result).toHaveLength(1);
    expect(result[0].id).toBe('ms2');
  });

  it('milestone.deleted 目标不存在 → 原样返回', () => {
    const milestones = [makeMilestone()];
    const result = applyMilestoneFrame(milestones, makeFrame('milestone.deleted', { id: 'ghost' }));
    expect(result).toBe(milestones);
  });
});

describe('applyUpdateFrame', () => {
  it('非 project_update.added 事件原样返回', () => {
    const updates = [makeUpdate()];
    const result = applyUpdateFrame(updates, makeFrame('project.updated', { id: 'u1' }));
    expect(result).toBe(updates);
  });

  it('无 id 原样返回', () => {
    const updates = [makeUpdate()];
    const result = applyUpdateFrame(updates, makeFrame('project_update.added', { message: 'hi' }));
    expect(result).toBe(updates);
  });

  it('新增更新头插且不改写入参数组', () => {
    const updates = [makeUpdate()];
    const result = applyUpdateFrame(
      updates,
      makeFrame('project_update.added', { ...makeUpdate({ id: 'u2', message: 'new' }) }),
    );
    expect(result).toHaveLength(2);
    expect(result[0].id).toBe('u2');
    expect(updates).toHaveLength(1);
  });

  it('重复 id 不重复插入 → 原样返回', () => {
    const updates = [makeUpdate({ id: 'u1' })];
    const result = applyUpdateFrame(
      updates,
      makeFrame('project_update.added', { ...makeUpdate({ id: 'u1', message: 'dup' }) }),
    );
    expect(result).toBe(updates);
  });
});

describe('mergeProjectHeader', () => {
  it('非 project.updated 事件原样返回', () => {
    const detail = makeDetail();
    const result = mergeProjectHeader(detail, makeFrame('project.archived', { id: 'p1' }));
    expect(result).toBe(detail);
  });

  it('id 不匹配原样返回', () => {
    const detail = makeDetail({ id: 'p1' });
    const result = mergeProjectHeader(
      detail,
      makeFrame('project.updated', { id: 'other', status: 'paused' }),
    );
    expect(result).toBe(detail);
  });

  it('id 缺失(非字符串)原样返回', () => {
    const detail = makeDetail({ id: 'p1' });
    const result = mergeProjectHeader(detail, makeFrame('project.updated', { status: 'paused' }));
    expect(result).toBe(detail);
  });

  it('合并变更字段(含嵌套 changes)且不改写原对象', () => {
    const detail = makeDetail({ id: 'p1' });
    const result = mergeProjectHeader(
      detail,
      makeFrame('project.updated', {
        id: 'p1',
        updated_at: T2,
        changes: { progress: 0.75, status: 'active' },
      }),
    );
    expect(result.progress).toBe(0.75);
    expect(result).not.toBe(detail);
    expect(detail.progress).toBe(0);
  });

  it('防回退:帧更旧则原样返回', () => {
    const detail = makeDetail({ id: 'p1', updated_at: T2 });
    const result = mergeProjectHeader(
      detail,
      makeFrame('project.updated', { id: 'p1', updated_at: T1, changes: { progress: 1 } }),
    );
    expect(result).toBe(detail);
  });
});
