import { describe, expect, it } from 'vitest';
import type { RealtimeEventFrame } from '../../types/realtime';
import { mergeEntityFrame } from '../merge';
import type { MergeContext } from '../merge';

interface Entity {
  id: string;
  updated_at?: string;
  version?: number;
  title?: string;
  status?: string;
}

/** 构造后端 v0.1.0 形态的数据帧 {op:'event', channel, seq, event, payload} */
function frame(event: string, payload: Record<string, unknown>): RealtimeEventFrame {
  return { op: 'event', channel: 'view:1', seq: 1, event, payload };
}

const alwaysBelongs: MergeContext<Entity> = { belongs: () => true };
const neverBelongs: MergeContext<Entity> = { belongs: () => false };

describe('mergeEntityFrame', () => {
  it('inserts a new entity on .created when it belongs', () => {
    const result = mergeEntityFrame<Entity>(
      new Map(),
      frame('issue.created', { id: 'a', title: 'hello', updated_at: '2026-07-25T00:00:01Z' }),
      alwaysBelongs,
    );
    expect(result.get('a')).toEqual({ id: 'a', title: 'hello', updated_at: '2026-07-25T00:00:01Z' });
  });

  it('does not insert when the entity does not belong', () => {
    const result = mergeEntityFrame<Entity>(
      new Map(),
      frame('issue.created', { id: 'a', title: 'x' }),
      neverBelongs,
    );
    expect(result.has('a')).toBe(false);
  });

  it('shallow-merges changed fields over the existing entity on .updated', () => {
    const initial = new Map<string, Entity>([
      ['a', { id: 'a', title: 'old', status: 'todo', updated_at: '2026-07-25T00:00:01Z' }],
    ]);
    const result = mergeEntityFrame<Entity>(
      initial,
      frame('issue.updated', { id: 'a', status: 'done', updated_at: '2026-07-25T00:00:02Z' }),
      alwaysBelongs,
    );
    expect(result.get('a')).toEqual({
      id: 'a',
      title: 'old',
      status: 'done',
      updated_at: '2026-07-25T00:00:02Z',
    });
  });

  it('removes an existing entity that no longer belongs on .updated', () => {
    const initial = new Map<string, Entity>([['a', { id: 'a', title: 'x' }]]);
    const result = mergeEntityFrame<Entity>(
      initial,
      frame('issue.updated', { id: 'a', title: 'y' }),
      neverBelongs,
    );
    expect(result.has('a')).toBe(false);
  });

  it('handles .moved with upsert-by-membership semantics', () => {
    const initial = new Map<string, Entity>([['a', { id: 'a', status: 'todo' }]]);
    const result = mergeEntityFrame<Entity>(
      initial,
      frame('issue.moved', { id: 'a', status: 'in_progress', updated_at: '2026-07-25T00:00:03Z' }),
      alwaysBelongs,
    );
    expect(result.get('a')?.status).toBe('in_progress');
  });

  it('removes an entity on .deleted', () => {
    const initial = new Map<string, Entity>([['a', { id: 'a' }]]);
    const result = mergeEntityFrame<Entity>(initial, frame('issue.deleted', { id: 'a' }), alwaysBelongs);
    expect(result.has('a')).toBe(false);
  });

  it('returns the input unchanged when deleting an absent entity', () => {
    const initial = new Map<string, Entity>();
    const result = mergeEntityFrame<Entity>(initial, frame('issue.deleted', { id: 'zzz' }), alwaysBelongs);
    expect(result).toBe(initial);
  });

  it('discards a stale .updated payload (older updated_at) — 防回退', () => {
    const initial = new Map<string, Entity>([
      ['a', { id: 'a', status: 'done', updated_at: '2026-07-25T00:00:05Z' }],
    ]);
    const result = mergeEntityFrame<Entity>(
      initial,
      frame('issue.updated', { id: 'a', status: 'todo', updated_at: '2026-07-25T00:00:01Z' }),
      alwaysBelongs,
    );
    expect(result).toBe(initial);
    expect(result.get('a')?.status).toBe('done');
  });

  it('discards a stale .moved payload (older updated_at)', () => {
    const initial = new Map<string, Entity>([
      ['a', { id: 'a', status: 'done', updated_at: '2026-07-25T00:00:05Z' }],
    ]);
    const result = mergeEntityFrame<Entity>(
      initial,
      frame('issue.moved', { id: 'a', status: 'todo', updated_at: '2026-07-25T00:00:02Z' }),
      alwaysBelongs,
    );
    expect(result).toBe(initial);
  });

  it('applies payload when only one side has updated_at (no stale comparison)', () => {
    const initial = new Map<string, Entity>([['a', { id: 'a', status: 'todo' }]]);
    const result = mergeEntityFrame<Entity>(
      initial,
      frame('issue.updated', { id: 'a', status: 'done', updated_at: '2026-07-25T00:00:09Z' }),
      alwaysBelongs,
    );
    expect(result.get('a')?.status).toBe('done');
  });

  it('treats an unknown action suffix with .updated upsert semantics', () => {
    const initial = new Map<string, Entity>([['a', { id: 'a', title: 'old' }]]);
    const result = mergeEntityFrame<Entity>(
      initial,
      frame('issue.labels_changed', { id: 'a', title: 'new' }),
      alwaysBelongs,
    );
    expect(result.get('a')?.title).toBe('new');
  });

  it('returns the input unchanged when the payload has no string id', () => {
    const initial = new Map<string, Entity>([['a', { id: 'a' }]]);
    const result = mergeEntityFrame<Entity>(initial, frame('issue.updated', { title: 'no id' }), alwaysBelongs);
    expect(result).toBe(initial);
  });

  it('is pure — never mutates the input map', () => {
    const entity: Entity = { id: 'a', title: 'old' };
    const initial = new Map<string, Entity>([['a', entity]]);
    const result = mergeEntityFrame<Entity>(
      initial,
      frame('issue.updated', { id: 'a', title: 'new' }),
      alwaysBelongs,
    );
    expect(result).not.toBe(initial);
    expect(initial.get('a')?.title).toBe('old');
    expect(initial.size).toBe(1);
  });

  it('returns a new map (not the same reference) when a change is applied', () => {
    const initial = new Map<string, Entity>();
    const result = mergeEntityFrame<Entity>(
      initial,
      frame('issue.created', { id: 'a' }),
      alwaysBelongs,
    );
    expect(result).not.toBe(initial);
  });

  it('returns the input unchanged when a non-belonging entity is already absent', () => {
    const initial = new Map<string, Entity>();
    const result = mergeEntityFrame<Entity>(
      initial,
      frame('issue.updated', { id: 'a' }),
      neverBelongs,
    );
    expect(result).toBe(initial);
  });
  it('version 兜底防回退:无 updated_at 时按 version 比较(§6.14 替代关系)', () => {
    const initial = new Map<string, Entity>([['a', { id: 'a', version: 5, status: 'done' }]]);
    const stale = mergeEntityFrame<Entity>(
      initial,
      frame('issue.updated', { id: 'a', version: 3, status: 'todo' }),
      alwaysBelongs,
    );
    expect(stale).toBe(initial);
    const fresh = mergeEntityFrame<Entity>(
      initial,
      frame('issue.updated', { id: 'a', version: 6, status: 'todo' }),
      alwaysBelongs,
    );
    expect(fresh.get('a')?.status).toBe('todo');
    expect(fresh.get('a')?.version).toBe(6);
  });

  it('payload 以浅拷贝存入:本地状态不与帧对象共享引用(不可变纯粹性)', () => {
    const payload = { id: 'a', title: 'hello' };
    const result = mergeEntityFrame<Entity>(new Map(), frame('issue.created', payload), alwaysBelongs);
    const stored = result.get('a');
    expect(stored).toEqual({ id: 'a', title: 'hello' });
    expect(stored).not.toBe(payload); // 不同引用
  });
});
