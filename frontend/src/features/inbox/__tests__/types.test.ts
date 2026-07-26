/**
 * 收件箱类型守卫测试(comment-inbox.md §2.6)。
 */
import { describe, expect, it } from 'vitest';
import type { Notification } from '../types';
import { isNotification, isUnread } from '../types';

const BASE: Notification = {
  id: 'n-1',
  type: 'mentioned',
  priority: 'normal',
  issue_id: 'iss-1',
  comment_id: 'c-1',
  execution_id: null,
  group_key: 'issue:iss-1:mentioned',
  actor: { id: 'mem-1', member_type: 'human', name: 'Alice' },
  preview: 'hi',
  title: 'You were mentioned',
  count: 1,
  read_at: null,
  archived_at: null,
  created_at: '2026-07-01T00:00:00Z',
  latest_comment_id: 'c-1',
};

describe('isNotification', () => {
  it('accepts a notification-shaped payload', () => {
    expect(isNotification(BASE)).toBe(true);
  });
  it('rejects malformed values', () => {
    expect(isNotification(null)).toBe(false);
    expect(isNotification({ id: 'n-1' })).toBe(false);
  });
});

describe('isUnread', () => {
  it('is unread when not read and not archived', () => {
    expect(isUnread(BASE)).toBe(true);
  });
  it('is read once read_at is set', () => {
    expect(isUnread({ ...BASE, read_at: '2026-07-02T00:00:00Z' })).toBe(false);
  });
  it('is not unread when archived', () => {
    expect(isUnread({ ...BASE, archived_at: '2026-07-02T00:00:00Z' })).toBe(false);
  });
});
