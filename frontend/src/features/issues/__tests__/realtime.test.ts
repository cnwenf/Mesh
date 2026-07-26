/**
 * Issue 实时帧合并纯函数测试(issue.md §3.6/§4.5,README §6.7):
 * created/updated/moved/deleted/project_changed 合并、updated_at 防回退、belongs 水位。
 */
import { describe, expect, it } from 'vitest';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { applyIssueDetailFrame, applyIssueListFrame } from '../realtime';
import type { IssueSummary } from '../types';

function frame(event: string, payload: unknown): RealtimeEventFrame {
  return { op: 'event', channel: 'workspace:ws-1:issues', seq: 1, event, payload } as RealtimeEventFrame;
}

const BASE: IssueSummary = {
  id: 'iss-1',
  workspace_id: 'ws-1',
  project_id: null,
  project: null,
  identifier_namespace_key: 'WS',
  number: 1,
  identifier: 'WS-1',
  title: 'base',
  description: null,
  status: null,
  status_id: 'st-1',
  state_category: 'todo',
  priority: 'none',
  assignee: null,
  assignee_id: null,
  reporter: null,
  reporter_id: null,
  estimate: null,
  estimate_unit: null,
  due_date: null,
  start_date: null,
  milestone_id: null,
  cycle_id: null,
  parent_id: null,
  position: 0,
  completed_at: null,
  version: 1,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-02T00:00:00Z',
};

const always = (): boolean => true;

describe('applyIssueListFrame', () => {
  it('ignores non-issue events and returns the same reference', () => {
    const list = [BASE];
    expect(applyIssueListFrame(list, frame('comment.created', {}), always)).toBe(list);
  });

  it('upserts issue.created payloads when the issue belongs', () => {
    const created = { ...BASE, id: 'iss-2', identifier: 'WS-2' };
    const next = applyIssueListFrame([BASE], frame('issue.created', { issue: created }), always);
    expect(next.map((i) => i.id)).toEqual(['iss-1', 'iss-2']);
    // duplicate created is a no-op (same reference)
    const same = applyIssueListFrame(next, frame('issue.created', { issue: created }), always);
    expect(same).toBe(next);
  });

  it('drops created issues that fail the belongs predicate', () => {
    const created = { ...BASE, id: 'iss-2' };
    const next = applyIssueListFrame([BASE], frame('issue.created', { issue: created }), () => false);
    expect(next).toEqual([BASE]);
  });

  it('merges issue.updated changes with stale guard', () => {
    const next = applyIssueListFrame(
      [BASE],
      frame('issue.updated', {
        id: 'iss-1',
        changes: { priority: 'high' },
        version: 2,
        updated_at: '2026-07-03T00:00:00Z',
      }),
      always,
    );
    expect(next[0].priority).toBe('high');
    expect(next[0].version).toBe(2);
    // older frame is discarded (same reference returned)
    const stale = applyIssueListFrame(
      next,
      frame('issue.updated', {
        id: 'iss-1',
        changes: { priority: 'low' },
        updated_at: '2026-07-01T00:00:00Z',
      }),
      always,
    );
    expect(stale).toBe(next);
  });

  it('removes the row when the updated issue no longer belongs', () => {
    const next = applyIssueListFrame(
      [BASE],
      frame('issue.updated', { id: 'iss-1', changes: {}, updated_at: '2026-07-03T00:00:00Z' }),
      () => false,
    );
    expect(next).toEqual([]);
  });

  it('handles issue.moved like updated', () => {
    const next = applyIssueListFrame(
      [BASE],
      frame('issue.moved', {
        id: 'iss-1',
        changes: { state_category: 'done' },
        updated_at: '2026-07-04T00:00:00Z',
      }),
      always,
    );
    expect(next[0].state_category).toBe('done');
  });

  it('removes rows on issue.deleted', () => {
    const next = applyIssueListFrame([BASE], frame('issue.deleted', { id: 'iss-1' }), always);
    expect(next).toEqual([]);
  });

  it('merges then re-filters on issue.project_changed', () => {
    const next = applyIssueListFrame(
      [BASE],
      frame('issue.project_changed', {
        id: 'iss-1',
        project_id: 'prj-9',
        updated_at: '2026-07-05T00:00:00Z',
      }),
      (issue) => issue.project_id === null,
    );
    expect(next).toEqual([]);
  });

  it('returns same reference for unknown actions and missing ids', () => {
    const list = [BASE];
    expect(applyIssueListFrame(list, frame('issue.labels_changed', {}), always)).toBe(list);
    expect(applyIssueListFrame(list, frame('issue.updated', {}), always)).toBe(list);
    expect(
      applyIssueListFrame(list, frame('issue.created', { issue: { title: 'no id' } }), always),
    ).toBe(list);
  });
});

it('strips frame meta fields from merged rows (F11)', () => {
  const merged = applyIssueListFrame(
    [BASE],
    frame('issue.project_changed', {
      id: 'iss-1',
      from_project_id: 'prj-1',
      to_project_id: 'prj-2',
      mapped_fields: [{ field: 'status' }],
      cleared_fields: [{ field: 'milestone_id' }],
      updated_at: '2026-07-03T00:00:00Z',
    }),
    always,
  );
  const row = merged[0] as unknown as Record<string, unknown>;
  expect(row.from_project_id).toBeUndefined();
  expect(row.to_project_id).toBeUndefined();
  expect(row.mapped_fields).toBeUndefined();
  expect(row.cleared_fields).toBeUndefined();
  expect(row.id).toBe('iss-1');
});

it('skips __proto__/constructor/prototype keys from frame payloads (LOW-1 原型污染防护)', () => {
  // JSON.parse 使 `__proto__` 成为帧载荷的自有属性(真实 WS 帧即经 JSON.parse),
  // 若合并时按下标赋值进普通对象将触发 Object.prototype setter 改写原型。
  const hostile = JSON.parse(
    '{"id":"iss-1","__proto__":{"polluted":"list"},"constructor":{"polluted":"list"},' +
      '"prototype":{"polluted":"list"},"changes":{"title":"safe"},' +
      '"updated_at":"2026-07-03T00:00:00Z"}',
  ) as unknown;
  const merged = applyIssueListFrame([BASE], frame('issue.updated', hostile), always);
  // Object.prototype 未被改写:全新空对象不含攻击者植入的属性
  expect(({} as { polluted?: unknown }).polluted).toBeUndefined();
  // 攻击键不作为字段并入行对象,合法字段照常合并
  const row = merged[0] as unknown as Record<string, unknown>;
  expect(row.polluted).toBeUndefined();
  expect(row.title).toBe('safe');
  expect(row.id).toBe('iss-1');
  // 详情级合并同路径防护
  const detailHostile = JSON.parse(
    '{"id":"iss-1","__proto__":{"polluted":"detail"},"updated_at":"2026-07-03T00:00:00Z"}',
  ) as unknown;
  const detail = applyIssueDetailFrame(
    BASE,
    frame('issue.updated', detailHostile),
  ) as unknown as Record<string, unknown>;
  expect(({} as { polluted?: unknown }).polluted).toBeUndefined();
  expect(detail.polluted).toBeUndefined();
});

describe('applyIssueDetailFrame', () => {
  it('merges frames for the same issue id only', () => {
    const merged = applyIssueDetailFrame(
      BASE,
      frame('issue.updated', {
        id: 'iss-1',
        changes: { title: 'new' },
        updated_at: '2026-07-03T00:00:00Z',
      }),
    );
    expect(merged.title).toBe('new');
    const other = applyIssueDetailFrame(
      BASE,
      frame('issue.updated', { id: 'iss-other', changes: { title: 'x' } }),
    );
    expect(other).toBe(BASE);
  });

  it('ignores created/deleted frames and non-issue events', () => {
    expect(applyIssueDetailFrame(BASE, frame('issue.created', { id: 'iss-1' }))).toBe(BASE);
    expect(applyIssueDetailFrame(BASE, frame('issue.deleted', { id: 'iss-1' }))).toBe(BASE);
    expect(applyIssueDetailFrame(BASE, frame('dependency.changed', { id: 'iss-1' }))).toBe(BASE);
  });

  it('applies the stale guard', () => {
    const stale = applyIssueDetailFrame(
      BASE,
      frame('issue.updated', {
        id: 'iss-1',
        changes: { title: 'older' },
        updated_at: '2026-07-01T00:00:00Z',
      }),
    );
    expect(stale).toBe(BASE);
  });
});
