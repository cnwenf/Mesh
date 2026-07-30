/**
 * 评论实时帧合并纯函数测试(comment-inbox.md §3.6):
 * created(顶层/回复)/updated/deleted/resolved/reaction.changed 合并、防回退、
 * 执行占位 applyExecutionFrame / clearPlaceholdersForAgentComment。
 */
import { describe, expect, it } from 'vitest';
import type { RealtimeEventFrame } from '../../../types/realtime';
import {
  applyCommentsFrame,
  applyExecutionFrame,
  applyExecutionLifecycleFrame,
  clearPlaceholdersForAgentComment,
  executionChannel,
} from '../realtime';
import type { ExecutionPlaceholder } from '../realtime';
import type { Comment } from '../types';

function frame(event: string, payload: unknown): RealtimeEventFrame {
  return { op: 'event', channel: 'issue:iss-1', seq: 1, event, payload } as RealtimeEventFrame;
}

const ROOT: Comment = {
  id: 'c-1',
  issue_id: 'iss-1',
  parent_id: null,
  thread_root_id: null,
  author_kind: 'member',
  author: { id: 'mem-1', member_type: 'human', name: 'Owner' },
  body_markdown: 'root',
  body_html: '<p>root</p>',
  body_text: 'root',
  reactions: [],
  reply_count: 0,
  resolved_at: null,
  resolved_by: null,
  mentions: [],
  triggered_execution_ids: [],
  deleted_at: null,
  created_at: '2026-07-01T00:00:00Z',
  updated_at: '2026-07-02T00:00:00Z',
  edited_at: null,
};

describe('applyCommentsFrame', () => {
  it('ignores unrelated events and returns the same reference', () => {
    const list = [ROOT];
    expect(applyCommentsFrame(list, frame('issue.updated', {}))).toBe(list);
  });

  it('upserts a top-level comment.created and dedupes', () => {
    const created = { ...ROOT, id: 'c-2' };
    const next = applyCommentsFrame([ROOT], frame('comment.created', created));
    expect(next.map((c) => c.id)).toEqual(['c-1', 'c-2']);
    const same = applyCommentsFrame(next, frame('comment.created', created));
    expect(same).toBe(next);
  });

  it('rejects a malformed created payload', () => {
    const list = [ROOT];
    expect(applyCommentsFrame(list, frame('comment.created', { nope: true }))).toBe(list);
  });

  it('appends a reply into its thread root preview_replies and bumps reply_count', () => {
    const reply: Comment = {
      ...ROOT,
      id: 'c-3',
      parent_id: 'c-1',
      thread_root_id: 'c-1',
      body_markdown: 'reply',
    };
    const next = applyCommentsFrame([ROOT], frame('comment.created', reply));
    expect(next[0].reply_count).toBe(1);
    expect(next[0].preview_replies?.map((c) => c.id)).toEqual(['c-3']);
  });

  it('ignores a reply whose thread root is absent', () => {
    const reply = { ...ROOT, id: 'c-9', parent_id: 'missing', thread_root_id: 'missing' };
    const list = [ROOT];
    expect(applyCommentsFrame(list, frame('comment.created', reply))).toBe(list);
  });

  it('merges comment.updated with stale guard', () => {
    const next = applyCommentsFrame(
      [ROOT],
      frame('comment.updated', { id: 'c-1', body_html: '<p>new</p>', updated_at: '2026-07-03T00:00:00Z' }),
    );
    expect(next[0].body_html).toBe('<p>new</p>');
    const stale = applyCommentsFrame(
      next,
      frame('comment.updated', { id: 'c-1', body_html: '<p>old</p>', updated_at: '2026-07-01T00:00:00Z' }),
    );
    expect(stale).toBe(next);
  });

  it('merges nested changes object', () => {
    const next = applyCommentsFrame(
      [ROOT],
      frame('comment.updated', { id: 'c-1', changes: { body_text: 'edited' }, updated_at: '2026-07-03T00:00:00Z' }),
    );
    expect(next[0].body_text).toBe('edited');
  });

  it('marks deleted comments and clears bodies', () => {
    const next = applyCommentsFrame([ROOT], frame('comment.deleted', { id: 'c-1', deleted_at: '2026-07-04T00:00:00Z' }));
    expect(next[0].deleted_at).toBe('2026-07-04T00:00:00Z');
    expect(next[0].body_html).toBe('');
  });

  it('falls back to updated_at when deleted frame omits deleted_at', () => {
    const next = applyCommentsFrame([ROOT], frame('comment.deleted', { id: 'c-1' }));
    expect(next[0].deleted_at).toBe('2026-07-02T00:00:00Z');
  });

  it('applies comment.resolved', () => {
    const next = applyCommentsFrame(
      [ROOT],
      frame('comment.resolved', { id: 'c-1', resolved_at: '2026-07-05T00:00:00Z', updated_at: '2026-07-05T00:00:00Z' }),
    );
    expect(next[0].resolved_at).toBe('2026-07-05T00:00:00Z');
  });

  it('applies reaction.changed', () => {
    const reactions = [{ emoji: '👍', count: 1, reacted_by_me: true, actors: [] }];
    const next = applyCommentsFrame(
      [ROOT],
      frame('reaction.changed', { id: 'c-1', reactions, updated_at: '2026-07-05T00:00:00Z' }),
    );
    expect(next[0].reactions).toEqual(reactions);
  });

  it('merges an update into a nested reply (preview_replies)', () => {
    const reply: Comment = { ...ROOT, id: 'c-3', parent_id: 'c-1', thread_root_id: 'c-1' };
    const withReply: Comment = { ...ROOT, preview_replies: [reply], reply_count: 1 };
    const next = applyCommentsFrame(
      [withReply],
      frame('comment.updated', { id: 'c-3', body_text: 'edited reply', updated_at: '2026-07-03T00:00:00Z' }),
    );
    expect(next[0].preview_replies?.[0].body_text).toBe('edited reply');
  });

  it('returns same reference for unknown id / action', () => {
    const list = [ROOT];
    expect(applyCommentsFrame(list, frame('comment.updated', { id: 'nope' }))).toBe(list);
    expect(applyCommentsFrame(list, frame('comment.updated', {}))).toBe(list);
    expect(applyCommentsFrame(list, frame('comment.something', { id: 'c-1' }))).toBe(list);
  });

  it('skips prototype-pollution keys from payloads', () => {
    const hostile = JSON.parse(
      '{"id":"c-1","__proto__":{"polluted":"x"},"body_text":"safe","updated_at":"2026-07-03T00:00:00Z"}',
    );
    const next = applyCommentsFrame([ROOT], frame('comment.updated', hostile));
    expect(({} as { polluted?: unknown }).polluted).toBeUndefined();
    expect((next[0] as unknown as Record<string, unknown>).polluted).toBeUndefined();
    expect(next[0].body_text).toBe('safe');
  });
});

describe('execution placeholders', () => {
  const queued = frame('execution.queued', {
    execution_id: 'exec-1',
    agent_member_id: 'mem-agent',
    agent_name: 'reviewer',
    comment_id: 'c-1',
    status: 'queued',
    trigger: 'mention',
  });

  it('adds a placeholder on execution.queued and dedupes', () => {
    const next = applyExecutionFrame([], queued);
    expect(next).toHaveLength(1);
    expect(next[0].agent_name).toBe('reviewer');
    const same = applyExecutionFrame(next, queued);
    expect(same).toBe(next);
  });

  it('falls back to agent id when name missing and ignores malformed frames', () => {
    const noName = applyExecutionFrame([], frame('execution.queued', { execution_id: 'e', agent_member_id: 'a' }));
    expect(noName[0].agent_name).toBe('a');
    expect(applyExecutionFrame([], frame('execution.queued', { execution_id: 'e' }))).toEqual([]);
    expect(applyExecutionFrame([], frame('other.event', {}))).toEqual([]);
  });

  it('clears placeholders when the agent comment arrives', () => {
    const placeholders = applyExecutionFrame([], queued);
    const agentComment = { ...ROOT, id: 'c-9', author: { id: 'mem-agent', member_type: 'agent', name: 'reviewer' } };
    const cleared = clearPlaceholdersForAgentComment(placeholders, frame('comment.created', agentComment));
    expect(cleared).toEqual([]);
    // non-agent comment / non-created frames keep placeholders
    expect(clearPlaceholdersForAgentComment(placeholders, frame('comment.created', ROOT))).toBe(placeholders);
    expect(clearPlaceholdersForAgentComment(placeholders, frame('comment.updated', {}))).toBe(placeholders);
  });
});

/** 执行生命周期帧 → 占位五态迁移(验收必修 3 / §9.8)。 */
describe('applyExecutionLifecycleFrame 五态迁移', () => {
  const PLACEHOLDER: ExecutionPlaceholder = {
    execution_id: 'e1',
    comment_id: 'c-1',
    agent_id: 'mem-agent',
    agent_name: 'reviewer',
    status: 'queued',
    failure_reason: null,
  };
  const lifecycleFrame = (event: string, payload: unknown): RealtimeEventFrame =>
    ({ op: 'event', channel: 'execution:e1', seq: 1, event, payload }) as RealtimeEventFrame;

  it('queued 占位初始为 queued 态(附 failure_reason 缺省 null)', () => {
    const added = applyExecutionFrame(
      [],
      frame('execution.queued', { execution_id: 'e1', agent_member_id: 'mem-agent' }),
    );
    expect(added[0].status).toBe('queued');
    expect(added[0].failure_reason).toBeNull();
  });

  it('started → running;awaiting_approval → waiting', () => {
    const running = applyExecutionLifecycleFrame([PLACEHOLDER], lifecycleFrame('execution.started', { execution_id: 'e1' }));
    expect(running[0].status).toBe('running');
    const waiting = applyExecutionLifecycleFrame([PLACEHOLDER], lifecycleFrame('execution.awaiting_approval', { execution_id: 'e1' }));
    expect(waiting[0].status).toBe('waiting');
  });

  it('failed / timeout → failed + failure_reason(留失败占位供重试,§4.1)', () => {
    const failed = applyExecutionLifecycleFrame(
      [PLACEHOLDER],
      lifecycleFrame('execution.failed', { execution_id: 'e1', failure_reason: 'nonzero_exit' }),
    );
    expect(failed[0].status).toBe('failed');
    expect(failed[0].failure_reason).toBe('nonzero_exit');
    const timeout = applyExecutionLifecycleFrame(
      [PLACEHOLDER],
      lifecycleFrame('execution.timeout', { execution_id: 'e1' }),
    );
    expect(timeout[0].status).toBe('failed');
    expect(timeout[0].failure_reason).toBeNull();
  });

  it('completed / cancelled → 移除占位', () => {
    expect(
      applyExecutionLifecycleFrame([PLACEHOLDER], lifecycleFrame('execution.completed', { execution_id: 'e1' })),
    ).toEqual([]);
    expect(
      applyExecutionLifecycleFrame([PLACEHOLDER], lifecycleFrame('execution.cancelled', { execution_id: 'e1' })),
    ).toEqual([]);
  });

  it('未知执行 id / 无关事件 / 非法载荷 → 原样返回(同引用)', () => {
    const frames: RealtimeEventFrame[] = [
      lifecycleFrame('execution.started', { execution_id: 'other' }),
      lifecycleFrame('execution.progress', { execution_id: 'e1' }),
      lifecycleFrame('execution.started', {}),
    ];
    for (const f of frames) {
      const input: ExecutionPlaceholder[] = [PLACEHOLDER];
      expect(applyExecutionLifecycleFrame(input, f)).toBe(input);
    }
  });

  it('executionChannel 按执行 id 组装频道名', () => {
    expect(executionChannel('e1')).toBe('execution:e1');
  });
});
