/**
 * 收件箱实时帧合并纯函数测试(comment-inbox.md §3.6)。
 */
import { describe, expect, it } from 'vitest';
import type { RealtimeEventFrame } from '../../../types/realtime';
import { applyInboxFrame, extractUnreadCount } from '../realtime';
import type { Notification } from '../types';

function frame(event: string, payload: unknown): RealtimeEventFrame {
  return { op: 'event', channel: 'member:mem-1:inbox', seq: 1, event, payload } as RealtimeEventFrame;
}

const N1: Notification = {
  id: 'n-1',
  type: 'mentioned',
  priority: 'normal',
  issue_id: 'iss-1',
  comment_id: null,
  execution_id: null,
  group_key: null,
  actor: null,
  preview: '',
  title: 'old',
  count: 1,
  read_at: null,
  archived_at: null,
  created_at: '2026-07-01T00:00:00Z',
  latest_comment_id: null,
};

describe('applyInboxFrame', () => {
  it('ignores non-notification events', () => {
    const list = [N1];
    expect(applyInboxFrame(list, frame('comment.created', {}))).toBe(list);
  });

  it('prepends a created notification and dedupes', () => {
    const created = { ...N1, id: 'n-2', title: 'new' };
    const next = applyInboxFrame([N1], frame('notification.created', created));
    expect(next.map((n) => n.id)).toEqual(['n-2', 'n-1']);
    const same = applyInboxFrame(next, frame('notification.created', created));
    expect(same).toBe(next);
  });

  it('keeps realtime creates inside the active server-equivalent filter', () => {
    const assigned = { ...N1, id: 'n-assigned', type: 'assigned' as const };
    const fromAgent = {
      ...N1,
      id: 'n-agent',
      actor: { id: 'agent-1', member_type: 'agent' as const, name: 'Agent' },
    };

    expect(
      applyInboxFrame([N1], frame('notification.created', assigned), 'mentions'),
    ).toEqual([N1]);
    expect(
      applyInboxFrame([N1], frame('notification.created', fromAgent), 'agent').map(
        (item) => item.id,
      ),
    ).toEqual(['n-agent', 'n-1']);
  });

  it('rejects malformed created payloads', () => {
    const list = [N1];
    expect(applyInboxFrame(list, frame('notification.created', { nope: 1 }))).toBe(list);
  });

  it('marks a notification read', () => {
    const next = applyInboxFrame([N1], frame('notification.read', { id: 'n-1', read_at: '2026-07-02T00:00:00Z' }));
    expect(next[0].read_at).toBe('2026-07-02T00:00:00Z');
  });

  it('removes a remotely-read notification from the unread filter', () => {
    expect(
      applyInboxFrame(
        [N1],
        frame('notification.read', { id: 'n-1', read_at: '2026-07-02T00:00:00Z' }),
        'unread',
      ),
    ).toEqual([]);
  });

  it('returns same reference for read of unknown id', () => {
    const list = [N1];
    expect(applyInboxFrame(list, frame('notification.read', { id: 'nope' }))).toBe(list);
  });

  it('falls back to the current time when read frame omits read_at', () => {
    const next = applyInboxFrame([N1], frame('notification.read', { id: 'n-1' }));
    expect(typeof next[0].read_at).toBe('string');
    expect(next[0].read_at).not.toBeNull();
  });
});

describe('extractUnreadCount', () => {
  it('extracts the count from inbox.unread_count', () => {
    expect(extractUnreadCount(frame('inbox.unread_count', { count: 5 }))).toBe(5);
  });
  it('returns null for other events / malformed payload', () => {
    expect(extractUnreadCount(frame('notification.read', {}))).toBeNull();
    expect(extractUnreadCount(frame('inbox.unread_count', {}))).toBeNull();
  });
});
