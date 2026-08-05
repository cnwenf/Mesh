/**
 * realtime.ts 分支补齐(coverage fill):entityOf 无点事件、isStale 非字符串 updated_at、
 * changes 内原型污染键、replaceById 多顶层(无预览/预览未命中)、reply 缺 thread_root_id、
 * 重复回复去重、execution 非字符串 id、comment 包裹载荷、agent 不匹配占位保留。
 * 纯函数,无网络。
 */
import { describe, expect, it } from 'vitest';
import type { RealtimeEventFrame } from '../../../types/realtime';
import {
  applyCommentsFrame,
  applyExecutionFrame,
  clearPlaceholdersForAgentComment,
} from '../realtime';
import type { Comment } from '../types';

function frame(event: string, payload: unknown): RealtimeEventFrame {
  return { op: 'event', channel: 'issue:iss-1', seq: 1, event, payload } as RealtimeEventFrame;
}

function comment(id: string, previewReplies?: Comment[]): Comment {
  return {
    id,
    issue_id: 'iss-1',
    parent_id: null,
    thread_root_id: null,
    author_kind: 'member',
    author: { id: 'mem-1', member_type: 'human', name: 'Owner' },
    body_markdown: 'x',
    body_html: '<p>x</p>',
    body_text: 'x',
    reactions: [],
    reply_count: previewReplies ? previewReplies.length : 0,
    resolved_at: null,
    resolved_by: null,
    mentions: [],
    triggered_execution_ids: [],
    deleted_at: null,
    edited_at: null,
    created_at: '2026-01-01T00:00:00Z',
    updated_at: '2026-01-02T00:00:00Z',
    preview_replies: previewReplies,
  };
}

describe('realtime branch fill', () => {
  it('returns the same reference for an event without a dot (entityOf empty)', () => {
    const list = [comment('c-1')];
    // 'ping' 无 '.' → entityOf 返回 '' → 既非 comment 也非 reaction → 原样返回
    expect(applyCommentsFrame(list, frame('ping', {}))).toBe(list);
  });

  it('merges an update whose updated_at is not a string (isStale falls through)', () => {
    // updated_at 为数字 → frameUpdatedAt undefined → isStale 返回 false → 合并
    const next = applyCommentsFrame(
      [comment('c-1')],
      frame('comment.updated', { id: 'c-1', body_text: 'num-ts', updated_at: 12345 }),
    );
    expect(next[0].body_text).toBe('num-ts');
  });

  it('skips prototype-pollution keys nested inside changes', () => {
    const hostileChanges = JSON.parse('{"__proto__":{"polluted":"x"},"body_text":"safe"}');
    const next = applyCommentsFrame(
      [comment('c-1')],
      frame('comment.updated', {
        id: 'c-1',
        updated_at: '2026-03-01T00:00:00Z',
        changes: hostileChanges,
      }),
    );
    expect(({} as { polluted?: unknown }).polluted).toBeUndefined();
    expect(next[0].body_text).toBe('safe');
  });

  it('replaceById passes over a top-level comment with no preview_replies', () => {
    // 两个顶层:第一个无 preview_replies(返回自身),命中第二个
    const a = comment('c-a');
    const b = comment('c-b');
    const next = applyCommentsFrame(
      [a, b],
      frame('comment.updated', {
        id: 'c-b',
        body_text: 'b-edited',
        updated_at: '2026-03-01T00:00:00Z',
      }),
    );
    expect(next[0]).toBe(a);
    expect(next[1].body_text).toBe('b-edited');
  });

  it('replaceById passes over a thread whose replies do not match the id', () => {
    // 第一个顶层有 preview_replies 但内层 id 不匹配(nestedChanged=false → 返回自身)
    const root = { ...comment('c-root'), preview_replies: [comment('c-r1')], reply_count: 1 };
    const other = comment('c-other');
    const next = applyCommentsFrame(
      [root, other],
      frame('comment.updated', {
        id: 'c-other',
        body_text: 'o-edited',
        updated_at: '2026-03-01T00:00:00Z',
      }),
    );
    expect(next[0]).toBe(root);
    expect(next[1].body_text).toBe('o-edited');
  });

  it('appends a reply using parent_id when thread_root_id is null', () => {
    const reply: Comment = {
      ...comment('c-r'),
      parent_id: 'c-1',
      thread_root_id: null,
    };
    const next = applyCommentsFrame([comment('c-1')], frame('comment.created', reply));
    expect(next[0].reply_count).toBe(1);
    expect(next[0].preview_replies?.map((c) => c.id)).toEqual(['c-r']);
  });

  it('dedupes a reply that already exists in preview_replies', () => {
    const existing = comment('c-r');
    const root = { ...comment('c-1'), preview_replies: [existing], reply_count: 1 };
    const dup: Comment = { ...existing, body_text: 'dup' };
    const list = [root];
    expect(applyCommentsFrame(list, frame('comment.created', dup))).toBe(list);
  });

  it('ignores execution.queued when execution_id is not a string', () => {
    expect(
      applyExecutionFrame([], frame('execution.queued', { execution_id: 5, agent_member_id: 'a' })),
    ).toEqual([]);
    expect(applyExecutionFrame([], frame('execution.queued', { agent_member_id: 'a' }))).toEqual(
      [],
    );
  });

  it('clears an explicitly linked placeholder when the agent comment is wrapped', () => {
    const placeholders = applyExecutionFrame(
      [],
      frame('execution.queued', {
        execution_id: 'e1',
        agent_member_id: 'mem-agent',
        agent_name: 'rev',
      }),
    );
    const agentComment = {
      ...comment('c-9'),
      author: { id: 'mem-agent', member_type: 'agent', name: 'rev' },
    };
    const cleared = clearPlaceholdersForAgentComment(
      placeholders,
      frame('comment.created', { comment: agentComment, execution_id: 'e1' }),
    );
    expect(cleared).toEqual([]);
  });

  it('keeps placeholders when the agent comment author matches none of them', () => {
    const placeholders = applyExecutionFrame(
      [],
      frame('execution.queued', {
        execution_id: 'e1',
        agent_member_id: 'mem-agent',
        agent_name: 'rev',
      }),
    );
    const otherAgent = {
      ...comment('c-9'),
      author: { id: 'other-agent', member_type: 'agent', name: 'other' },
    };
    expect(
      clearPlaceholdersForAgentComment(placeholders, frame('comment.created', otherAgent)),
    ).toBe(placeholders);
  });
});
