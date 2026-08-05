/**
 * 收件箱分组与跳转纯函数测试(comment-inbox.md I8/I12)。
 */
import { describe, expect, it } from 'vitest';
import { groupNotifications } from '../grouping';
import { notificationTargetPath } from '../links';
import type { Notification } from '../types';

function make(
  id: string,
  issueId: string | null,
  createdAt: string,
  extra: Partial<Notification> = {},
): Notification {
  return {
    id,
    type: 'comment_created',
    priority: 'normal',
    issue_id: issueId,
    comment_id: null,
    execution_id: null,
    group_key: null,
    actor: null,
    preview: '',
    title: id,
    count: 1,
    read_at: null,
    archived_at: null,
    created_at: createdAt,
    latest_comment_id: null,
    ...extra,
  };
}

describe('groupNotifications', () => {
  it('groups by issue and orders groups by latest created_at desc', () => {
    const groups = groupNotifications([
      make('n-1', 'iss-1', '2026-07-01T00:00:00Z', {
        issue: { id: 'iss-1', identifier: 'WS-1', title: 'One' },
      }),
      make('n-2', 'iss-2', '2026-07-03T00:00:00Z'),
      make('n-3', 'iss-1', '2026-07-02T00:00:00Z'),
    ]);
    // 组按组内最新通知降序:iss-2(07-03)先于 iss-1(07-02)
    expect(groups.map((g) => g.issueId)).toEqual(['iss-2', 'iss-1']);
    const iss1 = groups.find((g) => g.issueId === 'iss-1');
    expect(iss1?.items.length).toBe(2);
    expect(iss1?.issue?.identifier).toBe('WS-1');
  });

  it('puts issue-less notifications into the none group', () => {
    const groups = groupNotifications([make('n-1', null, '2026-07-01T00:00:00Z')]);
    expect(groups[0].issueId).toBe('none');
    expect(groups[0].issue).toBeNull();
  });
});

describe('notificationTargetPath', () => {
  it('anchors to the latest comment', () => {
    expect(
      notificationTargetPath(
        make('n', 'iss-1', '', { latest_comment_id: 'c-9', comment_id: 'c-1' }),
      ),
    ).toBe('/issues/iss-1#comment-c-9');
  });
  it('falls back to comment_id then plain issue path', () => {
    expect(notificationTargetPath(make('n', 'iss-1', '', { comment_id: 'c-1' }))).toBe(
      '/issues/iss-1#comment-c-1',
    );
    expect(notificationTargetPath(make('n', 'iss-1', ''))).toBe('/issues/iss-1');
  });
  it('returns null without an issue', () => {
    expect(notificationTargetPath(make('n', null, ''))).toBeNull();
  });
  it('deep-links an execution notification to its run audit before the issue fallback', () => {
    const notification = make('n', 'iss-1', '', {
      type: 'execution_finished',
      execution_id: 'exec-9',
    });
    expect(notificationTargetPath(notification)).toBe('/executions/exec-9');
    expect(notificationTargetPath(notification, 'acme')).toBe('/w/acme/executions/exec-9');
  });
});
